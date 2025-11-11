import asyncio
import contextvars
import enum
import functools
import logging
import os
import random
import secrets
import socket
import typing
import uuid
from uuid import UUID
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine
from contextlib import asynccontextmanager


import datetime
from typing import List

from sqlalchemy import (
    JSON,
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine
from sqlalchemy.ext.asyncio import async_scoped_session
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
from sqlalchemy.exc import NoResultFound, IntegrityError, OperationalError  # noqa: F401

LOG = logging.getLogger(__name__)

P = typing.ParamSpec("P")
R = typing.TypeVar("R")

PROCESS_IDENTITY = f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(4)}"


def utcnow():
    return datetime.datetime.now(tz=datetime.timezone.utc)


async_engine: AsyncEngine
AsyncSessionMaker: async_sessionmaker[AsyncSession]
ScopedSession: async_scoped_session[AsyncSession]


async def init_engine(db: str, echo=False):
    global async_engine, AsyncSessionMaker, ScopedSession

    if db.startswith("sqlite://"):
        db = db.replace("sqlite://", "sqlite+aiosqlite://")
    elif db.startswith("postgresql://"):
        db = db.replace("postgresql://", "postgresql+asyncpg://")
    else:
        raise ValueError(
            f"Unsupported database dialect: {db} (must be sqlite:// or postgresql://)"
        )

    async_engine = create_async_engine(db, echo=echo)
    AsyncSessionMaker = async_sessionmaker(async_engine, expire_on_commit=False)

    ScopedSession = async_scoped_session(
        AsyncSessionMaker,
        scopefunc=get_db_scope_id,
    )

    if "postgres" in async_engine.url.drivername:
        dbname = async_engine.url.database
        tmp_engine = create_async_engine(
            async_engine.url._replace(database="postgres"), isolation_level="AUTOCOMMIT"
        )
        async with tmp_engine.connect() as conn:
            result = await conn.execute(
                sqlalchemy.text("SELECT datname FROM pg_database")
            )
            if dbname not in [row[0] for row in result]:
                LOG.info("Database not found, trying to create it: {dbname}")
                await conn.execute(
                    sqlalchemy.text(f"CREATE DATABASE {dbname} ENCODING 'utf-8'")
                )
        await tmp_engine.dispose()

    async with async_engine.begin() as conn:
        # Creating tables is not transactional in some databases, so we just try
        # our luck and if that fails, we sleep a couple of ms and try again.
        try:
            await conn.run_sync(Base.metadata.create_all)
        except OperationalError:
            sleep = random.randint(100, 200)
            LOG.warning(f"Failed to create tables. Trying again in {sleep}ms")
            await asyncio.sleep(sleep)
            await conn.run_sync(Base.metadata.create_all)


async def dispose_engine():
    if async_engine:
        await async_engine.dispose()


db_scope: contextvars.ContextVar[typing.Optional[str]] = contextvars.ContextVar(
    "db_scope", default=None
)


def get_db_scope_id():
    scope_id = db_scope.get()
    if not scope_id:
        raise RuntimeError("Trying to use a scoped session without an active scope")
    return scope_id


AsyncCallable = typing.Callable[..., typing.Awaitable]


@asynccontextmanager
async def scope(begin=False, isolated=False, autocommit=False):
    """Create a context-bound session scope if needed and return the
    :cls:`AsyncSession` currently in scope. You can also access the 'current'
    session via the :data:`ScopedSession` proxy.

    The scoped session is bound to the current 'context' (async task or thread)
    and carries over to tasks created from the current one. Opening a nested
    scope will re-use the existing session, if present.

    Set `isolated` to `true` if you need a fresh session and do not want to
    inherit the session scope from the parent task. This is usefull for
    background tasks that need to run independently from the task that started
    them.

    :cls:`AsyncSession` will lazily start a transaction as soon as it is first
    used. The session will be closed automatically once you exit the outermost
    scope for the session.

    Note that closing a session does not commit its state. Set `autocommit`
    to `True` to trigger an automatic commit of the wrapped code did not raise
    an exception.

    Set `begin` to `true` to wrap a nested scope in an explicit (nested)
    transaction, which will commit after the nested scope ends, or rolled
    back on errors.

    """

    token = None
    if not db_scope.get() or isolated:
        scope = str(uuid.uuid4())
        LOG.debug(
            f"Creating session scope {scope} ({len(ScopedSession.registry.registry) + 1} total)"
        )
        token = db_scope.set(scope)

    session = ScopedSession()
    try:
        if begin and session.in_transaction():
            async with ScopedSession.begin_nested() as tx:
                yield session
                if autocommit and tx.is_active:
                    await tx.commit()
        elif begin:
            async with ScopedSession.begin() as tx:
                yield session
                if autocommit and tx.is_active:
                    await tx.commit()
        else:
            yield session
            if autocommit:
                await session.commit()
    finally:
        if token:
            try:
                await ScopedSession.remove()
            finally:
                db_scope.reset(token)


def transactional(begin=False, isolated=False, autocommit=False):
    """Wrapping an async callable into a :func:`scope` context."""

    def decorator(func: AsyncCallable) -> AsyncCallable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            async with scope(begin=begin, isolated=isolated, autocommit=autocommit):
                return await func(*args, **kwargs)

        return wrapper

    return decorator


async def get_or_create(
    session: AsyncSession,
    select: Select[typing.Tuple[R]],
    create: typing.Callable[[], R],
) -> tuple[R, bool]:
    """Get or create an entity. Returns the entity and a boolean singaling if
    the entity was created.

    The function first tries to fetch the model with the `select` statement.
    If there is no result, it calls the `create` callable and tries to
    commit the returned entity. If that fails with an IntegrityError, we try
    to fetch the entity again and return it.

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


class ScopedORMMixin:
    @classmethod
    def select(cls, *a, **filter):
        stmt = select(cls)
        if a:
            stmt = stmt.filter(*a)
        if filter:
            stmt = stmt.filter_by(**filter)
        return stmt

    @classmethod
    async def get(cls, *a, **filter):
        return (await ScopedSession.execute(cls.select(*a, **filter))).scalar_one()

    @classmethod
    async def find(cls, *a, **filter):
        return (
            await ScopedSession.execute(cls.select(*a, **filter).limit(1))
        ).scalar_one_or_none()

    async def delete(self):
        await ScopedSession.delete(self)

    async def save(self, now=False):
        ScopedSession.add(self)
        if now:
            await ScopedSession.flush([self])


class Base(ScopedORMMixin, AsyncAttrs, DeclarativeBase):
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
        async with async_engine.begin() as conn:
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
        async with async_engine.begin() as conn:
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
        async with async_engine.begin() as conn:
            result = await conn.execute(
                delete(cls).where(Lock.name == name, Lock.owner == PROCESS_IDENTITY)
            )
            if result.rowcount > 0:
                LOG.debug(f"Lock {name!r} released by {PROCESS_IDENTITY}")
                return True
            return False

    def __str__(self):
        return f"Lock({self.name}')"


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    realm: Mapped[str] = mapped_column(unique=True, nullable=False)
    secret: Mapped[str] = mapped_column(unique=True, nullable=False)

    meetings: Mapped[list["Meeting"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    recordings: Mapped[list["Recording"]] = relationship(back_populates="tenant")

    def __str__(self):
        return f"Tenant({self.name}')"


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
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False)

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
        stmt = stmt.order_by(Server.load.desc())
        return stmt

    @property
    def api_base(self):
        return f"https://{self.domain}/bigbluebutton/api/"

    def __str__(self):
        return f"Server({self.domain}')"


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
