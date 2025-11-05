import os
import logging
import pathlib
import shlex
import typing
import types

LOG = logging.getLogger(__name__)


class ConfigError(AttributeError):
    pass


class _MissingValue:
    def __str__(self):
        return "MISSING VALUE"


MISSING = _MissingValue()


class BaseConfig:
    #: A shell or .env style (KEY=VALUE) config file with one option per line.
    #: If the CONFIG option is defined (via environment variables or explicitly
    #: during initialization) then this file is loaded and parsed. Its content
    #: will overwrite other values or defaults.
    CONFIG: pathlib.Path | None = None

    def __init__(self):
        self._options = {
            name: anno
            for name, anno in typing.get_type_hints(self.__class__).items()
            if name.upper() == name
        }
        self._source = {}
        self._watchers = []

        # Copy defaults from class definition
        for mro in reversed(self.__class__.__mro__):
            for name, value in mro.__dict__.items():
                if name not in self._options:
                    continue
                self._set(name, value, f"{mro.__module__}.{mro.__name__}")

    def watch(self, func: typing.Callable[[str, typing.Any, typing.Any], typing.Any]):
        self._watchers.append(func)
        return func

    def itersources(self):
        """Yield (name, value, source) tuples for everything in this config object"""
        for name in sorted(self._options):
            yield name, getattr(self, name), self._source[name]

    def set_defaults(self, **defaults):
        for name, value in defaults.items():
            if name in self._options and not hasattr(self, name):
                self._set(name, value, "set_defaults")

    def load_file(self, path: pathlib.Path, remove_prefix="", strict=False):
        with open(path, "rt") as fp:
            for n, line in enumerate(fp):
                line = line.strip()
                if not line or line[0] == "#":
                    continue
                key, _, val = map(str.strip, line.partition("="))
                if not val:
                    continue
                key = key.strip()
                if key.startswith(remove_prefix):
                    key = key[len(remove_prefix) :]
                key = key.strip()
                val = " ".join(shlex.split(val.strip()))
                if not (key and val):
                    continue
                if key in self._options or strict:
                    self._set(key, val, f"{path}:{n}")

    def load_env(self, env_prefix: str, strict=False):
        for env_name in os.environ:
            if not env_name.startswith(env_prefix):
                continue
            name = env_name[len(env_prefix) :]
            if name in self._options or strict:
                self._set(name, os.environ[env_name], f"env.{env_name}")

    def get_missing(self):
        return set(self._options) - set(self.__dict__)

    def ensure_complete(self):
        missing = self.get_missing()
        if missing:
            raise ConfigError(
                f"Required but missing config parameters: {', '.join(missing)}"
            )

    def _cast(self, name: str, value, source: str):
        anno = self._options.get(name)
        if anno is None:
            raise ConfigError(f"Unrecognized config option: {name} ({source})")

        if typing.get_origin(anno) in (typing.Union, types.UnionType):
            options = list(typing.get_args(anno))
            if types.NoneType in options and value is None:
                return value
        else:
            options = [anno]

        for tdef in options:
            if tdef in (str, int, float) and isinstance(value, (str, int, float)):
                return tdef(value)
            elif tdef is bool and isinstance(value, (str, int, bool)):
                return str(value).lower() in ("yes", "true", "1")
            elif tdef is pathlib.Path and isinstance(value, (str, pathlib.Path)):
                return pathlib.Path(value).resolve()
        else:
            raise ConfigError(f"Unable to convert between {type(value)} and {anno}")

    def _set(self, name: str, value, source: str):
        cast = self._cast(name, value, source)
        for watch in self._watchers:
            watch(name, getattr(self, name, MISSING), cast)
        super().__setattr__(name, cast)
        self._source[name] = source

    def __setattr__(self, name: str, value):
        if name.startswith("_"):
            return super().__setattr__(name, value)
        return self._set(name, value, "Direct assignment")

    def __getattr__(self, name: str):
        if name.startswith("_"):
            return super().__getattribute__(name)
        if name in self._options:
            raise ConfigError(f"Missing config parameter: {name}")
        raise AttributeError(name)


class BBBLBConfig(BaseConfig):
    #: Enable debug logs
    DEBUG: bool

    #: An sqlalchemy-style database URL. MUST be an async-capable engine
    #: Supportet are sqlite+aiosqlite:// and postgresql+asyncpg:// schemes
    DB_URI: str

    #: Primary domain for this service. This will be added as bbblb-origin
    #: metadata to meetings and is used by e.g. the recording upload script
    #: to get back at bbblb from the BBB nodes.
    DOMAIN: str

    #: Domain where recordings are hostet. The wildcards {DOMAIN} or {REALM}
    #: can be used to refer to the global DOMAIN config, or the realm of the
    #: current tenant.
    PLAYBACK_DOMAIN: str = "{DOMAIN}"

    #: For each BBB API request, the value of this header is matched against the
    #: tenant realms to find the correct tenant. This defaults to the `Host`
    #: header, which means each tenant needs to use a different (sub-)domain to
    #: reach BBBLB.
    TENANT_HEADER: str = "Host"

    #: Maximum number of meetings or records to return from APIs that potentially
    #: return a lot of stuff.
    API_MAXLIMIT: int = 1000

    #: Secret used to sign and verify API credentials and protected callbacks.
    #: This is NOT your BBB API secret.
    SECRET: str

    #: How often to retry webhooks if the target does not respond.
    WEBHOOK_RETRY: int = 3

    ##
    ### Recording imports
    ##

    #: Root path for all the directories used by the recording importer. Must be
    #: fully write-able for bbblb and parts of it also read-able by your foront-end
    #: HTTP server. See docs/recording.md for details.
    RECORDING_PATH: pathlib.Path
    #: Maximum number of import tasks to perform at the same timer. It is usually
    #: not a good idea to increase this too much.
    RECORDING_THREADS: int = 2

    #: If true, meeting IDs are scoped with the tenant ID to avoid conflicts between
    #: tenants. API clients will still see the unmodified meeting ID, but the scoped
    #: ID may end up in recording metadata and logs.
    SCOPED_MEETING_IDS: bool = True

    #: Maximum body size for BBB API requests as well as back-end requests to
    #: BBB nodes.
    MAX_BODY: int = 1024 * 1024  # 1MB

    #: Poll interval in seconds for the background server health and meeting checker
    POLL_INTERVAL: int = 30

    # An ONLINE server that fails during a poll is marked UNSTABLE immediately and won't get new meetings.
    # Existing meetings will still be served until the server is fully OFFLINE.

    #: Number of errors after which an UNSTABLE server is marked OFFLINE and remaining meetings are lost.
    POLL_FAIL: int = 3
    #: Number of successes in a row after a server is considered ONLINE again.
    POLL_RECOVER: int = 5

    # Expected base load per meeting.
    LOADFACTOR_MEETING: float = 15.0
    # Expected load per user in a meeting
    LOADFACTOR_SIZE: float = 1.0
    # Expected load per voice user
    LOADFACTOR_VOICE: float = 0.5
    # Expected load per video user
    LOADFACTOR_VIDEO: float = 0.5

    # Initial load penalty for new meetings.
    # This value is used to predict the future load for new meetings and should match
    # the load of a 'typical' meeting on your cluster. The initial load penalty will
    # slowly decrease over the first hour of a meeting until we can assume that the
    # measured load is accurate enough.
    # The idea is to avoid the 'trampling herd' effect where multiple meetings are
    # started in a short time and end up on the same server.
    excpected_size = 30
    LOADFACTOR_INITIAL: float = LOADFACTOR_MEETING + excpected_size * (
        LOADFACTOR_SIZE + LOADFACTOR_VOICE + LOADFACTOR_VIDEO
    )

    API_BRANDING: str = "Served by BBBLB (AGPL-3, https://github.com/defnull/bbblb)"

    def populate(self, verify=True):
        if self.get_missing():
            config.load_env("BBBLB_", strict=True)
            if config.CONFIG:
                config.load_file(config.CONFIG, remove_prefix="BBBLB_", strict=True)
        if verify:
            self.ensure_complete()


config = BBBLBConfig()
