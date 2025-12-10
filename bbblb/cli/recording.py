import click
import sqlalchemy.orm

from bbblb import model

from bbblb.services import ServiceRegistry
from bbblb.services.db import DBContext
from bbblb.services.recording import RecordingManager

from . import main, async_command


@main.group()
def recording():
    """Recording management."""


@recording.command("list")
@async_command()
async def _list(obj: ServiceRegistry):
    """List all recordings and their formats"""
    db = await obj.use("db", DBContext)
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
    importer = await obj.use("importer", RecordingManager)

    db = await obj.use("db", DBContext)
    async with db.session() as session, session.begin():
        stmt = model.Recording.select(model.Recording.record_id.in_(record_id))
        for record in (await session.execute(stmt)).scalars().all():
            await session.delete(record)
            importer.delete(record.tenant.name, record.record_id)
            click.echo(f"Deleted {record.record_id}")


@recording.command("import")
@click.option("--tenant", help="Override the tenant found in the recording")
@click.argument("FILE", type=click.Path(dir_okay=True), default="-")
@async_command()
async def _import(obj: ServiceRegistry, tenant: str, file: str):
    """Import one or more recordings from a tar archive"""
    obj.get("importer", RecordingManager, uninitialized_ok=True).auto_import = False
    importer = await obj.use("importer", RecordingManager)

    async def reader(file):
        with click.open_file(file, "rb") as fp:
            while chunk := fp.read(1024 * 64):
                yield chunk

    task = await importer.start_import(reader(file), force_tenant=tenant)
    await task.wait()
    if task.error:
        click.echo(f"ERROR {task.error}")
        raise SystemExit(1)
    click.echo("OK")


@recording.command()
@click.option(
    "--dry-run", "-n", help="Simulate changes without changing anything.", is_flag=True
)
@async_command()
async def remove_orphans(obj: ServiceRegistry, dry_run: bool):
    """Remove recording DB entries that do not exist on disk."""
    db = await obj.use("db", DBContext)
    importer = await obj.use("importer", RecordingManager)
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
