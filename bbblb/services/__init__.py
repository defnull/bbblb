from abc import ABC, abstractmethod
import asyncio
import enum
import inspect
import logging
import sys
import typing

from bbblb import ROOT_LOGGER
from bbblb.settings import BBBLBConfig


LOG = logging.getLogger(__name__)

T = typing.TypeVar("T")


class ServiceRegistry:
    """A lazy service registry that provides typed access to arbitrary
    singletons.

    If a singleton implement :cls:`ManagedService`, then it get some
    basic dependency injection on top and is started and stopped
    gracefully."""

    def __init__(self):
        self.services: dict[str, typing.Any] = {}
        self.started: list[str] = []
        self._dep: set[tuple[str, str]] = set()
        self.start_lock = asyncio.Lock()
        self.register("sr", self)

    def register(self, name: str, service: "typing.Any|ManagedService", _replace=False):
        """Register a new service.

        It is an error to register the same service name twice.
        The _replace switch is only for testing.
        """
        if name in self.services and not _replace:
            raise RuntimeError(f"Services registered twice: {name}")
        self.services[name] = service

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a, **ka):
        """Calls :meth:`shutdown`."""
        await self.shutdown()

    async def shutdown(self):
        """Stop all started services."""
        while self.started:
            to_stop = self.started[0]
            await self._stop(to_stop)

    def get(self, name, cast: type[T] = object, uninitialized_ok=False) -> T:
        """Request a service instance by name, and optionally check its
        type.

        Requesting uninitialized services is a :exc:`RuntimeError`
        by default, unless `uninitialized_ok` is true.
        """
        if name not in self.services:
            raise AttributeError(f"Unknown service: {name}")
        obj = self.services[name]
        if not (cast is object or isinstance(obj, cast)):
            raise TypeError(f"Expected {cast} but was {type(obj)}")
        if not (uninitialized_ok or name in self.started):
            raise RuntimeError(f"Service not initialized yet: {name}")
        return obj

    async def use(self, name, cast: type[T] = object) -> T:
        """Request a service and initialize it, if necessary."""
        obj = self.get(name, cast, uninitialized_ok=True)
        if name not in self.started:
            await self._start(name)
        return obj

    async def _stop(self, name):
        """Un-initialize a service, if supported and required."""
        if name not in self.started:
            return

        stop_first = [a for a, b in self._dep if b == name and a in self.started]
        for other in stop_first:
            await self._stop(other)

        self.started.remove(name)
        obj = self.services[name]
        LOG.debug(f"Stopping [{name}]: {obj}")
        if isinstance(obj, ManagedService):
            await obj.on_shutdown()

    async def _start(self, name):
        """Initialize a service, if supported and required."""
        if name in self.started:
            return

        self.started.append(name)
        obj = self.services[name]
        LOG.debug(f"Starting [{name}]: {obj}")

        if isinstance(obj, ManagedService):
            # Poor man's dependency injection
            argsspec = inspect.signature(obj.on_start)
            args = {}
            for spec in argsspec.parameters.values():
                self._dep.add((name, spec.name))
                args[spec.name] = await self.use(spec.name, object)

            await obj.on_start(**args)


class Health(enum.Enum):
    UNKNOWN = 0
    OK = 1
    WARN = 2
    CRITICAL = 3


class ManagedService(ABC):
    @abstractmethod
    async def on_start(self):
        """Called when the managed service is first requested.

        The method can request dependencies via named arguments. Managed
        dependencies are started before they are passed to this method.
        """
        pass

    async def check_health(self) -> tuple[Health, str]:
        return Health.UNKNOWN, "Not implemented"

    @abstractmethod
    async def on_shutdown(self):
        """Called during shutdown to perform cleanup tasks.

        The shutdown order takes dependencies into account, all managed
        dependencies requested during :meth:`on_startup` are still
        available.
        """
        pass


class BackgroundService(ManagedService):
    """Base class for long running background task wrapped in a managed
    service.

    Subclasses implement :meth:`run` and optionally override
    :meth:`on_start` and :meth:`on_shutdown` (remember to call super).

    The abstract :meth:`run` method should return a coroutine that can
    be wrapped in a Task and run in the background. On shutdown the task
    is cancelled, which raises a CancelledError within the coroutine.
    The service waits for the coroutine to *actually* terminate to
    ensures that code in except- or finally-blocks is not interrupted.

    The :meth:`get_health` method reports OK for running tasks, UNKNOWN
    for canceled tasks and CRITICAL for crashed tasks. Those are
    NOT restarted automatically. Implement restart logic and proper error
    handling directly in your own :meth:`run` method.
    """

    task: asyncio.Task | None = None
    shutdown_complete: asyncio.Event

    async def on_start(self):
        assert not self.task
        self.shutdown_complete = asyncio.Event()
        self.task = asyncio.create_task(self._run_wrapper())
        self.task.add_done_callback(lambda task: self.shutdown_complete.set())

    async def check_health(self) -> tuple[Health, str]:
        if not self.task:
            return Health.UNKNOWN, "Task not started yet"
        if self.task.cancelled() or self.task.cancelling():
            return Health.UNKNOWN, "Task is shutting down"
        if not self.task.done():
            return Health.OK, "Task running"
        return Health.CRITICAL, "Task failed"

    async def on_shutdown(self):
        if self.task:
            self.task.cancel()
            self.task = None
            await self.shutdown_complete.wait()

    async def _run_wrapper(self):
        try:
            LOG.debug(f"Starting background task: {self}")
            await self.run()
        except asyncio.CancelledError:
            LOG.debug(f"Shutting down background task: {self}")
            raise
        except BaseException:
            LOG.exception(f"Failed background task: {self}")
            pass

    @abstractmethod
    async def run(self):
        pass


def configure_logging(config: BBBLBConfig):
    ROOT_LOGGER.setLevel(logging.DEBUG if config.DEBUG else logging.INFO)
    ROOT_LOGGER.propagate = False
    if not ROOT_LOGGER.handlers:
        ch = logging.StreamHandler(stream=sys.stderr)
        ch.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        ROOT_LOGGER.addHandler(ch)


async def bootstrap(
    config: BBBLBConfig, autostart=True, logging=True
) -> ServiceRegistry:
    import bbblb.settings
    import bbblb.services.poller
    import bbblb.services.recording
    import bbblb.services.locks
    import bbblb.services.db
    import bbblb.services.bbb
    import bbblb.services.health

    if logging:

        @config.watch
        def watch_debug_level(name, old, new):
            if name in ("DEBUG", ""):
                configure_logging(config)

    LOG.debug("Bootstrapping services...")

    ctx = ServiceRegistry()
    ctx.register("config", config)
    ctx.register(
        "health", bbblb.services.health.HealthService(interval=config.POLL_INTERVAL)
    )
    ctx.register(
        "db",
        bbblb.services.db.DBContext(
            config.DB,
            create=config.DB_CREATE,
            migrate=config.DB_MIGRATE,
        ),
    )
    ctx.register("bbb", bbblb.services.bbb.BBBHelper())
    ctx.register("locks", bbblb.services.locks.LockManager())
    ctx.register(
        "poller",
        bbblb.services.poller.MeetingPoller(config),
    )
    ctx.register(
        "importer",
        bbblb.services.recording.RecordingManager(config),
    )

    if autostart:
        for name, service in ctx.services.items():
            await ctx.use(name, service.__class__)

    LOG.debug("Bootstrapping completed!")

    return ctx
