import asyncio
from contextlib import asynccontextmanager
import pathlib
from starlette.applications import Starlette
from starlette.routing import Mount

from bbblb.api import bbbapi
from bbblb.api import bbblbapi
from bbblb import model
from bbblb import poller
from bbblb import recordings
from bbblb.settings import config


async def _run_dev_hooks(name, *a, **ka):
    if not config.DEBUG:
        return

    try:
        import testhook

        await getattr(testhook, name)(*a, *ka)
    except (ImportError, AttributeError):
        pass


@asynccontextmanager
async def lifespan(app: Starlette):
    await asyncio.to_thread(config.populate)
    await model.init_engine(config.DB_URI, echo=config.DEBUG)
    poll_worker = poller.Poller()
    importer = recordings.RecordingImporter(
        basedir=pathlib.Path(config.RECORDING_PATH),
        concurrency=config.RECORDING_THREADS,
    )

    await _run_dev_hooks("startup")

    try:
        async with poll_worker, importer:
            app.state.poll_worker = poll_worker
            app.state.importer = importer
            yield
    finally:
        await _run_dev_hooks("shutdown")
        await model.dispose_engine()


routes = [
    Mount("/bigbluebutton/api", routes=bbbapi.api_routes),
    Mount("/api", routes=bbblbapi.api_routes),
]

app = Starlette(debug=True, routes=routes, lifespan=lifespan)
