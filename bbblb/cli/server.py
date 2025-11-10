from bbblb import model, utils
from bbblb.bbblib import BBBClient
from bbblb.settings import config as cfg
import click

from . import main, run_async


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
@run_async
async def create(update: bool, domain: str, secret: str | None):
    """Create a new server or update a server secret."""
    await model.init_engine(cfg.DB)
    async with model.AsyncSessionMaker() as session:
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
@run_async
async def enable(domain: str):
    """Enable a server and make it available for new meetings."""
    await model.init_engine(cfg.DB)
    async with model.AsyncSessionMaker() as session:
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
@run_async
async def disable(domain: str, nuke: bool):
    """Disable a server, so now new meetings are created on it."""
    await model.init_engine(cfg.DB)
    async with model.AsyncSessionMaker() as session:
        server = (
            await session.execute(model.Server.select(domain=domain))
        ).scalar_one_or_none()
        if not server:
            click.echo(f"Server {domain!r} not found")
            return
        if nuke:
            meetings = await server.awaitable_attrs.meetings
            for meeting in meetings:
                await _end_meeting(meeting)
        if not server.enabled:
            click.echo(f"Server {domain!r} already disabled")
        else:
            server.enabled = False
            await session.commit()
            click.echo(f"Server {domain!r} disabled")


async def _end_meeting(meeting: model.Meeting):
    server = await meeting.awaitable_attrs.server
    tenant = await meeting.awaitable_attrs.server
    scoped_id = utils.add_scope(meeting.external_id, tenant.name)

    bbb = BBBClient(meeting.server.api_base, server.secret)
    result = await bbb.action("end", {"meetingID": scoped_id})
    if result.success:
        click.echo(f"Ended meeting {meeting.external_id} ({meeting.tenant.name})")
    else:
        click.echo(
            f"Failed to end meeting {meeting.external_id}: {result.messageKey} {result.message}"
        )


@server.command()
@run_async
async def list(with_secrets=False):
    """List all servers with their secrets."""
    await model.init_engine(cfg.DB)
    async with model.AsyncSessionMaker() as session:
        servers = (await session.execute(model.Server.select())).scalars()
        for server in servers:
            out = f"{server.domain} {server.secret}"
            click.echo(out)
