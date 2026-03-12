# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    """A service registry with some basic dependency injection.

    Services are identified by their type. There are no scopes, all
    services are singletons.

    Service factories (e.g. their class constructors) can make use of
    other registered services via dependency injection.

    If a service class implements :cls:`ManagedService`, it is started
    if needed and gracefully stopped during shutdown.
    """

    def __init__(self):
        #: A dict mapping registered services classes to their factory method
        self.services: dict[type, typing.Callable[..., typing.Any]] = {}
        self.starting: list[type] = []
        self.started: dict[type, typing.Any] = {}

        self._depencency_graph: set[tuple[type, type]] = set()
        self._start_lock = asyncio.Lock()
        self.register(self.__class__, lambda: self)

    def register(
        self,
        klass: type[T],
        factory: typing.Callable[..., T] | None = None,
        _replace=False,
    ):
        """Register a new service class.

        You may provide a factory that returns an instance of the
        service class. If no factory is provided, the class itself
        (it's constructor) is used to create an instance.

        The factory (or class constructor) may request dependencies via
        named and type-annotated arguments. Those are injected during
        initialization of the service. Dependencies are fully
        initialized and started before the are injected.

        It is an error to register the same class twice. The _replace
        switch is only used for testing.
        """
        if klass in self.services and not _replace:
            raise RuntimeError(f"Services registered twice: {_clsname(klass)}")
        self.services[klass] = factory or klass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a, **ka):
        """Calls :meth:`shutdown`."""
        await self.shutdown()

    async def shutdown(self):
        """Stop all started services in correct order according to their
        dependency graph."""
        while self.started:
            await self._stop(next(iter(self.started), None))

    def get(self, klass: type[T]) -> T:
        """Request a service instance, identified by its class.

        Requesting uninitialized services is a :exc:`RuntimeError`.
        """
        if klass in self.started:
            return self.started[klass]
        if klass not in self.services:
            raise RuntimeError(f"Unknown service type: {_clsname(klass)}")
        raise RuntimeError(f"Service not started yet: {_clsname(klass)}")

    async def use(self, klass: type[T]) -> T:
        """Request a service instance and initialize it first, if necessary."""
        async with self._start_lock:
            return await self._start(klass)

    async def _start(self, klass: type[T]) -> T:
        """Initialize and return a service instance."""
        if klass in self.started:
            return self.started[klass]
        if klass not in self.services:
            raise RuntimeError(f"Unknown service type: {_clsname(klass)}")
        if klass in self.starting:
            raise RuntimeError(
                f"Dependency loop: {'->'.join(map(_clsname, self.starting))}"
            )
        self.starting.append(klass)

        LOG.debug(f"Starting: {_clsname(klass)}")

        # Everyone depends on ServiceRegistry
        if klass is not self.__class__:
            self._depencency_graph.add((klass, self.__class__))

        args = {}
        factory = self.services[klass]

        # Poor man's dependency injection
        argsspec = inspect.signature(factory)
        for param in argsspec.parameters.values():
            deptype = param.annotation
            # Require service dependency
            args[param.name] = await self._start(deptype)
            # Remember service dependency graph
            self._depencency_graph.add((klass, deptype))

        obj = typing.cast(T, factory(**args))
        if not isinstance(obj, klass):
            raise RuntimeError(
                f"Unexpected class {type(obj)} returned from factory {factory}, expected {klass}"
            )
        if isinstance(obj, ManagedService):
            await obj.on_start()

        self.started[klass] = obj

        _tmp = self.starting.pop()
        if _tmp is not klass:
            raise RuntimeError(f"Unexpected class on service start stack: {_tmp}")

        return obj

    async def start_all(self):
        """Initialize all services."""
        for klass in self.services:
            await self.use(klass)

    async def _stop(self, klass):
        """Un-initialize a service and all services that depend on it."""
        assert klass in self.services
        if klass not in self.started:
            return

        # Stop services that depend on the current service
        stop_first = [
            dependent
            for dependent, dependency in self._depencency_graph
            if dependency == klass
        ]
        for stop in stop_first:
            await self._stop(stop)

        LOG.debug(f"Stopping: {_clsname(klass)}")
        obj = self.started.pop(klass)
        if isinstance(obj, ManagedService):
            await obj.on_shutdown()


class ManagedService(ABC):
    """
    Classes implementing this interface are started when first requested
    as a dependency or runtime service from ManagedService and stopped
    on shutdown.
    """

    @abstractmethod
    async def on_start(self):
        """Called directly after the managed service instance is created."""
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
    # Configure root logger, if logging is not configured already.
    if not ROOT_LOGGER.hasHandlers():
        ROOT_LOGGER.setLevel(logging.DEBUG if config.DEBUG else logging.INFO)
        ROOT_LOGGER.propagate = False  # Detach from actual root logger
        ch = logging.StreamHandler(stream=sys.stderr)
        ch.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s - %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
        ROOT_LOGGER.addHandler(ch)
        ROOT_LOGGER.warning(
            f"Logging is not configured for {ROOT_LOGGER.name!r} logger, adding a fallback console handler."
        )


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
    ctx.register(BBBLBConfig, lambda: config)
    ctx.register(bbblb.services.health.HealthService)
    ctx.register(bbblb.services.db.DBContext)
    ctx.register(bbblb.services.bbb.BBBHelper)
    ctx.register(bbblb.services.locks.LockManager)
    ctx.register(bbblb.services.poller.MeetingPoller)
    ctx.register(bbblb.services.recording.RecordingManager)
    ctx.register(bbblb.services.analytics.AnalyticsHandler)
    ctx.register(bbblb.services.tenants.TenantCache)

    if autostart:
        await ctx.start_all()

    LOG.debug("Bootstrapping completed!")

    return ctx
