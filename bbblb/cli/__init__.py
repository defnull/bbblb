import asyncio
import functools
import importlib
import pkgutil
from bbblb.settings import ConfigError, config as cfg
import click
import os


def run_async(func):
    """Decorator that wraps coroutine with asyncio.run."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


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
def main(config_file, config):
    if config_file:
        os.environ["BBBLB_CONFIG"] = config_file
    for kv in config:
        name, _, value = kv.partition("=")
        name = name.upper()
        if name not in cfg._options:
            raise ConfigError(f"Unknown config parameter: {name}")
        env_name = f"BBBLB_{name}"
        if value:
            os.environ[env_name] = value
        elif env_name in os.environ:
            del os.environ[env_name]
    cfg.populate()


# Auto-load all modules in the bbblb.cli package to load all commands.
for module in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__package__}.{module.name}")
