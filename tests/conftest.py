import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import Point, box

BBOX = pa.struct([pa.field(k, pa.float32(), nullable=False) for k in ("xmin", "ymin", "xmax", "ymax")])


def geo_metadata(types: list[str], has_bbox: bool = True) -> dict[bytes, bytes]:
    geometry: dict[str, object] = {"encoding": "WKB", "geometry_types": types}
    if has_bbox:
        geometry["covering"] = {"bbox": {k: ["bbox", k] for k in ("xmin", "ymin", "xmax", "ymax")}}
    return {
        b"geo": json.dumps(
            {"version": "1.1.0", "primary_column": "geometry", "columns": {"geometry": geometry}}
        ).encode()
    }


def write_table(
    path: Path, fields: list[pa.Field], values: dict[str, object], metadata: dict[bytes, bytes] | None = None
) -> None:
    schema = pa.schema(fields, metadata=metadata)
    pq.write_table(pa.table(values, schema=schema), path, row_group_size=2)


@pytest.fixture
def hfx(tmp_path: Path) -> Path:
    return make_hfx(tmp_path)


def make_hfx(root: Path) -> Path:
    root.mkdir(exist_ok=True)
    manifest = {
        "format_version": "0.4.0",
        "fabric_name": "synthetic",
        "crs": "EPSG:4326",
        "has_up_area": True,
        "topology": "dag",
        "bbox": [0, 0, 4, 1],
        "unit_count": 5,
        "created_at": "2026-01-01T00:00:00Z",
        "adapter_version": "test",
        "auxiliary": [
            {
                "schema": "hfx.aux.snap.v2",
                "artifacts": {"snap": "coarse.parquet"},
                "metadata": {
                    "name": "coarse",
                    "description": "nodes",
                    "references_levels": [0],
                    "weight_semantics": "within set",
                },
            },
            {
                "schema": "hfx.aux.snap.v2",
                "artifacts": {"snap": "fine.parquet"},
                "metadata": {
                    "name": "fine",
                    "description": "nodes",
                    "references_levels": [1],
                    "weight_semantics": "within set",
                },
            },
        ],
    }

    # Catchments and graph intentionally have different orders and different boundaries.
    fields = [
        pa.field("id", pa.int64(), False),
        pa.field("level", pa.int16(), False),
        pa.field("parent_id", pa.int64()),
        pa.field("area_km2", pa.float32(), False),
        pa.field("up_area_km2", pa.float32()),
        pa.field("outlet_lon", pa.float64(), False),
        pa.field("outlet_lat", pa.float64(), False),
        pa.field("bbox", BBOX, False),
        pa.field("geometry", pa.binary(), False),
        pa.field("source_id", pa.string()),
    ]
    boxes = [box(0, 0, 4, 1), box(2, 0, 3, 1), box(0, 0, 1, 1), box(3, 0, 4, 1), box(1, 0, 2, 1)]
    write_table(
        root / "catchments.parquet",
        fields,
        {
            "id": [9000000000, 30, 10, 40, 20],
            "level": [0, 1, 1, 1, 1],
            "parent_id": [None, 9000000000, 9000000000, 9000000000, 9000000000],
            "area_km2": [4, 1, 1, 1, 1],
            "up_area_km2": [None, 2, 1, None, 2],
            "outlet_lon": [4, 3, 1, 4, 2],
            "outlet_lat": [0.5] * 5,
            "bbox": [dict(zip(("xmin", "ymin", "xmax", "ymax"), g.bounds, strict=True)) for g in boxes],
            "geometry": [g.wkb for g in boxes],
            "source_id": [None, "thirty", "ten", "forty", "twenty"],
        },
        geo_metadata(["Polygon", "MultiPolygon"]),
    )
    fields = [
        pa.field("id", pa.int64(), False),
        pa.field("level", pa.int16(), False),
        pa.field("upstream_ids", pa.list_(pa.int64()), False),
    ]
    fields += [pa.field(n, pa.float32(), False) for n in ("bbox_minx", "bbox_miny", "bbox_maxx", "bbox_maxy")]
    write_table(
        root / "graph.parquet",
        fields,
        {
            "id": [40, 20, 9000000000, 10, 30],
            "level": [1, 1, 0, 1, 1],
            "upstream_ids": [[20, 30], [10], [], [], [10]],
            "bbox_minx": [3, 1, 0, 0, 2],
            "bbox_miny": [0] * 5,
            "bbox_maxx": [4, 2, 4, 1, 3],
            "bbox_maxy": [1] * 5,
        },
    )
    fields = [
        pa.field("id", pa.int64(), False),
        pa.field("unit_id", pa.int64(), False),
        pa.field("weight", pa.float32(), False),
        pa.field("stem_role", pa.string()),
        pa.field("bbox", BBOX),
        pa.field("geometry", pa.binary(), False),
    ]
    write_table(
        root / "fine.parquet",
        fields,
        {
            "id": [101, 102, 103, 104],
            "unit_id": [10, 10, 40, 20],
            "weight": [1, 2, 3, 4],
            "stem_role": [None, "mainstem", "unknown", "tributary"],
            "bbox": [
                {"xmin": 0.5, "ymin": 0.5, "xmax": 0.5, "ymax": 0.5},
                None,
                {"xmin": 3.5, "ymin": 0.5, "xmax": 3.5, "ymax": 0.5},
                None,
            ],
            "geometry": [Point(x, 0.5).wkb for x in (0.5, 0.6, 3.5, 1.5)],
        },
        geo_metadata(["Point", "LineString"]),
    )
    fields = [f for f in fields if f.name != "bbox"]
    write_table(
        root / "coarse.parquet",
        fields,
        {"id": [201], "unit_id": [9000000000], "weight": [1], "stem_role": [None], "geometry": [Point(0.5, 0.5).wkb]},
        geo_metadata(["Point", "LineString"], False),
    )
    catchments = pq.read_table(root / "catchments.parquet")
    (root / "catchments.parquet").unlink()
    with pa.ipc.new_file(
        root / "catchments.arrow", catchments.schema, options=pa.ipc.IpcWriteOptions(compression="zstd")
    ) as writer:
        for batch in catchments.to_batches(max_chunksize=2):
            writer.write_batch(batch)
    catchment_digest = hashlib.sha256((root / "catchments.arrow").read_bytes()).hexdigest()
    lookup_schema = pa.schema(
        [pa.field("id", pa.int64(), False), pa.field("block", pa.int32(), False), pa.field("row", pa.int32(), False)],
        metadata={b"hfx:format_version": b"0.4.0", b"hfx:catchments_sha256": catchment_digest.encode()},
    )
    # Explicit independent addresses for catchment order above, not graph positions.
    lookup = pa.table(
        {"id": [10, 20, 30, 40, 9000000000], "block": [1, 2, 0, 1, 0], "row": [0, 0, 1, 1, 0]}, schema=lookup_schema
    )
    with pa.ipc.new_file(root / "lookup.arrow", lookup_schema) as writer:
        writer.write_table(lookup)
    (root / "lookup-directory.json").write_text(
        json.dumps({"version": 1, "batches": [{"batch": 0, "min_id": 10, "max_id": 9000000000, "rows": 5}]})
    )
    (root / "source-manifest.json").write_text("{}")
    manifest.update(
        {
            "catchments": "catchments.arrow",
            "lookup": "lookup.arrow",
            "graph": "graph.parquet",
            "directory": "lookup-directory.json",
            "source_manifest": "source-manifest.json",
            "index_batch_rows": 65536,
            "catchment_batch_bytes": 8388608,
        }
    )
    manifest["artifacts"] = {p.name: fingerprint(p) for p in root.iterdir() if p.is_file()}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def fingerprint(path: Path) -> dict[str, int | str]:
    data = path.read_bytes()
    return {
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "head_sha256": hashlib.sha256(data[:65536]).hexdigest(),
        "tail_sha256": hashlib.sha256(data[-65536:]).hexdigest(),
    }


def refresh_fingerprint(root: Path, name: str) -> None:
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["artifacts"][name] = fingerprint(root / name)
    path.write_text(json.dumps(manifest))


def rewrite_catchments(root: Path, name: str, values: list[object]) -> None:
    path = root / "catchments.arrow"
    with pa.ipc.open_file(path) as reader:
        table = reader.read_all()
    field = table.schema.field(name)
    table = table.set_column(table.schema.get_field_index(name), field, pa.array(values, type=field.type))
    with pa.ipc.new_file(path, table.schema, options=pa.ipc.IpcWriteOptions(compression="zstd")) as writer:
        writer.write_table(table, max_chunksize=2)
    refresh_fingerprint(root, "catchments.arrow")
    lookup_path = root / "lookup.arrow"
    with pa.ipc.open_file(lookup_path) as reader:
        lookup = reader.read_all()
    metadata = dict(lookup.schema.metadata or {})
    metadata[b"hfx:catchments_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest().encode()
    lookup = lookup.replace_schema_metadata(metadata)
    with pa.ipc.new_file(lookup_path, lookup.schema) as writer:
        writer.write_table(lookup)
    refresh_fingerprint(root, "lookup.arrow")
