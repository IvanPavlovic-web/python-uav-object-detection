import json
import queue
import socket
import ssl
import threading
import time
from typing import Any, Dict, Optional

from .security import MessageSigner


def build_client_context(ca_file: str, cert_file: str, key_file: str) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return context


def build_server_context(ca_file: str, cert_file: str, key_file: str) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return context


class TelemetryClient:
    def __init__(self, host: str, port: int, ca_file: str, cert_file: str, key_file: str,
                 hmac_key: bytes, sender_id: str = "MARA-1", server_hostname: Optional[str] = None,
                 queue_size: int = 256, connect_timeout_s: float = 5.0,
                 max_backoff_s: float = 10.0):
        self.host = host
        self.port = port
        self.server_hostname = server_hostname or host
        self.connect_timeout_s = connect_timeout_s
        self.max_backoff_s = max_backoff_s
        self._context = build_client_context(ca_file, cert_file, key_file)
        self._signer = MessageSigner(hmac_key, sender_id)
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[ssl.SSLSocket] = None
        self.sent = 0
        self.dropped = 0
        self.last_error: Optional[str] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="telemetry", daemon=True)
        self._thread.start()

    def send(self, event: Dict[str, Any]) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def stop(self, flush_timeout_s: float = 3.0) -> None:
        deadline = time.monotonic() + flush_timeout_s
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.05)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._close()

    def _connect(self) -> None:
        raw = socket.create_connection((self.host, self.port), timeout=self.connect_timeout_s)
        try:
            self._sock = self._context.wrap_socket(raw, server_hostname=self.server_hostname)
        except Exception:
            raw.close()
            raise
        self._sock.settimeout(self.connect_timeout_s)

    def _close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _transmit(self, event: Dict[str, Any]) -> None:
        envelope = self._signer.sign(event)
        line = json.dumps(envelope, separators=(",", ":")).encode("utf-8") + b"\n"
        self._sock.sendall(line)

    def _run(self) -> None:
        backoff = 0.5
        pending: Optional[Dict[str, Any]] = None
        while not self._stop.is_set() or pending is not None or not self._queue.empty():
            if pending is None:
                try:
                    pending = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
            try:
                if self._sock is None:
                    self._connect()
                    backoff = 0.5
                self._transmit(pending)
                self.sent += 1
                pending = None
            except (OSError, ssl.SSLError) as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._close()
                if self._stop.is_set():
                    self.dropped += 1
                    pending = None
                    continue
                time.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff_s)
