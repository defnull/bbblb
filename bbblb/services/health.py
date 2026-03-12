# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import logging
from bbblb.services import (
    BackgroundService,
    Health,
    HealthReportingMixin,
    ServiceRegistry,
)
from bbblb.settings import BBBLBConfig

LOG = logging.getLogger(__name__)


class HealthService(BackgroundService):
    def __init__(self, config: BBBLBConfig, sr: ServiceRegistry):
        self.sr = sr
        self.interval = config.POLL_INTERVAL
        self.checks = {}

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
                    if status == Health.OK:
                        LOG.debug(f"[{name}] {status.name} {msg}")
                    else:
                        LOG.warning(f"[{name}] {status.name} {msg}")
            except asyncio.CancelledError:
                self.checks.clear()
                raise
            except BaseException:
                continue
