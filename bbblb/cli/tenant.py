import re
from bbblb import model
from bbblb.settings import config as cfg
import secrets
import click

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
@async_command(db=True)
async def create(update: bool, name: str, realm: str | None, secret: str | None):
    async with model.session() as session:
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
@async_command(db=True)
async def remove(name: str):
    async with model.session() as session:
        tenant = (
            await session.execute(model.Tenant.select(name=name))
        ).scalar_one_or_none()
        if not tenant:
            click.echo(f"Tenant {name!r} not found")
            return
        await session.delete(tenant)
        await session.commit()
        click.echo(f"Tenant {name!r} removed")


@tenant.command("list")
@async_command(db=True)
async def list_(with_secrets=False):
    """List all tenants with their realms and secrets."""
    async with model.session() as session:
        tenants = (await session.execute(model.Tenant.select())).scalars()
        for tenant in tenants:
            out = f"{tenant.name} {tenant.realm} {tenant.secret}"
            click.echo(out)


@tenant.command()
@click.option(
    "--clear", help="Remove all overrides not mentioned during this call.", is_flag=True
)
@click.argument("name")
@click.argument("overrides", nargs=-1, metavar="NAME=VALUE")
@async_command(db=True)
async def override(clear: bool, name: str, overrides: list[str]):
    """Override create call parameters.

    You can define any number of create parameter overrides per tenant as
    PARAM=VALUE pairs. PARAM should match a BBB create call API parameter
    and the given VALUE will be enforced on all future create calls
    issued by this tenant. If VALUE is empty, then the parameter will be
    removed from create calls.

    Instead of the '=' operator you can also use '?' to define a fallback,
    '<' to define a maximum value for numeric parameters (e.g. duration,
    paxParticipants), or '+' to add items to a comma separated list
    parameter (e.g. disableFeatures).
    """
    async with model.session() as session:
        tenant = (
            await session.execute(model.Tenant.select(name=name))
        ).scalar_one_or_none()
        if not tenant:
            click.echo(f"Tenant {name!r} not found")
            raise SystemExit(1)

        if clear:
            tenant.clear_overrides()
        for override in overrides:
            m = re.match("^([a-zA-Z0-9-_]+)([=?<-])(.*)$", override)
            if not m:
                click.echo(f"Failed to parse override {override!r}")
                raise SystemExit(1)
            name, operator, value = m.groups()
            tenant.add_setting(name, operator, value)

        session.commit()
