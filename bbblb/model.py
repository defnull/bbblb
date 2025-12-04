import enum
import logging
import typing
from uuid import UUID

import datetime
from typing import List

from sqlalchemy import (
    JSON,
    ColumnExpressionArgument,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Select,
    Text,
    TypeDecorator,
    UniqueConstraint,
    select,  # noqa: F401
    delete,  # noqa: F401
    update,  # noqa: F401
    insert,  # noqa: F401
)
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession

from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    validates,
)

from sqlalchemy.exc import (
    NoResultFound,  # noqa: F401
    IntegrityError,  # noqa: F401
    OperationalError,  # noqa: F401
    ProgrammingError,  # noqa: F401
)


LOG = logging.getLogger(__name__)

P = typing.ParamSpec("P")
R = typing.TypeVar("R")


def utcnow():
    return datetime.datetime.now(tz=datetime.timezone.utc)


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
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )

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

    def __str__(self):
        return f"Lock({self.name})"


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    realm: Mapped[str] = mapped_column(unique=True, nullable=False)
    secret: Mapped[str] = mapped_column(unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    # Default values or overrides for create call parameters.
    # The key should match a BBB create call parameter. The value must start with a control flag:
    # '=' -> Enforce a value for this parameter. If empty, then remove this parameter.
    # '?' -> Set parameter only if missing.
    # '<' -> Cap a numeric parameter (e.g. 'duration' or 'maxParticipants') to a maximum value.
    #        Override parameter if is is missing, below or equal to zero (which means 'unlimited'),
    #        or larger than the desired value.
    # '+' -> Add values to a comma separated list (e.g. disabledFeatures)
    overrides: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    meetings: Mapped[list["Meeting"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    recordings: Mapped[list["Recording"]] = relationship(back_populates="tenant")

    @validates("overrides")
    def validate_overrides(self, key, params):
        if not isinstance(params, (dict)):
            raise TypeError(f"Tenant.{key} must be a dict")
        for key, value in params.items():
            if not key:
                raise TypeError(f"Tenant.{key} keys must be non-empty strings")
            if not value or not isinstance(value, str):
                raise TypeError(f"Tenant.{key} values must be non-empty strings")
            if value[0] not in "=?<+":
                raise TypeError(
                    "Tenant.{key} values must start with a valid action flag"
                )
        return params

    def clear_overrides(self):
        self.overrides = {}

    def add_override(
        self, name: str, operator: typing.Literal["=", "?", "<", "+"], value: str
    ):
        self.overrides = {**self.overrides, name: operator + value}

    def remove_override(self, name: str):
        self.overrides = {k: v for k, v in self.overrides.items() if k != name}

    def apply_overrides(self, params):
        for name, value in self.overrides.items():
            action, value = value[0], value[1:]
            if action == "=":
                params[name] = value
            elif action == "?":
                params.setdefault(name, value)
            elif action == "<":
                try:
                    orig = int(params[name])
                except (ValueError, KeyError):
                    orig = -1
                if orig <= 0 or orig > int(value):
                    params[name] = value
            elif action == "+":
                params = params.get("key", "").split(",")
                for add in value.split():
                    if add not in params:
                        params.append(add)
                params[name] = ",".join(filter(None, params))
            else:
                LOG.warning(
                    f"{self} has bad create setting: {name} = {action + value!r}"
                )

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
        IntEnum(ServerHealth), nullable=False, default=ServerHealth.UNSTABLE
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

    def mark_error(self, fail_threshold: int):
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

    def mark_success(self, recover_threshold: int):
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
    __table_args__ = (UniqueConstraint("external_id", "tenant_fk"),)

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
        back_populates="recording", cascade="all, delete-orphan", passive_deletes=True
    )

    # Non-essential but nice to have attributes
    started: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    participants: Mapped[int] = mapped_column(nullable=False, default=0)

    @validates("meta")
    def validate_meta(self, key, meta):
        if not isinstance(meta, (dict)):
            raise TypeError(f"Recording.{key} must be a dict")
        for key, value in meta.items():
            if not key:
                raise TypeError(f"Recording.{key} keys must be non-empty strings")
            if not value or not isinstance(value, str):
                raise TypeError(f"Recording.{key} values must be non-empty strings")
        return meta

    def __str__(self):
        return f"Recording({self.record_id}')"


class PlaybackFormat(Base):
    __tablename__ = "playback"
    __table_args__ = (UniqueConstraint("recording_fk", "format"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    recording_fk: Mapped[int] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False
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
