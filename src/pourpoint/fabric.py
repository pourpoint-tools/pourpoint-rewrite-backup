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
    """One eager query lifecycle with an accumulating original-byte disk cache.

    Opening reads only the manifest. Use ``offline=True`` to require its cached
    snapshot and fail without network if any required content is unavailable.
    Instances are single-thread-owned; acquired results survive closure.
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

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("fabric is closed")

    def _reader(self, domain: str) -> VectorReader:
        self._ensure_open()
        if domain not in self._vectors:
            self._vectors[domain] = VectorReader(self._source, self.manifest, domain)
        return self._vectors[domain]

    def records(self, domain: str, ids: Sequence[int], *, columns: Sequence[str] | None = None) -> pa.Table:
        """Selected source rows in input order, including duplicate IDs."""
        return self._reader(domain).records(ids, columns=columns)

    def candidates(
        self, domain: str, bounds: Bounds, *, levels: Sequence[int] | None = None, columns: Sequence[str] | None = None
    ) -> pa.Table:
        """Conservative bbox candidates, not exact geometry intersections."""
        return self._reader(domain).candidates(bounds, levels=levels, columns=columns)

    def relationships(self, domain: str, ids: Sequence[int], *, columns: Sequence[str] | None = None) -> pa.Table:
        """Immediate supplied relationships, preserving domains and missing neighbors."""
        return self._reader(domain).relationships(ids, columns=columns)

    def traverse(self, domain: str, ids: Sequence[int], *, relationship: str = "upstream_ids") -> list[int]:
        """Inclusive graph closure over one supplied same-domain relationship."""
        return self._reader(domain).traverse(ids, relationship=relationship)

    def subbasins(self, ids: Sequence[int]) -> list[Subbasin]:
        table = self.records("catchments", ids)
        return [Subbasin(table, row) for row in range(table.num_rows)]

    def nodes(self, ids: Sequence[int]) -> list[SourceNode]:
        table = self.records("river_nodes", ids)
        return [SourceNode(table, row) for row in range(table.num_rows)]

    def river_network(self, ids: Sequence[int]) -> RiverNetwork:
        """Selected rivers and their supplied source nodes, without invented flow."""
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
        self._ensure_open()
        if self._rasters is None:
            self._rasters = RasterReader(self._source, self.manifest)
        return self._rasters

    @property
    def stats(self) -> TransferStats:
        return self._source.stats

    @property
    def buffer_bytes(self) -> dict[str, int]:
        self._ensure_open()
        return {
            "source": self._source.buffer_bytes,
            "vectors": sum(reader.buffer_bytes for reader in self._vectors.values()),
        }

    def close(self) -> None:
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
