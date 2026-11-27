# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

from contextlib import asynccontextmanager
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from functools import cached_property
from typing import cast
from starlette.requests import Request

from bbblb.services import ServiceRegistry
from bbblb.services.bbb import BBBHelper
from bbblb.services.db import DBContext
from bbblb.settings import BBBLBConfig

import bbblb.services


class RequestBodyTooLarge(Exception):
    """Raised when a streamed request body exceeds the configured limit."""


class ApiRequestContext:
    """A wrapper for requests that gives convenient access to importand
    BBBLB services."""

    def __init__(self, request: Request):
        self.request = request

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a, **ka):
        if "session" in self.__dict__:
            await self.session.close()

    @cached_property
    def services(self) -> ServiceRegistry:
        return cast(ServiceRegistry, self.request.app.state.services)

    @cached_property
    def config(self) -> BBBLBConfig:
        return self.services.get(BBBLBConfig)

    @cached_property
    def bbb(self) -> BBBHelper:
        return self.services.get(BBBHelper)

    @cached_property
    def db(self) -> DBContext:
        return self.services.get(DBContext)

    @cached_property
    def session(self):
        """A request specific AsyncSession object.

        The session is closed at the end of the request. It can also be
        used in an async-with statement to ensure the session is reset at
        the end of a code secion, or you can call reset() explicitly to
        end any transactions and free any DB handles mid-request.
        """
        return self.db.session()

    async def read_body(self) -> bytes:
        """Read and cache the request body, enforcing the configured size limit."""
        cached: bytes | None = getattr(self, "_body", None)
        if cached is not None:
            return cached

        body = bytearray()
        async for chunk in self.request.stream():
            body.extend(chunk)
            if len(body) > self.config.MAX_BODY:
                raise RequestBodyTooLarge

        self._body = bytes(body)
        return self._body


def redirect(src, dst):
    async def handler(request):
        return RedirectResponse(url=dst)

    return Route(src, endpoint=handler)


def redirect_prefix(prefix):
    async def redirect_app(scope, receive, send):
        query = scope.get("query_string", b"").decode("latin-1")
        url = f"{prefix}{scope['path']}"
        if query:
            url = f"{url}?{query}"
        response = RedirectResponse(url=url)
        await response(scope, receive, send)

    return redirect_app


async def collect_routes(sr: ServiceRegistry):
    from bbblb.web import bbbapi, bbblbapi, playback, admin

    config = await sr.use(BBBLBConfig)

    # Mount API routes first, because they are triggered most often.
    # Note: We explicitly redirect non-slash requests to prefix mounts, because automatic
    # slash handling breaks if there are other routes matching the non-slash request :/
    routes = [
        Mount("/bigbluebutton/api", routes=bbbapi.api_routes),
        redirect("/bigbluebutton/api", "/bigbluebutton/api/"),
        Mount("/bbblb/api", routes=bbblbapi.api_routes),
        redirect("/bbblb/api", "/bbblb/api/"),
    ]

    # Playback player and media routes as well as a bunch of redirects
    routes += [
        Mount(
            "/playback/presentation/2.3/{record_id}",
            app=playback.PlaybackPlayerApp(config),
            name="bbb:playback:player",
        ),
        Mount(
            "/playback",
            app=playback.PlaybackMediaApp(config, await sr.use(DBContext)),
            name="bbb:playback:media",
        ),
        # Some playback formats think they are served from /{format}/* instead of
        # their proper /playback/{format}/* path, so we have to add a couple of
        # redirects here. We redirect instead of serving actual files in case a reverse
        # proxy in front of BBBLB serves /playback/* files for us.
        *[
            Mount(f"/{format}", app=redirect_prefix("/playback"))
            for format in ("presentation", "video")
        ],
    ]

    # Optional admin web UI routes
    if config.ADMIN_ENABLED:
        routes += [
            redirect("/bbblb/admin", "/bbblb/admin/"),
            redirect("/bbblb/admin/", "/bbblb/admin/cluster"),
            Mount("/bbblb/admin", routes=admin.admin_routes, name="admin"),
        ]

    # Serve static files from the {PATH_DATA}/htdocs/ folder and fall back to files
    # shipped with BBBLB for missing files.
    static_dir = config.PATH_DATA / "htdocs"
    static_dir.mkdir(parents=True, exist_ok=True)
    routes += [
        Mount(
            "/",
            app=StaticFiles(
                directory=static_dir,
                packages=[(f"{__package__}", "static")],
                follow_symlink=True,
                html=True,
            ),
            name="static",
        ),
    ]

    return routes


def make_app(config: BBBLBConfig | None = None, autostart=True):
    if not config:
        config = BBBLBConfig()
        config.populate()

    @asynccontextmanager
    async def lifespan(app: Starlette):
        services = await bbblb.services.bootstrap(config)
        async with services:
            if autostart:
                await services.start_all()
            app.router.routes.extend(await collect_routes(services))
            app.state.services = services

            yield

    return Starlette(debug=config.DEBUG, lifespan=lifespan)
