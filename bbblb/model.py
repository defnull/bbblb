import contextvars
import enum
import functools
import logging
import typing
import uuid
from uuid import UUID
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
from sqlalchemy.exc import NoResultFound, IntegrityError  # noqa: F401

import bbblb.recordings

LOG = logging.getLogger(__name__)

P = typing.ParamSpec("P")
R = typing.TypeVar("R")


def utcnow():
    return datetime.datetime.now(tz=datetime.timezone.utc)


async_engine: AsyncEngine
AsyncSessionSession: async_sessionmaker[AsyncSession]
ScopedSession: async_scoped_session[AsyncSession]


async def init_engine(db: str, echo=False):
    global async_engine, AsyncSessionSession, ScopedSession
    async_engine = create_async_engine(db, echo=echo)
    AsyncSessionSession = async_sessionmaker(async_engine, expire_on_commit=False)

    ScopedSession = async_scoped_session(
        AsyncSessionSession,
        scopefunc=get_db_scope_id,
    )

    async with async_engine.begin() as conn:
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


class ServerState(enum.Enum):
    #: All fine, this will get new meetings
    ONLINE = 0
    #: Does not get new meetings, but existing meetings are sill served
    UNSTABLE = 1
    #: Existing meetings are considered 'Zombies' and forgotten.
    OFFLINE = 3
    #: Same as 'FAILING' but will also disable polling and health checks.
    DISABLED = 4


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(unique=True, nullable=False)
    secret: Mapped[str] = mapped_column(nullable=False)

    state: Mapped[ServerState] = mapped_column(
        IntEnum(ServerState), nullable=False, default=ServerState.DISABLED
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
        return cls.select(state=ServerState.ONLINE).order_by(Server.load.desc())

    @property
    def api_base(self):
        return f"https://{self.domain}/bigbluebutton/api/"

    def __str__(self):
        return f"Server({self.domain}')"


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The external meeting ID. Unscoped, as provided by the front-end.
    external_id: Mapped[str] = mapped_column(unique=True, nullable=False)
    internal_id: Mapped[str] = mapped_column(unique=True, nullable=True)
    uuid: Mapped[UUID] = mapped_column(unique=True, nullable=False)

    tenant_fk: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    tenant: Mapped["Tenant"] = relationship()
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

    def get_fixed_xml(self, root_tag: str):
        return bbblb.recordings.fix_playback_xml(self, root_tag)


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
