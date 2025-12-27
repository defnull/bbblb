import re
from bbblb import model
from bbblb.services import ServiceRegistry
from bbblb.services.db import DBContext
import click


from . import MultiChoice, main, async_command


@main.group()
def override():
    """Manage tenant overrides"""


type_choices = MultiChoice(["create", "join"])
type_choice = click.Choice(type_choices.choices)


@override.command("list")
@click.argument("tenant", required=False)
@click.option(
    "--type",
    help="List specific override types only",
    type=type_choices,
    default=",".join(type_choices.choices),
)
@async_command()
async def override_list(obj: ServiceRegistry, tenant: str, type: list[str]):
    """List create or join overrides by tenant."""
    db = await obj.use(DBContext)

    async with db.session() as session:
        if tenant:
            stmt = model.TenantOverride.select(
                model.TenantOverride.tenant.name == tenant
            )
        else:
            stmt = model.TenantOverride.select()
        if type:
            stmt = stmt.where(model.TenantOverride.type.in_(type))
        stmt = stmt.options(model.joinedload(model.TenantOverride.tenant))

        overrides = (await session.execute(stmt)).scalars().all()
        for ovr in overrides:
            click.echo(f"{ovr.tenant.name} {ovr.type} {ovr.param}{ovr.op}{ovr.value}")


@override.command("set")
@click.option(
    "--clear",
    help="Remove all overrides for that tenant and type before adding new ones.",
    is_flag=True,
)
@click.argument("tenant")
@click.argument("type", type=type_choice)
@click.argument("overrides", nargs=-1, metavar="NAME=VALUE")
@async_command()
async def override_set(
    obj: ServiceRegistry, clear: bool, tenant: str, type: str, overrides: list[str]
):
    """Override create or join call parameters for a given tenant.

    You can define any number of overrides per tenant as PARAM=VALUE
    pairs. PARAM should match a BBB API parameter supported by the given
    type (create or join) and the given VALUE will be enforced on all
    future API calls issued by this tenant. If VALUE is empty, then the
    parameter will be removed from API calls.

    Instead of the '=' operator you can also use '?' to define a
    fallback for missing parameters instead of an override, '<' to
    define a maximum value for numeric parameters (e.g. duration
    or maxParticipants), or '+' to add items to a comma separated list
    parameter (e.g. disabledFeatures).
    """
    db = await obj.use(DBContext)
    async with db.session() as session:
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

        if clear:
            db_tenant.overrides.clear()
        elif not overrides:
            click.echo("Set at least one override, see --help")
            raise SystemExit(1)

        for override in overrides:
            split = re.split(r"([?=<+])", override, maxsplit=1)
            param, op, value = split if len(split) == 3 else (override, "=", "")
            if op not in model.OPERATOR__ALL:
                raise ValueError(
                    f"Operator must be one of: {', '.join(model.OPERATOR__ALL)}"
                )
            to_update = next(
                (o for o in db_tenant.overrides if o.type == type and o.param == param),
                None,
            )
            if not to_update:
                to_update = model.TenantOverride(type=type, param=param)
                db_tenant.overrides.append(to_update)
            to_update.op = op
            to_update.value = value

        await session.commit()
        click.echo("OK")


@override.command("unset")
@click.argument("tenant")
@click.argument("type", type=type_choice)
@click.argument("overrides", nargs=-1, metavar="NAME")
@async_command()
async def override_unset(
    obj: ServiceRegistry, tenant: str, type: str, overrides: list[str]
):
    """Remove specific overrides on a tenant."""
    db = await obj.use(DBContext)
    async with db.session() as session:
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

        for override in list(db_tenant.overrides):
            if override.type == type and override.param in overrides:
                db_tenant.overrides.remove(override)
                click.echo(f"Removed {override.param}")

        await session.commit()
