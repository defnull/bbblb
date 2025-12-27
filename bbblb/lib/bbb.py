import logging

from functools import cached_property
import hashlib
import hmac
import typing
import aiohttp
import lxml.etree
import lxml.builder  # type: ignore

from urllib.parse import parse_qsl, urlencode, urljoin

import yarl

LOG = logging.getLogger(__name__)
XML = lxml.builder.ElementMaker()
ETree: typing.TypeAlias = lxml.etree._Element | lxml.etree._ElementTree
Element: typing.TypeAlias = lxml.etree._Element
SubElement = lxml.etree.SubElement

LOG = logging.getLogger(__name__)
MAX_URL_SIZE = 1024 * 2
TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)


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
    def __init__(
        self, base_url: str, secret: str, session: aiohttp.ClientSession | None = None
    ):
        self.session = session or aiohttp.ClientSession()
        self.base_url = base_url
        self.secret = secret

    def encode_uri(self, endpoint: str, query: dict[str, str]):
        return urljoin(self.base_url, endpoint) + "?" + self.sign_query(endpoint, query)

    def sign_query(self, endpoint: str, query: dict[str, str]):
        return sign_query(endpoint, query, secret=self.secret)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a, **ka):
        await self.close()

    async def close(self):
        await self.session.close()

    async def action(
        self,
        endpoint: str,
        query: dict[str, str] | None = None,
        body: bytes | typing.AsyncIterable[bytes] | None = None,
        content_type: str | None = None,
        method: str | None = None,
        expect_json=False,
        timeout: int | float | None = None,
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

        if timeout and timeout > 0:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
        else:
            client_timeout = TIMEOUT

        LOG.debug(f"Request: {url}")
        try:
            async with (
                self.session.request(
                    method, url, data=body, headers=headers, timeout=client_timeout
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


def sign_query(endpoint: str, query: dict[str, str], secret: str):
    if query:
        query.pop("checksum", None)
        qs = urlencode(query)
        checksum = hashlib.sha256((endpoint + qs + secret).encode("UTF-8")).hexdigest()
        return f"{qs}&checksum={checksum}"
    else:
        checksum = hashlib.sha256((endpoint + secret).encode("UTF-8")).hexdigest()
        return f"checksum={checksum}"


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
