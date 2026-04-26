# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
import datetime
import enum
import functools
from functools import cached_property
import logging
from pathlib import Path
import random
from secrets import token_hex
import secrets
import shutil
import tarfile
import time
import typing
import uuid
import lxml.etree
import urllib.parse

from bbblb import model, utils
from bbblb.services import BackgroundService
from bbblb.services.bbb import BBBHelper
from bbblb.services.db import DBContext
from bbblb.services.locks import LockManager
from bbblb.settings import BBBLBConfig
from bbblb.lib.bbb import ETree, XML, Element, SubElement

LOG = logging.getLogger(__name__)

P = typing.ParamSpec("P")
R = typing.TypeVar("R")


class RecordingImportError(RuntimeError):
    pass


def playback_to_xml(
    config: BBBLBConfig,
    playback: model.PlaybackFormat,
    ticket_prefix: str | None = None,
) -> Element:
    orig = lxml.etree.fromstring(playback.xml)
    playback_domain = config.PLAYBACK_DOMAIN.format(
        DOMAIN=config.DOMAIN, REALM=playback.recording.tenant.realm
    )

    result = XML.format(
        XML.type(playback.format),
    )

    # The field names and sometimes also values differ a lot between
    # metadata.xml and getRecordings. Here is what we know:
    if (value := orig.findtext("link")) is not None:
        SubElement(result, "url").text = value
    if (value := orig.findtext("processing_time")) is not None:
        SubElement(result, "processingTime").text = value
    if (value := orig.findtext("duration")) is not None:
        SubElement(result, "length").text = str(int(value) // 60000)
    if (value := orig.findtext("size")) is not None:
        SubElement(result, "size").text = value

    # Append everything from the 'extentions' subelement (e.g. extensions/preview)
    result.extend(orig.iterfind("extensions/*"))

    # Fix all URLs we can find
    for node in result.iter():
        if not node.text or "://" not in node.text:
            continue
        try:
            url = urllib.parse.urlparse(node.text.strip())
        except ValueError:
            continue
        url = url._replace(scheme="https", netloc=playback_domain)
        if url.path.startswith(f"/{playback.format}"):
            url = url._replace(path=f"/playback{url.path}")
        if ticket_prefix and url.path.startswith("/playback/"):
            url = url._replace(path=ticket_prefix + url.path)
        node.text = url.geturl()

    return result


def _sanity_pathname(name: str):
    name = name.strip()
    if not name:
        raise ValueError("Path name cannot be empty")
    for bad in "/\\:":
        if bad in name:
            raise ValueError(f"Unexpected character in path name: {name!r}")
    return name


def _record_from_metadata(metadata_xml: "FormatXMLWrapper", tenant: model.Tenant):
    meta_dict = dict(metadata_xml.meta)
    meta_dict["meetingId"] = metadata_xml.unscoped_id

    return model.Recording(
        tenant=tenant,
        record_id=metadata_xml.record_id,
        external_id=metadata_xml.unscoped_id,
        state=(
            model.RecordingState.PUBLISHED
            if metadata_xml.published
            else model.RecordingState.UNPUBLISHED
        ),
        started=metadata_xml.started,
        ended=metadata_xml.ended,
        participants=metadata_xml.participants,
        meta=meta_dict,
    )


def _format_from_metadata(metadata_xml: "FormatXMLWrapper", recording: model.Recording):
    return model.PlaybackFormat(
        recording=recording,
        format=metadata_xml.format,
        xml=lxml.etree.tostring(metadata_xml.playback_node).decode("ASCII"),
    )


class FormatXMLWrapper:
    def __init__(self, xml: ETree):
        self.xml = xml

    @cached_property
    def record_id(self):
        record_id = self.xml.findtext("id")
        if not (record_id and utils.RE_RECORD_ID.match(record_id)):
            raise RecordingImportError(
                f"Invalid or missing recording ID: {record_id!r}"
            )
        return record_id

    @cached_property
    def format(self):
        format = self.xml.findtext("playback/format")
        if not (format and utils.RE_FORMAT_NAME.match(format)):
            raise RecordingImportError(
                f"Invalid or missing playback format name: {format!r}"
            )
        return format

    @cached_property
    def bbblb_tenant(self):
        tenant = self.xml.findtext("meta/bbblb-tenant")
        if not (tenant and utils.RE_TENANT_NAME.match(tenant)):
            raise RecordingImportError(
                f"Invalid or missing tenant information: {tenant!r}"
            )
        return tenant

    @cached_property
    def meta(self):
        metatags = self.xml.find("meta")
        if metatags is None:
            raise RecordingImportError("Invalid or missing meta information")
        return {tag.tag: tag.text for tag in metatags if tag.text}

    @cached_property
    def unscoped_id(self):
        return utils.remove_scope(self.meta["meetingId"])

    @cached_property
    def started(self):
        return datetime.datetime.fromtimestamp(
            int(self.xml.findtext("start_time") or 0) / 1000, tz=datetime.timezone.utc
        )

    @cached_property
    def ended(self):
        return datetime.datetime.fromtimestamp(
            int(self.xml.findtext("end_time") or 0) / 1000, tz=datetime.timezone.utc
        )

    @cached_property
    def participants(self):
        return int(self.xml.findtext("participants") or 0)

    @cached_property
    def published(self):
        pinfo = self.xml.findtext("published")
        if pinfo and pinfo.lower() == "true":
            return model.RecordingState.PUBLISHED
        else:
            return model.RecordingState.UNPUBLISHED

    @cached_property
    def playback_node(self):
        playback_node = self.xml.find("playback")
        if playback_node is None:
            raise RecordingImportError("Invalid or missing playback information")
        return playback_node


class RecordingManager(BackgroundService):
    def __init__(
        self, config: BBBLBConfig, db: DBContext, locks: LockManager, bbb: BBBHelper
    ):

        self.db = db
        self.bbb = bbb

        self.poll_interval = config.POLL_INTERVAL
        self.is_worker = config.WORKER
        self.import_unpublished = config.RECORDING_IMPORT_UNPUBLISHED

        self.base_dir = (config.PATH_DATA / "recordings").resolve()
        self.inbox_dir = self.base_dir / "inbox"
        self.failed_dir = self.base_dir / "failed"
        self.work_dir = self.base_dir / "work"
        self.public_dir = self.base_dir / "public"
        self.storage_dir = self.base_dir / "storage"
        self.deleted_dir = self.base_dir / "deleted"
        self.maxtasks = asyncio.Semaphore(config.RECORDING_THREADS)
        self.pool = ThreadPoolExecutor(thread_name_prefix="rec-")
        self.tasks: dict[str, "RecordingImportTask"] = {}
        self.lock = locks.create(
            "importer", datetime.timedelta(seconds=self.poll_interval) * 2
        )

    async def on_start(self):
        # Create all directories we need, if missing
        for dir in (d for d in self.__dict__.values() if isinstance(d, Path)):
            if dir and not dir.exists():
                await self._in_pool(dir.mkdir, parents=True, exist_ok=True)

        await super().on_start()

    async def run(self):
        try:
            while True:
                await asyncio.sleep(self.poll_interval + random.random())
                if self.is_worker:
                    await self.lock.try_run_locked(self.run_locked)
        finally:
            await self.close()

    async def run_locked(self):
        while await self.lock.check():
            await self.import_waiting()
            await self.cleanup()
            await asyncio.sleep(self.poll_interval)

    async def import_waiting(self):
        """Pick up waiting tasks from inbox"""
        # Only pick up older files for which we are sure the regular
        # import didn't work or was aborted.
        min_age = random.randint(60, 120)

        for file in self.inbox_dir.glob("*.tar"):
            if file.stat().st_mtime + min_age > time.time():
                continue
            self._schedule(RecordingImportTask(self, file.stem, file))

    async def cleanup(self):
        # TODO: Cleanup *.failed and *.canceled work directories.

        # Cleanup expored ViewTicket entries for protected recordings.
        async with self.db.connect() as conn:
            result = await conn.execute(model.ViewTicket.delete_expired())
            if result.rowcount:
                LOG.debug(f"Removed {result.rowcount} expired ViewTickets")

    async def close(self):
        for task in list(self.tasks.values()):
            task.cancel()
        await asyncio.to_thread(self.pool.shutdown)

    def _in_pool(
        self, func: typing.Callable[P, R], *a: P.args, **ka: P.kwargs
    ) -> asyncio.Future[R]:
        loop = asyncio.get_running_loop()
        func = functools.partial(func, *a, **ka)
        return loop.run_in_executor(self.pool, func)

    def _in_pool_ctx(
        self, func: typing.Callable[P, R], *a: P.args, **ka: P.kwargs
    ) -> asyncio.Future[R]:
        return self._in_pool(contextvars.copy_context().run, func, *a, **ka)

    async def start_import(
        self,
        data: typing.AsyncGenerator[bytes, None],
        force_tenant: str | None = None,
        schedule=True,
    ) -> "RecordingImportTask":
        """Copy the provided data stream into the inbox directory and
        schedule a :cls:`RecordingImportTask`.

        This method only waits for the inbox-copy to complete, the task
        itself runs in the background and may take several seconds to
        complete.

        If force_tenant is set, a specific tenant is used and the tenant
        referenced in metadata is ignored. This is useful to import old
        recordings.

        If schedule is False, then the task is just returned and not
        scheduled for execution in the background. It may still be
        picked up and processed by any RecordingManager worker scanning
        the import directory.
        """

        import_id = str(uuid.uuid4())
        tmp = self.inbox_dir / f"{import_id}.temp"
        final = tmp.with_suffix(".tar")
        fp = await self._in_pool(tmp.open, "wb")
        try:
            async for chunk in data:
                await self._in_pool(fp.write, chunk)
            await self._in_pool(fp.close)
            await self._in_pool(tmp.rename, final)
        except BaseException:
            # Fire and forget cleanup
            @self._in_pool
            def cleanup():
                try:
                    fp.close()
                except OSError:
                    pass
                tmp.unlink()

            raise

        task = RecordingImportTask(self, import_id, final, force_tenant)
        if schedule:
            self._schedule(task)
        return task

    def _schedule(self, task: "RecordingImportTask"):
        if task.import_id in self.tasks:
            return

        self.tasks[task.import_id] = task

        async def waiter():
            try:
                async with self.maxtasks:
                    await task.run()
            except asyncio.CancelledError:
                raise
            except BaseException:
                raise
            finally:
                self.tasks.pop(task.import_id, None)

        asyncio.create_task(waiter(), name=f"rec-{task.import_id}")

    def get_storage_dir(self, tenant: str, record_id: str, format: str):
        tenant = _sanity_pathname(tenant)
        record_id = _sanity_pathname(record_id)
        format = _sanity_pathname(format)
        return self.storage_dir / tenant / record_id / format

    async def ensure_state(self, record_id: str, target_state: model.RecordingState):
        """Change the published state of a recording. Sets the new state both in the
        the database and on disk. Returns the old state, or None if the record was
        not found.
        """

        # TODO: This is racy, but the chances are very low. Preventing
        # races here would require a global (db-base) lock?

        # Fetch and update the DB record
        async with self.db.session() as session:
            stmt = model.Recording.select(
                model.Recording.record_id == record_id
            ).options(model.joinedload(model.Recording.tenant))
            record = (await session.execute(stmt)).scalars().one_or_none()
            if not record:
                return None

            old_state = record.state
            if old_state != target_state:
                stmt = model.Recording.update(
                    model.Recording.record_id == record_id
                ).values(state=target_state)
                await session.execute(stmt)
                await session.commit()

        # Unconditionally ensure on-disk state.
        if target_state is model.RecordingState.PUBLISHED:
            action = self.ensure_published
        else:
            action = self.ensure_unpublished
        await asyncio.to_thread(action, record.tenant.name, record.record_id)

        return old_state

    def ensure_published(self, tenant: str, record_id: str):
        """Ensure all available formats for a recording have symlink
        in the public directory on disk."""

        tenant = _sanity_pathname(tenant)
        record_id = _sanity_pathname(record_id)
        try:
            for format_dir in (self.storage_dir / tenant / record_id).iterdir():
                if not format_dir.is_dir():
                    continue
                if format_dir.name.endswith(".temp"):
                    continue
                format_name = format_dir.name
                symlink = self.public_dir / format_name / record_id
                try:
                    if symlink.exists():
                        continue
                    symlink.parent.mkdir(parents=True, exist_ok=True)
                    symlink.symlink_to(
                        format_dir.relative_to(symlink.parent, walk_up=True),
                        target_is_directory=True,
                    )
                    LOG.info(
                        f"Published recording {format_name}/{record_id} ({tenant})"
                    )
                except FileExistsError:
                    continue
        except FileNotFoundError:
            return

    def ensure_unpublished(self, tenant: str, record_id: str):
        """Ensure no formats for this recording has a symlink in the
        public directory on disk."""
        tenant = _sanity_pathname(tenant)
        record_id = _sanity_pathname(record_id)

        for format_dir in self.public_dir.iterdir():
            symlink = format_dir / record_id
            if symlink.is_symlink():
                symlink.unlink(missing_ok=True)
                LOG.info(
                    f"Unpublished recording {format_dir.name}/{record_id} ({tenant})"
                )

    def delete(self, tenant: str, record_id: str):
        """Delete a recording from disk"""
        tenant = _sanity_pathname(tenant)
        record_id = _sanity_pathname(record_id)

        # Unpublish all formats
        self.ensure_unpublished(tenant, record_id)

        # Move files to trash
        store_path = self.storage_dir / tenant / record_id
        deleted_path = (
            self.deleted_dir / tenant / f"{record_id}.{secrets.token_hex(8)}.deleted"
        )

        try:
            deleted_path.parent.mkdir(exist_ok=True, parents=True)
            shutil.move(store_path, deleted_path)
            LOG.info(f"Deleted recording {record_id} ({tenant})")
        except FileNotFoundError:
            pass  #  Already deleted

    def move_tenant(self, record_id: str, current_tenant: str, new_tenant: str):
        current_tenant = _sanity_pathname(current_tenant)
        new_tenant = _sanity_pathname(new_tenant)
        record_id = _sanity_pathname(record_id)

        # Move storage dir to new tenant
        old_path = self.storage_dir / current_tenant / record_id
        new_path = self.storage_dir / new_tenant / record_id
        new_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.move(old_path, new_path)  # This can raise

        # Fix existing symlinks
        for format_dir in self.public_dir.iterdir():
            symlink = format_dir / record_id
            if symlink.is_symlink():
                symlink.unlink()
                symlink.symlink_to(
                    new_path.relative_to(symlink.parent, walk_up=True),
                    target_is_directory=True,
                )


class RecordingImportTask:
    def __init__(
        self,
        importer: RecordingManager,
        import_id: str,
        source: Path,
        force_tenant: str | None = None,
    ):
        self.importer = importer
        self.import_id = import_id
        self.source = source
        self.task_dir = self.importer.work_dir / self.import_id
        self.force_tenant = force_tenant

        self.formats: list[model.PlaybackFormat] = []
        self.errors: list[BaseException] = []
        self.import_done = asyncio.Event()

        self._in_pool = self.importer._in_pool
        self._task: asyncio.Task | None = None

    def cancel(self):
        if self._task:
            self._task.cancel()

    async def wait(self):
        await self.import_done.wait()

    async def run(self):
        try:
            if self._task:
                raise RuntimeError("Task started twice")

            self._task = asyncio.current_task()
            if not self._task:
                raise RuntimeError("Must run in an asyncio task context.")

            await self._run()
        except Exception as exc:
            self.errors.append(exc)
            raise
        finally:
            self.import_done.set()

    async def _run(self):
        # Claim the task directory atomically and give up if it already exists,
        # so only one task will work on this import at any given time.
        try:
            await self._in_pool(self.task_dir.mkdir, parents=True)
        except FileExistsError:
            # TODO: If the task dir was created very recently, log as DEBUG instead.
            # Conflicts are common during a multi-worker restart with a non-empty
            # input dir
            self._log(
                f"Failed to claim work directory: {self.task_dir}", logging.WARNING
            )
            return  # Not an error

        try:
            if not self.source.exists():
                # We may have been scheduled for so long that another process
                # already completed the work for us. Not an error
                return

            # Process this import
            await self._process()

            # Move failed imports to the "failed" directory for human
            # inspection and to prevent them beeing picked up again and
            # again. Successfull imports are just removed.
            if self.errors:
                failed = self.importer.failed_dir / self.source.name
                await self._in_pool(self.source.rename, failed)
            else:
                await self._in_pool(self.source.unlink)

        except asyncio.CancelledError:
            self._log("Task canceled")
            raise
        except Exception as exc:
            self.errors.append(exc)
            self._log("Unhandled exception during import", logging.ERROR, exc_info=exc)
        finally:
            # Un-claim the task directory as quickly and robust as possible.
            tmp = self.task_dir.with_suffix(f".{token_hex()}.temp")
            await asyncio.to_thread(self.task_dir.rename, tmp)
            await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)

    async def _process(self):
        """Process a single import archive."""

        def _extract():
            self._log(f"Extracting: {self.source}")
            with tarfile.open(self.source) as tar:
                tar.extractall(self.task_dir, filter=tarfile.data_filter)
            self._log(f"Extracted: {self.source}")

        try:
            await self._in_pool(_extract)
        except Exception as exc:
            self._log(
                f"Failed to extract import archive: {self.source}",
                logging.ERROR,
                exc_info=exc,
            )
            self.errors.append(exc)
            return

        for metafile in self.task_dir.glob("**/metadata.xml"):
            try:
                self._log(f"Found: {metafile}")
                await self._process_one(metafile)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                self._log(
                    f"Recording failed to import: {metafile}",
                    logging.ERROR,
                    exc_info=exc,
                )
                self.errors.append(exc)

        total = len(self.errors) + len(self.formats)
        if self.errors and self.formats:
            self._log(
                f"Some recordings failed to import ({len(self.errors)} out of {total})",
                logging.ERROR,
            )
        elif self.errors:
            self._log(
                f"All recordings failed to import ({total})",
                logging.ERROR,
            )
        elif self.formats:
            self._log(f"Finished processing {total} recordings")
        else:
            msg = f"No recordings found in: {self.source}"
            self.errors.append(RecordingImportError(msg))
            self._log(msg, logging.ERROR)

    def _copy_format_atomic(self, source_dir: Path, final_dir: Path):
        temp_dir = final_dir.with_suffix(f".{secrets.token_hex()}.temp")

        if final_dir.exists():
            self._log(
                f"Skipping file copy because target directory exists: {final_dir}"
            )
            return

        try:
            self._log(f"Copying files to: {final_dir}")
            temp_dir.mkdir(parents=True)
            shutil.copytree(source_dir, temp_dir, dirs_exist_ok=True)

            try:
                if not final_dir.exists():
                    temp_dir.rename(final_dir)
            except OSError:
                if not final_dir.exists():
                    raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _process_one(self, metafile: Path):
        try:
            xml = await self._in_pool(lxml.etree.parse, metafile)
            assert isinstance(xml, ETree)
            metadata = FormatXMLWrapper(xml)
        except BaseException:
            raise RecordingImportError(f"Failed to parse metadata.xml: {metafile}")

        # Extract more info info from metadata.xml
        tenant_name = self.force_tenant or metadata.bbblb_tenant
        format_name = metadata.format

        # Fetch tenant this record belongs to, or fail
        async with self.importer.db.session() as session:
            try:
                tenant = await model.Tenant.get(session, name=tenant_name)
            except model.NoResultFound:
                raise RecordingImportError(f"Unknown tenant: {tenant_name}")

        # Copy files while we do not hold a database connection, because
        # this may take a while.
        format_dir = self.importer.get_storage_dir(
            tenant.name, metadata.record_id, format_name
        )
        await self._in_pool(self._copy_format_atomic, metafile.parent, format_dir)

        async with self.importer.db.session() as session:
            # Create or fetch recording entity
            record, record_created = await model.get_or_create(
                session,
                model.Recording.select(record_id=metadata.record_id),
                lambda: _record_from_metadata(metadata, tenant),
            )

            if record.tenant_fk != tenant.id:
                raise RecordingImportError(
                    "Recording belongs to different tenant already!"
                )

            if record_created and self.importer.import_unpublished:
                record.state = model.RecordingState.UNPUBLISHED

            # Create or fetch format entity
            format, format_created = await model.get_or_create(
                session,
                model.PlaybackFormat.select(recording=record, format=metadata.format),
                lambda: _format_from_metadata(metadata, record),
            )
            self.formats.append(format)
            await session.commit()

        if format_created:
            await self._trigger_callbacks(metadata)

        # Ensure record is actually published/unpublished
        await self.importer.ensure_state(record.record_id, record.state)

    async def _trigger_callbacks(self, metadata: FormatXMLWrapper):
        # The recording-ready callbacks are triggered for each format,
        # and may be triggered again if a format is imported multiple
        # times. That's the way BBB behaves and most front-ends expect.
        # We never know when the last import happend, so we keep the
        # callbacks around for a while.
        meeting_uuid = metadata.meta.get("bbblb-uuid")
        if not meeting_uuid:
            return

        async with self.importer.db.session() as session:
            stmt = model.Callback.select(
                uuid=meeting_uuid, type=model.CALLBACK_TYPE_REC
            )
            callbacks = (await session.execute(stmt)).scalars().all()

        # Fire callbacks in the background. They may take a while to
        # complete if the front-end is unresponsive.
        payload = {"meeting_id": metadata.unscoped_id, "record_id": metadata.record_id}
        for callback in callbacks:
            asyncio.create_task(
                self.importer.bbb.fire_signed_callback(callback, payload)
            )

    def _log(self, msg, level=logging.INFO, exc_info=None):
        LOG.log(level, f"[{self.import_id}] {msg}", exc_info=exc_info)

    def __str__(self):
        return f"{self.__class__.__name__}({self.import_id})"


##
### Desaster recovery and consistency checker
##


class FixCategory(enum.Enum):
    NO_FIX = enum.auto()
    MISSING = enum.auto()
    ORPHAN = enum.auto()
    PUB_STATE = enum.auto()
    TENANT = enum.auto()


class ConsistencyChecker:
    """(experimental) Find and optionally fix recordings in the database
    that do not match recording data on disk.
    """

    LOGGER = logging.getLogger(__name__ + ".ConsistencyChecker")

    def __init__(self, manager: RecordingManager, autofix: set[FixCategory] | None):
        self.manager = manager
        self.autofix = autofix or set()
        self.autofix.discard(FixCategory.NO_FIX)
        self.unfixed_issues = 0

    def log_progress(self, message):
        """Overrideable method that logs scanning progress.

        The default implementation logs progress at INFO level.
        """
        self.LOGGER.info(message)

    async def ask_human(self, category: FixCategory, issue, solution, **details):
        """Overrideable method that should report issue details and
        decides if an available automatic solution should be applied.

        The defalt implementation logs issue details at WARNING level
        and returns true if :attr:`autofix` contains the given catergory.
        A subclass may override this method to actually ask a human.

        The NO_FIX category cannot be fixed automatically, so the return
        value should always be false.
        """
        do_fix = category in self.autofix
        msg = "Inconsistency found!\n"
        msg += f"  Issue: {issue}\n"
        if details:
            msg += f"  Details: {' '.join(f'{k}={v}' for k, v in details.items())}\n"
        if category is FixCategory.NO_FIX:
            msg += f"  Fix (manual): {solution}"
        elif do_fix:
            msg += f"  Fix: {solution}"
        else:
            msg += f"  Fix (disabled): {solution}"

        self.LOGGER.warning(msg.strip())

        return do_fix

    async def _should_autofix(self, category: FixCategory, issue, fix, **details):
        do_fix = await self.ask_human(category, issue, fix, **details)
        if category is FixCategory.NO_FIX:
            do_fix = False
        if not do_fix:
            self.unfixed_issues += 1
        return do_fix

    async def _report_nofix(self, issue, fix, **details):
        await self.ask_human(FixCategory.NO_FIX, issue, fix, **details)
        self.unfixed_issues += 1

    async def scan(self, prefix=""):
        """Scan and optionally repair recordings in the database.

        This is NOT thread safe. Stop all running instances of BBBLB
        before starting a scan.

        If *prefix* is a non-empty string, then only recordings with
        a matching record_id are scanned or fixed. This can be used to
        split huge recording pools into more manageable chunks.
        """
        ts_start = time.time()

        async with self.manager.db.session() as session:
            # Remember all recording IDs in the database
            known_records = set()
            stmt = model.select(model.Recording.record_id).execution_options(
                yield_per=1000
            )
            if prefix:
                stmt = stmt.where(
                    model.Recording.record_id.startswith(prefix, autoescape=True)
                )
            async for record_id in await session.stream_scalars(stmt):
                known_records.add(record_id)

            self.log_progress(f"Found {len(known_records)} recordings in database")

            # Forward scan: Check disk state and fix db records
            found_records = set()
            for tenant_dir in self.manager.storage_dir.iterdir():
                tenant_name = tenant_dir.name
                tenant = (
                    await session.execute(model.Tenant.select(name=tenant_name))
                ).scalar_one_or_none()

                if not tenant:
                    await self._report_nofix(
                        "Found storage directory for an unknown tenant",
                        "Remove the directory, or add the missing tenant",
                        tenant=tenant_name,
                    )
                    continue

                for record_dir in tenant_dir.iterdir():
                    if prefix and not record_dir.name.startswith(prefix):
                        continue
                    found_records.add(record_dir.name)
                    await self._scan_record(session, tenant, record_dir)

            # All DB entries that were not found on disk are orphans
            if known_records - found_records:
                orphans = list(known_records - found_records)
                stmt = model.Recording.select(model.Recording.id.in_(orphans))
                async for record in await session.stream_scalars(stmt):
                    await self._fix_orphan(session, record)

            await session.commit()
            self.log_progress(
                f"Scan complete after {time.time() - ts_start:.2f} seconds."
            )

    async def _scan_record(
        self, session: model.AsyncSession, tenant: model.Tenant, record_dir: Path
    ):
        self.log_progress(f"Scanning: {tenant.name}/{record_dir.name}")

        record_id = record_dir.name
        stmt = model.Recording.select(record_id=record_id)
        recording = (await session.execute(stmt)).scalar_one_or_none()

        # Find all formats and their state
        formats = {}
        published = set()
        for format_dir in record_dir.iterdir():
            format_name = format_dir.name
            metadata_xml = format_dir / "metadata.xml"

            if not metadata_xml.exists():
                await self._report_nofix(
                    "Recordings format directory does not contain a metadata.xml",
                    "Remove the recording directory or recover missing files",
                    path=format_dir,
                )
                continue

            try:
                xml = await self.manager._in_pool(lxml.etree.parse, metadata_xml)
                metaxml = FormatXMLWrapper(xml)
            except Exception:
                await self._report_nofix(
                    "Failed to parse metadata.xml",
                    "Check and fix the file or recover it from backups",
                    path=metadata_xml,
                )
                continue
            formats[format_name] = metaxml
            symlink = self.manager.public_dir / format_name / record_id
            if symlink.is_symlink():
                published.add(format_name)

        if not formats:
            await self._report_nofix(
                "Recording directory exists but has no valid format",
                "Remove the directory if empty",
                path=record_dir,
            )
            return  # Orphans will be cleaned up later

        if not recording:
            recording = await self._fix_missing(session, tenant, record_id, metaxml)
            if not recording:
                return

        if (await recording.awaitable_attrs.tenant) != tenant:
            await self._fix_tenant(recording, tenant)

        state = (
            model.RecordingState.PUBLISHED
            if published
            else model.RecordingState.UNPUBLISHED
        )
        if len(published) not in (0, len(formats)):
            await self._report_nofix(
                "Recording has both published and unpublished formats",
                "Set the recording to the correct state",
                tenant=tenant.name,
                record_id=record_id,
            )
        elif recording.state is not state:
            await self._fix_visibility(recording, state)

        expected_formats = set(formats)
        known_formats = {f.format for f in await recording.awaitable_attrs.formats}
        for missing in expected_formats - known_formats:
            await self._fix_missing_format(session, recording, formats[missing])
        for orphan in known_formats - expected_formats:
            await self._fix_orphan_format(session, recording, orphan)

        return recording

    async def _fix_missing(
        self,
        session: model.AsyncSession,
        tenant: model.Tenant,
        record_id: str,
        format_meta: FormatXMLWrapper,
    ) -> model.Recording | None:
        if await self._should_autofix(
            FixCategory.MISSING,
            "Recording found on disk but missing in database",
            "Add recording to database",
            tenant=tenant.name,
            record_id=record_id,
        ):
            recording = _record_from_metadata(format_meta, tenant)
            session.add(recording)
            return recording

    async def _fix_orphan(
        self, session: model.AsyncSession, recording: model.Recording
    ):
        if await self._should_autofix(
            FixCategory.ORPHAN,
            "Recording found in database but not on disk",
            "Delete recording from database",
            tenant=recording.tenant.name,
            record_id=recording.record_id,
        ):
            await session.delete(recording)

    async def _fix_tenant(
        self,
        recording: model.Recording,
        tenant: model.Tenant,
    ):
        if await self._should_autofix(
            FixCategory.TENANT,
            "Recording belongs to different tenant",
            "Change owning tenant of recording to match on-disk storage location.",
            tenant=recording.tenant.name,
            record_id=recording.record_id,
        ):
            recording.tenant = tenant

    async def _fix_visibility(
        self,
        recording: model.Recording,
        expected_state: model.RecordingState,
    ):
        if await self._should_autofix(
            FixCategory.PUB_STATE,
            f"Recording state is {expected_state._name_} on disk but not in database",
            f"Set recording state to {expected_state._name_}",
            tenant=recording.tenant.name,
            record_id=recording.record_id,
        ):
            recording.state = expected_state

    async def _fix_missing_format(
        self,
        session: model.AsyncSession,
        recording: model.Recording,
        format_xml: FormatXMLWrapper,
    ):
        if await self._should_autofix(
            FixCategory.MISSING,
            "Recording format found on disk but not in database",
            "Add missing format to database",
            tenant=recording.tenant.name,
            record_id=recording.record_id,
            format=format_xml.format,
        ):
            format = _format_from_metadata(format_xml, recording)
            session.add(format)
            return format

    async def _fix_orphan_format(
        self,
        session: model.AsyncSession,
        recording: model.Recording,
        format: model.PlaybackFormat,
    ) -> model.Recording | None:
        if await self._should_autofix(
            FixCategory.ORPHAN,
            "Recording format found in database but not in disk",
            "Remove orphaned recording format from database",
            tenant=recording.tenant.name,
            record_id=recording.record_id,
            format=format.format,
        ):
            await session.delete(format)
