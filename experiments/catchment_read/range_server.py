"""Loopback-only HTTP fixture with completed-request transport measurements.

Delay is a simulated per-request latency, not a model of remote object storage.
Payload counts are bytes accepted by the socket, excluding HTTP headers.
"""

import math
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from time import perf_counter_ns, sleep
from types import TracebackType
from typing import Self, TypedDict
from urllib.parse import unquote, urlsplit


class RequestEvent(TypedDict):
    request_id: str | None
    path: str
    method: str
    range: str | None
    status: int
    start_ns: int
    end_ns: int
    payload_bytes: int


class _Server(ThreadingHTTPServer):
    daemon_threads = False


class RangeServer:
    """Serve /root-name/relative-path from explicitly named roots.

    A file root is available as /root-name. Keep inputs immutable while served;
    ETags use file metadata rather than hashing payloads. Close client pools before
    context exit, which waits for handlers so the final event snapshot is complete.
    Events are copied under a lock.
    """

    def __init__(self, roots: dict[str, Path], delay_seconds: float = 0) -> None:
        if not math.isfinite(delay_seconds) or delay_seconds < 0:
            raise ValueError("delay_seconds must be finite and nonnegative")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in roots):
            raise ValueError("root names must contain only letters, digits, '_' or '-'")
        self.roots = {name: path.resolve() for name, path in roots.items()}
        self.delay_seconds = delay_seconds
        self._events: list[RequestEvent] = []
        self._lock = Lock()
        self._server: _Server | None = None
        self._thread: Thread | None = None
        self.url = ""

    @property
    def events(self) -> list[RequestEvent]:
        with self._lock:
            return [event.copy() for event in self._events]

    def __enter__(self) -> Self:
        if self._server is not None:
            raise RuntimeError("server is already running")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            # Separate header/body writes must not trigger Nagle/delayed-ACK latency.
            disable_nagle_algorithm = True

            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_HEAD(self) -> None:
                owner._handle(self)

            def do_GET(self) -> None:
                owner._handle(self)

        self._server = _Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            if self._thread is not None:
                self._thread.join()
            self._server = None

    def _path(self, target: str) -> Path | None:
        parts = unquote(urlsplit(target).path).split("/")
        if any(part in {"..", "."} or "\\" in part or "\x00" in part for part in parts):
            return None
        root = self.roots.get(parts[1]) if len(parts) > 1 else None
        if root is None:
            return None
        candidate = root.joinpath(*parts[2:]).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return None
        return candidate

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        event: RequestEvent = {
            "request_id": handler.headers.get("X-Request-ID"),
            "path": handler.path,
            "method": handler.command,
            "range": handler.headers.get("Range"),
            "status": 500,
            "start_ns": perf_counter_ns(),
            "end_ns": 0,
            "payload_bytes": 0,
        }
        try:
            if self.delay_seconds:
                sleep(self.delay_seconds)
            path = self._path(handler.path)
            if path is None:
                event["status"] = 404
                handler.send_response(404)
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return
            with path.open("rb") as stream:
                # Inputs stay immutable while served. Bind metadata to the open file.
                stat = os.fstat(stream.fileno())
                size = stat.st_size
                etag = f'"{stat.st_mtime_ns:x}-{size:x}-{stat.st_ino:x}"'
                if_match = handler.headers.get("If-Match")
                if (
                    if_match is not None
                    and if_match.strip() != "*"
                    and etag not in {value.strip() for value in if_match.split(",")}
                ):
                    event["status"] = 412
                    handler.send_response(412)
                    handler.send_header("ETag", etag)
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    return
                start, end = 0, size - 1
                status = 200
                requested = (
                    handler.headers.get("Range") if handler.command == "GET" else None
                )
                if requested is not None:
                    bounds = _bounds(requested, size)
                    if bounds is None:
                        event["status"] = 416
                        handler.send_response(416)
                        handler.send_header("Content-Range", f"bytes */{size}")
                        handler.send_header("Content-Length", "0")
                        handler.end_headers()
                        return
                    start, end = bounds
                    status = 206
                event["status"] = status
                handler.send_response(status)
                handler.send_header("Accept-Ranges", "bytes")
                handler.send_header("ETag", etag)
                handler.send_header("Content-Length", str(end - start + 1))
                if status == 206:
                    handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                handler.end_headers()
                if handler.command == "HEAD":
                    return
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(remaining, 64 * 1024))
                    if not chunk:
                        break
                    view = memoryview(chunk)
                    while view:
                        sent = handler.connection.send(view)
                        if sent == 0:
                            return
                        event["payload_bytes"] += sent
                        remaining -= sent
                        view = view[sent:]
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            event["end_ns"] = perf_counter_ns()
            with self._lock:
                self._events.append(event)


def _bounds(value: str, size: int) -> tuple[int, int] | None:
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if match is None or size == 0:
        return None
    first, last = match.groups()
    if not first:
        if not last or int(last) == 0:
            return None
        return max(0, size - int(last)), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    return (start, end) if start <= end else None
