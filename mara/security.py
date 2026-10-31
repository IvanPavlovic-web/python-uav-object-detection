import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional

PROTOCOL_VERSION = 1
MIN_KEY_BYTES = 32
KEY_ENV_VAR = "MARA_HMAC_KEY"


class SecurityError(Exception):
    pass


def generate_key(length: int = MIN_KEY_BYTES) -> bytes:
    return secrets.token_bytes(length)


def save_key(path: str, key: bytes) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(key.hex() + "\n")


def load_key(path: Optional[str] = None) -> bytes:
    text = None
    if path:
        try:
            with open(path, "r", encoding="ascii") as handle:
                text = handle.read().strip()
        except OSError as exc:
            raise SecurityError(f"Ključ se ne može pročitati ({path}): {exc}") from exc
    else:
        text = os.environ.get(KEY_ENV_VAR, "").strip()
    if not text:
        raise SecurityError("HMAC ključ nije zadat (datoteka ili MARA_HMAC_KEY).")
    try:
        key = bytes.fromhex(text)
    except ValueError as exc:
        raise SecurityError("HMAC ključ mora biti zapisan heksadecimalno.") from exc
    if len(key) < MIN_KEY_BYTES:
        raise SecurityError(f"HMAC ključ mora imati najmanje {MIN_KEY_BYTES} bajtova.")
    return key


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


class ReplayCache:

    def __init__(self, ttl_s: float = 120.0, max_entries: int = 10000,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._clock = clock
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._lock = threading.Lock()

    def check_and_add(self, nonce: str) -> bool:
        now = self._clock()
        with self._lock:
            while self._seen:
                oldest, stamp = next(iter(self._seen.items()))
                if now - stamp > self.ttl_s or len(self._seen) > self.max_entries:
                    self._seen.popitem(last=False)
                else:
                    break
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True


class MessageSigner:

    def __init__(self, key: bytes, sender_id: str = "MARA-1",
                 clock: Callable[[], float] = time.time):
        if len(key) < MIN_KEY_BYTES:
            raise SecurityError(f"Ključ mora imati najmanje {MIN_KEY_BYTES} bajtova.")
        self._key = key
        self.sender_id = sender_id
        self._clock = clock
        self._seq = 0
        self._lock = threading.Lock()

    def sign(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._seq += 1
            seq = self._seq
        envelope = {
            "v": PROTOCOL_VERSION,
            "sid": self.sender_id,
            "seq": seq,
            "ts": round(self._clock(), 3),
            "nonce": secrets.token_hex(12),
            "payload": payload,
        }
        envelope["mac"] = hmac.new(self._key, canonical_bytes(envelope),
                                   hashlib.sha256).hexdigest()
        return envelope


class MessageVerifier:

    REQUIRED = ("v", "sid", "seq", "ts", "nonce", "payload", "mac")

    def __init__(self, key: bytes, max_skew_s: float = 30.0,
                 replay_cache: Optional[ReplayCache] = None,
                 clock: Callable[[], float] = time.time):
        if len(key) < MIN_KEY_BYTES:
            raise SecurityError(f"Ključ mora imati najmanje {MIN_KEY_BYTES} bajtova.")
        self._key = key
        self.max_skew_s = max_skew_s
        self.replay_cache = replay_cache or ReplayCache()
        self._clock = clock
        self._last_seq: Dict[str, int] = {}
        self._lock = threading.Lock()

    def verify(self, envelope: Any) -> Dict[str, Any]:
        if not isinstance(envelope, dict) or any(k not in envelope for k in self.REQUIRED):
            raise SecurityError("Neispravna struktura poruke.")
        if envelope["v"] != PROTOCOL_VERSION:
            raise SecurityError("Nepodržana verzija protokola.")
        if not isinstance(envelope["mac"], str) or not isinstance(envelope["nonce"], str):
            raise SecurityError("Neispravna polja poruke.")

        body = {k: v for k, v in envelope.items() if k != "mac"}
        try:
            expected = hmac.new(self._key, canonical_bytes(body), hashlib.sha256).hexdigest()
        except (TypeError, ValueError) as exc:
            raise SecurityError("Poruka se ne može serijalizovati.") from exc
        if not hmac.compare_digest(expected, envelope["mac"]):
            raise SecurityError("Potpis poruke nije ispravan.")

        ts, seq, sid = envelope["ts"], envelope["seq"], envelope["sid"]
        if not isinstance(ts, (int, float)) or not isinstance(seq, int):
            raise SecurityError("Neispravna polja ts/seq.")
        if abs(self._clock() - ts) > self.max_skew_s:
            raise SecurityError("Poruka je izvan dozvoljenog vremenskog prozora.")
        if not self.replay_cache.check_and_add(envelope["nonce"]):
            raise SecurityError("Ponovljena poruka (nonce je već korišten).")
        with self._lock:
            if seq <= self._last_seq.get(sid, 0):
                raise SecurityError("Redni broj poruke nije rastući.")
            self._last_seq[sid] = seq
        return envelope["payload"]


def _main(argv) -> int:
    if len(argv) == 3 and argv[1] == "genkey":
        if os.path.exists(argv[2]):
            print(f"Datoteka već postoji, neću je prepisati: {argv[2]}")
            return 1
        save_key(argv[2], generate_key())
        print(f"HMAC ključ je snimljen u {argv[2]} (prava 0600).")
        return 0
    print("Upotreba: python -m mara.security genkey PUTANJA")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
