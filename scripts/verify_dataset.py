"""Sequential integrated HFX verification; run in a fresh process for peak evidence.

Real dataset runs are opt-in. Credentials/proxy configuration enter through
storage options or a JSON configuration file, never the measurement record. Peaks
are process-lifetime high-water marks, not per-phase allocations; do not sum them.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import platform
import resource
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from time import perf_counter

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely

from pourpoint.dataset import Dataset
from pourpoint.geometry import DecodedGeometry, GeometryBatch
from pourpoint.metadata import Bounds
from pourpoint.topology import UnitSelection


def memory() -> dict[str, int]:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "process_lifetime_peak_rss_bytes": int(peak if platform.system() == "Darwin" else peak * 1024),
        "arrow_current_allocated_bytes": pa.total_allocated_bytes(),
        "arrow_pool_peak_bytes": pa.default_memory_pool().max_memory(),
    }


def upstream_oracle(path: Path, terminal: int) -> npt.NDArray[np.int64]:
    """Independent Arrow ID-domain frontier; no production lookup or dense edges.

    This rereads only the verified local graph after all measured queries. Every
    frontier scans the ID column: deliberately independent, not a fast runtime.
    """
    table = pq.read_table(path, columns=["id", "upstream_ids"])
    frontier = pa.array([terminal], type=pa.int64())
    seen: set[int] = set()
    while len(frontier):
        rows = table.filter(pc.call_function("is_in", [table["id"]], options=pc.SetLookupOptions(frontier)))
        if rows.num_rows != len(frontier):
            missing = set(frontier.to_pylist()) - set(rows["id"].to_pylist())
            raise KeyError(f"oracle missing IDs: {sorted(missing)}")
        seen.update(frontier.to_pylist())
        upstream = pc.call_function("unique", [pc.call_function("list_flatten", [rows["upstream_ids"]])])
        frontier = pa.array(sorted(set(upstream.to_pylist()) - seen), type=pa.int64())
    return np.array(sorted(seen), dtype=np.int64)


def run(
    root: str | Path,
    *,
    cache_dir: str | Path,
    output_dir: str | Path,
    unit_id: int,
    storage_options: Mapping[str, object] | None = None,
    window_degrees: float = 0.001,
) -> dict[str, object]:
    """Measure production access; write witnesses and independent checks afterward.

    Traffic must come from external transport receipts joined by UTC boundaries.
    First-query results remain live during repeats for exact identity comparison;
    their Arrow/selection buffers are reported, not counted as dataset buffers.
    """
    if not math.isfinite(window_degrees) or window_degrees < 0:
        raise ValueError("window_degrees must be finite and nonnegative")
    phases: list[dict[str, object]] = []
    data: Dataset | None = None

    def measure[T](name: str, action: Callable[[], T], **details: object) -> T:
        before = memory()
        started_utc = datetime.now(UTC).isoformat()
        started = perf_counter()
        result = action()
        seconds = perf_counter() - started
        finished_utc = datetime.now(UTC).isoformat()
        owner = result if isinstance(result, Dataset) else data
        entry: dict[str, object] = {
            "name": name,
            "started_utc": started_utc,
            "finished_utc": finished_utc,
            "seconds": seconds,
            "memory_before": before,
            "memory_at_ready": memory(),
            "dataset_buffers": {} if owner is None else owner.buffer_bytes,
            "last_access": {} if owner is None else dict(owner.last_access),
            **details,
        }
        if isinstance(result, (GeometryBatch, DecodedGeometry, UnitSelection)):
            entry["selected_count"] = result.table.num_rows if isinstance(result, GeometryBatch) else len(result.ids)
            entry["result_buffer_bytes"] = (
                result.attributes.get_total_buffer_size() + result.ids.nbytes + result.geometries.nbytes
                if isinstance(result, DecodedGeometry)
                else result.buffer_bytes
            )
        phases.append(entry)
        return result

    data = measure("open", lambda: Dataset.open(root, cache_dir=cache_dir, storage_options=storage_options))
    with data:
        selection = measure("topology:first", lambda: data.upstream(unit_id))
        repeated_selection = measure("topology:repeat", lambda: data.upstream(unit_id))
        np.testing.assert_array_equal(selection.ids, repeated_selection.ids)
        del repeated_selection
        selected = measure("records:first", lambda: data.records(selection))
        repeated = measure(
            "records:repeat", lambda: data.records(selection), baseline_retained_bytes=selected.buffer_bytes
        )
        if not selected.table.equals(repeated.table):
            raise AssertionError("repeat records differ")
        del repeated
        np.testing.assert_array_equal(selected.table["id"].to_numpy(), selection.ids)
        requested = [unit_id, int(selection.ids[-1]), unit_id]
        batch = measure("records:batched", lambda: data.records(requested), requested_ids=requested)
        np.testing.assert_array_equal(batch.table["id"].to_numpy(), requested)
        del batch
        terminal = measure("terminal_geometry:first", lambda: data.polygons([unit_id]).decode())
        repeated_terminal = measure("terminal_geometry:repeat", lambda: data.polygons([unit_id]).decode())
        np.testing.assert_array_equal(terminal.ids, [unit_id])
        np.testing.assert_array_equal(repeated_terminal.ids, terminal.ids)
        if not np.all(shapely.equals_exact(terminal.geometries, repeated_terminal.geometries, 0)):
            raise AssertionError("repeat terminal geometry differs")
        del terminal, repeated_terminal
        # Terminal is first in inclusive upstream order; no extra planning read.
        x, y = (float(selected.table[name][0].as_py()) for name in ("outlet_lon", "outlet_lat"))
        level = int(selected.table["level"][0].as_py())
        bounds: Bounds = (
            max(-180, x - window_degrees),
            max(-90, y - window_degrees),
            min(180, x + window_degrees),
            min(90, y + window_degrees),
        )
        candidates = measure(
            "catchment_candidates:first", lambda: data.catchment_candidates(bounds, levels=[level]), bounds=bounds
        )
        repeated_candidates = measure(
            "catchment_candidates:repeat", lambda: data.catchment_candidates(bounds, levels=[level]), bounds=bounds
        )
        if not candidates.table.equals(repeated_candidates.table):
            raise AssertionError("repeat catchment candidates differ")
        del candidates, repeated_candidates
        for name in data.manifest.snap_sets:
            snap = measure(
                f"snap_candidates:{name}:first",
                partial(data.snap_candidates, name, bounds),
                snap_set=name,
                bounds=bounds,
            )
            repeat_snap = measure(
                f"snap_candidates:{name}:repeat",
                partial(data.snap_candidates, name, bounds),
                snap_set=name,
                bounds=bounds,
            )
            if not snap.attributes.equals(repeat_snap.attributes) or not np.all(
                shapely.equals_exact(snap.geometries, repeat_snap.geometries, 0)
            ):
                raise AssertionError("repeat snap candidates differ")
            del snap, repeat_snap
        measured_final_memory = memory()
        # Use the already-acquired local graph; no transport/hash calls in oracle.
        source = data._source
        local = source._local_root
        graph = (
            local / data.manifest.graph
            if local is not None
            else Path(cache_dir) / source.identity / f"{data.manifest.artifacts[data.manifest.graph].sha256}.parquet"
        )
        expected = upstream_oracle(graph, unit_id)
        np.testing.assert_array_equal(np.sort(selection.ids), expected)
        record: dict[str, object] = {
            "status": "passed",
            "phases": phases,
            "dataset": {
                "fabric_name": data.manifest.fabric_name,
                "unit_count": data.manifest.unit_count,
                "topology": data.manifest.topology,
                "source_identity": source.identity,
                "graph_sha256": data.manifest.artifacts[data.manifest.graph].sha256,
                "catchments_sha256": data.manifest.artifacts[data.manifest.catchments].sha256,
            },
            "settings": {
                "unit_id": unit_id,
                "window_degrees": window_degrees,
                "arrow_cpu_count": pa.cpu_count(),
                "arrow_io_thread_count": pa.io_thread_count(),
            },
            "versions": {
                name: importlib.metadata.version(name)
                for name in ("pourpoint", "numpy", "pyarrow", "shapely", "obstore")
            },
            "platform": platform.platform(),
            "python": platform.python_version(),
            "topology_check": {"selected_count": len(expected), "exact_membership": True},
            "oracle_method": "post-timing local Arrow ID-domain frontier scans",
            "measured_final_memory": measured_final_memory,
            "memory_method": "RSS and Arrow peaks are process-lifetime high-water marks, not per-phase allocations",
            "decoded_native_bytes": "unavailable from Shapely/GEOS",
            "traffic": "external transport receipts required; join by phase UTC boundaries, never use object sizes",
            "state": "first and repeated queries share one Dataset; OS cache uncontrolled",
            "retention": {
                "selected_records_bytes": selected.buffer_bytes,
                "selection_bytes": selection.buffer_bytes,
                "note": "selection and first records stay live during all later phases; oracle and witness serialization run after timing",
            },
        }
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        with pa.ipc.new_file(output / "selected.arrow", selected.table.schema) as writer:
            writer.write_table(selected.table)
    record["after_close_memory"] = memory()
    (output / "measurement.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--unit-id", type=int, required=True)
    parser.add_argument("--window-degrees", type=float, default=0.001)
    parser.add_argument("--storage-options-json", type=Path)
    args = parser.parse_args()
    options = None if args.storage_options_json is None else json.loads(args.storage_options_json.read_text())
    if options is not None and not isinstance(options, dict):
        raise ValueError("storage options JSON must be an object")
    run(
        args.root,
        cache_dir=args.cache,
        output_dir=args.output_dir,
        unit_id=args.unit_id,
        window_degrees=args.window_degrees,
        storage_options=options,
    )


if __name__ == "__main__":
    main()
