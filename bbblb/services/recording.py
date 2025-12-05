import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
import datetime
import functools
import logging
from pathlib import Path
from secrets import token_hex
import secrets
import shutil
import tarfile
import typing
import uuid
import lxml.etree
import urllib.parse

from bbblb import model, utils
from bbblb.services import BackgroundService
from bbblb.services.bbb import BBBHelper
from bbblb.services.db import DBContext
from bbblb.settings import BBBLBConfig
from bbblb.lib.bbb import ETree, XML, SubElement

LOG = logging.getLogger(__name__)

P = typing.ParamSpec("P")
R = typing.TypeVar("R")

URLPATTERNS = {
    "presentation",
    "{BASEURL}/playback/presentation/player/{RECORD_ID}/*",
    "{BASEURL}/playback/{FORMAT}/{RECORD_ID}/",
}


class RecordingImportError(RuntimeError):
    pass


def playback_to_xml(config: BBBLBConfig, playback: model.PlaybackFormat) -> ETree:
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


class RecordingManager(BackgroundService):
    def __init__(self, config: BBBLBConfig):
        self.base_dir = (config.PATH_DATA / "recordings").resolve()
        self.inbox_dir = self.base_dir / "inbox"
        self.failed_dir = self.base_dir / "failed"
        self.work_dir = self.base_dir / "work"
        self.public_dir = self.base_dir / "public"
        self.storage_dir = self.base_dir / "storage"
        self.deleted_dir = self.base_dir / "deleted"
        max_threads = config.RECORDING_THREADS
        self.maxtasks = asyncio.Semaphore(max_threads)
        self.pool = ThreadPoolExecutor(thread_name_prefix="rec-")
        self.tasks: dict[str, "RecordingImportTask"] = {}

        self.poll_interval = config.POLL_INTERVAL

    async def on_start(self, db: DBContext, bbb: BBBHelper):
        self.db = db
        self.bbb = bbb

        # Create all directories we need, if missing
        for dir in (d for d in self.__dict__.values() if isinstance(d, Path)):
            if dir and not dir.exists():
                await self._in_pool(dir.mkdir, parents=True, exist_ok=True)

        await super().on_start()

    async def run(self):
        try:
            while True:
                try:
                    await asyncio.sleep(self.poll_interval)
                    await self.schedule_waiting()
                    await self.cleanup()
                except asyncio.CancelledError:
                    raise
                except BaseException:
                    LOG.exception("Unhandled recording import error")
                    continue
        finally:
            await self.close()

    async def schedule_waiting(self):
        """Pick up waiting tasks from inbox"""
        for file in self.inbox_dir.glob("*.tar"):
            self._schedule(RecordingImportTask(self, file.stem, file))

    async def cleanup(self):
        # TODO: Cleanup *.failed and *.canceled work directories.
        pass

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
    ):
        """Copy the data stream into the inbox directory and schedule a
        :cls:`RecordingImportTask`. The returned task may take a while to
        complete, this method only waits for the copy operation to inbox to
        complete.

        If fallback_tenant is set, this tenant is used if no tenant info could
        be found in the recording. This is useful to import old recordings.

        If replace_existing is set, any existing recording formats are replaced
        with this new import.

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

    def publish(self, tenant: str, record_id: str):
        """Publish all available formats for a recording.

        Return a list of format names, which may be empty if there were no
        recordings that could be published."""

        tenant = _sanity_pathname(tenant)
        record_id = _sanity_pathname(record_id)
        formats = []
        try:
            for format_dir in (self.storage_dir / tenant / record_id).iterdir():
                if not format_dir.is_dir():
                    continue
                if format_dir.name.endswith(".temp"):
                    continue
                format_name = format_dir.name
                formats.append(format_name)
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
            return []

    def unpublish(self, tenant: str, record_id: str):
        """Unpublish all formats for a given recording."""
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
        tenant = _sanity_pathname(tenant)
        record_id = _sanity_pathname(record_id)

        # Unpublish all formats
        self.unpublish(tenant, record_id)

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
        self._in_pool = self.importer._in_pool
        self._task: asyncio.Task | None = None
        self.error = None

    def cancel(self):
        if not self.error:
            self.error = asyncio.CancelledError()
        if self._task:
            self._task.cancel()

    async def run(self):
        if self._task:
            raise RuntimeError("Task started twice")

        self._task = asyncio.current_task()
        if not self._task:
            raise RuntimeError("Must run in an asyncio task context.")

        try:
            self._breakpoint()
            await self._run()
        except BaseException as exc:
            if not self.error:
                self.error = exc

    def __str__(self):
        return f"{self.__class__.__name__}({self.import_id})"

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
            self.cancel()
            return  # Not an error

        try:
            if not self.source.exists():
                # We may have been scheduled for so long that another process
                # already completed the work for us. Not an error
                return

            # Process this import
            await self._process()

            # Successfull imports are removed from the inbox
            await self._in_pool(self.source.unlink)

        except BaseException as exc:
            if not self.error:
                self.error = exc

            if isinstance(exc, asyncio.CancelledError):
                self._log("Task canceled")
                self.cancel()
                raise

            if isinstance(exc, RecordingImportError):
                self._log(str(exc), logging.ERROR, exc_info=exc)
            else:
                self._log(
                    "Unhandled exception during import", logging.ERROR, exc_info=exc
                )

            # Failed imports need human inspection. Move the archive to the
            # "failed" directory.
            failed = self.importer.failed_dir / self.source.name
            self._in_pool(self.source.rename, failed)
            raise

        finally:
            # Un-claim the task directory as quickly and robust as possible by
            # renaming it first, and do the cleanup later.
            unique = token_hex()
            if isinstance(self.error, asyncio.CancelledError):
                tmp = self.task_dir.with_suffix(f".{unique}.canceled")
            elif self.error:
                tmp = self.task_dir.with_suffix(f".{unique}.failed")
            else:
                tmp = self.task_dir.with_suffix(f".{unique}.done")
            await self._in_pool(self.task_dir.rename, tmp)

            # Do the actual cleanup in the background and do not wait for the result
            self._in_pool(shutil.rmtree, tmp, ignore_errors=True)

    def _breakpoint(self):
        """Raise self.error if it has a value (likely a CancelledError)"""
        if self.error:
            raise self.error

    async def _process(self):
        def _extract():
            self._breakpoint()
            self._log(f"Extracting: {self.source}")
            with tarfile.open(self.source) as tar:
                tar.extractall(self.task_dir, filter=tarfile.data_filter)
            self._log(f"Extracted: {self.source}")

        await self._in_pool(_extract)

        recordings = 0
        errors = []
        for metafile in self.task_dir.glob("**/metadata.xml"):
            try:
                self._breakpoint()
                self._log(f"Found: {metafile}")
                await self._process_one(metafile)
                recordings += 1
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                self._log(
                    f"Recording failed to import: {metafile}",
                    logging.ERROR,
                    exc_info=exc,
                )
                errors.append(exc)

        total = len(errors) + recordings
        if errors and recordings:
            raise RecordingImportError(
                f"Some recordings failed to import ({len(errors)} our of {total})"
            )
        elif errors:
            raise RecordingImportError(f"All recordings failed to import ({total})")
        elif recordings:
            self._log(f"Finished processing {total} recordings")
        else:
            raise RecordingImportError(f"No recordings found in: {self.source}")

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

            self._breakpoint()
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
        except BaseException:
            raise RecordingImportError(f"Failed to parse metadata.xml: {metafile}")

        # Extract info info from metadata.xml
        record_id = xml.findtext("id")
        if not (record_id and utils.RE_RECORD_ID.match(record_id)):
            raise RecordingImportError(
                f"Invalid or missing recording ID: {record_id!r}"
            )
        format_name = xml.findtext("playback/format")
        if not (format_name and utils.RE_FORMAT_NAME.match(format_name)):
            raise RecordingImportError(
                f"Invalid or missing playback format name: {format_name!r}"
            )
        tenant_name = self.force_tenant or xml.findtext("meta/bbblb-tenant")
        if not (tenant_name and utils.RE_TENANT_NAME.match(tenant_name)):
            raise RecordingImportError(
                f"Invalid or missing tenant information: {tenant_name!r}"
            )
        metatags = xml.find("meta")
        assert metatags is not None  # For typing, we know it's there
        meta = {tag.tag: tag.text for tag in metatags if tag.text}
        external_id = meta["meetingId"] = utils.remove_scope(meta["meetingId"])
        started = datetime.datetime.fromtimestamp(
            int(xml.findtext("start_time") or 0) / 1000, tz=datetime.timezone.utc
        )
        ended = datetime.datetime.fromtimestamp(
            int(xml.findtext("end_time") or 0) / 1000, tz=datetime.timezone.utc
        )
        participants = int(xml.findtext("participants") or 0)
        state = model.RecordingState.UNPUBLISHED
        playback_node = xml.find("playback")
        assert playback_node is not None  # For typing, we know it's there

        # Fetch tenant this record belongs to, or fail
        async with self.importer.db.session() as session:
            try:
                tenant = await model.Tenant.get(session, name=tenant_name)
            except model.NoResultFound:
                raise RecordingImportError(f"Unknown tenant: {tenant_name}")

        # Copy files while we do not hold a database connection, because
        # this may take a while.
        self._breakpoint()
        format_dir = self.importer.get_storage_dir(tenant.name, record_id, format_name)
        await self._in_pool(self._copy_format_atomic, metafile.parent, format_dir)
        self._breakpoint()

        # Create or fetch recording entity
        async with self.importer.db.session() as session:
            stmt = model.Recording.select(record_id=record_id)
            record, record_created = await model.get_or_create(
                session,
                stmt,
                lambda: model.Recording(
                    tenant=tenant,
                    record_id=record_id,
                    external_id=external_id,
                    state=state,
                    started=started,
                    ended=ended,
                    participants=participants,
                    meta=meta,
                ),
            )
            if not record_created:
                if record.tenant_fk != tenant.id:
                    raise RecordingImportError("Recording belongs to different tenant!")
                # TODO: Merge existing with new record?

        # Create or fetch format entity
        async with self.importer.db.session() as session:
            stmt = model.PlaybackFormat.select(recording=record, format=format_name)
            format, format_created = await model.get_or_create(
                session,
                stmt,
                lambda: model.PlaybackFormat(
                    recording=record,
                    format=format_name,
                    xml=lxml.etree.tostring(playback_node).decode("UTF-8"),
                ),
            )
            if not format_created:
                pass  # TODO: Merge existing with new format?

        # The recording-ready callbacks are triggered for each format,
        # and may be triggered again if a format is imported multiple
        # times. That#s the way BBB behaves and most front-ends expect.
        # We never know when the last import happend, so we keep the
        # callbacks around for a while. (TODO)
        if "bbblb-uuid" in record.meta:
            async with self.importer.db.session() as session:
                stmt = model.Callback.select(
                    uuid=record.meta["bbblb-uuid"], type=model.CALLBACK_TYPE_REC
                )
                callbacks = (await session.execute(stmt)).scalars().all()

            # Fire callbacks in the background, they may take a while to
            # complete if the front-end is unresponsive.
            for callback in callbacks:
                asyncio.create_task(
                    self.importer.bbb.fire_callback(
                        callback,
                        {"meeting_id": external_id, "record_id": record_id},
                        clear=False,
                    )
                )

    def _log(self, msg, level=logging.INFO, exc_info=None):
        LOG.log(level, f"[{self.import_id}] {msg}", exc_info=exc_info)
