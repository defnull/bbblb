import asyncio
from contextlib import asynccontextmanager
import enum
import logging
import os
from pathlib import Path
import secrets
import socket
import typing
from uuid import UUID
import asyncpg
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine


import datetime
from typing import List

from sqlalchemy import (
    JSON,
    ColumnExpressionArgument,
    DateTime,
    ForeignKey,
    Integer,
    Select,
    Text,
    TypeDecorator,
    UniqueConstraint,
    delete,
    insert,
    update,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    AsyncConnection,
    AsyncSessionTransaction,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
from sqlalchemy.exc import (
    NoResultFound,  # noqa: F401
    IntegrityError,  # noqa: F401
    OperationalError,  # noqa: F401
    ProgrammingError,  # noqa: F401
)


from bbblb import migrations

LOG = logging.getLogger(__name__)

P = typing.ParamSpec("P")
R = typing.TypeVar("R")

PROCESS_IDENTITY = f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(4)}"


def utcnow():
    return datetime.datetime.now(tz=datetime.timezone.utc)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


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


async def init_engine(db_url: str, echo=False, create=False, migrate=False):
    global _engine, _sessionmaker

    if _engine or _sessionmaker:
        raise RuntimeError("Database engine already initialized")

    try:
        if create:
            await create_database(db_url, echo)

        current, target = await check_migration_state(db_url, echo)
        if current != target and migrate:
            await migrate_db(db_url, echo)
        elif current != target:
            LOG.error(f"Expected schema revision {target!r} but found {current!r}.")
            raise RuntimeError("Database migrations pending. Run migrations first.")

        _engine = create_async_engine(_async_db_url(db_url), echo=echo)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    except ConnectionRefusedError as e:
        raise RuntimeError(f"Failed to connect to database: {e}")
    except BaseException as e:
        raise RuntimeError(f"Failed to initialize database: {e}")


async def create_database(db_url, echo=False):
    db_url = _async_db_url(db_url)
    db_name = db_url.database
    if "postgres" not in db_url.drivername:
        return

    tmp_engine = create_async_engine(
        db_url.set(database="postgres"),
        poolclass=sqlalchemy.pool.NullPool,
        isolation_level="AUTOCOMMIT",
        echo=echo,
    )
    try:
        async with tmp_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname=:dbname"),
                {"dbname": db_name},
            )

            if not result.first():
                LOG.info(f"Creating missing database: {db_name}")
                await conn.execute(
                    text(f"CREATE DATABASE {db_name} WITH ENCODING 'utf-8'")
                )
    except ProgrammingError as e:
        while getattr(e, "__cause__", None):
            e = e.__cause__
        if not isinstance(e, asyncpg.exceptions.DuplicateDatabaseError):
            raise e
    finally:
        await tmp_engine.dispose()


async def check_migration_state(db_url, echo=False):
    import alembic
    import alembic.script
    import alembic.migration

    def check(conn):
        script_dir = Path(migrations.__file__).parent
        script = alembic.script.ScriptDirectory(script_dir)
        context = alembic.migration.MigrationContext.configure(conn)
        return context.get_current_revision(), script.get_current_head()

    engine = create_async_engine(
        _async_db_url(db_url), poolclass=sqlalchemy.pool.NullPool, echo=echo
    )

    async with engine.connect() as conn:
        return await conn.run_sync(check)


async def migrate_db(db_url, echo=False):
    return await asyncio.to_thread(migrate_db_sync, db_url, echo)


def migrate_db_sync(db_url, echo=False):
    import alembic
    import alembic.config
    import alembic.command

    db_url = _sync_db_url(db_url).render_as_string(hide_password=False)
    alembic_dir = Path(migrations.__file__).parent
    alembic_cfg = alembic.config.Config()
    alembic_cfg.set_main_option("script_location", str(alembic_dir))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.set_main_option("sqlalchemy.echo", str(echo))
    alembic.command.upgrade(alembic_cfg, "heads")


async def dispose_engine():
    global _engine, _sessionmaker
    if _engine:
        await _engine.dispose()
        _engine = _sessionmaker = None


def session() -> AsyncSession:
    if not _sessionmaker:
        raise RuntimeError("Database engine not initialized")
    return _sessionmaker()


@asynccontextmanager
async def begin() -> typing.AsyncIterator[AsyncSessionTransaction]:
    async with session() as sess, sess.begin() as tx:
        yield tx


@asynccontextmanager
async def connect() -> typing.AsyncIterator[AsyncConnection]:
    if not _engine:
        raise RuntimeError("Database engine not initialized")
    async with _engine.begin() as conn:
        yield conn


async def get_or_create(
    session: AsyncSession,
    select: Select[typing.Tuple[R]],
    create: typing.Callable[[], R],
) -> tuple[R, bool]:
    """Get or create an entity. Returns the entity and a boolean singaling if
    the entity was created. The session is committed to make sure the object
    could really be created.

    The function first tries to fetch the model with the `select` statement.
    If there is no result, it calls the `create` callable and tries to
    ass the entity and commit the session. If that fails with an IntegrityError,
    we assume someone else created the entity in the meantime. We fetch and
    return it.

    The select statement should return the created entity, or the function
    will throw NoResultFound during the second attempt to fetch the entity.
    """
    model = (await session.execute(select)).scalar_one_or_none()
    if model:
        return model, False
    model = create()
    session.add(model)
    try:
        await session.commit()
        return model, True
    except IntegrityError:
        await session.rollback()
        return (await session.execute(select)).scalar_one(), False


class NewlineSeparatedList(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: List[str] | None, dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return "\n".join(value)
        raise TypeError("Must be a list or tuple of strings")

    def process_result_value(self, value: str | None, dialect) -> List[str] | None:
        if value is None:
            return None
        return value.split("\n")


class IntEnum(TypeDecorator):
    impl = Integer  # Store as an Integer in the database
    cache_ok = True

    def __init__(self, enum_type: type[enum.Enum]):
        super().__init__()
        self.enum_type = enum_type

    def process_bind_param(self, value: enum.Enum | None, dialect):
        if value is None:
            return None
        if not isinstance(value, self.enum_type):
            raise TypeError(f"Value must be an instance of {self.enum_type}")
        return value.value

    def process_result_value(self, value: int | None, dialect):
        if value is None:
            return None
        try:
            return self.enum_type(value)
        except ValueError:
            # Handle cases where the integer from the DB doesn't match an enum member
            # You might want to log this or raise a more specific error
            return None


class ORMMixin:
    @classmethod
    def select(cls, *a, **filter):
        stmt = select(cls)
        if a:
            stmt = stmt.filter(*a)
        if filter:
            stmt = stmt.filter_by(**filter)
        return stmt

    @classmethod
    def update(cls, where: ColumnExpressionArgument[bool], *more_where):
        return update(cls).where(where, *more_where)

    @classmethod
    def delete(cls, where: ColumnExpressionArgument[bool], *more_where):
        return delete(cls).where(where, *more_where)

    @classmethod
    async def get(cls, session: AsyncSession, *a, **filter):
        return (await session.execute(cls.select(*a, **filter))).scalar_one()

    @classmethod
    async def find(cls, session: AsyncSession, *a, **filter):
        return (
            await session.execute(cls.select(*a, **filter).limit(1))
        ).scalar_one_or_none()


class Base(ORMMixin, AsyncAttrs, DeclarativeBase):
    __abstract__ = True

    type_annotation_map = {
        list[str]: NewlineSeparatedList,
    }

    def __str__(self):
        return f"{self.__class__.__name__}({getattr(self, 'id', None)})"


class Lock(Base):
    __tablename__ = "locks"
    name: Mapped[str] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(nullable=False)
    ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), insert_default=utcnow, onupdate=utcnow, nullable=False
    )

    @classmethod
    async def try_acquire(cls, name, force_release: datetime.timedelta | None = None):
        """Try to acquire a named inter-process lock and force-release any
        existing locks if it they are older than the force_release time limit.

        This is not re-entrant. Acquiring the same lock twice will fail.
        """
        async with connect() as conn:
            if force_release:
                expire = utcnow() - force_release
                await conn.execute(
                    delete(cls).where(Lock.name == name, Lock.ts < expire)
                )
            try:
                await conn.execute(
                    insert(cls).values(name=name, owner=PROCESS_IDENTITY)
                )
                await conn.commit()
                LOG.debug(f"Lock {name!r} acquired by {PROCESS_IDENTITY}")
                return True
            except IntegrityError:
                await conn.rollback()
                return False

    @classmethod
    async def check(cls, name):
        """Update the lifetime of an already held lock, return true if such a
        lock exists, false otherwise."""
        async with connect() as conn:
            result = await conn.execute(
                update(cls)
                .values(ts=utcnow())
                .where(Lock.name == name, Lock.owner == PROCESS_IDENTITY)
            )
            if result.rowcount > 0:
                LOG.debug(f"Lock {name!r} updated by {PROCESS_IDENTITY}")
                return True
            return False

    @classmethod
    async def try_release(cls, name):
        """Release a named inter-process lock if it's owned by the current
        process. Return true if such a lock existed, false otherwise."""
        async with connect() as conn:
            result = await conn.execute(
                delete(cls).where(Lock.name == name, Lock.owner == PROCESS_IDENTITY)
            )
            if result.rowcount > 0:
                LOG.debug(f"Lock {name!r} released by {PROCESS_IDENTITY}")
                return True
            return False

    def __str__(self):
        return f"Lock({self.name})"


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    realm: Mapped[str] = mapped_column(unique=True, nullable=False)
    secret: Mapped[str] = mapped_column(unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    meetings: Mapped[list["Meeting"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    recordings: Mapped[list["Recording"]] = relationship(back_populates="tenant")

    def __str__(self):
        return f"Tenant({self.name})"


class ServerHealth(enum.Enum):
    #: All fine, this server will get new meetings.
    AVAILABLE = 0
    #: Does not get new meetings, but existing meetings are sill served
    UNSTABLE = 1
    #: Existing meetings are considered 'Zombies' and forgotten.
    OFFLINE = 2


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(unique=True, nullable=False)
    secret: Mapped[str] = mapped_column(nullable=False)

    #: New meetings are only created on enabled servers
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    #: New meetings are only created on AVAILABLE servers
    health: Mapped[ServerHealth] = mapped_column(
        IntEnum(ServerHealth), nullable=False, default=ServerHealth.OFFLINE
    )
    errors: Mapped[int] = mapped_column(nullable=False, default=0)
    recover: Mapped[int] = mapped_column(nullable=False, default=0)

    load: Mapped[float] = mapped_column(nullable=False, default=0.0)

    meetings: Mapped[list["Meeting"]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )

    @classmethod
    def select_available(cls, tenant: Tenant):
        # TODO: Filter by tenant
        stmt = cls.select(enabled=True, health=ServerHealth.AVAILABLE)
        return stmt

    @classmethod
    def select_best(cls, tenant: Tenant):
        return cls.select_available(tenant).order_by(Server.load.desc()).limit(1)

    def increment_load_stmt(self, load: float):
        return (
            update(Server).where(Server.id == self.id).values(load=Server.load + load)
        )
    
    def mark_error(self, fail_threshold:int):
        if self.health == ServerHealth.OFFLINE:
            pass  # Already dead
        elif self.errors < fail_threshold:
            # Server is failing
            self.recover = 0  # Reset recovery counter
            self.errors += 1
            self.health = ServerHealth.UNSTABLE
            LOG.warning(
                f"Server {self.domain} is UNSTABLE and failing ({self.errors}/{fail_threshold})"
            )
        else:
            # Server failed too often, give up
            self.health = ServerHealth.OFFLINE
            LOG.warning(f"Server {self.domain} is OFFLINE")

    def mark_success(self, recover_threshold:int):
        if self.health == ServerHealth.AVAILABLE:
            pass  # Already healthy
        elif self.recover < recover_threshold:
            # Server is still recovering
            self.recover += 1
            self.health = ServerHealth.UNSTABLE
            LOG.warning(
                f"Server {self.domain} is UNSTABLE and recovering ({self.recover}/{recover_threshold})"
            )
        else:
            # Server fully recovered
            self.errors = 0
            self.recover = 0
            self.health = ServerHealth.AVAILABLE
            LOG.info(f"Server {self.domain} is ONLINE")


    @property
    def api_base(self):
        return f"https://{self.domain}/bigbluebutton/api/"

    def __str__(self):
        return f"Server({self.domain})"


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        UniqueConstraint("external_id", "tenant_fk", name="meeting_tenant_uc"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The external meetingID. Unscoped, as provided by the front-end.
    external_id: Mapped[str] = mapped_column(nullable=False)
    internal_id: Mapped[str] = mapped_column(unique=True, nullable=True)
    uuid: Mapped[UUID] = mapped_column(unique=True, nullable=False)

    tenant_fk: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    tenant: Mapped["Tenant"] = relationship(lazy=False)
    server_fk: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    server: Mapped["Server"] = relationship(lazy=False)

    created: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), insert_default=utcnow, nullable=False
    )
    modified: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), insert_default=utcnow, onupdate=utcnow, nullable=False
    )

    def __str__(self):
        return f"Meeting({self.external_id}')"


CALLBACK_TYPE_END = "END"
CALLBACK_TYPE_REC = "REC"


class Callback(Base):
    """Callbacks and their (optional) forward URL."""

    __tablename__ = "callbacks"
    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[UUID] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(nullable=False)

    tenant_fk: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    tenant: Mapped["Tenant"] = relationship(lazy=False)
    server_fk: Mapped[int] = mapped_column(ForeignKey("servers.id"), nullable=False)
    server: Mapped["Server"] = relationship(lazy=False)

    #: Original callback URL (optional)
    forward: Mapped[str] = mapped_column(nullable=True)

    #: TODO: Delete very old callbacks on startup
    created: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), insert_default=utcnow, nullable=False
    )


class RecordingState(enum.StrEnum):
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Recordings are not removed if the tenant is deleted, they stay as orphans.
    tenant_fk: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    tenant: Mapped["Tenant"] = relationship(back_populates="recordings", lazy=False)

    record_id: Mapped[str] = mapped_column(unique=True, nullable=False)
    external_id: Mapped[str] = mapped_column(nullable=False)
    state: Mapped[RecordingState] = mapped_column(nullable=False)

    meta: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default={})
    formats: Mapped[list["PlaybackFormat"]] = relationship(
        back_populates="recording", cascade="all, delete-orphan"
    )

    # Non-essential but nice to have attributes
    started: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    participants: Mapped[int] = mapped_column(nullable=False, default=0)

    def __str__(self):
        return f"Recording({self.record_id}')"


class PlaybackFormat(Base):
    __tablename__ = "playback"
    __table_args__ = (
        UniqueConstraint("recording_fk", "format", name="unique_playback_rcf"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    recording_fk: Mapped[int] = mapped_column(
        ForeignKey("recordings.id"), nullable=False
    )
    recording: Mapped[Recording] = relationship(back_populates="formats")
    format: Mapped[str] = mapped_column(nullable=False)

    # We need this for getMeetings search results, so store it ...
    xml: Mapped[str] = mapped_column(nullable=False)


# class Task(Base):
#     __tablename__ = "tasks"
#     id: Mapped[int] = mapped_column(primary_key=True)
#     name: Mapped[str] = mapped_column(unique=True, nullable=False)

#     created: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), insert_default=utcnow, nullable=False)
#     modified: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), insert_default=utcnow, onupdate=utcnow, nullable=False)
#     completed: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=True)


# class RecordingMeta(Base):
#     __tablename__ = "recording_meta"
#     __table_args__ = (
#         UniqueConstraint("recording_fk", "name", name="_recording_fk_meta_name_uc"),
#     )

#     id: Mapped[int] = mapped_column(primary_key=True)
#     recording_fk: Mapped[int] = mapped_column(
#         ForeignKey("recordings.id"), nullable=False
#     )
#     name: Mapped[str] = mapped_column(nullable=False)
#     value: Mapped[str] = mapped_column(nullable=False)

#     recording = relationship("Recording", back_populates="meta")
