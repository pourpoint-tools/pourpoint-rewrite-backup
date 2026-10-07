from pathlib import Path

import pyarrow as pa
import pytest

from http_reader import HttpCatchmentReader
from range_server import RangeServer

pytest_plugins = ["test_access"]


def test_remote_baseline(source: tuple[Path, pa.Table]) -> None:
    path, expected = source
    with (
        RangeServer({"bundle": path}) as server,
        HttpCatchmentReader(server.url + "/bundle") as reader,
    ):
        reader.prepare()
        outputs, stats = reader.retrieve([[3, 901, 3], [20]])
        assert outputs[0].equals(expected.take([3, 0, 3]))
        assert outputs[1].equals(expected.take([1]))
        assert stats["data_blocks"] == [0, 1]
        assert reader.retained_locator_bytes == 80
        assert reader.retrieve([[]])[0][0].equals(expected.slice(0, 0))
        with pytest.raises(KeyError):
            reader.retrieve([[21]])


def test_remote_candidate(candidate: tuple[Path, pa.Table]) -> None:
    path, expected = candidate
    with (
        RangeServer({"bundle": path}) as server,
        HttpCatchmentReader(server.url + "/bundle", experimental=True) as reader,
    ):
        reader.prepare()
        assert reader.retained_locator_bytes == 0
        assert reader.transport.plans == []  # No candidate array pre-scan.
        before = reader.io_counts()
        outputs, stats = reader.retrieve([[20, 3, 20], [3]])
        assert outputs[0].equals(expected.take([1, 3, 1]))
        assert outputs[1].equals(expected.take([3]))
        assert stats["lookup_batches"] == [0]
        assert stats["data_blocks"] == [0, 1]
        from measure import io_delta

        first = io_delta(reader.io_counts(), before)
        assert first["lookup.arrow"]["reads"] == 1
        before = reader.io_counts()
        assert reader.retrieve([[20, 3, 20]])[0][0].equals(outputs[0])
        assert io_delta(reader.io_counts(), before) == first
        assert all(not file.data for file in reader.transport.files.values())
        for missing in [0, 4, 21, 778, 999]:
            with pytest.raises(KeyError):
                reader.retrieve([[missing]])


def test_four_range_windows_and_oversized_block(source: tuple[Path, pa.Table]) -> None:
    path, _ = source
    stride = 16 * 1024 * 1024
    with (path / "blocks.bin").open("wb") as file:
        file.truncate(6 * stride)
    ranges = [(0, 9 * 1024 * 1024)] + [
        (n * stride, n * stride + 1) for n in range(1, 6)
    ]
    with (
        RangeServer({"bundle": path}) as server,
        HttpCatchmentReader(server.url + "/bundle") as reader,
    ):
        windows = []
        for window in reader._windows("blocks.bin", list(range(6)), ranges):
            windows.append(window)
            remote = reader.file("blocks.bin")
            assert len(remote.data) <= 4
            for number in window:
                start, end = ranges[number]
                remote.seek(start)
                assert len(remote.read(end - start)) == end - start
        assert windows == [[0, 1, 2, 3], [4, 5]]
        assert reader.transport.plans[0]["oversized_ranges"] == 1
        assert not reader.file("blocks.bin").data


def test_remote_candidate_rejects_bad_fingerprint(
    candidate: tuple[Path, pa.Table],
) -> None:
    path, _ = candidate
    with (path / "lookup.arrow").open("r+b") as file:
        file.write(b"BROKEN")
    with (
        RangeServer({"bundle": path}) as server,
        HttpCatchmentReader(server.url + "/bundle", experimental=True) as reader,
        pytest.raises(ValueError, match="fingerprint"),
    ):
        reader.prepare()
