import asyncio
import datetime
import random
import time

from bbblb import model
from bbblb.services import BackgroundService
from bbblb.lib.bbb import BBBError
from bbblb.services.bbb import BBBHelper
from bbblb.services.db import DBContext
from bbblb.services.locks import LockManager

import logging

from bbblb.settings import BBBLBConfig

LOG = logging.getLogger(__name__)


class MeetingPoller(BackgroundService):
    def __init__(self, config: BBBLBConfig):
        self.config = config
        self.interval = config.POLL_INTERVAL
        self.maxerror = config.POLL_FAIL
        self.minsuccess = config.POLL_RECOVER

        self.load_base = config.LOAD_BASE
        self.load_user = config.LAOD_USER
        self.load_video = config.LOAD_VIDEO
        self.load_voice = config.LOAD_VOICE
        self.load_prediction = config.LOAD_PENALTY
        self.load_cooldown = config.LOAD_COOLDOWN * 60

    async def on_start(self, db: DBContext, locks: LockManager, bbb: BBBHelper):
        self.db = db
        self.lock = locks.create(
            "poller", datetime.timedelta(seconds=self.interval) * 2
        )
        self.bbb = bbb
        await super().on_start()

    async def run(self):
        while True:
            # Random sleep to give other proceses a chance to fetch the lock
            await asyncio.sleep(random.random())

            # Acquire exclusive lock, or try again
            if not await self.lock.try_acquire():
                continue

            try:
                await self.poll_loop()
            finally:
                await self.lock.try_release()
                self.locked = False

    async def poll_loop(self):
        try:
            LOG.info("Starting poller loop ...")
            while True:
                ts = time.time()

                if not await self.lock.check():
                    LOG.warning(f"We lost the {self.lock.name!r} lock!?")
                    break

                async with self.db.session() as session:
                    result = await session.execute(model.Server.select())
                    servers = result.scalars()

                futures = [
                    asyncio.ensure_future(self.poll_one(server.id))
                    for server in servers
                ]
                while futures:
                    done, futures = await asyncio.wait(
                        futures, timeout=(self.lock.timeout * 0.8).total_seconds()
                    )

                    if futures and not await self.lock.check():
                        LOG.warning(f"We lost the {self.lock.name!r} lock!?")
                        for future in futures:
                            future.cancel()
                        return

                dt = time.time() - ts
                sleep = self.interval - dt
                if sleep <= 0.0:
                    LOG.warning(
                        f"Poll took longer than {self.interval}s ({dt:.1}s total)"
                    )
                await asyncio.sleep(max(1.0, sleep))

        except asyncio.CancelledError:
            LOG.info("Poller shutting down...")
            raise
        except BaseException:
            LOG.exception("Unhandled polling error")
            return  # Recover by starting another loop

    async def poll_one(self, server_id):
        async with self.db.session() as session:
            server = (
                await session.execute(model.Server.select(id=server_id))
            ).scalar_one()
            meetings = await server.awaitable_attrs.meetings
            meetings = {meeting.internal_id: meeting for meeting in meetings}

        if not server.enabled:
            if not meetings:
                return
            LOG.debug("Disabled server {server.domain} still has meetings.")

        LOG.info(f"Polling {server.api_base} (state={server.health.name})")
        running_ids = set()
        users = 0
        load = 0.0
        success = True
        try:
            async with self.bbb.connect(server.api_base, server.secret) as client:
                result = await client.action("getMeetings")
                result.raise_on_error()

            for mxml in result.xml.iterfind("meetings/meeting"):
                endTime = int(mxml.findtext("endTime") or 0)
                if endTime > 0:
                    continue

                meeting_id = mxml.findtext("internalMeetingID")
                parent_id = mxml.findtext("breakout/parentMeetingID")
                running_ids.add(meeting_id)

                load += self.load_base
                users += int(mxml.findtext("participantCount") or 0)
                load += int(mxml.findtext("participantCount") or 0) * self.load_user
                load += (
                    int(mxml.findtext("voiceParticipantCount") or 0) * self.load_voice
                )
                load += int(mxml.findtext("videoCount") or 0) * self.load_video

                age = max(0.0, time.time() - int(mxml.findtext("createTime") or 0))
                if age < self.load_cooldown:
                    load += self.load_prediction * (1.0 - (age / self.load_cooldown))

                if meeting_id not in meetings:
                    if parent_id:
                        # TODO: Breakout rooms may be created without our knowledge,
                        # maybe learn those?
                        continue
                    LOG.warning(f"Meeting on server that is not in DB: {meeting_id}")
                    continue  # Ignore unknown meetings

        except BBBError as err:
            LOG.warning(f"Server {server.domain} returned an error: {err}")
            success = False

        async with self.db.session() as session:
            # Forget meetings not found on server
            forget_ids = set(
                meeting.internal_id
                for meeting in meetings.values()
                if meeting.internal_id not in running_ids
            )
            if forget_ids:
                LOG.debug(
                    f"{len(forget_ids)} meetings not found on server, ending them all"
                )
                await session.execute(
                    model.delete(model.Meeting).where(
                        model.Meeting.internal_id.in_(forget_ids)
                    )
                )

            # Re-fetch server from DB so we can update load and state values
            server = (
                await session.execute(model.Server.select(id=server_id))
            ).scalar_one()

            old_health = server.health

            if success:
                server.load = load
                server.mark_success(self.minsuccess)
            else:
                server.mark_error(self.maxerror)

            LOG.info(
                f"[{server.domain}] meetings={len(running_ids)} users={users} load={load:.1f} health={server.health.name}"
            )

            # Log all state changes (including recovery) as warnings
            if old_health != server.health:
                LOG.warning(
                    f"[{server.domain}] health changed from {old_health.name} to {server.health.name}"
                )

            await session.commit()
