"""Sequential public-API delineation timings; run once per stack and target.

Imports and validation are outside timings. Use a new cache and process each run.
Geometry witnesses stay beside the JSON receipt, not in the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
from collections.abc import Callable
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any


def measure[T, R](
    opening: Callable[[], T],
    query: Callable[[T], R],
    validate: Callable[[R], None],
    *,
    repeats: int,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    start = clock()
    engine = opening()
    opened = clock()
    result = query(engine)
    finished = clock()
    validate(result)
    repeated = []
    for _ in range(repeats):
        start_repeat = clock()
        result = query(engine)
        repeated.append(clock() - start_repeat)
        validate(result)
    return {"opening_seconds": opened - start, "cold_total_seconds": finished - start, "repeated_seconds": repeated}


def search_bounds(lon: float, lat: float) -> tuple[float, float, float, float]:
    # Conservative 2 km box for the fixed nonpolar study targets; snap still filters at 1 km.
    dy = 2000 / 110000
    dx = 2000 / (110000 * math.cos(math.radians(abs(lat) + dy)))
    return lon - dx, lat - dy, lon + dx, lat + dy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", choices=["python", "rust"], required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.cache.exists() and any(args.cache.iterdir()):
        parser.error("cache must be empty; choose a fresh directory")
    if args.report.exists():
        parser.error("report already exists")
    args.cache.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    target = next(t for t in json.loads(args.target.read_text()) if t["name"] == args.name)
    lon, lat = target["lon"], target["lat"]
    source = (
        "https://basin-delineations-public.upstream.tech/grit/hfx-v0."
        + ("4" if args.stack == "python" else "3")
        + ".0/"
    )
    os.environ["HFX_CACHE_DIR"] = str(args.cache.resolve())
    if "POURPOINT_BENCH_TRACE" in os.environ:
        parser.error("unset POURPOINT_BENCH_TRACE for timings")
    import shapely
    from shapely.geometry import Point

    import pourpoint

    report: dict[str, Any] = {
        "stack": args.stack,
        "source": source,
        "target": target,
        "package": version("pourpoint"),
        "snap_radius_m": 1000,
        "python_candidate_bbox": search_bounds(lon, lat),
        "python": sys.version,
        "machine": platform.platform(),
        "processor": platform.processor(),
        "command": sys.argv,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "validation": [],
        "status": "running",
    }
    engine: Any = None
    point = Point(lon, lat)

    def opening() -> Any:
        nonlocal engine
        if args.stack == "python":
            engine = pourpoint.HydroFabric(source, cache_dir=args.cache)
        else:
            engine = pourpoint.Engine(source, refine=False, snap_radius=1000)
        return engine

    def query(reader: Any) -> Any:
        if args.stack == "python":
            candidates = reader.candidates("rivers", search_bounds(lon, lat), levels=[1])
            rivers = [pourpoint.River(candidates, row) for row in range(candidates.num_rows)]
            return pourpoint.delineate(
                fabric=reader, target=point, level=2, snap_onto=rivers, radius=1000, refine=False, merge=True
            )
        return reader.delineate(lon=lon, lat=lat, geometry=True)

    def validate(result: Any) -> None:
        if args.stack == "python":
            geometry = result.geometry
            units = result.subbasins
            ids = sorted(unit.id for unit in units)
            outlets = [unit.id for unit in units if unit.geometry.contains(point)]
            receipt = {
                "ids": ids,
                "interior_outlets": outlets,
                "snapped_coordinate": [result.target.x, result.target.y],
                "selected_source_keys": sorted(
                    (u.attributes["source_namespace"], u.attributes["source_id"])
                    for u in units
                    if u.geometry.intersects(result.target)
                ),
                "source_keys": sorted((u.attributes["source_namespace"], u.attributes["source_id"]) for u in units),
                "levels": sorted({u.attributes["level"] for u in units}),
                "transfer_cumulative": asdict(engine.stats),
            }
        else:
            geometry = shapely.from_wkb(bytes(result.geometry_wkb))
            ids = sorted(result.upstream_unit_ids)
            receipt = {
                "ids": ids,
                "terminal_unit_id": result.terminal_unit_id,
                "snapped_coordinate": list(result.resolved_outlet),
                "resolution_method": result.resolution_method,
                "refinement_seed_kind": result.refinement_seed_kind,
                "levels": sorted({u.level for u in result.upstream_units}),
            }
        assert geometry is not None and not geometry.is_empty and geometry.is_valid
        receipt.update(count=len(ids), geometry_sha256=hashlib.sha256(geometry.wkb).hexdigest(), bounds=geometry.bounds)
        if report["validation"]:
            assert receipt["ids"] == report["validation"][0]["ids"]
            assert receipt["geometry_sha256"] == report["validation"][0]["geometry_sha256"]
        else:
            args.report.with_suffix(".wkb").write_bytes(geometry.wkb)
        report["validation"].append(receipt)

    try:
        report.update(measure(opening, query, validate, repeats=args.repeats))
        report["status"] = "passed"
    except BaseException as exc:
        report.update(status="failed", error=repr(exc))
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        if args.stack == "python" and engine is not None:
            engine.close()


if __name__ == "__main__":
    main()
