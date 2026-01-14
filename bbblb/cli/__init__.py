import asyncio
import functools
import importlib
import pkgutil
import typing
from bbblb.services import bootstrap
from bbblb.settings import ConfigError, BBBLBConfig
import click
import os
import tabulate
import json


def async_command():
    """Decorator that wraps coroutine with asyncio.run and click.pass_obj."""

    def decorator(func):
        @functools.wraps(func)
        @click.pass_obj
        def sync_wrapper(*args, **kwargs):
            return asyncio.run(func(*args, **kwargs))

        return sync_wrapper

    return decorator


class MultiChoice(click.ParamType):
    name = "list"

    def __init__(self, choices):
        self.choices = tuple(choices)

    def convert(self, value, param, ctx):
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            return tuple(value)
        values = [v.strip() for v in value.split(",")]
        for v in values:
            if v not in self.choices:
                self.fail(
                    f"Invalid choice: '{v}'. Must be one of: {', '.join(self.choices)}",
                    param,
                    ctx,
                )
        return values

    def to_info_dict(self):
        info_dict = super().to_info_dict()
        info_dict["choices"] = self.choices
        return info_dict


class Table:
    formats = ["simple", "plain", "raw", "json"]
    option = click.option(
        "--table-format",
        type=click.Choice(formats),
        default=formats[0],
        help="Change the result table format.",
    )

    def __init__(self):
        self._rows: list[list[typing.Any]] = []
        self._headers = {}

    def headers(self, **headers):
        """Map column names to human readable labels"""
        self._headers.update(headers)

    def row(self, **values):
        """Add a row to the table, pamming column names ot values.

        Missing columns are stored as `None`. Previously unknown columns
        are added to the table.
        """
        for key in values:
            if key not in self._headers:
                self._headers[key] = key.title()
                for row in self._rows:
                    row.append(None)
        self._rows.append([values.get(column, None) for column in self._headers])

    def print(self, format="simple"):
        if format == "json":
            keys = list(self._headers)
            for row in self._rows:
                click.echo(json.dumps(dict(zip(keys, row))))
        elif format == "raw":
            for row in self._rows:
                click.echo("\t".join(map(str, row)))
        else:
            click.echo(
                tabulate.tabulate(
                    self._rows,
                    list(self._headers.values()),
                    tablefmt=format,
                    floatfmt=".2f",
                )
            )


@click.group(
    name="bbblb",
    context_settings=dict(show_default=True, help_option_names=["--help", "-h"]),
)
@click.option(
    "--config-file",
    "-C",
    metavar="FILE",
    envvar="BBBLB_CONFIG",
    help="Load config from file",
)
@click.option(
    "--config",
    "-c",
    metavar="KEY=VALUE",
    help="Set or unset a BBBLB config parameter",
    multiple=True,
)
@click.option(
    "-v", "--verbose", help="Increase verbosity. Can be repeated.", count=True
)
@async_command()
@click.pass_context
async def main(ctx, obj, config_file, config, verbose):
    import logging

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.WARNING
    )

    if verbose == 0:
        logging.getLogger("bbblb").setLevel(logging.WARNING)
    elif verbose == 1:
        logging.getLogger("bbblb").setLevel(logging.INFO)
    elif verbose == 2:
        logging.getLogger("bbblb").setLevel(logging.DEBUG)
    elif verbose == 3:
        logging.getLogger("bbblb").setLevel(logging.DEBUG)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.DEBUG)
    elif verbose >= 4:
        logging.root.setLevel(logging.DEBUG)

    config_ = BBBLBConfig()

    if config_file:
        os.environ["BBBLB_CONFIG"] = config_file
    for kv in config:
        name, _, value = kv.partition("=")
        name = name.upper()
        if name not in config_._options:
            raise ConfigError(f"Unknown config parameter: {name}")
        env_name = f"BBBLB_{name}"
        if value:
            os.environ[env_name] = value
        elif env_name in os.environ:
            del os.environ[env_name]

    config_.populate()
    ctx.obj = await bootstrap(config_, autostart=False, logging=False)


# Auto-load all modules in the bbblb.cli package to load all commands.
for module in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__package__}.{module.name}")
