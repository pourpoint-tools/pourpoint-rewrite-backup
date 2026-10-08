"""Selective access through the published HFX numeric lookup.

Opening reads schema/footer metadata and the small directory, not numeric batches.
The source owns open handles. Results own compact selected Arrow buffers.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO, Protocol

import numpy as np
import pyarrow as pa

from pourpoint._selection import compact_dictionaries
from pourpoint._validation import CATCHMENT_FIELDS, schema_check
from pourpoint.metadata import Fingerprint, Manifest


class ArtifactSource(Protocol):
    def open_artifact(self, path: str, fingerprint: Fingerprint, *, bounded: bool = True) -> BinaryIO: ...

    def read_json(self, path: str, fingerprint: Fingerprint | None = None) -> dict[str, object]: ...


@dataclass(frozen=True)
class Access:
    lookup_batches: tuple[int, ...] = ()
    catchment_batches: tuple[int, ...] = ()


@dataclass(frozen=True)
class LookupRange:
    min_id: int
    max_id: int
    rows: int


def _integer(value: object, name: str, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name}: expected integer in [{minimum}, {maximum}]")
    return value


class CatchmentReader:
    def __init__(self, source: ArtifactSource, manifest: Manifest) -> None:
        self._catchments = pa.ipc.open_file(
            source.open_artifact(manifest.catchments, manifest.artifacts[manifest.catchments], bounded=True)
        )
        self._lookup = pa.ipc.open_file(
            source.open_artifact(manifest.lookup, manifest.artifacts[manifest.lookup], bounded=True)
        )
        self.schema: pa.Schema = self._catchments.schema
        schema_check(self.schema, CATCHMENT_FIELDS, "catchments")
        if len(set(self.schema.names)) != len(self.schema.names):
            raise ValueError("catchments: duplicate field names")
        expected = pa.schema(
            [
                pa.field("id", pa.int64(), False),
                pa.field("block", pa.int32(), False),
                pa.field("row", pa.int32(), False),
            ],
            metadata={
                b"hfx:format_version": b"0.4.0",
                b"hfx:catchments_sha256": manifest.artifacts[manifest.catchments].sha256.encode("ascii"),
            },
        )
        if not self._lookup.schema.equals(expected, check_metadata=True):
            raise ValueError("lookup: schema or catchment digest binding mismatch")
        directory = source.read_json(manifest.directory, manifest.artifacts[manifest.directory])
        entries = directory.get("batches")
        if type(directory.get("version")) is not int or directory["version"] != 1 or not isinstance(entries, list):
            raise ValueError("lookup directory: invalid version or batches")
        if len(entries) != self._lookup.num_record_batches or len(entries) != (manifest.unit_count + 65535) // 65536:
            raise ValueError("lookup directory: batch count mismatch")
        ranges: list[LookupRange] = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict) or _integer(entry.get("batch"), "directory batch") != i:
                raise ValueError("lookup directory: invalid batch order")
            low = _integer(entry.get("min_id"), "directory min_id", 1)
            high = _integer(entry.get("max_id"), "directory max_id", low)
            rows = _integer(entry.get("rows"), "directory rows", 1, 65536)
            if (
                rows != min(65536, manifest.unit_count - i * 65536)
                or (ranges and low <= ranges[-1].max_id)
                or high - low + 1 < rows
            ):
                raise ValueError("lookup directory: invalid rows or ranges")
            ranges.append(LookupRange(low, high, rows))
        self._ranges = tuple(ranges)
        self._maxima = tuple(r.max_id for r in ranges)
        self.last_access = Access()

    def read(self, ids: Iterable[int]) -> pa.Table:
        requested = [
            _integer(int(value) if isinstance(value, np.integer) else value, "requested ID", 1) for value in ids
        ]
        self.last_access = Access()
        if not requested:
            return pa.Table.from_batches([], schema=self.schema)
        groups: dict[int, list[tuple[int, int]]] = {}
        for position, unit_id in enumerate(requested):
            batch = bisect_left(self._maxima, unit_id)
            if batch == len(self._ranges) or unit_id < self._ranges[batch].min_id:
                raise KeyError(f"unknown catchment ID {unit_id}")
            groups.setdefault(batch, []).append((position, unit_id))
        addresses: dict[int, list[tuple[int, int, int]]] = {}
        for batch_number, queries in sorted(groups.items()):
            batch = self._lookup.get_batch(batch_number)
            declared = self._ranges[batch_number]
            if batch.num_rows != declared.rows or any(column.null_count for column in batch.columns):
                raise ValueError("lookup: invalid row count or null values")
            keys = batch.column(0).to_numpy()
            if keys[0] != declared.min_id or keys[-1] != declared.max_id or np.any(keys[1:] <= keys[:-1]):
                raise ValueError("lookup: IDs disagree with directory or are not strictly sorted")
            for position, unit_id in queries:
                index = int(np.searchsorted(keys, unit_id))
                if index == len(keys) or keys[index] != unit_id:
                    raise KeyError(f"unknown catchment ID {unit_id}")
                block = int(batch.column(1)[index].as_py())
                row = int(batch.column(2)[index].as_py())
                if not 0 <= block < self._catchments.num_record_batches or row < 0:
                    raise ValueError("lookup: catchment address out of bounds")
                addresses.setdefault(block, []).append((position, unit_id, row))
        pieces: list[pa.RecordBatch] = []
        positions: list[int] = []
        for block, queries in sorted(addresses.items()):
            batch = self._catchments.get_batch(block)
            rows = [row for _, _, row in queries]
            if max(rows) >= batch.num_rows:
                raise ValueError("lookup: catchment row out of bounds")
            selected = batch.take(pa.array(rows, type=pa.int64()))
            if selected.column(self.schema.get_field_index("id")).to_pylist() != [unit_id for _, unit_id, _ in queries]:
                raise ValueError("lookup: addressed catchment ID mismatch")
            pieces.append(selected)
            positions.extend(position for position, _, _ in queries)
        # Arrow take copies selected values, so results do not retain discarded WKB.
        table = pa.Table.from_batches(pieces, schema=self.schema)
        result = table.take(pa.array(np.argsort(positions), type=pa.int64()))
        self.last_access = Access(tuple(sorted(groups)), tuple(sorted(addresses)))
        return compact_dictionaries(result)
