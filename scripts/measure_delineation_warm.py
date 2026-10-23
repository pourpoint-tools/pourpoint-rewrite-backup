"""Offline warm-cache follow-up to measure_delineation; sequential fixed GRIT targets."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any

import shapely
from shapely.geometry import Point

from pourpoint import HydroFabric, River, delineate
from scripts.measure_delineation import search_bounds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--snap-prototype", type=Path)
    args = parser.parse_args()
    if args.snap_prototype:
        import runpy

        import pourpoint.rivers

        pourpoint.rivers.snap = runpy.run_path(str(args.snap_prototype))["snap"]
    report: dict[str, Any] = {
        "revision": args.revision,
        "python": sys.version,
        "platform": platform.platform(),
        "versions": {n: version(n) for n in ("numpy", "pyarrow", "shapely", "pyproj")},
        "source_sha256": {
            str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in Path("src/pourpoint").glob("*.py")
        },
        "cache": "existing disk caches, offline=True; first query includes lazy preparation",
        "targets": [],
    }
    comparison = json.loads(args.compare.read_text()) if args.compare else None
    targets = json.loads(Path("docs/receipts/grit-delineation-targets.json").read_text())
    for name, cache in (
        ("australia", "python-australia-snap"),
        ("amazon", "python-amazon-snap"),
        ("spain-river", "python-spain-river"),
    ):
        target = next(t for t in targets if t["name"] == name)
        point = Point(target["lon"], target["lat"])
        start = perf_counter()
        with HydroFabric(
            "https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/",
            cache_dir=Path("scratchpad/grit-benchmark") / cache,
            offline=True,
        ) as fabric:
            entry: dict[str, Any] = {"name": name, "opening_seconds": perf_counter() - start, "queries": []}
            for _ in range(8):
                start = perf_counter()
                table = fabric.candidates("rivers", search_bounds(point.x, point.y), levels=[1])
                rivers = [River(table, row) for row in range(table.num_rows)]
                result = delineate(
                    fabric=fabric, target=point, level=2, snap_onto=rivers, radius=1000, refine=False, merge=True
                )
                elapsed = perf_counter() - start
                assert result.geometry is not None and result.geometry.is_valid
                entry["queries"].append(
                    {
                        "seconds": elapsed,
                        "ids": [u.id for u in result.subbasins],
                        "attributes_sha256": hashlib.sha256(
                            repr([dict(u.attributes) for u in result.subbasins]).encode()
                        ).hexdigest(),
                        "snap": list(result.target.coords[0]),
                        "wkb_sha256": hashlib.sha256(result.geometry.wkb).hexdigest(),
                        "transfer": asdict(fabric.stats),
                    }
                )
            if comparison:
                expected = next(t for t in comparison["targets"] if t["name"] == name)["queries"][0]
                for query in entry["queries"]:
                    for key in ("ids", "attributes_sha256", "snap", "wkb_sha256"):
                        assert query[key] == expected[key], (name, key)
                entry["exact_baseline_match"] = True
            if args.diagnostics:
                from pourpoint.spatial import RowGroupBounds

                entry["preparation"] = []
                for domain, reader in fabric._vectors.items():
                    start = perf_counter()
                    bounds = RowGroupBounds(reader._file)
                    elapsed = perf_counter() - start
                    entry["preparation"].append(
                        {
                            "domain": domain,
                            "seconds": elapsed,
                            "bytes": bounds.buffer_bytes,
                            "groups": reader._file.num_row_groups,
                        }
                    )
                import cProfile

                profile = cProfile.Profile()
                profile.enable()
                table = fabric.candidates("rivers", search_bounds(point.x, point.y), levels=[1])
                delineate(
                    fabric=fabric,
                    target=point,
                    level=2,
                    snap_onto=[River(table, row) for row in range(table.num_rows)],
                    radius=1000,
                    refine=False,
                    merge=True,
                )
                profile.disable()
                import pstats

                stats = pstats.Stats(profile)
                entry["profile"] = [
                    {"function": str(key), "calls": val[1], "self_seconds": val[2], "cumulative_seconds": val[3]}
                    for key, val in stats.stats.items()
                    if key[2] in ("candidate_groups", "snap", "_select", "_records", "merge", "candidates", "traverse")
                ]
                geometries = [u.geometry for u in result.subbasins]
                entry["union"] = []
                for _ in range(5):
                    start = perf_counter()
                    valid = bool(shapely.coverage_is_valid(geometries))
                    validation = perf_counter() - start
                    row = {"coverage_valid": valid, "validation_seconds": validation}
                    for algorithm in ("union_all", "disjoint_subset_union_all", "coverage_union_all"):
                        if algorithm == "coverage_union_all" and not valid:
                            continue
                        start = perf_counter()
                        merged = getattr(shapely, algorithm)(geometries)
                        duration = perf_counter() - start
                        row[algorithm] = {
                            "seconds": duration,
                            "valid": bool(merged.is_valid),
                            "equals": bool(merged.equals(result.geometry)),
                        }
                    entry["union"].append(row)
            report["targets"].append(entry)
    args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
