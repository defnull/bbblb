import asyncio
import functools
import importlib
import pkgutil
from bbblb.services import bootstrap
from bbblb.settings import ConfigError, BBBLBConfig
import click
import os


def async_command():
    """Decorator that wraps coroutine with asyncio.run and click.pass_obj."""

    def decorator(func):
        @functools.wraps(func)
        @click.pass_obj
        def sync_wrapper(*args, **kwargs):
            return asyncio.run(func(*args, **kwargs))

        return sync_wrapper

    return decorator


@click.group(name="bbblb", context_settings={"show_default": True})
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
