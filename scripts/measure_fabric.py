"""Verify and measure selected public GRIT workloads against retained source witnesses.

Run from the repository with ``uv run python -m scripts.measure_fabric --help``.
The cache must start empty. Reports are checkpointed after every operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import struct
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from pourpoint.fabric import HydroFabric


def ids_digest(ids: Sequence[int]) -> str:
    return hashlib.sha256(b"".join(struct.pack("<q", i) for i in sorted(ids))).hexdigest()


def source_grass(values: npt.NDArray[np.int8], valid: npt.NDArray[np.bool_]) -> npt.NDArray[np.uint8]:
    """Invert the public normalization for this unsigned GRASS witness only."""
    return np.where(valid, np.where(values == 0, 0, 9 - values).astype(np.uint8), np.uint8(255))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def table_receipt(table: pa.Table) -> dict[str, Any]:
    return {
        "rows": table.num_rows,
        "logical_bytes": table.nbytes,
        "schema": str(table.schema),
        "ids_sha256": ids_digest(table["id"].to_pylist()),
    }


@contextmanager
def disabled_transport() -> Iterator[None]:
    # All native obstore entry points used by Source are blocked, not just Python sockets.
    with ExitStack() as stack:
        for name in ("get", "get_range", "get_ranges", "head"):
            stack.enter_context(
                patch("pourpoint.storage.obstore." + name, side_effect=AssertionError("network transport disabled"))
            )
        yield


def run(args: argparse.Namespace) -> None:
    expected = json.loads(args.expected.read_text())
    report: dict[str, Any] = {
        "source": args.source,
        "code_sha256": {
            str(p): digest(p.read_bytes()) for p in [Path(__file__), *sorted(Path("src/pourpoint").glob("*.py"))]
        },
        "range_workers": args.range_workers,
        "profile": args.profile,
        "resumed_cache": args.resume,
        "expected_path": str(args.expected.resolve()),
        "expected_sha256": digest(args.expected.read_bytes()),
        "measurements": [],
        "scope": "Public API eager acquisition, decoding, assembly, traversal; no delineation algorithms",
        "limits": [
            "Selected source witnesses, not global producer verification",
            "RSS is process-lifetime peak",
            "Body bytes exclude protocol overhead; first use is not cold CDN",
            "Logical buffers are not RSS",
            "Direction source bytes reconstructed from lossless normalization and validity mask",
        ],
        "rss_native_unit": "bytes" if sys.platform == "darwin" else "KiB",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        args.report.write_text(json.dumps(report, indent=2) + "\n")

    def disk_bytes() -> int:
        return sum(p.stat().st_size for p in args.cache.rglob("*") if p.is_file())

    def measure(label: str, fabric: HydroFabric, operation: Callable[[], dict[str, Any]]) -> None:
        before = asdict(fabric.stats)
        start = perf_counter()
        result = operation()
        if "schema" in result:
            schema = result.pop("schema")
            schema_hash = digest(schema.encode())
            report.setdefault("schemas", {})[schema_hash] = schema
            result["schema_sha256"] = schema_hash
        report["measurements"].append(
            {
                "label": label,
                "elapsed_seconds": perf_counter() - start,
                "transfer": {k: v - before[k] for k, v in asdict(fabric.stats).items()},
                "cache_disk_bytes": disk_bytes(),
                "logical_buffers": fabric.buffer_bytes,
                "process_peak_rss_native": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "result": result,
            }
        )
        save()
        print(label, report["measurements"][-1]["elapsed_seconds"], flush=True)

    def candidates(fabric: HydroFabric, domain: str) -> dict[str, Any]:
        key = "nodes" if domain == "river_nodes" else domain
        witness = expected[key + "_spatial_small"]
        bounds = witness["window"]["bbox"] if key == "nodes" else witness["bounds_epsg4326"]
        oracle = witness if key == "nodes" else witness["all_tiers"]
        table = fabric.candidates(domain, tuple(bounds), columns=["id", "level", "bbox"])
        assert ids_digest(table["id"].to_pylist()) == oracle["sorted_ids_sha256_le_i64"]
        assert table.num_rows == oracle["count"]
        return table_receipt(table)

    def records(fabric: HydroFabric, domain: str) -> dict[str, Any]:
        key = "nodes" if domain == "river_nodes" else domain
        witness = expected.get(key + "_scattered")
        ids = (
            witness["ids"]
            if witness
            else [1284709, 1284710, 1285133, expected["supplemental"]["alternative_small_catchment"]["id"]]
        )
        table = fabric.records(domain, ids)
        assert table["id"].to_pylist() == ids
        hashes = [digest(value) for value in table["geometry"].to_pylist()]
        if witness:
            assert hashes == witness["wkb_sha256_each"]
            assert table["level"].to_pylist() == witness["levels"]
        if domain == "catchments":
            oracle = expected["supplemental"]["alternative_small_catchment"]
            assert hashes[-1] == oracle["wkb_sha256"]
            for field in ("level", "bbox", "outlet_lon", "outlet_lat", "source_namespace", "source_id", "parent_id"):
                assert table[field][-1].as_py() == oracle[field]
        relationships = fabric.relationships(domain, ids)
        if domain == "rivers":
            for oracle in expected["supplemental"]["river_1_direct_flow_receipts"]:
                neighbors = relationships[oracle["direction"] + "_ids"][0].as_py()
                assert len(neighbors) == oracle["count"]
                assert ids_digest(neighbors) == oracle["sorted_ids_sha256"]
            for column, neighbors in expected["supplemental"]["river_1_other_immediate"].items():
                if column.endswith("_ids"):
                    assert relationships[column][0].as_py() == neighbors
        for name in relationships.column_names:
            if name == "id":
                continue
            for row, neighbors in enumerate(relationships[name].to_pylist()):
                assert neighbors == sorted(set(neighbors))
                scalar = name[:-1] if name.endswith("_ids") else ""
                if scalar in table.column_names:
                    value = table[scalar][row].as_py()
                    assert neighbors == ([] if value is None else [value])
        result = table_receipt(table)
        result.update(
            wkb_sha256=hashes,
            relationships=relationships.to_pylist(),
            relationship_logical_bytes=relationships.nbytes,
            independent_wkb_witness_ids=ids
            if witness
            else [expected["supplemental"]["alternative_small_catchment"]["id"]],
        )
        return result

    def closure(fabric: HydroFabric, bulk: bool) -> dict[str, Any]:
        witness = expected["catchment_upstream_small"]
        seeds = witness["seeds"]["explicit_ids"] if bulk else [1284709]
        oracle = witness["closure"] if bulk else witness["per_seed"]["1284709"]
        ids = fabric.traverse("catchments", seeds)
        assert ids_digest(ids) == oracle["sorted_ids_sha256_le_i64"]
        table = fabric.records("catchments", ids)
        return table_receipt(table)

    def raster(fabric: HydroFabric, name: str) -> dict[str, Any]:
        witnesses = [v for v in expected["raster_windows"] if v["name"] == name]
        col, row, width, height = witnesses[0]["window"]
        bounds = (
            -15000000 + 30 * col,
            8400000 - 30 * (row + height),
            -15000000 + 30 * (col + width),
            8400000 - 30 * row,
        )
        request = fabric.rasters.prepare(bounds, crs="EPSG:8857")
        assert (request.col, request.row, request.width, request.height) == (col, row, width, height)
        result = fabric.rasters.read(request)
        hashes: dict[str, str] = {}
        for witness in witnesses:
            direction = witness["kind"] == "dir"
            values = source_grass(result.direction, result.direction_valid) if direction else result.accumulation
            valid = result.direction_valid if direction else result.accumulation_valid
            cells = digest(values.tobytes())
            mask = digest(valid.tobytes())
            assert cells == witness["expected_cells_sha256"], (name, witness["kind"], cells)
            assert mask == witness["expected_mask_sha256"], (name, witness["kind"], mask)
            hashes[witness["kind"] + "_cells"] = cells
            hashes[witness["kind"] + "_mask"] = mask
        return {
            "hashes": hashes,
            "logical_bytes": sum(
                a.nbytes
                for a in (result.direction, result.accumulation, result.direction_valid, result.accumulation_valid)
            ),
        }

    def workload(fabric: HydroFabric, phase: str, *, bulk: bool) -> None:
        for domain in ("catchments", "rivers", "river_nodes"):
            measure(phase + "/candidates/" + domain, fabric, lambda d=domain: candidates(fabric, d))
        measure(phase + "/single-closure", fabric, lambda: closure(fabric, False))
        if bulk:
            measure(phase + "/overlapping-bulk-closure", fabric, lambda: closure(fabric, True))
            for domain in ("catchments", "rivers", "river_nodes"):
                measure(phase + "/records-relationships/" + domain, fabric, lambda d=domain: records(fabric, d))
            for name in ("greenland", "madagascar"):
                measure(phase + "/rasters/" + name, fabric, lambda n=name: raster(fabric, n))

    def opened(phase: str, offline: bool = False) -> HydroFabric:
        start = perf_counter()
        fabric = HydroFabric(args.source, cache_dir=args.cache, offline=offline)
        # Measurement-only tuning hook, not a HydroFabric public API option.
        fabric._source.range_workers = args.range_workers
        assert fabric.manifest.declarations["input_manifest_sha256"] == expected["source_manifest_sha256"]
        # Inspect the exact acquired snapshot, never a reserialized JSON digest.
        cache = fabric._source._cache
        assert cache is not None
        snapshot = cache.metadata("json:manifest.json")
        assert snapshot is not None
        report["manifest_sha256"] = digest(snapshot)
        assert report["manifest_sha256"] == expected["final_manifest_sha256"]
        report["measurements"].append(
            {
                "label": phase + "/open",
                "elapsed_seconds": perf_counter() - start,
                "transfer": asdict(fabric.stats),
                "cache_disk_bytes": disk_bytes(),
            }
        )
        save()
        return fabric

    try:
        with opened("resume" if args.resume else "first") as fabric:
            workload(fabric, "resume-single" if args.resume else "first-single", bulk=False)
            if args.profile == "single":
                report["status"] = "passed"
                return
            workload(fabric, "incremental-bulk", bulk=True)
            workload(fabric, "same-context", bulk=True)
        with opened("reopen") as fabric:
            workload(fabric, "reopen", bulk=True)
        with disabled_transport(), opened("offline", True) as fabric:
            workload(fabric, "offline", bulk=True)
            assert fabric.stats.requests == 0
        report["status"] = "passed"
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = repr(exc)
        raise
    finally:
        save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0")
    parser.add_argument("--resume", action="store_true", help="Retain failed-run cache; not first-use measurements")
    parser.add_argument("--range-workers", type=int, choices=(1, 4), default=4)
    parser.add_argument("--profile", choices=("full", "single"), default="full")
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not args.resume and args.cache.exists() and any(args.cache.iterdir()):
        parser.error("first-use cache must be empty; retain previous cache and choose another directory")
    run(args)


if __name__ == "__main__":
    main()
