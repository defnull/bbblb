import asyncio
import datetime
import time

from bbblb import model
from bbblb.bbblib import BBBClient, BBBError
from bbblb.settings import config

import logging

LOG = logging.getLogger(__name__)


class Poller:
    task: asyncio.Task | None

    def __init__(self):
        self.should_stop = False
        self.task = None
        self.lockname = "poll"
        self.lock_timeout = datetime.timedelta(seconds=config.POLL_INTERVAL * 10)
        self.locked = False
        self.shutdown_complete = asyncio.Event()

    async def __aenter__(self):
        self.should_stop = False
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.poller_loop())
        return self

    async def __aexit__(self, *a):
        await self.close()

    async def close(self):
        self.should_stop = True
        if self.task:
            self.task.cancel()

        # We cannot wait for a cancelled task, but we want to wait until all
        # cleanup/finally activities have completed.
        await self.shutdown_complete.wait()

    async def poller_loop(self):
        try:
            while not self.should_stop:
                self.locked = await model.Lock.try_acquire(
                    self.lockname, self.lock_timeout
                )
                if not self.locked:
                    # Another process holds the lock, pause for a while and then try again
                    await asyncio.sleep((self.lock_timeout / 2).total_seconds())
                    continue

                try:
                    LOG.info("Starting poller loop ...")
                    while not self.should_stop:
                        ts = time.time()

                        if not await model.Lock.check(self.lockname):
                            LOG.warning("We lost our {lockname!r} lock!?")
                            break

                        async with model.scope() as session:
                            result = await session.execute(model.Server.select())
                            servers = result.scalars()
                        tasks = [self.poll_one(server.id) for server in servers]
                        await asyncio.gather(*tasks)

                        dt = time.time() - ts
                        sleep = config.POLL_INTERVAL - dt
                        if sleep <= 0.0:
                            LOG.warning(
                                "Poll took longer than POLL_INTERVAL ({dt:.1}s total)"
                            )
                            await asyncio.sleep(config.POLL_INTERVAL)
                        else:
                            await asyncio.sleep(max(1.0, sleep))
                except asyncio.CancelledError:
                    LOG.info("Poller cancelled")
                    self.should_stop = True
                except BaseException:
                    LOG.exception("Unhandled polling error")
        finally:
            if self.locked:
                await model.Lock.try_release(self.lockname)
            self.shutdown_complete.set()

    @model.transactional(isolated=True, autocommit=True)
    async def poll_one(self, server_id):
        server = await model.Server.get(id=server_id)
        known = await server.awaitable_attrs.meetings
        meetings = {meeting.internal_id: meeting for meeting in known}

        if server.state == model.ServerState.DISABLED:
            if meetings:
                LOG.debug(
                    "Server {server.domain} is DISABLED but still has meetings, let's change that."
                )
            for meeting in meetings:
                await meeting.delete()
            return

        LOG.info(f"Polling {server.api_base} (state={server.state.name})")
        running_ids = set()
        load = 0.0
        success = True
        try:
            bbb = BBBClient(server.api_base, server.secret)
            result = await bbb.action("getMeetings")

            for mxml in result.xml.iterfind("meetings/meeting"):
                endTime = int(mxml.findtext("endTime"))
                if endTime > 0:
                    continue

                meeting_id = mxml.findtext("internalMeetingID")
                parent_id = mxml.findtext("breakout/parentMeetingID")
                running_ids.add(meeting_id)

                load += config.LOADFACTOR_MEETING
                load += int(mxml.findtext("participantCount")) * config.LOADFACTOR_SIZE
                load += (
                    int(mxml.findtext("voiceParticipantCount"))
                    * config.LOADFACTOR_VOICE
                )
                load += int(mxml.findtext("videoCount")) * config.LOADFACTOR_VIDEO

                age = max(0.0, time.time() - int(mxml.findtext("createTime")))
                if age < 60 * 60:
                    load += config.LOADFACTOR_INITIAL * (1.0 - (age / 60 * 60))

                if meeting_id not in meetings:
                    if parent_id:
                        # TODO: Breakout rooms may be created without our knowledge,
                        # maybe learn those?
                        continue
                    LOG.warning(f"Meeting on server that is not in DB: {meeting_id}")
                    continue  # Ignore unknown meetings

            for meeting in meetings.values():
                if meeting.internal_id in running_ids:
                    continue
                LOG.debug("Meeting not found on server, probably ended: {meeting_id}")
                await meeting.delete()

        except BBBError as err:
            LOG.warning(f"Server {server.domain} returned an error: {err}")
            success = False

        if success:
            server.load = load
            LOG.warning(
                f"Server {server.domain} has {len(running_ids)} meetings and a load of {load}"
            )

            if server.state == model.ServerState.ONLINE:
                pass  # Already healthy
            elif server.recover < config.POLL_RECOVER:
                # Server is still recovering
                server.recover += 1
                server.state = model.ServerState.UNSTABLE
                LOG.warning(
                    f"Server {server.domain} is UNSTABLE and recovering ({server.recover}/{config.POLL_RECOVER})"
                )
            else:
                # Server fully recovered
                server.errors = 0
                server.recover = 0
                server.state = model.ServerState.ONLINE
                LOG.info(f"Server {server.domain} is ONLINE")
        else:
            if server.state == model.ServerState.OFFLINE:
                pass  # Already dead
            elif server.errors < config.POLL_FAIL:
                # Server is failing
                server.recover = 0  # Reset recovery counter
                server.errors += 1
                server.state = model.ServerState.UNSTABLE
                LOG.warning(
                    f"Server {server.domain} is UNSTABLE and failing ({server.errors}/{config.POLL_FAIL})"
                )
            else:
                # Server failed too often, hive up
                server.state = model.ServerState.OFFLINE
                LOG.warning(f"Server {server.domain} is OFFLINE")
