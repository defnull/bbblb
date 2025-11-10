import asyncio
import functools
import hashlib
import hmac
from urllib.parse import parse_qs
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

    def has_scope(self, *scopes):
        return any(scope in self.scopes for scope in scopes)

    def ensure_scope(self, *scopes):
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
    async def from_request(cls, request: Request) -> "AuthContext":
        auth = request.headers.get("Authorization")
        if not auth:
            raise ApiError(403, "Authentication required", "This API is protected")

        try:
            scheme, credentials = auth.split()
            if scheme.lower() != "bearer":
                raise ApiError(403, "Authentication required", "This API is protected")

            header = jwt.get_unverified_header(credentials)
            kid = header.get("kid")
            if kid and kid.startswith("bbb:"):
                server = await model.Server.find(domain=kid[4:])
                if not server:
                    raise ApiError(401, "Access denied", "This API is protected")
                payload = jwt.decode(credentials, server.secret, algorithms=["HS256"])
                payload["scope"] = SERVER_SCOPE
                payload["sub"] = server.domain
                return AuthContext(payload, server=server)
            elif kid and kid.startswith("tenant:"):
                tenant = await model.Tenant.find(name=kid[7:])
                if not tenant:
                    raise ApiError(401, "Access denied", "This API is protected")
                payload = jwt.decode(credentials, tenant.secret, algorithms=["HS256"])
                payload["scope"] = TENANT_SCOPE
                payload["sub"] = tenant.name
                return AuthContext(payload, tenant=tenant)
            else:
                payload = jwt.decode(credentials, config.SECRET, algorithms=["HS256"])
                return AuthContext(payload)

        except BaseException:
            raise ApiError(401, "Access denied", "This API is protected")


##
### Callback handling
##


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
                bbblib.trigger_callback(
                    "GET", callback.forward, params=request.query_params
                )
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
        asyncio.create_task(bbblib.fire_callback(callback, payload, clear=True))

    return Response("OK", 200)


##
### Recording Upload
##


@api("v1/recording/upload", methods=["POST"], name="bbblb:upload")
async def handle_recording_upload(request: Request):
    auth = await AuthContext.from_request(request)
    auth.ensure_scope("rec:upload", SERVER_SCOPE)

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


@api("v1/tenant", methods=["GET"])
async def handle_tenants_list(request: Request):
    auth = await AuthContext.from_request(request)
    auth.ensure_scope("tenant:list")

    async with model.AsyncSessionMaker() as session:
        stmt = model.Tenant.select().order_by(model.Tenant.name)
        tenants = (await session.execute(stmt)).scalars()
        return {
            "tenants": [
                {"name": t.name, "realm": t.realm, "secret": t.secret} for t in tenants
            ]
        }


@api("v1/tenant/{name}", methods=["POST"])
async def handle_tenant_post(request: Request):
    auth = await AuthContext.from_request(request)
    tenant_name = request.path_params["name"]
    body = await request.json()

    async with model.AsyncSessionMaker() as session:
        stmt = model.Tenant.select(name=tenant_name)
        tenant = (await session.execute(stmt)).scalar_one_or_none()
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

        session.add(tenant)
        await session.commit()


@api("v1/tenant/{name}/delete", methods=["POST"])
async def handle_tenant_delete(request: Request):
    auth = await AuthContext.from_request(request)
    tenant_name = request.path_params["name"]
    auth.ensure_scope("tenant:delete")

    if auth.tenant and auth.tenant != tenant_name:
        raise ApiError(401, "Access denied", "This API is protected")

    async with model.AsyncSessionMaker() as session:
        stmt = model.Tenant.select(name=tenant_name)
        tenant = (await session.execute(stmt)).scalar_one_or_none()
        if tenant:
            await session.delete(tenant)
            await session.commit()


@api("v1/server", methods=["GET"])
async def handle_server_list(request: Request):
    auth = await AuthContext.from_request(request)
    auth.ensure_scope("server:list")

    async with model.AsyncSessionMaker() as session:
        stmt = model.Server.select().order_by(model.Server.domain)
        servers = (await session.execute(stmt)).scalars()
        return {"servers": [{"domain": s.domain, "secret": s.secret} for s in servers]}


@api("v1/server/{domain}", methods=["POST"])
async def handle_server_post(request: Request):
    auth = await AuthContext.from_request(request)
    domain = request.path_params["domain"]
    body = await request.json()

    async with model.AsyncSessionMaker() as session:
        stmt = model.Server.select(domain=domain)
        server = (await session.execute(stmt)).scalar_one_or_none()
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

        session.add(server)
        await session.commit()


@api("v1/server/{name}/enable", methods=["POST"])
async def handle_server_enable(request: Request, enable=True):
    auth = await AuthContext.from_request(request)
    domain = request.path_params["domain"]
    auth.ensure_scope("server:state")

    async with model.AsyncSessionMaker() as session:
        stmt = model.Server.select(domain=domain)
        server = (await session.execute(stmt)).scalar_one_or_none()
        if not server:
            raise ApiError(404, "Unknown server", f"Server not known: {domain}")
        server.enabled = True
        await session.commit()


@api("v1/server/{name}/disable", methods=["POST"])
async def handle_server_disable(request: Request):
    return await handle_server_enable(request, enable=False)
