from bbblb import model
from bbblb.services import ServiceRegistry
import secrets
import sys
import time
import click
import jwt

from bbblb.services.db import DBContext
from bbblb.settings import BBBLBConfig

from . import main, async_command


@main.command()
@click.option("--tenant", "-t", help="Create a Tenant-Token instead of an Admin-Token.")
@click.option("--server", "-s", help="Create a Server-Token instead of an Admin-Token.")
@click.option(
    "--expire",
    "-e",
    metavar="SECONDS",
    default=-1,
    help="Number of seconds after which this token should expire.",
)
@click.option(
    "--verbose", "-v", help="Print the clear-text token to stdout.", is_flag=True
)
@click.argument("subject")
@click.argument("scope", nargs=-1)
@async_command()
async def maketoken(
    obj: ServiceRegistry, subject, expire, server, tenant, scope, verbose
):
    """Generate an Admin Token that can be used to authenticate against the BBBLB API.

    The SUBJECT should be a short name or id that identifies the token
    or token owner. It will be logged when the token is used.

    SCOPEs limit the capabilities and permissions for this token. If no scope
    is defined, the token will have `admin` privileges.

    Tenant or Server tokens do not have scopes, their permissions are hard
    coded because tenants or servers can create their own tokens.
    """
    config = await obj.use(BBBLBConfig)
    db = await obj.use(DBContext)

    headers = {}
    payload = {
        "sub": subject,
        "aud": config.DOMAIN,
        "scope": " ".join(sorted(set(scope))) or "admin",
        "jti": secrets.token_hex(8),
    }
    if expire > 0:
        payload["exp"] = int(time.time() + int(expire))

    if server:
        async with db.session() as session:
            stmt = model.Server.select(domain=server)
            try:
                server = (await session.execute(stmt)).scalar_one()
            except model.NoResultFound:
                raise RuntimeError(f"Server not found in database: {server}")
        headers["kid"] = f"bbb:{server.domain}"
        del payload["scope"]
        key = server.secret
    elif tenant:
        async with db.session() as session:
            stmt = model.Tenant.select(name=tenant)
            try:
                tenant = (await session.execute(stmt)).scalar_one()
            except model.NoResultFound:
                raise RuntimeError(f"Tenant not found in database: {tenant}")
        headers["kid"] = f"tenant:{tenant.name}"
        del payload["scope"]
        key = tenant.secret
    else:
        key = config.SECRET

    token = jwt.encode(payload, key, headers=headers)

    if verbose:
        click.echo(f"Token Header: {headers}", file=sys.stderr)
        click.echo(f"Token Payload: {payload}", file=sys.stderr)
    click.echo(token)
