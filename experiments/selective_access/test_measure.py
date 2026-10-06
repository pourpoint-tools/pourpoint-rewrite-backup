"""End-to-end output traceability on an independent tiny compiled-data shape."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import Point, box

from measure import run


def test_complete_measurement(tmp_path: Path) -> None:
    manifest = {
        "format_version": "0.3.0",
        "unit_count": 4,
        "auxiliary": [
            {
                "schema": "hfx.aux.snap.v2",
                "artifacts": {"snap": "snap.parquet"},
                "metadata": {"references_levels": [1]},
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "objects": [
                    {"relative_path": "catchments.parquet", "sha256": "synthetic"}
                ]
            }
        )
    )
    pq.write_table(
        pa.table(
            {
                "id": [901, 20, 777, 3],
                "level": [0, 1, 1, 1],
                "parent_id": pa.array([None, 901, 901, 901], type=pa.int64()),
                "up_area_km2": pa.array([None, 2.0, 3.0, 4.0], type=pa.float32()),
                "geometry": [box(i, 0, i + 1, 1).wkb for i in range(4)],
            }
        ),
        tmp_path / "catchments.parquet",
        row_group_size=2,
    )
    pq.write_table(
        pa.table(
            {
                "id": [10, 11],
                "unit_id": [20, 20],
                "weight": [1.0, 2.0],
                "bbox": [{"xmin": 0.0, "ymin": 0.0, "xmax": 0.0, "ymax": 0.0}] * 2,
                "geometry": [Point(0, 0).wkb] * 2,
            }
        ),
        tmp_path / "snap.parquet",
    )
    result = run(tmp_path, receipt, "synthetic correctness test")
    assert result["status"] == "passed"
    queries = result["queries"]
    assert isinstance(queries, list) and len(queries) == 16
    assert len(str(result["code_commit"])) == 40
    assert result["remote_requests"] == "not measured"
    json.dumps(result)
