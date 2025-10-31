import asyncio
import functools
import hashlib
import hmac
import typing
from urllib.parse import parse_qs
import aiohttp
import logging
import jwt

from bbblb.api import bbbapi
from bbblb import bbblib, model, recordings
from bbblb.settings import config

from starlette.requests import Request
from starlette.routing import Route
from starlette.responses import Response, JSONResponse

LOG = logging.getLogger(__name__)


api_routes = []


def api(route: str, methods=["GET", "POST"], name: str | None = None):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            try:
                out = await func(request)
            except BaseException:
                LOG.exception("Unhandled exception")
                out = JSONResponse(
                    {"error": "Unhandled exception", "message": "You found a bug!"},
                    status_code=500,
                )
            return out

        path = "/" + route
        api_routes.append(Route(path, wrapper, methods=methods, name=name))
        return wrapper

    return decorator


##
### Callback handling
##


async def trigger_callback(
    method: str,
    url: str,
    params: typing.Mapping[str, str] | None = None,
    data: bytes | typing.Mapping[str, str] | None = None,
):
    for i in range(config.WEBHOOK_RETRY):
        try:
            async with bbblib.HTTP.request(method, url, params=params, data=data) as rs:
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


@api("v1/callback/{uuid}/end/{sig}", name="bbblb:callback_end")
@model.transactional(autocommit=True)
async def handle_callback_end(request: Request):
    """Handle the meetingEndedURL callback"""

    try:
        meeting_uuid = request.path_params["uuid"]
        callback_sig = request.path_params["sig"]
    except (KeyError, ValueError):
        LOG.warning("Callback called with missing or invalid parameters")
        return Response("Invalid callback URL", 400)

    # Verify callback signature
    sig = f"bbblb:callback:end:{meeting_uuid}".encode("ASCII")
    sig = hmac.digest(config.SECRET.encode("UTF8"), sig, hashlib.sha256)
    if not hmac.compare_digest(sig, bytes.fromhex(callback_sig)):
        LOG.warning("Callback signature mismatch")
        return Response("Access denied, signature check failed", 401)

    # Check if we have to notify a frontend
    stmt = model.Callback.select(uuid=meeting_uuid, type=model.CALLBACK_TYPE_END)
    callback = (await model.ScopedSession.execute(stmt)).scalar_one_or_none()
    if callback:
        if callback.forward:
            # Fire and forget callback forward task
            asyncio.ensure_future(
                trigger_callback("GET", callback.forward, params=request.query_params)
            )
        await model.ScopedSession.delete(callback)

    # Mark meeting as ended, if still present
    stmt = model.Meeting.select(uuid=meeting_uuid)
    meeting = (await model.ScopedSession.execute(stmt)).scalar_one_or_none()
    if meeting:
        LOG.info("Meeting ended (callback): {meeting}")
        await bbbapi.forget_meeting(meeting)

    return Response("OK", 200)


@api("v1/callback/{uuid}/{type}", name="bbblb:callback_proxy")
@model.transactional(autocommit=True)
async def handle_callback_proxy(request: Request):
    try:
        meeting_uuid = request.path_params["uuid"]
        callback_type = request.path_params["type"]
    except (KeyError, ValueError):
        LOG.warning("Callback called with missing or invalid parameters")
        return Response("Invalid callback URL", 400)

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > config.MAX_BODY:
            return Response("Request Entity Too Large", 413)

    try:
        form = parse_qs(body.decode("UTF-8"))
        payload = form["signed_parameters"][0]
    except (UnicodeDecodeError, KeyError, IndexError):
        return Response("Invalid request", 400)

    stmt = model.Callback.select(uuid=meeting_uuid, type=callback_type)
    callbacks = (await model.ScopedSession.execute(stmt)).scalars().all()
    if not callbacks:
        # Strange, there should be at least one. Already fired?
        return Response("OK", 200)

    try:
        origin = callbacks[0].server
        payload = jwt.decode(payload, origin.secret, algorithms=["HS256"])
    except BaseException:
        return Response("Access denied, signature check failed", 401)

    # Find and trigger callbacks

    for callback in callbacks:
        asyncio.create_task(fire_callback(callback, payload, clear=True))

    return Response("OK", 200)


##
### Recording Upload
##


class AuthContext:
    def __init__(self, claims):
        self.claims = claims

    @functools.cached_property
    def scopes(self):
        return set(self.claims.get("scope", "").split())

    @property
    def sub(self):
        return self.claims["sub"]

    def has_scope(self, *scopes):
        return any(scope in self.scopes for scope in scopes)

    @classmethod
    async def from_request(cls, request: Request):
        auth = request.headers.get("Authorization")
        if not auth:
            return

        try:
            scheme, credentials = auth.split()
            if scheme.lower() != "bearer":
                return

            header = jwt.get_unverified_header(credentials)
            kid = header.get("kid")
            if kid:
                # TODO Cache this!
                server = await model.Server.find(domain=kid)
                if not server:
                    return
                payload = jwt.decode(credentials, server.secret, algorithms=["HS256"])
                payload["scope"] = "bbb"
                payload["sub"] = server.domain
                return AuthContext(payload)
            else:
                payload = jwt.decode(credentials, config.SECRET, algorithms=["HS256"])
                return AuthContext(payload)

        except BaseException:
            LOG.exception("Request denied")
            return


@api("v1/recording/upload", methods=["POST"], name="bbblb:upload")
async def handle_recording_upload(request: Request):
    auth = await AuthContext.from_request(request)

    if not auth or not auth.has_scope("rec", "rec:upload", "bbb"):
        return JSONResponse(
            {"error": "Access denied", "message": "This API is protected"},
            status_code=401,
        )

    ctype = request.headers["content-type"]
    if ctype != "application/x-tar":
        return JSONResponse(
            {
                "error": "Unsupported Media Type",
                "message": f"Expected application/x-tar, got {ctype}",
            },
            status_code=415,
            headers={"Accept-Post": "application/x-tar"},
        )

    force_tenant = request.query_params.get("tenant")

    try:
        importer = request.app.state.importer
        assert isinstance(importer, recordings.RecordingImporter)
        task = await importer.start_import(request.stream(), force_tenant=force_tenant)
        return JSONResponse(
            {"message": "Import accepted", "importId": task.import_id}, status_code=202
        )
    except BaseException as exc:
        LOG.exception("Import failed")
        return JSONResponse(
            {"error": "Import failed", "message": str(exc)}, status_code=500
        )
