"""Portable QGIS projects with native-grid rasters and lossless source tables."""

from __future__ import annotations

import json
import sqlite3
import struct
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self
from uuid import uuid4

import numpy as np
import pyarrow as pa
import rasterio
from rasterio.crs import CRS  # ty: ignore[unresolved-import]
from shapely.geometry.base import BaseGeometry

from pourpoint.features import Feature, RiverNetwork
from pourpoint.refinement import FootprintRefinement, GranularRefinement, RasterSubset, SubbasinContribution
from pourpoint.results import BatchIssue, BatchResult, DelineationResult

type ExportResult = DelineationResult | GranularRefinement | FootprintRefinement
type VectorFeature = Feature | SubbasinContribution


@dataclass
class _Vector:
    name: str
    geometries: list[BaseGeometry]
    sources: list[VectorFeature]
    crs: CRS
    group: str | None = None
    target_index: int | None = None
    outline: bool = False


def _text(parent: ET.Element, name: str, value: str) -> ET.Element:
    element = ET.SubElement(parent, name)
    element.text = value
    return element


def _crs(parent: ET.Element, crs: CRS) -> None:
    spatial = ET.SubElement(ET.SubElement(parent, "srs"), "spatialrefsys")
    _text(spatial, "wkt", crs.to_wkt())
    _text(spatial, "authid", crs.to_string())
    _text(spatial, "description", crs.to_string())
    _text(spatial, "projectionacronym", "longlat" if crs.is_geographic else "")
    _text(spatial, "ellipsoidacronym", "EPSG:7030")
    _text(spatial, "geographicflag", "true" if crs.is_geographic else "false")


def _arrow(db: sqlite3.Connection, layer: str, kind: str, table: pa.Table) -> None:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    db.execute("INSERT INTO source_arrow VALUES (?, ?, ?)", (layer, kind, sink.getvalue().to_pybytes()))


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _scalar_type(dtype: pa.DataType) -> str | None:
    if pa.types.is_boolean(dtype) or (pa.types.is_integer(dtype) and not pa.types.is_uint64(dtype)):
        return "INTEGER"
    if pa.types.is_floating(dtype):
        return "REAL"
    if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        return "TEXT"
    if pa.types.is_binary(dtype) or pa.types.is_large_binary(dtype):
        return "BLOB"
    return None


def _initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
        PRAGMA application_id = 1196444487;
        PRAGMA user_version = 10300;
        CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY,
          organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL, description TEXT);
        CREATE TABLE gpkg_contents (table_name TEXT PRIMARY KEY, data_type TEXT NOT NULL,
          identifier TEXT UNIQUE, description TEXT DEFAULT '', last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER);
        CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL, column_name TEXT NOT NULL,
          geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
          PRIMARY KEY (table_name, column_name));
        CREATE TABLE source_arrow (layer_name TEXT NOT NULL, kind TEXT NOT NULL, payload BLOB NOT NULL);
        CREATE TABLE source_metadata (layer_name TEXT NOT NULL, metadata TEXT NOT NULL);
        CREATE TABLE target_issues (target_index INTEGER, target_name TEXT, code TEXT, reason TEXT);
        CREATE TABLE result_metadata (target_name TEXT, target_index INTEGER, metadata TEXT, original_target BLOB, adjusted_target BLOB);
        INSERT INTO gpkg_contents (table_name, data_type, identifier) VALUES ('result_metadata','attributes','result_metadata');
        CREATE TABLE river_connections (layer_name TEXT, source_id INTEGER, relation TEXT, target_id INTEGER);
        INSERT INTO gpkg_contents (table_name, data_type, identifier) VALUES ('river_connections','attributes','river_connections');
        INSERT INTO gpkg_spatial_ref_sys VALUES ('Undefined Cartesian', -1, 'NONE', -1, 'undefined', NULL);
        INSERT INTO gpkg_spatial_ref_sys VALUES ('Undefined geographic', 0, 'NONE', 0, 'undefined', NULL);
        INSERT INTO gpkg_contents (table_name, data_type, identifier) VALUES ('source_arrow','attributes','source_arrow');
        INSERT INTO gpkg_contents (table_name, data_type, identifier) VALUES ('source_metadata','attributes','source_metadata');
        INSERT INTO gpkg_contents (table_name, data_type, identifier) VALUES ('target_issues','attributes','target_issues');
    """)


class QGISBundle:
    """Incremental export; write() creates a new folder and never replaces a path.

    Scalar source fields become ordinary vector columns. Reserved-name collisions
    gain ``source_`` prefixes; ``source_metadata.attribute_columns`` records names.
    Rich values, original WKB and Arrow schemas/metadata are losslessly retained
    as IPC streams in ``source_arrow``. Result locations are WGS84 WKB in
    ``result_metadata``, alongside provenance; these are not extra map layers.
    """

    def __init__(self) -> None:
        self._vectors: list[_Vector] = []
        self._networks: list[tuple[int, RiverNetwork]] = []
        self._rasters: list[tuple[str, RasterSubset, str | None]] = []
        self._groups: list[str] = []
        self._issues: list[BatchIssue] = []
        self._results: list[tuple[str | None, int | None, DelineationResult]] = []

    def add_vector(
        self,
        name: str,
        data: BaseGeometry | VectorFeature | Sequence[VectorFeature] | RiverNetwork,
        *,
        crs: str | CRS | None = None,
    ) -> Self:
        sources: list[VectorFeature]
        if isinstance(data, BaseGeometry):
            if crs is None:
                raise ValueError("raw vector geometry requires an explicit CRS")
            geometries, sources = [data], []
        else:
            if isinstance(data, RiverNetwork):
                self._networks.append((len(self._vectors), data))
                sources = list(data.rivers)
            elif isinstance(data, (Feature, SubbasinContribution)):
                sources = [data]
            else:
                sources = list(data)
            geometries = [feature.geometry for feature in sources]
        self._vectors.append(_Vector(name, geometries, sources, CRS.from_user_input(crs or "EPSG:4326")))
        return self

    def add_raster(self, name: str, data: RasterSubset) -> Self:
        self._rasters.append((name, data, None))
        return self

    def add_result(self, result: ExportResult | BatchResult[ExportResult]) -> Self:
        if isinstance(result, BatchResult):
            self._issues.extend(result.issues)
            for target, value in zip(result.targets, result.results, strict=True):
                self._groups.append(target.name)
                if value is not None:
                    self._result(value, target.name, target.index)
        else:
            self._result(result, None, None)
        return self

    def _result(self, result: ExportResult, group: str | None, index: int | None) -> None:
        if isinstance(result, DelineationResult):
            self._results.append((group, index, result))
        start = len(self._vectors)
        if isinstance(result, FootprintRefinement):
            self.add_vector("watershed", result.footprint, crs="EPSG:4326")
        else:
            if result.subbasins:
                self.add_vector("subbasins", result.subbasins)
            if isinstance(result, DelineationResult) and result.geometry is not None:
                self.add_vector("watershed", result.geometry, crs="EPSG:4326")
        if result.outlet_cell is not None:
            self.add_vector("outlet_cell", result.outlet_cell.geometry, crs=result.outlet_cell.grid.crs)
            self._vectors[-1].outline = True
        for vector in self._vectors[start:]:
            vector.group, vector.target_index = group, index
        if result.rasters is not None:
            self._rasters.extend(
                (
                    ("flow_direction", result.rasters.direction, group),
                    ("flow_accumulation", result.rasters.accumulation, group),
                )
            )

    def write(self, path: str | Path) -> Path:
        folder = Path(path)
        folder.mkdir(parents=True, exist_ok=False)
        project = ET.Element("qgis", version="3.40.0", projectname="Pourpoint")
        _text(project, "title", "Pourpoint")
        tree = ET.SubElement(project, "layer-tree-group", name="", checked="Qt::Checked", expanded="1")
        layers = ET.SubElement(project, "projectlayers")
        groups = {
            name: ET.SubElement(tree, "layer-tree-group", name=name, checked="Qt::Checked", expanded="1")
            for name in self._groups
        }
        with sqlite3.connect(folder / "vectors.gpkg") as db:
            _initialize(db)
            db.executemany(
                "INSERT INTO result_metadata VALUES (?, ?, ?, ?, ?)",
                [
                    (name, index, json.dumps(dict(result.provenance)), result.original_target.wkb, result.target.wkb)
                    for name, index, result in self._results
                ],
            )
            db.executemany(
                "INSERT INTO target_issues VALUES (?, ?, ?, ?)",
                [(issue.index, issue.name, issue.code, issue.reason) for issue in self._issues],
            )
            for index, vector in enumerate(self._vectors):
                table = f"layer_{index}"
                srid = vector.crs.to_epsg() or 100000 + index
                db.execute(
                    "INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        vector.crs.to_string(),
                        srid,
                        "EPSG" if vector.crs.to_epsg() else "NONE",
                        srid,
                        vector.crs.to_wkt(),
                        None,
                    ),
                )
                db.execute(
                    f'CREATE TABLE "{table}" (fid INTEGER PRIMARY KEY, geometry BLOB, source_id INTEGER, target_name TEXT, target_index INTEGER)'
                )
                db.execute(
                    "INSERT INTO gpkg_contents (table_name,data_type,identifier,srs_id) VALUES (?, 'features', ?, ?)",
                    (table, table, srid),
                )
                db.execute(
                    "INSERT INTO gpkg_geometry_columns VALUES (?, 'geometry', 'GEOMETRY', ?, 0, 0)", (table, srid)
                )
                columns: dict[str, str] = {}
                used = {"fid", "geometry", "source_id", "target_name", "target_index"}
                for source in vector.sources:
                    for field in source.attributes.schema:
                        sql_type = _scalar_type(field.type)
                        if field.name in columns or sql_type is None:
                            continue
                        column = field.name
                        while column.lower() in used:
                            column = "source_" + column
                        used.add(column.lower())
                        columns[field.name] = column
                        db.execute(f'ALTER TABLE "{table}" ADD COLUMN {_quote(column)} {sql_type}')
                for row, geometry in enumerate(vector.geometries):
                    source = vector.sources[row] if vector.sources else None
                    blob = b"GP" + bytes((0, 1)) + struct.pack("<i", srid) + geometry.wkb
                    db.execute(
                        f'INSERT INTO "{table}" (fid,geometry,source_id,target_name,target_index) VALUES (?, ?, ?, ?, ?)',
                        (row + 1, blob, source.id if source else None, vector.group, vector.target_index),
                    )
                    if source:
                        for field, column in columns.items():
                            if field in source.attributes:
                                db.execute(
                                    f'UPDATE "{table}" SET {_quote(column)} = ? WHERE fid = ?',
                                    (source.attributes[field], row + 1),
                                )
                        original = source.source if isinstance(source, SubbasinContribution) else source
                        _arrow(db, table, f"feature:{row + 1}", original.table.slice(original.row, 1))
                if columns:
                    db.execute(
                        "INSERT INTO source_metadata VALUES (?, ?)", (table, json.dumps({"attribute_columns": columns}))
                    )
                self._layer(
                    layers,
                    groups[vector.group] if vector.group is not None else tree,
                    vector.name,
                    f"./vectors.gpkg|layername={table}",
                    "vector",
                    vector.crs,
                    vector.outline,
                )
            for index, network in self._networks:
                table = f"layer_{index}"
                _arrow(db, table, "relationships", network.relationships)
                for row in network.relationships.to_pylist():
                    for relation, targets in row.items():
                        if relation != "id" and isinstance(targets, list):
                            db.executemany(
                                "INSERT INTO river_connections VALUES (?, ?, ?, ?)",
                                [(table, row["id"], relation, target) for target in targets],
                            )
                if network.node_relationships is not None:
                    _arrow(db, table, "node_relationships", network.node_relationships)
                for node in network.nodes:
                    _arrow(db, table, "node", node.table.slice(node.row, 1))
                db.execute("INSERT INTO source_metadata VALUES (?, ?)", (table, json.dumps(dict(network.declarations))))
        for index, (name, subset, group) in enumerate(self._rasters):
            filename = f"raster_{index}.tif"
            window = subset.window
            if subset.band == "direction":
                values = window.raw_direction
                nodata = window.direction_nodata
                valid = window.direction_valid & subset.mask
            else:
                values = window.accumulation
                nodata = window.accumulation_nodata
                valid = window.accumulation_valid & subset.mask
            if values is None or nodata is None:
                raise ValueError("raster export requires retained source values and nodata")
            output = np.where(valid, values, nodata).astype(values.dtype)
            with rasterio.open(
                folder / filename,
                "w",
                driver="GTiff",
                width=output.shape[1],
                height=output.shape[0],
                count=1,
                dtype=output.dtype,
                crs=window.crs,
                transform=window.transform,
                nodata=nodata,
                compress="deflate",
            ) as raster:
                raster.write(output, 1)
                raster.write_mask(valid.astype("uint8") * 255)
            self._layer(
                layers, groups[group] if group is not None else tree, name, "./" + filename, "raster", window.crs
            )
        with zipfile.ZipFile(folder / "watershed.qgz", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("watershed.qgs", ET.tostring(project, encoding="utf-8", xml_declaration=True))
        return folder

    @staticmethod
    def _layer(
        layers: ET.Element, tree: ET.Element, name: str, source: str, kind: str, crs: CRS, outline: bool = False
    ) -> None:
        identity = "layer_" + uuid4().hex
        tree_layer = ET.SubElement(
            tree,
            "layer-tree-layer",
            id=identity,
            name=name,
            source=source,
            providerKey="ogr" if kind == "vector" else "gdal",
            checked="Qt::Checked",
            expanded="1",
        )
        if outline:
            tree.remove(tree_layer)
            tree.insert(0, tree_layer)
        layer = ET.SubElement(layers, "maplayer", type=kind, autoRefreshEnabled="0")
        _text(layer, "id", identity)
        _text(layer, "layername", name)
        _text(layer, "datasource", source)
        _text(layer, "provider", "ogr" if kind == "vector" else "gdal")
        _crs(layer, crs)
        if outline:
            renderer = ET.SubElement(layer, "renderer-v2", type="singleSymbol", symbollevels="0")
            symbol = ET.SubElement(ET.SubElement(renderer, "symbols"), "symbol", type="fill", name="0", alpha="1")
            symbol_layer = ET.SubElement(symbol, "layer", {"class": "SimpleFill", "enabled": "1"})
            options = ET.SubElement(symbol_layer, "Option", type="Map")
            for key, value in {"style": "no", "outline_color": "255,0,0,255", "outline_width": "0.6"}.items():
                ET.SubElement(options, "Option", name=key, value=value, type="QString")
