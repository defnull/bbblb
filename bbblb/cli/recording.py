import asyncio
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
    # Disable auto-import for use in a cli context
    obj.get(RecordingManager, uninitialized_ok=True).auto_import = False


@recording.command("list")
@async_command()
async def _list(obj: ServiceRegistry):
    """List all recordings and their formats"""
    db = await obj.use(DBContext)
    async with db.session() as session, session.begin():
        stmt = model.Recording.select().options(
            sqlalchemy.orm.joinedload(model.Recording.tenant),
            sqlalchemy.orm.selectinload(model.Recording.formats),
        )
        for record in (await session.execute(stmt)).scalars():
            click.echo(
                f"{record.tenant.name} {record.record_id} {','.join(f.format for f in record.formats)}"
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


@recording.command()
@click.argument("record_id", nargs=-1)
@async_command()
async def publish(obj: ServiceRegistry, record_id):
    """Publish recordings"""
    await _change_publish_flag(obj, record_id, model.RecordingState.PUBLISHED)


@recording.command()
@click.argument("record_id", nargs=-1)
@async_command()
async def unpublish(obj: ServiceRegistry, record_id):
    """Unpublish recordings"""
    await _change_publish_flag(obj, record_id, model.RecordingState.UNPUBLISHED)


async def _change_publish_flag(
    obj: ServiceRegistry, record_id, state: model.RecordingState
):
    importer = await obj.use(RecordingManager)
    db = await obj.use(DBContext)

    async with db.session() as session:
        stmt = model.Recording.select(model.Recording.record_id.in_(record_id)).options(
            sqlalchemy.orm.joinedload(model.Recording.tenant)
        )
        records = (await session.execute(stmt)).scalars().all()
        for record in records:
            if record.state != state:
                record.state = state
                await session.commit()
            if state == model.RecordingState.PUBLISHED:
                await asyncio.to_thread(
                    importer.publish, record.tenant.name, record.record_id
                )
            else:
                await asyncio.to_thread(
                    importer.unpublish, record.tenant.name, record.record_id
                )


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
        if (
            publish is True
            and format.recording.started != model.RecordingState.PUBLISHED
        ):
            await _change_publish_flag(
                obj, [format.recording.record_id], model.RecordingState.PUBLISHED
            )
        elif (
            publish is False
            and format.recording.started != model.RecordingState.UNPUBLISHED
        ):
            await _change_publish_flag(
                obj, [format.recording.record_id], model.RecordingState.UNPUBLISHED
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
