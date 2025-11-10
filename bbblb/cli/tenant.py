from bbblb import model
from bbblb.settings import config as cfg
import secrets
import click

from . import main, run_async


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
@run_async
async def create(update: bool, name: str, realm: str | None, secret: str | None):
    await model.init_engine(cfg.DB)
    async with model.AsyncSessionMaker() as session:
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
@run_async
async def remove(name: str):
    await model.init_engine(cfg.DB)
    async with model.AsyncSessionMaker() as session:
        tenant = (
            await session.execute(model.Tenant.select(name=name))
        ).scalar_one_or_none()
        if not tenant:
            click.echo(f"Tenant {name!r} not found")
            return
        await session.delete(tenant)
        await session.commit()
        click.echo(f"Tenant {name!r} removed")


@tenant.command()
@run_async
async def list(with_secrets=False):
    """List all tenants with their realms and secrets."""
    await model.init_engine(cfg.DB)
    async with model.AsyncSessionMaker() as session:
        tenants = (await session.execute(model.Tenant.select())).scalars()
        for tenant in tenants:
            out = f"{tenant.name} {tenant.realm} {tenant.secret}"
            click.echo(out)
