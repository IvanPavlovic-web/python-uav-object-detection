import socket
import threading

import pytest

from mara.tello import TelloClient, TelloError, parse_state

SAMPLE_STATE = ("pitch:0;roll:1;yaw:-3;vgx:0;vgy:0;vgz:0;templ:60;temph:63;tof:10;"
                "h:0;bat:87;baro:-6.92;time:0;agx:3.00;agy:-3.00;agz:-999.00;\r\n")


class FakeTello:

    def __init__(self, replies=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.received = []
        self.replies = {"battery?": "87"}
        self.replies.update(replies or {})
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            try:
                data, addr = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            text = data.decode()
            self.received.append(text)
            if text.startswith("rc "):
                continue
            self.sock.sendto(self.replies.get(text, "ok").encode(), addr)

    def close(self):
        self._running = False
        self._thread.join(timeout=1)
        self.sock.close()


@pytest.fixture
def tello():
    fake = FakeTello()
    client = TelloClient("127.0.0.1", fake.port, default_timeout_s=1.0)
    client.connect()
    yield client, fake
    client.close()
    fake.close()


def test_connect_enters_sdk_mode(tello):
    _, fake = tello
    assert fake.received[0] == "command"


def test_takeoff_land_and_stream(tello):
    client, fake = tello
    client.takeoff()
    client.stream_on()
    client.land()
    assert fake.received[1:] == ["takeoff", "streamon", "land"]


def test_battery_query(tello):
    client, _ = tello
    assert client.battery() == 87


def test_error_reply_raises(tello):
    client, fake = tello
    fake.replies["takeoff"] = "error"
    with pytest.raises(TelloError):
        client.takeoff()


def test_timeout_raises():
    silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    silent.bind(("127.0.0.1", 0))
    client = TelloClient("127.0.0.1", silent.getsockname()[1], default_timeout_s=0.2)
    client._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    with pytest.raises(TelloError):
        client.command("command")
    client.close()
    silent.close()


def test_rc_values_are_clamped_and_unanswered(tello):
    client, fake = tello
    client.rc(500, -500, 0, 10)
    import time
    time.sleep(0.2)
    assert fake.received[-1] == "rc 100 -100 0 10"


def test_send_without_connect_raises():
    with pytest.raises(TelloError):
        TelloClient().send_command("battery?")


def test_parse_state():
    state = parse_state(SAMPLE_STATE)
    assert state["bat"] == 87 and state["h"] == 0 and state["baro"] == pytest.approx(-6.92)
    assert state["yaw"] == -3
    assert parse_state("") == {}
    assert parse_state("bat:abc;h:5;nonsense") == {"h": 5.0}
