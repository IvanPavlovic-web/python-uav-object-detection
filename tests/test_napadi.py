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
from mara.telemetry import build_client_context

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


@pytest.fixture
def station(pki, tmp_path):
    key = generate_key()
    gs = GroundStation("127.0.0.1", 0, pki["ca.crt"], pki["server.crt"], pki["server.key"],
                       key, str(tmp_path / "gs.jsonl"))
    gs.start()
    gs.hmac_key_for_tests = key
    yield gs
    gs.stop()


@pytest.fixture
def fast_station(pki, tmp_path):
    key = generate_key()
    gs = GroundStation("127.0.0.1", 0, pki["ca.crt"], pki["server.crt"], pki["server.key"],
                       key, str(tmp_path / "gs_fast.jsonl"),
                       handshake_timeout_s=0.5, idle_timeout_s=0.5)
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


def raw_tls_socket(station, pki, max_version=None):
    context = build_client_context(pki["ca.crt"], pki["client.crt"], pki["client.key"])
    if max_version is not None:
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = max_version
    sock = socket.create_connection(("127.0.0.1", station.port), timeout=3)
    return context.wrap_socket(sock, server_hostname="localhost")


def client_context_without_certificate(ca_file):
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def test_client_without_certificate_is_rejected_at_handshake(station, pki):
    before = dict(station.stats)
    context = client_context_without_certificate(pki["ca.crt"])
    sock = socket.create_connection(("127.0.0.1", station.port), timeout=3)
    with pytest.raises((ssl.SSLError, OSError)):
        wrapped = context.wrap_socket(sock, server_hostname="localhost")
        wrapped.recv(16)
    assert wait_for(lambda: station.stats["handshake_failed"] == before["handshake_failed"] + 1)
    assert station.stats["connections"] == before["connections"]
    assert station.stats["accepted"] == before["accepted"]


def test_replay_across_two_connections_is_rejected(station, pki):
    before = dict(station.stats)
    signer = MessageSigner(station.hmac_key_for_tests, "MARA-1")
    line = json.dumps(signer.sign({"event_type": "OBJECT_DETECTED",
                                   "message": "ponovljena preko druge veze"})).encode() + b"\n"

    sock1 = raw_tls_socket(station, pki)
    sock1.sendall(line)
    assert wait_for(lambda: station.stats["accepted"] == before["accepted"] + 1)
    sock1.close()

    sock2 = raw_tls_socket(station, pki)
    sock2.sendall(line)
    assert wait_for(lambda: station.stats["rejected"] == before["rejected"] + 1)
    sock2.close()

    assert station.stats["accepted"] == before["accepted"] + 1


def test_slow_client_without_handshake_is_closed_after_timeout(fast_station):
    before = dict(fast_station.stats)
    sock = socket.create_connection(("127.0.0.1", fast_station.port), timeout=3)
    try:
        assert wait_for(lambda: fast_station.stats["handshake_failed"]
                        == before["handshake_failed"] + 1, timeout=3)
        assert fast_station.stats["connections"] == before["connections"]
        assert fast_station.stats["accepted"] == before["accepted"]
    finally:
        sock.close()


def test_idle_connection_is_closed_after_idle_timeout(fast_station, pki):
    before = dict(fast_station.stats)
    sock = raw_tls_socket(fast_station, pki)
    try:
        assert wait_for(lambda: fast_station.stats["connections"] == before["connections"] + 1)
        time.sleep(1.0)
        sock.settimeout(1.0)
        try:
            data = sock.recv(16)
        except (OSError, ssl.SSLError):
            data = b""
        assert data == b""
        assert fast_station.stats["accepted"] == before["accepted"]
        assert fast_station.stats["rejected"] == before["rejected"]
    finally:
        sock.close()


@pytest.mark.parametrize("polje, nova_vrijednost", [
    ("nonce", "a" * 24),
    ("v", 2),
    ("sid", "NAPADAC"),
])
def test_tampering_nonce_v_or_sid_invalidates_signature(station, pki, polje, nova_vrijednost):
    before = dict(station.stats)
    signer = MessageSigner(station.hmac_key_for_tests, "MARA-1")
    envelope = signer.sign({"event_type": "OBJECT_DETECTED", "message": "x"})
    envelope[polje] = nova_vrijednost

    sock = raw_tls_socket(station, pki)
    sock.sendall(json.dumps(envelope).encode() + b"\n")
    assert wait_for(lambda: station.stats["rejected"] == before["rejected"] + 1)
    assert station.stats["accepted"] == before["accepted"]
    sock.close()
