import click
import bbblb
from bbblb.settings import config

from bbblb.cli import async_command, main
import bbblb.model


@main.group()
def db():
    """Manage database"""


@db.command()
@click.option(
    "--create", help="Create database if needed (only postgres).", is_flag=True
)
@async_command(db=False)
async def migrate(create: bool):
    """
    Migrate database to the current schema version.

    WARNING: Make backups!
    """

    try:
        if create:
            await bbblb.model.create_database(config.DB)
        current, target = await bbblb.model.check_migration_state(config.DB)
        if current != target:
            click.echo(
                f"Migrating database schema from {current or 'empty'!r} to {target!r}..."
            )
            await bbblb.model.migrate_db(config.DB)
            click.echo("Migration complete!")
        else:
            click.echo("Database is up to date. Nothing to do")
    except ConnectionRefusedError as e:
        raise RuntimeError(f"Failed to connect to database: {e}")
    except BaseException as e:
        raise RuntimeError(f"Failed to migrate database: {e}")
