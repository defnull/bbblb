import os
import pytest
import pytest_asyncio
import bbblb.model
import bbblb.settings
import bbblb.services
import bbblb.services.db
import bbblb.web

from starlette.testclient import TestClient

ENV_TEST_DB = "TEST_DB"
ENV_TEST_BBB = "TEST_BBB"


@pytest.fixture(scope="session")
def db_url(tmp_path_factory):
    db = os.environ.get(ENV_TEST_DB, None)
    if not db:
        # We cannot use in-memopry DBs because migrations create their
        # own short-lived engine.
        db = f"sqlite:///{tmp_path_factory.mktemp('db') / 'test.db'}"
    yield db


@pytest.fixture(scope="function")
def config(db_url, tmp_path):
    cfg = bbblb.settings.BBBLBConfig()
    cfg.set(
        DB=db_url,
        DEBUG=True,
        SECRET="1234",
        DOMAIN="localhost",
        PATH_DATA=tmp_path,
    )
    yield cfg


@pytest_asyncio.fixture(scope="function")
async def services(config: bbblb.settings.BBBLBConfig):
    services = await bbblb.services.bootstrap(config, autostart=False)
    async with services:
        yield services


@pytest_asyncio.fixture(scope="function")
async def db(services: bbblb.services.ServiceRegistry):
    db = await services.use(bbblb.services.db.DBContext)
    for table in reversed(bbblb.model.Base.metadata.sorted_tables):
        async with db.session() as session, session.begin():
            await session.execute(table.delete())
    yield db


@pytest_asyncio.fixture(scope="function")
async def orm(db: bbblb.services.db.DBContext):
    async with db.session() as session:
        yield session


@pytest.fixture(scope="function")
def client(config: bbblb.settings.BBBLBConfig):
    app = bbblb.web.make_app(config, autostart=False)
    with TestClient(app) as client:
        yield client


@pytest_asyncio.fixture(scope="function")
async def bbb_server(db: bbblb.services.db.DBContext):
    """Register an external BBB test server if available, or skip tests
    depending on it."""
    domain = os.environ.get(ENV_TEST_BBB)
    if not domain:
        pytest.skip(f"Test requires {ENV_TEST_BBB} environment variable.")
        return
    domain, _, secret = domain.rpartition(":")
    if not domain or not secret:
        pytest.fail(f"Invalid {ENV_TEST_BBB} environment variable.")

    async with db.begin() as tx:
        server = bbblb.model.Server(
            domain=domain, secret=secret, health=bbblb.model.ServerHealth.AVAILABLE
        )
        tx.session.add(server)
    yield server
