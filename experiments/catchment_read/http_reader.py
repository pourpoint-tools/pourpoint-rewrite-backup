"""Experimental range-backed catchment reader with bounded fetch windows."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow as pa

from access import CatchmentReader
from http_access import RemoteFile, Transport, ipc_blocks, merge_ranges


class HttpCatchmentReader(CatchmentReader):
    """Keep metadata across calls, but discard fetched lookup and data windows."""

    def __init__(
        self,
        root: str,
        *,
        experimental: bool = False,
        transport: Transport | None = None,
    ) -> None:
        self.transport = transport if transport is not None else Transport(root)
        self.block_ranges: dict[str, list[tuple[int, int]]] = {}
        try:
            super().__init__(Path("."), experimental=experimental)
            if self.arrow is not None:
                self._ipc_ranges(self.manifest["catchments"])
        except Exception:
            self.close()
            raise

    def file(self, name: str) -> RemoteFile:
        return self.transport.file(name)

    def io_counts(self) -> dict[str, dict[str, int]]:
        return self.transport.io_counts()

    def close(self) -> None:
        self.transport.close()

    def _ipc_ranges(self, name: str) -> None:
        file = self.file(name)
        file.seek(-10, 2)
        trailer = file.read(10)
        length = int.from_bytes(trailer[:4], "little")
        if trailer[4:] != b"ARROW1" or length > file.size - 10:
            raise ValueError("invalid IPC trailer")
        file.seek(file.size - 10 - length)
        self.block_ranges[name] = ipc_blocks(file.read(length), file.size)

    def _parquet_ranges(self, *, ids_only: bool = False) -> list[tuple[int, int]]:
        assert self.parquet is not None
        metadata = self.parquet.metadata
        ranges: list[tuple[int, int]] = []
        for number in range(metadata.num_row_groups):
            group = metadata.row_group(number)
            chunks: list[tuple[int, int]] = []
            for column in range(group.num_columns):
                chunk = group.column(column)
                if ids_only and chunk.path_in_schema != "id":
                    continue
                start = chunk.data_page_offset
                if chunk.has_dictionary_page:
                    start = min(start, chunk.dictionary_page_offset)
                chunks.append((start, start + chunk.total_compressed_size))
            ranges.append((min(a for a, _ in chunks), max(b for _, b in chunks)))
        return ranges

    def _windows(
        self, name: str, numbers: list[int], ranges: list[tuple[int, int]]
    ) -> Iterator[list[int]]:
        file = self.file(name)
        merged = merge_ranges([ranges[n] for n in numbers], gap=65536, limit=8388608)
        ordered = sorted(numbers, key=lambda n: ranges[n])
        cursor = 0
        try:
            for offset in range(0, len(merged), 4):
                window = merged[offset : offset + 4]
                selected: list[int] = []
                while (
                    cursor < len(ordered)
                    and ranges[ordered[cursor]][1] <= window[-1][1]
                ):
                    selected.append(ordered[cursor])
                    cursor += 1
                file.prefetch([ranges[number] for number in selected])
                yield selected
                file.clear()
        finally:
            file.clear()

    def prepare(self) -> None:
        if self.kind != "0.3.0":
            super().prepare()
            self._ipc_ranges(self.manifest["lookup"])
            for file in self.transport.files.values():
                file.metadata.clear()
                file.sealed = True
            return
        assert self.parquet is not None
        name = "catchments.parquet"
        self.file(name).metadata.clear()
        self.file(name).sealed = True
        pieces: list[np.ndarray[tuple[int], np.dtype[np.int64]]] = []
        for window in self._windows(
            name,
            list(range(self.parquet.num_row_groups)),
            self._parquet_ranges(ids_only=True),
        ):
            for number in window:
                pieces.append(
                    self.parquet.read_row_group(
                        number, columns=["id"], use_threads=False
                    )["id"]
                    .combine_chunks()
                    .to_numpy()
                )
        ids = np.concatenate(pieces) if pieces else np.empty(0, dtype=np.int64)
        self.positions = np.argsort(ids).astype(np.int64)
        self.sorted_ids = ids[self.positions]
        self.ends = np.cumsum(
            [
                self.parquet.metadata.row_group(i).num_rows
                for i in range(self.parquet.num_row_groups)
            ],
            dtype=np.int64,
        )
        self.prepared = True

    def lookup_batches(
        self, numbers: list[int]
    ) -> Iterator[tuple[int, pa.RecordBatch]]:
        assert self.lookup is not None
        name = self.manifest["lookup"]
        for window in self._windows(name, numbers, self.block_ranges[name]):
            for number in window:
                yield number, self.lookup.get_batch(number)

    def data_tables(self, numbers: list[int]) -> Iterator[tuple[int, pa.Table]]:
        name = self.manifest.get("catchments", "catchments.parquet")
        ranges = (
            self.block_ranges[name]
            if self.arrow is not None
            else self._parquet_ranges()
        )
        for window in self._windows(name, numbers, ranges):
            yield from super().data_tables(window)
