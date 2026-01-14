from bbblb import model
from bbblb.cli.server import _end_meeting
from bbblb.services import ServiceRegistry
from bbblb.services.db import DBContext
import secrets
import click

from bbblb.settings import BBBLBConfig

from . import Table, main, async_command


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
    db = await obj.use(DBContext)
    cfg = await obj.use(BBBLBConfig)
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
    db = await obj.use(DBContext)
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
    db = await obj.use(DBContext)
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
@Table.option
@async_command()
async def list_(obj: ServiceRegistry, table_format: str):
    """List all tenants with their realms and secrets."""
    db = await obj.use(DBContext)
    tbl = Table()
    async with db.session() as session:
        tenants = (await session.execute(model.Tenant.select())).scalars()
        for tenant in tenants:
            tbl.row(
                tenant=tenant.name,
                realm=tenant.realm,
                enabled=tenant.enabled,
                secret=tenant.secret,
            )
    tbl.print(format=table_format)
