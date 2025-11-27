from bbblb import model
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime


async def insert_testdata(orm: AsyncSession):
    async with orm.begin():
        tenant = model.Tenant(name="test", realm="TEST", secret="1234")
        record = model.Recording(
            record_id="123",
            external_id="123",
            state=model.RecordingState.PUBLISHED,
            started=datetime.now(),
            ended=datetime.now(),
            tenant=tenant,
        )
        playback = model.PlaybackFormat(recording=record, format="presentation", xml="")
        orm.add(tenant)
        orm.add(record)
        orm.add(playback)


async def test_recording_delete(orm: AsyncSession):
    await insert_testdata(orm)

    async with orm.begin():
        await orm.execute(model.Recording.delete(model.Recording.record_id == "123"))

    assert not (await orm.execute(model.PlaybackFormat.select())).all()
    assert not (await orm.execute(model.Recording.select())).all()
