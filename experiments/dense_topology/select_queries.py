"""Select headwater, medium and large queries from the complete graph."""

import argparse
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

from topology import Graph, convert


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.data / "manifest.json").read_text())
    if manifest["topology"] != "tree":
        raise ValueError("component-based selection requires declared tree")
    graph = Graph(
        pq.read_table(
            args.data / "graph.parquet", columns=["id", "level", "upstream_ids"]
        )
    )
    positions = convert(graph)
    downstream_count = np.bincount(positions, minlength=graph.ids.size)
    if np.any(downstream_count > 1):
        raise ValueError("component-based selection requires <=1 downstream edge")
    matrix = csr_matrix(
        (np.ones(positions.size, dtype=np.int8), positions, graph.offsets),
        shape=(graph.ids.size,) * 2,
    )
    _, labels = connected_components(matrix, directed=False)
    counts = np.bincount(labels)
    finest = int(graph.levels.max())
    terminals = np.flatnonzero((graph.levels == finest) & (downstream_count == 0))
    sizes = counts[labels[terminals]]
    large = terminals[np.lexsort((graph.ids[terminals], -sizes))[0]]
    medium = terminals[np.lexsort((graph.ids[terminals], np.abs(sizes - 1000)))[0]]
    headwaters = graph.ids[(graph.levels == finest) & (np.diff(graph.offsets) == 0)]
    receipt = json.loads(args.receipt.read_text())
    record = {
        "commit": args.commit,
        "method": "finest level: terminal of largest weak component; terminal "
        "of weak component nearest 1000 rows; smallest headwater ID. "
        "Ties use smallest ID. Requires declared tree and checked "
        "<=1 downstream edge. Selection excluded from measurement.",
        "level": finest,
        "queries": [
            {
                "role": "large",
                "id": int(graph.ids[large]),
                "component_rows": int(counts[labels[large]]),
            },
            {
                "role": "medium",
                "id": int(graph.ids[medium]),
                "component_rows": int(counts[labels[medium]]),
            },
            {"role": "headwater", "id": int(headwaters.min())},
        ],
        "objects": [
            item
            for item in receipt["objects"]
            if item["relative_path"] in ("manifest.json", "graph.parquet")
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n")
    print(" ".join(str(query["id"]) for query in record["queries"]))


if __name__ == "__main__":
    main()
