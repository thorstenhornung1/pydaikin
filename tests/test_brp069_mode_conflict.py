"""Test multi-split mode conflict detection (err=10000) on BRP069."""

from aiohttp import ClientSession
import pytest
import pytest_asyncio

from pydaikin.daikin_brp069 import DaikinBRP069

CONTROL = "ret=OK,pow=1,mode=4,stemp=22.0,shum=0"


@pytest_asyncio.fixture
async def client_session():
    client_session = ClientSession()
    yield client_session
    await client_session.close()


@pytest.mark.parametrize(
    ("err", "conflict"), [("0", False), ("10000", True), ("U4", False), (None, False)]
)
def test_mode_conflict(err, conflict):
    """Only err=10000 is a mode conflict."""
    device = DaikinBRP069("192.168.1.100", session=object())
    if err is not None:
        device.values.update_by_resource("aircon/get_sensor_info", {"err": err})
    assert device.mode_conflict is conflict


@pytest.mark.parametrize("basic_err", ["0", "10000"])
@pytest.mark.asyncio
async def test_mode_conflict_follows_polling(aresponses, client_session, basic_err):
    """err follows get_sensor_info, not the cached basic_info (static resource)."""

    def add(path, body):
        aresponses.add(path_pattern=path, response=body)

    add("/common/get_datetime", "ret=OK,sta=2")
    add("/common/basic_info", f"ret=OK,mac=00,err={basic_err}")
    add("/aircon/get_sensor_info", "ret=OK,htemp=25,err=0")
    add("/aircon/get_model_info", "ret=OK,model=0FC1")
    add("/aircon/get_control_info", CONTROL)
    for path in ("get_day_power_ex", "get_week_power", "get_year_power"):
        add(f"/aircon/{path}", "ret=OK")
    device = DaikinBRP069("192.168.1.100", session=client_session)

    await device.init()
    assert device.mode_conflict is False

    # The conflict appears after init (another unit starts cooling) and ends.
    # Reading mode_conflict marks get_sensor_info for the next poll.
    for err, conflict in (("10000", True), ("0", False)):
        add("/aircon/get_sensor_info", f"ret=OK,htemp=25,err={err}")
        await device.update_status()
        assert device.mode_conflict is conflict

    aresponses.assert_all_requests_matched()
    aresponses.assert_no_unused_routes()
