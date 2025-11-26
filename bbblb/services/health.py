import asyncio
import logging
from bbblb.services import BackgroundService, Health, ManagedService, ServiceRegistry

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

                for name in self.sr.started:
                    obj = self.sr.get(name)
                    if not isinstance(obj, ManagedService):
                        continue
                    status, msg = await obj.check_health()
                    self.checks[name] = (status, msg)
                    LOG.debug(f"Check: {name} {status.name} ({msg})")
            except asyncio.CancelledError:
                self.checks.clear()
                raise
            except BaseException:
                continue

    async def check_health(self) -> tuple[Health, str]:
        # TODO
        return await super().check_health()
