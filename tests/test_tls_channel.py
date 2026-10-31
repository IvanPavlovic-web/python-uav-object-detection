import json
import os
import shutil
import socket
import ssl
import subprocess
import time

import pytest

from mara.groundstation import GroundStation
from mara.security import MessageSigner, generate_key
from mara.telemetry import TelemetryClient, build_client_context

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "generate_certs.sh")

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None or shutil.which("bash") is None,
                                reason="potrebni su bash i openssl za generisanje certifikata")


def make_pki(directory):
    subprocess.run(["bash", SCRIPT, str(directory)], check=True, capture_output=True)
    return {name: os.path.join(str(directory), name)
            for name in ("ca.crt", "server.crt", "server.key", "client.crt", "client.key")}


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return make_pki(tmp_path_factory.mktemp("pki"))


@pytest.fixture(scope="module")
def other_pki(tmp_path_factory):
    return make_pki(tmp_path_factory.mktemp("other_pki"))


@pytest.fixture
def station(pki, tmp_path):
    key = generate_key()
    gs = GroundStation("127.0.0.1", 0, pki["ca.crt"], pki["server.crt"], pki["server.key"],
                       key, str(tmp_path / "gs.jsonl"))
    gs.start()
    gs.hmac_key_for_tests = key
    yield gs
    gs.stop()


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def make_client(station, pki, key=None, **kwargs):
    client = TelemetryClient("127.0.0.1", station.port, pki["ca.crt"], pki["client.crt"],
                             pki["client.key"], key or station.hmac_key_for_tests,
                             server_hostname=kwargs.pop("server_hostname", "localhost"), **kwargs)
    client.start()
    return client


def read_records(station):
    if not os.path.exists(station.out_path):
        return []
    with open(station.out_path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def raw_tls_socket(station, pki, max_version=None):
    context = build_client_context(pki["ca.crt"], pki["client.crt"], pki["client.key"])
    if max_version is not None:
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = max_version
    sock = socket.create_connection(("127.0.0.1", station.port), timeout=3)
    return context.wrap_socket(sock, server_hostname="localhost")


def test_events_arrive_authenticated_and_in_order(station, pki):
    client = make_client(station, pki)
    for i in range(5):
        client.send({"event_type": "WAYPOINT_REACHED", "message": f"WP{i}"})
    assert wait_for(lambda: station.stats["accepted"] == 5)
    client.stop()
    records = read_records(station)
    assert [r["payload"]["message"] for r in records] == [f"WP{i}" for i in range(5)]
    assert [r["seq"] for r in records] == [1, 2, 3, 4, 5]
    assert all(r["sender"] == "MARA-1" for r in records)


def test_connection_uses_tls_13(station, pki):
    sock = raw_tls_socket(station, pki)
    try:
        assert sock.version() == "TLSv1.3"
    finally:
        sock.close()


def test_server_rejects_tls_12_client(station, pki):
    try:
        sock = raw_tls_socket(station, pki, max_version=ssl.TLSVersion.TLSv1_2)
        sock.close()
    except (ssl.SSLError, OSError):
        pass
    assert wait_for(lambda: station.stats["handshake_failed"] == 1)
    assert station.stats["connections"] == 0


def test_wrong_hmac_key_messages_are_rejected(station, pki):
    client = make_client(station, pki, key=generate_key())
    client.send({"event_type": "X", "message": "lažna poruka"})
    assert wait_for(lambda: station.stats["rejected"] == 1)
    client.stop()
    assert station.stats["accepted"] == 0 and read_records(station) == []


def test_client_with_foreign_certificate_is_refused(station, pki, other_pki):
    foreign = dict(pki, **{"client.crt": other_pki["client.crt"],
                           "client.key": other_pki["client.key"]})
    client = TelemetryClient("127.0.0.1", station.port, pki["ca.crt"], foreign["client.crt"],
                             foreign["client.key"], station.hmac_key_for_tests,
                             server_hostname="localhost")
    client.start()
    client.send({"event_type": "X", "message": "ne smije proći"})
    assert wait_for(lambda: station.stats["handshake_failed"] >= 1)
    client.stop(flush_timeout_s=0.5)
    assert station.stats["accepted"] == 0


def test_client_refuses_server_with_wrong_name(station, pki):
    client = make_client(station, pki, server_hostname="evil.example")
    client.send({"event_type": "X", "message": "nikad ne stiže"})
    assert wait_for(lambda: client.last_error is not None)
    client.stop(flush_timeout_s=0.5)
    assert station.stats["accepted"] == 0
    assert "certificate" in client.last_error.lower() or "hostname" in client.last_error.lower()


def test_client_refuses_server_from_unknown_ca(station, pki, other_pki):
    client = TelemetryClient("127.0.0.1", station.port, other_pki["ca.crt"], pki["client.crt"],
                             pki["client.key"], station.hmac_key_for_tests,
                             server_hostname="localhost")
    client.start()
    client.send({"event_type": "X", "message": "nikad ne stiže"})
    assert wait_for(lambda: client.last_error is not None)
    client.stop(flush_timeout_s=0.5)
    assert station.stats["accepted"] == 0


def test_plain_tcp_client_is_refused(station):
    sock = socket.create_connection(("127.0.0.1", station.port), timeout=3)
    sock.sendall(b'{"payload": "bez TLS-a"}\n')
    sock.close()
    assert wait_for(lambda: station.stats["handshake_failed"] == 1)
    assert station.stats["accepted"] == 0


def test_replayed_line_is_rejected_by_station(station, pki):
    signer = MessageSigner(station.hmac_key_for_tests, "MARA-1")
    line = json.dumps(signer.sign({"event_type": "OBJECT_DETECTED", "message": "x"})).encode() + b"\n"
    sock = raw_tls_socket(station, pki)
    sock.sendall(line)
    sock.sendall(line)
    assert wait_for(lambda: station.stats["accepted"] == 1 and station.stats["rejected"] == 1)
    sock.close()


def test_tampered_line_is_rejected_by_station(station, pki):
    signer = MessageSigner(station.hmac_key_for_tests, "MARA-1")
    envelope = signer.sign({"event_type": "OBJECT_DETECTED", "message": "person"})
    envelope["payload"]["message"] = "ništa nije detektovano"
    sock = raw_tls_socket(station, pki)
    sock.sendall(json.dumps(envelope).encode() + b"\n")
    assert wait_for(lambda: station.stats["rejected"] == 1)
    sock.close()
    assert read_records(station) == []


def test_garbage_and_oversized_input_does_not_crash_station(station, pki):
    sock = raw_tls_socket(station, pki)
    sock.sendall(b"ovo nije json\n")
    sock.sendall(b'{"v":1}\n')
    assert wait_for(lambda: station.stats["rejected"] == 2)
    sock.sendall(b"A" * 100_000)
    assert wait_for(lambda: station.stats["rejected"] >= 3)
    sock.close()
    client = make_client(station, pki)
    client.send({"event_type": "INFO", "message": "i dalje radi"})
    assert wait_for(lambda: station.stats["accepted"] == 1)
    client.stop()


def test_client_does_not_block_when_station_is_down(pki):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    client = TelemetryClient("127.0.0.1", free_port, pki["ca.crt"], pki["client.crt"],
                             pki["client.key"], generate_key(), server_hostname="localhost",
                             queue_size=4, connect_timeout_s=0.5)
    client.start()
    started = time.monotonic()
    results = [client.send({"event_type": "INFO", "message": str(i)}) for i in range(10)]
    assert time.monotonic() - started < 0.5
    assert results.count(False) >= 5 and client.dropped >= 5
    client.stop(flush_timeout_s=0.3)


def test_client_reconnects_after_station_restart(pki, tmp_path):
    key = generate_key()
    out = str(tmp_path / "gs.jsonl")
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    gs1 = GroundStation("127.0.0.1", port, pki["ca.crt"], pki["server.crt"], pki["server.key"], key, out)
    gs1.start()
    client = TelemetryClient("127.0.0.1", port, pki["ca.crt"], pki["client.crt"], pki["client.key"],
                             key, server_hostname="localhost", max_backoff_s=0.5)
    client.start()
    client.send({"event_type": "INFO", "message": "prije"})
    assert wait_for(lambda: gs1.stats["accepted"] == 1)
    gs1.stop()

    gs2 = GroundStation("127.0.0.1", port, pki["ca.crt"], pki["server.crt"], pki["server.key"], key, out)
    gs2.start()
    try:
        def delivered():
            client.send({"event_type": "INFO", "message": "poslije"})
            return gs2.stats["accepted"] >= 1
        assert wait_for(delivered, timeout=15)
    finally:
        client.stop(flush_timeout_s=0.5)
        gs2.stop()
