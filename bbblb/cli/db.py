import click
from bbblb.services import ServiceRegistry
from bbblb.services.db import check_migration_state, create_database, migrate_db
from bbblb.settings import BBBLBConfig

from bbblb.cli import async_command, main


@main.group()
def db():
    """Manage database"""


@db.command()
@click.option(
    "--create", help="Create database if needed (only postgres).", is_flag=True
)
@async_command()
async def migrate(obj: ServiceRegistry, create: bool):
    """
    Migrate database to the current schema version.

    WARNING: Make backups!
    """
    config = await obj.use(BBBLBConfig)

    try:
        if create:
            await create_database(config.DB)
        current, target = await check_migration_state(config.DB)
        if current != target:
            click.echo(
                f"Migrating database schema from {current or 'empty'!r} to {target!r}..."
            )
            await migrate_db(config.DB)
            click.echo("Migration complete!")
        else:
            click.echo("Database is up to date. Nothing to do")
    except ConnectionRefusedError as e:
        raise RuntimeError(f"Failed to connect to database: {e}")
    except BaseException as e:
        raise RuntimeError(f"Failed to migrate database: {e}")
