"""Three local experimental catchment paths, not a production HFX reader."""

from __future__ import annotations

import hashlib
import io
import json
from collections import defaultdict
from collections.abc import Buffer, Iterator
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from http_access import RemoteFile

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import ipc


class CountedFile(io.FileIO):
    """Count bytes returned by unbuffered local read calls, not physical disk I/O."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, "rb")
        self.reads = 0
        self.returned_bytes = 0

    def read(self, size: int | None = -1) -> bytes:
        data = super().read(size)
        assert data is not None
        self.reads += 1
        self.returned_bytes += len(data)
        return data

    def readinto(self, buffer: Buffer) -> int:
        count = super().readinto(buffer)
        assert count is not None
        self.reads += 1
        self.returned_bytes += count
        return count


class CatchmentReader:
    """Retain metadata/locator only. Batch requests share reads; calls do not cache."""

    def __init__(self, root: Path, *, experimental: bool = False) -> None:
        self.root = root
        self.files: dict[str, CountedFile] = {}
        self.sorted_ids = np.empty(0, dtype=np.int64)
        self.positions = np.empty(0, dtype=np.int64)
        self.ends = np.empty(0, dtype=np.int64)
        self.prepared = False
        manifest = json.loads(self.file("manifest.json").read())
        self.manifest = manifest
        self.kind = "0.3.0"
        self.lookup: ipc.RecordBatchFileReader | None = None
        self.directory: list[dict[str, int]] = []
        self.parquet: pq.ParquetFile | None = None
        self.arrow: ipc.RecordBatchFileReader | None = None
        try:
            if manifest["format_version"] != "0.3.0":
                if not (
                    experimental
                    and manifest.get("experimental") is True
                    and manifest["format_version"] == "0.4.0"
                    and manifest.get("candidate") in ("a-index", "b-arrow")
                ):
                    raise ValueError("experimental candidate requires explicit opt-in")
                self.kind = manifest["candidate"]
            name = (
                manifest["catchments"] if self.kind != "0.3.0" else "catchments.parquet"
            )
            if self.kind == "b-arrow":
                self.arrow = ipc.open_file(
                    self.file(name), options=ipc.IpcReadOptions(use_threads=False)
                )
                self.schema = self.arrow.schema
            else:
                self.parquet = pq.ParquetFile(self.file(name), pre_buffer=False)
                self.schema = self.parquet.schema_arrow
        except Exception:
            self.close()
            raise

    def file(self, name: str) -> CountedFile | RemoteFile:
        if name not in self.files:
            self.files[name] = CountedFile(self.root / name)
        return self.files[name]

    def io_counts(self) -> dict[str, dict[str, int]]:
        return {
            name: {"reads": f.reads, "bytes": f.returned_bytes}
            for name, f in self.files.items()
        }

    def prepare(self) -> None:
        if self.kind == "0.3.0":
            assert self.parquet is not None
            ids = (
                self.parquet.read(columns=["id"], use_threads=False)["id"]
                .combine_chunks()
                .to_numpy()
            )
            self.positions = np.argsort(ids).astype(np.int64)
            self.sorted_ids = ids[self.positions]
            self.ends = np.cumsum(
                [
                    self.parquet.metadata.row_group(i).num_rows
                    for i in range(self.parquet.num_row_groups)
                ],
                dtype=np.int64,
            )
        else:
            for name in (
                self.manifest["catchments"],
                self.manifest["lookup"],
                self.manifest["directory"],
            ):
                expected = self.manifest["artifacts"][name]
                file = self.file(name)
                size = file.seek(0, 2)
                if size != expected["size_bytes"]:
                    raise ValueError(f"fingerprint size mismatch: {name}")
                width = min(65536, size)
                for offset, key in ((0, "head_sha256"), (size - width, "tail_sha256")):
                    file.seek(offset)
                    if hashlib.sha256(file.read(width)).hexdigest() != expected[key]:
                        raise ValueError(f"fingerprint mismatch: {name} {key}")
            file = self.file(self.manifest["directory"])
            file.seek(0)
            self.directory = json.loads(file.read())["batches"]
            self.lookup = ipc.open_file(
                self.file(self.manifest["lookup"]),
                options=ipc.IpcReadOptions(use_threads=False),
            )
            metadata = self.lookup.schema.metadata
            expected_digest = self.manifest["artifacts"][self.manifest["catchments"]][
                "sha256"
            ].encode()
            if (
                metadata[b"hfx:catchments_sha256"] != expected_digest
                or metadata[b"hfx:candidate"] != self.kind.encode()
            ):
                raise ValueError("lookup binding mismatch")
        self.prepared = True

    @property
    def retained_locator_bytes(self) -> int:
        return self.sorted_ids.nbytes + self.positions.nbytes + self.ends.nbytes

    def addresses(
        self, requested: npt.NDArray[np.int64]
    ) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64], list[int]]:
        if self.kind != "0.3.0":
            assert self.lookup is not None
            blocks = np.empty(len(requested), dtype=np.int64)
            rows = np.empty(len(requested), dtype=np.int64)
            maxima = [entry["max_id"] for entry in self.directory]
            batch_numbers = np.searchsorted(maxima, requested)
            read_batches: list[int] = []
            numbers = [int(n) for n in np.unique(batch_numbers)]
            if numbers and numbers[-1] == len(self.directory):
                raise KeyError("absent catchment ID")
            for batch_number, batch in self.lookup_batches(numbers):
                entry = self.directory[int(batch_number)]
                indices = np.flatnonzero(batch_numbers == batch_number)
                wanted = requested[indices]
                if np.any(wanted < entry["min_id"]):
                    raise KeyError("absent catchment ID")
                read_batches.append(int(batch_number))
                ids = batch["id"].to_numpy()
                found = np.searchsorted(ids, wanted)
                if np.any(found == len(ids)) or np.any(ids[found] != wanted):
                    raise KeyError("absent catchment ID")
                blocks[indices] = batch["block"].to_numpy()[found]
                rows[indices] = batch["row"].to_numpy()[found]
            return blocks, rows, read_batches
        positions = np.searchsorted(self.sorted_ids, requested)
        if np.any(positions == len(self.sorted_ids)) or np.any(
            self.sorted_ids[positions] != requested
        ):
            raise KeyError("absent catchment ID")
        physical = self.positions[positions]
        blocks = np.searchsorted(self.ends, physical, side="right")
        starts = np.concatenate(([0], self.ends[:-1]))
        return blocks, physical - starts[blocks], []

    def lookup_batches(
        self, numbers: list[int]
    ) -> Iterator[tuple[int, pa.RecordBatch]]:
        assert self.lookup is not None
        for number in numbers:
            yield number, self.lookup.get_batch(number)

    def data_tables(self, numbers: list[int]) -> Iterator[tuple[int, pa.Table]]:
        for number in numbers:
            if self.arrow is not None:
                yield number, pa.Table.from_batches([self.arrow.get_batch(number)])
            else:
                assert self.parquet is not None
                yield number, self.parquet.read_row_group(number, use_threads=False)

    def retrieve(
        self, requests: list[list[int]]
    ) -> tuple[list[pa.Table], dict[str, object]]:
        if not self.prepared:
            raise RuntimeError("prepare must be measured before retrieval")
        unique = np.unique(
            np.asarray([i for request in requests for i in request], dtype=np.int64)
        )
        blocks, rows, lookup_batches = self.addresses(unique)
        grouped: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for index, (block, row) in enumerate(zip(blocks, rows, strict=True)):
            grouped[int(block)].append((index, int(row)))
        pieces: list[pa.Table] = []
        unique_order: list[int] = []
        materialized = 0
        max_backing = 0
        for block, table in self.data_tables(sorted(grouped)):
            addresses = grouped[block]
            materialized += table.nbytes
            max_backing = max(max_backing, table.get_total_buffer_size())
            selected = table.take([row for _, row in addresses])
            pieces.append(selected)
            unique_order.extend(index for index, _ in addresses)
            del table
        selected = (
            pa.concat_tables(pieces)
            if pieces
            else pa.Table.from_batches([], schema=self.schema)
        )
        if unique_order:
            selected = selected.take(np.argsort(unique_order))
        if selected["id"].to_pylist() != unique.tolist():
            raise ValueError("lookup physical address does not match requested ID")
        outputs = [
            selected.take(np.searchsorted(unique, request).astype(np.int64))
            for request in requests
        ]
        return outputs, {
            "lookup_batches": lookup_batches,
            "data_blocks": sorted(grouped),
            "materialized_logical_bytes": materialized,
            "largest_materialized_block_backing_bytes": max_backing,
            "selected_logical_bytes": sum(t.nbytes for t in outputs),
            "selected_backing_bytes": sum(t.get_total_buffer_size() for t in outputs),
        }

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        for file in self.files.values():
            file.close()
