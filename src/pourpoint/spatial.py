"""Conservative row-group and row bounds filtering, before selected decoding."""

from __future__ import annotations

from typing import cast

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq

from pourpoint.metadata import Bounds, bounds


def conservative_bounds(value: Bounds) -> Bounds:
    """Expand by one float32 step to include rounded, degenerate HFX bounds."""
    checked = bounds(value)
    if any(abs(v) > np.finfo(np.float32).max for v in checked):
        raise ValueError("bbox exceeds float32 coordinate range")
    return cast(
        Bounds,
        tuple(
            float(np.nextafter(np.float32(v), np.float32(-np.inf if i < 2 else np.inf))) for i, v in enumerate(checked)
        ),
    )


def candidate_groups(
    file: pq.ParquetFile, envelope: Bounds, *, levels: tuple[int, ...] | None = None, graph: bool = False
) -> list[int]:
    if "bbox" not in file.schema_arrow.names and not graph:
        return list(range(file.num_row_groups))
    indices = {file.metadata.schema.column(i).path: i for i in range(file.metadata.num_columns)}
    result: list[int] = []
    for group_id in range(file.num_row_groups):
        group = file.metadata.row_group(group_id)
        if levels is not None and "level" in indices:
            level_stats = group.column(indices["level"]).statistics
            if (
                level_stats is not None
                and level_stats.has_min_max
                and not any(level_stats.min <= level <= level_stats.max for level in levels)
            ):
                continue
        paths = (
            ("bbox_minx", "bbox_miny", "bbox_maxx", "bbox_maxy")
            if graph
            else tuple(f"bbox.{k}" for k in ("xmin", "ymin", "xmax", "ymax"))
        )
        stats = [group.column(indices[path]).statistics for path in paths]
        # Null/unknown bounds cannot be pruned. In particular null parent structs
        # appear as null leaves in Parquet statistics.
        if any(
            stat is None or not stat.has_min_max or stat.null_count is None or stat.null_count > 0 for stat in stats
        ):
            result.append(group_id)
            continue
        if (
            stats[0].min <= envelope[2]
            and stats[1].min <= envelope[3]
            and stats[2].max >= envelope[0]
            and stats[3].max >= envelope[1]
        ):
            result.append(group_id)
    return result


def overlap(table: pa.Table, envelope: Bounds, *, graph: bool = False) -> npt.NDArray[np.bool_]:
    if graph:
        values = [table[k].to_numpy() for k in ("bbox_minx", "bbox_miny", "bbox_maxx", "bbox_maxy")]
        return (
            (values[0] <= envelope[2])
            & (values[1] <= envelope[3])
            & (values[2] >= envelope[0])
            & (values[3] >= envelope[1])
        )
    if "bbox" not in table.column_names:
        return np.ones(table.num_rows, dtype=np.bool_)
    array = table["bbox"].combine_chunks()
    unknown = array.is_null().to_numpy(zero_copy_only=False)
    values = [array.field(k).to_numpy(zero_copy_only=False) for k in ("xmin", "ymin", "xmax", "ymax")]
    return unknown | (
        (values[0] <= envelope[2])
        & (values[1] <= envelope[3])
        & (values[2] >= envelope[0])
        & (values[3] >= envelope[1])
    )
