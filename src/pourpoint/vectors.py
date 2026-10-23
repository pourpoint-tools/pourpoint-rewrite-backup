"""Selective final HFX feature and immediate-relationship access."""

from __future__ import annotations

from bisect import bisect_left
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from typing import cast

import numpy as np
import pyarrow as pa

from pourpoint._selection import compact_dictionaries
from pourpoint.metadata import Bounds, SpatialManifest
from pourpoint.spatial import RowGroupBounds, conservative_bounds, overlap
from pourpoint.storage import Source


def _ids(values: Iterable[int]) -> list[int]:
    result = []
    for value in values:
        if isinstance(value, np.integer):
            value = int(value)
        if type(value) is not int or not 0 < value < 2**63:
            raise ValueError("IDs must be positive int64 values")
        result.append(value)
    return result


def _columns(schema: pa.Schema, columns: Sequence[str] | None) -> list[str]:
    names = schema.names if columns is None else list(columns)
    if len(set(names)) != len(names) or any(name not in schema.names for name in names):
        raise ValueError("unknown or duplicate column")
    return names


def _empty(schema: pa.Schema, names: list[str]) -> pa.Table:
    return pa.Table.from_batches([], schema=schema).select(names)


class _IndexedIPC:
    """One published ID directory; projected readers acquire selected IPC fields."""

    def __init__(self, source: Source, manifest: SpatialManifest, data: str, directory: str, count: int) -> None:
        self.source, self.manifest, self.data = source, manifest, data
        self.stream = source.open_artifact(data, manifest.artifacts[data], bounded=True)
        reader = pa.ipc.open_file(self.stream)
        self.schema = reader.schema
        if len(set(self.schema.names)) != len(self.schema.names) or self.schema.field("id") != pa.field(
            "id", pa.int64(), False
        ):
            raise ValueError("IPC: invalid ID schema")
        declaration = source.read_json(directory, manifest.artifacts[directory])
        entries = declaration.get("batches")
        if (
            declaration.get("version") != 1
            or not isinstance(entries, list)
            or len(entries) != reader.num_record_batches
        ):
            raise ValueError("IPC directory: invalid batches")
        self.ranges: list[tuple[int, int, int]] = []
        for number, entry in enumerate(entries):
            if not isinstance(entry, dict) or entry.get("batch") != number:
                raise ValueError("IPC directory: invalid batch order")
            low, high, rows = (entry.get(k) for k in ("min_id", "max_id", "rows"))
            if any(type(v) is not int for v in (low, high, rows)):
                raise ValueError("IPC directory: invalid range")
            low, high, rows = cast(int, low), cast(int, high), cast(int, rows)
            if (
                not 0 < low <= high < 2**63
                or not 0 < rows <= high - low + 1
                or (self.ranges and low <= self.ranges[-1][1])
            ):
                raise ValueError("IPC directory: invalid range")
            self.ranges.append((low, high, rows))
        if sum(r[2] for r in self.ranges) != count:
            raise ValueError("IPC directory: count mismatch")
        self.maxima = [r[1] for r in self.ranges]
        self._reader = reader
        self._projection = self.schema.names

    def read(self, requested: list[int], columns: Sequence[str] | None = None) -> pa.Table:
        self.source.check_artifact(self.data)
        names = _columns(self.schema, columns)
        if not requested:
            return _empty(self.schema, names)
        projection = [n for n in self.schema.names if n in {*names, "id"}]
        if projection != self._projection:
            self.stream.seek(0)
            self._reader = pa.ipc.open_file(
                self.stream,
                options=pa.ipc.IpcReadOptions(included_fields=[self.schema.get_field_index(n) for n in projection]),
            )
            self._projection = projection
        groups: dict[int, list[tuple[int, int]]] = {}
        for position, value in enumerate(requested):
            group = bisect_left(self.maxima, value)
            if group == len(self.ranges) or value < self.ranges[group][0]:
                raise KeyError(f"unknown ID {value}")
            groups.setdefault(group, []).append((position, value))
        pieces: list[pa.RecordBatch] = []
        positions: list[int] = []
        for group, queries in groups.items():
            batch = self._reader.get_batch(group)
            low, high, rows = self.ranges[group]
            keys = batch.column("id").to_numpy()
            if (
                batch.num_rows != rows
                or batch.column("id").null_count
                or keys[0] != low
                or keys[-1] != high
                or np.any(keys[1:] <= keys[:-1])
            ):
                raise ValueError("IPC: IDs or count disagree with directory")
            indices = []
            for position, value in queries:
                index = int(np.searchsorted(keys, value))
                if index == len(keys) or keys[index] != value:
                    raise KeyError(f"unknown ID {value}")
                indices.append(index)
                positions.append(position)
            pieces.append(batch.take(pa.array(indices, type=pa.int64())))
        self.source.check_artifact(self.data)
        return compact_dictionaries(
            pa.Table.from_batches(pieces).take(pa.array(np.argsort(positions), type=pa.int64())).select(names)
        )


class VectorReader:
    """An eager domain reader. Source owns resources; returned tables own their data."""

    def __init__(self, source: Source, manifest: SpatialManifest, domain: str) -> None:
        self.source, self.manifest, self.domain = source, manifest, domain
        self.family = manifest.families[domain]
        family = self.family
        source.open_artifact(family.data, manifest.artifacts[family.data], bounded=True)
        self._file = source.parquet(family.data, manifest.artifacts[family.data])
        self.schema = self._file.schema_arrow
        if self._file.metadata.num_rows != family.count:
            raise ValueError("feature count mismatch")
        if len(set(self.schema.names)) != len(self.schema.names) or self.schema.field("id") != pa.field(
            "id", pa.int64(), False
        ):
            raise ValueError("feature ID schema mismatch")
        self._bounds: RowGroupBounds | None = None
        self._lookup: _IndexedIPC | None = None
        self._relationships: _IndexedIPC | None = None
        self._adjacency: OrderedDict[tuple[str, int], tuple[int, ...]] = OrderedDict()
        self._adjacency_bytes = 0

    @property
    def buffer_bytes(self) -> int:
        """Retained spatial arrays and estimated adjacency storage, excluding native metadata."""
        return self._adjacency_bytes + (0 if self._bounds is None else self._bounds.buffer_bytes)

    def records(self, ids: Iterable[int], columns: Sequence[str] | None = None) -> pa.Table:
        requested = _ids(ids)
        names = _columns(self.schema, columns)
        if not requested:
            self.source.check_artifact(self.family.data)
            return _empty(self.schema, names)
        if self._lookup is None:
            family = self.family
            lookup = _IndexedIPC(self.source, self.manifest, family.lookup, family.directory, family.count)
            expected = pa.schema(
                [
                    pa.field("id", pa.int64(), False),
                    pa.field("block", pa.int32(), False),
                    pa.field("row", pa.int32(), False),
                ]
            )
            if (
                not lookup.schema.equals(expected)
                or (lookup.schema.metadata or {}).get(b"hfx:data_sha256") != family.data_sha256.encode()
            ):
                raise ValueError("lookup schema or feature binding mismatch")
            self._lookup = lookup
        table = self._lookup.read(requested)
        if any(c.null_count for c in table.columns):
            raise ValueError("lookup: null address")
        addresses: dict[int, list[tuple[int, int, int]]] = {}
        for pos, (value, group, row) in enumerate(
            zip(requested, table["block"].to_pylist(), table["row"].to_pylist(), strict=True)
        ):
            addresses.setdefault(group, []).append((pos, value, row))
        return self._records(addresses, names)

    def _records(self, addresses: dict[int, list[tuple[int, int, int]]], names: list[str]) -> pa.Table:
        self.source.check_artifact(self.family.data)
        pieces: list[pa.Table] = []
        positions: list[int] = []
        projection = list(dict.fromkeys([*names, "id"]))
        for group, queries in sorted(addresses.items()):
            if not 0 <= group < self._file.num_row_groups or any(
                not 0 <= row < self._file.metadata.row_group(group).num_rows for _, _, row in queries
            ):
                raise ValueError("lookup: feature address out of bounds")
            self.source.prefetch_parquet(self.family.data, self.manifest.artifacts[self.family.data], group, projection)
            table = self._file.read_row_group(group, columns=projection, use_threads=False)
            selected = table.take(pa.array([r for _, _, r in queries], type=pa.int64()))
            if selected["id"].to_pylist() != [v for _, v, _ in queries]:
                raise ValueError("lookup: addressed feature ID mismatch")
            pieces.append(selected)
            positions.extend(p for p, _, _ in queries)
        self.source.check_artifact(self.family.data)
        if not pieces:
            return _empty(self.schema, names)
        return compact_dictionaries(
            pa.concat_tables(pieces).take(pa.array(np.argsort(positions), type=pa.int64())).select(names)
        )

    def candidates(
        self, bounds: Bounds, levels: Sequence[int] | None = None, columns: Sequence[str] | None = None
    ) -> pa.Table:
        """Conservative bbox overlap, not exact geometry intersection."""
        envelope = conservative_bounds(bounds)
        names = _columns(self.schema, columns)
        tiers = None if levels is None else tuple(levels)
        if tiers is not None and any(type(v) is not int or v < 0 for v in tiers):
            raise ValueError("levels must be nonnegative integers")
        self.source.check_artifact(self.family.data)
        if self._bounds is None:
            self._bounds = RowGroupBounds(self._file)
        discovery = [n for n in ("id", "level", "bbox") if n in self.schema.names]
        addresses: dict[int, list[tuple[int, int, int]]] = {}
        position = 0
        for group in self._bounds.candidate_groups(envelope, levels=tiers):
            self.source.prefetch_parquet(self.family.data, self.manifest.artifacts[self.family.data], group, discovery)
            table = self._file.read_row_group(group, columns=discovery, use_threads=False)
            mask = overlap(table, envelope)
            if tiers is not None:
                mask &= np.isin(table["level"].to_numpy(), tiers)
            for row in np.flatnonzero(mask).tolist():
                value = table["id"][row].as_py()
                _ids([value])
                addresses.setdefault(group, []).append((position, value, row))
                position += 1
        return self._records(addresses, names)

    def relationships(self, ids: Iterable[int], columns: Sequence[str] | None = None) -> pa.Table:
        """Return supplied immediate relationships without inferring flow or outlets."""
        requested = _ids(ids)
        if self._relationships is None:
            declaration = self.manifest.relationships[self.domain]
            reader = _IndexedIPC(
                self.source,
                self.manifest,
                cast(str, declaration["data"]),
                cast(str, declaration["directory"]),
                self.family.count,
            )
            declared = cast(dict[str, object], declaration["columns"])
            if set(reader.schema.names) != {"id", *declared}:
                raise ValueError("relationship columns disagree with declaration")
            for field in reader.schema:
                if field.name != "id" and (
                    field.nullable or not pa.types.is_list(field.type) or field.type.value_type != pa.int64()
                ):
                    raise ValueError("relationship schema must contain nonnull list<int64>")
            self._relationships = reader
        table = self._relationships.read(requested, columns)
        for name in table.column_names:
            if name == "id":
                continue
            for neighbors in table[name].to_pylist():
                if (
                    neighbors is None
                    or any(type(v) is not int or not 0 < v < 2**63 for v in neighbors)
                    or neighbors != sorted(set(neighbors))
                ):
                    raise ValueError("relationship IDs must be nonnull, positive, sorted and unique")
        return table

    def traverse(self, ids: Iterable[int], relationship: str = "upstream_ids") -> list[int]:
        """Breadth-first same-domain traversal, including seeds and deduplicating cycles.

        The reusable adjacency cache is capped at 8 MiB of estimated Python storage;
        one call's returned IDs and visited workspace necessarily scale with its result.
        """
        declaration = self.manifest.relationships[self.domain]
        columns = cast(dict[str, dict[str, object]], declaration["columns"])
        if relationship not in columns or columns[relationship]["target_domain"] != self.domain:
            raise ValueError("traversal requires a declared same-domain relationship")
        self.source.check_artifact(self.family.data)
        frontier = list(dict.fromkeys(_ids(ids)))
        seen = set(frontier)
        result: list[int] = []
        while frontier:
            missing = [value for value in frontier if (relationship, value) not in self._adjacency]
            # Current frontier owns its adjacency even if larger than the reuse budget.
            adjacency = {
                value: self._adjacency[(relationship, value)]
                for value in frontier
                if (relationship, value) in self._adjacency
            }
            if missing:
                table = self.relationships(missing, columns=[relationship])
                for value, neighbors in zip(missing, table[relationship].to_pylist(), strict=True):
                    adjacency[value] = tuple(neighbors)
                    cost = 192 + 36 * len(neighbors)
                    if cost <= 8 * 1024 * 1024:
                        while self._adjacency_bytes + cost > 8 * 1024 * 1024:
                            _, removed = self._adjacency.popitem(last=False)
                            self._adjacency_bytes -= 192 + 36 * len(removed)
                        self._adjacency[(relationship, value)] = tuple(neighbors)
                        self._adjacency_bytes += cost
            result.extend(frontier)
            following = set()
            for value in frontier:
                following.update(adjacency[value])
            frontier = sorted(following - seen)
            seen.update(frontier)
        if self._relationships is not None:
            self.source.check_artifact(self._relationships.data)
        return result
