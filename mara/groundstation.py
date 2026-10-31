import argparse
import json
import os
import socket
import ssl
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

from .security import MessageVerifier, ReplayCache, SecurityError, load_key
from .telemetry import build_server_context
from .utils import ensure_dir, utc_timestamp

MAX_LINE_BYTES = 64 * 1024


class GroundStation:

    def __init__(self, host: str, port: int, ca_file: str, cert_file: str, key_file: str,
                 hmac_key: bytes, out_path: str = "logs/ground_station.jsonl",
                 on_message: Optional[Callable[[Dict[str, Any]], None]] = None,
                 handshake_timeout_s: float = 5.0, idle_timeout_s: float = 60.0,
                 max_skew_s: float = 30.0):
        self.host = host
        self._requested_port = port
        self._context = build_server_context(ca_file, cert_file, key_file)
        self._hmac_key = hmac_key
        self._replay_cache = ReplayCache()
        self._max_skew_s = max_skew_s
        self.out_path = out_path
        self.on_message = on_message
        self.handshake_timeout_s = handshake_timeout_s
        self.idle_timeout_s = idle_timeout_s
        self._server: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._file_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self.stats = {"accepted": 0, "rejected": 0, "handshake_failed": 0, "connections": 0}

    @property
    def port(self) -> int:
        return self._server.getsockname()[1] if self._server else self._requested_port

    def start(self) -> None:
        ensure_dir(os.path.dirname(self.out_path) or ".")
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self._requested_port))
        self._server.listen(8)
        self._server.settimeout(0.5)
        self._thread = threading.Thread(target=self._accept_loop, name="gs-accept", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._server:
            self._server.close()
            self._server = None

    def _bump(self, key: str) -> None:
        with self._stats_lock:
            self.stats[key] += 1

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn, addr),
                             name="gs-client", daemon=True).start()

    def _serve(self, raw: socket.socket, addr) -> None:
        try:
            raw.settimeout(self.handshake_timeout_s)
            conn = self._context.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError) as exc:
            self._bump("handshake_failed")
            print(f"[GS] TLS rukovanje odbijeno ({addr[0]}): {exc}")
            raw.close()
            return

        self._bump("connections")
        peer = conn.getpeercert() or {}
        subject = dict(item[0] for item in peer.get("subject", ()))
        print(f"[GS] Veza: {addr[0]}, {conn.version()}, CN={subject.get('commonName', '?')}")
        verifier = MessageVerifier(self._hmac_key, self._max_skew_s, self._replay_cache)
        conn.settimeout(self.idle_timeout_s)
        buffer = b""
        try:
            while not self._stop.is_set():
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    self._handle_line(line, verifier)
                if len(buffer) > MAX_LINE_BYTES:
                    self._bump("rejected")
                    print("[GS] Predugačka linija, prekidam vezu.")
                    break
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle_line(self, line: bytes, verifier: MessageVerifier) -> None:
        try:
            envelope = json.loads(line.decode("utf-8"))
            payload = verifier.verify(envelope)
        except (ValueError, SecurityError) as exc:
            self._bump("rejected")
            print(f"[GS] Odbačena poruka: {exc}")
            return
        record = {"received_at": utc_timestamp(), "sender": envelope["sid"],
                  "seq": envelope["seq"], "payload": payload}
        with self._file_lock:
            with open(self.out_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._bump("accepted")
        if self.on_message:
            self.on_message(record)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MARA-1 zemaljska stanica (TLS 1.3 + HMAC)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ca", default="certs/ca.crt")
    parser.add_argument("--cert", default="certs/server.crt")
    parser.add_argument("--key", default="certs/server.key")
    parser.add_argument("--hmac-key", default="keys/hmac.key")
    parser.add_argument("--out", default="logs/ground_station.jsonl")
    args = parser.parse_args(argv)

    try:
        station = GroundStation(
            args.host, args.port, args.ca, args.cert, args.key, load_key(args.hmac_key), args.out,
            on_message=lambda r: print(f"[GS] {r['payload'].get('event_type', '?'):<18} "
                                       f"{r['payload'].get('message', '')}"))
        station.start()
    except (SecurityError, OSError, ssl.SSLError) as exc:
        print(f"Greška pri pokretanju zemaljske stanice: {exc}")
        return 1
    print(f"[GS] Slušam na {args.host}:{station.port} (TLS 1.3, obavezan klijentski certifikat)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[GS] Zaustavljam.")
    finally:
        station.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
