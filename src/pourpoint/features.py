"""Materialized source features backed by faithful Arrow values and original WKB."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import ClassVar, cast

import pyarrow as pa
import shapely
from shapely.geometry.base import BaseGeometry


class Attributes(Mapping[str, object]):
    """Discoverable Python values, with lossless Arrow scalars/schema available."""

    def __init__(self, table: pa.Table, row: int) -> None:
        self._table = table
        self._row = row

    def __getitem__(self, name: str) -> object:
        return self.scalar(name).as_py()

    def scalar(self, name: str) -> pa.Scalar:
        if name == "geometry":
            raise KeyError(name)
        return self._table[name][self._row]

    def __iter__(self) -> Iterator[str]:
        return (name for name in self._table.column_names if name != "geometry")

    def __len__(self) -> int:
        return len(self._table.column_names) - int("geometry" in self._table.column_names)

    @property
    def schema(self) -> pa.Schema:
        return pa.schema(
            [field for field in self._table.schema if field.name != "geometry"], metadata=self._table.schema.metadata
        )


@dataclass(frozen=True)
class Feature:
    """A source row. Materialization never rewrites WKB or source attributes."""

    table: pa.Table
    row: int
    geometry_types: ClassVar[tuple[str, ...]] = ()

    @property
    def id(self) -> int:
        return cast(int, self.table["id"][self.row].as_py())

    @property
    def wkb(self) -> bytes:
        value = self.table["geometry"][self.row].as_py()
        if not isinstance(value, bytes):
            raise ValueError(f"feature {self.id}: missing geometry")
        return value

    @property
    def geometry(self) -> BaseGeometry:
        result = shapely.from_wkb(self.wkb)
        if result.is_empty or result.geom_type not in self.geometry_types:
            raise ValueError(f"feature {self.id}: invalid {type(self).__name__} geometry")
        return result

    @property
    def attributes(self) -> Attributes:
        return Attributes(self.table, self.row)


class Subbasin(Feature):
    geometry_types = ("Polygon", "MultiPolygon")


class River(Feature):
    geometry_types = ("LineString", "MultiLineString")


class SourceNode(Feature):
    geometry_types = ("Point",)


@dataclass(frozen=True)
class RiverNetwork:
    """Individual features plus complete supplied selected-row relationships.

    Neighbor IDs absent from ``rivers`` remain boundary references, never terminals.
    Relationship declarations retain target domains and source meaning.
    """

    rivers: list[River]
    relationships: pa.Table
    nodes: list[SourceNode]
    node_relationships: pa.Table | None
    declarations: Mapping[str, object]

    @property
    def geometry(self) -> BaseGeometry:
        return shapely.union_all([river.geometry for river in self.rivers])

    @property
    def boundary_river_ids(self) -> frozenset[int]:
        selected = {river.id for river in self.rivers}
        neighbors: set[int] = set()
        for name in ("upstream_ids", "downstream_ids"):
            if name in self.relationships.column_names:
                for values in self.relationships[name].to_pylist():
                    neighbors.update(values)
        return frozenset(neighbors - selected)
