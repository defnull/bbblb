# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import functools
import io
from pathlib import Path
import uuid
import tarfile
import lxml.etree

import pytest
import pytest_asyncio
from bbblb import model
from datetime import timedelta
from sqlalchemy.ext.asyncio import AsyncSession

from bbblb.services import ServiceRegistry
from bbblb.services.bbb import BBBHelper
from bbblb.services.recording import RecordingImportTask, RecordingManager

from bbblb.settings import BBBLBConfig
from bbblb.utils import remove_scope
from conftest import get_testdata


@functools.cache
def tar_bytes(src_path: Path, root=None):
    buffer = io.BytesIO()
    if not root:
        root = src_path.parent
    elif root not in src_path.parents and root != src_path:
        raise ValueError(f"{root} is not a parent of {src_path}")
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(src_path, arcname=src_path.relative_to(root))
    return buffer.getvalue()


async def to_async_generator(bytes):
    yield bytes


async def run_import(rm: RecordingManager, name, force_tenant=None):
    """Import a (named) test recording and return the finished import task
    and the parsed metadata.xml"""
    rec_path = get_testdata("recordings", name)
    rec_meta = lxml.etree.fromstring(
        get_testdata("recordings", name, "metadata.xml").read_text()
    )
    rec_tar_bytes = tar_bytes(rec_path, rec_path.parent)
    task = await rm.start_import(
        to_async_generator(rec_tar_bytes), schedule=False, force_tenant=force_tenant
    )
    await task.run()
    assert task.import_done.is_set()
    return task, rec_meta


@pytest.mark.parametrize("rec_name", ["video", "presentation"])
async def test_import(
    rec_name: str,
    orm: AsyncSession,
    services: ServiceRegistry,
    test_tenant: model.Tenant,
):
    rm = await services.use(RecordingManager)
    task, meta = await run_import(rm, rec_name)
    assert not task.errors

    all_recs = list(await orm.scalars(model.Recording.select()))
    assert len(all_recs) == 1

    # Check Recording DB entry
    rec = all_recs[0]
    assert rec.tenant == test_tenant
    assert f"{rec.external_id}" == remove_scope(meta.findtext("meta/meetingId") or "")
    assert f"{rec.record_id}" == meta.findtext("id")
    assert rec.participants == int(meta.findtext("participants") or 0)
    # TODO: Test more

    # Check PlaybackFormat DB entry
    assert len(await rec.awaitable_attrs.formats) == 1
    format = rec.formats[0]
    assert format.format == meta.findtext("playback/format")
    # TODO: Test more

    # Check Recording on disk
    sdir = rm.get_storage_dir(test_tenant.name, rec.record_id, format.format)
    assert sdir.exists()
    ldir = rm.public_dir / format.format / rec.record_id
    assert rec.state is model.RecordingState.PUBLISHED
    assert ldir.is_symlink()
    assert ldir.resolve() == sdir
    assert (ldir / "metadata.xml").exists()


async def test_import_config_unpublish(
    orm: AsyncSession,
    config: BBBLBConfig,
    services: ServiceRegistry,
    test_tenant: model.Tenant,
):
    config.RECORDING_IMPORT_UNPUBLISHED = True
    rm = await services.use(RecordingManager)
    task, meta = await run_import(rm, "presentation")
    assert not task.errors

    all_recs = list(await orm.scalars(model.Recording.select()))
    assert len(all_recs) == 1
    rec = all_recs[0]

    assert rec.state is model.RecordingState.UNPUBLISHED
    ldir = rm.public_dir / "presentation" / rec.record_id
    assert not ldir.exists(follow_symlinks=False)


async def test_import_no_tenant(
    services: ServiceRegistry,
    test_tenant: model.Tenant,
):
    rm = await services.use(RecordingManager)
    task, _ = await run_import(rm, "no-tenant")
    assert task.errors
    assert "Invalid or missing tenant information" in str(task.errors)

    task, _ = await run_import(rm, "no-tenant", force_tenant=test_tenant.name)
    assert not task.errors
