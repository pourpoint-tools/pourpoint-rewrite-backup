"""One fresh-process polygon query, or untimed exact witness comparison."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import pyarrow as pa
import shapely
from pyarrow import ipc
from shapely.geometry.base import BaseGeometry

from measure import process_memory
from selective import Locator, polygons


def compare(paths: list[Path]) -> dict[str, object]:
    """Compare framed Arrow witnesses exactly, including decoded-output WKB."""
    tables = []
    for path in paths:
        with ipc.open_file(path) as reader:
            tables.append(reader.read_all().sort_by("id"))
    if len(tables) < 2:
        raise ValueError("at least two witnesses required")
    baseline = tables[0]
    for table in tables[1:]:
        if not baseline.equals(table):
            raise AssertionError("IDs, source WKB, or decoded output WKB differ")
    for raw, decoded in zip(baseline["geometry"], baseline["decoded_wkb"], strict=True):
        if not shapely.equals_exact(
            shapely.from_wkb(raw.as_py()), shapely.from_wkb(decoded.as_py()), 0
        ):
            raise AssertionError("decoded output differs from source geometry")
    return {
        "status": "passed",
        "comparison": "exact Arrow table equality after ID sort: id, geometry, decoded_wkb; source/decoded equals_exact tolerance=0",
        "selected_rows": baseline.num_rows,
        "witnesses": [
            {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in paths
        ],
        "digest_framing": "SHA256 of complete Arrow IPC file, with schema and length-framed columns; equality is direct, not hash-only",
    }


def run(
    data: Path, receipt: Path, selection: str, method: str, witness: Path, activity: str
) -> dict[str, object]:
    start = perf_counter()
    prior = json.loads(
        (Path(__file__).parent / "results/hydrobasins-isolated.json").read_text()
    )
    query = next(
        q
        for q in prior["queries"]
        if q["kind"] == "polygon" and q["label"] == selection + "_geometry_group"
    )
    ids: list[int] = query["query_ids"]
    manifest = json.loads((data / "manifest.json").read_text())
    if manifest["format_version"] != "0.3.0":
        raise ValueError("requires unchanged HFX 0.3.0")
    result: dict[str, object] = {
        "status": "passed",
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "utc": datetime.now(UTC).isoformat(),
        "dataset_receipt": json.loads(receipt.read_text()),
        "versions": {
            p: importlib.metadata.version(p)
            for p in ["numpy", "pyarrow", "shapely", "psutil"]
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "selection": selection,
        "query_ids": ids,
        "source_group": query["source_group"],
        "source_group_geometry_compressed_bytes": query[
            "source_group_geometry_compressed_bytes"
        ],
        "method": method,
        "state": "fresh process; immutable existing local bundle; OS cache uncontrolled; no prior geometry read in process",
        "concurrent_activity": activity,
        "settings": {
            "pre_buffer": True,
            "use_threads": False,
            "batch_readahead": 1,
            "fragment_readahead": 1,
            "projection": ["id", "geometry"],
        },
        "initial_memory": process_memory(),
        "startup_seconds": perf_counter() - start,
    }
    preparation_start = perf_counter()
    locator = Locator.load(data / "catchments.parquet")
    if locator.attributes.num_rows != manifest["unit_count"]:
        raise AssertionError("manifest count mismatch")
    result["preparation_seconds"] = perf_counter() - preparation_start
    result["attribute_load_seconds"] = locator.attribute_load_seconds
    result["locator_build_seconds"] = locator.locator_build_seconds
    result["locator_buffers"] = locator.memory()
    result["preparation_memory"] = process_memory()
    retained: list[BaseGeometry] = []
    query_start = perf_counter()
    table, metrics = polygons(
        data / "catchments.parquet",
        locator,
        ids,
        method == "explicit_groups",
        retain_decoded=retained,
    )
    result["query_seconds"] = perf_counter() - query_start
    result["ready_memory"] = process_memory()
    result["total_to_ready_seconds"] = perf_counter() - start
    result["metrics"] = metrics
    result["retained_decoded_geometry_count"] = len(retained)
    result["decoded_native_buffer_bytes"] = "not measured"
    result["retention_scope"] = (
        "locator attributes and arrays, selected Arrow table, and all selected decoded Shapely geometries remain live at READY; candidate table no longer live"
    )
    # All witness work is after the READY memory snapshot and outside query timing.
    witness_table = table.append_column(
        "decoded_wkb", pa.array([g.wkb for g in retained])
    )
    witness.parent.mkdir(parents=True, exist_ok=True)
    with ipc.new_file(witness, witness_table.schema) as writer:
        writer.write_table(witness_table)
    result["witness_path"] = str(witness)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("run")
    worker.add_argument("--data", type=Path, required=True)
    worker.add_argument("--receipt", type=Path, required=True)
    worker.add_argument("--selection", choices=["ordinary", "largest"], required=True)
    worker.add_argument(
        "--method", choices=["filtered", "explicit_groups"], required=True
    )
    worker.add_argument("--witness", type=Path, required=True)
    worker.add_argument("--activity", required=True)
    worker.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("compare")
    verify.add_argument("witnesses", type=Path, nargs="+")
    verify.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "compare":
        result = compare(args.witnesses)
    else:
        result = run(
            args.data,
            args.receipt,
            args.selection,
            args.method,
            args.witness,
            args.activity,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
