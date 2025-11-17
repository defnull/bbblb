import asyncio
import json
import pathlib
import sys

from bbblb import model
import click

from bbblb.cli.server import _end_meeting

from . import main, async_command


@main.group()
def state():
    """Save or load cluster state (servers and tenants) from a JSON file"""


@state.command()
@click.argument("FILE", default="-")
@async_command(db=True)
async def save(file: str):
    """Export current server and tenant configuration as JSON."""
    output = {
        "servers": {},
        "tenants": {},
    }
    async with model.session() as session:
        stmt = model.Server.select().order_by(model.Server.domain)
        for server in (await session.execute(stmt)).scalars():
            output["servers"][server.domain] = {
                "secret": server.secret,
                "enabled": server.enabled,
            }

        stmt = model.Tenant.select().order_by(model.Tenant.name)
        for tenant in (await session.execute(stmt)).scalars():
            output["tenants"][tenant.name] = {
                "secret": tenant.secret,
                "realm": tenant.secret,
                "enabled": tenant.enabled,
            }

    payload = json.dumps(output, indent=2)
    if file == "-":
        click.echo(payload)
    else:
        await asyncio.to_thread(pathlib.Path(file).write_text, payload)


@state.command()
@click.option(
    "--nuke",
    help="End all meetings related to obsolete servers or tenants",
    is_flag=True,
)
@click.option(
    "--clean",
    help="Remove obsolete server and tenants instead of just disabling them."
    "Combine with --nuke to force removal.",
    is_flag=True,
)
@click.option(
    "--dry-run", "-n", help="Simulate changes without changing anything.", is_flag=True
)
@click.argument("FILE", default="-")
@async_command(db=True)
async def load(file: str, nuke: bool, dry_run: bool, clean: bool):
    """Load and apply server and tenant configuration from JSON.

    WARNING: This will modify or remove tenants and servers without asking.
    Try with --dry-run first if you are unsure.

    Obsolete servers and tenants are disabled by default.
    Use --clean to fully remove them.

    Servers and tenants with meetings cannot be removed.
    Use --nuke to forcefully end all meetings on obsolete servers or meetings.

    """

    if file == "-":
        state = await asyncio.to_thread(json.load, sys.stdin)
    else:
        state = json.loads(await asyncio.to_thread(pathlib.Path(file).read_text))

    changes = False
    if dry_run:
        click.echo("=== DRY RUN ===")

    def logchange(obj, attr, value):
        nonlocal changes
        oldval = getattr(obj, attr)
        if oldval == value:
            return
        changes = True
        setattr(obj, attr, value)
        click.echo(f"CHANGE {obj}.{attr} {oldval!r} -> {value!r}")

    async with model.session() as session, session.begin():
        # Fetch and lock ALL servers and meetings.
        cur = await session.execute(model.Server.select().with_for_update())
        servers = {server.domain: server for server in cur.scalars()}
        cur = await session.execute(model.Tenant.select().with_for_update())
        tenants = {tenant.name: tenant for tenant in cur.scalars()}

        # Create or modify servers
        for domain, server_conf in state["servers"].items():
            if domain not in servers:
                servers[domain] = model.Server(domain=domain)
                session.add(servers[domain])
                changes = True
                click.echo(f"NEW {servers[domain]}")
            server = servers[domain]
            logchange(server, "secret", server_conf["secret"])
            logchange(server, "enabled", server_conf["enabled"])

        # Disable or remove obsolete servers
        for obsolete in set(servers) - set(state["servers"]):
            server = servers[obsolete]
            meetings = await server.awaitable_attrs.meetings
            changes = True

            if nuke and meetings:
                for meeting in meetings:
                    click.echo(f"END {meeting}")
                    if not dry_run:
                        await _end_meeting(meeting)

            if clean and (nuke or not meetings):
                click.echo(f"REMOVED {server}")
                await session.delete(server)
            else:
                logchange(server, "enabled", False)

        # Create or modify tenants
        for name, tenant_conf in state["tenants"].items():
            if name not in tenants:
                tenants[name] = model.Tenant(name=name)
                session.add(tenants[name])
                changes = True
                click.echo(f"NEW {tenants[name]}")

            tenant = tenants[name]
            logchange(tenant, "secret", tenant_conf["secret"])
            logchange(tenant, "realm", tenant_conf["realm"])
            logchange(tenant, "enabled", tenant_conf["enabled"])

        # Disable or remove obsolete tenants
        for obsolete in set(tenants) - set(state["tenants"]):
            tenant = tenants[obsolete]
            meetings = await tenant.awaitable_attrs.meetings
            changes = True

            if nuke and meetings:
                for meeting in meetings:
                    click.echo(f"END {meeting}")
                    if not dry_run:
                        await _end_meeting(meeting)

            if clean and (nuke or not meetings):
                click.echo(f"REMOVED {tenant}")
                await session.delete(tenant)
            else:
                logchange(tenant, "enabled", False)

        # Finalize changes, if any
        if not changes:
            click.echo("OK: Nothing to do")
            await session.rollback()
        elif dry_run:
            click.echo("=== DRY RUN ===")
            await session.rollback()
        else:
            await session.commit()
            click.echo("OK: Changes applied successfully")
