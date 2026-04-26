# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import functools
import json
from urllib.parse import parse_qs
import logging
import uuid
import jwt

from bbblb.services.analytics import AnalyticsHandler
from bbblb.services.bbb import JWT_ALGORITHMS
from bbblb.web import bbbapi
from bbblb import model, utils

from starlette.requests import Request
from starlette.routing import Route
from starlette.responses import RedirectResponse, Response, JSONResponse

from bbblb.web import ApiRequestContext
from bbblb.services.recording import RecordingManager
from bbblb.web import playback
from bbblb.utils import hmac_verify

LOG = logging.getLogger(__name__)


api_routes = []


def api(route: str, methods=["GET", "POST"], name: str | None = None):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            try:
                async with BBBLBApiRequest(request) as ctx:
                    out = await func(ctx)
            except ApiError as exc:
                out = exc.to_response()
            except BaseException:
                LOG.exception("Unhandled exception")
                out = ApiError(
                    500, "Unhandled exception", "You found a bug!"
                ).to_response()
            return out

        path = "/" + route
        api_routes.append(Route(path, wrapper, methods=methods, name=name))
        return wrapper

    return decorator


class BBBLBApiRequest(ApiRequestContext):
    _auth = None

    async def auth(self):
        if not self._auth:
            self._auth = await AuthContext.from_request(self, self.request)
        return self._auth


class ApiError(RuntimeError):
    def __init__(self, status: int, error: str, message: str, **args):
        self.status = status
        self.ctx = {"error": error, "message": message, **args}
        super().__init__(f"{error} ({status}) {message} {args or ''}")

    def to_response(self):
        return JSONResponse(
            self.ctx,
            status_code=self.status,
        )


TENANT_SCOPE = "signed:tenant"  # The only scope that tenant-tokens have
SERVER_SCOPE = "signed:server"  # The only scope that server-tokens have
_API_SCOPES = {
    "rec": ("list", "upload", "update", "delete"),
    "tenant": ("list", "create", "update", "delete", "secret"),
    "server": ("list", "create", "update", "delete", "state"),
}
API_SCOPES = set(_API_SCOPES) | set(
    f"{resource}:{action}"
    for (resource, actions) in _API_SCOPES.items()
    for action in actions
)


class AuthContext:
    def __init__(
        self,
        claims,
        server: model.Server | None = None,
        tenant: model.Tenant | None = None,
    ):
        self.claims = claims
        self.server = server
        self.tenant = tenant

    @functools.cached_property
    def scopes(self):
        return set(self.claims.get("scope", "").split())

    @property
    def sub(self):
        return self.claims["sub"]

    def has_scope(self, *scopes: str):
        return any(scope in self.scopes for scope in scopes)

    def ensure_scope(self, *scopes: str):
        """Ensure that the token has one of the given scopes. Return the matching scope."""
        if "admin" in self.scopes:
            return "admin"
        for scope in scopes:
            if scope in self.scopes:
                return scope
            if ":" in scope and scope.split(":", 1)[0] in self.scopes:
                return scope
        raise ApiError(401, "Access denied", "This API is protected")

    @classmethod
    async def from_request(
        cls, ctx: ApiRequestContext, request: Request
    ) -> "AuthContext":
        auth = request.headers.get("Authorization")
        if not auth:
            raise ApiError(
                403, "Authentication required", "This API requires authentication"
            )

        try:
            scheme, credentials = auth.split()
            if scheme.lower() != "bearer":
                raise ApiError(401, "Access denied", "Unsupported Authorization type")

            header = jwt.get_unverified_header(credentials)
            kid = header.get("kid")  # type: str|None
            if kid and kid.startswith("bbb:"):
                # TODO: Disabled servers can still upload recordings. Correct?
                server = await model.Server.find(ctx.session, domain=kid[4:])
                if not server:
                    raise ApiError(
                        401, "Access denied", "Unknown server in key identifier"
                    )
                payload = jwt.decode(
                    credentials,
                    server.secret,
                    algorithms=["HS256"],
                    audience=ctx.config.DOMAIN,
                )
                payload["scope"] = SERVER_SCOPE
                payload["sub"] = server.domain
                return AuthContext(payload, server=server)
            elif kid and kid.startswith("tenant:"):
                tenant = await model.Tenant.find(
                    ctx.session, name=kid[7:], enabled=True
                )
                if not tenant:
                    raise ApiError(
                        401,
                        "Access denied",
                        "Unknown or disabled tenant in key identifier",
                    )
                payload = jwt.decode(
                    credentials,
                    tenant.secret,
                    algorithms=["HS256"],
                    audience=ctx.config.DOMAIN,
                )
                payload["scope"] = TENANT_SCOPE
                payload["sub"] = tenant.name
                return AuthContext(payload, tenant=tenant)
            elif kid:
                raise ApiError(401, "Access denied", "Unknown key identifier type")
            else:
                payload = jwt.decode(
                    credentials,
                    ctx.config.SECRET,
                    algorithms=["HS256"],
                    audience=ctx.config.DOMAIN,
                )
                return AuthContext(payload)
        except jwt.exceptions.InvalidAudienceError:
            raise ApiError(401, "Access denied", "Invalid token audience")
        except jwt.exceptions.InvalidSignatureError:
            raise ApiError(401, "Access denied", "Invalid token signature")
        except (
            jwt.exceptions.ExpiredSignatureError,
            jwt.exceptions.ImmatureSignatureError,
        ):
            raise ApiError(401, "Access denied", "Expired token signature")
        except jwt.exceptions.PyJWTError:
            raise ApiError(401, "Access denied", "Invalid or missing token")


##
### Callback handling
##


@api("v1/callback/end/{uuid_signed}", name="bbblb:callback_end")
async def handle_callback_end(ctx: BBBLBApiRequest):
    """Handle the meetingEndedURL callback"""

    uuid_signed = ctx.request.path_params["uuid_signed"]
    meeting_uuid = hmac_verify(uuid_signed, ctx.config.SECRET, "end")
    if not meeting_uuid:
        LOG.warning("Callback signature mismatch")
        return Response("Access denied, signature check failed", 401)

    async with ctx.session.begin():
        # Check if we have to notify a frontend
        stmt = model.Callback.select(uuid=meeting_uuid, type=model.CALLBACK_TYPE_END)
        callback = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if callback:
            # Fire and forget callback forward task
            asyncio.create_task(
                ctx.bbb.fire_unsigned_callback(
                    callback, params=ctx.request.query_params
                )
            )

        # Mark meeting as ended, if still present
        stmt = model.Meeting.select(uuid=meeting_uuid)
        meeting = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if meeting:
            LOG.info(f"Meeting ended (callback): {meeting}")
            await bbbapi.forget_meeting(ctx.session, meeting)

    return Response("OK", 200)


@api("v1/callback/{uuid}/{type}", name="bbblb:callback_proxy")
async def handle_callback_proxy(ctx: BBBLBApiRequest):
    meeting_uuid = ctx.request.path_params["uuid"]
    callback_type = ctx.request.path_params["type"]

    # Fetch matching callbacks instance
    stmt = model.Callback.select(uuid=meeting_uuid, type=callback_type)
    callbacks = (await ctx.session.execute(stmt)).scalars().all()
    if not callbacks:
        # Strange, there should be at least one. Already fired?
        raise ApiError(404, "NotFound", "Callback not found")

    origin = callbacks[0].server

    async def read_body():
        body = bytearray()
        async for chunk in ctx.request.stream():
            body.extend(chunk)
            if len(body) > ctx.config.MAX_BODY:
                raise ApiError(413, "BadRequest", "Request body too large")
        return body

    # BBB knows two different types of JWT enhanced callbacks:
    # analytics: Minimal JWT in Authorization (beare) header, unsigned payload.
    # everything else: Signed JWT payload in form["signed_parameters"].

    ctype = ctx.request.headers.get("Content-Type", "").lower()
    auth = ctx.request.headers.get("Authorization")
    payload: None | dict = None

    if ctype == "application/json":
        if not auth or not auth.lower().startswith("bearer "):
            raise ApiError(
                403, "AccessDenied", "Missing or unsupported Authorization header"
            )

        try:
            token = auth.split(" ", 1)[-1].strip()
            jwt.decode(token, origin.secret, algorithms=JWT_ALGORITHMS)
        except BaseException:
            raise ApiError(401, "AccessDenied", "Invalid JWT")

        body = await read_body()

        try:
            payload = json.loads(body)
            assert isinstance(payload, dict)
        except BaseException:
            raise ApiError(400, "BadRequest", "Invalid JSON")

        # TODO: Fix meeting_id everywhere and also revert all the callbacks in metadata?
        if "meeting_id" in payload:
            payload["meeting_id"] = utils.remove_scope(payload["meeting_id"])

        # Intercept callbacks we are interested in
        if ctx.config.ANALYTICS_STORE and callback_type == "analytics":
            analytics = await ctx.services.use(AnalyticsHandler)
            asyncio.create_task(analytics.store(callbacks[0].tenant, payload))

        # Forward callbacks to front-ends
        for callback in callbacks:
            asyncio.create_task(ctx.bbb.fire_analytics_callback(callback, payload))

    elif ctype == "application/x-www-form-urlencoded":
        body = await read_body()

        try:
            signed_parameters = parse_qs(body.decode("UTF-8"))["signed_parameters"][0]
        except BaseException:
            raise ApiError(400, "BadRequest", "Invalid form data")

        try:
            payload = jwt.decode(
                signed_parameters, origin.secret, algorithms=JWT_ALGORITHMS
            )
            assert isinstance(payload, dict)
        except BaseException:
            raise ApiError(401, "AccessDenied", "Invalid JWT")

        # TODO: Fix meeting_id everywhere and also revert all the callbacks in metadata?
        if "meeting_id" in payload:
            payload["meeting_id"] = utils.remove_scope(payload["meeting_id"])

        # Forward callbacks to front-ends
        for callback in callbacks:
            asyncio.create_task(ctx.bbb.fire_signed_callback(callback, payload))

    else:
        raise ApiError(400, "BadRequest", "Unknown callback format")

    assert payload

    return Response("OK", 200)


##
### Recording Upload
##


@api("v1/recording/upload", methods=["POST"], name="bbblb:upload")
async def handle_recording_upload(ctx: BBBLBApiRequest):
    auth = await ctx.auth()
    auth.ensure_scope("rec:upload", SERVER_SCOPE)

    ctype = ctx.request.headers.get("content-type")
    if ctype != "application/x-tar":
        return JSONResponse(
            {
                "error": "Unsupported Media Type",
                "message": f"Expected application/x-tar, got {ctype}",
            },
            status_code=415,
            headers={"Accept-Post": "application/x-tar"},
        )

    force_tenant = ctx.request.query_params.get("tenant")

    try:
        importer = ctx.services.get(RecordingManager)
        task = await importer.start_import(
            ctx.request.stream(), force_tenant=force_tenant
        )
        return JSONResponse(
            {"message": "Import accepted", "importId": task.import_id}, status_code=202
        )
    except BaseException as exc:
        LOG.exception("Import failed")
        return JSONResponse(
            {"error": "Import failed", "message": str(exc)}, status_code=500
        )


##
### Protected Recordings
##


@api(
    "v1/recording/ticket/{ticket_uuid}/{original_path:path}",
    methods=["GET"],
    name="bbblb:ticket",
)
async def handle_protected_recording_link(ctx: BBBLBApiRequest):
    """A recording link that can only be used by a single user.

    When visited for the first time (ticket not consumed) the ticket is
    consumed, the user gets a signed cookie and is then redirected. If
    visited a second time (ticket already consumed) the user either
    needs a valid cookie or is rejected.
    """
    if not ctx.config.PROTECTED_RECORDINGS:
        return Response("Invalid recording link", 404)

    ticket_uuid = ctx.request.path_params["ticket_uuid"]
    try:
        ticket_id = uuid.UUID(ticket_uuid)
    except ValueError:
        return Response("Invalid recording link", 404)

    ticket = await ctx.session.get(model.ViewTicket, ticket_id)
    if not ticket or ticket.is_expired():
        return Response("This recording link is expired", 403)

    original_path = ctx.request.path_params["original_path"]
    target = ctx.request.url.replace(scheme="https", path=original_path)

    if not ticket.recording.protected:
        return RedirectResponse(target)

    record_id = ticket.recording.record_id
    cookie_key = playback.PRT_COOKIE_PREFIX + record_id
    cookie = ctx.request.cookies.get(cookie_key)

    if cookie and playback.verify_prt_cookie(cookie, record_id, ctx.config):
        return RedirectResponse(target)

    if not ticket.consumed and await ticket.consume(ctx.session, commit=True):
        rs = RedirectResponse(target)
        rs.set_cookie(
            cookie_key,
            playback.sign_prt_cookie(record_id, ticket.expire, ctx.config),
            path="/",
            max_age=ctx.config.PROTECTED_RECORDINGS_TIMEOUT * 60,
        )
        return rs

    return Response("This recording link is expired", 403)


@api("v1/recording/auth/{original_path:path}", methods=["GET"])
async def handle_protected_recording_auth(ctx: BBBLBApiRequest):
    """Auth API used by front-end webservers or CDNs to validate user
    requests for recording data."""
    if not ctx.config.PROTECTED_RECORDINGS:
        return Response(status_code=204)

    # Get record_id ouf of the requested path
    path = ctx.request.path_params["original_path"].lstrip("/")
    if path.startswith("playback/"):
        path = path[9:]
    if match := playback.split_media_path(path):
        format_name, record_id, resource = match
    else:
        return playback.PlaybackMediaApp.response_bad_path(path)

    # Allow access to unprotected assets (js, css, fonts, ...)
    if playback.is_unprotected_asset(format_name, resource):
        return Response(status_code=204)

    # Fetch cookie and load recording
    cookie_key = f"bbblb_prt_{record_id}"
    cookie = ctx.request.cookies.get(cookie_key, "")
    recording = await ctx.session.scalar(model.Recording.select(record_id=record_id))

    # Reject requests for missing or unpublished recordings
    if not recording or recording.state is not model.RecordingState.PUBLISHED:
        return playback.PlaybackMediaApp.response_missing(record_id)

    # Require a valid cookie for protected recordings
    if not recording.protected or playback.verify_prt_cookie(
        cookie, record_id, ctx.config
    ):
        return Response(status_code=204)

    return playback.PlaybackMediaApp.response_reject()


##
### REST API
##


@api("v1/tenant", methods=["GET"])
async def handle_tenants_list(ctx: BBBLBApiRequest):
    auth = await ctx.auth()
    auth.ensure_scope("tenant:list")

    stmt = model.Tenant.select().order_by(model.Tenant.name)
    tenants = (await ctx.session.execute(stmt)).scalars()
    return {
        "tenants": [
            {"name": t.name, "realm": t.realm, "secret": t.secret} for t in tenants
        ]
    }


@api("v1/tenant/{name}", methods=["POST"])
async def handle_tenant_post(ctx: BBBLBApiRequest):
    auth = await ctx.auth()
    tenant_name = ctx.request.path_params["name"]
    body = await ctx.request.json()

    async with ctx.session.begin():
        stmt = model.Tenant.select(name=tenant_name)
        tenant = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if not tenant:
            auth.ensure_scope("tenant:create")
            tenant = model.Tenant(name=tenant_name)
        else:
            auth.ensure_scope("tenant:update")

        try:
            tenant.realm = body["realm"]
            tenant.secret = body["secret"]
        except KeyError as e:
            raise ApiError(
                400,
                "Missing parameter",
                f"Missing parameter in request body: {e.args[0]}",
            )

        ctx.session.add(tenant)


@api("v1/tenant/{name}/delete", methods=["POST"])
async def handle_tenant_delete(ctx: BBBLBApiRequest):
    auth = await ctx.auth()
    tenant_name = ctx.request.path_params["name"]
    auth.ensure_scope("tenant:delete")

    if auth.tenant and auth.tenant != tenant_name:
        raise ApiError(401, "Access denied", "This API is protected")

    async with ctx.session.begin():
        stmt = model.Tenant.select(name=tenant_name)
        tenant = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if tenant:
            await ctx.session.delete(tenant)


@api("v1/server", methods=["GET"])
async def handle_server_list(ctx: BBBLBApiRequest):
    auth = await ctx.auth()
    auth.ensure_scope("server:list")

    stmt = model.Server.select().order_by(model.Server.domain)
    servers = (await ctx.session.execute(stmt)).scalars()
    return {"servers": [{"domain": s.domain, "secret": s.secret} for s in servers]}


@api("v1/server/{domain}", methods=["POST"])
async def handle_server_post(ctx: BBBLBApiRequest):
    auth = await ctx.auth()
    domain = ctx.request.path_params["domain"]
    body = await ctx.request.json()

    async with ctx.session.begin():
        stmt = model.Server.select(domain=domain)
        server = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if not server:
            auth.ensure_scope("server:create")
            server = model.Server(domain=domain)
        else:
            auth.ensure_scope("server:update")

        try:
            server.secret = body["secret"]
        except KeyError as e:
            raise ApiError(
                400,
                "Missing parameter",
                f"Missing parameter in request body: {e.args[0]}",
            )

        ctx.session.add(server)


async def handle_server_switch(ctx: BBBLBApiRequest, enable: bool):
    auth = await ctx.auth()
    domain = ctx.request.path_params["domain"]
    auth.ensure_scope("server:state")

    async with ctx.session.begin():
        stmt = model.Server.select(domain=domain)
        server = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if not server:
            raise ApiError(404, "Unknown server", f"Server not known: {domain}")
        server.enabled = enable


@api("v1/server/{name}/enable", methods=["POST"])
async def handle_server_enable(ctx: BBBLBApiRequest, enable=True):
    return await handle_server_switch(ctx, True)


@api("v1/server/{name}/disable", methods=["POST"])
async def handle_server_disable(ctx: BBBLBApiRequest):
    return await handle_server_switch(ctx, False)
