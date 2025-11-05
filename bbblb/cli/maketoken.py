from bbblb.settings import config as cfg
import secrets
import sys
import time
import click
import jwt

from . import main


@main.command()
@click.option("--tenant", "-t", help="Limit token to specific tenant.")
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
def maketoken(subject, expire, tenant, scope, verbose):
    """Generate an API token

    The SUBJECT should be a short name or id that identifies the intended token owner. It will be logged when the token is used.

    The SCOPEs limit the capabilities and permissions for this token. If no scope
    is defined, the token will have `admin` privileges.
    """
    payload = {
        "sub": subject,
        "scope": " ".join(sorted(set(scope))) or "admin",
        "jti": secrets.token_hex(8),
    }
    if expire > 0:
        payload["exp"] = int(time.time() + int(expire))
    if tenant:
        payload["tenant"] = tenant
    token = jwt.encode(payload, cfg.SECRET)

    if verbose:
        click.echo(f"Token Payload: {payload}", file=sys.stderr)
    click.echo(token)
