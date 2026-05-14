# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    """Manage BBB servers."""


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
@click.argument("domains", nargs=-1)
@click.option(
    "--now",
    help="Skip health checks and make the server available for new meetings immediately.",
    is_flag=True,
)
@async_command()
async def enable(obj: ServiceRegistry, domains: list[str], now: bool):
    """Enable servers and make them available for new meetings."""
    if not domains:
        raise click.BadParameter("Provide at least one value for DOMAINS")

    db = await obj.use(DBContext)
    async with db.session() as session:
        for domain in domains:
            server = (
                await session.execute(model.Server.select(domain=domain))
            ).scalar_one_or_none()
            if not server:
                click.echo(f"Server {domain!r} not found")
                raise SystemExit(1)
            if server.enabled:
                click.echo(f"Server {domain!r} already enabled")
            else:
                server.enabled = True
                click.echo(f"Server {domain!r} enabled")
            if now:
                server.force_available()
        await session.commit()


@server.command()
@click.argument("domains", nargs=-1)
@click.option("--nuke", help="End all meetings immediately.", is_flag=True)
@click.option(
    "--wait",
    help="Seconds to wait for meetings to end. A value of -1 waits forever.",
    type=int,
    default=0,
)
@async_command()
async def disable(obj: ServiceRegistry, domains: list[str], nuke: bool, wait: int):
    """Disable servers and optionally wait for meetings to end.

    Disabling a server by default does not interrupt running meetings,
    it just prevents new meetings from being assigned to that server.

    You can --wait for meetings to end on their own, or --nuke them.

    If there are still running meetings after --wait seconds, the process
    will end with status code `3`.
    """
    db = await obj.use(DBContext)
    if not domains:
        raise click.BadParameter("Provide at least one value for DOMAINS")

    servers = []
    async with db.session() as session:
        for domain in domains:
            server = (
                await session.execute(
                    model.Server.select(model.Server.domain == domain)
                )
            ).scalar_one_or_none()
            if not server:
                click.echo(f"Server {domain!r} not found")
                raise SystemExit(1)
            servers.append(server)
            if not server.enabled:
                click.echo(f"Server {domain!r} already disabled")
            else:
                server.enabled = False
                click.echo(f"Server {domain!r} disabled")
        await session.commit()
        if nuke:
            for server in servers:
                meetings = await server.awaitable_attrs.meetings
                for meeting in meetings:
                    await _end_meeting(obj, meeting)

    if wait:
        if wait < 0:
            wait = 60 * 60 * 24 * 356

        maxwait = time.time() + wait
        interval = 5.0
        last_count = 0

        while True:
            async with db.session() as session:
                stmt = (
                    model.Meeting.select(
                        model.Meeting.server_fk.in_([s.id for s in servers])
                    )
                    .with_only_columns(func.count())
                    .order_by(None)
                )
                count = (await session.execute(stmt)).scalar()

            if count == 0:
                click.echo("No meetings left on disabled servers")
                return

            if time.time() + interval > maxwait:
                click.echo(
                    f"Timeout while waiting for meetings to end: {count} meetings still running"
                )
                raise SystemExit(3)

            if last_count != count:
                click.echo(f"Waiting for {count} meetings to end ...")

            last_count = count
            await asyncio.sleep(interval)


@server.command("delete")
@click.argument("domain")
@async_command()
async def _delete(obj: ServiceRegistry, domain: str):
    """Remove an empty server from the cluster.

    The command will fail if the server still has running meetings.
    """
    db = await obj.use(DBContext)
    async with db.session() as session:
        server = (
            await session.execute(model.Server.select(domain=domain))
        ).scalar_one_or_none()
        if not server:
            click.echo(f"Server {domain!r} not found")
            return
        stmt = (
            model.Meeting.select(model.Meeting.server == server)
            .with_only_columns(func.count())
            .order_by(None)
        )
        if (await session.execute(stmt)).scalar() or 0 > 0:
            click.echo(f"Server {domain!r} not empty")
            raise SystemExit(3)
        await session.delete(server)
    click.echo(f"Server {domain!r} removed")


async def _end_meeting(obj: ServiceRegistry, meeting: model.Meeting):
    server = await meeting.awaitable_attrs.server
    tenant = await meeting.awaitable_attrs.tenant
    scoped_id = utils.add_scope(meeting.external_id, tenant.name)
    async with (await obj.use(BBBHelper)).connect(server) as bbb:
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
                meetings=server.stats.meetings,
                largest=server.stats.largest,
                users=server.stats.users,
                voice=server.stats.voice,
                video=server.stats.video,
                load=server.load,
            )
    tbl.print(format=table_format)
