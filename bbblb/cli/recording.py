# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import click
import sqlalchemy.orm

from bbblb import model

from bbblb.services import ServiceRegistry
from bbblb.services.db import DBContext
from bbblb.services.recording import RecordingManager

from . import main, async_command


@main.group()
@async_command()
async def recording(obj: ServiceRegistry):
    """Recording management."""
    pass


@recording.command("list")
@click.option("--tenant", help="Filter by tenant")
@click.option("--format", help="Filter by format")
@async_command()
async def _list(obj: ServiceRegistry, tenant: str, format: str):
    """List all recordings and their formats"""
    db = await obj.use(DBContext)
    async with db.session() as session, session.begin():
        stmt = (
            model.Recording.select()
            .join(model.Recording.tenant)
            .options(sqlalchemy.orm.contains_eager(model.Recording.tenant))
        )
        if tenant:
            stmt = stmt.where(model.Tenant.name == tenant)
        if format:
            stmt = stmt.where(
                model.Recording.formats.any(model.PlaybackFormat.format == format)
            )
        async for record in await session.stream_scalars(stmt):
            format_names = (
                [format]
                if format
                else [f.format for f in await record.awaitable_attrs.formats]
            )
            click.echo(
                f"{record.tenant.name} {record.record_id} {record.state} {','.join(format_names)}"
            )


@recording.command("delete")
@click.argument("record_id", nargs=-1)
@async_command()
async def _delete(obj: ServiceRegistry, record_id):
    """Delete recordings (all formats)"""
    importer = await obj.use(RecordingManager)

    db = await obj.use(DBContext)
    async with db.session() as session, session.begin():
        stmt = model.Recording.select(model.Recording.record_id.in_(record_id))
        for record in (await session.execute(stmt)).scalars().all():
            await session.delete(record)
            importer.delete(record.tenant.name, record.record_id)
            click.echo(f"Deleted {record.record_id}")


async def _ensure_state(
    rm: RecordingManager, record_id: str, state: model.RecordingState
):
    old_state = await rm.ensure_state(record_id, state)
    if not old_state:
        click.echo(f"Not found: {record_id}")
    elif old_state is not state:
        click.echo(f"Published: {record_id}")


@recording.command()
@click.argument("record_id", nargs=-1)
@async_command()
async def publish(obj: ServiceRegistry, record_id):
    """Publish recordings"""
    importer = await obj.use(RecordingManager)
    for item in record_id:
        await _ensure_state(importer, item, model.RecordingState.PUBLISHED)


@recording.command()
@click.argument("record_id", nargs=-1)
@async_command()
async def unpublish(obj: ServiceRegistry, record_id):
    """Unpublish recordings"""
    importer = await obj.use(RecordingManager)
    for item in record_id:
        await _ensure_state(importer, item, model.RecordingState.UNPUBLISHED)


@recording.command("import")
@click.option("--tenant", help="Override the tenant found in the recording")
@click.option(
    "--publish/--unpublish",
    help="Publish or unpublish recording after import",
    default=None,
)
@click.argument("FILE", type=click.Path(dir_okay=True), default="-")
@async_command()
async def _import(obj: ServiceRegistry, tenant: str, publish: bool | None, file: str):
    """Import one or more recordings from a tar archive"""
    importer = await obj.use(RecordingManager)

    async def reader(file):
        with click.open_file(file, "rb") as fp:
            while chunk := fp.read(1024 * 64):
                yield chunk

    task = await importer.start_import(reader(file), force_tenant=tenant)
    await task.wait()

    for format in task.formats:
        click.echo(
            f"Imported: {format.recording.tenant.name}/{format.recording.record_id} ({format.format})"
        )
        if publish is True:
            await _ensure_state(
                importer, format.recording.record_id, model.RecordingState.PUBLISHED
            )
        elif publish is False:
            await _ensure_state(
                importer, format.recording.record_id, model.RecordingState.UNPUBLISHED
            )
    for error in task.errors:
        click.echo(f"ERROR: {error}")
    if task.errors:
        raise SystemExit(1)


@recording.command()
@click.option(
    "--dry-run", "-n", help="Do not actually remove any recordings.", is_flag=True
)
@async_command()
async def remove_orphans(obj: ServiceRegistry, dry_run: bool):
    """Remove recording DB entries that do not exist on disk."""
    db = await obj.use(DBContext)
    importer = await obj.use(RecordingManager)
    async with db.session() as session, session.begin():
        stmt = model.Recording.select().options(
            sqlalchemy.orm.joinedload(model.Recording.tenant),
            sqlalchemy.orm.selectinload(model.Recording.formats),
        )
        records = await session.execute(stmt)
        for record in records.scalars():
            populated = False
            for format in record.formats:
                sdir = importer.get_storage_dir(
                    record.tenant.name,
                    record.record_id,
                    format.format,
                )
                if sdir.exists():
                    populated = True
                    continue
                click.echo(
                    f"Deleting orphan format: {record.tenant.name}/{record.record_id}/{format.format}"
                )
                await session.delete(format)
            if not populated:
                click.echo(
                    f"Deleting record without formats: {record.tenant.name}/{record.record_id}"
                )
                await session.delete(record)

        if dry_run:
            click.echo("Rolling back changes (dry run)")
            await session.rollback()
