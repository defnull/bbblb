from contextlib import asynccontextmanager
from functools import partial
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
        return cast(BBBLBConfig, self.request.app.state.config)

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


# Playback formats for which we know that they sometimes expect their files
# in /{format}/* instead of the default /playback/{format}/* path.
PLAYBACK_FROM_ROOT_FORMATS = ("presentation", "video")


async def format_redirect_app(format, scope, receive, send):
    assert scope["type"] == "http"
    path = scope["path"].lstrip("/")
    response = RedirectResponse(url=f"/playback/{format}/{path}")
    await response(scope, receive, send)


def redirect(src, dst):
    async def handler(request):
        return RedirectResponse(url=dst)

    return Route(src, endpoint=handler)


def make_routes(config: BBBLBConfig):
    from bbblb.web import bbbapi, bbblbapi

    playback_dir = config.PATH_DATA / "recordings" / "public"
    playback_dir.mkdir(parents=True, exist_ok=True)
    static_dir = config.PATH_DATA / "htdocs"
    static_dir.mkdir(parents=True, exist_ok=True)

    return [
        Mount("/bigbluebutton/api", routes=bbbapi.api_routes),
        Mount("/bbblb/api", routes=bbblbapi.api_routes),
        # Serve /playback/* files in case the reverse proxy in front if BBBLB does not.
        Mount(
            "/playback",
            app=StaticFiles(
                directory=playback_dir,
                check_dir=False,
                follow_symlink=True,
            ),
            name="bbb:playback",
        ),
        # Redirect misguided playback file requests to the real path. We send
        # redirects instead of real files in case a reverse proxy in front if BBBLB
        # serves /playback/* for us more efficiently.
        *[
            Mount(f"/{format}", app=partial(format_redirect_app, format))
            for format in PLAYBACK_FROM_ROOT_FORMATS
        ],
        # Redirect non-slash requests to prefix mounts, because automatic slash handling
        # breaks if there are other routes matching the non-slash request :/
        redirect("/bigbluebutton/api", "/bigbluebutton/api/"),
        redirect("/bbblb/api", "/bbblb/api/"),
        # Serve static files from the {PATH_DATA}/htdocs/ folder, or fall back to
        # files shipped with BBBLB. This is just for convenience, BBBLB itself
        # does not need any static files.
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


def make_app(config: BBBLBConfig | None = None, autostart=True):
    if not config:
        config = BBBLBConfig()
        config.populate()

    @asynccontextmanager
    async def lifespan(app: Starlette):
        services = await bbblb.services.bootstrap(config, autostart=autostart)
        async with services:
            app.state.config = config
            app.state.services = services
            yield

    return Starlette(debug=config.DEBUG, routes=make_routes(config), lifespan=lifespan)
