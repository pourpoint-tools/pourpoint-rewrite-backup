"""HFX dataset-owned readers and compact topology; query-owned selections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Self

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from pourpoint._validation import (
    BBOX_TYPE,
    GRAPH_FIELDS,
    SNAP_FIELDS,
    bbox_check,
    nonnull,
    scalar_check,
    schema_check,
)
from pourpoint.catchments import CatchmentReader
from pourpoint.geometry import DecodedGeometry, GeometryBatch
from pourpoint.metadata import Bounds, Manifest
from pourpoint.rasters import RasterReader
from pourpoint.spatial import candidate_groups, conservative_bounds, overlap
from pourpoint.storage import Source
from pourpoint.topology import Topology, UnitSelection

type UnitIds = Sequence[int] | npt.NDArray[np.int64] | UnitSelection


class Dataset:
    """One immutable publication, reusable readers and lazy resident graph.

    Opening reads the manifest, directory and IPC metadata/fingerprints only.
    First topology use acquires the original graph and prepares numeric arrays.
    Catchments remain selective on every call. Instances are not thread-safe.
    Selected results outlive close(); selections cannot address another instance.
    """

    manifest: Manifest
    _source: Source
    _cache_dir: Path
    _graph_path: Path
    _catchments: CatchmentReader

    def __init__(self) -> None:
        self._closed = False
        self._rasters: RasterReader | None = None
        self._temporary_cache: TemporaryDirectory[str] | None = None
        self._topology: Topology | None = None
        self._graph: pq.ParquetFile | None = None
        self._snap_readers: dict[str, pq.ParquetFile] = {}
        self.last_access: dict[str, object] = {}

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        cache_dir: str | Path | None = None,
        storage_options: Mapping[str, object] | None = None,
    ) -> Self:
        instance = cls()
        instance._source = Source(root, storage_options=storage_options)
        try:
            instance.manifest = Manifest.parse(instance._source.read_json("manifest.json"))
            if cache_dir is None:
                instance._temporary_cache = TemporaryDirectory(prefix="pourpoint-graph-")
                cache_dir = instance._temporary_cache.name
            instance._cache_dir = Path(cache_dir)
            instance._catchments = CatchmentReader(instance._source, instance.manifest)
            return instance
        except BaseException:
            instance.close()
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("dataset is closed")

    def _graph_reader(self) -> pq.ParquetFile:
        self._ensure_open()
        if self._graph is None:
            path = self.manifest.graph
            local = self._source.graph_path(self._cache_dir, path, self.manifest.artifacts[path])
            self._source.check_graph(local)
            reader = pq.ParquetFile(local, pre_buffer=False)
            try:
                schema_check(reader.schema_arrow, GRAPH_FIELDS, path)
                if reader.metadata.num_rows != self.manifest.unit_count:
                    raise ValueError(f"{path}: count differs from manifest unit_count")
                self._source.check_graph(local)
            except BaseException:
                reader.close()
                raise
            self._graph_path = local
            self._graph = reader
        self._source.check_graph(self._graph_path)
        return self._graph

    @property
    def rasters(self) -> RasterReader:
        """Lazy native-grid raster access, independent of topology acquisition."""
        self._ensure_open()
        if self._rasters is None:
            self._rasters = RasterReader(self._source, self.manifest)
        return self._rasters

    @property
    def topology(self) -> Topology:
        self._ensure_open()
        if self._topology is None:
            table = self._graph_reader().read(columns=["id", "level", "upstream_ids"])
            self._source.check_graph(self._graph_path)
            self._topology = Topology(table, topology=self.manifest.topology)
        return self._topology

    @property
    def levels(self) -> tuple[int, ...]:
        return tuple(int(level) for level in np.unique(self.topology.levels))

    def _ids(self, units: UnitIds) -> npt.NDArray[np.int64]:
        self._ensure_open()
        if isinstance(units, UnitSelection):
            units.require_source(self.topology)
            # Validate IDs rather than trusting user-constructed positions.
            return units.ids
        if not isinstance(units, np.ndarray) and any(isinstance(value, (bool, np.bool_)) for value in units):
            raise ValueError("unit IDs must be positive int64 values")
        ids = np.asarray(units if isinstance(units, np.ndarray) else list(units))
        if ids.ndim != 1 or (ids.size and ids.dtype.kind not in "iu"):
            raise ValueError("unit IDs must be a one-dimensional integer array")
        if ids.size and (np.any(ids <= 0) or np.any(ids > np.iinfo(np.int64).max)):
            raise ValueError("unit IDs must be positive int64 values")
        return ids.astype(np.int64, copy=False)

    def upstream(self, unit_id: int) -> UnitSelection:
        return self.topology.upstream(unit_id)

    def _records(self, units: UnitIds) -> pa.Table:
        ids = self._ids(units)
        table = self._catchments.read(ids)
        self.last_access = {
            "lookup_batches": self._catchments.last_access.lookup_batches,
            "catchment_batches": self._catchments.last_access.catchment_batches,
        }
        scalar_check(table, self.manifest.catchments)
        # Selected references only. Never certify worldwide catchment attributes.
        positions = self.topology.positions(ids)
        levels = table["level"].to_numpy()
        if not np.array_equal(levels, self.topology.levels[positions]):
            raise ValueError("catchments: selected level differs from graph")
        parents = table["parent_id"].combine_chunks()
        valid = parents.is_valid().to_numpy(zero_copy_only=False)
        if np.any(valid):
            try:
                parent_positions = self.topology.positions(parents.drop_null().to_numpy())
            except KeyError as exc:
                raise ValueError("catchments: selected parent_id references unknown unit") from exc
            if np.any(self.topology.levels[parent_positions] >= levels[valid]):
                raise ValueError("catchments: parent_id must reference a strictly coarser level")
        if not self.manifest.has_up_area and table["up_area_km2"].null_count != table.num_rows:
            raise ValueError("catchments: up_area_km2 populated with has_up_area=false")
        return table

    def records(self, units: UnitIds) -> GeometryBatch:
        """Complete selected attributes and exact WKB, preserving order/duplicates.

        Geometry decoding is explicit. A combined request reads shared batches once.
        """
        return GeometryBatch(self._records(units), "polygon", self.manifest.catchments)

    def attributes(self, units: UnitIds, columns: Sequence[str] | None = None) -> pa.Table:
        self._ensure_open()
        requested = (
            list(columns)
            if columns is not None
            else [name for name in self._catchments.schema.names if name != "geometry"]
        )
        if "geometry" in requested:
            raise ValueError("use records() or polygons() for geometry access")
        if not requested or len(set(requested)) != len(requested):
            raise ValueError("columns must be nonempty and distinct")
        unknown = set(requested) - set(self._catchments.schema.names)
        if unknown:
            raise KeyError(f"catchments: unknown columns {sorted(unknown)}")
        return self._records(units).select(requested)

    def polygons(self, units: UnitIds) -> GeometryBatch:
        return GeometryBatch(self._records(units).select(["id", "geometry"]), "polygon", self.manifest.catchments)

    def _eligible_levels(self, levels: Sequence[int] | None) -> tuple[int, ...]:
        available = self.levels
        if levels is None:
            return available
        if any(type(level) is not int or level not in available for level in levels):
            raise ValueError("unknown dataset level")
        return tuple(levels)

    def catchment_candidates(self, bounds: Bounds, *, levels: Sequence[int] | None = None) -> GeometryBatch:
        """Conservative graph-bbox candidates in graph row order, not containment.

        Parquet row-group statistics prune graph reads. Catchment records always
        resolve through the published ID lookup, independently of graph ordering.
        """
        self._ensure_open()
        envelope = conservative_bounds(bounds)
        eligible = self._eligible_levels(levels)
        graph = self._graph_reader()
        groups = candidate_groups(graph, envelope, levels=eligible, graph=True) if eligible else []
        ids: list[npt.NDArray[np.int64]] = []
        for group in groups:
            self._source.check_graph(self._graph_path)
            candidate = graph.read_row_group(
                group, columns=["id", "level", "bbox_minx", "bbox_miny", "bbox_maxx", "bbox_maxy"], use_threads=False
            )
            self._source.check_graph(self._graph_path)
            nonnull(candidate, candidate.column_names, self.manifest.graph)
            bbox_check(candidate, self.manifest.graph, graph=True)
            mask = overlap(candidate, envelope, graph=True) & np.isin(candidate["level"].to_numpy(), eligible)
            ids.append(candidate["id"].to_numpy()[mask])
        result = self.records(np.concatenate(ids) if ids else np.array([], dtype=np.int64))
        self.last_access["graph_row_groups"] = groups
        return result

    def snap_candidates(self, name: str, bounds: Bounds, *, levels: Sequence[int] | None = None) -> DecodedGeometry:
        """Read one named snap set, preserving feature IDs and unit references.

        Output follows file order. Unknown/null bounds use decoded bounds as
        fallback. Weights/roles are retained, not ranked or compared across sets.
        All bbox-selected references and scalar records are validated on access.
        """
        self._ensure_open()
        envelope = conservative_bounds(bounds)
        eligible = self._eligible_levels(levels)
        declaration = self.manifest.snap_sets[name]
        if name not in self._snap_readers:
            reader = self._source.parquet(declaration.path, self.manifest.artifacts[declaration.path])
            schema_check(reader.schema_arrow, SNAP_FIELDS, declaration.path)
            optional = {}
            if "stem_role" in reader.schema_arrow.names:
                optional["stem_role"] = (pa.string(), True)
            if "bbox" in reader.schema_arrow.names:
                optional["bbox"] = (BBOX_TYPE, True)
            schema_check(reader.schema_arrow, optional, declaration.path)
            self._snap_readers[name] = reader
        file = self._snap_readers[name]
        groups = candidate_groups(file, envelope) if set(eligible).intersection(declaration.references_levels) else []
        columns = file.schema_arrow.names
        pieces: list[pa.Table] = []
        geometries: list[npt.NDArray[np.object_]] = []
        maximum = 0
        for group in groups:
            candidate = file.read_row_group(group, columns=columns, use_threads=False)
            maximum = max(maximum, candidate.get_total_buffer_size())
            if "bbox" in candidate.column_names:
                bbox_check(candidate, declaration.path)
            selected = candidate.filter(pa.array(overlap(candidate, envelope)))
            nonnull(selected, ["id", "unit_id", "weight", "geometry"], declaration.path)
            ids = selected["id"].to_numpy()
            weights = selected["weight"].to_numpy()
            if np.any(ids <= 0) or np.any(~np.isfinite(weights)) or np.any(weights < 0):
                raise ValueError(f"{declaration.path}: invalid selected id or weight")
            if "stem_role" in selected.column_names:
                roles = selected["stem_role"].drop_null().to_pylist()
                if any(role not in ("mainstem", "tributary", "distributary", "unknown") for role in roles):
                    raise ValueError(f"{declaration.path}: invalid selected stem_role")
            try:
                positions = self.topology.positions(selected["unit_id"].to_numpy())
            except KeyError as exc:
                raise ValueError(f"{declaration.path}: unknown selected unit_id") from exc
            reference_levels = self.topology.levels[positions]
            if np.any(~np.isin(reference_levels, declaration.references_levels)):
                raise ValueError(f"{declaration.path}: unit_id level not in references_levels")
            selected = selected.filter(pa.array(np.isin(reference_levels, eligible)))
            decoded = GeometryBatch(selected, "snap", declaration.path).decode()
            unknown = (
                selected["bbox"].is_null().to_numpy(zero_copy_only=False)
                if "bbox" in selected.column_names
                else np.ones(selected.num_rows, dtype=np.bool_)
            )
            if np.any(unknown):
                actual = shapely.bounds(decoded.geometries)
                keep = ~unknown | (
                    (actual[:, 0] <= envelope[2])
                    & (actual[:, 1] <= envelope[3])
                    & (actual[:, 2] >= envelope[0])
                    & (actual[:, 3] >= envelope[1])
                )
                pieces.append(decoded.attributes.filter(pa.array(keep)))
                geometries.append(decoded.geometries[keep])
            else:
                pieces.append(decoded.attributes)
                geometries.append(decoded.geometries)
        self.last_access = {"row_groups": groups, "maximum_candidate_buffer_bytes": maximum}
        schema = pa.schema(
            [file.schema_arrow.field(name) for name in columns if name != "geometry"],
            metadata=file.schema_arrow.metadata,
        )
        table = pa.concat_tables(pieces) if pieces else pa.Table.from_batches([], schema=schema)
        ids = table["id"].to_numpy()
        if np.unique(ids).size != len(ids):
            raise ValueError(f"{declaration.path}: duplicate selected snap id")
        decoded_geometries = np.concatenate(geometries) if geometries else np.array([], dtype=object)
        decoded_geometries.flags.writeable = False
        ids.flags.writeable = False
        return DecodedGeometry(ids, decoded_geometries, table)

    @property
    def buffer_bytes(self) -> dict[str, int]:
        """Retained bulk arrays; excludes metadata, query results, native geometry and RSS."""
        self._ensure_open()
        return {
            "source_read_buffers": self._source.buffer_bytes,
            "topology": 0 if self._topology is None else self._topology.buffer_bytes,
            "query_workspace": 0 if self._topology is None else self._topology.query_workspace_bytes,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._graph is not None:
            self._graph.close()
            self._graph = None
        if self._rasters is not None:
            self._rasters.close()
        self._source.close()
        self._snap_readers.clear()
        self._topology = None
        if hasattr(self, "_catchments"):
            del self._catchments
        if self._temporary_cache is not None:
            self._temporary_cache.cleanup()
            self._temporary_cache = None

    def __enter__(self) -> Self:
        self._ensure_open()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()
