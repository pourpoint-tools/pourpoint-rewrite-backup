"""Independent snap v2 cases; deliberately tiny row groups exercise pruning."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import Point

from snap_access import snap_candidates


def write_snap(path: Path, *, bbox: bool = True, statistics: bool = True) -> pa.Table:
    xs = [0.0, 50.0, 60.0, 61.0, 70.0, 71.0]
    data: dict[str, object] = {
        "id": pa.array([11, 12, 13, 14, 15, 16], type=pa.int64()),
        "unit_id": pa.array([101, 101, 103, 104, 101, 106], type=pa.int64()),
        "weight": pa.array([1.0] * 6, type=pa.float32()),
        "geometry": pa.array(
            [
                Point(0 if i == 4 else x, 0 if i == 4 else x).wkb
                for i, x in enumerate(xs)
            ],
            type=pa.binary(),
        ),
    }
    if bbox:
        bbox_type = pa.struct(
            [
                pa.field(k, pa.float32(), nullable=False)
                for k in ("xmin", "ymin", "xmax", "ymax")
            ]
        )
        data["bbox"] = pa.array(
            [
                None if i >= 4 else dict.fromkeys(("xmin", "ymin", "xmax", "ymax"), x)
                for i, x in enumerate(xs)
            ],
            type=bbox_type,
        )
    table = pa.table(data)
    pq.write_table(table, path, row_group_size=2, write_statistics=statistics)
    return table


@pytest.mark.parametrize("explicit", [False, True])
def test_bbox_fallback_and_many_to_one(tmp_path: Path, explicit: bool) -> None:
    path = tmp_path / "snap.parquet"
    write_snap(path)
    result, measured = snap_candidates(path, (-1, -1, 1, 1), explicit)
    assert result["id"].to_pylist() == [11, 15]
    assert result["unit_id"].to_pylist() == [101, 101]
    assert measured["candidate_groups"] == 2
    assert measured["candidate_rows"] == 4
    assert measured["selected_rows"] == 2
    assert measured["pre_fallback_selected_rows"] == measured["decoded_geometries"] == 3
    assert measured["fallback_decoded_geometries"] == 2
    assert measured["selected_wkb_bytes"] == 42
    assert measured["materialized_wkb_bytes"] == (84 if explicit else "not measured")
    chunk_bytes = measured["candidate_column_chunk_compressed_bytes"]
    assert isinstance(chunk_bytes, int) and chunk_bytes > 0
    assert measured["wire_bytes"] == "not measured"
    for key in ("read_filter_seconds", "decode_fallback_seconds"):
        value = measured[key]
        assert isinstance(value, float) and value >= 0
    for key in (
        "candidate_arrow_logical_bytes",
        "candidate_arrow_retained_buffer_bytes",
    ):
        value = measured[key]
        if explicit:
            assert isinstance(value, int) and value > 0
        else:
            assert value == "not measured"


@pytest.mark.parametrize("explicit", [False, True])
def test_missing_bbox_decodes_every_feature(tmp_path: Path, explicit: bool) -> None:
    path = tmp_path / "snap.parquet"
    source = write_snap(path, bbox=False)
    result, measured = snap_candidates(path, (-1, -1, 1, 1), explicit)
    assert result.equals(source.take(pa.array([0, 4])))
    assert measured["candidate_rows"] == measured["pre_fallback_selected_rows"] == 6
    assert measured["selected_rows"] == 2
    assert measured["candidate_groups"] == 3
    assert measured["decoded_geometries"] == 6


@pytest.mark.parametrize("explicit", [False, True])
def test_missing_statistics_does_not_exclude_rows(
    tmp_path: Path, explicit: bool
) -> None:
    path = tmp_path / "snap.parquet"
    write_snap(path, statistics=False)
    result, measured = snap_candidates(path, (0, 0, 0, 0), explicit)
    assert result["id"].to_pylist() == [11, 15]
    assert measured["candidate_groups"] == 3


@pytest.mark.parametrize("explicit", [False, True])
def test_no_candidates(tmp_path: Path, explicit: bool) -> None:
    path = tmp_path / "snap.parquet"
    table = write_snap(path).slice(0, 4)
    pq.write_table(table, path, row_group_size=2)
    result, measured = snap_candidates(path, (90, 90, 91, 91), explicit)
    assert result.schema == table.schema
    assert result.num_rows == measured["decoded_geometries"] == 0
    assert measured["candidate_groups"] == measured["candidate_rows"] == 0


@pytest.mark.parametrize("explicit", [False, True])
def test_only_selected_geometry_is_decoded(tmp_path: Path, explicit: bool) -> None:
    path = tmp_path / "snap.parquet"
    table = write_snap(path)
    # The second row shares a candidate group, but is rejected by its bbox.
    geometry = table["geometry"].to_pylist()
    geometry[1] = b"not WKB"
    table = table.set_column(
        table.schema.get_field_index("geometry"),
        "geometry",
        pa.array(geometry, type=pa.binary()),
    )
    pq.write_table(table, path, row_group_size=2)
    result, measured = snap_candidates(path, (0, 0, 0, 0), explicit)
    assert result["id"].to_pylist() == [11, 15]
    assert measured["decoded_geometries"] == 3


@pytest.mark.parametrize("explicit", [False, True])
def test_all_null_bbox_group_is_retained(tmp_path: Path, explicit: bool) -> None:
    path = tmp_path / "snap.parquet"
    table = write_snap(path).slice(0, 2)
    table = table.set_column(
        table.schema.get_field_index("bbox"),
        "bbox",
        pa.array([None, None], type=table.schema.field("bbox").type),
    )
    pq.write_table(table, path)
    result, measured = snap_candidates(path, (90, 90, 91, 91), explicit)
    assert result["id"].to_pylist() == []
    assert measured["pre_fallback_selected_rows"] == 2
    assert measured["candidate_groups"] == 1
    assert measured["decoded_geometries"] == 2
