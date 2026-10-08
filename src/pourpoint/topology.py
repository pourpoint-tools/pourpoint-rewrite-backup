"""Compact same-level topology and source-bound, query-owned selections.

Only numeric buffers remain resident, not Arrow tables or upstream ID edges.
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
import pyarrow as pa

type IDs = npt.NDArray[np.int64]
type Positions = npt.NDArray[np.uint32]
type IDInput = list[int] | IDs


def _readonly[T: np.generic](array: npt.NDArray[T]) -> npt.NDArray[T]:
    """Compact immutable backing also prevents setflags(write=True)."""
    return np.frombuffer(array.tobytes(), dtype=array.dtype)


@dataclass(frozen=True, eq=False)
class UnitSelection:
    """Request-ordered IDs and positions bound to one prepared graph ordering."""

    ids: IDs
    positions: Positions
    source: object

    def require_source(self, topology: "Topology") -> None:
        if self.source is not topology.source:
            raise ValueError("selection belongs to another topology source")

    @property
    def buffer_bytes(self) -> int:
        return self.ids.nbytes + self.positions.nbytes


@dataclass(frozen=True, init=False, eq=False)
class Topology:
    """Prepared graph. IDs are not positions; all retained arrays are read-only.

    Each upstream call owns a bool visited array and uint32 queue (5*N bytes),
    released before return. Output copies retain only 12 bytes per selected row.
    Producer acyclicity and tree validity are trusted, not globally audited.
    Construction additionally uses Arrow consolidation, sorting and conversion
    temporaries; buffer_bytes is retained storage, not construction peak RSS.
    """

    ids: IDs
    levels: npt.NDArray[np.int16]
    offsets: Positions
    edges: Positions
    _order: Positions
    _sorted_ids: IDs
    source: object

    def __init__(self, table: pa.Table, topology: Literal["tree", "dag"]) -> None:
        if topology not in ("tree", "dag"):
            raise ValueError("graph: topology must be tree or dag")
        if table.num_rows > np.iinfo(np.uint32).max:
            raise ValueError("graph: row count exceeds uint32 position capacity")
        for name, dtype in {"id": pa.int64(), "level": pa.int16(), "upstream_ids": pa.list_(pa.int64())}.items():
            if name not in table.column_names or table[name].type != dtype or table[name].null_count:
                raise ValueError(f"graph: invalid {name} type or null values")
        ids = table["id"].combine_chunks().to_numpy()
        levels = table["level"].combine_chunks().to_numpy()
        upstream = table["upstream_ids"].combine_chunks()
        if upstream.values.null_count:
            raise ValueError("graph: null upstream_ids entry")
        if np.any(ids <= 0):
            raise ValueError("graph: id must be positive")
        if np.any(levels < 0):
            raise ValueError("graph: level must be nonnegative")
        if len(upstream.values) > np.iinfo(np.uint32).max:
            raise ValueError("graph: edge count exceeds uint32 offset capacity")
        order = np.argsort(ids).astype(np.uint32)
        sorted_ids = ids[order]
        if np.any(sorted_ids[1:] == sorted_ids[:-1]):
            raise ValueError("graph: duplicate id")
        for name, value in (("ids", ids), ("levels", levels), ("_order", order), ("_sorted_ids", sorted_ids)):
            object.__setattr__(self, name, _readonly(value))
        object.__setattr__(self, "source", object())
        object.__setattr__(self, "offsets", _readonly(upstream.offsets.to_numpy().astype(np.uint32)))
        edges = np.empty(len(upstream.values), dtype=np.uint32)
        edge_ids = upstream.values.to_numpy()
        # Bound mapping temporaries by row blocks; a single large adjacency can
        # still exceed this row limit, so it is not a byte budget.
        for start in range(0, self.ids.size, 65536):
            stop = min(start + 65536, self.ids.size)
            lo, hi = int(self.offsets[start]), int(self.offsets[stop])
            mapped = self.positions(edge_ids[lo:hi])
            source_levels = np.repeat(self.levels[start:stop], np.diff(self.offsets[start : stop + 1]))
            if np.any(source_levels != self.levels[mapped]):
                raise ValueError("graph: cross-level upstream_ids reference")
            edges[lo:hi] = mapped
        object.__setattr__(self, "edges", _readonly(edges))

    def positions(self, ids: IDInput) -> Positions:
        """Resolve every ID; preserve request order and repeated IDs."""
        if isinstance(ids, list) and any(isinstance(value, (bool, np.bool_)) for value in ids):
            raise ValueError("unit IDs must be positive int64 values")
        requested = np.asarray(ids)
        if requested.ndim != 1 or (requested.size and requested.dtype.kind not in "iu"):
            raise ValueError("unit IDs must be a one-dimensional integer array")
        if requested.size and (np.any(requested <= 0) or np.any(requested > np.iinfo(np.int64).max)):
            raise ValueError("unit IDs must be positive int64 values")
        requested = requested.astype(np.int64, copy=False)
        slots = np.searchsorted(self._sorted_ids, requested)
        valid = slots < self.ids.size
        valid[valid] &= self._sorted_ids[slots[valid]] == requested[valid]
        if not np.all(valid):
            raise KeyError(f"graph: unknown unit ID {int(requested[~valid][0])}")
        return _readonly(self._order[slots])

    def selection(self, ids: IDInput) -> UnitSelection:
        positions = self.positions(ids)
        return UnitSelection(_readonly(self.ids[positions]), positions, self.source)

    def upstream(self, identity: int) -> UnitSelection:
        """Inclusive upstream membership, once per unit, with terminal first."""
        root = int(self.positions([identity])[0])
        seen = np.zeros(self.ids.size, dtype=np.bool_)
        queue = np.empty(self.ids.size, dtype=np.uint32)
        queue[0] = root
        seen[root] = True
        head, tail = 0, 1
        while head < tail:
            row = int(queue[head])
            head += 1
            for upstream in self.edges[int(self.offsets[row]) : int(self.offsets[row + 1])]:
                if not seen[upstream]:
                    seen[upstream] = True
                    queue[tail] = upstream
                    tail += 1
        positions = _readonly(queue[:tail])
        return UnitSelection(_readonly(self.ids[positions]), positions, self.source)

    @property
    def buffer_bytes(self) -> int:
        return sum(a.nbytes for a in (self.ids, self.levels, self.offsets, self.edges, self._order, self._sorted_ids))

    @property
    def query_workspace_bytes(self) -> int:
        return int(self.ids.size) * 5
