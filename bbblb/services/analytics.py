import asyncio
import json
import logging

from bbblb import model
from bbblb.services import ManagedService
from bbblb.settings import BBBLBConfig

LOG = logging.getLogger(__name__)


class AnalyticsHandler(ManagedService):
    """Store analytics data to {PATH_DATA}/{tenant}/{meetingID}.json"""

    def __init__(self, config: BBBLBConfig):
        self.store_path = (config.PATH_DATA / "analytics").resolve()

    async def on_start(self):
        await super().on_start()

    async def on_shutdown(self):
        return await super().on_shutdown()

    async def store(self, tenant: model.Tenant, data: dict):
        """Store the payload of an analytics callback"""
        internal_meeting_id = data["internal_meeting_id"]
        dst = self.store_path / tenant.name / f"{internal_meeting_id}.json"
        await asyncio.to_thread(dst.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(dst.write_text, json.dumps(data))
