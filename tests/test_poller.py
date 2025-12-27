import bbblb.model
from sqlalchemy.ext.asyncio import AsyncSession

from bbblb.services import ServiceRegistry
from bbblb.services.poller import MeetingPoller


async def test_update_load(
    orm: AsyncSession, services: ServiceRegistry, bbb_server: bbblb.model.Server
):
    poller = await services.use(MeetingPoller)
    poller.task.cancel()  # type: ignore

    # Set a fake load value
    await orm.execute(
        bbb_server.update(bbblb.model.Server.id == bbb_server.id).values(load=1337.0)
    )
    await orm.commit()
    await orm.reset()

    # Poll this server
    await poller.poll_one(bbb_server.id)

    # Assume a different load value after polling
    server = (
        await orm.execute(bbblb.model.Server.select(id=bbb_server.id))
    ).scalar_one()
    assert server.health == bbblb.model.ServerHealth.AVAILABLE
    assert server.load != 1337.0
