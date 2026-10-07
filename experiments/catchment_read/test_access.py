from pathlib import Path

import pyarrow as pa
import pytest

from access import CatchmentReader


def test_baseline_ids_only_prepare_and_ordered_batched_retrieval(
    source: tuple[Path, pa.Table],
) -> None:
    path, expected = source
    with CatchmentReader(path) as reader:
        reader.prepare()
        assert reader.retained_locator_bytes == 4 * 16 + 2 * 8
        outputs, stats = reader.retrieve([[3, 901, 3], [20, 3]])
        assert outputs[0].equals(expected.take([3, 0, 3]))
        assert outputs[1].equals(expected.take([1, 3]))
        assert stats["data_blocks"] == [0, 1]
        assert stats["lookup_batches"] == []
        again, _ = reader.retrieve([[3, 901, 3]])
        assert again[0].equals(outputs[0])
        assert reader.retrieve([[]])[0][0].equals(expected.slice(0, 0))
        for missing in [0, -1, 21, 999]:
            with pytest.raises(KeyError):
                reader.retrieve([[3, missing]])


@pytest.fixture(params=["a-index", "b-arrow"])
def candidate(
    source: tuple[Path, pa.Table], request: pytest.FixtureRequest
) -> tuple[Path, pa.Table]:
    import hashlib
    import json

    from pyarrow import ipc

    path, table = source
    kind = request.param
    data_name = "catchments.parquet" if kind == "a-index" else "catchments.arrow"
    if kind == "b-arrow":
        with ipc.new_file(path / data_name, table.schema) as writer:
            for batch in table.to_batches(max_chunksize=2):
                writer.write_batch(batch)
    digest = hashlib.sha256((path / data_name).read_bytes()).hexdigest()
    lookup = pa.table(
        {
            "id": pa.array([3, 20, 777, 901], type=pa.int64()),
            "block": pa.array([1, 0, 1, 0], type=pa.int32()),
            "row": pa.array([1, 1, 0, 0], type=pa.int32()),
        }
    )
    lookup = lookup.replace_schema_metadata(
        {b"hfx:catchments_sha256": digest.encode(), b"hfx:candidate": kind.encode()}
    )
    with ipc.new_file(path / "lookup.arrow", lookup.schema) as writer:
        for batch in lookup.to_batches(max_chunksize=2):
            writer.write_batch(batch)
    (path / "lookup-directory.json").write_text(
        json.dumps(
            {
                "version": 1,
                "batches": [
                    {"batch": 0, "min_id": 3, "max_id": 20, "rows": 2},
                    {"batch": 1, "min_id": 777, "max_id": 901, "rows": 2},
                ],
            }
        )
    )
    artifacts = {}
    for name in [data_name, "lookup.arrow", "lookup-directory.json"]:
        raw = (path / name).read_bytes()
        artifacts[name] = {
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "head_sha256": hashlib.sha256(raw[:65536]).hexdigest(),
            "tail_sha256": hashlib.sha256(raw[-65536:]).hexdigest(),
        }
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": "0.4.0",
                "experimental": True,
                "candidate": kind,
                "catchments": data_name,
                "lookup": "lookup.arrow",
                "directory": "lookup-directory.json",
                "artifacts": artifacts,
            }
        )
    )
    return path, table


def test_candidate_selects_only_needed_lookup_and_shared_blocks(
    candidate: tuple[Path, pa.Table],
) -> None:
    path, expected = candidate
    with CatchmentReader(path, experimental=True) as reader:
        reader.prepare()
        assert reader.retained_locator_bytes == 0
        outputs, stats = reader.retrieve([[20, 3, 20], [3]])
        assert outputs[0].equals(expected.take([1, 3, 1]))
        assert outputs[1].equals(expected.take([3]))
        assert stats["lookup_batches"] == [0]
        assert stats["data_blocks"] == [0, 1]
        outputs, stats = reader.retrieve([[901, 777, 20, 3]])
        assert outputs[0].equals(expected.take([0, 2, 1, 3]))
        assert stats["lookup_batches"] == [0, 1]
        assert stats["data_blocks"] == [0, 1]
        for missing in [0, 4, 21, 778, 999]:
            with pytest.raises(KeyError):
                reader.retrieve([[missing]])
        assert reader.retrieve([[]])[0][0].equals(expected.slice(0, 0))


def test_candidate_requires_explicit_opt_in(candidate: tuple[Path, pa.Table]) -> None:
    with pytest.raises(ValueError, match="opt-in"):
        CatchmentReader(candidate[0])


def test_bounded_identity_rejects_changed_artifact(
    candidate: tuple[Path, pa.Table],
) -> None:
    path, _ = candidate
    with (path / "lookup.arrow").open("r+b") as file:
        file.write(b"BROKEN")
    with (
        pytest.raises(ValueError, match="fingerprint"),
        CatchmentReader(path, experimental=True) as reader,
    ):
        reader.prepare()


def test_baseline_preparation_projects_only_ids(
    source: tuple[Path, pa.Table], monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    with CatchmentReader(source[0]) as reader:
        assert reader.parquet is not None
        read = Mock(wraps=reader.parquet.read)
        monkeypatch.setattr(reader.parquet, "read", read)
        reader.prepare()
        read.assert_called_once_with(columns=["id"], use_threads=False)


def test_candidate_io_boundary_has_no_hidden_lookup_or_block_scans(
    candidate: tuple[Path, pa.Table],
) -> None:
    from measure import io_delta

    with CatchmentReader(candidate[0], experimental=True) as reader:
        before = reader.io_counts()
        reader.prepare()
        prep = io_delta(reader.io_counts(), before)
        data_name = reader.manifest["catchments"]
        size = (candidate[0] / data_name).stat().st_size
        assert prep[data_name] == {"reads": 2, "bytes": 2 * min(65536, size)}
        # Bounded lookup fingerprints plus the two IPC footer reads; no get_batch.
        assert prep["lookup.arrow"]["reads"] == 4
        before = reader.io_counts()
        reader.retrieve([[20, 3, 20], [3]])
        first = io_delta(reader.io_counts(), before)
        # Both IDs are in lookup batch 0; repeated caller IDs share this one read.
        assert first["lookup.arrow"]["reads"] == 1
        if reader.parquet is not None:
            metadata = reader.parquet.metadata
            assert first[data_name] == {
                "reads": 2 * metadata.num_columns,
                "bytes": sum(
                    metadata.row_group(g).column(c).total_compressed_size
                    for g in [0, 1]
                    for c in range(metadata.num_columns)
                ),
            }
        else:
            assert first[data_name]["reads"] == 2
        before = reader.io_counts()
        reader.retrieve([[20, 3, 20], [3]])
        assert io_delta(reader.io_counts(), before) == first
