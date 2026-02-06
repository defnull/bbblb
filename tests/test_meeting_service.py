import uuid
from bbblb import model
from datetime import timedelta
from sqlalchemy.ext.asyncio import AsyncSession

from bbblb.services import ServiceRegistry
from bbblb.services.bbb import BBBHelper


async def test_cleanup_stale_meetings(orm: AsyncSession, services: ServiceRegistry):
    helper = await services.use(BBBHelper)

    t1 = model.Tenant(name="test", realm="bbb.example.com", secret="test")
    s1 = model.Server(domain="bbb1.example.com", secret="test")
    m1 = model.Meeting(
        created=model.utcnow() - timedelta(minutes=4),
        tenant=t1,
        server=s1,
        uuid=uuid.uuid4(),
        external_id="foo",
    )
    m2 = model.Meeting(
        created=model.utcnow() - timedelta(minutes=6),
        tenant=t1,
        server=s1,
        uuid=uuid.uuid4(),
        external_id="bar",
    )
    orm.add_all([t1, s1, m1, m2])
    await orm.commit()

    await helper._cleanup_stale_meetings(timedelta(minutes=5))
    assert [m1] == (await orm.execute(model.Meeting.select())).scalars().all()


async def test_cleanup_callbacks(orm: AsyncSession, services: ServiceRegistry):
    helper = await services.use(BBBHelper)

    t1 = model.Tenant(name="test", realm="bbb.example.com", secret="test")
    s1 = model.Server(domain="bbb1.example.com", secret="test")

    c1 = model.Callback(
        created=model.utcnow() - timedelta(days=44),
        tenant=t1,
        server=s1,
        uuid=uuid.uuid4(),
        type=model.CALLBACK_TYPE_REC,
    )
    c2 = model.Callback(
        created=model.utcnow() - timedelta(days=46),
        tenant=t1,
        server=s1,
        uuid=uuid.uuid4(),
        type=model.CALLBACK_TYPE_REC,
    )
    orm.add_all([t1, s1, c1, c2])
    await orm.commit()

    await helper._cleanup_old_callbacks(timedelta(days=45))
    assert [c1] == (await orm.execute(model.Callback.select())).scalars().all()
