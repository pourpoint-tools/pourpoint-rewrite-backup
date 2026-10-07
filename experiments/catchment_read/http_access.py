"""Narrow measured HTTP range transport for this experiment, not an HFX API."""

from __future__ import annotations

import io
import struct
from collections.abc import Buffer, Iterable
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import perf_counter_ns
from typing import NotRequired, TypedDict

import httpx
from flatbuffers.table import Table

Range = tuple[int, int]
GAP = 64 * 1024
LIMIT = 8 * 1024 * 1024
WORKERS = 4


def merge_ranges(
    ranges: Iterable[Range], gap: int = GAP, limit: int = LIMIT
) -> list[Range]:
    result: list[Range] = []
    for start, end in sorted(set(ranges)):
        if start < 0 or end <= start:
            raise ValueError("invalid range")
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        elif result and start - result[-1][1] <= gap and end - result[-1][0] <= limit:
            result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return result


def ipc_blocks(footer: bytes, file_size: int) -> list[Range]:
    """Read only Arrow Footer.recordBatches with the FlatBuffers table runtime."""
    try:
        root = struct.unpack_from("<I", footer)[0]
        if root >= len(footer):
            raise ValueError("invalid IPC footer root")
        table = Table(bytearray(footer), root)
        dictionaries = table.Offset(8)  # field 2
        if dictionaries and table.VectorLen(dictionaries):
            raise ValueError("dictionary IPC is unsupported in this experiment")
        field = table.Offset(10)  # field 3, vector of 24-byte Block structs
        if not field:
            return []
        count, offset = table.VectorLen(field), table.Vector(field)
        if offset + count * 24 > len(footer):
            raise ValueError("truncated IPC block vector")
        result: list[Range] = []
        for index in range(count):
            start, metadata, body = struct.unpack_from(
                "<qi4xq", footer, offset + index * 24
            )
            end = start + metadata + body
            if (
                start < 8
                or metadata <= 0
                or body < 0
                or end > file_size - len(footer) - 10
            ):
                raise ValueError("IPC block outside file body")
            if result and start < result[-1][1]:
                raise ValueError("overlapping IPC blocks")
            result.append((start, end))
        return result
    except (struct.error, IndexError, TypeError) as error:
        raise ValueError("invalid IPC footer") from error


class Event(TypedDict):
    error: NotRequired[str]
    service_request_id: NotRequired[str]
    request_id: str
    path: str
    method: str
    start: int
    end: int
    status: int
    body_bytes: int
    start_ns: int
    end_ns: int


class Transport:
    def __init__(
        self,
        root: str,
        *,
        client: httpx.Client | None = None,
        identities: dict[str, tuple[int, str]] | None = None,
    ) -> None:
        self.identities = identities
        self.root = root.rstrip("/")
        self.client = client or httpx.Client(
            limits=httpx.Limits(
                max_connections=WORKERS, max_keepalive_connections=WORKERS
            ),
            timeout=60,
            trust_env=False,
        )
        self.pool = ThreadPoolExecutor(max_workers=WORKERS)
        self.files: dict[str, RemoteFile] = {}
        self.events: list[Event] = []
        self.lock = Lock()
        self.serial = 0
        self.plans: list[dict[str, int | str]] = []

    def request(
        self,
        name: str,
        method: str,
        start: int = 0,
        end: int = 0,
        etag: str | None = None,
    ) -> httpx.Response:
        with self.lock:
            self.serial += 1
            identity = str(self.serial)
        headers = {"Accept-Encoding": "identity", "X-Request-ID": identity}
        if method == "GET":
            headers["Range"] = f"bytes={start}-{end - 1}"
            assert etag is not None
            headers["If-Match"] = etag
        before = perf_counter_ns()
        try:
            response = self.client.request(
                method, self.root + "/" + name, headers=headers
            )
        except Exception as error:
            with self.lock:
                self.events.append(
                    Event(
                        request_id=identity,
                        path=name,
                        method=method,
                        start=start,
                        end=end,
                        status=0,
                        body_bytes=0,
                        start_ns=before,
                        end_ns=perf_counter_ns(),
                        error=type(error).__name__,
                    )
                )
            raise
        with self.lock:
            self.events.append(
                Event(
                    request_id=identity,
                    path=name,
                    method=method,
                    start=start,
                    end=end,
                    status=response.status_code,
                    service_request_id=response.headers.get("x-amz-request-id", ""),
                    body_bytes=len(response.content),
                    start_ns=before,
                    end_ns=perf_counter_ns(),
                )
            )
        return response

    def file(self, name: str) -> RemoteFile:
        if self.identities is not None and name not in self.identities:
            raise ValueError("object absent from receipt identity records")
        if name not in self.files:
            self.files[name] = RemoteFile(self, name)
        return self.files[name]

    def io_counts(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for event in self.events:
            item = counts.setdefault(event["path"], {"reads": 0, "bytes": 0})
            item["reads"] += 1
            item["bytes"] += event["body_bytes"]
        return counts

    def close(self) -> None:
        self.pool.shutdown(wait=True)
        self.client.close()


class RemoteFile(io.RawIOBase):
    def __init__(self, transport: Transport, name: str) -> None:
        super().__init__()
        self.transport, self.name = transport, name
        head = transport.request(name, "HEAD")
        head.raise_for_status()
        self.size = int(head.headers["Content-Length"])
        self.etag = head.headers.get("ETag", "")
        if not self.etag.startswith('"') or not self.etag.endswith('"'):
            raise ValueError("strong ETag required")
        if transport.identities is not None and transport.identities.get(name) != (
            self.size,
            self.etag,
        ):
            raise ValueError("uploaded receipt identity mismatch")
        self.position = 0
        self.sealed = False
        self.metadata: list[tuple[int, bytes]] = []
        self.data: list[tuple[int, bytes]] = []

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        position = offset + (
            0 if whence == 0 else self.position if whence == 1 else self.size
        )
        if whence not in (0, 1, 2) or position < 0:
            raise ValueError("invalid seek")
        self.position = position
        return position

    def fetch(self, span: Range) -> tuple[int, bytes]:
        start, end = span
        if not 0 <= start < end <= self.size:
            raise ValueError("range outside object")
        response = self.transport.request(self.name, "GET", start, end, self.etag)
        if (
            response.status_code != 206
            or response.headers.get("Content-Range")
            != f"bytes {start}-{end - 1}/{self.size}"
            or response.headers.get("ETag") != self.etag
            or response.headers.get("Content-Encoding", "identity") != "identity"
            or response.headers.get("Content-Length") != str(end - start)
            or len(response.content) != end - start
        ):
            raise ValueError("invalid or changed range response")
        return start, response.content

    def prefetch(self, ranges: Iterable[Range]) -> None:
        raw = list(ranges)
        planned = merge_ranges(raw)
        if len(planned) > WORKERS:
            raise ValueError("prefetch window exceeds four ranges")
        self.clear()
        self.transport.plans.append(
            {
                "path": self.name,
                "requested_bytes": sum(b - a for a, b in merge_ranges(raw, gap=0)),
                "fetched_bytes": sum(b - a for a, b in planned),
                "ranges": len(planned),
                "oversized_ranges": sum(b - a > LIMIT for a, b in planned),
            }
        )
        self.data = list(self.transport.pool.map(self.fetch, planned))

    def clear(self) -> None:
        self.data.clear()

    def read(self, size: int = -1) -> bytes:
        end = (
            self.size
            if size is None or size < 0
            else min(self.size, self.position + size)
        )
        start = self.position
        if end <= start:
            return b""
        for offset, data in self.data + self.metadata:
            if offset <= start and end <= offset + len(data):
                self.position = end
                return data[start - offset : end - offset]
        if self.sealed:
            raise ValueError(f"unplanned data read: {self.name} {start}:{end}")
        offset, data = self.fetch((start, end))
        self.metadata.append((offset, data))
        self.position = end
        return data

    def readinto(self, buffer: Buffer) -> int:
        view = memoryview(buffer).cast("B")
        data = self.read(len(view))
        view[: len(data)] = data
        return len(data)
