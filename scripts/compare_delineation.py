"""Untimed source-membership and footprint comparison of saved public-API results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fsspec
import pyarrow.parquet as pq
import shapely
from shapely.geometry import Point, Polygon


def compare(python: dict[str, Any], rust: dict[str, Any], old_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare source identities, never dataset-local numeric IDs."""
    expected = python["validation"][0]
    actual = rust["validation"][0]
    rows = {row["id"]: row for row in old_rows}
    assert set(rows) == set(actual["ids"]), "missing or extra old source rows"
    keys = {source_id for namespace, source_id in expected["source_keys"] if ":reach_catchments:" in namespace}
    assert len(keys) == len(expected["ids"]), "nonunique or nonreach Python source keys"
    assert all(row["level"] == 1 and row["source_id"].startswith("reach:") for row in rows.values())
    old_keys = {row["source_id"].removeprefix("reach:") for row in rows.values()}
    terminal = rows[actual["terminal_unit_id"]]["source_id"].removeprefix("reach:")
    return {
        "membership_equal": keys == old_keys,
        "python_only": sorted(keys - old_keys),
        "rust_only": sorted(old_keys - keys),
        "outlet_source_equal": terminal
        in {key[1] for key in expected.get("selected_source_keys", [["", python["target"]["source_id"]]])},
        "old_source_rows": old_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--rust", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    python, rust = json.loads(args.python.read_text()), json.loads(args.rust.read_text())
    ids = set(rust["validation"][0]["ids"])
    rows: list[dict[str, Any]] = []
    with fsspec.open(rust["source"] + "catchments.parquet", "rb") as stream:
        file = pq.ParquetFile(stream)
        id_column = file.schema.names.index("id")
        for i in range(file.num_row_groups):
            stats = file.metadata.row_group(i).column(id_column).statistics
            if stats is not None and not any(stats.min <= unit <= stats.max for unit in ids):
                continue
            rows.extend(
                row
                for row in file.read_row_group(i, columns=["id", "level", "source_id", "geometry"]).to_pylist()
                if row["id"] in ids
            )
    terminal = next(row for row in rows if row["id"] == rust["validation"][0]["terminal_unit_id"])
    p = Point(python["target"]["lon"], python["target"]["lat"])
    interior = shapely.from_wkb(terminal["geometry"]).contains(p)
    for row in rows:
        del row["geometry"]
    report = compare(python, rust, rows)
    a, b = (shapely.from_wkb(path.with_suffix(".wkb").read_bytes()) for path in [args.python, args.rust])
    polygons_a = list(a.geoms) if a.geom_type == "MultiPolygon" else [a]
    polygons_b = list(b.geoms) if b.geom_type == "MultiPolygon" else [b]
    filled_a = shapely.union_all([Polygon(p.exterior) for p in polygons_a])
    report.update(
        python_holes=sum(len(p.interiors) for p in polygons_a),
        rust_holes=sum(len(p.interiors) for p in polygons_b),
        python_hole_area_degrees2=sum(Polygon(ring).area for p in polygons_a for ring in p.interiors),
        hole_filled_python_difference_degrees2=filled_a.symmetric_difference(b).area,
        rust_outlet_interior=interior,
        footprints_exact=bool(a.equals(b)),
        symmetric_difference_degrees2=a.symmetric_difference(b).area,
        relative_symmetric_difference=a.symmetric_difference(b).area / a.union(b).area,
        hausdorff_degrees=a.hausdorff_distance(b),
        python_area_degrees2=a.area,
        rust_area_degrees2=b.area,
    )
    args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
