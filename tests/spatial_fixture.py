"""Small hand-authored spatial publication, independent of the HFX producer."""

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import LineString, Point, box


def make_spatial_bundle(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format_version": "0.4.0",
        "representation": "spatial-geoparquet-adjacency",
        "representation_version": 1,
        "fabric_name": "example",
        "crs": "EPSG:4326",
        "bbox": [0, 0, 20, 20],
        "families": {},
        "relationships": {},
        "artifacts": {},
    }
    for domain in ("catchments", "rivers", "river_nodes"):
        schema = pa.schema(
            [
                pa.field("id", pa.int64(), False),
                pa.field("level", pa.int32(), False),
                pa.field("geometry", pa.binary(), False),
                pa.field("bbox", pa.struct([(k, pa.float32()) for k in ("xmin", "ymin", "xmax", "ymax")])),
                pa.field("source_value", pa.int64(), metadata={b"unit": b"native"}),
            ],
            metadata={b"provenance": b"independent fixture"},
        )
        ids = [30, 10, 40, 20]
        table = pa.Table.from_pylist(
            [
                {
                    "id": i,
                    "level": 1 if i < 30 else 2,
                    "geometry": (
                        box(i / 10, 1, i / 10 + 0.25, 1.25)
                        if domain == "catchments"
                        else LineString([(i / 10, 1), (i / 10 + 0.25, 1.25)])
                        if domain == "rivers"
                        else Point(i / 10, 1)
                    ).wkb,
                    "bbox": {
                        "xmin": i / 10,
                        "ymin": 1,
                        "xmax": i / 10 + (0 if domain == "river_nodes" else 0.25),
                        "ymax": 1 + (0 if domain == "river_nodes" else 0.25),
                    },
                    "source_value": None if i == 10 else i * 100,
                }
                for i in ids
            ],
            schema=schema,
        )
        data = f"{domain}.parquet"
        pq.write_table(table, path / data, row_group_size=2)
        digest = hashlib.sha256((path / data).read_bytes()).hexdigest()
        lookup = f"{domain}-lookup.arrow"
        directory = f"{domain}-lookup.json"
        family = {"data": data, "data_sha256": digest, "count": 4, "lookup": lookup, "directory": directory}
        manifest["families"][domain] = family
        lookup_schema = pa.schema(
            [
                pa.field("id", pa.int64(), False),
                pa.field("block", pa.int32(), False),
                pa.field("row", pa.int32(), False),
            ],
            metadata={b"hfx:data_sha256": digest.encode()},
        )
        addresses = [{"id": i, "block": ids.index(i) // 2, "row": ids.index(i) % 2} for i in sorted(ids)]
        write_ipc(path / lookup, pa.Table.from_pylist(addresses, schema=lookup_schema))
        ranges = {
            "version": 1,
            "batches": [
                {"batch": 0, "min_id": 10, "max_id": 20, "rows": 2},
                {"batch": 1, "min_id": 30, "max_id": 40, "rows": 2},
            ],
        }
        (path / directory).write_text(json.dumps(ranges))
        rel = f"{domain}-relationships.arrow"
        rel_dir = f"{domain}-relationships.json"
        rel_schema = pa.schema(
            [
                pa.field("id", pa.int64(), False),
                pa.field("upstream_ids", pa.list_(pa.int64()), False),
                pa.field("downstream_ids", pa.list_(pa.int64()), False),
                pa.field("parent_ids", pa.list_(pa.int64()), False),
            ]
        )
        write_ipc(
            path / rel,
            pa.Table.from_pylist(
                [
                    {"id": 10, "upstream_ids": [], "downstream_ids": [20, 30], "parent_ids": [40]},
                    {"id": 20, "upstream_ids": [10], "downstream_ids": [40], "parent_ids": []},
                    {"id": 30, "upstream_ids": [10], "downstream_ids": [40], "parent_ids": []},
                    {"id": 40, "upstream_ids": [20, 30], "downstream_ids": [], "parent_ids": []},
                ],
                schema=rel_schema,
            ),
        )
        (path / rel_dir).write_text(json.dumps(ranges))
        manifest["relationships"][domain] = {
            "data": rel,
            "directory": rel_dir,
            "count": 4,
            "columns": {k: {"source_domain": domain, "target_domain": domain} for k in rel_schema.names[1:]},
            "feature_artifacts": {domain: {"data": data, "data_sha256": digest}},
        }
    incidence = {
        "rivers": (
            "river_nodes",
            {"upstream_node_ids": [[10], [20], [20], [30]], "downstream_node_ids": [[20], [30], [30], [40]]},
        ),
        "river_nodes": (
            "rivers",
            {"incoming_river_ids": [[], [10], [20, 30], [40]], "outgoing_river_ids": [[10], [20, 30], [40], []]},
        ),
    }
    for domain, (target, columns) in incidence.items():
        declaration = manifest["relationships"][domain]
        artifact = path / declaration["data"]
        with pa.memory_map(str(artifact), "r") as stream:
            table = pa.ipc.open_file(stream).read_all()
        for name, values in columns.items():
            table = table.append_column(
                pa.field(name, pa.list_(pa.int64()), False), pa.array(values, type=pa.list_(pa.int64()))
            )
            declaration["columns"][name] = {"source_domain": "rivers", "target_domain": target}
        write_ipc(artifact, table)
        family = manifest["families"][target]
        declaration["feature_artifacts"][target] = {"data": family["data"], "data_sha256": family["data_sha256"]}
    for artifact in path.iterdir():
        raw = artifact.read_bytes()
        manifest["artifacts"][artifact.name] = {
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "head_sha256": hashlib.sha256(raw[:65536]).hexdigest(),
            "tail_sha256": hashlib.sha256(raw[-65536:]).hexdigest(),
        }
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path


def write_ipc(path: Path, table: pa.Table) -> None:
    with pa.OSFile(str(path), "wb") as stream, pa.ipc.new_file(stream, table.schema) as writer:
        writer.write_table(table, max_chunksize=2)
