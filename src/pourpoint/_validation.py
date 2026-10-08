"""Checks for loaded HFX records; not a full geometric conformance validator."""

import json

import numpy as np
import pyarrow as pa

BBOX_TYPE = pa.struct([pa.field(k, pa.float32(), nullable=False) for k in ("xmin", "ymin", "xmax", "ymax")])
CATCHMENT_FIELDS = {
    "id": (pa.int64(), False),
    "level": (pa.int16(), False),
    "parent_id": (pa.int64(), True),
    "area_km2": (pa.float32(), False),
    "up_area_km2": (pa.float32(), True),
    "outlet_lon": (pa.float64(), False),
    "outlet_lat": (pa.float64(), False),
    "bbox": (BBOX_TYPE, False),
    "geometry": (pa.binary(), False),
}
GRAPH_FIELDS = {
    "id": (pa.int64(), False),
    "level": (pa.int16(), False),
    "upstream_ids": (pa.list_(pa.int64()), False),
    **{k: (pa.float32(), False) for k in ("bbox_minx", "bbox_miny", "bbox_maxx", "bbox_maxy")},
}
SNAP_FIELDS = {
    "id": (pa.int64(), False),
    "unit_id": (pa.int64(), False),
    "weight": (pa.float32(), False),
    "geometry": (pa.binary(), False),
}


def schema_check(schema: pa.Schema, fields: dict[str, tuple[pa.DataType, bool]], artifact: str) -> None:
    for name, (dtype, nullable) in fields.items():
        if name not in schema.names:
            raise ValueError(f"{artifact}: missing {name}")
        field = schema.field(name)
        if field.type != dtype or field.nullable != nullable:
            raise ValueError(f"{artifact}: {name} expected {dtype}, nullable={nullable}; got {field}")


def geo_check(schema: pa.Schema, artifact: str) -> None:
    try:
        geo = json.loads((schema.metadata or {})[b"geo"])
        geometry = geo["columns"]["geometry"]
        if geo["version"] != "1.1.0" or geo["primary_column"] != "geometry" or geometry["encoding"] != "WKB":
            raise ValueError("expected GeoParquet 1.1 WKB geometry")
        if "bbox" in schema.names and geometry["covering"]["bbox"] != {
            k: ["bbox", k] for k in ("xmin", "ymin", "xmax", "ymax")
        }:
            raise ValueError("invalid bbox covering")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{artifact}: invalid geo metadata / bbox covering") from exc


def nonnull(table: pa.Table, names: list[str], artifact: str) -> None:
    for name in names:
        if table[name].null_count:
            raise ValueError(f"{artifact}: null {name}")


def bbox_check(table: pa.Table, artifact: str, *, graph: bool = False) -> None:
    if graph:
        cols = [table[n].combine_chunks() for n in ("bbox_minx", "bbox_miny", "bbox_maxx", "bbox_maxy")]
        valid = np.ones(table.num_rows, dtype=np.bool_)
    else:
        array = table["bbox"].combine_chunks()
        valid = array.is_valid().to_numpy(zero_copy_only=False)
        cols = [array.field(k) for k in ("xmin", "ymin", "xmax", "ymax")]
    values = [c.to_numpy(zero_copy_only=False) for c in cols]
    for c, value in zip(cols, values, strict=True):
        if np.any(c.is_null().to_numpy(zero_copy_only=False) & valid) or np.any(~np.isfinite(value[valid])):
            raise ValueError(f"{artifact}: bbox contains null or nonfinite leaf")
    if np.any((values[0] > values[2]) & valid) or np.any((values[1] > values[3]) & valid):
        raise ValueError(f"{artifact}: bbox is unordered")


def scalar_check(table: pa.Table, artifact: str) -> None:
    nonnull(table, ["id", "level", "area_km2", "outlet_lon", "outlet_lat", "bbox"], artifact)
    bbox_check(table, artifact)
    for name, limit in (("outlet_lon", 180), ("outlet_lat", 90)):
        values = table[name].to_numpy()
        if np.any(~np.isfinite(values)) or np.any(np.abs(values) > limit):
            raise ValueError(f"{artifact}: invalid {name}")
    for name in ("area_km2", "up_area_km2"):
        array = table[name].combine_chunks()
        values = array.drop_null().to_numpy()
        if np.any(~np.isfinite(values)) or np.any(values < 0):
            raise ValueError(f"{artifact}: invalid {name}")
