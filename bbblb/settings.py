import os
import logging
from pathlib import Path
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
    CONFIG: Path | None = None

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
        for name, value, _ in self.itersources():
            func(name, None, value)
        return func

    def itersources(self):
        """Yield (name, value, source) tuples for everything in this config object"""
        for name in sorted(self._options):
            yield name, getattr(self, name), self._source[name]

    def set_defaults(self, **defaults):
        for name, value in defaults.items():
            if name in self._options and not hasattr(self, name):
                self._set(name, value, "set_defaults()")

    def set(self, *dicts, **values):
        for name, value in dict(*dicts, **values).items():
            if name in self._options:
                self._set(name, value, "set()")

    def load_file(self, path: Path, remove_prefix="", strict=False):
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
            source = f"env.{env_name}"
            if name in self._options or strict:
                self._set(name, os.environ[env_name], source)
            else:
                LOG.warning(f"Ignoring unrecognized config option: {name} ({source})")

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
            elif tdef is Path and isinstance(value, (str, Path)):
                return Path(value).resolve()
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

    if not typing.TYPE_CHECKING:

        def __getattr__(self, name: str):
            if name.startswith("_"):
                return super().__getattribute__(name)
            if name in self._options:
                raise ConfigError(f"Missing config parameter: {name}")
            raise AttributeError(name)


class BBBLBConfig(BaseConfig):
    #: Primary domain for this service. This will be added as bbblb-origin
    #: metadata to meetings and is used by e.g. the recording upload script
    #: to get back at bbblb from the BBB nodes.
    DOMAIN: str

    #: Secret used to sign and verify API credentials and protected callbacks.
    #: This is NOT your BBB API secret.
    SECRET: str

    #: An sqlalchemy compatible database connection string, starting with either
    #: `sqlite://` or `postgresql://`. For example `sqlite:////path/to/file.db`
    #: or `postgresql://user:pass@host/name`.
    DB: str = "sqlite:////usr/share/bbblb/sqlite.db"

    #: Create database if it does not exist on startup (postgres only).
    DB_CREATE: bool = True

    #: Run database schema migrations automatically on startup.
    DB_MIGRATE: bool = True

    #: The directory where BBBLB stores all its persistent data, including
    #: recordings, lockfiles, logs and more. Must be fully write-able for BBBLB
    #: and the `{PATH_DATA}/recordings` sub-directory must also be read-able by
    #: your front-end HTTP server, if used. See docs/recording.md for details.
    PATH_DATA: Path = Path("/usr/share/bbblb/")

    #: For each BBB API request, the value of this header is matched against the
    #: tenant realms to find the correct tenant. This defaults to the `Host`
    #: header, which means each tenant needs to use a different (sub-)domain to
    #: reach BBBLB.
    TENANT_HEADER: str = "Host"

    #: Cache tenant info for a couple of seconds before requesting fresh info from the
    #: database. Even a short cache time improves API latency by a lot. The only downside
    #: is that tenant changes (e.g. new secret) may take a couple of seconds to take
    #: effect.
    TENANT_CACHE: int = 10

    #: If true, meeting IDs are scoped with the tenant ID to avoid conflicts between
    #: tenants. API clients will still see the unmodified meeting ID, but the scoped
    #: ID may end up in recording metadata and logs.
    SCOPED_MEETING_IDS: bool = True

    #: Maximum number of import tasks to perform at the same timer. It is usually
    #: not a good idea to increase this too much.
    RECORDING_THREADS: int = 1

    #: Domain where recordings are hostet. The wildcards {DOMAIN} or {REALM}
    #: can be used to refer to the global DOMAIN config, or the realm of the
    #: current tenant.
    PLAYBACK_DOMAIN: str = "{DOMAIN}"

    #: Poll interval in seconds for the background server health and meeting checker.
    #: This also defines the timeout for each individual poll, and changes how quickly
    #: the POLL_FAIL and POLL_RECOVER watermarks can be reached. The interval should be
    #: between 10 (fast) and 60 (very slow) depending on the size of your cluster.
    POLL_INTERVAL: int = 30

    #: Number of failed create calls or health checks after which we give up on an
    #: UNSTABLE server and mark it as OFFLINE. All remaining meetings are dropped,
    #: so they can be re-created on another server.
    POLL_FAIL: int = 3

    #: Number of successfull health checks in a row after which an OFFLINE or UNSTABLE
    #: server is considered to be AVAILABLE again.
    POLL_RECOVER: int = 5

    #: Base load counted for each meeting.
    LOAD_BASE: float = 5.0

    #: Additional load counted for each user in a meeting.
    LOAD_USER: float = 1.0

    #: Additional load counted for each voice user in a meeting.
    LOAD_VOICE: float = 0.5

    #: Additional load counted for each video user in a meeting.
    LOAD_VIDEO: float = 0.5

    #: Reserved seats for new meetings.
    #: When new meetings are created, their final user count is still
    #: unknown. To avoid uneven meeting distribution during peaks hours,
    #: we reserve up to LOAD_RESERVED seats for additional users that are
    #: likely to join during the first LOAD_COOLDOWN minutes of a new
    #: meeting.
    #: The number of reserved seats will slowly decrease over time until
    #: LOAD_COOLDOWN minutes have passed.
    LOAD_RESERVED: float = 20.0

    #: Number of minutes after which new meetings are no longer impacted
    #: by LOAD_RESERVED. The accounted number of reserved seats
    #: decreases linearly over time.
    LOAD_COOLDOWN: float = 30.0

    #: If true, BBBLB will intercept the analytics-callback-url webhook
    #: and dump json files into the {PATH_DATA}/analytics/{tenant}/
    #: folder for later analysis (WIP). The callback is only fired if
    #: BBB is configured with defaultKeepEvents=true in bbb-web.properties.
    ANALYTICS_STORE: bool = False

    #: Maximum number of meetings or recordings to return from APIs that
    #: potentially return an unlimited amount of data.
    MAX_ITEMS: int = 1000

    #: Maximum body size for BBB API requests, both front-end and back-end.
    #: This does not affect presentation uploads, so 1MB should be plenty.
    MAX_BODY: int = 1024 * 1024

    #: How often to retry webhooks if the target fails to respond.
    WEBHOOK_RETRY: int = 3

    #: Enable debug and SQL logs
    DEBUG: bool = False

    def populate(self, verify=True, strict=True):
        cfile = os.environ.get("BBBLB_CONFIG", None)
        if cfile:
            self.load_file(Path(cfile), remove_prefix="BBBLB_", strict=strict)
        self.load_env("BBBLB_", strict=strict)
        if verify:
            self.ensure_complete()
