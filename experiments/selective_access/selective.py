"""Local selective vector access measurements; no production engine behavior."""

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import shapely
from shapely.geometry.base import BaseGeometry


def buffers(table: pa.Table) -> dict[str, int]:
    return {
        "logical_bytes": table.nbytes,
        "retained_backing_bytes": table.get_total_buffer_size(),
    }


@dataclass
class Locator:
    attributes: pa.Table
    sorted_ids: npt.NDArray[np.int64]
    row_positions: npt.NDArray[np.int64]
    group_ends: npt.NDArray[np.int64]
    attribute_load_seconds: float
    locator_build_seconds: float

    @classmethod
    def load(cls, path: Path) -> "Locator":
        start = perf_counter()
        file = pq.ParquetFile(path, pre_buffer=True)
        columns = ["id", "level", "parent_id", "up_area_km2"]
        attributes = file.read(columns=columns, use_threads=False)
        attribute_load_seconds = perf_counter() - start
        start = perf_counter()
        ids = attributes["id"].combine_chunks().to_numpy()
        positions = np.argsort(ids).astype(np.int64)
        sorted_ids = ids[positions]
        if len(sorted_ids) and (sorted_ids[0] <= 0 or np.any(np.diff(sorted_ids) == 0)):
            raise ValueError("catchment IDs must be unique positive int64")
        ends = np.cumsum(
            [file.metadata.row_group(i).num_rows for i in range(file.num_row_groups)],
            dtype=np.int64,
        )
        return cls(
            attributes,
            sorted_ids,
            positions,
            ends,
            attribute_load_seconds,
            perf_counter() - start,
        )

    def groups(self, ids: list[int]) -> list[int]:
        requested = np.asarray(ids, dtype=np.int64)
        positions = np.searchsorted(self.sorted_ids, requested)
        if np.any(positions == len(self.sorted_ids)):
            raise KeyError("missing catchment ID")
        if np.any(self.sorted_ids[positions] != requested):
            raise KeyError("missing catchment ID")
        return np.unique(
            np.searchsorted(
                self.group_ends, self.row_positions[positions], side="right"
            )
        ).tolist()

    def memory(self) -> dict[str, object]:
        return {
            "parent_level_attributes": buffers(self.attributes.drop(["id"])),
            "catchment_id_column": buffers(self.attributes.select(["id"])),
            "locator_numpy_owned_bytes": self.sorted_ids.nbytes
            + self.row_positions.nbytes
            + self.group_ends.nbytes,
            "locator_numpy_arrays": {
                "sorted_ids": self.sorted_ids.nbytes,
                "row_positions": self.row_positions.nbytes,
                "group_ends": self.group_ends.nbytes,
            },
            "locator_temporary_peak_bytes": "not measured",
            "graph": "excluded",
            "graph_lookup": "excluded",
        }


def storage_bytes(file: pq.ParquetFile, groups: list[int], columns: list[str]) -> int:
    return sum(
        file.metadata.row_group(g).column(c).total_compressed_size
        for g in groups
        for c in range(file.metadata.num_columns)
        if file.metadata.schema.column(c).path.split(".")[0] in columns
    )


def wkb_bytes(table: pa.Table) -> int:
    return int(
        pc.call_function(
            "sum", [pc.call_function("binary_length", [table["geometry"]])]
        ).as_py()
        or 0
    )


def polygons(
    path: Path,
    locator: Locator,
    ids: list[int],
    explicit_groups: bool,
    *,
    retain_decoded: list[BaseGeometry] | None = None,
) -> tuple[pa.Table, dict[str, object]]:
    start = perf_counter()
    groups = locator.groups(ids)  # Missing IDs fail before either reader.
    file = pq.ParquetFile(path, pre_buffer=True)
    expression = ds.field("id").isin(pa.array(ids, type=pa.int64()))
    columns = ["id", "geometry"]
    materialized: int | str = "not measured"
    if explicit_groups:
        candidate = file.read_row_groups(groups, columns=columns, use_threads=False)
        materialized = wkb_bytes(candidate)
        table = candidate.filter(
            pc.call_function(
                "is_in",
                [candidate["id"]],
                pc.SetLookupOptions(value_set=pa.array(ids, type=pa.int64())),
            )
        )
        candidate_buffers: dict[str, int] | str = buffers(candidate)
    else:
        dataset = ds.dataset(path, format="parquet")
        fragments = list(dataset.get_fragments())
        groups = [
            g.id
            for frag in fragments
            for part in frag.split_by_row_group(filter=expression)
            for g in part.row_groups
        ]
        table = dataset.to_table(
            columns=columns,
            filter=expression,
            use_threads=False,
            batch_readahead=1,
            fragment_readahead=1,
            fragment_scan_options=ds.ParquetFragmentScanOptions(pre_buffer=True),
        )
        candidate_buffers = "not measured"
    read_seconds = perf_counter() - start
    actual = np.sort(table["id"].combine_chunks().to_numpy())
    if not np.array_equal(actual, np.unique(np.asarray(ids, dtype=np.int64))):
        raise AssertionError("selected catchment IDs differ")
    decode_start = perf_counter()
    decoded = 0
    for value in table["geometry"]:
        geometry = shapely.from_wkb(value.as_py())
        if retain_decoded is not None:
            retain_decoded.append(geometry)
        decoded += 1
    return table, {
        "read_filter_seconds": read_seconds,
        "decode_seconds": perf_counter() - decode_start,
        "candidate_group_ids": groups,
        "candidate_groups": len(groups),
        "candidate_rows": sum(file.metadata.row_group(g).num_rows for g in groups),
        "selected_rows": table.num_rows,
        "exact_selected_ids_check": "passed",
        "materialized_prefilter_wkb_bytes": materialized,
        "selected_wkb_bytes": wkb_bytes(table),
        "decoded_geometry_count": decoded,
        "candidate_buffers": candidate_buffers,
        "query_buffers": buffers(table),
        "candidate_column_chunks_compressed_bytes": storage_bytes(
            file, groups, columns
        ),
        "remote_requests": "not measured",
        "remote_response_bytes": "not measured",
    }
