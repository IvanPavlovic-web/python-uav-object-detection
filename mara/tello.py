import socket
import time
from typing import Dict, Optional

TELLO_IP = "192.168.10.1"
COMMAND_PORT = 8889
STATE_PORT = 8890
VIDEO_PORT = 11111
VIDEO_URL = f"udp://0.0.0.0:{VIDEO_PORT}"


class TelloError(RuntimeError):
    pass


def parse_state(packet: str) -> Dict[str, float]:
    state: Dict[str, float] = {}
    for item in packet.strip().split(";"):
        if ":" not in item:
            continue
        key, _, value = item.partition(":")
        try:
            state[key.strip()] = float(value)
        except ValueError:
            continue
    return state


class TelloClient:

    def __init__(self, ip: str = TELLO_IP, command_port: int = COMMAND_PORT,
                 default_timeout_s: float = 7.0):
        self.address = (ip, command_port)
        self.default_timeout_s = default_timeout_s
        self._sock: Optional[socket.socket] = None

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("", 0))
        self.command("command")

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def send_command(self, text: str, timeout_s: Optional[float] = None) -> str:
        if self._sock is None:
            raise TelloError("Veza nije otvorena (pozovite connect()).")
        self._sock.settimeout(timeout_s or self.default_timeout_s)
        self._sock.sendto(text.encode("utf-8"), self.address)
        try:
            data, _ = self._sock.recvfrom(1024)
        except socket.timeout as exc:
            raise TelloError(f"Nema odgovora na komandu '{text}'.") from exc
        return data.decode("utf-8", errors="replace").strip()

    def command(self, text: str, timeout_s: Optional[float] = None) -> None:
        reply = self.send_command(text, timeout_s)
        if reply.lower() != "ok":
            raise TelloError(f"Komanda '{text}' odbijena: {reply!r}")

    def query(self, text: str) -> str:
        return self.send_command(text)

    def takeoff(self) -> None:
        self.command("takeoff", timeout_s=15)

    def land(self) -> None:
        self.command("land", timeout_s=15)

    def stream_on(self) -> None:
        self.command("streamon")

    def stream_off(self) -> None:
        self.command("streamoff")

    def battery(self) -> int:
        reply = self.query("battery?")
        try:
            return int(reply)
        except ValueError as exc:
            raise TelloError(f"Neočekivan odgovor na battery?: {reply!r}") from exc

    def emergency(self) -> None:
        self.send_command("emergency", timeout_s=2)

    def rc(self, left_right: int, forward_back: int, up_down: int, yaw: int) -> None:
        if self._sock is None:
            raise TelloError("Veza nije otvorena (pozovite connect()).")
        values = [max(-100, min(100, int(v))) for v in
                  (left_right, forward_back, up_down, yaw)]
        text = "rc {} {} {} {}".format(*values)
        self._sock.sendto(text.encode("utf-8"), self.address)


def receive_state(port: int = STATE_PORT, timeout_s: float = 2.0) -> Dict[str, float]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        sock.settimeout(timeout_s)
        data, _ = sock.recvfrom(1024)
        return parse_state(data.decode("utf-8", errors="replace"))
    except socket.timeout as exc:
        raise TelloError("Nije stigao paket stanja.") from exc
    finally:
        sock.close()


if __name__ == "__main__":
    client = TelloClient()
    client.connect()
    print("Baterija:", client.battery(), "%")
    client.close()
    time.sleep(0.1)
