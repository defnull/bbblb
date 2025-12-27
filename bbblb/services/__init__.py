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


def _clsname(cls: type | object):
    if not isinstance(cls, type):
        cls = cls.__class__
    return f"{cls.__module__}.{cls.__qualname__}"


class ServiceRegistry:
    """A lazy service registry that provides access to arbitrary
    service instances, identified by their type.

    If a service instance implements :cls:`ManagedService`, then it get
    some basic dependency injection on top and is started on demand and
    gracefully stopped on shutdown."""

    def __init__(self):
        #: A dict mapping registered services classes to their instances
        self.services: dict[type, typing.Any] = {}
        #: A list of service instances that were already started.
        self.started: list[typing.Any] = []

        self._depencency_graph: set[tuple[type, type]] = set()
        self._start_lock = asyncio.Lock()
        self.register(self)

    def register(self, service: typing.Any, _replace=False):
        """Register a new service.

        It is an error to register the same service name twice.
        The _replace switch is only for testing.
        """
        klass = service.__class__
        if klass in self.services and not _replace:
            raise RuntimeError(f"Services registered twice: {_clsname(klass)}")
        self.services[klass] = service

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a, **ka):
        """Calls :meth:`shutdown`."""
        await self.shutdown()

    async def shutdown(self):
        """Stop all started services."""
        while self.started:
            await self._stop(self.started[0])

    def get(self, klass: type[T], uninitialized_ok=False) -> T:
        """Request a service instance by its class.

        Requesting uninitialized services is a :exc:`RuntimeError`
        by default, unless `uninitialized_ok` is true.
        """
        obj = self.services.get(klass)
        if obj is None:
            raise AttributeError(f"Unknown service type: {_clsname(klass)}")
        if not (uninitialized_ok or obj in self.started):
            raise RuntimeError(f"Service not initialized yet: {_clsname(klass)}")
        return obj

    async def use(self, klass: type[T] = object) -> T:
        """Request a service and initialize it, if necessary."""
        obj = self.get(klass, uninitialized_ok=True)
        if obj not in self.started:
            await self._start(obj)
        return obj

    async def _stop(self, obj):
        """Un-initialize a service, if supported and required."""
        assert obj in self.services.values()
        if obj not in self.started:
            return

        # Stop services that depend on the current service
        stop_first = [
            dependent
            for dependent, dependency in self._depencency_graph
            if dependency == obj.__class__
        ]
        for stop in stop_first:
            await self._stop(self.services[stop])

        self.started.remove(obj)
        LOG.debug(f"Stopping: {_clsname(obj)}")
        if isinstance(obj, ManagedService):
            await obj.on_shutdown()

    async def _start(self, obj):
        """Initialize a service, if supported and required."""
        assert obj in self.services.values()
        if obj in self.started:
            return

        self.started.append(obj)
        LOG.debug(f"Starting: {_clsname(obj)}")

        # Everyone depends on ServiceRegistry
        if obj is not self:
            self._depencency_graph.add((obj.__class__, self.__class__))

        if isinstance(obj, ManagedService):
            # Poor man's dependency injection
            argsspec = inspect.signature(obj.on_start)
            args = {}
            for param in argsspec.parameters.values():
                deptype = param.annotation
                # Require service dependency
                args[param.name] = await self.use(deptype)
                # Remember service dependency graph
                self._depencency_graph.add((obj.__class__, deptype))

            await obj.on_start(**args)


class ManagedService(ABC):
    @abstractmethod
    async def on_start(self):
        """Called when the managed service is first requested.

        The method can request dependencies by accepting arguments with
        type annotations referencing other services. ManagedService
        instances are started before they are passed to this method.
        """
        pass

    @abstractmethod
    async def on_shutdown(self):
        """Called during shutdown to perform cleanup tasks.

        The shutdown order takes dependencies into account, all managed
        dependencies requested during :meth:`on_startup` are still
        available.
        """
        pass


class Health(enum.Enum):
    UNKNOWN = 0
    OK = 1
    WARN = 2
    CRITICAL = 3


class HealthReportingMixin:
    @abstractmethod
    async def check_health(self) -> tuple[Health, str]:
        pass


class BackgroundService(ManagedService, HealthReportingMixin):
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
            LOG.debug(f"Background task starting: {_clsname(self)}")
            await self.run()
        except asyncio.CancelledError:
            LOG.debug(f"Background task stopped: {_clsname(self)}")
            raise
        except BaseException:
            LOG.exception(f"Background task failed: {_clsname(self)}")
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
    import bbblb.services.poller
    import bbblb.services.recording
    import bbblb.services.analytics
    import bbblb.services.locks
    import bbblb.services.db
    import bbblb.services.bbb
    import bbblb.services.health
    import bbblb.services.tenants

    if logging:

        @config.watch
        def watch_debug_level(name, old, new):
            if name in ("DEBUG", ""):
                configure_logging(config)

    LOG.debug("Bootstrapping services...")

    ctx = ServiceRegistry()
    ctx.register(config)
    ctx.register(bbblb.services.health.HealthService(interval=config.POLL_INTERVAL))
    ctx.register(
        bbblb.services.db.DBContext(
            config.DB,
            create=config.DB_CREATE,
            migrate=config.DB_MIGRATE,
        ),
    )
    ctx.register(bbblb.services.bbb.BBBHelper())
    ctx.register(bbblb.services.locks.LockManager())
    ctx.register(
        bbblb.services.poller.MeetingPoller(config),
    )
    ctx.register(
        bbblb.services.recording.RecordingManager(config),
    )
    ctx.register(
        bbblb.services.analytics.AnalyticsHandler(config),
    )
    ctx.register(bbblb.services.tenants.TenantCache(config))

    if autostart:
        for service_type in ctx.services:
            await ctx.use(service_type)

    LOG.debug("Bootstrapping completed!")

    return ctx
