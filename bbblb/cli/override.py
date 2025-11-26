import re
from bbblb import model
import click

from bbblb.services import ServiceRegistry
from bbblb.services.db import DBContext

from . import main, async_command


@main.group()
def override():
    """Manage meeting overrides"""


@override.command("list")
@click.argument("tenant", required=False)
@async_command()
async def list_(obj: ServiceRegistry, tenant: str):
    """List overrides for all or a specific tenant."""
    db = await obj.use("db", DBContext)

    async with db.session() as session:
        if tenant:
            stmt = model.Tenant.select(name=tenant)
        else:
            stmt = model.Tenant.select()

        tenants = (await session.execute(stmt)).scalars().all()
        if tenant and not tenants:
            click.echo(f"Tenant {tenant!r} not found")
            raise SystemExit(1)

        for ten in tenants:
            for key, value in sorted(ten.overrides.items()):
                click.echo(f"{ten.name}: {key}{value}")


@override.command("set")
@click.option(
    "--clear", help="Remove all overrides not mentioned during this call.", is_flag=True
)
@click.argument("tenant")
@click.argument("overrides", nargs=-1, metavar="NAME=VALUE")
@async_command()
async def set_(obj: ServiceRegistry, clear: bool, tenant: str, overrides: list[str]):
    """Override create call parameters for a given tenant.

    You can define any number of create parameter overrides per tenant as
    PARAM=VALUE pairs. PARAM should match a BBB create call API parameter
    and the given VALUE will be enforced on all future create calls
    issued by this tenant. If VALUE is empty, then the parameter will be
    removed from create calls.

    Instead of the '=' operator you can also use '?' to define a fallback
    instead of an override, '<' to define a maximum value for numeric
    parameters (e.g. duration or maxParticipants), or '+' to add items
    to a comma separated list parameter (e.g. disabledFeatures).
    """
    db = await obj.use("db", DBContext)
    async with db.session() as session:
        db_tenant = (
            await session.execute(model.Tenant.select(name=tenant))
        ).scalar_one_or_none()
        if not db_tenant:
            click.echo(f"Tenant {tenant!r} not found")
            raise SystemExit(1)

        if clear:
            db_tenant.clear_overrides()
        elif not overrides:
            click.echo("Set at least one override, see --help")
            raise SystemExit(1)

        for override in overrides:
            m = re.match("^([a-zA-Z0-9-_]+)([=?<+])(.*)$", override)
            if not m:
                click.echo(f"Failed to parse override {override!r}")
                raise SystemExit(1)
            name, operator, value = m.groups()
            assert operator in ("=", "?", "<", "+")
            db_tenant.add_override(name, operator, value)

        await session.commit()
        click.echo("OK")


@override.command()
@click.argument("tenant")
@click.argument("overrides", nargs=-1, metavar="NAME")
@async_command()
async def unset(obj: ServiceRegistry, tenant: str, overrides: list[str]):
    """Remove overrides from a given tenant."""
    db = await obj.use("db", DBContext)

    async with db.session() as session:
        db_tenant = (
            await session.execute(model.Tenant.select(name=tenant))
        ).scalar_one_or_none()
        if not db_tenant:
            click.echo(f"Tenant {tenant!r} not found")
            raise SystemExit(1)

        for override in overrides:
            db_tenant.remove_override(override)

        await session.commit()
        click.echo("OK")
