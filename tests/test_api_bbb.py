import pytest
import pytest_asyncio
from bbblb.lib.bbb import sign_query
from bbblb.services.tenants import TenantCache
from conftest import TestClient
import lxml.etree
from unittest.mock import MagicMock
import bbblb.web.bbbapi
from bbblb import model
from bbblb.services import ServiceRegistry


@pytest_asyncio.fixture(scope="function")
async def mock_request(config, services):
    mock = MagicMock(bbblb.web.Request)
    mock.app.state.config = config
    mock.app.state.services = services
    yield mock


def test_index(client: TestClient):
    response = client.get("/bigbluebutton/api")
    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]
    xml = lxml.etree.fromstring(response.content)
    assert xml.findtext("returncode") == "SUCCESS"


async def test_tenant_detection(
    mock_request: MagicMock, orm: model.AsyncSession, services: ServiceRegistry
):
    mock_request.headers.get.side_effect = dict(Host="foo.local").get

    async with bbblb.web.bbbapi.BBBApiRequest(mock_request) as ctx:
        with pytest.raises(bbblb.web.bbbapi.BBBError) as exc:
            await ctx.require_tenant()
        assert exc.value.error == "checksumError"

    foo_tenant = model.Tenant(name="foo", realm="foo.local", secret="meh")
    orm.add(foo_tenant)
    await orm.commit()

    await services.get(TenantCache).refresh_cache(force=True)

    async with bbblb.web.bbbapi.BBBApiRequest(mock_request) as ctx:
        tenant = await ctx.require_tenant()
        assert tenant.id == foo_tenant.id


async def test_parameter(mock_request: MagicMock, orm: model.AsyncSession):
    foo_tenant = model.Tenant(name="foo", realm="foo.local", secret="correct")
    orm.add(foo_tenant)
    await orm.commit()

    mock_request.headers.get.side_effect = dict(Host="foo.local").get
    mock_request.url.path = "/bigbluebutton/api/getMeetingInfo"
    mock_request.url.query = ""

    with pytest.raises(bbblb.web.bbbapi.BBBError) as exc:
        async with bbblb.web.bbbapi.BBBApiRequest(mock_request) as ctx:
            await ctx.require_param("meetingID")
    assert exc.value.error == "checksumError"

    mock_request.url.query = "checksum=foo"
    with pytest.raises(bbblb.web.bbbapi.BBBError) as exc:
        async with bbblb.web.bbbapi.BBBApiRequest(mock_request) as ctx:
            await ctx.require_param("meetingID")
    assert exc.value.error == "checksumError"

    mock_request.url.query = sign_query("getMeetingInfo", {}, secret="wrong")
    with pytest.raises(bbblb.web.bbbapi.BBBError) as exc:
        async with bbblb.web.bbbapi.BBBApiRequest(mock_request) as ctx:
            await ctx.require_param("meetingID")
    assert exc.value.error == "checksumError"

    mock_request.url.query = sign_query(
        "getMeetingInfo", {"stuff": "meh"}, secret="correct"
    )
    with pytest.raises(bbblb.web.bbbapi.BBBError) as exc:
        async with bbblb.web.bbbapi.BBBApiRequest(mock_request) as ctx:
            await ctx.require_param("meetingID")
    assert exc.value.error == "missingParameterMeetingID"

    mock_request.url.query = sign_query(
        "getMeetingInfo", {"meetingID": "1234"}, secret="correct"
    )
    async with bbblb.web.bbbapi.BBBApiRequest(mock_request) as ctx:
        result = await ctx.require_param("meetingID")
    assert result == "1234"
