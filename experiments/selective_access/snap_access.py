"""Snap-v2 bbox candidate access, without distance tests or snap ranking."""

from math import isfinite
from pathlib import Path
from time import perf_counter

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import shapely


def _predicate(
    schema: pa.Schema, bounds: tuple[float, float, float, float]
) -> ds.Expression:
    xmin, ymin, xmax, ymax = bounds
    if not all(isfinite(value) for value in bounds) or xmin > xmax or ymin > ymax:
        raise ValueError("Bounds must be finite and ordered")
    if "bbox" not in schema.names:
        return ds.scalar(True)
    # A valid snap-v2 non-null bbox has four non-null leaves. Testing xmin
    # also catches null parent structs and lets Arrow use leaf statistics.
    return ds.field(("bbox", "xmin")).is_null() | (
        (ds.field(("bbox", "xmin")) <= xmax)
        & (ds.field(("bbox", "ymin")) <= ymax)
        & (ds.field(("bbox", "xmax")) >= xmin)
        & (ds.field(("bbox", "ymax")) >= ymin)
    )


def _wkb_bytes(table: pa.Table) -> int:
    return int(
        pc.call_function(
            "sum", [pc.call_function("binary_length", [table["geometry"]])]
        ).as_py()
        or 0
    )


def snap_candidates(
    path: Path,
    bounds: tuple[float, float, float, float],
    explicit_groups: bool,
) -> tuple[pa.Table, dict[str, object]]:
    """Return bbox overlaps, decoding unknown bounds for fallback, preserving snap IDs.

    Candidate groups are Arrow's metadata-pruned groups, not observed I/O.
    Ordinary scan intermediate WKB materialization is not exposed by Arrow.
    Explicit mode counts all WKB in candidate groups before row filtering.
    Chunk sizes are compressed storage sizes, never network response bytes.
    Geometries are decoded only after filtering, then discarded. This measures
    actual decode count but does not implement distance/radius or ranking.
    """
    read_start = perf_counter()
    dataset = ds.dataset(path, format="parquet")
    predicate = _predicate(dataset.schema, bounds)
    fragment = next(dataset.get_fragments())
    candidates = list(fragment.split_by_row_group(filter=predicate))
    group_ids = [group.id for part in candidates for group in part.row_groups]
    parquet = pq.ParquetFile(path, pre_buffer=True)
    metadata = parquet.metadata
    candidate_rows = sum(metadata.row_group(i).num_rows for i in group_ids)
    chunk_bytes = sum(
        metadata.row_group(i).column(j).total_compressed_size
        for i in group_ids
        for j in range(metadata.row_group(i).num_columns)
    )
    materialized_wkb: int | str = "not measured"
    candidate_logical: int | str = "not measured"
    candidate_retained: int | str = "not measured"
    if explicit_groups:
        candidate_table = parquet.read_row_groups(group_ids, use_threads=False)
        materialized_wkb = _wkb_bytes(candidate_table)
        candidate_logical = candidate_table.nbytes
        candidate_retained = candidate_table.get_total_buffer_size()
        selected = candidate_table.filter(predicate)
    else:
        selected = dataset.to_table(
            filter=predicate,
            use_threads=False,
            batch_readahead=1,
            fragment_readahead=1,
            fragment_scan_options=ds.ParquetFragmentScanOptions(pre_buffer=True),
        )
    pre_fallback_rows = selected.num_rows
    pre_fallback_wkb = _wkb_bytes(selected)
    read_filter_seconds = perf_counter() - read_start
    decode_start = perf_counter()
    decoded_count = 0
    fallback_count = 0
    masks: list[pa.Array] = []
    xmin, ymin, xmax, ymax = bounds
    for batch in selected.to_batches():
        decoded = shapely.from_wkb(
            batch.column("geometry").to_numpy(zero_copy_only=False)
        )
        decoded_count += len(decoded)
        unknown = (
            batch.column("bbox").is_null().to_numpy(zero_copy_only=False)
            if "bbox" in batch.schema.names
            else np.ones(batch.num_rows, dtype=bool)
        )
        fallback_count += int(np.count_nonzero(unknown))
        keep = np.ones(batch.num_rows, dtype=bool)
        if np.any(unknown):
            actual_bounds = shapely.bounds(decoded[unknown])
            keep[unknown] = (
                (actual_bounds[:, 0] <= xmax)
                & (actual_bounds[:, 1] <= ymax)
                & (actual_bounds[:, 2] >= xmin)
                & (actual_bounds[:, 3] >= ymin)
            )
        masks.append(pa.array(keep))
        del decoded
    if masks:
        selected = selected.filter(pa.chunked_array(masks))
    decode_fallback_seconds = perf_counter() - decode_start
    selected_wkb = _wkb_bytes(selected)
    measurements: dict[str, object] = {
        "read_filter_seconds": read_filter_seconds,
        "decode_fallback_seconds": decode_fallback_seconds,
        "candidate_arrow_logical_bytes": candidate_logical,
        "candidate_arrow_retained_buffer_bytes": candidate_retained,
        "candidate_groups": len(group_ids),
        "candidate_group_ids": group_ids,
        "candidate_rows": candidate_rows,
        "selected_rows": selected.num_rows,
        "pre_fallback_selected_rows": pre_fallback_rows,
        "pre_fallback_wkb_bytes": pre_fallback_wkb,
        "fallback_decoded_geometries": fallback_count,
        "selected_wkb_bytes": selected_wkb,
        "materialized_wkb_bytes": materialized_wkb,
        "materialized_wkb_coverage": (
            "candidate groups before row filtering"
            if explicit_groups
            else "not measured: Arrow scan intermediate WKB is not exposed; selected_wkb_bytes is output only"
        ),
        "decoded_geometries": decoded_count,
        "candidate_column_chunk_compressed_bytes": chunk_bytes,
        "storage_bytes_coverage": "all column chunks in metadata-selected row groups; not observed reads",
        "wire_bytes": "not measured",
        "wire_bytes_coverage": "not measured",
        "selected_arrow_logical_bytes": selected.nbytes,
        "selected_arrow_retained_buffer_bytes": selected.get_total_buffer_size(),
    }
    return selected, measurements
