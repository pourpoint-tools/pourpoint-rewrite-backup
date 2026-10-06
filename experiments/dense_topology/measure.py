"""One-process, one-layout local HydroBASINS measurement."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import numpy as np
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import breadth_first_order

from topology import Graph, Ints, Positions, convert, traverse

type JSON = None | bool | int | float | str | list[JSON] | dict[str, JSON]
PROCESS = psutil.Process()


def memory() -> dict[str, JSON]:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "rss_bytes": PROCESS.memory_info().rss,
        "process_peak_rss_bytes": int(
            peak if sys.platform == "darwin" else peak * 1024
        ),
        "arrow_live_allocated_bytes": pa.total_allocated_bytes(),
        "arrow_pool_peak_bytes": pa.default_memory_pool().max_memory(),
    }


def phase[T](action: Callable[[], T]) -> tuple[T, dict[str, JSON]]:
    before = memory()
    samples = [PROCESS.memory_info().rss]
    stop = threading.Event()

    def sample() -> None:
        while not stop.wait(0.005):
            samples.append(PROCESS.memory_info().rss)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    start = time.perf_counter()
    try:
        value = action()
        seconds = time.perf_counter() - start
    finally:
        stop.set()
        thread.join()
    after = memory()
    samples.append(PROCESS.memory_info().rss)
    return value, {
        "seconds": seconds,
        "before": before,
        "after": after,
        "sampled_peak_rss_bytes": max(samples),
        "sample_count": len(samples),
    }


def oracle(graph: Graph, identity: int) -> Ints:
    """Separate vectorized ID mapping plus SciPy BFS; outside measured queries."""
    rows = np.arange(graph.ids.size, dtype=np.int64)
    # Unique IDs were checked at index construction. Intersection independently
    # joins the complete sorted edge identity domain to original graph row IDs.
    unique, inverse = np.unique(graph.edges, return_inverse=True)
    common, source, target = np.intersect1d(
        unique, graph.ids, assume_unique=True, return_indices=True
    )
    if common.size != unique.size:
        raise KeyError("oracle missing upstream ID")
    mapping = np.empty(unique.size, dtype=np.int64)
    mapping[source] = rows[target]
    matrix = csr_matrix(
        (np.ones(graph.edges.size, dtype=np.int8), mapping[inverse], graph.offsets),
        shape=(graph.ids.size, graph.ids.size),
    )
    root = int(np.flatnonzero(graph.ids == identity)[0])
    reached = breadth_first_order(
        matrix, root, directed=True, return_predecessors=False
    )
    return np.sort(graph.ids[reached])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--mode", choices=["id", "dense"], required=True)
    parser.add_argument("--ids", type=int, nargs="+", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--concurrent-activity", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    receipt = json.loads(args.receipt.read_text())
    manifest = json.loads((args.data / "manifest.json").read_text())
    if manifest["format_version"] != "0.3.0":
        raise ValueError("requires HFX 0.3.0")
    objects = [
        item
        for item in receipt["objects"]
        if item["relative_path"] in ("graph.parquet", "manifest.json")
    ]
    if len(objects) != 2:
        raise ValueError("receipt lacks graph/manifest identities")
    record: dict[str, JSON] = {
        "schema": "dense-topology-measurement-v1",
        "status": "ok",
        "started_utc": datetime.now(UTC).isoformat(),
        "commit": args.commit,
        "command_arguments": [str(arg) for arg in sys.argv[1:]],
        "mode": args.mode,
        "state": "local warm/OS-cache-uncontrolled; fresh process/layout",
        "concurrent_activity": args.concurrent_activity,
        "objects": objects,
        "dataset": manifest,
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "pyarrow", "psutil", "scipy")
        },
        "python": sys.version,
        "platform": platform.platform(),
        "reader": {
            "columns": ["id", "level", "upstream_ids"],
            "pre_buffer": True,
            "use_threads": True,
            "memory_map": False,
        },
        "remote_requests": "not measured",
        "remote_response_bytes": "not measured",
        "parent_buffers_bytes": "not measured",
        "locator_buffers_bytes": "not measured",
        "excluded": "parents, other attributes, catchment locator, geometry, "
        "snap, union, area, full HFX conformance, download",
        "rss_method": "psutil RSS sampled every 0.005 seconds plus phase endpoints; "
        "ru_maxrss is process-lifetime peak, bytes on macOS/KiB elsewhere; "
        "Arrow pool counters are separate, not process memory",
        "startup_seconds": time.time() - PROCESS.create_time(),
        "startup_method": "wall clock before read minus process creation time; "
        "includes imports/argument parsing; excludes uv launcher",
        "initial_memory": memory(),
    }
    try:
        table, load = phase(
            lambda: pq.read_table(
                args.data / "graph.parquet",
                columns=["id", "level", "upstream_ids"],
                pre_buffer=True,
                use_threads=True,
                memory_map=False,
            )
        )
        record["load"] = load
        graph, index = phase(partial(Graph, table))
        del table
        record["compact_and_index"] = index
        record["after_release_read_table"] = memory()
        positions: Positions | None = None
        if args.mode == "dense":
            positions, conversion = phase(lambda: convert(graph))
            record["conversion"] = conversion
        else:
            record["conversion"] = "not applicable"
        record["rows"] = int(graph.ids.size)
        record["edges"] = int(graph.edges.size)
        if graph.ids.size != manifest["unit_count"]:
            raise ValueError("manifest row count differs")
        record["buffers"] = {
            "graph_arrow_logical_bytes": graph.table.select(
                ["id", "upstream_ids"]
            ).nbytes,
            "graph_arrow_retained_bytes": graph.table.select(
                ["id", "upstream_ids"]
            ).get_total_buffer_size(),
            "level_arrow_logical_bytes": graph.table.select(["level"]).nbytes,
            "level_arrow_retained_bytes": graph.table.select(
                ["level"]
            ).get_total_buffer_size(),
            "lookup_numpy_owned_bytes": graph.order.nbytes + graph.sorted_ids.nbytes,
            "dense_edges_numpy_owned_bytes": 0
            if positions is None
            else positions.nbytes,
            "query_seen_numpy_owned_bytes": int(graph.ids.size),
            "query_queue_numpy_owned_bytes": int(graph.ids.size * 4),
            "dtypes": "ID int64; lookup/edges/queue uint32; offsets int32; level int16",
            "graph_numpy_views": "zero-copy Arrow views; not additional allocations",
            "conversion_temporary_buffers": "not measured individually; see phase RSS",
        }
        record["ready_memory"] = memory()
        queries: list[JSON] = []
        expected_results: list[Ints] = []
        for identity in args.ids:
            timings: list[JSON] = []
            first: Ints | None = None
            for repeat in range(args.repeats + 1):
                result, timing = phase(partial(traverse, graph, identity, positions))
                timing["state"] = (
                    "first call for this ID"
                    if repeat == 0
                    else "repeated; fresh query workspace"
                )
                timing["result_numpy_owned_bytes"] = result.nbytes
                timings.append(timing)
                selected = np.sort(result)
                if first is None:
                    first = selected
                else:
                    np.testing.assert_array_equal(selected, first)
                del result, selected
            assert first is not None
            expected_results.append(first)
            queries.append(
                {
                    "id": identity,
                    "selected_count": first.size,
                    "sorted_ids_sha256": hashlib.sha256(
                        first.astype("<i8").tobytes()
                    ).hexdigest(),
                    "timings": timings,
                }
            )
        record["queries"] = queries
        # Oracle allocation is deliberately after all measured traversals.
        for identity, expected in zip(args.ids, expected_results, strict=True):
            np.testing.assert_array_equal(oracle(graph, identity), expected)
        record["correctness"] = (
            "exact original-ID arrays equal SciPy BFS for every query; repeats exact"
        )
        record["memory_after_oracle_excluded_from_timings"] = memory()
    except Exception as error:
        record["status"] = "failed"
        record["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
