import logging
from bbblb import model
from bbblb.lib.bbb import BBBClient
from bbblb.services import ManagedService
from bbblb.services.db import DBContext
from bbblb.settings import BBBLBConfig

import asyncio
import typing
import aiohttp
import jwt


LOG = logging.getLogger(__name__)


class BBBHelper(ManagedService):
    async def on_start(self, config: BBBLBConfig, db: DBContext):
        self.config = config
        self.db = db
        self.connector = aiohttp.TCPConnector(limit_per_host=10)

    async def on_shutdown(self):
        if self.connector and not self.connector.closed:
            await self.connector.close()

    def make_http_client(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(connector=self.connector, connector_owner=False)

    def connect(self, server, secret) -> BBBClient:
        return BBBClient(server, secret, session=self.make_http_client())

    async def trigger_callback(
        self,
        method: str,
        url: str,
        params: typing.Mapping[str, str] | None = None,
        data: bytes | typing.Mapping[str, str] | None = None,
    ):
        async with self.make_http_client() as client:
            for i in range(self.config.WEBHOOK_RETRY):
                try:
                    async with client.request(
                        method, url, params=params, data=data
                    ) as rs:
                        rs.raise_for_status()
                except aiohttp.ClientError:
                    LOG.warning(
                        f"Failed to forward callback {url} ({i + 1}/{self.config.WEBHOOK_RETRY})"
                    )
                    await asyncio.sleep(10 * i)
                    continue

    async def fire_callback(self, callback: model.Callback, payload: dict, clear=True):
        url = callback.forward
        key = callback.tenant.secret
        data = {"signed_parameters": jwt.encode(payload, key, "HS256")}
        await self.trigger_callback("POST", url, data=data)
        if clear:
            async with self.db.session() as session, session.begin():
                await session.delete(callback)
