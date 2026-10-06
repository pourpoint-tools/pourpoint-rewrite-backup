from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import box

from selective import Locator, buffers, polygons


@pytest.fixture
def catchments(tmp_path: Path) -> Path:
    path = tmp_path / "catchments.parquet"
    pq.write_table(
        pa.table(
            {
                "id": pa.array([901, 20, 777, 3], type=pa.int64()),
                "level": pa.array([0, 1, 1, 1], type=pa.int16()),
                "parent_id": pa.array([None, 901, 901, 901], type=pa.int64()),
                "up_area_km2": pa.array([None, 2, None, 4], type=pa.float32()),
                "geometry": [box(i, 0, i + 1, 1).wkb for i in range(4)],
            }
        ),
        path,
        row_group_size=2,
    )
    return path


@pytest.mark.parametrize("explicit", [False, True])
def test_non_row_ids_nulls_and_geometry_alignment(
    catchments: Path, explicit: bool
) -> None:
    locator = Locator.load(catchments)
    # Graph order can be [3,777,901,20]; it does not enter the catchment locator.
    assert locator.groups([3, 901]) == [0, 1]
    table, metrics = polygons(catchments, locator, [3, 901], explicit)
    assert sorted(table["id"].to_pylist()) == [3, 901]
    by_id = dict(zip(table["id"].to_pylist(), table["geometry"].to_pylist()))
    assert by_id[3] == box(3, 0, 4, 1).wkb
    assert by_id[901] == box(0, 0, 1, 1).wkb
    assert metrics["decoded_geometry_count"] == 2
    assert locator.attributes["parent_id"].to_pylist() == [None, 901, 901, 901]
    assert locator.attributes["up_area_km2"].null_count == 2
    assert "geometry" not in locator.attributes.column_names


@pytest.mark.parametrize("ids", [[-1], [0], [42], [3, 42]])
def test_missing_id_error(catchments: Path, ids: list[int]) -> None:
    with pytest.raises(KeyError):
        Locator.load(catchments).groups(ids)


def test_empty_and_duplicate_selection(catchments: Path) -> None:
    locator = Locator.load(catchments)
    assert polygons(catchments, locator, [], True)[0].num_rows == 0
    assert polygons(catchments, locator, [3, 3], False)[0].num_rows == 1


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.parquet"
    pq.write_table(
        pa.table(
            {
                "id": [8, 8],
                "level": [0, 0],
                "parent_id": pa.array([None, None], type=pa.int64()),
                "up_area_km2": [1.0, 2.0],
            }
        ),
        path,
    )
    with pytest.raises(ValueError, match="unique positive"):
        Locator.load(path)


def test_retained_backing() -> None:
    table = pa.table({"x": np.arange(100, dtype=np.int64)}).slice(0, 1)
    assert buffers(table) == {"logical_bytes": 8, "retained_backing_bytes": 800}
