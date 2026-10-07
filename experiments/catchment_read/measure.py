"""Fresh process worker. Reference and witness serialization are outside its boundary."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import resource
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TypedDict

import psutil
import pyarrow as pa
import shapely
from pyarrow import ipc
from shapely.geometry.base import BaseGeometry

from access import CatchmentReader


class Phase(TypedDict):
    seconds: float
    io: dict[str, dict[str, int]]
    memory: dict[str, int]


class Call(Phase):
    stats: dict[str, object]
    witnesses: list[str]


class Measurement(TypedDict):
    open: Phase
    prepare: Phase
    initial_memory: dict[str, int]
    through_first_retrieval_seconds: float
    calls: list[Call]
    decode: dict[str, float | int]
    ready_memory: dict[str, int]
    retained_locator_numpy_bytes: int
    retained_output_backing_bytes: int


def memory() -> dict[str, int]:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "rss_bytes": psutil.Process().memory_info().rss,
        "process_lifetime_peak_rss_bytes": int(
            peak * (1 if platform.system() == "Darwin" else 1024)
        ),
        "arrow_current_bytes": pa.total_allocated_bytes(),
        "arrow_pool_peak_bytes": pa.default_memory_pool().max_memory(),
    }


def io_delta(
    after: dict[str, dict[str, int]], before: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    return {
        name: {
            key: value - before.get(name, {}).get(key, 0)
            for key, value in counts.items()
        }
        for name, counts in after.items()
    }


def measure(
    root: Path, requests: list[list[int]], *, experimental: bool
) -> tuple[Measurement, list[list[pa.Table]]]:
    initial = memory()
    start = perf_counter()
    reader = CatchmentReader(root, experimental=experimental)
    opened = perf_counter() - start
    with reader:
        previous = reader.io_counts()
        opening: Phase = {"seconds": opened, "io": previous, "memory": memory()}
        start = perf_counter()
        reader.prepare()
        elapsed = perf_counter() - start
        current = reader.io_counts()
        preparation: Phase = {
            "seconds": elapsed,
            "io": io_delta(current, previous),
            "memory": memory(),
        }
        previous = current
        calls: list[Call] = []
        retained: list[list[pa.Table]] = []
        for _ in range(2):
            start = perf_counter()
            tables, stats = reader.retrieve(requests)
            elapsed = perf_counter() - start
            retained.append(tables)
            current = reader.io_counts()
            calls.append(
                {
                    "seconds": elapsed,
                    "io": io_delta(current, previous),
                    "memory": memory(),
                    "stats": stats,
                    "witnesses": [],
                }
            )
            previous = current
        # Decode only the second call. Both Arrow outputs remain live, explicitly.
        start = perf_counter()
        decoded: list[BaseGeometry] = [
            shapely.from_wkb(value.as_py())
            for table in retained[-1]
            for value in table["geometry"]
        ]
        decode_seconds = perf_counter() - start
        result: Measurement = {
            "open": opening,
            "prepare": preparation,
            "initial_memory": initial,
            "through_first_retrieval_seconds": opened
            + preparation["seconds"]
            + calls[0]["seconds"],
            "calls": calls,
            "decode": {"seconds": decode_seconds, "geometries": len(decoded)},
            "ready_memory": memory(),
            "retained_locator_numpy_bytes": reader.retained_locator_bytes,
            "retained_output_backing_bytes": sum(
                t.get_total_buffer_size() for tables in retained for t in tables
            ),
        }
    return result, retained


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--selections", type=Path, required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--witness-dir", type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--experimental", action="store_true")
    args = parser.parse_args()
    requests = json.loads(args.selections.read_text())["workloads"][args.workload]
    result, tables = measure(args.data, requests, experimental=args.experimental)
    # After every timing and memory snapshot. No reference data enters this process.
    args.witness_dir.mkdir(parents=True, exist_ok=False)
    for index, outputs in enumerate(tables):
        for request, table in enumerate(outputs):
            path = args.witness_dir / f"call-{index}-request-{request}.arrow"
            with ipc.new_file(path, table.schema) as writer:
                writer.write_table(table)
            result["calls"][index]["witnesses"].append(str(path))
    document = dict(
        result,
        workload=args.workload,
        data=str(args.data),
        code_commit=args.code_commit,
        utc=datetime.now(UTC).isoformat(),
        versions={
            p: importlib.metadata.version(p)
            for p in ["pyarrow", "numpy", "psutil", "shapely"]
        },
        python=platform.python_version(),
        platform=platform.platform(),
        settings={
            "pre_buffer": False,
            "use_threads": False,
            "memory_map": False,
            "projection": "all source columns",
            "cache": "metadata/locator only; no lookup/data batch cache between calls",
            "calls": 2,
        },
        scope={
            "time": "imports/selection JSON excluded; open + preparation + retrieval measured; total is phase sum, excludes counter/snapshot bookkeeping",
            "memory": "fresh-process lifetime peaks include imports and selection IDs; no graph/reference/witness work; first output remains live during repeat; both outputs plus second-call decoded geometries live at READY",
            "unmeasured": "physical disk I/O, transport/wire bytes, native decoded allocation size, Python/reader metadata retained sizes, phase-only peak RSS",
            "io": "unbuffered local FileIO read/readinto calls and bytes returned, including metadata and bounded fingerprints; not syscall/disk/network counts",
            "cache_state": "uncontrolled filesystem cache, not cold disk",
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n")


if __name__ == "__main__":
    main()
