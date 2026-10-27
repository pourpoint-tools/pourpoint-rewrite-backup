"""Context-managed selective access to one final HFX publication snapshot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Self

import pyarrow as pa

from pourpoint.features import River, RiverNetwork, SourceNode, Subbasin
from pourpoint.metadata import Bounds, SpatialManifest
from pourpoint.rasters import RasterReader
from pourpoint.storage import Source, TransferStats
from pourpoint.vectors import VectorReader


class HydroFabric:
    """Open one HFX publication for eager queries and reusable source access.

    Parameters
    ----------
    source
        Dataset root directory or HTTP(S)/S3 URL containing ``manifest.json``.
    cache
        Retain acquired original bytes and metadata on disk across lifetimes.
        Disabling this does not disable bounded in-memory reuse.
    cache_dir
        Persistent cache location. When omitted, use ``~/Library/Caches/pourpoint``
        on macOS, ``%LOCALAPPDATA%/pourpoint`` on Windows (falling back to
        ``~/AppData/Local/pourpoint``), or ``$XDG_CACHE_HOME/pourpoint`` on other
        platforms (falling back to ``~/.cache/pourpoint``).
    offline
        Require the cached manifest snapshot and cached content without network
        access. Requires caching; missing content raises rather than returning
        incomplete results.
    storage_options
        Options passed to the remote object store, including S3 credentials or
        endpoint configuration. Local paths do not accept storage options.

    Attributes
    ----------
    manifest
        Parsed publication metadata, artifact identities and relationship
        declarations for this lifetime.

    Notes
    -----
    Opening reads only the manifest; vector and raster readers open as needed.
    Use a context manager and perform source-dependent work before it closes.
    Materialized vector results and raster arrays survive closure, but readers
    and raster requests cannot be used to acquire more data afterward.
    Instances belong to one thread; calls must not overlap.

    The disk cache grows across queries against the same snapshot. Closing does
    not remove it and there is no automatic disk eviction. A partial cache is
    not necessarily an independently openable HFX publication.
    """

    def __init__(
        self,
        source: str | Path,
        *,
        cache: bool = True,
        cache_dir: str | Path | None = None,
        offline: bool = False,
        storage_options: Mapping[str, object] | None = None,
    ) -> None:
        self._location = source
        self._cache = cache
        self._cache_dir = cache_dir
        self._offline = offline
        self._storage_options = storage_options
        self._source = Source(
            source, cache=cache, cache_dir=cache_dir, offline=offline, storage_options=storage_options
        )
        self._closed = False
        self._vectors: dict[str, VectorReader] = {}
        self._rasters: RasterReader | None = None
        try:
            self.manifest = SpatialManifest.parse(self._source.read_json("manifest.json"))
        except BaseException:
            self.close()
            raise

    def _fork(self) -> HydroFabric:
        """Open independent worker-owned readers against the same pinned snapshot."""
        self._ensure_open()
        worker = HydroFabric(
            self._location,
            cache=self._cache,
            cache_dir=self._cache_dir,
            offline=self._offline,
            storage_options=self._storage_options,
        )
        if worker.manifest.declarations != self.manifest.declarations:
            worker.close()
            raise ValueError("publication changed while starting batch workers")
        return worker

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("fabric is closed")

    def _reader(self, domain: str) -> VectorReader:
        self._ensure_open()
        if domain not in self._vectors:
            self._vectors[domain] = VectorReader(self._source, self.manifest, domain)
        return self._vectors[domain]

    def records(self, domain: str, ids: Sequence[int], *, columns: Sequence[str] | None = None) -> pa.Table:
        """Read source rows in requested order, preserving duplicate IDs.

        Parameters
        ----------
        domain
            Published feature family: ``catchments``, ``rivers`` or ``river_nodes``.
        ids
            Positive int64 source IDs. Empty input returns an empty table with
            the selected schema; an unknown ID raises ``KeyError``.
        columns
            Unique source field names to read, or all fields when omitted.
            Geometry is original WKB, not decoded Shapely geometry.

        Returns
        -------
        pyarrow.Table
            Materialized values with source types, nulls and applicable schema
            and field metadata. Remains usable after closure.

        Raises
        ------
        ValueError
            Invalid IDs, unknown or duplicate columns, or corrupt source data.
        KeyError
            The domain or a requested ID is not supplied.
        """
        return self._reader(domain).records(ids, columns=columns)

    def candidates(
        self, domain: str, bounds: Bounds, *, levels: Sequence[int] | None = None, columns: Sequence[str] | None = None
    ) -> pa.Table:
        """Read conservative bounding-box candidates, not exact intersections.

        Parameters
        ----------
        domain
            Published ``catchments``, ``rivers`` or ``river_nodes`` family.
        bounds
            Non-wrapping ``(west, south, east, north)`` envelope in EPSG:4326.
        levels
            Nonnegative source levels to retain; omitted means all levels.
            Levels are independent between feature families.
        columns
            Unique source field names, or all fields when omitted.

        Returns
        -------
        pyarrow.Table
            Materialized source rows in publication order. False positives are
            possible; test decoded geometry for exact intersection. No matches
            returns an empty table with the selected schema.
        """
        return self._reader(domain).candidates(bounds, levels=levels, columns=columns)

    def relationships(self, domain: str, ids: Sequence[int], *, columns: Sequence[str] | None = None) -> pa.Table:
        """Read immediate supplied relationships in requested ID order.

        Parameters
        ----------
        domain
            Feature family whose relationship rows are requested.
        ids
            Positive int64 source IDs; duplicates and order are preserved.
        columns
            Unique relationship fields to read, or all fields when omitted.

        Returns
        -------
        pyarrow.Table
            Selected source relationship columns, with supplied metadata.
            Neighbor IDs are retained as lists; ``id`` is included only when selected.
            Consult ``manifest.relationships`` for each field's target domain
            and meaning. Flow, hierarchy and node incidence are not interchangeable.
            References to unselected features remain present; they do not imply
            natural terminals. Empty input returns a schema-bearing empty table.

        Raises
        ------
        KeyError
            Relationship data for the domain or a requested ID is not supplied.
        ValueError
            Invalid IDs, columns or relationship data.
        """
        return self._reader(domain).relationships(ids, columns=columns)

    def traverse(self, domain: str, ids: Sequence[int], *, relationship: str = "upstream_ids") -> list[int]:
        """Return inclusive closure over a supplied same-domain relationship.

        Parameters
        ----------
        domain
            Feature family to traverse without crossing domains.
        ids
            Positive int64 seed IDs. Duplicate seeds are visited only once.
        relationship
            Declared relationship column targeting the same domain. Traversal
            follows only that relationship; it does not infer hydrological flow.

        Returns
        -------
        list
            Breadth-first IDs: seeds in input order, then each frontier sorted
            by ID. Cycles are deduplicated; empty seeds return an empty list.

        Raises
        ------
        ValueError
            The relationship is absent or targets another domain, or data is invalid.
        KeyError
            Domain relationship data or a visited ID is unavailable.
        """
        return self._reader(domain).traverse(ids, relationship=relationship)

    def subbasins(self, ids: Sequence[int]) -> list[Subbasin]:
        """Materialize whole source sub-basins in ID order, including duplicates.

        Uses ``records`` ID validation and missing-ID behavior. Geometry is
        decoded only when a feature's ``geometry`` property is accessed.
        """
        table = self.records("catchments", ids)
        return [Subbasin(table, row) for row in range(table.num_rows)]

    def nodes(self, ids: Sequence[int]) -> list[SourceNode]:
        """Materialize source nodes in ID order, with ``records`` ID semantics."""
        table = self.records("river_nodes", ids)
        return [SourceNode(table, row) for row in range(table.num_rows)]

    def river_network(self, ids: Sequence[int]) -> RiverNetwork:
        """Materialize selected rivers, their relationships and referenced nodes.

        River order and duplicates follow ``ids``; referenced nodes are unique
        and sorted by ID. Relationships retain references to unselected rivers.
        Missing IDs raise ``KeyError`` rather than dropping features. No clipping,
        traversal or inference of flow is performed.
        """
        table = self.records("rivers", ids)
        relations = self.relationships("rivers", ids)
        node_ids: set[int] = set()
        for name in ("upstream_node_ids", "downstream_node_ids"):
            if name in relations.column_names:
                for values in relations[name].to_pylist():
                    node_ids.update(values)
        nodes = self.nodes(sorted(node_ids)) if node_ids else []
        node_relations = (
            self.relationships("river_nodes", sorted(node_ids))
            if node_ids and "river_nodes" in self.manifest.relationships
            else None
        )
        return RiverNetwork(
            [River(table, row) for row in range(table.num_rows)],
            relations,
            nodes,
            node_relations,
            self.manifest.declarations,
        )

    @property
    def rasters(self) -> RasterReader:
        """Native-grid raster access owned by this open fabric's lifecycle."""
        self._ensure_open()
        if self._rasters is None:
            self._rasters = RasterReader(self._source, self.manifest)
        return self._rasters

    @property
    def stats(self) -> TransferStats:
        """Cumulative transport calls, payload bytes and disk-cache hit bytes."""
        return self._source.stats

    @property
    def buffer_bytes(self) -> dict[str, int]:
        """Logical source/vector buffer bytes, not total process memory.

        Excludes native-library caches, metadata and caller-owned results.
        """
        self._ensure_open()
        return {
            "source": self._source.buffer_bytes,
            "vectors": sum(reader.buffer_bytes for reader in self._vectors.values()),
        }

    def close(self) -> None:
        """Release source readers and connections; retain the persistent cache.

        Repeated closure is harmless. Materialized tables, features and raster
        arrays remain usable; subsequent source-dependent calls fail.
        """
        if self._closed:
            return
        self._closed = True
        if self._rasters is not None:
            self._rasters.close()
        self._vectors.clear()
        self._source.close()

    def __enter__(self) -> Self:
        self._ensure_open()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()
