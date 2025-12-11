import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import typing
import asyncpg
import sqlalchemy
import sqlalchemy.exc
import sqlalchemy.event
from sqlalchemy.ext.asyncio import create_async_engine


from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    AsyncConnection,
    AsyncSessionTransaction,
)


from bbblb import migrations
from bbblb.services import Health, HealthReportingMixin, ManagedService

LOG = logging.getLogger(__name__)


class DBContext(ManagedService, HealthReportingMixin):
    engine: AsyncEngine | None = None
    sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def __init__(self, db_url: str, create=False, migrate=False):
        self._db_url = db_url
        self._create = create
        self._migrate = migrate

    async def on_start(self):
        if self.engine or self.sessionmaker:
            raise RuntimeError("Database engine already initialized")

        try:
            if self._create:
                await create_database(self._db_url)

            current, target = await check_migration_state(self._db_url)
            if current != target and self._migrate:
                await migrate_db(self._db_url)
            elif current != target:
                LOG.error(f"Expected schema revision {target!r} but found {current!r}.")
                raise RuntimeError("Database migrations pending. Run migrations first.")

            try:
                self.engine = create_async_engine(_async_db_url(self._db_url))
                self.sessionmaker = async_sessionmaker(
                    self.engine, expire_on_commit=False
                )

                # Enable foreign key support in sqlite
                if "sqlite" in self.engine.url.drivername:

                    @sqlalchemy.event.listens_for(self.engine.sync_engine, "connect")
                    def _fk_pragma_on_connect(conn, con_record):  # noqa
                        conn.execute("pragma foreign_keys=ON")

            except BaseException:
                self.engine = self.sessionmaker = None
                raise

        except ConnectionRefusedError as e:
            raise RuntimeError(f"Failed to connect to database: {e}")
        except BaseException as e:
            raise RuntimeError(f"Failed to initialize database: {e}")

    async def check_health(self) -> tuple[Health, str]:
        if not self.engine:
            return Health.UNKNOWN, "Not connected"
        try:
            async with self.engine.connect() as conn:
                await conn.execute(sqlalchemy.text("SELECT 'health check'"))
            return Health.OK, "Connection established"
        except BaseException as exc:
            return Health.CRITICAL, f"ERROR: {exc}"

    async def on_shutdown(self):
        if self.engine:
            await self.engine.dispose()
            self.engine = self.sessionmaker = None

    def session(self) -> AsyncSession:
        if not self.sessionmaker:
            raise RuntimeError("Database engine not initialized")
        return self.sessionmaker()

    @asynccontextmanager
    async def begin(self) -> typing.AsyncIterator[AsyncSessionTransaction]:
        async with self.session() as sess, sess.begin() as tx:
            yield tx

    @asynccontextmanager
    async def connect(self) -> typing.AsyncIterator[AsyncConnection]:
        """Create a connection and begin a transaction."""
        if not self.engine:
            raise RuntimeError("Database engine not initialized")
        async with self.engine.begin() as conn:
            yield conn


def _async_db_url(db_url) -> sqlalchemy.engine.url.URL:
    if isinstance(db_url, str):
        db_url = sqlalchemy.engine.url.make_url(db_url)
    if db_url.drivername == "sqlite":
        return db_url.set(drivername="sqlite+aiosqlite")
    elif db_url.drivername == "postgresql":
        return db_url.set(drivername="postgresql+asyncpg")
    else:
        raise ValueError(
            f"Unsupported database driver name: {db_url} (must be sqlite:// or postgresql://)"
        )


def _sync_db_url(db_url) -> sqlalchemy.engine.url.URL:
    if isinstance(db_url, str):
        db_url = sqlalchemy.engine.url.make_url(db_url)
    if db_url.drivername == "sqlite":
        return db_url
    elif db_url.drivername == "postgresql":
        return db_url.set(drivername="postgresql+psycopg")
    else:
        raise ValueError(
            f"Unsupported database driver name: {db_url} (must be sqlite:// or postgresql://)"
        )


async def create_database(db_url):
    db_url = _async_db_url(db_url)
    db_name = db_url.database
    if "postgres" not in db_url.drivername:
        return

    tmp_engine = create_async_engine(
        db_url.set(database="postgres"),
        poolclass=sqlalchemy.pool.NullPool,
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with tmp_engine.connect() as conn:
            result = await conn.execute(
                sqlalchemy.text("SELECT 1 FROM pg_database WHERE datname=:dbname"),
                {"dbname": db_name},
            )

            if not result.first():
                LOG.info(f"Creating missing database: {db_name}")
                await conn.execute(
                    sqlalchemy.text(f"CREATE DATABASE {db_name} WITH ENCODING 'utf-8'")
                )
    except sqlalchemy.exc.ProgrammingError as e:
        while cause := getattr(e, "__cause__", None):
            e = cause
        if not isinstance(e, asyncpg.exceptions.DuplicateDatabaseError):
            raise e
    finally:
        await tmp_engine.dispose()


async def check_migration_state(db_url):
    import alembic
    import alembic.script
    import alembic.migration

    def check(conn):
        script_dir = Path(migrations.__file__).parent
        script = alembic.script.ScriptDirectory(script_dir)
        context = alembic.migration.MigrationContext.configure(conn)
        return context.get_current_revision(), script.get_current_head()

    engine = create_async_engine(
        _async_db_url(db_url), poolclass=sqlalchemy.pool.NullPool
    )

    async with engine.connect() as conn:
        return await conn.run_sync(check)


async def migrate_db(db_url):
    return await asyncio.to_thread(migrate_db_sync, db_url)


def migrate_db_sync(db_url):
    import alembic
    import alembic.config
    import alembic.command

    db_url = _sync_db_url(db_url).render_as_string(hide_password=False)
    alembic_dir = Path(migrations.__file__).parent
    alembic_cfg = alembic.config.Config()
    alembic_cfg.set_main_option("script_location", str(alembic_dir))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic.command.upgrade(alembic_cfg, "heads")
