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

from bbblb import bbblib, model, utils
from bbblb.settings import config

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


def playback_xml(playback: model.PlaybackFormat, root_tag: str = "format"):
    xml = lxml.etree.fromstring(playback.xml)
    xml.tag = root_tag
    playback_domain = config.PLAYBACK_DOMAIN.format(
        DOMAIN=config.DOMAIN, REALM=playback.recording.tenant.realm
    )
    format = playback.format

    def fix(url):
        url = urllib.parse.urlparse(url)
        url = url._replace(scheme="https", netloc=playback_domain)
        if url.path.startswith(f"/{format}"):
            url = url._replace(path=f"/playback{url.path}")
        return url.geturl()

    xml.find("link").text = fix(xml.find("link").text)
    for node in xml.iterfind("extensions/preview/images/image"):
        node.text = fix(node.text)

    return xml


def _sanity_pathname(name: str):
    name = name.strip()
    if not name:
        raise ValueError("Path name cannot be empty")
    for bad in "/\\:":
        if bad in name:
            raise ValueError(f"Unexpected character in path name: {name:r}")
    return name


class RecordingImporter:
    def __init__(self, basedir: Path, concurrency=2):
        self.base_dir = basedir.resolve()
        self.inbox_dir = self.base_dir / "inbox"
        self.failed_dir = self.base_dir / "failed"
        self.work_dir = self.base_dir / "work"
        self.public_dir = self.base_dir / "public"
        self.storage_dir = self.base_dir / "storage"
        self.deleted_dir = self.base_dir / "deleted"

        self.maxtasks = asyncio.Semaphore(concurrency)
        self.pool = ThreadPoolExecutor(
            max_workers=concurrency + 2, thread_name_prefix="recording-importer-"
        )
        self.tasks: dict[str, "RecordingImportTask"] = {}

    async def __aenter__(self):
        await self.init()
        return self

    async def __aexit__(self, *a):
        await self.close()

    async def init(self):
        # Create all directories we need, if missing
        for dir in (d for d in self.__dict__.values() if isinstance(d, Path)):
            if dir and not dir.exists():
                await self._in_pool(dir.mkdir, parents=True, exist_ok=True)
        # TODO: Cleanup *.failed and *.canceled work directories.

        # Pick up task from the inbox directory
        for file in self.inbox_dir.glob("*.tar"):
            self._schedule(RecordingImportTask(self, file.stem, file))

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

        asyncio.create_task(waiter(), name=str(task))

    def get_storage_dir(self, tenant: str, record_id: str, format: str):
        tenant = _sanity_pathname(tenant)
        record_id = _sanity_pathname(record_id)
        format = _sanity_pathname(format)
        return self.storage_dir / tenant / record_id / format

    def ensure_state(self, tenant: str, record_id: str, published: bool):
        """Publish or unpublish all formats of a recording.
        Raise FileNotFoundError if the recording is not found."""
        # format_dir  = storage_dir / tenant / record_id / format
        # public_link = public_dir / format / record_id
        tenant = _sanity_pathname(tenant)
        record_id = _sanity_pathname(record_id)
        for format_dir in (self.storage_dir / tenant / record_id).iterdir():
            if not format_dir.is_dir():
                continue
            if format_dir.name.endswith(".temp"):
                continue

            public_link = self.public_dir / format_dir.name / record_id

            if published:
                try:
                    public_link.parent.mkdir(parents=True, exist_ok=True)
                    public_link.symlink_to(
                        format_dir.relative_to(public_link.parent, walk_up=True),
                        target_is_directory=True,
                    )
                    LOG.info(f"Published recording {record_id} ({tenant})")
                except FileExistsError:
                    pass
            elif public_link.is_symlink():
                public_link.unlink(missing_ok=True)
                LOG.info(f"Unpublished recording {record_id} ({tenant})")

    def delete(self, tenant: str, record_id: str):
        tenant = _sanity_pathname(tenant)
        record_id = _sanity_pathname(record_id)
        store_path = self.storage_dir / tenant / record_id
        deleted_path = self.storage_dir / tenant / record_id
        if not store_path.exists():
            return  # Nothing to delete
        if deleted_path.exists():
            raise RuntimeError(
                "Cannot delete recording {record_id} ({tenant}), trashbin path already exists: {deleted_path}"
            )
        self.ensure_state(tenant, record_id, False)

        shutil.move(store_path, deleted_path)
        LOG.info(f"Deleted recording {record_id} ({tenant})")


class RecordingImportTask:
    def __init__(
        self,
        importer: RecordingImporter,
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
                self._log(f"Opened: {tar}")
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
        except BaseException:
            raise RecordingImportError(f"Failed to parse metadata.xml: {metafile}")

        record_id = str(xml.findtext("id") or "")
        if not utils.RE_RECORD_ID.match(record_id):
            raise RecordingImportError(
                f"Invalid or missing recording ID: {record_id:r}"
            )

        format_name = str(xml.findtext("playback/format") or "")
        if not utils.RE_FORMAT_NAME.match(format_name):
            raise RecordingImportError(
                f"Invalid or missing playback format name: {format_name:r}"
            )

        tenant_name = str(self.force_tenant or xml.findtext("meta/bbblb-tenant") or "")
        if not utils.RE_TENANT_NAME.match(tenant_name):
            raise RecordingImportError(
                f"Invalid or missing tenant information: {tenant_name:r}"
            )

        async with model.scope(autocommit=True) as session:
            try:
                tenant = (
                    await session.execute(model.Tenant.select(name=tenant_name))
                ).scalar_one()
            except model.NoResultFound:
                raise RecordingImportError(f"Unknown tenant: {tenant_name}")

        meta = {tag.tag: tag.text for tag in xml.find("meta")}
        external_id = meta["meetingId"] = utils.remove_scope(meta["meetingId"])
        started = datetime.datetime.fromtimestamp(
            int(xml.findtext("start_time")) / 1000, tz=datetime.timezone.utc
        )
        ended = datetime.datetime.fromtimestamp(
            int(xml.findtext("end_time")) / 1000, tz=datetime.timezone.utc
        )
        participants = int(xml.findtext("participants"))
        state = model.RecordingState.UNPUBLISHED

        # Copy files first, so the time consuming part is done before the API
        # returns new entries.
        self._breakpoint()
        format_dir = self.importer.get_storage_dir(tenant.name, record_id, format_name)
        await self._in_pool(self._copy_format_atomic, metafile.parent, format_dir)
        self._breakpoint()

        # Get or create the Recording entry
        async with model.scope(autocommit=True) as session:
            recording, recording_created = await model.get_or_create(
                session,
                model.Recording.select(record_id=record_id),
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

            if recording.tenant_fk != tenant.id:
                raise RecordingImportError("Recording belongs to different tenant!")

        # New recordings are unpublished, but existing recordings may be public
        # already and the new format may need to be published before the DB
        # entry is created and the API returns it as an available format.
        if recording.state == model.RecordingState.PUBLISHED:
            await self._in_pool(
                self.importer.ensure_state, tenant.name, record_id, True
            )

        # Get or create the RecordingFormat entry
        async with model.scope(autocommit=True) as session:
            format, format_created = await model.get_or_create(
                session,
                model.PlaybackFormat.select(recording=recording, format=format_name),
                lambda: model.PlaybackFormat(
                    recording=recording,
                    format=format_name,
                    xml=lxml.etree.tostring(xml.find("playback")).decode("UTF-8"),
                ),
            )

        self._breakpoint()

        # Fire recording-ready callbacks, if there are any. We fire them for
        # every successfull import of a new format, and assume the frontend
        # handles repeated calls. We have to do this because BBB may import
        # formats individually, so new formats may appear after the first
        # callback was triggered.
        if format_created:
            callbacks = []
            async with model.scope(autocommit=True) as session:
                uuid = meta.get("bbblb-uuid", None)
                if uuid:
                    stmt = model.Callback.select(
                        uuid=uuid, type=model.CALLBACK_TYPE_REC
                    )
                    callbacks = (await session.execute(stmt)).scalars()
            for callback in callbacks:
                asyncio.create_task(
                    bbblib.fire_callback(
                        callback,
                        {"meeting_id": external_id, "record_id": record_id},
                        clear=False,
                    )
                )
                # TODO: Cleanup old callbacks.

    def _log(self, msg, level=logging.INFO, exc_info=None):
        LOG.log(level, f"[{self.import_id}] {msg}", exc_info=exc_info)
