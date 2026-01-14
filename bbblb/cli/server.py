import asyncio
import time

from sqlalchemy import func
from bbblb import model, utils
import click

from bbblb.services import ServiceRegistry
from bbblb.services.bbb import BBBHelper
from bbblb.services.db import DBContext

from . import Table, main, async_command


@main.group()
def server():
    """Manage servers"""


@server.command()
@click.option(
    "--update",
    "-U",
    help="Update the server with the same domain, if present.",
    is_flag=True,
)
@click.option("--secret", help="Set the server secret. Required for new servers")
@click.argument("domain")
@async_command()
async def create(obj: ServiceRegistry, update: bool, domain: str, secret: str | None):
    """Create a new server or update a server secret."""
    db = await obj.use(DBContext)
    async with db.session() as session:
        server = (
            await session.execute(model.Server.select(domain=domain))
        ).scalar_one_or_none()
        if server and not update:
            raise RuntimeError(f"Server {domain} already exists.")
        action = "UPDATED"
        if not server:
            action = "CREATED"
            server = model.Server(domain=domain)
            session.add(server)
        server.secret = secret or server.secret
        if not server.secret:
            raise RuntimeError("New servers need a --secret.")
        await session.commit()
        click.echo(f"{action}: server name={server.domain} secret={server.secret}")


@server.command()
@click.argument("domain")
@async_command()
async def enable(obj: ServiceRegistry, domain: str):
    """Enable a server and make it available for new meetings."""
    db = await obj.use(DBContext)
    async with db.session() as session:
        server = (
            await session.execute(model.Server.select(domain=domain))
        ).scalar_one_or_none()
        if not server:
            click.echo(f"Server {domain!r} not found")
            return
        if server.enabled:
            click.echo(f"Server {domain!r} already enabled")
        else:
            server.enabled = True
            await session.commit()
            click.echo(f"Server {domain!r} enabled")


@server.command()
@click.argument("domain")
@click.option("--nuke", help="End all meetings on this server.", is_flag=True)
@click.option(
    "--wait",
    help="Wait for this many seconds for all meetings to end. A value of -1 waits forever",
    type=int,
    default=0,
)
@async_command()
async def disable(obj: ServiceRegistry, domain: str, nuke: bool, wait: int):
    """Disable a server and wait for meetings to end."""
    db = await obj.use(DBContext)

    async with db.session() as session:
        server = (
            await session.execute(model.Server.select(domain=domain))
        ).scalar_one_or_none()
        if not server:
            click.echo(f"Server {domain!r} not found")
            return
        if nuke:
            meetings = await server.awaitable_attrs.meetings
            for meeting in meetings:
                await _end_meeting(obj, meeting)
        if not server.enabled:
            click.echo(f"Server {domain!r} already disabled")
        else:
            server.enabled = False
            await session.commit()
            click.echo(f"Server {domain!r} disabled")

    if wait:
        if wait < 0:
            wait = 60 * 60 * 24 * 356

        maxwait = time.time() + wait
        interval = 5.0
        last_count = 0

        while True:
            async with db.session() as session:
                stmt = (
                    model.Meeting.select(model.Meeting.server == server)
                    .with_only_columns(func.count())
                    .order_by(None)
                )
                count = (await session.execute(stmt)).scalar()

            if count == 0:
                click.echo("No meetings left on server")
                return

            if time.time() + interval > maxwait:
                raise RuntimeError(
                    f"Server not empty: There are still {count} meetings running"
                )

            if last_count != count:
                click.echo(f"Waiting for {count} meetings to end")

            last_count = count
            await asyncio.sleep(interval)


@server.command("delete")
@click.argument("domain")
@async_command()
async def _delete(obj: ServiceRegistry, domain: str):
    """Remove an empty server from the server list."""
    db = await obj.use(DBContext)
    async with db.session() as session:
        server = (
            await session.execute(model.Server.select(domain=domain))
        ).scalar_one_or_none()
        if not server:
            click.echo(f"Server {domain!r} not found")
            return
        if server.enabled:
            click.echo(f"Server {domain!r} not disabled")
        stmt = (
            model.Meeting.select(model.Meeting.server == server)
            .with_only_columns(func.count())
            .order_by(None)
        )
        if (await session.execute(stmt)).scalar() or 0 > 0:
            click.echo(f"Server {domain!r} not empty")
            return
        await session.delete(server)
    click.echo(f"Server {domain!r} removed")


async def _end_meeting(obj: ServiceRegistry, meeting: model.Meeting):
    server = await meeting.awaitable_attrs.server
    tenant = await meeting.awaitable_attrs.server
    scoped_id = utils.add_scope(meeting.external_id, tenant.name)
    bbb = (await obj.use(BBBHelper)).connect(meeting.server.api_base, server.secret)

    result = await bbb.action("end", {"meetingID": scoped_id})
    if result.success:
        click.echo(f"Ended meeting {meeting.external_id} ({meeting.tenant.name})")
    else:
        click.echo(
            f"Failed to end meeting {meeting.external_id}: {result.messageKey} {result.message}"
        )


@server.command()
@Table.option
@async_command()
async def list(obj: ServiceRegistry, table_format: str):
    """List all servers with their secrets."""
    db = await obj.use(DBContext)

    tbl = Table()
    async with db.session() as session:
        stmt = model.Server.select().order_by(model.Server.domain)
        for server in (await session.execute(stmt)).scalars():
            tbl.row(server=server.domain, secret=server.secret)
    tbl.print(format=table_format)


@server.command()
@Table.option
@async_command()
async def stats(obj: ServiceRegistry, table_format):
    """Show server statistics (state, health, load)."""
    db = await obj.use(DBContext)

    tbl = Table()
    async with db.session() as session:
        stmt = model.Server.select().order_by(model.Server.domain)
        for server in (await session.execute(stmt)).scalars():
            tbl.row(
                server=server.domain,
                enabled=server.enabled,
                state=server.health.name.lower(),
                meetings=server.stats.get("meetings", 0),
                users=server.stats.get("users", 0),
                voice=server.stats.get("voice", 0),
                video=server.stats.get("video", 0),
                load=server.load,
            )
    tbl.print(format=table_format)
