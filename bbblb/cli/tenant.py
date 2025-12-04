import json
import re
from bbblb import model
from bbblb.cli.server import _end_meeting
from bbblb.services import ServiceRegistry
from bbblb.services.db import DBContext
import secrets
import click

from bbblb.settings import BBBLBConfig

from . import main, async_command


@main.group()
def tenant():
    """Manage tenants"""


@tenant.command()
@click.option(
    "--update", "-U", help="Update the tenant with the same name, if any.", is_flag=True
)
@click.option(
    "--realm", help="Set tenant realm. Defaults to '{name}.{DOMAIN}' for new tenants."
)
@click.option(
    "--secret",
    help="Set the tenant secret. Defaults to a randomly generated string for new tenants.",
)
@click.argument("name")
@async_command()
async def create(
    obj: ServiceRegistry, update: bool, name: str, realm: str | None, secret: str | None
):
    db = await obj.use("db", DBContext)
    cfg = await obj.use("config", BBBLBConfig)
    async with db.session() as session:
        tenant = (
            await session.execute(model.Tenant.select(name=name))
        ).scalar_one_or_none()
        if tenant and not update:
            raise RuntimeError(f"Tenant with name {name} already exists.")
        action = "UPDATED"
        if not tenant:
            action = "CREATED"
            tenant = model.Tenant(name=name)
            session.add(tenant)
        tenant.realm = realm or tenant.realm or f"{name}.{cfg.DOMAIN}"
        tenant.secret = secret or tenant.secret or secrets.token_urlsafe(16)
        await session.commit()
        click.echo(
            f"{action}: tenant name={tenant.name} realm={tenant.realm} secret={tenant.secret}"
        )


@tenant.command()
@click.argument("name")
@async_command()
async def enable(obj: ServiceRegistry, name: str):
    """Enable a tenant"""
    db = await obj.use("db", DBContext)
    async with db.session() as session:
        tenant = (
            await session.execute(model.Tenant.select(name=name))
        ).scalar_one_or_none()
        if not tenant:
            click.echo(f"Tenant {name!r} not found")
            return
        if tenant.enabled:
            click.echo(f"Tenant {tenant!r} already enabled")
            return
        tenant.enabled = True
        await session.commit()
        click.echo(f"Tenant {tenant!r} disabled")


@tenant.command()
@click.argument("name")
@click.option("--nuke", help="End all meetings owned by this tenant.", is_flag=True)
@async_command()
async def disable(obj: ServiceRegistry, name: str, nuke: bool):
    """Disable a tenant"""
    db = await obj.use("db", DBContext)
    async with db.session() as session:
        tenant = (
            await session.execute(model.Tenant.select(name=name))
        ).scalar_one_or_none()
        if not tenant:
            click.echo(f"Tenant {name!r} not found")
            return
        if not tenant.enabled:
            click.echo(f"Tenant {tenant!r} already disabled")
            return
        tenant.enabled = False
        await session.commit()
        if nuke:
            meetings = await tenant.awaitable_attrs.meetings
            for meeting in meetings:
                await _end_meeting(obj, meeting)

        click.echo(f"Tenant {tenant!r} disabled")


@tenant.command("list")
@async_command()
async def list_(obj: ServiceRegistry):
    """List all tenants with their realms and secrets."""
    db = await obj.use("db", DBContext)
    async with db.session() as session:
        tenants = (await session.execute(model.Tenant.select())).scalars()
        for tenant in tenants:
            out = f"{tenant.name} {tenant.realm} {tenant.secret} {json.dumps(tenant.overrides)}"
            click.echo(out)


@tenant.group()
def override():
    """Manage meeting overrides"""


@override.command("list")
@click.argument("tenant", required=False)
@async_command()
async def override_list(obj: ServiceRegistry, tenant: str):
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
async def override_set(
    obj: ServiceRegistry, clear: bool, tenant: str, overrides: list[str]
):
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


@override.command("unset")
@click.argument("tenant")
@click.argument("overrides", nargs=-1, metavar="NAME")
@async_command()
async def overide_unset(obj: ServiceRegistry, tenant: str, overrides: list[str]):
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
