"""The running engine with a simulated panel: what actually reaches the panel after each click."""
import asyncio
import threading
import time

import pytest

from pixelpanel import ble, config, work
from pixelpanel.engine import Engine


class FakeClient:
    """Stands in for the Bluetooth connection and counts what is sent."""

    connected = True
    size = (64, 16)
    address = "x"
    device_info = None

    def __init__(self) -> None:
        self.images = 0
        self.hang = False

    async def command(self, data):
        pass

    async def sync_time(self):
        pass

    async def disconnect(self):
        pass

    async def connect(self, address):
        pass

    async def send_image(self, data, is_gif):
        if self.hang:
            raise TimeoutError("no answer from the panel")
        self.images += 1


def wait_for(check, seconds=4.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def running(monkeypatch):
    monkeypatch.setattr(work, "on_air_users", lambda mic=True, camera=True: AIR["users"])
    AIR["users"] = []
    cfg = config.default_config()
    cfg["pages"] = [config.new_page("clock")]
    cfg["transition"] = "none"
    cfg["teams"]["enabled"] = False
    cfg["claude"]["enabled"] = False
    e = Engine(cfg)
    e.client = FakeClient()
    e.start()
    assert wait_for(lambda: e.status.showing), "the engine did not start"
    yield e
    e.stop()


AIR = {"users": []}


def sign(text="Do not disturb"):
    return {**config.new_page("text"), "text": text, "icon": ""}


def test_sign_goes_up_and_comes_down(running):
    running.show_message(sign(), None)
    assert wait_for(lambda: running.status.showing == "Door sign / message")
    running.clear_message()
    assert wait_for(lambda: running.status.showing == "Clock")


def test_starting_focus_takes_the_sign_down_and_shows_the_timer(running):
    running.show_message(sign(), None)
    assert wait_for(lambda: running.status.showing == "Door sign / message")
    running.focus_toggle()
    assert wait_for(lambda: running.status.showing == "Focus timer")
    assert running._message is None


def test_next_page_takes_the_sign_down(running):
    running.show_message(sign(), None)
    assert wait_for(lambda: running.status.showing == "Door sign / message")
    running.next_page()
    assert wait_for(lambda: running.status.showing == "Clock")


def test_stopwatch_is_visible_without_a_ticked_page(running):
    running.stopwatch_toggle()
    assert wait_for(lambda: running.status.showing == "Stopwatch"), "must show up right away"
    assert any(p["type"] == "stopwatch" for p in running._rotation(running.config))
    running.stopwatch_toggle()
    assert not any(p["type"] == "stopwatch" for p in running._rotation(running.config))


def test_stopwatch_shows_briefly_over_a_sign_and_the_sign_comes_back(running):
    running.show_message(sign(), None)
    assert wait_for(lambda: running.status.showing == "Door sign / message")
    running._peek_seconds = 0.3
    running.stopwatch_toggle()
    assert wait_for(lambda: running.status.showing == "Stopwatch")
    assert wait_for(lambda: running.status.showing == "Door sign / message")


def test_a_ticked_stopwatch_page_is_not_added_twice(running):
    running.config["pages"].append(config.new_page("stopwatch"))
    running.stopwatch_toggle()
    assert [p["type"] for p in running._rotation(running.config)].count("stopwatch") == 1


def test_focus_without_takeover_still_appears_in_the_rotation(running):
    running.config["focus"]["takeover"] = False
    running.focus_toggle()
    assert any(p["type"] == "focus" for p in running._rotation(running.config))


def test_on_air_beats_a_sign_and_the_sign_returns(running):
    running.config["on_air"]["enabled"] = True
    running.show_message(sign(), None)
    assert wait_for(lambda: running.status.showing == "Door sign / message")
    AIR["users"] = ["Teams"]
    assert wait_for(lambda: running.status.showing == "On air", 5)
    assert running.status.on_air == "In use by: Teams"
    AIR["users"] = []
    assert wait_for(lambda: running.status.showing == "Door sign / message", 5)


def test_clicking_fast_ends_in_the_right_state(running):
    for _ in range(10):
        running.show_message(sign(), None)
        time.sleep(0.01)
        running.clear_message()
    assert wait_for(lambda: running.status.showing == "Clock")
    sent = running.client.images
    running.show_message(sign("Back soon"), None)
    assert wait_for(lambda: running.client.images > sent and running.status.showing == "Door sign / message")


def test_a_panel_that_does_not_answer_does_not_stop_the_engine(running):
    running.client.hang = True
    running.show_message(sign(), None)
    assert wait_for(lambda: running.status.error)
    running.client.hang = False
    running.clear_message()
    assert wait_for(lambda: running.status.showing == "Clock" and not running.status.error)


def test_heartbeat_shows_the_engine_is_alive(running):
    first = running.last_tick
    assert wait_for(lambda: running.last_tick > first)


def test_stuck_engine_is_reported_with_its_position(running, caplog):
    running.last_tick = time.monotonic() - 100
    assert running.report_if_stuck(30) is True
    assert "stuck" in caplog.text.lower()


# --- Bluetooth steps have a time limit --------------------------------------------------------------

class SilentBleak:
    is_connected = True
    mtu_size = 517

    async def write_gatt_char(self, uuid, data, response=False):
        await asyncio.sleep(60)  # the panel never answers

    async def disconnect(self):
        pass


def test_a_write_that_never_returns_runs_into_a_time_limit(monkeypatch):
    monkeypatch.setattr(ble, "WRITE_TIMEOUT", 0.05)
    client = ble.PanelClient()
    client._client = SilentBleak()

    async def run():
        with pytest.raises(TimeoutError):
            await client.send_image(b"x" * 10, False)

    asyncio.run(run())
    assert threading.active_count() >= 1


# --- on air ----------------------------------------------------------------------------------------

def test_on_air_ignores_programs_that_are_not_running_anymore():
    names = {"ms-teams.exe", "python.exe"}
    assert work.still_running(r"C:#Program Files#x#python.exe", names) is True
    assert work.still_running(r"C:#Old#crashed.exe", names) is False
    assert work.still_running("MSTeams_8wekyb3d8bbwe", names) is True  # store apps: not checkable, trust Windows
    assert work.app_label(r"C:#Program Files#Zoom#Zoom.exe") == "Zoom"
    assert work.app_label("MSTeams_8wekyb3d8bbwe") == "MSTeams"
