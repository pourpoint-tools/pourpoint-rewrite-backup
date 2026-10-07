from pathlib import Path

import pyarrow as pa
import pytest
from pyarrow import ipc

from http_access import ipc_blocks, merge_ranges


def test_ranges_coalesce_only_within_gap_and_budget() -> None:
    assert merge_ranges([(100, 110), (0, 10), (12, 20)], gap=2, limit=25) == [
        (0, 20),
        (100, 110),
    ]


@pytest.mark.parametrize("compression", [None, "zstd"])
def test_ipc_footer_blocks_match_arrow_batches(
    tmp_path: Path, compression: str | None
) -> None:
    path = tmp_path / "data.arrow"
    table = pa.table({"id": [9, 2, 70, 8]})
    with ipc.new_file(
        path, table.schema, options=ipc.IpcWriteOptions(compression=compression)
    ) as writer:
        for batch in table.to_batches(max_chunksize=2):
            writer.write_batch(batch)
    raw = path.read_bytes()
    size = int.from_bytes(raw[-10:-6], "little")
    blocks = ipc_blocks(raw[-10 - size : -10], len(raw))
    assert len(blocks) == 2
    for number, (start, end) in enumerate(blocks):
        batch = ipc.read_record_batch(pa.BufferReader(raw[start:end]), table.schema)
        assert batch.equals(table.to_batches(max_chunksize=2)[number])
    with pytest.raises(ValueError):
        ipc_blocks(raw[-10 - size : -10], 10)


@pytest.mark.parametrize("fault", ["ignored", "range", "truncated", "changed"])
def test_range_failures_close(fault: str) -> None:
    import httpx

    from http_access import Transport

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": "10", "ETag": '"v1"'})
        assert request.headers["If-Match"] == '"v1"'
        return httpx.Response(
            200 if fault == "ignored" else 206,
            headers={
                "Content-Length": "3",
                "Content-Range": "bytes 3-5/10" if fault == "range" else "bytes 2-4/10",
                "ETag": '"v2"' if fault == "changed" else '"v1"',
            },
            content=b"23" if fault == "truncated" else b"234",
        )

    transport = Transport(
        "http://fixture", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    try:
        with pytest.raises(ValueError, match="range response"):
            transport.file("data").fetch((2, 5))
    finally:
        transport.close()


def test_prefetch_four_actual_overlapping_requests_and_no_data_cache() -> None:
    from threading import Barrier, Lock

    import httpx

    from http_access import Transport

    barrier, lock = Barrier(4, timeout=5), Lock()
    active = peak = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        if request.method == "HEAD":
            return httpx.Response(
                200, headers={"Content-Length": "400000", "ETag": '"v1"'}
            )
        start, end = map(int, request.headers["Range"][6:].split("-"))
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait()
        with lock:
            active -= 1
        return httpx.Response(
            206,
            headers={
                "Content-Length": str(end - start + 1),
                "Content-Range": f"bytes {start}-{end}/400000",
                "ETag": '"v1"',
            },
            content=b"x" * (end - start + 1),
        )

    transport = Transport(
        "http://fixture", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    try:
        file = transport.file("data")
        file.sealed = True
        ranges = [(n * 100000, n * 100000 + 10) for n in range(4)]
        for _ in range(2):
            file.prefetch(ranges)
            file.seek(0)
            assert file.read(10) == b"x" * 10
            file.clear()
            file.seek(0)
            with pytest.raises(ValueError, match="unplanned"):
                file.read(10)
        assert peak == 4
        assert transport.io_counts() == {"data": {"reads": 9, "bytes": 80}}
    finally:
        transport.close()


def test_content_encoding_and_weak_identity_rejected() -> None:
    import httpx

    from http_access import Transport

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Length": "10", "ETag": 'W/"weak"'}
            )
        )
    )
    transport = Transport("http://fixture", client=client)
    try:
        with pytest.raises(ValueError, match="strong ETag"):
            transport.file("data")
    finally:
        transport.close()


@pytest.mark.parametrize("footer", [b"", b"\xff" * 32, b"\x04\0\0\0"])
def test_malformed_ipc_footer_fails(footer: bytes) -> None:
    with pytest.raises(ValueError):
        ipc_blocks(footer, 1000)


def test_oversized_blocks_stay_whole_without_adding_gap() -> None:
    assert merge_ranges([(0, 20), (21, 50)], gap=10, limit=10) == [(0, 20), (21, 50)]


def test_dictionary_ipc_is_explicitly_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "dictionary.arrow"
    table = pa.table({"value": pa.array(["a", "b", "a"]).dictionary_encode()})
    with ipc.new_file(path, table.schema) as writer:
        writer.write_table(table)
    raw = path.read_bytes()
    length = int.from_bytes(raw[-10:-6], "little")
    with pytest.raises(ValueError, match="dictionary IPC"):
        ipc_blocks(raw[-10 - length : -10], len(raw))
