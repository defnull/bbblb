# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import functools
import json
import logging
import secrets
import time
from urllib.parse import parse_qs, parse_qsl, urlencode

import jinja2
import sqlalchemy as sa
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

import bbblb
from bbblb import model
from bbblb.lib.ratelimit import network_key
from bbblb.services.ratelimit import LoginRateLimiter
from bbblb.services.tenants import TenantCache
from bbblb.utils import hmac_sign, hmac_verify
from bbblb.web import ApiRequestContext, RequestBodyTooLarge

LOG = logging.getLogger(__name__)
CSRF_COOKIE = "bbblb_csrf"
SESSION_COOKIE = "bbblb_sx"
SESSION_TTL = 8 * 60 * 60
COOKIE_OPTS = {
    "path": "/bbblb/admin/",
    "secure": True,
    "httponly": True,
    "samesite": "strict",
}


class AdminRequest(ApiRequestContext):
    def __init__(self, request: Request):
        super().__init__(request)
        self.is_admin = False
        self.tenant: model.Tenant | None = None
        self._csrf_token: str | None = None
        self._csrf_new = False
        self._csrf_rotated = False
        self._logged_out = False
        self._session_invalid = False

    async def try_login(self, username: str, password: str) -> bool:
        if not username or not password:
            return False

        if not self.config.ADMIN_ENABLED or not self.config.ADMIN_PASSWORD:
            return False

        if username == "admin" and secrets.compare_digest(
            password, self.config.ADMIN_PASSWORD
        ):
            self.is_admin = True
            return True

        if not self.config.ADMIN_ALLOW_TENANTS:
            return False

        tenant = await (await self.services.use(TenantCache)).get_tenant_by_name(
            username
        )
        if tenant and secrets.compare_digest(password, tenant.secret):
            self.tenant = tenant
            return True

        return False

    def logout(self):
        self.is_admin = False
        self.tenant = None
        self._logged_out = True

    async def continue_session(self):
        if not self.config.ADMIN_ENABLED:
            self._session_invalid = True
            return False
        if not (cookie := self.request.cookies.get(SESSION_COOKIE)):
            return False
        if not (claims := self._session_check(cookie)):
            self._session_invalid = True
            return False
        if claims["role"] == "admin":
            self.is_admin = True
            return True
        elif claims["role"] == "tenant" and self.config.ADMIN_ALLOW_TENANTS:
            tenant = await (await self.services.use(TenantCache)).get_tenant_by_name(
                claims["tenant"]
            )
            if tenant and tenant.enabled:
                self.tenant = tenant
                return True
        self._session_invalid = True
        return False

    def _session_sign(self, role, tenant=None):
        claims = {"ts": str(int(time.time())), "role": role}
        if tenant:
            claims["tenant"] = tenant
        return hmac_sign(urlencode(claims), self.config.SECRET, "admin")

    def _session_check(self, cookie):
        payload = hmac_verify(cookie, self.config.SECRET, "admin")
        if payload:
            claims = dict(parse_qsl(payload, strict_parsing=True))
            age = time.time() - int(claims.pop("ts"))
            if 0 < age < SESSION_TTL:
                return claims

    def get_csrf(self) -> str:
        if self._csrf_token is None:
            token = self.request.cookies.get(CSRF_COOKIE)
            if token:
                self._csrf_token = token
            else:
                self._csrf_token = secrets.token_urlsafe(32)
                self._csrf_new = True
        return self._csrf_token

    @property
    def is_htmx(self) -> bool:
        return self.request.headers.get("HX-Request") == "true"

    def on_response(self, response: Response):
        """Prevent caching admin responses and persist session/CSRF state."""
        response.headers["Cache-Control"] = "private, no-store, no-cache, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

        if self.is_admin:
            response.set_cookie(
                SESSION_COOKIE, self._session_sign("admin"), **COOKIE_OPTS
            )
        elif self.tenant:
            response.set_cookie(
                SESSION_COOKIE,
                self._session_sign("tenant", self.tenant.name),
                **COOKIE_OPTS,
            )
        elif (self._logged_out or self._session_invalid) and (
            SESSION_COOKIE in self.request.cookies
        ):
            response.delete_cookie(SESSION_COOKIE, **COOKIE_OPTS)

        if self._logged_out:
            response.delete_cookie(CSRF_COOKIE, **COOKIE_OPTS)
        elif self._csrf_token is not None and (self._csrf_new or self._csrf_rotated):
            response.set_cookie(CSRF_COOKIE, self._csrf_token, **COOKIE_OPTS)

        if (
            self.is_htmx
            and self._csrf_rotated
            and self._csrf_token
            and not self._logged_out
        ):
            response.headers["HX-Trigger"] = json.dumps(
                {"csrfRotated": self._csrf_token}
            )

    async def get_form(self) -> dict[str, str]:
        """Read a bounded form and require a valid double-submit CSRF token."""
        try:
            form = parse_qs(
                (await self.read_body()).decode("utf-8"),
                strict_parsing=True,
                keep_blank_values=True,
            )
        except RequestBodyTooLarge:
            raise AdminError(413, "Request body too large")
        except (UnicodeDecodeError, ValueError):
            raise AdminError(400, "Invalid form data")

        csrf_values = form.get("csrf", [])
        token = csrf_values[0] if len(csrf_values) == 1 else ""
        if not token:
            token = self.request.headers.get("X-CSRF-Token", "")
        cookie = self.request.cookies.get(CSRF_COOKIE, "")
        if not token or not cookie or not secrets.compare_digest(token, cookie):
            raise AdminError(403, "Invalid or expired form token")

        self._csrf_token = secrets.token_urlsafe(32)
        self._csrf_rotated = True
        return {key: values[-1] for key, values in form.items()}

    TPL = jinja2.Environment(
        loader=jinja2.PackageLoader("bbblb.web", "templates"),
        autoescape=jinja2.select_autoescape(),
    )

    def render(self, tpl_name, **args):
        args["ctx"] = self
        if self.is_admin:
            args["version"] = bbblb.__version__
        args["license"] = bbblb.LICENSE
        args["source_url"] = bbblb.SOURCE_URL

        return HTMLResponse(self.TPL.get_template(tpl_name).render(**args))


class AdminError(Exception):
    def __init__(self, status_code: int, message: str, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.headers = headers or {}

    def to_response(self) -> Response:
        return Response(
            self.message,
            status_code=self.status_code,
            headers=self.headers,
            media_type="text/plain",
        )


admin_routes: list[Route] = []


def route(
    route: str,
    methods=("GET",),
    name: str | None = None,
    authenticate: bool = True,
):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            try:
                async with AdminRequest(request) as ctx:
                    if not ctx.config.ADMIN_ENABLED:
                        return Response(status_code=404)

                    await ctx.continue_session()
                    if authenticate and not (ctx.is_admin or ctx.tenant):
                        if ctx.is_htmx:
                            response = Response(status_code=204)
                            response.headers["HX-Redirect"] = "/bbblb/admin/login"
                        else:
                            response = RedirectResponse(
                                "/bbblb/admin/login", status_code=303
                            )
                        ctx.on_response(response)
                        return response

                    try:
                        response = await func(ctx)
                    except AdminError as exc:
                        response = exc.to_response()
                    ctx.on_response(response)
                    return response
            except Exception:
                LOG.exception("Unhandled admin exception")
                return Response("Internal server error", status_code=500)

        admin_routes.append(
            Route("/" + route, wrapper, methods=list(methods), name=name)
        )
        return wrapper

    return decorator


@route("login", methods=("GET", "POST"), name="admin:login", authenticate=False)
async def handle_login(ctx: AdminRequest):
    if ctx.is_admin or ctx.tenant:
        return RedirectResponse("/bbblb/admin/cluster", status_code=303)

    username = ""
    if ctx.request.method == "POST":
        form = await ctx.get_form()
        username = form.get("username", "")
        password = form.get("password", "")

        breaker = await ctx.services.use(LoginRateLimiter)
        client_host = ctx.request.client.host if ctx.request.client else None
        key = network_key(client_host)
        if retry_after := breaker.retry_after(key):
            LOG.warning(f"Login rate limit exceeded for {client_host} ({key})")
            raise AdminError(
                429,
                "Too many login attempts",
                {"Retry-After": str(retry_after)},
            )

        if await ctx.try_login(username, password):
            breaker.clear(key)
            return RedirectResponse("/bbblb/admin/cluster", status_code=303)
        else:
            breaker.record_failure(key)
            return ctx.render("login.html", username=username)
    return ctx.render("login.html")


@route("logout", methods=("POST",), name="admin:logout")
async def handle_logout(ctx: AdminRequest):
    await ctx.get_form()  # To ensure CSRF
    ctx.logout()
    return RedirectResponse("/bbblb/admin/login", status_code=303)


@route("cluster", name="admin:cluster")
async def handle_cluster(ctx: AdminRequest):
    servers = (
        (await ctx.session.execute(model.Server.select().order_by(model.Server.domain)))
        .scalars()
        .all()
    )
    return ctx.render("cluster.html", servers=servers)


async def _tenant_stats(session, tenant_ids):
    tenant_ids = list(tenant_ids)
    tenant_stats = {
        tenant_id: {"meetings": 0, "recordings": 0, "recording_hours": 0.0}
        for tenant_id in tenant_ids
    }
    if not tenant_ids:
        return tenant_stats

    for tenant_id, mcount in await session.execute(
        model.select(model.Meeting.tenant_fk, sa.func.count(model.Meeting.id))
        .where(
            model.Meeting.internal_id.is_not(None),
            model.Meeting.tenant_fk.in_(tenant_ids),
        )
        .group_by(model.Meeting.tenant_fk)
    ):
        tenant_stats[tenant_id]["meetings"] = mcount

    for tenant_id, started, ended in await session.execute(
        model.select(
            model.Recording.tenant_fk,
            model.Recording.started,
            model.Recording.ended,
        ).where(model.Recording.tenant_fk.in_(tenant_ids))
    ):
        tenant_stats[tenant_id]["recordings"] += 1
        tenant_stats[tenant_id]["recording_hours"] += max(
            0.0, (ended - started).total_seconds() / 3600
        )

    return tenant_stats


@route("tenants", name="admin:tenants")
async def handle_tenants(ctx: AdminRequest):
    if not ctx.is_admin:
        raise AdminError(403, "Global admin credentials required")

    tenants = (
        (await ctx.session.execute(model.Tenant.select().order_by(model.Tenant.name)))
        .scalars()
        .all()
    )
    tenant_stats = await _tenant_stats(ctx.session, [tenant.id for tenant in tenants])
    return ctx.render("tenants.html", tenants=tenants, tenant_stats=tenant_stats)


def _meetings_filter_query(filter_domain, filter_tenant):
    return urlencode(
        {
            name: value
            for name, value in (
                ("server", filter_domain),
                ("tenant", filter_tenant),
            )
            if value
        }
    )


@route("meetings", name="admin:meetings")
async def handle_meetings(ctx: AdminRequest):
    filter_domain = ctx.request.query_params.get("server")
    filter_tenant = ctx.request.query_params.get("tenant")
    async with ctx.session as session:
        stmt = model.Meeting.select(model.Meeting.internal_id.is_not(None))

        if ctx.tenant is not None:
            # Tenant credentials are always scoped to their authenticated
            # tenant, regardless of any tenant filter supplied in the URL.
            stmt = stmt.where(model.Meeting.tenant_fk == ctx.tenant.id)
            filter_tenant = ctx.tenant.name
        elif filter_tenant:
            tenant = await model.Tenant.find(session, name=filter_tenant)
            stmt = stmt.where(
                model.Meeting.tenant_fk == tenant.id if tenant else sa.false()
            )

        if filter_domain:
            server = (
                await session.execute(model.Server.select(domain=filter_domain))
            ).scalar_one_or_none()
            stmt = stmt.where(
                model.Meeting.server_fk == server.id if server else sa.false()
            )

        stmt = stmt.order_by(model.Meeting.created.desc())
        meetings = (await session.execute(stmt)).scalars().all()
    return ctx.render(
        "meetings.html",
        meetings=meetings,
        filter_domain=filter_domain,
        filter_tenant=filter_tenant,
        filter_query=_meetings_filter_query(filter_domain, filter_tenant),
    )


@route("meeting/end", methods=("POST",), name="admin:meeting_end")
async def handle_meeting_end(ctx: AdminRequest):
    form = await ctx.get_form()
    meeting_id = form.get("meeting_id", "")
    if not meeting_id.isdigit():
        raise AdminError(400, "Invalid meeting")

    async with ctx.session as session:
        stmt = model.Meeting.select(
            model.Meeting.id == int(meeting_id),
            model.Meeting.internal_id.is_not(None),
        )
        if ctx.tenant is not None:
            stmt = stmt.where(model.Meeting.tenant_fk == ctx.tenant.id)
        meeting = (await session.execute(stmt)).scalar_one_or_none()
        if meeting is None:
            raise AdminError(404, "Meeting not found")

    result = await ctx.bbb.end_meeting(meeting)
    if not result.success:
        raise AdminError(502, f"Failed to end meeting: {result.message}")

    if ctx.is_htmx:
        return Response(status_code=200)

    query = _meetings_filter_query(form.get("server"), form.get("tenant"))
    target = "/bbblb/admin/meetings"
    if query:
        target += "?" + query
    return RedirectResponse(target, status_code=303)


@route("server", methods=("POST",), name="admin:server_update")
async def handle_server_update(ctx: AdminRequest):
    if not ctx.is_admin:
        raise AdminError(403, "Global admin credentials required")

    form = await ctx.get_form()
    action = form.get("action")
    if action not in {"enable", "disable"}:
        raise AdminError(400, "Invalid server action")

    domain = form.get("domain", "")
    if not domain:
        raise AdminError(400, "Invalid server domain")

    async with ctx.session as session:
        server = (
            await session.execute(model.Server.select(domain=domain))
        ).scalar_one_or_none()
        if server is None:
            raise AdminError(404, "Server not found")
        server.enabled = action == "enable"
        await session.commit()
        if ctx.is_htmx:
            return ctx.render("_server_row.html", server=server)

    return RedirectResponse("/bbblb/admin/cluster", status_code=303)


@route("tenant", methods=("POST",), name="admin:tenant_update")
async def handle_tenant_update(ctx: AdminRequest):
    if not ctx.is_admin:
        raise AdminError(403, "Global admin credentials required")

    form = await ctx.get_form()
    action = form.get("action")
    if action not in {"enable", "disable"}:
        raise AdminError(400, "Invalid tenant action")

    name = form.get("name", "")
    if not name:
        raise AdminError(400, "Invalid tenant name")

    async with ctx.session as session:
        tenant = (
            await session.execute(model.Tenant.select(name=name))
        ).scalar_one_or_none()
        if tenant is None:
            raise AdminError(404, "Tenant not found")
        tenant.enabled = action == "enable"
        await session.commit()
        if ctx.is_htmx:
            tenant_stats = await _tenant_stats(session, [tenant.id])
            return ctx.render(
                "_tenant_row.html", tenant=tenant, tenant_stats=tenant_stats
            )

    return RedirectResponse("/bbblb/admin/tenants", status_code=303)
