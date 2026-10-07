from pathlib import Path

import pyarrow as pa

from access import CountedFile
from measure import io_delta, measure


def test_io_counts_include_actual_returned_bytes(tmp_path: Path) -> None:
    path = tmp_path / "raw"
    path.write_bytes(b"abcdefgh")
    with CountedFile(path) as file:
        assert file.read(3) == b"abc"
        file.seek(6)
        assert file.read(99) == b"gh"
        assert file.readinto(bytearray(2)) == 0
        assert (file.reads, file.returned_bytes) == (3, 5)
    assert io_delta(
        {"x": {"reads": 4, "bytes": 13}}, {"x": {"reads": 1, "bytes": 5}}
    ) == {"x": {"reads": 3, "bytes": 8}}


def test_measure_includes_prepare_repeat_and_separate_decode(
    source: tuple[Path, pa.Table], tmp_path: Path
) -> None:
    root, _ = source
    result, tables = measure(root, [[3, 901], [3]], experimental=False)
    assert len(result["calls"]) == len(tables) == 2
    assert result["prepare"]["io"]["catchments.parquet"]["bytes"] > 0
    assert result["open"]["io"]["manifest.json"]["bytes"] > 0
    assert result["calls"][0]["stats"]["data_blocks"] == [0, 1]
    assert result["decode"]["geometries"] == 3
    assert result["through_first_retrieval_seconds"] >= result["prepare"]["seconds"]
    assert tables[0][0].equals(tables[1][0])
    assert result["calls"][1]["io"]["catchments.parquet"]["bytes"] > 0
