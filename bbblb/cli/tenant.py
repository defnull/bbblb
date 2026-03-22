# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later


from bbblb import model
from bbblb.cli.server import _end_meeting
from bbblb.services import ServiceRegistry
from bbblb.services.db import DBContext
import secrets
import click

from bbblb.settings import BBBLBConfig

from . import MultiChoice, Table, main, async_command


@main.group()
def tenant():
    """Manage tenants."""


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
    """Create a new tenant."""
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
    """Enable a tenant."""
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
    """Disable (lock out) a tenant."""
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
@click.option(
    "--with-overrides",
    help="Include overrides in listing.",
    is_flag=True,
)
@click.option(
    "--with-secret",
    help="Include secret in listing.",
    is_flag=True,
)
@async_command()
async def list_(
    obj: ServiceRegistry, table_format: str, with_overrides: bool, with_secret: bool
):
    """List all tenants and their configuration."""
    db = await obj.use(DBContext)
    tbl = Table()
    tbl.headers(tenant="Tenant", realm="Realm", enabled="Enabled")
    if with_secret:
        tbl.headers(secret="Secret")
    if with_overrides:
        tbl.headers(create="Override: create")
        tbl.headers(join="Override: join")

    async with db.session() as session:
        tenants = (await session.execute(model.Tenant.select())).scalars()
        for tenant in tenants:
            extras = {}
            if with_secret:
                extras["secret"] = tenant.secret
            if with_overrides:
                overrides = await tenant.awaitable_attrs.overrides  # type: list[model.TenantOverride]
                extras["create"] = "; ".join(
                    sorted(
                        f"{o.param}{o.op}{o.value}"
                        for o in overrides
                        if o.type == "create"
                    )
                )
                extras["join"] = "; ".join(
                    sorted(
                        f"{o.param}{o.op}{o.value}"
                        for o in overrides
                        if o.type == "join"
                    )
                )
            tbl.row(
                tenant=tenant.name, realm=tenant.realm, enabled=tenant.enabled, **extras
            )
    tbl.print(format=table_format)


type_choices = MultiChoice(["create", "join"])
type_choice = click.Choice(type_choices.choices)


@tenant.command()
@click.argument("tenant")
@click.option(
    "--type",
    help="Change the API call this override should apply to",
    type=type_choice,
    default=type_choice.choices[0],
)
@click.option(
    "--set",
    "set_",
    metavar="PARAM=VALUE",
    help="Set or replace an override for a specific API parameter. Can be repeated for additional parameters.",
    multiple=True,
)
@click.option(
    "--unset",
    metavar="PARAM",
    help="Remove an override for a specific API parameter. Can be repeated for additional parameters.",
    multiple=True,
)
@click.option(
    "--clear",
    help="Remove all overrides before adding new ones.",
    is_flag=True,
)
@async_command()
async def override(
    obj: ServiceRegistry,
    clear: bool,
    tenant: str,
    type: str,
    set_: list[str],
    unset: list[str],
):
    """Manage tenant overrides.

    Tenant overrides affect the parameters of `create` or `join` API
    calls coming from a tenant.

    You can `--set` any number of overrides per tenant as `PARAM=VALUE`
    pairs. `PARAM` should match a BBB API parameter supported by the
    given type (`create` or `join`) and `VALUE` will be enforced on all
    future API calls issued by this tenant. If `VALUE` is empty, then
    the parameter will be removed from API calls.

    Instead of the `=` operator you can also use `PARAM?VALUE` to define
    a fallback for missing parameters, `PARAM<VALUE` to define a maximum
    value for numeric parameters (e.g. 'duration' or 'maxParticipants'),
    or `PARAM+VALUE` to add items to a list-type parameter
    (e.g. 'disabledFeatures').

    Example: `--set record=false --set duration<40`
    """
    db = await obj.use(DBContext)
    async with db.session() as session:
        changed = False
        db_tenant = (
            await session.execute(
                model.Tenant.select(name=tenant).options(
                    model.selectinload(model.Tenant.overrides)
                )
            )
        ).scalar_one_or_none()

        if not db_tenant:
            click.echo(f"Tenant {tenant!r} not found")
            raise SystemExit(1)

        if not (set_ or unset or clear):
            click.echo("Change at least one override, see --help")
            raise SystemExit(1)

        # Unset first, so "--unset a --set a=b" works as expected
        for override in list(db_tenant.overrides):
            if override.type != type:
                continue
            if override.param in unset or clear:
                click.echo(f"Removed {override}")
                db_tenant.overrides.remove(override)
                changed = True

        for override in set_:
            for operator in model.OPERATOR__ALL:
                param, op, value = override.partition(operator)
                if op:
                    break
            else:
                click.echo(
                    f"Overrides must use one of the following operators: {''.join(model.OPERATOR__ALL)}"
                )
                raise SystemExit(1)
            to_update = next(
                (o for o in db_tenant.overrides if o.type == type and o.param == param),
                None,
            )
            if not to_update:
                to_update = model.TenantOverride(
                    type=type, param=param, op=op, value=value
                )
                db_tenant.overrides.append(to_update)
                click.echo(f"Added {to_update}")
                changed = True

            if to_update.op != op or to_update.value != value:
                to_update.op = op
                to_update.value = value
                click.echo(f"Updated {to_update}")
                changed = True

        if not changed:
            click.echo("Nothing changed")
        await session.commit()
