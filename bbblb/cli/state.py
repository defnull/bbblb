import asyncio
import json
import typing

from bbblb import model
import click

from bbblb.cli.server import _end_meeting
from bbblb.services import ServiceRegistry
from bbblb.services.db import DBContext

from . import MultiChoice, main, async_command


@main.group()
def state():
    """Tools to export and import cluster state in JSON files."""


type_choices = MultiChoice(["servers", "tenants"])


@state.command()
@click.option(
    "--include",
    "-i",
    "types",
    help="Comma separated list of resource types to include in the export.",
    type=type_choices,
    default=",".join(type_choices.choices),
)
@click.argument("FILE", type=click.Path(dir_okay=False, writable=True), default="-")
@async_command()
async def export(obj: ServiceRegistry, types, file: str):
    """Export current cluster state as JSON."""
    db = await obj.use("db", DBContext)

    export: dict[str, typing.Any] = {}

    async with db.session() as session:
        if "servers" in types:
            export["servers"] = {}
            stmt = model.Server.select().order_by(model.Server.domain)
            for server in (await session.execute(stmt)).scalars():
                export["servers"][server.domain] = {
                    "secret": server.secret,
                    "enabled": server.enabled,
                }

        if "tenants" in types:
            export["tenants"] = {}
            stmt = model.Tenant.select().order_by(model.Tenant.name)
            for tenant in (await session.execute(stmt)).scalars():
                export["tenants"][tenant.name] = {
                    "secret": tenant.secret,
                    "realm": tenant.realm,
                    "enabled": tenant.enabled,
                    "overrides": tenant.overrides,
                }

    with click.open_file(file, "w") as fp:
        await asyncio.to_thread(json.dump, export, fp, indent=2)
        fp.write("\n")


@state.command("import")
@click.option(
    "--nuke",
    help="End all meetings related to obsolete servers or tenants",
    is_flag=True,
)
@click.option(
    "--delete",
    help="Remove obsolete server and tenants instead of just disabling them."
    "Combine with --nuke to force removal.",
    is_flag=True,
)
@click.option(
    "--dry-run", "-n", help="Simulate changes without changing anything.", is_flag=True
)
@click.option(
    "--include",
    "-i",
    "types",
    help="Comma separated list of resource types to include in the export.",
    type=type_choices,
    default=",".join(type_choices.choices),
)
@click.argument("FILE", type=click.Path(dir_okay=False, writable=True), default="-")
@async_command()
async def import_(
    obj: ServiceRegistry,
    types: list[str],
    file: str,
    nuke: bool,
    dry_run: bool,
    delete: bool,
):
    """Load and apply server and tenant configuration from JSON.

    WARNING: This will modify or remove tenants and servers without asking.
    Try with --dry-run first if you are unsure.

    Obsolete servers and tenants are disabled by default.
    Use --clean to fully remove them.

    Servers and tenants with meetings cannot be removed.
    Use --nuke to forcefully end all meetings on obsolete servers or meetings.

    """
    db = await obj.use("db", DBContext)
    with click.open_file(file, "r") as fp:
        state = await asyncio.to_thread(json.load, fp)

    if dry_run:
        click.echo("=== DRY RUN ===")

    async with db.session() as session, session.begin():
        changed = False
        if "servers" in types and "servers" in state:
            changed |= await sync_servers(
                state["servers"],
                session,
                obj,
                delete=delete,
                nuke=nuke,
                dry_run=dry_run,
            )

        if "tenants" in types and "tenants" in state:
            changed |= await sync_tenants(
                state["tenants"],
                session,
                obj,
                delete=delete,
                nuke=nuke,
                dry_run=dry_run,
            )

        # Finalize changes, if any
        if changed:
            await (session.rollback if dry_run else session.commit)()
            click.echo("Changes applied successfully")
        else:
            click.echo("Nothing to do")
            await session.rollback()

        if dry_run:
            click.echo("=== DRY RUN ===")


def logchange(obj, attr, value):
    oldval = getattr(obj, attr)
    if oldval == value:
        return False
    setattr(obj, attr, value)
    click.echo(f"{obj} changed: {attr} from {oldval!r} to {value!r}")
    return True


async def sync_servers(
    target,
    session: model.AsyncSession,
    sr: ServiceRegistry,
    delete: bool,
    nuke: bool,
    dry_run: bool,
):
    cur = await session.execute(model.Server.select().with_for_update())
    servers = {server.domain: server for server in cur.scalars()}
    changed = False

    # Create or modify servers
    for domain, server_conf in target.items():
        if domain not in servers:
            servers[domain] = model.Server(domain=domain, enabled=True)
            session.add(servers[domain])
            changed = True
            click.echo(f"{servers[domain]} created")
        server = servers[domain]
        changed |= logchange(server, "enabled", server_conf.get("enabled", True))
        changed |= logchange(server, "secret", server_conf["secret"])

    # Disable or remove obsolete servers
    for obsolete in set(servers) - set(target):
        server = servers[obsolete]
        meetings = await server.awaitable_attrs.meetings
        changed = True

        if nuke and meetings:
            for meeting in meetings:
                if not dry_run:
                    await _end_meeting(sr, meeting)
                click.echo(f"{meeting} nuked")
            meetings = []

        if delete and not meetings:
            click.echo(f"{server} removed")
            await session.delete(server)
        else:
            changed |= logchange(server, "enabled", False)

    return changed


async def sync_tenants(
    target,
    session: model.AsyncSession,
    sr: ServiceRegistry,
    delete: bool,
    nuke: bool,
    dry_run: bool,
):
    cur = await session.execute(model.Tenant.select().with_for_update())
    tenants = {tenant.name: tenant for tenant in cur.scalars()}
    changed = False

    # Create or modify tenants
    for name, tenant_conf in target.items():
        if name not in tenants:
            tenants[name] = model.Tenant(name=name)
            session.add(tenants[name])
            changed = True
            click.echo(f"{tenants[name]} created")

        tenant = tenants[name]
        changed |= logchange(tenant, "enabled", tenant_conf.get("enabled", True))
        changed |= logchange(tenant, "secret", tenant_conf["secret"])
        changed |= logchange(tenant, "realm", tenant_conf["realm"])
        changed |= logchange(tenant, "overrides", tenant_conf.get("overrides") or {})

    # Disable or remove obsolete tenants
    for obsolete in set(tenants) - set(target):
        tenant = tenants[obsolete]
        meetings = await tenant.awaitable_attrs.meetings
        changed = True

        if nuke and meetings:
            for meeting in meetings:
                if not dry_run:
                    await _end_meeting(sr, meeting)
                click.echo(f"{meeting} nuked")
            meetings = []

        if tenant.enabled and delete and not meetings:
            click.echo(
                f"INFO: Tenants currently cannot be removed. Disabling {tenant} instead"
            )
        changed |= logchange(tenant, "enabled", False)

    return changed
