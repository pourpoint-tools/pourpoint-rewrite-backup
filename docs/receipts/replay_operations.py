"""Archived validation recipe, not a public API. Requires the populated measurement cache."""

import argparse
import hashlib
import json
import resource
import sys
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

from shapely.geometry import Point

from pourpoint import (
    BatchResult,
    DelineationResult,
    FootprintRefinement,
    GranularRefinement,
    HydroFabric,
    RiverNetwork,
    Subbasin,
    delineate,
    merge,
    refine,
    select_rivers,
    select_upstream_subbasins,
)
from pourpoint.qgis import QGISBundle

parser = argparse.ArgumentParser(description="Replay the recorded GRIT workload offline; run from the repository root.")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--cache", type=Path, required=True)
args = parser.parse_args()
OUT = args.output
OUT.mkdir(parents=True, exist_ok=True)
source = "https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/"
cache = args.cache
point = Point(5.108851240509936, 51.78809971840007)
targets = {"netherlands": point, "duplicate": point, "ocean": Point(-30, 0)}
report: dict[str, Any] = {
    "measurements": [],
    "source": source,
    "settings": {
        "point": list(point.coords)[0],
        "level": 1,
        "child_level": 2,
        "window_size": 3,
        "n_jobs": 2,
        "river_level": 1,
    },
    "code_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path("src/pourpoint").glob("*.py")},
}


def save() -> None:
    (OUT / "backend-stable-live.json").write_text(json.dumps(report, indent=2))


def measure[T](label: str, f: HydroFabric, fn: Callable[[], tuple[T, dict[str, Any]]]) -> T:
    before = asdict(f.stats)
    t = perf_counter()
    value, detail = fn()
    report["measurements"].append(
        {
            "label": label,
            "elapsed_seconds": perf_counter() - t,
            "transfer": {k: v - before[k] for k, v in asdict(f.stats).items()},
            "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            "result": detail,
        }
    )
    save()
    print(label, flush=True)
    return value


def select(f: HydroFabric) -> tuple[list[Subbasin], dict[str, Any]]:
    s = select_upstream_subbasins(fabric=f, target=point)
    assert [b.id for b in s] == [842003]
    return s, {"ids": [b.id for b in s]}


def refined(f: HydroFabric, s: list[Subbasin]) -> tuple[GranularRefinement, dict[str, Any]]:
    r = refine(fabric=f, subbasins=s, target=point)
    return r, {
        "ids": [b.id for b in r.subbasins],
        "cell": [r.outlet_cell.row, r.outlet_cell.col],
        "shape": list(r.rasters.direction.mask.shape),
        "mask_cells": int(r.rasters.direction.mask.sum()),
        "geometry_area_degrees2": merge(subbasins=r.subbasins).area,
    }


def equivalent(f: HydroFabric, s: list[Subbasin], r: GranularRefinement) -> tuple[FootprintRefinement, dict[str, Any]]:
    other = refine(fabric=f, footprint=merge(subbasins=s), target=point)
    delta = merge(subbasins=r.subbasins).symmetric_difference(other.footprint).area
    assert delta < 1e-18, delta
    assert other.outlet_cell == r.outlet_cell
    return other, {"symmetric_difference_area_degrees2": delta}


def level2(f: HydroFabric) -> tuple[GranularRefinement, dict[str, Any]]:
    s = select_upstream_subbasins(fabric=f, target=point, level=2)
    assert sorted(b.id for b in s) == list(range(11205495, 11205506))
    r = refine(fabric=f, subbasins=s, target=point)
    other = refine(fabric=f, footprint=merge(subbasins=s), target=point)
    delta = merge(subbasins=r.subbasins).symmetric_difference(other.footprint).area
    assert delta < 1e-18, delta
    return r, {
        "whole_ids": [b.id for b in s],
        "contributing_ids": [b.id for b in r.subbasins],
        "symmetric_difference_area_degrees2": delta,
    }


def rivers(f: HydroFabric, s: list[Subbasin], downstream: bool) -> tuple[RiverNetwork, dict[str, Any]]:
    r = select_rivers(fabric=f, target=s, level=1, include_downstream=downstream)
    assert len({b.id for b in r.rivers}) == len(r.rivers)
    return r, {
        "ids": [b.id for b in r.rivers],
        "nodes": len(r.nodes),
        "relationship_columns": r.relationships.column_names,
    }


def batch(f: HydroFabric) -> tuple[BatchResult[DelineationResult], dict[str, Any]]:
    b = delineate(fabric=f, target=targets, refine=True, merge=True, n_jobs=2)
    assert [t.name for t in b.targets] == list(targets)
    assert b.results[0] is not None and b.results[1] is not None and b.results[2] is None
    assert b.results[0].geometry.equals(b.results[1].geometry)
    assert len(b.issues) == 1 and b.issues[0].code == "selection.no_coverage"
    return b, {"names": [t.name for t in b.targets], "issues": [asdict(i) for i in b.issues]}


def export(b: BatchResult[DelineationResult], r: RiverNetwork, phase: str) -> tuple[Path, dict[str, Any]]:
    path = QGISBundle().add_result(b).add_vector("rivers", r).write(OUT / ("bundle-" + phase))
    return path, {"path": str(path), "files": {p.name: p.stat().st_size for p in path.iterdir()}}


def workload(f: HydroFabric, phase: str) -> None:
    s = measure(phase + "/selection", f, lambda: select(f))
    r = measure(phase + "/refinement", f, lambda: refined(f, s))
    measure(phase + "/footprint-equivalence", f, lambda: equivalent(f, s, r))
    measure(phase + "/level2-granular", f, lambda: level2(f))
    river = measure(phase + "/rivers", f, lambda: rivers(f, s, False))
    measure(phase + "/rivers-downstream", f, lambda: rivers(f, s, True))
    b = measure(phase + "/batch-refined", f, lambda: batch(f))
    measure(phase + "/export", f, lambda: export(b, river, phase))


def opened(phase: str, offline: bool = False) -> HydroFabric:
    t = perf_counter()
    f = HydroFabric(source, cache_dir=cache, offline=offline)
    report["measurements"].append(
        {
            "label": phase + "/open",
            "elapsed_seconds": perf_counter() - t,
            "transfer": asdict(f.stats),
            "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
        }
    )
    report["manifest_sha256"] = hashlib.sha256(f._source._cache.metadata("json:manifest.json")).hexdigest()
    save()
    return f


try:
    with ExitStack() as stack:
        for name in ["get", "get_range", "get_ranges", "head"]:
            stack.enter_context(patch("pourpoint.storage.obstore." + name, side_effect=AssertionError("disabled")))
        with opened("stable-recovery", True) as f:
            workload(f, "stable-recovery")
            assert f.stats.requests == 0
            import sqlite3
            import xml.etree.ElementTree as ET
            import zipfile

            import numpy as np
            import rasterio

            selected = select_upstream_subbasins(fabric=f, target=point)
            r = refine(fabric=f, subbasins=selected, target=point)
            subset = r.rasters.direction
            window = subset.window
            checked = []
            for i in range(4):
                direction = i % 2 == 0
                values = window.raw_direction if direction else window.accumulation
                valid = (window.direction_valid if direction else window.accumulation_valid) & subset.mask
                nodata = window.direction_nodata if direction else window.accumulation_nodata
                path = OUT / "bundle-stable-recovery" / ("raster_" + str(i) + ".tif")
                with rasterio.open(path) as raster:
                    assert raster.crs == window.crs and raster.transform == window.transform
                    np.testing.assert_array_equal(raster.read(1), np.where(valid, values, nodata).astype(values.dtype))
                    np.testing.assert_array_equal(raster.read_masks(1) > 0, valid)
                    assert raster.nodata == nodata or (np.isnan(raster.nodata) and np.isnan(nodata))
                checked.append(
                    {
                        "file": path.name,
                        "valid_cells": int(valid.sum()),
                        "outside_mask_cells": int((~subset.mask).sum()),
                    }
                )
            with sqlite3.connect(OUT / "bundle-stable-recovery" / "vectors.gpkg") as db:
                issues = db.execute("SELECT * FROM target_issues").fetchall()
                assert len(issues) == 1 and "selection.no_coverage" in issues[0]
                connections = db.execute("SELECT count(*) FROM river_connections").fetchone()[0]
                assert connections > 0
            with zipfile.ZipFile(OUT / "bundle-stable-recovery" / "watershed.qgz") as z:
                xml = ET.fromstring(z.read("watershed.qgs"))
                refs = [e.text for e in xml.findall(".//datasource")]
                assert refs and all(v.startswith("./") for v in refs)
            standalone = refine(fabric=f, footprint=merge(subbasins=selected), target=point)
            standalone_path = QGISBundle().add_result(standalone).write(OUT / "standalone-footprint")
            with sqlite3.connect(standalone_path / "vectors.gpkg") as db:
                metadata = json.loads(db.execute("SELECT metadata FROM result_metadata").fetchone()[0])
                assert metadata == dict(f.manifest.declarations)
            report["standalone_provenance"] = metadata
            report["export_checks"] = {
                "rasters": checked,
                "issues": issues,
                "river_connection_rows": connections,
                "relative_sources": refs,
            }
    report["status"] = "passed"
except BaseException as e:
    report.update(status="failed", error=repr(e))
    raise
finally:
    report["code_sha256_after"] = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path("src/pourpoint").glob("*.py")
    }
    save()
