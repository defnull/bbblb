import asyncio
from functools import cached_property
import hashlib
import hmac
import typing
import aiohttp
import jwt
import lxml.etree
import lxml.builder
import logging
from urllib.parse import parse_qsl, urlencode, urljoin
from bbblb import model
from bbblb.settings import config

import yarl

XML = lxml.builder.ElementMaker()
ETree: typing.TypeAlias = lxml.etree._Element

LOG = logging.getLogger(__name__)
MAX_URL_SIZE = 1024 * 2
TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)

CONNPOOL: aiohttp.TCPConnector | None = None


async def get_pool():
    global CONNPOOL
    if not CONNPOOL or CONNPOOL.closed:
        CONNPOOL = aiohttp.TCPConnector(limit_per_host=10)
    return CONNPOOL


async def get_client():
    return aiohttp.ClientSession(connector=await get_pool(), connector_owner=False)


async def close_pool():
    if CONNPOOL and not CONNPOOL.closed:
        await CONNPOOL.close()


class BBBResponse:
    _xml: ETree | None = None
    _json: dict[str, typing.Any] | None
    status_code: int

    def __init__(
        self,
        xml: ETree | None = None,
        json: dict[str, typing.Any] | None = None,
        status_code=200,
    ):
        assert xml is not None or json is not None
        self._xml = xml
        self._json = json
        self.status_code = status_code

    @cached_property
    def xml(self) -> ETree:
        assert self._xml is not None
        return self._xml

    @cached_property
    def json(self) -> dict[str, typing.Any]:
        assert self._json is not None
        return self._json

    @cached_property
    def success(self):
        return self.find("returncode") == "SUCCESS"

    @cached_property
    def error(self):
        if self.success:
            return
        return self.find("messageKey", "missingErrorKey")

    def find(self, query, default: str | None = None):
        val = "___MISSING___"
        if self._xml is not None:
            val = self._xml.findtext(query, "___MISSING___")
        elif self._json is not None:
            val = self._json.get(query, "___MISSING___")
        return default if val == "___MISSING___" else val

    def __getattr__(self, name: str):
        val = self.find(name, "___MISSING___")
        if val == "___MISSING___":
            raise AttributeError(name)
        return val

    def raise_on_error(self):
        if not self.success:
            if isinstance(self, RuntimeError):
                raise self
            else:
                raise BBBError(self._xml, self._json, self.status_code)


class BBBError(BBBResponse, RuntimeError):
    def __init__(
        self,
        xml: ETree | None = None,
        json: dict[str, typing.Any] | None = None,
        status_code=200,
    ):
        BBBResponse.__init__(self, xml, json, status_code)
        assert not self.success and self.messageKey and self.message
        RuntimeError.__init__(self, f"{self.messageKey}: {self.message}")


def make_error(key: str, message: str, status_code=200, json=False):
    if json:
        return BBBError(
            json={"returncode": "FAILED", "messageKey": key, "message": message},
            status_code=status_code,
        )
    else:
        return BBBError(
            xml=XML.response(
                XML.returncode("FAILED"),
                XML.messageKey(key),
                XML.message(message),
            ),
            status_code=status_code,
        )


class BBBClient:
    def __init__(self, base_url: str, secret: str):
        self.base_url = base_url
        self.secret = secret
        self.session = None

    def encode_uri(self, endpoint: str, query: dict[str, str]):
        return urljoin(self.base_url, endpoint) + "?" + self.sign_query(endpoint, query)

    def sign_query(self, endpoint: str, query: dict[str, str]):
        if query:
            query.pop("checksum", None)
            qs = urlencode(query)
            checksum = hashlib.sha256(
                (endpoint + qs + self.secret).encode("UTF-8")
            ).hexdigest()
            return f"{qs}&checksum={checksum}"
        else:
            checksum = hashlib.sha256(
                (endpoint + self.secret).encode("UTF-8")
            ).hexdigest()
            return f"checksum={checksum}"

    async def action(
        self,
        endpoint: str,
        query: dict[str, str] | None = None,
        body: bytes | typing.AsyncIterable[bytes] | None = None,
        content_type: str | None = None,
        method: str | None = None,
        expect_json=False,
    ) -> BBBResponse:
        method = method or ("POST" if body else "GET")
        url = self.encode_uri(endpoint, query or {})
        headers = {}

        if query and len(url) > MAX_URL_SIZE:
            if body:
                return make_error(
                    "internalError",
                    "URL too long many parameters for request with explicit body",
                )
            url = urljoin(self.base_url, endpoint)
            body = self.sign_query(endpoint, query).encode("ASCII")
            content_type = "application/x-www-form-urlencoded"

        if body:
            headers["content-type"] = content_type

        # Required because aiohttp->yarl 'normalizes' the query string which breaks
        # the checksum (╯°□°)╯︵ ┻━┻
        url = yarl.URL(url, encoded=True)

        LOG.debug(f"Request: {url}")
        try:
            async with (
                await get_client() as client,
                client.request(
                    method, url, data=body, headers=headers, timeout=TIMEOUT
                ) as response,
            ):
                if response.status not in (200,):
                    return make_error(
                        "internalError",
                        f"Unexpected response status: {response.status}",
                        response.status,
                    )
                if expect_json and response.content_type == "application/json":
                    return BBBResponse(json=await response.json())
                else:
                    parser = lxml.etree.XMLParser()
                    async for chunk in response.content.iter_any():
                        parser.feed(chunk)
                    return BBBResponse(xml=parser.close())
        except BaseException:
            return make_error("internalError", "Unresponsive backend server")


len2hashfunc = {40: hashlib.sha1, 64: hashlib.sha256, 128: hashlib.sha512}


def verify_checksum_query(
    action: str, query: str, secrets: list[str]
) -> tuple[dict[str, str], str]:
    """Verify a checksum protected query string against a list of secrets.
    Returns the parsed query without the checksum, and the secret. Raises
    an appropriate BBBError if verification fails."""
    cleaned: list[tuple[str, str]] = []
    checksum = None
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key == "checksum":
            checksum = value
        else:
            cleaned.append((key, value))
    if not checksum:
        raise make_error("checksumError", "Missing checksum parameter")
    cfunc = len2hashfunc.get(len(checksum))
    if not cfunc:
        raise make_error(
            "checksumError", "Unknown checksum algorithm or invalid checksum string"
        )
    expected = bytes.fromhex(checksum)
    hash = cfunc((action + urlencode(cleaned)).encode("UTF-8"))
    for secret in secrets:
        clone = hash.copy()
        clone.update(secret.encode("ASCII"))
        if hmac.compare_digest(clone.digest(), expected):
            return dict(cleaned), secret
    raise make_error("checksumError", "Checksum did not pass verification")


async def trigger_callback(
    method: str,
    url: str,
    params: typing.Mapping[str, str] | None = None,
    data: bytes | typing.Mapping[str, str] | None = None,
):
    async with await get_client() as client:
        for i in range(config.WEBHOOK_RETRY):
            try:
                async with client.request(method, url, params=params, data=data) as rs:
                    rs.raise_for_status()
            except aiohttp.ClientError:
                LOG.warning(
                    f"Failed to forward callback {url} ({i + 1}/{config.WEBHOOK_RETRY})"
                )
                await asyncio.sleep(10 * i)
                continue


async def fire_callback(callback: model.Callback, payload: dict, clear=True):
    url = callback.forward
    key = callback.tenant.secret
    data = {"signed_parameters": jwt.encode(payload, key, "HS256")}
    await trigger_callback("POST", url, data=data)
    async with model.scope() as session:
        await session.delete(callback)
