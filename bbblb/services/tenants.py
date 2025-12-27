import asyncio
import time

from bbblb import model
from bbblb.services import ManagedService, HealthReportingMixin, Health
from bbblb.services.db import DBContext

import logging

from bbblb.settings import BBBLBConfig

LOG = logging.getLogger(__name__)


class TenantCache(ManagedService, HealthReportingMixin):
    def __init__(self, config: BBBLBConfig):
        self.config = config
        self.cache = {}
        self.cache_timeout = max(1, config.TENANT_CACHE)
        self.next_refresh = 0
        self.refresh_lock = asyncio.Lock()

    async def on_start(self, db: DBContext):
        self.db = db

    async def on_shutdown(self):
        pass

    async def check_health(self) -> tuple[Health, str]:
        await self.refresh_cache()
        if not self.cache:
            return Health.WARN, "No tenants enabled"
        return Health.OK, f"Found {len(self.cache)} tenants"

    async def refresh_cache(self, force=False):
        async with self.refresh_lock:
            if not force and self.next_refresh > time.time():
                return False
            stmt = model.Tenant.select(enabled=True).options(
                model.selectinload(model.Tenant.overrides)
            )
            async with self.db.session() as session:
                results = (await session.execute(stmt)).scalars().all()
            self.cache = {t.name: t for t in results}
            self.next_refresh = time.time() + self.cache_timeout
            return True

    async def get_tenant_by_name(self, name):
        await self.refresh_cache()
        return self.cache.get(name, None)

    async def get_tenant_by_realm(self, realm):
        await self.refresh_cache()
        return next((t for t in self.cache.values() if t.realm == realm), None)
