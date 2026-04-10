# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import timedelta
import uuid

import pytest
import pytest_asyncio
from bbblb.lib.bbb import sign_query
from bbblb.services.tenants import TenantCache
from bbblb.settings import BBBLBConfig
from conftest import BBBTestClient, TestClient
import lxml.etree
from unittest.mock import MagicMock
import bbblb.web.bbbapi
from bbblb import model
from bbblb.services import ServiceRegistry
import bbblb.web.playback as bwp


@pytest.fixture(scope="function")
def config(config: BBBLBConfig):
    config.PROTECTED_RECORDINGS = True
    yield config


async def test_ticket(client: TestClient, orm: model.AsyncSession):
    tenant = model.Tenant(name="test", realm="localhost", secret="1234")
    recording = model.Recording(
        tenant=tenant,
        record_id="1234567890123456789012345678901234567890-1775488952",
        external_id="foo",
        state=model.RecordingState.PUBLISHED,
        started=model.utcnow(),
        ended=model.utcnow() + timedelta(minutes=5),
    )
    ticket = model.ViewTicket.create(recording=recording, lifetime=timedelta(minutes=5))
    orm.add_all([tenant, recording, ticket])
    await orm.commit()

    # Fresh ticket
    assert not ticket.consumed
    assert not ticket.is_expired()

    # Consume ticket
    await ticket.consume(orm, commit=True)
    await orm.refresh(ticket)
    assert ticket.consumed
    assert not ticket.is_expired()

    # Expire ticket
    ticket.expire -= timedelta(minutes=5)
    await orm.commit()
    await orm.refresh(ticket)
    assert ticket.is_expired()

    # Ticket cleanup
    await orm.execute(model.ViewTicket.delete_expired())
    assert (await orm.get(model.ViewTicket, ticket.uuid)) is None


async def test_get_recordings_protected(
    client: BBBTestClient, orm: model.AsyncSession, config: BBBLBConfig
):

    tenant = model.Tenant(name="test", realm="localhost", secret="1234")
    recording = model.Recording(
        tenant=tenant,
        record_id="1234567890123456789012345678901234567890-1775488952000",
        external_id="foo",
        state=model.RecordingState.PUBLISHED,
        started=model.utcnow(),
        ended=model.utcnow() + timedelta(minutes=5),
    )
    playback_link = f"https://localhost/playback/presentation/2.3/{recording.record_id}"
    playback = model.PlaybackFormat(
        recording=recording,
        format="presentation",
        xml=f"""  <playback>
    <format>presentation</format>
    <link>{playback_link}</link>
    <processing_time>56734</processing_time>
    <duration>196310</duration>
    <extensions>
      <preview>
        <images>
          <image width="176" height="136" alt="Test Preview">{playback_link}/presentation/0be1d53b8cc27d94d621b06d9171f0b35e6c0dad-1645733919254/thumbnails/thumb-1.png</image>
        </images>
      </preview>
    </extensions>
    <size>2743009</size>
  </playback>""",
    )
    orm.add_all([tenant, recording, playback])
    await orm.commit()

    # Fetch recordings without protection
    rs, xml = client.bbb_api_request_xml(tenant, "getRecordings")
    assert xml.findtext("recordings/recording/protected") == "false"
    link = xml.findtext("recordings/recording/playback/format/url")
    assert link == playback_link

    # Protect recording
    rs = client.bbb_api_request(
        tenant, "updateRecordings", recordID=recording.record_id, protect="true"
    )
    assert rs.is_success
    await orm.refresh(recording)
    assert recording.protected

    # Fetch recording again, this time it should be protected and return a ticket link
    rs, xml = client.bbb_api_request_xml(tenant, "getRecordings")
    assert xml.findtext("recordings/recording/protected") == "true"
    ticket_link = xml.findtext("recordings/recording/playback/format/url")
    assert ticket_link
    assert ticket_link.startswith("https://localhost/bbblb/api/v1/recording/ticket/")
    ticket_id, ticket_suffix = ticket_link.partition("/ticket/")[2].split("/", 1)
    assert ticket_suffix == f"playback/presentation/2.3/{recording.record_id}"

    # Check ticket, it should be not expired or consumed
    ticket = await orm.get(model.ViewTicket, uuid.UUID(ticket_id))
    assert ticket
    assert not ticket.consumed
    assert not ticket.is_expired()

    # Consume ticket
    rs = client.get(ticket_link[17:], follow_redirects=False)
    assert rs.has_redirect_location
    assert rs.headers["Location"] == playback_link

    # Ticket should now be consumed
    await orm.refresh(ticket)
    assert ticket.consumed

    # Cookie should be present and valid
    cookie_name = bwp.PRT_COOKIE_PREFIX + recording.record_id
    cookie = rs.cookies[cookie_name]
    assert cookie
    assert bwp.verify_prt_cookie(cookie, recording.record_id, config)

    # Accessing the ticket link again (with cookie) should succeed
    rs = client.get(ticket_link[17:], follow_redirects=False)
    assert rs.is_redirect

    # Accessing the ticket link without cookie should fail
    client.cookies.clear()
    rs = client.get(ticket_link[17:], follow_redirects=False)
    assert rs.is_client_error
