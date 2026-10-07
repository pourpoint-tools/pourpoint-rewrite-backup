"""Untimed graph selection and independent full-source correctness scan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pyarrow import ipc


def upstream(graph: pa.Table, terminal: int) -> list[int]:
    ids = graph["id"].combine_chunks().to_numpy()
    order = np.argsort(ids)
    sorted_ids = ids[order]
    adjacency = graph["upstream_ids"].combine_chunks()
    offsets = adjacency.offsets.to_numpy()
    values = adjacency.values.to_numpy()
    seen: set[int] = set()
    stack = [terminal]
    while stack:
        identity = stack.pop()
        if identity in seen:
            continue
        index = int(np.searchsorted(sorted_ids, identity))
        if index == len(ids) or sorted_ids[index] != identity:
            raise KeyError(identity)
        row = int(order[index])
        seen.add(identity)
        stack.extend(int(v) for v in values[offsets[row] : offsets[row + 1]])
    return sorted(seen)


def source_reference(source: Path, ids: list[int]) -> pa.Table:
    """Scan all source rows with Arrow membership, no benchmark address code."""
    file = pq.ParquetFile(source / "catchments.parquet")
    wanted = pa.array(ids, type=pa.int64())
    pieces: list[pa.RecordBatch] = []
    for batch in file.iter_batches(batch_size=4096, use_threads=False):
        selected = batch.filter(
            pc.call_function(
                "is_in", [batch["id"]], pc.SetLookupOptions(value_set=wanted)
            )
        )
        if selected.num_rows:
            pieces.append(selected)
    result = pa.Table.from_batches(pieces, schema=file.schema_arrow).sort_by("id")
    if result["id"].to_pylist() != sorted(set(ids)):
        raise AssertionError("reference missing/duplicate source IDs")
    return result


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as file:
        while data := file.read(8 * 1024 * 1024):
            sha.update(data)
    return sha.hexdigest()


def prepare(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    graph = pq.read_table(source / "graph.parquet", columns=["id", "upstream_ids"])
    whole = upstream(graph, 6120007000)
    # Restrict the search for example terminals to this known complete watershed.
    local = graph.filter(
        pc.call_function(
            "is_in", [graph["id"]], pc.SetLookupOptions(value_set=pa.array(whole))
        )
    )
    children = dict(
        zip(local["id"].to_pylist(), local["upstream_ids"].to_pylist(), strict=True)
    )
    sizes: dict[int, int] = {}
    stack: list[tuple[int, bool]] = [(6120007000, False)]
    while stack:
        node, expanded = stack.pop()
        if expanded:
            sizes[node] = 1 + sum(sizes[child] for child in children[node])
        else:
            stack.append((node, True))
            stack.extend((child, False) for child in children[node])
    small_terminal = min(i for i, n in sizes.items() if 25 <= n <= 50)
    large_terminal = min(i for i, n in sizes.items() if 2000 <= n <= 5000)
    small = upstream(local, small_terminal)
    large = upstream(local, large_terminal)
    sorted_graph = np.sort(graph["id"].combine_chunks().to_numpy())
    scattered = sorted_graph[
        np.linspace(0, len(sorted_graph) - 1, 32, dtype=np.int64)
    ].tolist()
    workloads = {
        "single": [[3110936770]],
        "small_upstream": [small],
        "larger_upstream": [large],
        "overlap": [large[:64], large[32:96], large[:64]],
        "scattered": [scattered],
        "large_geometry": [[6010000010, 6050010520, 2050067280]],
    }
    workloads["batch"] = [
        request for requests in workloads.values() for request in requests
    ]
    document = {
        "workloads": workloads,
        "terminals": {"small": small_terminal, "larger": large_terminal},
        "unit_count": graph.num_rows,
        "source": str(source),
        "graph_sha256": digest(source / "graph.parquet"),
    }
    (output / "selections.json").write_text(json.dumps(document, indent=2) + "\n")
    union = sorted(
        {i for requests in workloads.values() for request in requests for i in request}
    )
    reference = source_reference(source, union)
    with ipc.new_file(output / "reference.arrow", reference.schema) as writer:
        writer.write_table(reference)
    (output / "reference.json").write_text(
        json.dumps(
            {
                "rows": reference.num_rows,
                "sha256": digest(output / "reference.arrow"),
                "source_catchments_sha256": digest(source / "catchments.parquet"),
                "selections_sha256": digest(output / "selections.json"),
                "method": "full independent source Parquet scan; exact Arrow is_in; sorted IDs",
            },
            indent=2,
        )
        + "\n"
    )


def verify(prepared: Path, runs: Path, output: Path) -> None:
    reference = ipc.open_file(prepared / "reference.arrow").read_all()
    workloads = json.loads((prepared / "selections.json").read_text())["workloads"]
    records: list[dict[str, object]] = []
    for result_path in sorted(runs.glob("*.json")):
        result = json.loads(result_path.read_text())
        requests = workloads[result["workload"]]
        for call in result["calls"]:
            for request, witness in zip(requests, call["witnesses"], strict=True):
                actual = ipc.open_file(witness).read_all()
                indices = pc.call_function(
                    "index_in",
                    [pa.array(request, type=pa.int64())],
                    pc.SetLookupOptions(value_set=reference["id"]),
                )
                expected = reference.take(indices)
                if not actual.equals(expected, check_metadata=True):
                    raise AssertionError(
                        f"source attributes/nulls/WKB mismatch: {witness}"
                    )
                records.append(
                    {
                        "witness": witness,
                        "rows": actual.num_rows,
                        "sha256": digest(Path(witness)),
                    }
                )
    if not records:
        raise ValueError("no witnesses")
    output.write_text(
        json.dumps(
            {
                "status": "passed",
                "comparison": "exact source Arrow table equality, all columns, nulls, WKB, schema metadata, caller order and duplicates",
                "reference": json.loads((prepared / "reference.json").read_text()),
                "checks": records,
            },
            indent=2,
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("source", type=Path)
    prep.add_argument("output", type=Path)
    check = sub.add_parser("verify")
    check.add_argument("prepared", type=Path)
    check.add_argument("runs", type=Path)
    check.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.source, args.output)
    else:
        verify(args.prepared, args.runs, args.output)


if __name__ == "__main__":
    main()
