from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
from obstore.exceptions import BaseError

from pourpoint.metadata import Fingerprint
from pourpoint.storage import Source


def fingerprint(data: bytes) -> Fingerprint:
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    return Fingerprint(len(data), digest(data), digest(data[:65536]), digest(data[-65536:]))


class OriginState(TypedDict):
    data: bytes
    mode: str
    requests: list[tuple[str | None, int, str | None, str | None]]
    files: NotRequired[dict[str, bytes]]
    signed: NotRequired[list[bool]]
    cache_controls: NotRequired[list[str | None]]


@pytest.fixture
def origin() -> Iterator[tuple[str, OriginState]]:
    state: OriginState = {"data": bytes(range(256)) * 4096, "mode": "good", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def data(self) -> bytes:
            return state["files"][self.path.split("?")[0].split("/")[-1]] if "files" in state else state["data"]

        def do_HEAD(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.data())))
            self.send_header("ETag", '"v1"')
            self.end_headers()

        def do_GET(self) -> None:
            state.setdefault("cache_controls", []).append(self.headers.get("Cache-Control"))
            data = self.data()
            state.setdefault("signed", []).append(self.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256"))
            mode = state["mode"]
            header = self.headers.get("Range")
            start, end = map(int, header.removeprefix("bytes=").split("-")) if header else (0, len(data) - 1)
            body = data[start : end + 1]
            if mode == "short":
                body = body[:-1]
            if mode == "long":
                body += b"x"
            self.send_response(200 if mode == "status" or not header else 206)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Range", f"bytes {start + 1 if mode == 'range' else start}-{end}/{len(data)}")
            self.send_header("ETag", '"v2"' if mode == "etag" else '"v1"')
            if mode == "encoding":
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            # Strict clients can reject malformed headers before reading a body.
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)
            state["requests"].append(
                (header, len(body), self.headers.get("If-Match"), self.headers.get("Accept-Encoding"))
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


def test_local_bounded_and_close(tmp_path: Path) -> None:
    data = b"abc" * 100000
    (tmp_path / "file").write_bytes(data)
    with Source(tmp_path) as source:
        stream = source.open_artifact("file", fingerprint(data), bounded=True)
        stream.seek(123456)
        assert stream.read(17) == data[123456:123473]
        assert source.buffer_bytes >= 17
    assert source.buffer_bytes == 0
    with pytest.raises(ValueError, match="closed"):
        stream.read(1)


def test_http_pinned_selective(origin: tuple[str, OriginState]) -> None:
    url, state = origin
    with Source(url, storage_options={"client_options": {"allow_http": True}}) as source:
        stream = source.open_artifact("file", fingerprint(state["data"]), bounded=True)
        stream.seek(300000)
        assert stream.read(17) == state["data"][300000:300017]
    requests = state["requests"]
    assert sum(item[1] for item in requests) < len(state["data"]) // 2
    assert all(item[2:] == ('"v1"', "identity") for item in requests)


@pytest.mark.parametrize("mode", ["status", "range", "etag", "encoding", "short", "long"])
def test_reject_bad_range(origin: tuple[str, OriginState], mode: str) -> None:
    url, state = origin
    with Source(
        url, storage_options={"client_options": {"allow_http": True}, "retry_config": {"max_retries": 0}}
    ) as source:
        stream = source.open_artifact("file", fingerprint(state["data"]))
        state["mode"] = mode
        stream.seek(300000)
        with pytest.raises((BaseError, OSError, ValueError)):
            stream.read(17)


def test_graph_stream_and_corrupt_cache(origin: tuple[str, OriginState], tmp_path: Path) -> None:
    url, state = origin
    data = state["data"]
    mark = fingerprint(data)
    with Source(url, storage_options={"client_options": {"allow_http": True}}) as source:
        cached = source.graph_path(tmp_path, "graph.parquet", mark)
        assert cached.read_bytes() == data
        assert state["requests"] == [(None, len(data), '"v1"', "identity")]
        assert source.graph_path(tmp_path, "graph.parquet", mark) == cached
        assert len(state["requests"]) == 1
        cached.write_bytes(b"corrupt")
        assert source.graph_path(tmp_path, "graph.parquet", mark).read_bytes() == data
        assert len(state["requests"]) == 2
        assert not list(tmp_path.rglob("*.part"))


def test_local_json_digest_graph_original_and_safety(tmp_path: Path) -> None:
    data = b'{"value": 7}'
    (tmp_path / "manifest.json").write_bytes(data)
    with Source(tmp_path) as source:
        assert source.read_json("manifest.json", fingerprint(data)) == {"value": 7}
        assert source.graph_path(tmp_path / "cache", "manifest.json", fingerprint(data)) == tmp_path / "manifest.json"
        assert not (tmp_path / "cache").exists()
        with pytest.raises(ValueError, match="relative|escapes"):
            source.open_artifact("../escape", fingerprint(data))
    with Source(tmp_path) as source, pytest.raises(ValueError, match="SHA-256"):
        source.read_json("manifest.json", fingerprint(b'{"value": 8}'))


def test_s3_configured_endpoint_and_source_isolation(origin: tuple[str, OriginState]) -> None:
    url, state = origin
    options = {
        "config": {"endpoint": url, "region": "us-east-1", "access_key_id": "fixture", "secret_access_key": "fixture"},
        "client_options": {"allow_http": True},
    }
    with Source("s3://fixture/root", storage_options=options) as source:
        stream = source.open_artifact("file", fingerprint(state["data"]), bounded=True)
        stream.seek(300000)
        assert stream.read(17) == state["data"][300000:300017]
        options["config"]["endpoint"] = url + "/different"
        with Source("s3://fixture/root", storage_options=options) as other:
            assert source.identity != other.identity


@pytest.mark.parametrize("protocol", ["http", "s3"])
def test_dataset_remote_fixture(origin: tuple[str, OriginState], hfx: Path, tmp_path: Path, protocol: str) -> None:
    from pourpoint import Dataset

    url, state = origin
    state["files"] = {path.name: path.read_bytes() for path in hfx.iterdir() if path.is_file()}
    options = {"client_options": {"allow_http": True}}
    root = url
    if protocol == "s3":
        root = "s3://fixture/root"
        options["config"] = {
            "endpoint": url,
            "region": "us-east-1",
            "access_key_id": "fixture",
            "secret_access_key": "fixture",
        }
    with Dataset.open(root, storage_options=options, cache_dir=tmp_path / "download") as data:
        assert set(data.upstream(40).ids) == {10, 20, 30, 40}
        assert data.attributes([40, 10, 40])["id"].to_pylist() == [40, 10, 40]
        assert data.polygons([10]).decode().ids.tolist() == [10]
        assert data.snap_candidates("fine", (0.4, 0.4, 0.7, 0.6)).ids.tolist() == [101, 102]
    assert state["requests"]
    assert all(item[2:] == ('"v1"', "identity") for item in state["requests"])
    assert all(signed == (protocol == "s3") for signed in state["signed"])


@pytest.mark.parametrize("format_name", ["ipc", "parquet"])
def test_arrow_adapters_read_selected_blocks(origin: tuple[str, OriginState], format_name: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    url, state = origin
    schema = pa.schema([("id", pa.int64()), ("payload", pa.binary())])
    sink = pa.BufferOutputStream()
    writer = (
        pa.ipc.new_file(sink, schema, options=pa.ipc.IpcWriteOptions(compression="zstd"))
        if format_name == "ipc"
        else pq.ParquetWriter(sink, schema)
    )
    with writer:
        for index in range(12):
            ids = list(range(index * 4096, (index + 1) * 4096))
            table = pa.table(
                {"id": ids, "payload": [hashlib.sha256(str(value).encode()).digest() * 8 for value in ids]},
                schema=schema,
            )
            if format_name == "ipc":
                writer.write_batch(table.to_batches()[0])
            else:
                writer.write_table(table)
    state["data"] = sink.getvalue().to_pybytes()
    with Source(url, storage_options={"client_options": {"allow_http": True}}) as source:
        if format_name == "ipc":
            reader = pa.ipc.open_file(source.open_artifact("file", fingerprint(state["data"]), bounded=True))
            selected = reader.get_batch(7)
        else:
            reader = source.parquet("file", fingerprint(state["data"]))
            selected = reader.read_row_group(7)
        assert selected.column("id")[0].as_py() == 7 * 4096
        assert sum(request[1] for request in state["requests"]) < len(state["data"]) // 3
        assert len(state["requests"]) <= 5


def test_incomplete_graph_not_published(origin: tuple[str, OriginState], tmp_path: Path) -> None:
    url, state = origin
    state["mode"] = "short"
    with Source(url, storage_options={"client_options": {"allow_http": True}}) as source, pytest.raises(ValueError):
        source.graph_path(tmp_path, "graph", fingerprint(state["data"]))
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.part"))


def test_bounded_digest_and_changed_local_file(tmp_path: Path) -> None:
    data = b"a" * 200000
    path = tmp_path / "file"
    path.write_bytes(data)
    with Source(tmp_path) as source, pytest.raises(ValueError, match="bounded SHA-256"):
        source.open_artifact("file", fingerprint(b"b" * len(data)), bounded=True)
    with Source(tmp_path) as source:
        stream = source.open_artifact("file", fingerprint(data), bounded=True)
        path.write_bytes(b"changed")
        with pytest.raises(ValueError, match="changed"):
            stream.read(1)


def test_graph_identity_detects_mutation_without_rehash(tmp_path: Path) -> None:
    data = b"original"
    graph = tmp_path / "graph.parquet"
    graph.write_bytes(data)
    with Source(tmp_path) as source:
        selected = source.graph_path(tmp_path / "cache", "graph.parquet", fingerprint(data))
        source.check_graph(selected)
        graph.write_bytes(b"modified")
        with pytest.raises(ValueError, match="changed"):
            source.check_graph(selected)


def test_cached_graph_identity_detects_replacement(origin: tuple[str, OriginState], tmp_path: Path) -> None:
    url, state = origin
    with Source(url, storage_options={"client_options": {"allow_http": True}}) as source:
        graph = source.graph_path(tmp_path, "graph", fingerprint(state["data"]))
        source.check_graph(graph)
        replacement = tmp_path / "replacement"
        replacement.write_bytes(state["data"])
        replacement.replace(graph)
        with pytest.raises(ValueError, match="changed"):
            source.check_graph(graph)


def test_graph_change_during_verification_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import BinaryIO

    data = b"original"
    graph = tmp_path / "graph.parquet"
    graph.write_bytes(data)
    original_digest = hashlib.file_digest

    def changed_digest(stream: BinaryIO, algorithm: str) -> object:
        digest = original_digest(stream, algorithm)
        graph.write_bytes(b"modified-after-hash")
        return digest

    monkeypatch.setattr(hashlib, "file_digest", changed_digest)
    with Source(tmp_path) as source, pytest.raises(ValueError, match="changed during verification"):
        source.graph_path(tmp_path / "cache", "graph.parquet", fingerprint(data))


def test_http_manifest_revalidation_and_snapshot(origin: tuple[str, OriginState]) -> None:
    url, state = origin
    state["data"] = b'{"snapshot": 1}'
    with Source(url, storage_options={"client_options": {"allow_http": True}}) as source:
        assert source.read_json("manifest.json") == {"snapshot": 1}
        state["data"] = b'{"snapshot": 2}'
        assert source.read_json("manifest.json") == {"snapshot": 1}
    with Source(url, storage_options={"client_options": {"allow_http": True}}) as source:
        assert source.read_json("manifest.json") == {"snapshot": 2}
    assert state["cache_controls"] == ["no-cache", "no-cache"]


# Rasterio 1.5.2's Cython callback also emits an unraisable diagnostic when
# obstore rejects a changed ETag. The operation still MUST raise RasterioIOError.
@pytest.mark.filterwarnings(
    "ignore:Exception ignored in.*rasterio._err.chaining_error_handler:pytest.PytestUnraisableExceptionWarning"
)
@pytest.mark.parametrize("protocol", ["http", "s3"])
def test_raster_transport_pins_reuse_and_mutation(origin: tuple[str, OriginState], hfx: Path, protocol: str) -> None:
    import numpy as np
    from conftest import add_pair
    from rasterio.errors import RasterioIOError
    from rasterio.transform import from_origin

    from pourpoint import Dataset

    # Uncompressed tiles ensure the later selection is outside opener readahead.
    add_pair(
        hfx,
        direction=np.full((1024, 1024), 8, dtype="uint8"),
        accumulation=np.full((1024, 1024), 16777217, dtype="int32"),
        transform=from_origin(0, 1024, 1, 1),
    )
    url, state = origin
    state["files"] = {path.name: path.read_bytes() for path in hfx.iterdir() if path.is_file()}
    options = {"client_options": {"allow_http": True}, "retry_config": {"max_retries": 0}}
    root = url
    if protocol == "s3":
        root = "s3://fixture/root"
        options["config"] = {
            "endpoint": url,
            "region": "us-east-1",
            "access_key_id": "fixture",
            "secret_access_key": "fixture",
        }
    with Dataset.open(root, storage_options=options) as data:
        request = data.rasters.prepare((0, 1023, 1, 1024))
        result = data.rasters.read(request)
        assert result.accumulation[0, 0] == 16777217
        count = len(state["requests"])
        state["mode"] = "etag"
        # Cached original bytes are still a coherent pinned snapshot.
        assert data.rasters.read(request).direction[0, 0] == 1
        assert len(state["requests"]) == count
        with pytest.raises((RasterioIOError, ValueError, OSError), match="[Rr]ead|ETag|changed|IReadBlock"):
            data.rasters.read(data.rasters.prepare((500, 500, 501, 501)))
    assert all(item[2:] == ('"v1"', "identity") for item in state["requests"])
    assert all(signed == (protocol == "s3") for signed in state["signed"])
    assert sum(item[1] for item in state["requests"]) < 1024 * 1024


def test_independent_cursors_accounting_and_close(tmp_path: Path) -> None:
    data = b"x" * 200000
    (tmp_path / "file").write_bytes(data)
    with Source(tmp_path) as source:
        with (
            source.raster_file("file", fingerprint(data)) as first,
            source.raster_file("file", fingerprint(data)) as second,
        ):
            first.seek(50000)
            assert first.read(1) == b"x"
            assert second.tell() == 0
            assert source.buffer_bytes >= 65536
        assert source.buffer_bytes == 0
