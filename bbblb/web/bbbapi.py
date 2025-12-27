import asyncio
import functools
import hashlib
import hmac
import re
import typing
import uuid
import lxml.etree
import logging
import sqlalchemy
import sqlalchemy.orm
from starlette.routing import Route
from starlette.responses import Response, RedirectResponse, JSONResponse
import bbblb
from bbblb import utils
from bbblb.services.poller import MeetingPoller
from bbblb.services.recording import RecordingManager, playback_to_xml
from bbblb.lib.bbb import (
    BBBResponse,
    BBBError,
    ETree,
    Element,
    SubElement,
    make_error,
    XML,
    verify_checksum_query,
)
from bbblb import model
from bbblb.services.tenants import TenantCache
from bbblb.web import ApiRequestContext

LOG = logging.getLogger(__name__)
R = typing.TypeVar("R")

RECORDING_READY_PATTERN = re.compile("^meta_(.+)-recording-ready-url$")
CALLBACK_META_PATTERN = re.compile("^meta_(.+)-callback-url$")

api_routes = []


def api(action: str, methods=["GET", "POST"]):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            try:
                async with BBBApiRequest(request) as ctx:
                    out = await func(ctx)
            except BBBError as err:
                out = err
            except Exception as err:
                LOG.exception("Unhandled exception")
                out = make_error("internalError", repr(err), 500)

            if isinstance(out, BBBResponse):
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
    for tag in node.iter("meetingID", "meetingId", "meeting_id"):
        if tag.text == search:
            tag.text = replace
    return node


class BBBApiRequest(ApiRequestContext):
    _tenant: model.Tenant | None = None
    _meeting: model.Meeting | None = None
    _query: dict[str, str] | None = None
    _body: bytearray | None = None

    async def require_tenant(self):
        if self._tenant:
            return self._tenant

        tenant_cache = await self.services.use(TenantCache)
        realm = self.request.headers.get(self.config.TENANT_HEADER, "__NO_REALM__")
        self._tenant = await tenant_cache.get_tenant_by_realm(realm)
        LOG.debug(f"Tenant for realm={realm!r} -> {self._tenant or 'NOT FOUND'}")
        if not self._tenant:
            raise make_error(
                "checksumError",
                "Unknown tenant, unable to perform checksum security check",
            )
        return self._tenant

    async def require_meeting(self):
        if self._meeting:
            return self._meeting
        tenant = await self.require_tenant()
        meetingID = await self.require_param("meetingID")
        try:
            self._meeting = await model.Meeting.get(
                self.session,
                model.Meeting.tenant == tenant,
                sqlalchemy.or_(
                    model.Meeting.internal_id == meetingID,
                    model.Meeting.external_id == meetingID,
                ),
            )
            return self._meeting
        except model.NoResultFound:
            raise make_error(
                "notFound",
                "We could not find a meeting with that meeting ID - perhaps the meeting is not yet running?",
            )

    async def require_bbb_query(self, allow_query_in_body=True):
        """Return BBB API query parameters with the checksum verified and removed."""
        if self._query is not None:
            return self._query

        tenant = await self.require_tenant()
        action = self.request.url.path.split("/")[-1]
        query_str = self.request.url.query

        # Some APIs allow passing query parameters in the request body. While the
        # API docs are not clear, we assume here that parameters cannot be in both
        # places. We only parse the request body if the query string is empty.
        if (
            not query_str
            and allow_query_in_body
            and self.request.method == "POST"
            and self.request.headers.get("Content-Type")
            == "application/x-www-form-urlencoded"
        ):
            try:
                query_str = (await self.read_body()).decode("UTF-8")
            except BBBError:
                # Unable to read enough to make a check, so technicalls this is a checksumError
                raise make_error(
                    "checksumError", "Request body too large, could not verify checksum"
                )

        query, _ = verify_checksum_query(action, query_str, [tenant.secret])
        return query

    async def read_body(self) -> bytes:
        """Read the request body in a save (limited size) way"""
        if self._body is not None:
            return self._body
        if self.request.method != "POST":
            raise TypeError("Expected POST request")
        body = bytearray()
        async for chunk in self.request.stream():
            body += chunk
            if len(body) > self.config.MAX_BODY:
                raise make_error("clientError", "Request body too large", 413)
        self._body = body
        return self._body

    async def require_param(
        self,
        name: str,
        default: R | None = None,
        type: typing.Callable[[str], R] = str,
    ) -> R:
        """Get a parameter from a query mal and raise an appropriate error if it's missing."""
        query = await self.require_bbb_query()
        try:
            return type(query[name])
        except (KeyError, ValueError):
            if default is not None:
                return default
            errorKey = f"missingParameter{name[0].upper()}{name[1:]}"
            raise make_error(errorKey, f"Missing ir invalid parameter {name}.")


##
### API root
##


@api("")
async def handle_index(ctx: BBBApiRequest):
    return XML.response(
        XML.returncode("SUCCESS"),
        XML.version("2.0"),
        XML.info(f"Served by {bbblb.BRANDING}"),
    )


##
### Manage meetings
##


async def forget_meeting(session: model.AsyncSession, meeting: model.Meeting):
    """Forget about a meeting and assume it does not exist (anymore)"""
    # TODO: We may want to re-calculate server load here?
    # Do not fire callbacks, they were already triggered by handle_bbblb_callback
    await session.delete(meeting)


async def _intercept_callbacks(
    cxt: BBBApiRequest, params: dict[str, str], meeting: model.Meeting, is_new: bool
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
    sig = hmac.digest(cxt.config.SECRET.encode("UTF8"), sig, hashlib.sha256).hex()
    url = cxt.request.url_for("bbblb:callback_end", uuid=str(meeting.uuid), sig=sig)
    url = url.replace(scheme="https", hostname=cxt.config.DOMAIN)
    params["meetingEndedURL"] = str(url)

    # Remember and remove all variants of the recording-ready callbacks so we
    # can fire them later, after the recordings were imported and are actually
    # available.
    for meta in list(params):
        if not RECORDING_READY_PATTERN.match(meta):
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

    # For all callbacks that follow the "meta_[name]-callback-url" pattern
    # we assume that they are JWT encoded and must be intercepted because
    # we have to re-sign their payload.

    # Some callbacks should always be intercepted, even if the client did
    # not request them.
    always_intercept: set[str] = set()
    if cxt.config.ANALYTICS_STORE:
        always_intercept.add("meta_analytics-callback-url")

    for param in set(params) | always_intercept:
        match = CALLBACK_META_PATTERN.match(param)
        if not match:
            continue
        orig_url = params.pop(param, None)
        typename = match.group(1)
        if is_new:
            callbacks.append(
                model.Callback(
                    uuid=meeting.uuid,
                    type=typename,
                    tenant=meeting.tenant,
                    server=meeting.server,
                    forward=orig_url,  # can be None
                )
            )

        if orig_url or param in always_intercept:
            url = cxt.request.url_for(
                "bbblb:callback_proxy",
                uuid=meeting.uuid,
                type=typename,
            )
            url = url.replace(scheme="https", hostname=cxt.config.DOMAIN)
            params[param] = str(url)

    return callbacks


@api("create")
async def handle_create(ctx: BBBApiRequest):
    tenant = await ctx.require_tenant()
    params = await ctx.require_bbb_query()
    unscoped_id = await ctx.require_param("meetingID")
    scoped_id = utils.add_scope(unscoped_id, tenant.name)
    await ctx.require_param("name")  # Just check

    if len(scoped_id) > utils.MAX_MEETING_ID_LEN:
        raise make_error(
            "sizeError",
            "Meeting ID must be between 2 and %d characters"
            % (utils.MAX_MEETING_ID_LEN - (len(scoped_id) - len(unscoped_id))),
        )

    # Phase one: Fetch an existing meeting, or create one in our own database
    # and assign a server, so the next create call will use the same server.

    select_meeting = model.Meeting.select(external_id=unscoped_id, tenant=tenant)
    meeting = (await ctx.session.execute(select_meeting)).scalar_one_or_none()
    meeting_created = False

    if not meeting:
        # Find best server for new meetings
        stmt = model.Server.select_best(tenant).with_for_update()
        server = (await ctx.session.execute(stmt)).scalar_one_or_none()
        if not server:
            raise make_error("internalError", "No suitable servers available.")

        # Increase server load NOW (as fast as possible)
        try:
            size_hint = int(params.get("meta_bbb-meeting-size-hint", "0"))
        except ValueError:
            size_hint = 0
        load = ctx.services.get(MeetingPoller).get_meeting_load(size_hint=size_hint)
        await ctx.session.execute(server.increment_load_stmt(load))

        # Try to create the meeting
        # Note: This commits the session as a side-effect
        meeting, meeting_created = await model.get_or_create(
            ctx.session,
            select_meeting,
            lambda: model.Meeting(
                uuid=uuid.uuid4(), external_id=unscoped_id, server=server, tenant=tenant
            ),
        )

    # Apply tenant-specific create call overrides
    for override in tenant.overrides:
        if override.type == "create":
            override.apply(params)

    # Enforce BBBLB specific overrides
    params["meetingID"] = scoped_id
    params["meta_bbblb-uuid"] = str(meeting.uuid)
    params["meta_bbblb-origin"] = ctx.config.DOMAIN
    params["meta_bbblb-tenant"] = meeting.tenant.name
    params["meta_bbblb-server"] = meeting.server.domain

    # Intercept callbacks.
    callbacks = await _intercept_callbacks(ctx, params, meeting, is_new=meeting_created)
    if meeting_created and callbacks:
        ctx.session.add_all(callbacks)
        await ctx.session.commit()

    # Phase two: At this point the meeting exists in the database, but may not
    # yet have an internal_id. We now forward the call to the back-end and see
    # what happens.

    try:
        # Give connection back to pool, because the create call may take a while
        await ctx.session.close()

        # Create meeting on back-end
        body, ctype = None, ctx.request.headers.get("Content-Type")
        if ctype == "application/xml":
            body = await ctx.read_body()

        async with ctx.bbb.connect(
            meeting.server.api_base, meeting.server.secret
        ) as bbb:
            upstream = await bbb.action("create", params, body=body, content_type=ctype)
            upstream.raise_on_error()

        # Success! Update meeting info if it's a new meeting
        if meeting_created:
            LOG.info(f"Created {meeting} on {meeting.server}")
            await ctx.session.execute(
                model.Meeting.update(model.Meeting.id == meeting.id).values(
                    internal_id=upstream.internalMeetingID
                )
            )
            await ctx.session.commit()

        xml_fix_meeting_id(upstream.xml, scoped_id, unscoped_id)
        return upstream

    except BaseException:
        if meeting_created:
            LOG.exception(f"Failed to create {meeting} on {meeting.server}")
            for cb in callbacks:
                await ctx.session.delete(cb)
            await forget_meeting(ctx.session, meeting)
            await ctx.session.commit()
        raise


@api("join", methods=["GET"])
async def handle_join(ctx: BBBApiRequest):
    tenant = await ctx.require_tenant()
    params = await ctx.require_bbb_query()
    unscoped_id = await ctx.require_param("meetingID")
    scoped_id = utils.add_scope(unscoped_id, tenant.name)
    meeting = await ctx.require_meeting()
    server = await meeting.awaitable_attrs.server

    await ctx.session.execute(server.increment_load_stmt(ctx.config.LOAD_USER))
    await ctx.session.commit()

    # Apply tenant-specific join call overrides
    for override in tenant.overrides:
        if override.type == "join":
            override.apply(params)

    await ctx.session.close()  # Give connection back to pool

    async with ctx.bbb.connect(server.api_base, server.secret) as bbb:
        params["meetingID"] = scoped_id
        redirect_uri = bbb.encode_uri("join", params)
        return RedirectResponse(redirect_uri)


@api("end")
async def handle_end(ctx: BBBApiRequest):
    async with ctx.session:
        tenant = await ctx.require_tenant()
        params = await ctx.require_bbb_query()
        unscoped_id = await ctx.require_param("meetingID")
        scoped_id = utils.add_scope(unscoped_id, tenant.name)
        meeting = await ctx.require_meeting()
        server = await meeting.awaitable_attrs.server
        # Always end the meeting if requested
        await forget_meeting(ctx.session, meeting)
        await ctx.session.commit()

    # Now try to actually end it in the backend.
    async with ctx.bbb.connect(server.api_base, server.secret) as bbb:
        params["meetingID"] = scoped_id
        upstream = await bbb.action("end", params)

    # Just pass any errors (most likely a notFound).
    xml_fix_meeting_id(upstream.xml, scoped_id, unscoped_id)
    return upstream


@api("sendChatMessage", methods=["GET"])
async def handle_send_chat_message(ctx: BBBApiRequest):
    async with ctx.session:
        tenant = await ctx.require_tenant()
        params = await ctx.require_bbb_query()
        unscoped_id = await ctx.require_param("meetingID")
        scoped_id = utils.add_scope(unscoped_id, tenant.name)
        meeting = await ctx.require_meeting()
        server = await meeting.awaitable_attrs.server

    async with ctx.bbb.connect(server.api_base, server.secret) as bbb:
        params["meetingID"] = scoped_id
        upstream = await bbb.action("sendChatMessage", params)

    if upstream.error == "notFound":
        async with ctx.session:
            await forget_meeting(ctx.session, meeting)
            await ctx.session.commit()

    xml_fix_meeting_id(upstream.xml, scoped_id, unscoped_id)
    return upstream


@api("getJoinUrl", methods=["GET"])
async def handle_get_join_url(ctx: BBBApiRequest):
    # Cannot be implemmented in a load-balancer:
    # https://github.com/bigbluebutton/bigbluebutton/issues/24212
    raise make_error(
        "notImplemented", "This API endpoint or feature is not implemented"
    )


@api("insertDocument", methods=["POST"])
async def handle_insert_document(ctx: BBBApiRequest):
    async with ctx.session:
        tenant = await ctx.require_tenant()
        params = await ctx.require_bbb_query()
        unscoped_id = await ctx.require_param("meetingID")
        scoped_id = utils.add_scope(unscoped_id, tenant.name)
        meeting = await ctx.require_meeting()
        server = await meeting.awaitable_attrs.server

    params["meetingID"] = scoped_id
    ctype = ctx.request.headers.get("Content-Type")
    stream = ctx.request.stream()

    async with ctx.bbb.connect(server.api_base, server.secret) as bbb:
        upstream = await bbb.action(
            "insertDocument", params, body=stream, content_type=ctype, expect_json=True
        )

    return upstream


@api("isMeetingRunning")
async def handle_is_meeting_running(ctx: BBBApiRequest):
    async with ctx.session:
        tenant = await ctx.require_tenant()
        params = await ctx.require_bbb_query()
        unscoped_id = await ctx.require_param("meetingID")
        scoped_id = utils.add_scope(unscoped_id, tenant.name)

        try:
            meeting = await ctx.require_meeting()
        except BBBError:
            # Not an error
            return BBBResponse(
                XML.response(
                    XML.returncode("SUCCESS"),
                    XML.running("false"),
                )
            )

        server = await meeting.awaitable_attrs.server

    async with ctx.bbb.connect(server.api_base, server.secret) as bbb:
        params["meetingID"] = scoped_id
        upstream = await bbb.action("isMeetingRunning", params)

    if upstream.find("running") == "false":
        async with ctx.session as session:
            await forget_meeting(session, meeting)
            await session.commit()

    xml_fix_meeting_id(upstream.xml, scoped_id, unscoped_id)
    return upstream


@api("getMeetings")
async def handle_get_meetings(ctx: BBBApiRequest):
    async with ctx.session as session:
        tenant = await ctx.require_tenant()
        params = await ctx.require_bbb_query()
        # Find all servers that currently have matching meetings
        stmt = (
            model.Server.select(model.Meeting.tenant == tenant)
            .join(model.Meeting)
            .distinct()
        )
        servers = (await session.execute(stmt)).scalars()

    result_xml = typing.cast(Element, XML.response(XML.returncode("SUCCESS")))
    all_meetings = SubElement(result_xml, "meetings")

    tasks: list[typing.Awaitable[BBBResponse]] = []

    async def fetch_meetings(server):
        async with ctx.bbb.connect(server.api_base, server.secret) as bbb:
            return await bbb.action("getMeetings", params)

    for server in servers:
        tasks.append(fetch_meetings(server))

    for next_upstream in asyncio.as_completed(tasks):
        upstream = await next_upstream
        if not upstream.success:
            return
        for meeting_xml in upstream.xml.iterfind("meetings/meeting"):
            if meeting_xml.findtext("metadata/bbblb-tenant") != tenant.name:
                continue
            scoped_id = meeting_xml.findtext("meetingID")
            if not scoped_id:
                continue
            unscoped_id, scope = utils.split_scope(scoped_id)
            if scope != tenant.name:
                continue
            xml_fix_meeting_id(meeting_xml, scoped_id, unscoped_id)
            all_meetings.append(meeting_xml)

    return result_xml


@api("getMeetingInfo")
async def handle_get_meeting_info(ctx: BBBApiRequest):
    async with ctx.session as session:
        tenant = await ctx.require_tenant()
        params = await ctx.require_bbb_query()
        unscoped_id = await ctx.require_param("meetingID")
        scoped_id = utils.add_scope(unscoped_id, tenant.name)
        meeting = await ctx.require_meeting()
        server = await meeting.awaitable_attrs.server

    async with ctx.bbb.connect(server.api_base, server.secret) as bbb:
        params["meetingID"] = scoped_id
        upstream = await bbb.action("getMeetingInfo", params)

    if upstream.error == "notFound":
        async with ctx.session as session:
            await forget_meeting(session, meeting)
            await session.commit()

    xml_fix_meeting_id(upstream.xml, scoped_id, unscoped_id)
    return upstream


##
### Recordings
##


@api("getRecordings", methods=["GET"])
async def handle_get_recordings(ctx: BBBApiRequest):
    tenant = await ctx.require_tenant()
    params = await ctx.require_bbb_query()
    meeting_ids = await ctx.require_param("meetingID", "")
    record_ids = await ctx.require_param("recordID", "")
    state = await ctx.require_param("state", "")
    meta = {key[5:]: value for key, value in params.items() if key.startswith("meta_")}
    offset = await ctx.require_param("offset", -1, type=int)
    limit = await ctx.require_param("limit", -1, type=int)

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
    if 0 < limit < ctx.config.MAX_ITEMS:
        stmt = stmt.limit(limit)
    else:
        stmt = stmt.limit(ctx.config.MAX_ITEMS)

    result_xml: Element = XML.response(XML.returncode("SUCCESS"))
    all_recordings = SubElement(result_xml, "recordings")

    for rec in (await ctx.session.execute(stmt)).scalars():
        rec_xml: Element = XML.recording(
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
            XML.participants(str(rec.participants)),
            XML.metadata(*[XML(key, value) for key, value in rec.meta.items()]),
        )

        # TODO: Undocumented <breakout> section with junk in it, see actual BBB responses
        # TODO: Undocumented <rawSize> section

        xml_fix_meeting_id(
            rec_xml, utils.add_scope(rec.external_id, tenant.name), rec.external_id
        )

        playback_xml = SubElement(rec_xml, "playback")
        for playback in rec.formats:
            format_xml = playback_to_xml(ctx.config, playback)
            playback_xml.append(format_xml)

        all_recordings.append(rec_xml)

    return result_xml


@api("publishRecordings", methods=["GET"])
async def handle_publish_recordings(ctx: BBBApiRequest):
    importer = await ctx.services.use(RecordingManager)

    tenant = await ctx.require_tenant()
    record_ids = (await ctx.require_param("recordID")).split(",")
    publish = (await ctx.require_param("publish")).lower() == "true"

    if publish:
        action = importer.publish
        new_state = model.RecordingState.PUBLISHED
    else:
        action = importer.unpublish
        new_state = model.RecordingState.UNPUBLISHED

    # Fetch all recordings
    stmt = model.Recording.select(
        model.Recording.tenant == tenant, model.Recording.record_id.in_(record_ids)
    )
    recs = (await ctx.session.execute(stmt)).scalars().all()

    if not recs:
        return make_error("notFound", "Unknown recording")

    # Publish or unpublish recordings
    for rec in recs:
        try:
            await asyncio.to_thread(action, tenant.name, rec.record_id)
            # TODO: This is racy, but unlikely to cause issues. Improve?
            await ctx.session.execute(
                model.Recording.update(model.Recording.id == rec.id).values(
                    state=new_state
                )
            )
        except FileNotFoundError:
            LOG.exception(
                f"Recording {rec.record_id} found in database but not in storage!"
            )
            continue

    # Persist changes (may be fewer than requested)
    await ctx.session.commit()

    return XML.response(
        XML.returncode("SUCCESS"),
        XML.published(new_state.value),
    )


@api("deleteRecordings", methods=["GET"])
async def handle_delete_recordings(ctx: BBBApiRequest):
    async with ctx.session:
        tenant = await ctx.require_tenant()
        record_ids = (await ctx.require_param("recordID")).split(",")

        # Delete all recordings from database
        stmt = model.Recording.delete(
            model.Recording.tenant == tenant, model.Recording.record_id.in_(record_ids)
        )
        await ctx.session.execute(stmt)
        await ctx.session.commit()

    # Actually delete records on disk, even if they did not exist in db.
    # Do so in the background, as this may take some time.
    importer = await ctx.services.use(RecordingManager)

    for record_id in record_ids:
        asyncio.create_task(asyncio.to_thread(importer.delete, tenant.name, record_id))

    return XML.response(
        XML.returncode("SUCCESS"),
        XML.deleted("true"),
    )


@api("updateRecordings")
async def handle_update_recordings(ctx: BBBApiRequest):
    tenant = await ctx.require_tenant()
    params = await ctx.require_bbb_query()

    record_ids = (await ctx.require_param("recordID")).split(",")

    meta = {
        key[5:]: value
        for key, value in params.items()
        if key.startswith("meta_") and not key.startswith("meta_bbblb-")
    }

    stmt = model.Recording.select(
        model.Recording.tenant == tenant, model.Recording.record_id.in_(record_ids)
    )
    recs = (await ctx.session.execute(stmt)).scalars().all()

    updated = False
    for rec in recs:
        for key, value in meta.items():
            updated |= rec.meta.get(key) != value
            if value:
                rec.meta[key] = value
            else:
                rec.meta.pop(key, None)

    await ctx.session.commit()

    return XML.response(
        XML.returncode("SUCCESS"),
        XML.updated("true" if updated else "false"),
    )


@api("getRecordingTextTracks")
async def handle_get_Recordings_text_tracks(ctx: BBBApiRequest):
    # Can only be implemented for existing captions. TODO
    raise make_error(
        "notImplemented", "This API endpoint or feature is not implemented"
    )


@api("putRecordingTextTrack", methods=["POST"])
async def handle_put_recordings_text_track(ctx: BBBApiRequest):
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
    raise make_error(
        "notImplemented", "This API endpoint or feature is not implemented"
    )
