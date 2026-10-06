"""A projected Arrow read experiment, not an engine or conformance validator."""

from __future__ import annotations

import time

IMPORT_START = time.perf_counter()

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
from pathlib import Path
from typing import Any

import boto3
import numpy as np
import numpy.typing as npt
import psutil
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as fs
import pyarrow.parquet as pq

IMPORT_SECONDS = time.perf_counter() - IMPORT_START
PROJECTION = ["id", "level", "upstream_ids"]
IntArray = npt.NDArray[np.int64]


def check_position_capacity(rows: int) -> None:
    if rows > np.iinfo(np.uint32).max:
        raise ValueError("graph exceeds uint32 position capacity")


class Graph:
    def __init__(self, table: pa.Table) -> None:
        compact_start = time.perf_counter()
        self.table = table.combine_chunks()
        self.compact_seconds = time.perf_counter() - compact_start
        index_start = time.perf_counter()
        for name, dtype in zip(
            PROJECTION, [pa.int64(), pa.int16(), pa.list_(pa.int64())], strict=True
        ):
            col = self.table[name]
            if col.type != dtype or col.null_count:
                raise ValueError(f"invalid {name} type or nulls")
        self.ids: IntArray = self.table["id"].chunk(0).to_numpy()
        self.levels = self.table["level"].chunk(0).to_numpy()
        adjacency = self.table["upstream_ids"].chunk(0)
        if adjacency.values.null_count:
            raise ValueError("null edge")
        self.offsets = adjacency.offsets.to_numpy()
        self.edges: IntArray = adjacency.values.to_numpy()
        check_position_capacity(len(self.ids))
        self.order: npt.NDArray[np.uint32] = np.argsort(self.ids).astype(np.uint32)
        self.sorted_ids = self.ids[self.order]
        if np.any(self.ids <= 0) or np.any(self.levels < 0):
            raise ValueError("nonpositive ID or negative level")
        if np.any(self.sorted_ids[1:] == self.sorted_ids[:-1]):
            raise ValueError("duplicate ID")
        self.index_runtime_checks_seconds = time.perf_counter() - index_start

    def position(self, unit_id: int) -> int:
        i = int(np.searchsorted(self.sorted_ids, unit_id))
        if i == len(self.ids) or self.sorted_ids[i] != unit_id:
            raise KeyError(unit_id)
        return int(self.order[i])

    def upstream(self, unit_id: int) -> IntArray:
        start = self.position(unit_id)
        visited = np.zeros(len(self.ids), dtype=np.bool_)
        stack = np.empty(len(self.ids), dtype=np.int64)
        stack[0] = start
        visited[start] = True
        count = 1
        while count:
            count -= 1
            row = int(stack[count])
            for edge in self.edges[self.offsets[row] : self.offsets[row + 1]]:
                neighbor = self.position(int(edge))
                if self.levels[neighbor] != self.levels[row]:
                    raise ValueError("cross-level edge")
                if not visited[neighbor]:
                    visited[neighbor] = True
                    stack[count] = neighbor
                    count += 1
        return np.sort(self.ids[visited])

    def buffers(self) -> dict[str, int]:
        roots: dict[int, int] = {}
        for column in self.table.columns:
            for chunk in column.chunks:
                for buffer in chunk.buffers():
                    if buffer is not None:
                        while buffer.parent is not None:
                            buffer = buffer.parent
                        roots[buffer.address] = buffer.size
        return {
            "graph_referenced_bytes": self.table.nbytes,
            "graph_retained_backing_bytes": sum(roots.values()),
            "lookup_owned_bytes": self.order.nbytes + self.sorted_ids.nbytes,
            "lookup_referenced_bytes": self.order.nbytes + self.sorted_ids.nbytes,
            "parent_attribute_bytes": 0,
            "locator_bytes": 0,
            "numpy_graph_views_additional_owned_bytes": 0,
        }


def read_graph(
    source: str, mode: str, filesystem: fs.FileSystem | None
) -> tuple[Graph, dict[str, float], dict[str, int]]:
    start = time.perf_counter()
    parquet = pq.ParquetFile(
        source,
        filesystem=filesystem,
        pre_buffer=mode == "prefetch",
        buffer_size=0,
        memory_map=False,
    )
    opened = time.perf_counter()
    table = parquet.read(columns=PROJECTION, use_threads=mode == "prefetch")
    loaded = time.perf_counter()
    graph = Graph(table)
    indexed = time.perf_counter()
    metadata = parquet.metadata
    compressed = sum(
        metadata.row_group(r).column(c).total_compressed_size
        for r in range(metadata.num_row_groups)
        for c in range(metadata.num_columns)
        if metadata.row_group(r).column(c).path_in_schema.split(".")[0] in PROJECTION
    )
    return (
        graph,
        {
            "open_footer_seconds": opened - start,
            "read_seconds": loaded - opened,
            "compact_index_runtime_checks_seconds": indexed - loaded,
            "compact_seconds": graph.compact_seconds,
            "index_runtime_checks_seconds": graph.index_runtime_checks_seconds,
            "load_total_seconds": indexed - start,
        },
        {
            "row_groups": metadata.num_row_groups,
            "projected_compressed_column_chunk_bytes": compressed,
        },
    )


def reference_upstream(table: pa.Table, unit_id: int) -> IntArray:
    """Independent vectorized frontier expansion; no numeric lookup or CSR traversal."""
    selected = pa.array([unit_id], type=pa.int64())
    while True:
        # Arrow registers these compute functions dynamically; ty cannot see their attributes.
        rows = table.filter(pc.is_in(table["id"], value_set=selected))  # ty: ignore[unresolved-attribute]
        edges = pc.list_flatten(rows["upstream_ids"]).combine_chunks()  # ty: ignore[unresolved-attribute]
        expanded = pc.unique(pa.concat_arrays([selected, edges]))  # ty: ignore[unresolved-attribute]
        if len(expanded) == len(selected):
            return np.sort(expanded.to_numpy())
        selected = expanded


def digest(values: IntArray) -> str:
    return hashlib.sha256(values.astype("<i8", copy=False).tobytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--mode", choices=["serial", "prefetch"], required=True)
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queries", type=int, nargs="+", required=True)
    parser.add_argument("--concurrent-activity", required=True)
    args = parser.parse_args()
    start = time.perf_counter()
    filesystem = None
    if args.remote:
        credentials = boto3.Session(profile_name="hetzner-pourpoint").get_credentials()
        if credentials is None:
            raise RuntimeError("profile credentials unavailable")
        frozen = credentials.get_frozen_credentials()
        filesystem = fs.S3FileSystem(
            access_key=frozen.access_key,
            secret_key=frozen.secret_key,
            session_token=frozen.token,
            endpoint_override="https://fsn1.your-objectstorage.com",
            region="fsn1",
        )
    setup = time.perf_counter() - start
    graph, timings, storage = read_graph(args.source, args.mode, filesystem)
    resident = psutil.Process().memory_info().rss
    queries: list[dict[str, Any]] = []
    for unit_id in args.queries:
        qstart = time.perf_counter()
        selected = graph.upstream(unit_id)
        elapsed = time.perf_counter() - qstart
        queries.append(
            {
                "unit_id": unit_id,
                "seconds": elapsed,
                "count": len(selected),
                "sorted_ids_sha256": digest(selected),
                "reference_exact_match": bool(
                    np.array_equal(selected, reference_upstream(graph.table, unit_id))
                ),
                "result_bytes": selected.nbytes,
                "workspace_owned_bytes": len(graph.ids) * 9,
            }
        )
    if not all(query["reference_exact_match"] for query in queries):
        raise AssertionError("independent exact selected-ID mismatch")
    record = {
        "status": "ok",
        "experiment_commit": args.commit,
        "object_identity": json.loads(args.identity.read_text()),
        "source": args.source,
        "mode": args.mode,
        "state": "fresh process; remote cache uncontrolled"
        if args.remote
        else "fresh process; warm local / OS cache uncontrolled",
        "concurrent_activity": args.concurrent_activity,
        "settings": {
            "projection": PROJECTION,
            "pre_buffer": args.mode == "prefetch",
            "use_threads": args.mode == "prefetch",
            "buffer_size": 0,
            "memory_map": False,
            "arrow_cpu_count": pa.cpu_count(),
            "arrow_io_thread_count": pa.io_thread_count(),
        },
        "versions": {
            name: importlib.metadata.version(name)
            for name in ["pyarrow", "numpy", "psutil", "boto3"]
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "timings": {
            "imports_seconds": IMPORT_SECONDS,
            "filesystem_setup_seconds": setup,
            **timings,
        },
        "graph_value_sha256": {"ids": digest(graph.ids), "edges": digest(graph.edges)},
        "rows": len(graph.ids),
        "edges": int(graph.offsets[-1] - graph.offsets[0]),
        "buffers": graph.buffers(),
        "rss_after_load_bytes": resident,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if platform.system() == "Darwin" else 1024),
        "peak_method": "process ru_maxrss; includes imports, queries, reference checks",
        "arrow_live_allocated_bytes": pa.total_allocated_bytes(),
        "arrow_pool_peak_bytes": pa.default_memory_pool().max_memory(),
        "remote_requests": "not measured",
        "remote_response_bytes": "not measured",
        "storage": storage,
        "queries": queries,
        "excluded": [
            "parents",
            "catchment locator",
            "geometry",
            "snap",
            "rasters",
            "complete conformance validation",
            "initial download",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
