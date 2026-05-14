# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    def __init__(
        self, config: BBBLBConfig, db: DBContext, locks: LockManager, bbb: BBBHelper
    ):
        self.config = config
        self.interval = config.POLL_INTERVAL
        self.timeout = self.interval * 1.1
        self.maxerror = config.POLL_FAIL
        self.minsuccess = config.POLL_RECOVER

        self.db = db
        self.lock = locks.create(
            "poller", datetime.timedelta(seconds=self.interval) * 2
        )
        self.bbb = bbb

        #: Start of a poll interval. Used as a common timestamp for all
        #: MeetingStats entries created during a single poll run. This
        #: allows later grouping by poll interval.
        self._poll_start = 0.0
        self.is_worker = config.WORKER

    async def run(self):
        while True:
            try:
                # Short random sleep to give other proceses a chance
                await asyncio.sleep(random.random() * self.interval)

                if self.is_worker:
                    # Run loop while holding the lock, or continue and try again
                    await self.lock.try_run_locked(self.poll_loop)

            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Unhandled polling error")
                continue  # Recover by starting another loop

    async def poll_loop(self):
        while True:
            ts = time.time()

            if not await self.lock.check():
                LOG.warning(f"We lost the {self.lock.name!r} lock!?")
                return

            async with self.db.session() as session:
                result = await session.execute(model.Server.select())
                servers = result.scalars().all()

            self._poll_start = model.utcnow()
            futures = [
                asyncio.ensure_future(self.poll_one(server.id)) for server in servers
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
                LOG.warning(f"Poll took longer than {self.interval}s ({dt:.1}s total)")
            await asyncio.sleep(max(1.0, sleep))

    async def poll_one(self, server_id):

        # Fetch current state (server, meetigns) from DB
        async with self.db.session() as session:
            server = (
                await session.execute(model.Server.select(id=server_id))
            ).scalar_one()
            meetings: dict[str, model.Meeting] = {
                meeting.internal_id: meeting
                for meeting in await server.awaitable_attrs.meetings
                if meeting.internal_id
            }

        # Poll disabled servers only if we think they still have meetings.
        if not server.enabled:
            if meetings or server.stats.meetings:
                LOG.debug(f"[{server.domain}] Disabled server still has meetings.")
            else:
                return  # No need to poll

        # Collect meetings and stats from the BBB backend server
        running_ids: set[str] = set()
        server_load = 0.0
        server_stats = model.ServerStats()
        meeting_stats: dict[int, model.MeetingStats] = {}
        success = True
        try:
            async with self.bbb.connect(server) as client:
                result = await client.action("getMeetings", timeout=self.timeout)
                result.raise_on_error()

            for mxml in result.xml.iterfind("meetings/meeting"):
                endTime = int(mxml.findtext("endTime") or 0)
                if endTime > 0:
                    continue

                meeting_id = mxml.findtext("internalMeetingID")
                parent_id = mxml.findtext("breakout/parentMeetingID")
                users = int(mxml.findtext("participantCount") or 0)
                voice = int(mxml.findtext("voiceParticipantCount") or 0)
                video = int(mxml.findtext("videoCount") or 0)
                age = max(0.0, time.time() - int(mxml.findtext("createTime") or 0))
                try:
                    size_hint = int(mxml.findtext("meta/bbb-meeting-size-hint") or 0)
                except ValueError:
                    size_hint = 0

                if not meeting_id:
                    continue

                # Count all meetings, even if we do not know them
                running_ids.add(meeting_id)
                server_stats.count_meeting(users, voice, video)
                server_load += self.get_meeting_load(
                    users, voice, video, age, size_hint
                )

                # Find the matching meeting
                meeting = None
                if meeting_id in meetings:
                    meeting = meetings.get(meeting_id)
                elif parent_id and parent_id in meeting_id:
                    meeting = meetings.get(parent_id)
                else:
                    # We do not know this meeting. If was created very
                    # recently (after the DB fetch) or not by us at all.
                    # TODO: If this is our meeting, but we forgot about
                    # it because the server went OFFLINE and came back,
                    # either learn it, or end it on the back-end.
                    continue

                if self.config.POLL_STATS and meeting:
                    if meeting.uuid not in meeting_stats:
                        meeting_stats[meeting.id] = model.MeetingStats(
                            ts=self._poll_start,
                            uuid=meeting.uuid,
                            meeting_id=meeting.external_id,
                            tenant_fk=meeting.tenant_fk,
                            users=users,
                            voice=voice,
                            video=video,
                        )
                    else:
                        # Likely a breakout room. We count users only
                        # once, but voice and video are all counted
                        stats = meeting_stats[meeting.id]
                        stats.users = max(users, stats.users)
                        stats.voice += voice
                        stats.video += video

        except BBBError as err:
            LOG.warning(f"[{server.domain}] Health check failed: {err}")
            success = False

        async with self.db.session() as session:
            # Refetch server entity for update
            server = (
                await session.execute(
                    model.Server.select(id=server_id).with_for_update()
                )
            ).scalar_one()
            old_health = server.health

            # Update health, load and stats
            if success:
                server.mark_success(self.minsuccess)
                server.load = server_load
                server.stats = server_stats
            else:
                server.mark_error(self.maxerror)
                if server.health is model.ServerHealth.OFFLINE:
                    server.load = 0
                    server.stats = model.ServerStats()

            # Log state changes
            self._log_poll_result(server, success, old_health)

            # Store collected meeting stats
            if meeting_stats:
                session.add_all(meeting_stats.values())

            await session.commit()

        # Cleanup ended meetings
        if success or server.health == model.ServerHealth.OFFLINE:
            ended = [
                m for m in meetings.values() if m.internal_id not in running_ids
            ]
            if ended:
                LOG.debug(
                    f"[{server.domain}] Removing {len(ended)}"
                    " meetings from database."
                )
                await self.bbb.forget_meetings(ended)

    def get_meeting_load(self, users=2, voice=2, video=2, age=0.0, size_hint=0):
        config = self.config

        # Actual load calculated from user, voice and video counts
        load = config.LOAD_BASE
        load += users * config.LOAD_USER
        load += voice * config.LOAD_VOICE
        load += video * config.LOAD_VIDEO

        # New meetings are assumed to have more users than they actually have
        if age < (config.LOAD_COOLDOWN * 60):
            # Use the front-end provided size hint capped between
            # LOAD_ESTIMATE and 5*LOAD_ESTIMATE, or just LOAD_ESTIMATE.
            ghosts = max(config.LOAD_RESERVED, min(size_hint, config.LOAD_RESERVED * 5))
            # Reduce number of ghosts based on meeting age
            ghosts *= 1.0 - (age / (config.LOAD_COOLDOWN * 60))
            # Assume every (future) user has voice activated, and 10% have video activated
            load += ghosts * config.LOAD_USER
            load += ghosts * config.LOAD_VOICE
            load += ghosts * 0.1 * config.LOAD_VIDEO

        return load

    def _log_poll_result(
        self, server: model.Server, success: bool, old_health: model.ServerHealth
    ):

        # Log state changes as warnings, even positive ones.
        if server.health is model.ServerHealth.AVAILABLE:
            if old_health is not model.ServerHealth.AVAILABLE:
                LOG.warning(f"[{server.domain}] Server recovered and is now AVAILABLE.")
        elif server.health is model.ServerHealth.UNSTABLE:
            if success:
                LOG.warning(
                    f"[{server.domain}] Server is UNSTABLE but recovering."
                    f" Successfull polls: {server.recover}/{self.minsuccess}"
                )
            else:
                LOG.warning(
                    f"[{server.domain}] Server is UNSTABLE and still failing."
                    f" Failed polls: {server.errors}/{self.maxerror}"
                )
        elif server.health is model.ServerHealth.OFFLINE:
            if old_health is not model.ServerHealth.OFFLINE:
                LOG.warning(
                    f"[{server.domain}] Server failed too often and is now marked as OFFLINE."
                )

        LOG.debug(
            f"[{server.domain}] {server.health.name}"
            f" enabled={server.enabled}"
            f" meetings={server.stats.meetings}"
            f" users={server.stats.users}"
            f" load={server.load:.1f}"
        )
