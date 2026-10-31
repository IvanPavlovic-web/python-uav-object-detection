import copy
import os

import pytest

from mara.security import (MessageSigner, MessageVerifier, ReplayCache, SecurityError,
                           generate_key, load_key, save_key)


class FakeTime:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def pair(key=None, clock=None):
    key = key or generate_key()
    clock = clock or FakeTime()
    return (MessageSigner(key, "MARA-1", clock=clock),
            MessageVerifier(key, max_skew_s=30, clock=clock), clock)


def test_valid_message_is_accepted():
    signer, verifier, _ = pair()
    payload = {"event_type": "OBJECT_DETECTED", "message": "person 0.91"}
    assert verifier.verify(signer.sign(payload)) == payload


def test_tampered_payload_is_rejected():
    signer, verifier, _ = pair()
    envelope = signer.sign({"message": "ok"})
    envelope["payload"]["message"] = "izmijenjeno"
    with pytest.raises(SecurityError, match="Potpis"):
        verifier.verify(envelope)


def test_tampered_sequence_or_timestamp_is_rejected():
    signer, verifier, _ = pair()
    envelope = signer.sign({"a": 1})
    forged = copy.deepcopy(envelope)
    forged["seq"] = 99
    with pytest.raises(SecurityError):
        verifier.verify(forged)


def test_wrong_key_is_rejected():
    signer, _, clock = pair()
    verifier = MessageVerifier(generate_key(), clock=clock)
    with pytest.raises(SecurityError, match="Potpis"):
        verifier.verify(signer.sign({"a": 1}))


def test_replay_is_rejected():
    signer, verifier, _ = pair()
    envelope = signer.sign({"a": 1})
    verifier.verify(envelope)
    with pytest.raises(SecurityError, match="Ponovljena"):
        verifier.verify(envelope)


def test_old_message_is_rejected():
    signer, verifier, clock = pair()
    envelope = signer.sign({"a": 1})
    clock.now += 31
    with pytest.raises(SecurityError, match="vremenskog prozora"):
        verifier.verify(envelope)


def test_future_message_is_rejected():
    key = generate_key()
    sender_clock, receiver_clock = FakeTime(1_000_100.0), FakeTime(1_000_000.0)
    signer = MessageSigner(key, "MARA-1", clock=sender_clock)
    verifier = MessageVerifier(key, max_skew_s=30, clock=receiver_clock)
    with pytest.raises(SecurityError, match="vremenskog prozora"):
        verifier.verify(signer.sign({"a": 1}))


def test_out_of_order_sequence_is_rejected():
    signer, verifier, _ = pair()
    first, second = signer.sign({"n": 1}), signer.sign({"n": 2})
    verifier.verify(second)
    with pytest.raises(SecurityError, match="rastući"):
        verifier.verify(first)


@pytest.mark.parametrize("broken", [None, "tekst", [], {}, {"v": 1}])
def test_malformed_envelopes_are_rejected(broken):
    _, verifier, _ = pair()
    with pytest.raises(SecurityError):
        verifier.verify(broken)


def test_short_key_is_refused():
    with pytest.raises(SecurityError):
        MessageSigner(b"kratak")
    with pytest.raises(SecurityError):
        MessageVerifier(b"kratak")


def test_replay_cache_expires_entries():
    now = [0.0]
    cache = ReplayCache(ttl_s=10, clock=lambda: now[0])
    assert cache.check_and_add("n1") is True
    assert cache.check_and_add("n1") is False
    now[0] = 11
    assert cache.check_and_add("n1") is True


def test_key_save_and_load(tmp_path):
    path = str(tmp_path / "keys" / "hmac.key")
    key = generate_key()
    save_key(path, key)
    assert load_key(path) == key
    if os.name == "posix":
        assert (os.stat(path).st_mode & 0o777) == 0o600


def test_load_key_rejects_bad_content(tmp_path):
    bad = tmp_path / "bad.key"
    bad.write_text("nije-heksadecimalno\n")
    with pytest.raises(SecurityError):
        load_key(str(bad))
    short = tmp_path / "short.key"
    short.write_text("abcd\n")
    with pytest.raises(SecurityError):
        load_key(str(short))
