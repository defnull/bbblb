import asyncio
import logging
from bbblb.services import (
    BackgroundService,
    Health,
    HealthReportingMixin,
    ServiceRegistry,
)

LOG = logging.getLogger(__name__)


class HealthService(BackgroundService):
    def __init__(self, interval: int):
        self.interval = interval
        self.checks = {}

    async def on_start(self, sr: ServiceRegistry):
        self.sr = sr
        await super().on_start()

    async def run(self):
        while True:
            try:
                await asyncio.sleep(self.interval)

                for obj in sorted(self.sr.started, key=lambda s: s.__class__.__name__):
                    if not isinstance(obj, HealthReportingMixin):
                        continue
                    try:
                        status, msg = await obj.check_health()
                    except Exception as exc:
                        status = Health.CRITICAL
                        msg = f"Internal error in health check: {exc}"
                    name = obj.__class__.__qualname__
                    self.checks[name] = (status, msg)
                    LOG.debug(f"[{name}] {status.name} {msg}")
            except asyncio.CancelledError:
                self.checks.clear()
                raise
            except BaseException:
                continue
