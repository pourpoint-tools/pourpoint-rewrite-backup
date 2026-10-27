"""Materialized source features backed by faithful Arrow values and original WKB."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import ClassVar, cast

import pyarrow as pa
import shapely
from shapely.geometry.base import BaseGeometry


class Attributes(Mapping[str, object]):
    """Read-only mapping of source fields other than geometry.

    Iteration exposes field names, including standard fields such as ``id``.
    Indexing converts an Arrow scalar to its Python value; source nulls become
    ``None``. Use ``scalar`` and ``schema`` for lossless typed inspection.
    Values are source quantities, not recomputed measurements of clipped pieces.
    """

    def __init__(self, table: pa.Table, row: int) -> None:
        self._table = table
        self._row = row

    def __getitem__(self, name: str) -> object:
        return self.scalar(name).as_py()

    def scalar(self, name: str) -> pa.Scalar:
        """Return a typed source value, including nulls; geometry raises ``KeyError``."""
        if name == "geometry":
            raise KeyError(name)
        return self._table[name][self._row]

    def __iter__(self) -> Iterator[str]:
        return (name for name in self._table.column_names if name != "geometry")

    def __len__(self) -> int:
        return len(self._table.column_names) - int("geometry" in self._table.column_names)

    @property
    def schema(self) -> pa.Schema:
        """Attribute fields with source field/schema metadata, excluding geometry."""
        return pa.schema(
            [field for field in self._table.schema if field.name != "geometry"], metadata=self._table.schema.metadata
        )


@dataclass(frozen=True)
class Feature:
    """A materialized feature backed by a source Arrow row.

    Attributes
    ----------
    table
        Retained source values, including original WKB and Arrow metadata.
    row
        Zero-based row within ``table`` identifying this feature.

    Notes
    -----
    Features remain usable after fabric closure. Reading a feature does not
    rewrite WKB or validate its geometry; decoding occurs on ``geometry`` access.
    """

    table: pa.Table
    row: int
    geometry_types: ClassVar[tuple[str, ...]] = ()

    @property
    def id(self) -> int:
        """Supplied source ID, not a target name or an index into a result list."""
        return cast(int, self.table["id"][self.row].as_py())

    @property
    def wkb(self) -> bytes:
        """Original source geometry bytes; missing geometry raises ``ValueError``."""
        value = self.table["geometry"][self.row].as_py()
        if not isinstance(value, bytes):
            raise ValueError(f"feature {self.id}: missing geometry")
        return value

    @property
    def geometry(self) -> BaseGeometry:
        """Decode source WKB in EPSG:4326 without repair or simplification.

        Empty geometry or a geometry type inappropriate for this feature raises
        ``ValueError``; malformed WKB raises a Shapely decoding error.
        """
        result = shapely.from_wkb(self.wkb)
        if result.is_empty or result.geom_type not in self.geometry_types:
            raise ValueError(f"feature {self.id}: invalid {type(self).__name__} geometry")
        return result

    @property
    def attributes(self) -> Attributes:
        """Supplied non-geometry fields with original values, nulls and metadata."""
        return Attributes(self.table, self.row)


class Subbasin(Feature):
    """A whole source catchment polygon or multipolygon, not a refined contribution."""

    geometry_types = ("Polygon", "MultiPolygon")


class River(Feature):
    """A source river line or multiline with supplied attributes, not inferred order."""

    geometry_types = ("LineString", "MultiLineString")


class SourceNode(Feature):
    """A supplied river-node point, distinct from a clipped river endpoint."""

    geometry_types = ("Point",)


@dataclass(frozen=True)
class RiverNetwork:
    """River features and their supplied directed relationships and nodes.

    Attributes
    ----------
    rivers
        Individual source rivers or clipped river pieces. Clipped pieces retain
        source identity and attributes; source quantities are not recomputed.
    relationships
        Arrow relationship rows for selected rivers, including references to
        unselected neighbors. Incidence is not a substitute for directed flow.
    nodes
        Available source nodes; new clipping endpoints are not source nodes.
    node_relationships
        Supplied node relationship rows, or ``None`` when not acquired or supplied.
    declarations
        Publication declarations retaining relationship target domains and meanings.

    Notes
    -----
    Neighbor IDs absent from ``rivers`` remain boundary references, not evidence
    of natural termination. Materialized networks survive fabric closure.
    """

    rivers: list[River]
    relationships: pa.Table
    nodes: list[SourceNode]
    node_relationships: pa.Table | None
    declarations: Mapping[str, object]

    @property
    def geometry(self) -> BaseGeometry:
        """Union of current river linework in EPSG:4326, empty for no rivers.

        This display geometry does not replace individual identities or directed
        relationships and may dissolve overlapping linework.
        """
        return shapely.union_all([river.geometry for river in self.rivers])

    @property
    def boundary_river_ids(self) -> frozenset[int]:
        """Supplied upstream/downstream neighbor IDs absent from ``rivers``."""
        selected = {river.id for river in self.rivers}
        neighbors: set[int] = set()
        for name in ("upstream_ids", "downstream_ids"):
            if name in self.relationships.column_names:
                for values in self.relationships[name].to_pylist():
                    neighbors.update(values)
        return frozenset(neighbors - selected)
