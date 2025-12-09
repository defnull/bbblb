import datetime
import logging
import time
from bbblb import model
from bbblb.lib.bbb import BBBClient
from bbblb.services import BackgroundService
from bbblb.services.db import DBContext
from bbblb.settings import BBBLBConfig

import asyncio
import typing
import aiohttp
import jwt


LOG = logging.getLogger(__name__)


JWT_ALGORITHMS = ["HS256", "HS384", "HS512"]


class BBBHelper(BackgroundService):
    async def on_start(self, config: BBBLBConfig, db: DBContext):
        self.config = config
        self.db = db
        self.connector = aiohttp.TCPConnector(limit_per_host=10)
        await super().on_start()

    async def on_shutdown(self):
        if self.connector and not self.connector.closed:
            await self.connector.close()
        await super().on_shutdown()

    async def run(self):
        while True:
            await self._cleanup_recordings()
            await asyncio.sleep(60)

    async def _cleanup_recordings(self):
        """Callbacks need to be kept in DB for some time because we never know when
        they may be fired, or if they are re-fired after some time (e.g. after a
        recording rebuild). Here we make sure they are cleaned up eventually.
        """

        max_age = datetime.timedelta(days=7)
        async with self.db.connect() as conn:
            stmt = model.Callback.delete(
                model.Callback.created < (model.utcnow() - max_age)
            )
            result = await conn.execute(stmt)
            if result.rowcount > 0:
                LOG.debug(f"Cleaned up {result.rowcount} callback objects")

    def make_http_client(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(connector=self.connector, connector_owner=False)

    def connect(self, server, secret) -> BBBClient:
        return BBBClient(server, secret, session=self.make_http_client())

    async def _trigger_callback(
        self,
        method: str,
        url: str,
        params: typing.Mapping[str, str] | None = None,
        data: bytes | typing.Mapping[str, str] | None = None,
        json: object | None = None,
        headers: dict[str, str] | None = None,
    ):
        for i in range(self.config.WEBHOOK_RETRY):
            async with self.make_http_client() as client:
                try:
                    async with client.request(
                        method,
                        url,
                        params=params,
                        data=data,
                        json=json,
                        headers=headers,
                    ) as rs:
                        rs.raise_for_status()
                except aiohttp.ClientError:
                    LOG.warning(
                        f"Failed to forward callback {url} ({i + 1}/{self.config.WEBHOOK_RETRY})"
                    )
                    await asyncio.sleep(10 * i)
                    continue

    async def fire_unsigned_callback(
        self, callback: model.Callback, params: typing.Mapping[str, str] | None = None
    ):
        """Fire an unsigned (legacy) callback, e.g. end meeting callbacks"""
        if callback.forward:
            await self._trigger_callback("GET", callback.forward, params=params)

    async def fire_analytics_callback(
        self, callback: model.Callback, payload: object = None
    ):
        """Fire an 'analytics' style callback with an Authorization header
        (Bearer, JWT) and unsigned JSON payload."""
        if callback.forward:
            timeout = 24 * 6300  # Default timeout used by BBB
            key = callback.tenant.secret
            token = jwt.encode(
                {"exp": int(time.time()) + timeout}, key, JWT_ALGORITHMS[0]
            )
            await self._trigger_callback(
                "POST",
                callback.forward,
                json=payload,
                headers={"Authorization": f"bearer {token}"},
            )

    async def fire_signed_callback(self, callback: model.Callback, payload: dict):
        """Fire a 'recording-ready' style callback using a form request
        wrapping JWT encoded (signed) payload."""
        if callback.forward:
            key = callback.tenant.secret
            await self._trigger_callback(
                "POST",
                callback.forward,
                data={"signed_parameters": jwt.encode(payload, key, JWT_ALGORITHMS[0])},
            )
