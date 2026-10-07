from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import box


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, pa.Table]:
    table = pa.table(
        {
            "id": pa.array([901, 20, 777, 3], type=pa.int64()),
            "level": pa.array([0, 1, 1, 1], type=pa.int16()),
            "parent_id": pa.array([None, 901, 901, 901], type=pa.int64()),
            "up_area_km2": pa.array([None, 2, None, 4], type=pa.float32()),
            "source_id": ["root", None, "third", "last"],
            "geometry": [box(i, 0, i + 1, 1).wkb for i in range(4)],
        }
    )
    (tmp_path / "manifest.json").write_text('{"format_version":"0.3.0"}')
    pq.write_table(table, tmp_path / "catchments.parquet", row_group_size=2)
    return tmp_path, table
