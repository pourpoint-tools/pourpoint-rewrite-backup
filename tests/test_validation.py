from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import refresh_fingerprint, rewrite_catchments
from shapely.geometry import Point

from pourpoint import Dataset


@pytest.mark.parametrize(
    "name,values,match",
    [
        ("level", [0, 2, 1, 1, 1], "level"),
        ("parent_id", [None, 40, 9000000000, 9000000000, 9000000000], "parent_id"),
        ("parent_id", [None, 999, 9000000000, 9000000000, 9000000000], "parent_id"),
        ("outlet_lon", [4, 181, 1, 4, 2], "outlet_lon"),
        ("area_km2", [4, float("nan"), 1, 1, 1], "area_km2"),
    ],
)
def test_only_consumed_catchment_records_are_validated(hfx: Path, name: str, values: list[object], match: str) -> None:
    rewrite_catchments(hfx, name, values)
    with Dataset.open(hfx) as data:
        assert data.attributes([10])["id"].to_pylist() == [10]
        with pytest.raises(ValueError, match=match):
            data.attributes([30])


def test_geometry_type_is_checked_at_selected_decode(hfx: Path) -> None:
    with pa.ipc.open_file(hfx / "catchments.arrow") as reader:
        values = reader.read_all()["geometry"].to_pylist()
    values[1] = Point(2, 0).wkb
    rewrite_catchments(hfx, "geometry", values)
    with Dataset.open(hfx) as data:
        assert data.attributes([30], ["id"]).num_rows == 1
        assert data.polygons([10]).decode().ids.tolist() == [10]
        with pytest.raises(ValueError, match="geometry.*30"):
            data.polygons([30]).decode()


@pytest.mark.parametrize(
    "name,values,match",
    [
        ("unit_id", [999, 10, 40, 20], "unit_id"),
        ("unit_id", [9000000000, 10, 40, 20], "references_levels"),
        ("weight", [float("nan"), 2, 3, 4], "weight"),
        ("stem_role", ["mystery", None, None, None], "stem_role"),
        ("id", [101, 101, 103, 104], "duplicate"),
    ],
)
def test_selected_snap_records_are_validated(hfx: Path, name: str, values: list[object], match: str) -> None:
    path = hfx / "fine.parquet"
    table = pq.read_table(path)
    field = table.schema.field(name)
    table = table.set_column(table.schema.get_field_index(name), field, pa.array(values, type=field.type))
    pq.write_table(table, path, row_group_size=2)
    refresh_fingerprint(hfx, "fine.parquet")
    with Dataset.open(hfx) as data, pytest.raises(ValueError, match=match):
        data.snap_candidates("fine", (0, 0, 1, 1))
