import asyncio
import functools
import hashlib
import hmac
import typing
import uuid
import lxml.etree
import logging
from sqlalchemy import update
import sqlalchemy
import sqlalchemy.orm
from starlette.requests import Request
from starlette.routing import Route
from starlette.responses import Response, RedirectResponse, JSONResponse
import bbblb
from bbblb import utils
from bbblb import bbblib
from bbblb.utils import checked_cast
from bbblb import recordings, model
from bbblb.bbblib import XML, BBBClient, BBBError, ETree
from bbblb.settings import config


LOG = logging.getLogger(__name__)
R = typing.TypeVar("R")

api_routes = []


def api(action: str, methods=["GET", "POST"]):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            try:
                out = await func(request)
            except BBBError as err:
                out = err
            except Exception as err:
                LOG.exception("Unhandled exception")
                out = bbblib.make_error("internalError", repr(err), 500)
            if isinstance(out, bbblib.BBBResponse):
                if out._xml is not None:
                    out = to_xml(out.xml, out.status_code)
                else:
                    out = JSONResponse(out.json, out.status_code)
            elif isinstance(out, ETree):
                out = to_xml(out, 200)
            elif isinstance(out, dict):
                out = JSONResponse(out, 200)
            return out

        path = "/" + action
        api_routes.append(
            Route(path, wrapper, methods=methods, name=f"bbb:{action or 'index'}")
        )
        return wrapper

    return decorator


def to_xml(xml, status_code=200):
    return Response(
        content=lxml.etree.tostring(xml, pretty_print=True),
        status_code=status_code,
        media_type="application/xml;charset=utf-8",
    )


def xml_fix_meeting_id(node: ETree, search: str, replace: str):
    """Do an in-place string search and replace of XML tags that typically
    contain an (external) meeting ID."""
    for tag in node.iter("meetingID", "meetingId"):
        if tag.text == search:
            tag.text = replace
    return node


async def require_tenant(request: Request):
    tenant = getattr(request.state, "tenant", None)
    if tenant:
        return typing.cast(model.Tenant, tenant)

    try:
        realm = request.headers.get(config.TENANT_HEADER, "__NO_REALM__")
        request.state.tenant = tenant = await model.Tenant.get(realm=realm)
        return tenant
    except model.NoResultFound:
        raise bbblib.make_error(
            "checksumError", "Unknown tenant, unable to perform checksum security check"
        )


async def require_meeting(tenant: model.Tenant, meeting_id: str):
    try:
        stmt = model.Meeting.select(
            model.Meeting.tenant == tenant, model.Meeting.external_id == meeting_id
        )
        result = await model.ScopedSession.execute(stmt)
        return result.scalar_one()
    except model.NoResultFound:
        raise bbblib.make_error(
            "notFound",
            "We could not find a meeting with that meeting ID - perhaps the meeting is not yet running?",
        )


async def require_bbb_query(
    request: Request, tenant: model.Tenant, allow_query_in_body=True
):
    """Return BBB API query parameters with the checksum verified and removed."""
    action = request.url.path.split("/")[-1]
    query_str = request.url.query

    # Some APIs allow passing query parameters in the request body. While the
    # API docs are not clear, we assume here that parameters cannot be in both
    # places. We only parse the request body if the query string is empty.
    if (
        not query_str
        and allow_query_in_body
        and request.method == "POST"
        and request.headers.get("Content-Type") == "application/x-www-form-urlencoded"
    ):
        try:
            query_str = (await read_body(request)).decode("UTF-8")
        except bbblib.make_error:
            # Unable to read enough to make a check, so technicalls this is a checksumError
            raise bbblib.make_error(
                "checksumError", "Request body too large, could not verify checksum"
            )

    query, _ = bbblib.verify_checksum_query(action, query_str, [tenant.secret])
    return query


async def read_body(request: Request) -> bytes:
    """Read the request body in a save (limited size) way"""
    if request.method != "POST":
        raise TypeError("Expected POST request")
    form_body = b""
    async for chunk in request.stream():
        form_body += chunk
        if len(form_body) > config.MAX_BODY:
            raise bbblib.make_error("clientError", "Request body too large", 413)
    return form_body


def require_param(
    params: dict[str, str],
    name: str,
    default: R | None = None,
    type: typing.Callable[[str], R] = str,
) -> R:
    """Get a parameter from a query mal and raise an appropriate error if it's missing."""
    try:
        return type(params[name])
    except (KeyError, ValueError):
        if default is not None:
            return default
        errorKey = f"missingParameter{name[0].upper()}{name[1:]}"
        raise bbblib.make_error(errorKey, f"Missing parameter {name}.")


##
### API root
##


@api("")
async def handle_index(request: Request):
    return XML.response(
        XML.returncode("SUCCESS"),
        XML.version("2.0"),
        XML.info(f"Served by {bbblb.BRANDING}"),
    )


##
### Manage meetings
##


async def forget_meeting(meeting: model.Meeting):
    """Forget about a meeting and assume it does not exist (anymore)"""
    # TODO: We may want to re-calculate server load here?
    # Do not fire callbacks, they were already triggered by handle_bbblb_callback
    await model.ScopedSession.delete(meeting)


async def _instrument_callbacks(
    request: Request, params: dict[str, str], meeting: model.Meeting, is_new: bool
):
    callbacks = []
    # Replace "meetingEndedURL" with our own callback, and remember the original
    # callback if present.
    orig_url = params.pop("meetingEndedURL", None)
    if orig_url and is_new:
        callbacks.append(
            model.Callback(
                uuid=meeting.uuid,
                type=model.CALLBACK_TYPE_END,
                tenant=meeting.tenant,
                server=meeting.server,
                forward=orig_url,
            )
        )
    # No signed payload, so we sign the URL instead.
    sig = f"bbblb:callback:end:{meeting.uuid}".encode("ASCII")
    sig = hmac.digest(config.SECRET.encode("UTF8"), sig, hashlib.sha256).hex()
    url = request.url_for("bbblb:callback_end", uuid=str(meeting.uuid), sig=sig)
    params["meetingEndedURL"] = str(url)

    # Remember and remove all variants of the recording-ready callbacks so we
    # can fire them later, after the recordings were imported and are actually
    # available.
    for meta in list(params):
        if not (meta.startswith("meta_") and meta.endswith("-recording-ready-url")):
            continue
        orig_url = params.pop(meta)
        if is_new:
            callbacks.append(
                model.Callback(
                    uuid=meeting.uuid,
                    type=model.CALLBACK_TYPE_REC,
                    tenant=meeting.tenant,
                    server=meeting.server,
                    forward=orig_url,
                )
            )

    # For all other known callbacks (other than meta_endCallbackUrl) we assume
    # that they follow the JWT model and can be proxied immediately. They still
    # need to be intercepted because we have to re-sign their payload.
    for meta in ("meta_analytics-callback-url",):
        orig_url = params.pop(meta, None)
        if orig_url:
            typename = meta[5:-14]  # Just the middle part
            if is_new:
                callbacks.append(
                    model.Callback(
                        uuid=meeting.uuid,
                        type=typename,
                        tenant=meeting.tenant,
                        server=meeting.server,
                        forward=orig_url,
                    )
                )
            url = request.url_for(
                "bbblb:callback_proxy",
                uuid=meeting.uuid,
                type=typename,
            )
            params[meta] = str(url)

    return callbacks


@api("create")
@model.transactional(autocommit=True)
async def handle_create(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    unscoped_id = require_param(params, "meetingID")
    require_param(params, "name")  # Just check

    scoped_id = utils.add_scope(unscoped_id, tenant.name)
    if len(scoped_id) > utils.MAX_MEETING_ID_LEN:
        raise bbblib.make_error(
            "sizeError",
            "Meeting ID must be between 2 and %d characters"
            % (utils.MAX_MEETING_ID_LEN - (len(scoped_id) - len(unscoped_id))),
        )

    # Fetch existing meeting, if present
    session = model.ScopedSession()
    select_meeting = model.Meeting.select(external_id=unscoped_id, tenant=tenant)
    meeting = (await session.execute(select_meeting)).scalar_one_or_none()
    meeting_created = False

    if not meeting:
        # Find suitable server
        stmt = model.Server.select_available(tenant).limit(1)
        server = (await model.ScopedSession.execute(stmt)).scalars().first()
        if not server:
            raise bbblib.make_error("internalError", "No suitable servers available.")

        # Try to create the meeting
        meeting, meeting_created = await model.get_or_create(
            session,
            select_meeting,
            lambda: model.Meeting(
                uuid=uuid.uuid4(), external_id=unscoped_id, server=server, tenant=tenant
            ),
        )

    # Add or replace create parameters
    params["meetingID"] = scoped_id
    params["meta_bbblb-uuid"] = str(meeting.uuid)
    params["meta_bbblb-origin"] = config.DOMAIN
    params["meta_bbblb-tenant"] = meeting.tenant.name
    params["meta_bbblb-server"] = meeting.server.domain

    # Fix all callback parameters and get a list of (not yet added) callbacks.
    callbacks = await _instrument_callbacks(
        request, params, meeting, is_new=meeting_created
    )

    # Increase server load as fast as possible for new meetings and add callbacks
    if meeting_created:
        load = config.LOADFACTOR_INITIAL + config.LOADFACTOR_MEETING
        meeting.server.load = model.Server.load + load
        if callbacks:
            session.add_all(callbacks)
        await session.commit()

    # Now actually try to create the meeting on the back-end server
    try:
        bbb = BBBClient(meeting.server.api_base, meeting.server.secret)
        body, ctype = None, request.headers.get("Content-Type")
        if ctype == "application/xml":
            body = await read_body(request)

        upstream = await bbb.action("create", params, body=body, content_type=ctype)
        upstream.raise_on_error()

        if meeting_created:
            LOG.info(f"Created {meeting} on {meeting.server}")
            await session.execute(
                update(model.Meeting)
                .where(model.Meeting.id == meeting.id)
                .values(internal_id=upstream.internalMeetingID)
            )
            await session.commit()

        xml_fix_meeting_id(upstream.xml, scoped_id, unscoped_id)
        return upstream

    except BaseException:
        if meeting_created:
            LOG.exception(f"Failed to create {meeting} on {meeting.server}")
            for cb in callbacks:
                await session.delete(cb)
            await forget_meeting(meeting)
            await session.commit()
        raise


@api("join", methods=["GET"])
@model.transactional(autocommit=True)
async def handle_join(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    unscoped_id = require_param(params, "meetingID")
    scoped_id = utils.add_scope(unscoped_id, tenant.name)
    meeting = await require_meeting(tenant, unscoped_id)
    server = await meeting.awaitable_attrs.server

    server.load += config.LOADFACTOR_SIZE

    bbb = BBBClient(server.api_base, server.secret)
    params["meetingID"] = scoped_id
    redirect_uri = bbb.encode_uri("join", params)
    return RedirectResponse(redirect_uri)


@api("end")
@model.transactional(autocommit=True)
async def handle_end(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    unscoped_id = require_param(params, "meetingID")
    scoped_id = utils.add_scope(unscoped_id, tenant.name)
    meeting = await require_meeting(tenant, unscoped_id)
    server = await meeting.awaitable_attrs.server

    # Always end the meeting if requested
    await forget_meeting(meeting)

    # Now try to actually end it in the backend.
    bbb = BBBClient(server.api_base, server.secret)
    params["meetingID"] = scoped_id
    upstream = await bbb.action("end", params)

    # Just pass any errors (most likely a notFound).
    xml_fix_meeting_id(upstream.xml, scoped_id, unscoped_id)
    return upstream


@api("sendChatMessage", methods=["GET"])
@model.transactional(autocommit=True)
async def handle_send_chat_message(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    unscoped_id = require_param(params, "meetingID")
    scoped_id = utils.add_scope(unscoped_id, tenant.name)
    meeting = await require_meeting(tenant, scoped_id)
    server = await meeting.awaitable_attrs.server

    bbb = BBBClient(server.api_base, server.secret)
    params["meetingID"] = scoped_id
    upstream = await bbb.action("sendChatMessage", params)

    if upstream.error == "notFound":
        await forget_meeting(meeting)

    xml_fix_meeting_id(upstream.xml, scoped_id, unscoped_id)
    return upstream


@api("getJoinUrl", methods=["GET"])
@model.transactional(autocommit=True)
async def handle_get_join_url(request: Request):
    # Cannot be implemmented in a load-balancer:
    # https://github.com/bigbluebutton/bigbluebutton/issues/24212
    raise bbblib.make_error(
        "notImplemented", "This API endpoint or feature is not implemented"
    )


@api("insertDocument", methods=["POST"])
@model.transactional(autocommit=True)
async def handle_insert_document(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    unscoped_id = require_param(params, "meetingID")
    meeting = await require_meeting(tenant, unscoped_id)
    server = await meeting.awaitable_attrs.server

    bbb = BBBClient(server.api_base, server.secret)
    ctype = request.headers.get("Content-Type")
    stream = request.stream()
    upstream = await bbb.action(
        "insertDocument", params, body=stream, content_type=ctype, expect_json=True
    )

    return upstream


@api("isMeetingRunning")
@model.transactional(autocommit=True)
async def handle_is_meeting_running(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    unscoped_id = require_param(params, "meetingID")
    scoped_id = utils.add_scope(unscoped_id, tenant.name)

    try:
        meeting = await require_meeting(tenant, unscoped_id)
    except bbblib.BBBError:
        return XML.response(
            XML.returncode("SUCCESS"),
            XML.running("false"),
        )

    server = await meeting.awaitable_attrs.server
    bbb = BBBClient(server.api_base, server.secret)
    params["meetingID"] = scoped_id
    upstream = await bbb.action("isMeetingRunning", params)

    if upstream.find("running") == "false":
        await forget_meeting(meeting)

    xml_fix_meeting_id(upstream.xml, scoped_id, unscoped_id)
    return upstream


@api("getMeetings")
@model.transactional(autocommit=True)
async def handle_get_meetings(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)

    result_xml: ETree = XML.response(XML.returncode("SUCCESS"), XML.meetings())
    all_meetings = result_xml.find("meetings")

    # Find all servers that currently have matching meetings
    stmt = (
        model.Server.select(model.Meeting.tenant == tenant)
        .join(model.Meeting)
        .distinct()
    )
    servers = (await model.ScopedSession.execute(stmt)).scalars()

    tasks: list[typing.Awaitable[bbblib.BBBResponse]] = []
    for server in servers:
        api = BBBClient(server.api_base, server.secret)
        tasks.append(api.action("getMeetings", params))
    for next_upstream in asyncio.as_completed(tasks):
        upstream = await next_upstream
        if not upstream.success:
            return
        for meeting_xml in upstream.xml.iterfind("meetings/meeting"):
            if meeting_xml.findtext("metadata/bbblb-tenant") != tenant.name:
                continue
            scoped_id = meeting_xml.findtext("meetingID")
            unscoped_id, scope = utils.extract_scope(scoped_id)
            if scope != tenant.name:
                continue
            xml_fix_meeting_id(meeting_xml, scoped_id, unscoped_id)
            all_meetings.append(meeting_xml)

    return result_xml


@api("getMeetingInfo")
@model.transactional(autocommit=True)
async def handle_get_meeting_info(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    unscoped_id = require_param(params, "meetingID")
    scoped_id = utils.add_scope(unscoped_id, tenant.name)
    meeting = await require_meeting(tenant, unscoped_id)
    server = await meeting.awaitable_attrs.server

    bbb = BBBClient(server.api_base, server.secret)
    params["meetingID"] = scoped_id
    upstream = await bbb.action("getMeetingInfo", params)

    if upstream.error == "notFound":
        await forget_meeting(meeting)

    xml_fix_meeting_id(upstream.xml, scoped_id, unscoped_id)
    return upstream


##
### Recordings
##


@api("getRecordings", methods=["GET"])
@model.transactional(autocommit=True)
async def handle_get_recordings(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    meeting_ids = require_param(params, "meetingID", "")
    record_ids = require_param(params, "recordID", "")
    state = require_param(params, "state", "")
    meta = {key[5:]: value for key, value in params.items() if key.startswith("meta_")}
    offset = require_param(params, "offset", -1, type=int)
    limit = require_param(params, "limit", -1, type=int)

    stmt = model.Recording.select(tenant=tenant)
    stmt = stmt.order_by(model.Recording.id)
    # TODO: Check if a joined loader is faster, and maybe skip recordings with
    # no formats
    stmt = stmt.options(sqlalchemy.orm.selectinload(model.Recording.formats))

    meeting_ids = [m.strip() for m in meeting_ids.split(",") if m.strip()]
    if meeting_ids:
        stmt = stmt.where(model.Recording.external_id.in_(meeting_ids))
    record_ids = [m.strip() for m in record_ids.split(",") if m.strip()]
    if record_ids:
        stmt = stmt.where(
            sqlalchemy.or_(
                *[
                    model.Recording.record_id.startswith(record_id, autoescape=True)
                    for record_id in record_ids[:100]
                ]
            )
        )
    state = [m.strip() for m in state.split(",") if m.strip()]
    if state and "any" not in state:
        # Info: We only manage published|unpublished recordings, so 'any' is
        # practically the same as no state filter at all.
        stmt = stmt.where(model.Recording.state.in_(state[:5]))
    if meta:
        for key, value in meta.items():
            stmt = stmt.where(model.Recording.meta[key].as_text() == value)
    if 0 < offset < 10000:
        stmt = stmt.offset(offset)
    if 0 < limit < config.MAX_ITEMS:
        stmt = stmt.limit(limit)
    else:
        stmt = stmt.limit(config.MAX_ITEMS)

    result_xml: ETree = XML.response(XML.returncode("SUCCESS"), XML.recordings())
    all_recordings = result_xml.find("recordings")

    for rec in (await model.ScopedSession.execute(stmt)).scalars():
        rec_xml: ETree = XML.recording(
            XML.recordID(rec.record_id),
            XML.meetingID(rec.external_id),
            XML.internalMeetingID(rec.record_id),  # TODO: Really always the case?
            XML.name(rec.meta["meetingName"]),
            XML.isBreakout(rec.meta.get("isBreakout", "false")),
            XML.published(
                "true" if rec.state == model.RecordingState.PUBLISHED else "false"
            ),
            XML.state(rec.state.value),
            XML.startTime(str(int(rec.started.timestamp() * 1000))),
            XML.endTime(str(int(rec.ended.timestamp() * 1000))),
            XML.parparticipants(str(rec.participants)),
            XML.metadata(*[XML(key, value) for key, value in meta]),
            XML.playback(),
        )

        xml_fix_meeting_id(
            rec_xml, utils.add_scope(rec.external_id, tenant.name), rec.external_id
        )

        playback_xml: ETree = rec_xml.find("playback")
        for playback in rec.formats:
            rec_xml = recordings.playback_xml(playback, root_tag="format")
            playback_xml.append(rec_xml)

        all_recordings.append(rec_xml)

    return result_xml


@api("publishRecordings", methods=["GET"])
@model.transactional(autocommit=True)
async def handle_publish_recordings(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    record_ids = require_param(params, "recordID").split(",")
    publish = require_param(params, "publish").lower() == "true"
    new_state = (
        model.RecordingState.PUBLISHED if publish else model.RecordingState.UNPUBLISHED
    )
    importer = checked_cast(recordings.RecordingImporter, request.app.state.importer)

    stmt = model.Recording.select(
        model.Recording.tenant == tenant, model.Recording.record_id.in_(record_ids)
    ).with_for_update()
    recs = (await model.ScopedSession.execute(stmt)).scalars().all()

    if not recs:
        return bbblib.make_error("notFound", "Unknown recording")

    for rec in recs:
        try:
            await asyncio.to_thread(
                importer.ensure_state, tenant.name, rec.record_id, published=publish
            )
            rec.state = new_state
        except FileNotFoundError:
            LOG.exception(
                f"Recording {rec.record_id} found in database but not in storage!"
            )
            continue

    return XML.response(
        XML.returncode("SUCCESS"),
        XML.published(new_state.value),
    )


@api("deleteRecordings", methods=["GET"])
@model.transactional(autocommit=True)
async def handle_delete_recordings(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    record_ids = require_param(params, "recordID").split(",")

    importer = checked_cast(recordings.RecordingImporter, request.app.state.importer)
    stmt = model.Recording.select(
        model.Recording.tenant == tenant, model.Recording.record_id.in_(record_ids)
    ).with_for_update()
    recs = (await model.ScopedSession.execute(stmt)).scalars().all()

    for rec in recs:
        await model.ScopedSession.delete(rec)
        try:
            await asyncio.to_thread(importer.delete, tenant.name, rec.record_id)
        except FileNotFoundError:
            continue  # already missing, which is fine in this case

    return XML.response(
        XML.returncode("SUCCESS"),
        XML.deleted("true"),
    )


@api("updateRecordings")
@model.transactional(autocommit=True)
async def handle_update_recordings(request: Request):
    tenant = await require_tenant(request)
    params = await require_bbb_query(request, tenant)
    record_ids = require_param(params, "recordID").split(",")

    meta = {
        key[5:]: value
        for key, value in params.items()
        if key.startswith("meta_") and not key.startswith("meta_bbblb-")
    }

    stmt = model.Recording.select(
        model.Recording.tenant == tenant, model.Recording.record_id.in_(record_ids)
    ).with_for_update()
    recs = (await model.ScopedSession.execute(stmt)).scalars().all()

    for rec in recs:
        for key, value in meta.items():
            if value:
                rec.meta[key] = value
            else:
                rec.meta.pop(key, None)


@api("getRecordingTextTracks")
@model.transactional(autocommit=True)
async def handle_get_Recordings_text_tracks(request: Request):
    # Can only be implemented for existing captions. TODO
    raise bbblib.make_error(
        "notImplemented", "This API endpoint or feature is not implemented"
    )


@api("putRecordingTextTrack", methods=["POST"])
@model.transactional(autocommit=True)
async def handle_put_recordings_text_track(request: Request):
    # Requires significant work to implement, because caption processing
    # requires scripts that run on the BBB server and modify the original
    # recording, but:
    #
    # 1) The recording may no longer be present on that backend-server.
    # 2) If it is, we would not be notified about the changes because the
    #    post_publish hooks are not triggered again.
    #
    # IF we assume that captions do not need to be modified (cut marks) but
    # already match the fully processed recording, then we COULD try to
    # implement the necessary steps here, if ffmpeg is installed and available.
    raise bbblib.make_error(
        "notImplemented", "This API endpoint or feature is not implemented"
    )
