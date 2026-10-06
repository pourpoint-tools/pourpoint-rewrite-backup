"""Numeric graph layouts for the standalone experiment; not an engine."""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pyarrow as pa

Ints = npt.NDArray[np.int64]
Positions = npt.NDArray[np.uint32]


@dataclass(init=False)
class Graph:
    table: pa.Table
    ids: Ints
    levels: npt.NDArray[np.int16]
    offsets: npt.NDArray[np.int32]
    edges: Ints
    order: Positions
    sorted_ids: Ints

    def __init__(self, table: pa.Table) -> None:
        expected = {
            "id": pa.int64(),
            "level": pa.int16(),
            "upstream_ids": pa.list_(pa.int64()),
        }
        for name, dtype in expected.items():
            if table[name].type != dtype or table[name].null_count:
                raise ValueError(f"invalid {name} type/nulls")
        self.table = table.combine_chunks()
        upstream = self.table["upstream_ids"].chunk(0)
        if upstream.values.null_count:
            raise ValueError("null upstream ID")
        self.ids = self.table["id"].chunk(0).to_numpy()
        self.levels = self.table["level"].chunk(0).to_numpy()
        self.offsets = upstream.offsets.to_numpy()
        self.edges = upstream.values.to_numpy()
        if np.any(self.ids <= 0) or np.any(self.levels < 0):
            raise ValueError("nonpositive ID or negative level")
        if self.ids.size > np.iinfo(np.uint32).max:
            raise ValueError("row count exceeds uint32 positions")
        self.order = np.argsort(self.ids).astype(np.uint32)
        self.sorted_ids = self.ids[self.order]
        if np.any(self.sorted_ids[1:] == self.sorted_ids[:-1]):
            raise ValueError("duplicate ID")

    def position(self, identity: int) -> int:
        slot = int(np.searchsorted(self.sorted_ids, identity))
        if slot == self.ids.size or self.sorted_ids[slot] != identity:
            raise KeyError(identity)
        return int(self.order[slot])


def convert(graph: Graph, chunk_rows: int = 65536) -> Positions:
    """Convert edges in bounded row blocks, verifying references and levels."""
    if chunk_rows < 1:
        raise ValueError("chunk_rows must be positive")
    positions = np.empty(graph.edges.size, dtype=np.uint32)
    for start in range(0, graph.ids.size, chunk_rows):
        stop = min(start + chunk_rows, graph.ids.size)
        lo, hi = int(graph.offsets[start]), int(graph.offsets[stop])
        identities = graph.edges[lo:hi]
        slots = np.searchsorted(graph.sorted_ids, identities)
        if np.any(slots == graph.ids.size):
            raise KeyError("missing upstream ID")
        if np.any(graph.sorted_ids[slots] != identities):
            raise KeyError("missing upstream ID")
        rows = graph.order[slots]
        source_levels = np.repeat(
            graph.levels[start:stop], np.diff(graph.offsets[start : stop + 1])
        )
        if np.any(source_levels != graph.levels[rows]):
            raise ValueError("cross-level edge")
        positions[lo:hi] = rows
    return positions


def traverse(graph: Graph, identity: int, positions: Positions | None = None) -> Ints:
    """Inclusive BFS; both variants share numeric queue and visited buffers.

    ID mode searches sorted IDs for each edge. Dense mode indexes converted edges.
    Allocation/reset and original-ID output materialization are inside query time.
    """
    root = graph.position(identity)
    seen = np.zeros(graph.ids.size, dtype=np.bool_)
    queue = np.empty(graph.ids.size, dtype=np.uint32)
    queue[0] = root
    seen[root] = True
    head, tail = 0, 1
    while head < tail:
        row = int(queue[head])
        head += 1
        for edge in range(int(graph.offsets[row]), int(graph.offsets[row + 1])):
            upstream = (
                graph.position(int(graph.edges[edge]))
                if positions is None
                else int(positions[edge])
            )
            if graph.levels[upstream] != graph.levels[row]:
                raise ValueError("cross-level edge")
            if not seen[upstream]:
                seen[upstream] = True
                queue[tail] = upstream
                tail += 1
    return graph.ids[queue[:tail]]
