from contextlib import asynccontextmanager
from functools import partial
import pathlib
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.responses import RedirectResponse

from bbblb.api import bbbapi
from bbblb.api import bbblbapi
from bbblb import model
from bbblb import poller
from bbblb import recordings
from bbblb import bbblib
from bbblb.settings import config


@asynccontextmanager
async def lifespan(app: Starlette):
    await model.init_engine(config.DB_URI, echo=False)
    poll_worker = poller.Poller()
    importer = recordings.RecordingImporter(
        basedir=pathlib.Path(config.RECORDING_PATH),
        concurrency=config.RECORDING_THREADS,
    )

    try:
        async with poll_worker, importer:
            app.state.poll_worker = poll_worker
            app.state.importer = importer
            yield
    finally:
        await bbblib.close_pool()
        await model.dispose_engine()


# Playback formats for which we know that they sometimes expect their files
# in /{format}/* instead of the default /playback/{format}/* path.
PLAYBACK_FROM_ROOT_FORMATS = ("presentation", "video")


async def format_redirect_app(format, scope, receive, send):
    assert scope["type"] == "http"
    path = scope["path"].lstrip("/")
    response = RedirectResponse(url=f"/playback/{format}/{path}")
    await response(scope, receive, send)


def make_playback_routes():
    return [
        # Serve /playback/* files in case the reverse proxy in front if BBBLB does not.
        Mount(
            "/playback",
            app=StaticFiles(
                directory=config.RECORDING_PATH / "public",
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
    ]


def make_routes():
    return [
        Mount("/bigbluebutton/api", routes=bbbapi.api_routes),
        Mount("/api", routes=bbblbapi.api_routes),
        *make_playback_routes(),
    ]


def make_app():
    config.populate()
    return Starlette(debug=True, routes=make_routes(), lifespan=lifespan)
