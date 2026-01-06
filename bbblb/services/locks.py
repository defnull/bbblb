import asyncio
import datetime
import logging
import os
import secrets
import socket
from bbblb import model
from bbblb.services import ManagedService
from bbblb.services.db import DBContext

LOG = logging.getLogger(__name__)

PROCESS_IDENTITY = f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(4)}"


class LockManager(ManagedService):
    async def on_start(self, db: DBContext):
        self.db = db
        LOG.debug(f"Log manager started with identity: {PROCESS_IDENTITY}")

    async def on_shutdown(self):
        LOG.debug("Log manager shutdown. Releasing locks...")
        async with self.db.connect() as conn:
            await conn.execute(
                model.delete(model.Lock).where(model.Lock.owner == PROCESS_IDENTITY)
            )

    def create(self, name, timeout: datetime.timedelta):
        return NamedLock(self, name, timeout)


class NamedLock:
    def __init__(self, lm: LockManager, name: str, timeout: datetime.timedelta):
        self.db = lm.db
        self.name = name
        self.timeout = timeout

    async def try_acquire(self):
        """Try to acquire a named inter-process lock and force-release any
        existing locks if they were older than the lock timeout.

        This is not re-entrant. Acquiring the same lock twice will fail.
        """
        async with self.db.connect() as conn:
            if self.timeout:
                expire = model.utcnow() - self.timeout
                await conn.execute(
                    model.delete(model.Lock).where(
                        model.Lock.name == self.name, model.Lock.ts < expire
                    )
                )

            result = await conn.execute(
                model.upsert(conn, model.Lock)
                .values(name=self.name, owner=PROCESS_IDENTITY)
                .on_conflict_do_nothing(index_elements=["name"])
            )
            if result.rowcount == 0:
                return False

            try:
                await conn.commit()
                LOG.debug(f"Lock {self.name!r} acquired by {PROCESS_IDENTITY}")
                return True
            except model.OperationalError:
                return False

    async def check(self):
        """Update the lifetime of an already held lock, return true if such a
        lock exists, false otherwise."""
        async with self.db.connect() as conn:
            result = await conn.execute(
                model.update(model.Lock)
                .values(ts=model.utcnow())
                .where(
                    model.Lock.name == self.name, model.Lock.owner == PROCESS_IDENTITY
                )
            )
            if result.rowcount > 0:
                LOG.debug(f"Lock {self.name!r} updated by {PROCESS_IDENTITY}")
                return True
            return False

    async def try_release(self):
        """Release a named inter-process lock if it's owned by the current
        process. Return true if such a lock existed, false otherwise.

        Because this is often called in a finally-block and is not expected to fail,
        if will simply return `False` for all exceptions other any CancelledError.
        """
        try:
            async with self.db.connect() as conn:
                result = await conn.execute(
                    model.delete(model.Lock).where(
                        model.Lock.name == self.name,
                        model.Lock.owner == PROCESS_IDENTITY,
                    )
                )
                if result.rowcount > 0:
                    LOG.debug(f"Lock {self.name!r} released by {PROCESS_IDENTITY}")
                    return True
                return False
        except asyncio.CancelledError:
            raise
        except BaseException:
            LOG.exception(
                f"Error while trying to release {self.name!r} as {PROCESS_IDENTITY}"
            )
            return False

    async def try_run_locked(self, callable, *a, **ka):
        """Run an async function while holding the lock.

        Return (False, None) if the lock could not be acquired,
        or (True, AnyResult) on success."""
        if not await self.try_acquire():
            return False, None
        try:
            result = await callable(*a, **ka)
            return True, result
        finally:
            await self.try_release()
