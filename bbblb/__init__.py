import logging
from .settings import config

__version__ = "0.0.6"
VERSION = __version__.split(".", 2)
VERSION[-1], _, BUILD = VERSION[-1].partition("-")

ROOT_LOGGER = logging.getLogger(__name__)
ROOT_LOGGER.setLevel(logging.INFO)
ROOT_LOGGER.propagate = False
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
ROOT_LOGGER.addHandler(ch)

BRANDING = "BBBLB (AGPL-3, https://github.com/defnull/bbblb)"


@config.watch
def watch_debug_level(name, old, new):
    if name == "DEBUG":
        level = logging.DEBUG if new else logging.INFO
        if level != ROOT_LOGGER.level:
            ROOT_LOGGER.setLevel(logging.DEBUG if new else logging.INFO)
