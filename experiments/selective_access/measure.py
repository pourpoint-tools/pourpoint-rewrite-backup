"""Run against a complete, separately acquired HFX dataset, never modify it."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import resource
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import numpy as np
import psutil
import pyarrow as pa
import pyarrow.parquet as pq

from selective import Locator, polygons, storage_bytes
from snap_access import snap_candidates


def process_memory() -> dict[str, int | str]:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "rss_bytes": psutil.Process().memory_info().rss,
        "process_reported_peak_rss_bytes": int(
            peak if platform.system() == "Darwin" else peak * 1024
        ),
        "arrow_current_allocated_bytes": pa.total_allocated_bytes(),
        "arrow_pool_peak_bytes": pa.default_memory_pool().max_memory(),
        "method": "psutil endpoint RSS; getrusage lifetime high-water RSS, not sampled; Arrow pool separate",
    }


def geometry_groups(file: pq.ParquetFile) -> tuple[int, int]:
    sizes = [storage_bytes(file, [g], ["geometry"]) for g in range(file.num_row_groups)]
    order = np.argsort(sizes)
    return int(order[len(order) // 2]), int(order[-1])


def run(data: Path, receipt: Path, activity: str) -> dict[str, object]:
    start = perf_counter()
    manifest = json.loads((data / "manifest.json").read_text())
    if manifest["format_version"] != "0.3.0":
        raise ValueError("requires unchanged HFX 0.3.0")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result: dict[str, object] = {
        "status": "passed",
        "code_commit": commit,
        "utc": datetime.now(UTC).isoformat(),
        "dataset_receipt": json.loads(receipt.read_text()),
        "versions": {
            p: importlib.metadata.version(p)
            for p in ["numpy", "pyarrow", "shapely", "psutil"]
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "state": "local OS cache uncontrolled; first and repeated queries reuse locator; no application geometry cache",
        "concurrent_activity": activity,
        "settings": {
            "pre_buffer": True,
            "use_threads": False,
            "batch_readahead": 1,
            "fragment_readahead": 1,
        },
        "remote_requests": "not measured",
        "remote_response_bytes": "not measured",
        "excluded": [
            "initial download (receipt separate)",
            "graph traversal",
            "snap ranking/distance",
            "geometry repair",
            "union",
            "area computation",
            "complete conformance validation",
        ],
        "initial_memory": process_memory(),
        "startup_seconds": perf_counter() - start,
    }
    catchments = data / "catchments.parquet"
    locator = Locator.load(catchments)
    if locator.attributes.num_rows != manifest["unit_count"]:
        raise AssertionError("manifest count mismatch")
    result["attribute_only"] = {
        "rows": locator.attributes.num_rows,
        "columns": locator.attributes.column_names,
        "geometry_wkb_bytes": 0,
        "decoded_geometry_count": 0,
        "attribute_load_seconds": locator.attribute_load_seconds,
        "locator_build_seconds": locator.locator_build_seconds,
        "buffers": locator.memory(),
        "memory_after": process_memory(),
        "parent_null_rows": locator.attributes["parent_id"].null_count,
        "up_area_null_rows": locator.attributes["up_area_km2"].null_count,
    }
    file = pq.ParquetFile(catchments)
    groups = geometry_groups(file)
    queries: list[dict[str, object]] = []
    for label, group in zip(
        ["ordinary_geometry_group", "largest_geometry_group"], groups
    ):
        begin = 0 if group == 0 else int(locator.group_ends[group - 1])
        end = int(locator.group_ends[group])
        ids = [
            int(locator.attributes["id"][i].as_py())
            for i in sorted({begin, (begin + end) // 2, end - 1})
        ]
        selected_identity: bytes | None = None
        for repeat in range(2):
            for explicit in [False, True] if repeat == 0 else [True, False]:
                table, metrics = polygons(catchments, locator, ids, explicit)
                # Exact IDs and byte-for-byte WKB identity, independent of reader row order.
                identity = b"".join(table.sort_by("id")["geometry"].to_pylist())
                if selected_identity is not None and identity != selected_identity:
                    raise AssertionError("polygon WKB mismatch between readers/repeats")
                selected_identity = identity
                queries.append(
                    {
                        "kind": "polygon",
                        "label": label,
                        "source_group": group,
                        "source_group_geometry_compressed_bytes": storage_bytes(
                            file, [group], ["geometry"]
                        ),
                        "query_ids": ids,
                        "method": "explicit_groups" if explicit else "filtered",
                        "repeat": repeat,
                        "metrics": metrics,
                        "memory_after": process_memory(),
                        "geometry_identity_check": "passed",
                    }
                )
                del table
    for declaration in manifest.get("auxiliary", []):
        if declaration["schema"] != "hfx.aux.snap.v2":
            continue
        snap_path = data / declaration["artifacts"]["snap"]
        snap_file = pq.ParquetFile(snap_path)
        for label, group in zip(
            ["ordinary_geometry_group", "largest_geometry_group"],
            geometry_groups(snap_file),
        ):
            # Only scalar bbox input planning; geometry never loaded here.
            bbox = None
            if "bbox" in snap_file.schema_arrow.names:
                scalar = snap_file.read_row_group(
                    group, columns=["bbox"], use_threads=False
                )
                bbox = scalar["bbox"][0].as_py()
            if bbox is None:
                bounds = (-0.1, -0.1, 0.1, 0.1)
            else:
                x = (bbox["xmin"] + bbox["xmax"]) / 2
                y = (bbox["ymin"] + bbox["ymax"]) / 2
                bounds = (x - 0.05, y - 0.05, x + 0.05, y + 0.05)
            baseline: pa.Table | None = None
            for repeat in range(2):
                for explicit in [False, True] if repeat == 0 else [True, False]:
                    table, metrics = snap_candidates(snap_path, bounds, explicit)
                    identity = table.select(["id", "unit_id", "geometry"]).sort_by("id")
                    if baseline is not None and not baseline.equals(identity):
                        raise AssertionError("snap IDs/unit references/WKB mismatch")
                    baseline = identity
                    # Lookup unit references via catchment locator, never treat snap id as unit id.
                    unit_ids = table["unit_id"].combine_chunks().to_numpy()
                    positions = np.searchsorted(locator.sorted_ids, unit_ids)
                    if np.any(
                        positions == len(locator.sorted_ids)
                    ) or not np.array_equal(locator.sorted_ids[positions], unit_ids):
                        raise AssertionError("unknown snap unit reference")
                    levels = (
                        locator.attributes["level"]
                        .take(pa.array(locator.row_positions[positions]))
                        .to_numpy()
                    )
                    if not np.all(
                        np.isin(levels, declaration["metadata"]["references_levels"])
                    ):
                        raise AssertionError("snap reference level mismatch")
                    queries.append(
                        {
                            "kind": "snap",
                            "label": label,
                            "source_group": group,
                            "artifact": declaration["artifacts"]["snap"],
                            "bounds": bounds,
                            "source_group_geometry_compressed_bytes": storage_bytes(
                                snap_file, [group], ["geometry"]
                            ),
                            "method": "explicit_groups" if explicit else "filtered",
                            "repeat": repeat,
                            "metrics": metrics,
                            "memory_after": process_memory(),
                            "exact_ids_units_wkb_check": "passed",
                            "unit_level_check": "passed",
                        }
                    )
                    del table
    result["queries"] = queries
    result["final_memory"] = process_memory()
    result["total_seconds"] = perf_counter() - start
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--activity", required=True)
    args = parser.parse_args()
    try:
        result = run(args.data, args.receipt, args.activity)
    except Exception as error:
        result = {
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
            "code_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "data": str(args.data),
            "receipt": str(args.receipt),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        raise
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
