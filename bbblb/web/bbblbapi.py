import asyncio
import functools
import hashlib
import hmac
from urllib.parse import parse_qs
import logging
import jwt

from bbblb.web import bbbapi
from bbblb import model

from starlette.requests import Request
from starlette.routing import Route
from starlette.responses import Response, JSONResponse

from bbblb.web import ApiRequestContext
from bbblb.services.recording import RecordingManager

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


@api("v1/callback/{uuid}/end/{sig}", name="bbblb:callback_end")
async def handle_callback_end(ctx: BBBLBApiRequest):
    """Handle the meetingEndedURL callback"""

    try:
        meeting_uuid = ctx.request.path_params["uuid"]
        callback_sig = ctx.request.path_params["sig"]
    except (KeyError, ValueError):
        LOG.warning("Callback called with missing or invalid parameters")
        return Response("Invalid callback URL", 400)

    # Verify callback signature
    sig = f"bbblb:callback:end:{meeting_uuid}".encode("ASCII")
    sig = hmac.digest(ctx.config.SECRET.encode("UTF8"), sig, hashlib.sha256)
    if not hmac.compare_digest(sig, bytes.fromhex(callback_sig)):
        LOG.warning("Callback signature mismatch")
        return Response("Access denied, signature check failed", 401)

    async with ctx.session.begin():
        # Check if we have to notify a frontend
        stmt = model.Callback.select(uuid=meeting_uuid, type=model.CALLBACK_TYPE_END)
        callback = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if callback:
            if callback.forward:
                # Fire and forget callback forward task
                asyncio.ensure_future(
                    ctx.bbb.trigger_callback(
                        "GET", callback.forward, params=ctx.request.query_params
                    )
                )
            await ctx.session.delete(callback)

        # Mark meeting as ended, if still present
        stmt = model.Meeting.select(uuid=meeting_uuid)
        meeting = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if meeting:
            LOG.info(f"Meeting ended (callback): {meeting}")
            await bbbapi.forget_meeting(ctx.session, meeting)

    return Response("OK", 200)


@api("v1/callback/{uuid}/{type}", name="bbblb:callback_proxy")
async def handle_callback_proxy(ctx: BBBLBApiRequest):
    try:
        meeting_uuid = ctx.request.path_params["uuid"]
        callback_type = ctx.request.path_params["type"]
    except (KeyError, ValueError):
        LOG.warning("Callback called with missing or invalid parameters")
        return Response("Invalid callback URL", 400)

    body = bytearray()
    async for chunk in ctx.request.stream():
        body.extend(chunk)
        if len(body) > ctx.config.MAX_BODY:
            return Response("Request Entity Too Large", 413)

    try:
        form = parse_qs(body.decode("UTF-8"))
        payload = form["signed_parameters"][0]
    except (UnicodeDecodeError, KeyError, IndexError):
        return Response("Invalid request", 400)

    stmt = model.Callback.select(uuid=meeting_uuid, type=callback_type)
    callbacks = (await ctx.session.execute(stmt)).scalars().all()
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
        asyncio.create_task(ctx.bbb.fire_callback(callback, payload, clear=True))

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
        importer = ctx.services.get("importer", RecordingManager)
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
