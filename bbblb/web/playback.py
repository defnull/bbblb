# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import datetime
import logging
import re
import time
from typing import Any, MutableMapping

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from bbblb import model, utils
from bbblb.services.db import DBContext
from bbblb.settings import BBBLBConfig

LOG = logging.getLogger(__name__)

##
### Shared helper functions for protected recordings
### used by PlaybackApp and bbblbapi.py
##

PRT_COOKIE_PREFIX = "bbblb_prt_"


def split_media_path(path: str) -> tuple[str, str, str] | None:
    """Split a {format}/{record_id}/{resource...} path into its three
    components, or return None for invalid paths (bad format,
    bad record_id). The resource part can be empty.
    """
    parts = path.strip("/").split("/", 2)
    while len(parts) < 3:
        parts.append("")
    format, record, resource = parts
    # Some basic sanity checks
    if not utils.RE_FORMAT_NAME.match(format):
        return None
    if not utils.RE_RECORD_ID.match(record):
        return None
    return format, record, resource


# Pattern for assets that do not need protection, one per format.
_UNPROTECTED = {
    "presentation": re.compile(r".*\.(js|css|html|woff)$"),
    "video": re.compile(r".*\.(js|css|html)$"),
}


def is_unprotected_asset(format_name: str, resource: str):
    """Return true if we can skip expensive checks for assets (css, js, fonts)
    that do not need protection."""
    if format_name not in _UNPROTECTED:
        return False

    return _UNPROTECTED[format_name].match(resource) is not None


def sign_prt_cookie(record_id: str, expire: datetime.datetime, conf: BBBLBConfig):
    return utils.hmac_sign(
        f"{int(expire.timestamp())}:{record_id}", "prc" + conf.SECRET
    )


def verify_prt_cookie(value: str, record_id: str, conf: BBBLBConfig):
    payload = utils.hmac_verify(value, "prc" + conf.SECRET)
    if not payload or ":" not in payload:
        return False
    expire, _, rid = payload.partition(":")
    if rid != record_id or not expire.isdecimal():
        return False
    if int(expire) < time.time():
        return False
    return True


def _make_redirect(format):
    async def redirect_app(scope, receive, send):
        assert scope["type"] == "http"
        path = scope["path"].lstrip("/")
        response = RedirectResponse(url=f"/playback/{format}/{path}")
        await response(scope, receive, send)

    return redirect_app


# Some playback formats think they are served from /{format}/* instead of
# their proper /playback/{format}/* path, so we have to add a couple of
# redirects.
PLAYBACK_FORMAT_REDIRECTS = [
    Mount(f"/{format}", app=_make_redirect(format))
    for format in ("presentation", "video")
]


class PlaybackPlayerApp(StaticFiles):
    """Serve the bbb-playback player from a local path.

    Should be munted to /playback/presentation/2.3/{record_id} so
    the record id is no longer part of the path.
    """

    def __init__(self, config: BBBLBConfig, version="2.3") -> None:
        self._config = config
        root = config.PLAYBACK_PLAYER_ROOT
        if not root:
            root = config.PATH_DATA / "htdocs" / "playback" / "presentation" / version

        super().__init__(directory=root, follow_symlink=True, check_dir=False)


class PlaybackMediaApp(StaticFiles):
    """Serve static files from the public recording directory,
    optionally enforcing 'protected recording' restrictions.

    Should be munted to /playback (after PlaybackPlayerApp).
    """

    def __init__(self, config: BBBLBConfig, db: DBContext) -> None:
        self._config = config
        self._db = db

        super().__init__(
            directory=self._config.PATH_DATA / "recordings" / "public",
            follow_symlink=True,
            check_dir=False,
            html=True,
        )

    @staticmethod
    def response_reject():
        return Response(
            "This recording is protected and requires a valid ticket"
            " (link or cookie) to watch.",
            status_code=403,
        )

    @staticmethod
    def response_bad_path(path):
        return Response("Recording not found (invalid path)", status_code=404)

    @staticmethod
    def response_missing(record_id):
        return Response("Recording not found", status_code=404)

    async def get_response(
        self, path: str, scope: MutableMapping[str, Any]
    ) -> Response:
        # TODO: presentation/2.3/{record_id} player requests

        if not self._config.PROTECTED_RECORDINGS:
            return await super().get_response(path, scope)

        # Get record_id ouf of the requested path
        if match := split_media_path(path):
            format_name, record_id, resource = match
        else:
            return self.response_bad_path(path)

        # Allow access to unprotected assets (js, css, fonts, ...)
        if is_unprotected_asset(format_name, resource):
            return await super().get_response(path, scope)

        # Load recording
        async with self._db.session() as session:
            recording = await session.scalar(
                model.Recording.select(record_id=record_id)
            )

        if not recording or recording.state is not model.RecordingState.PUBLISHED:
            return self.response_missing(record_id)

        # Serve unprotected recordings
        if not recording.protected:
            return await super().get_response(path, scope)

        # Serve protected recordings to clients with a valid prt cookie
        prt_cookie_name = PRT_COOKIE_PREFIX + record_id
        prt_cookie = Request(scope).cookies.get(prt_cookie_name, "NO-COOKIE")
        if recording.protected and verify_prt_cookie(
            prt_cookie, record_id, self._config
        ):
            return await super().get_response(path, scope)

        return self.response_reject()
