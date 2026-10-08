from unittest.mock import patch

import numpy as np
import pyarrow as pa
import pytest
import shapely

from pourpoint.geometry import GeometryBatch


def test_decode_preserves_identity_and_compacts_attributes() -> None:
    polygon = shapely.box(0, 0, 1, 1)
    table = pa.table(
        {
            "id": pa.array([1, 2**62, 3], type=pa.int64()),
            "source_id": ["discard" * 10000, "selected", "discard" * 10000],
            "geometry": [polygon.wkb] * 3,
        }
    ).slice(1, 1)
    batch = GeometryBatch(table, "polygon", "catchments.arrow")
    with patch("pourpoint.geometry.shapely.from_wkb", wraps=shapely.from_wkb) as decode:
        result = batch.decode()
        assert decode.call_count == 1
        batch.decode()
        assert decode.call_count == 2
    np.testing.assert_array_equal(result.ids, [2**62])
    assert shapely.equals_exact(result.geometries[0], polygon, 0)
    assert result.attributes.column_names == ["id", "source_id"]
    assert result.attributes["source_id"].to_pylist() == ["selected"]
    assert result.attributes.get_total_buffer_size() < 100
    assert not result.ids.flags.writeable
    assert not result.geometries.flags.writeable


@pytest.mark.parametrize(
    "wkb,message",
    [
        (None, "null geometry"),
        (b"invalid", "malformed"),
        (shapely.Point(0, 0).wkb, "type/empty"),
        (shapely.Polygon().wkb, "type/empty"),
        (shapely.box(180, 0, 181, 1).wkb, "EPSG:4326"),
    ],
)
def test_invalid_polygon(wkb: bytes | None, message: str) -> None:
    table = pa.table({"id": pa.array([8], type=pa.int64()), "geometry": pa.array([wkb], type=pa.binary())})
    with pytest.raises(ValueError, match=message):
        GeometryBatch(table, "polygon", "catchments.arrow").decode()


def test_snap_references_duplicates_and_empty_selection() -> None:
    table = pa.table(
        {
            "id": pa.array([2, 1, 2], type=pa.int64()),
            "unit_id": pa.array([90, 90, 90], type=pa.int64()),
            "weight": pa.array([1, 2, 1], type=pa.float32()),
            "stem_role": pa.array([None, "mainstem", None], type=pa.string()),
            "geometry": [shapely.Point(0, 0).wkb, shapely.LineString([(0, 0), (1, 1)]).wkb, shapely.Point(0, 0).wkb],
        }
    )
    decoded = GeometryBatch(table, "snap", "snap.parquet").decode()
    np.testing.assert_array_equal(decoded.ids, [2, 1, 2])
    assert decoded.attributes["unit_id"].to_pylist() == [90, 90, 90]
    assert decoded.attributes["stem_role"].null_count == 2
    empty = GeometryBatch(table.slice(0, 0), "snap", "snap.parquet").decode()
    assert empty.ids.size == empty.geometries.size == 0
