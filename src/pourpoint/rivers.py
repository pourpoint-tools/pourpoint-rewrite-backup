"""Combined-footprint river selection and optional nearest-ground-distance snapping."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite, sqrt
from typing import cast

import shapely
from pyproj import Geod
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring

from pourpoint.fabric import HydroFabric
from pourpoint.features import River, RiverNetwork, Subbasin
from pourpoint.refinement import SubbasinContribution
from pourpoint.results import OperationError


@dataclass(frozen=True)
class ClippedRiver(River):
    """A river portion; Arrow values and WKB still describe the source feature."""

    portion: BaseGeometry

    @property
    def geometry(self) -> BaseGeometry:
        return self.portion

    @property
    def source(self) -> River:
        return River(self.table, self.row)


def _linework(geometry: BaseGeometry) -> BaseGeometry:
    if isinstance(geometry, (LineString, MultiLineString)):
        return geometry
    return shapely.union_all(
        [part for part in shapely.get_parts(geometry) if isinstance(part, (LineString, MultiLineString))]
    )


def _oriented_line(river: River, network: RiverNetwork) -> LineString:
    geometry = river.geometry
    parts = list(geometry.geoms) if isinstance(geometry, MultiLineString) else [geometry]
    coordinates: list[tuple[float, ...]] = []
    for part in parts:
        values = list(part.coords)
        if coordinates and coordinates[-1] != values[0]:
            raise OperationError("rivers.ambiguous_direction", f"River {river.id} has disconnected or unordered parts")
        coordinates.extend(values if not coordinates else values[1:])
    line = LineString(coordinates)
    orientation = river.attributes.get("geometry_orientation", 0)
    if orientation == 1:
        return line
    if orientation == 2:
        return shapely.reverse(line)
    node_ids: dict[str, list[int]] = {}
    rows = network.relationships.to_pylist()
    row = next(item for item in rows if item["id"] == river.id)
    for end in ("upstream", "downstream"):
        ids = row.get(f"{end}_node_ids", [])
        source_id = river.attributes.get(f"{end}_node_id")
        node_ids[end] = ids or ([source_id] if isinstance(source_id, int) else [])
    votes: set[bool] = set()
    for node in network.nodes:
        for end, ids in node_ids.items():
            if node.id not in ids:
                continue
            at_start = node.geometry.equals(Point(coordinates[0]))
            at_end = node.geometry.equals(Point(coordinates[-1]))
            if at_start != at_end:
                votes.add(at_start if end == "upstream" else at_end)
    if len(votes) != 1:
        raise OperationError(
            "rivers.ambiguous_direction", f"River {river.id} lacks unambiguous directed endpoint geometry"
        )
    return line if votes.pop() else shapely.reverse(line)


def select_rivers(
    *,
    fabric: HydroFabric,
    target: BaseGeometry | Subbasin | SubbasinContribution | Sequence[Subbasin | SubbasinContribution],
    level: int = 1,
    include_downstream: bool = False,
) -> RiverNetwork:
    """Clip to one combined EPSG:4326 footprint, optionally retaining all downstream flow."""
    if type(level) is not int or level < 0:
        raise ValueError("level must be a nonnegative integer")
    if isinstance(target, BaseGeometry):
        footprint = target
    elif isinstance(target, (Subbasin, SubbasinContribution)):
        footprint = target.geometry
    else:
        footprint = shapely.union_all([part.geometry for part in target])
    if not footprint.is_empty and not isinstance(footprint, (Polygon, MultiPolygon)):
        raise ValueError("river target must be a polygon footprint")
    if "rivers" not in fabric.manifest.families:
        raise OperationError("rivers.unavailable", "Source does not supply rivers")
    if footprint.is_empty:
        return fabric.river_network([])
    table = fabric.candidates("rivers", cast(tuple[float, float, float, float], footprint.bounds), levels=[level])
    pieces: dict[int, BaseGeometry] = {}
    for row in range(table.num_rows):
        river = River(table, row)
        clipped = _linework(river.geometry.intersection(footprint))
        if not clipped.is_empty:
            pieces[river.id] = clipped
    network = fabric.river_network(list(pieces))
    if include_downstream and pieces:
        declaration = fabric.manifest.relationships.get("rivers", {})
        columns = cast(dict[str, object], declaration.get("columns", {}))
        if "downstream_ids" not in columns:
            raise OperationError(
                "rivers.connectivity_unavailable", "Source does not supply downstream river connectivity"
            )
        for river in network.rivers:
            if not footprint.covers(river.geometry):
                line = _oriented_line(river, network)
                clipped = pieces[river.id]
                positions = [line.project(Point(xy)) for part in shapely.get_parts(clipped) for xy in part.coords]
                pieces[river.id] = substring(line, min(positions), line.length)
        all_ids = fabric.traverse("rivers", list(pieces), relationship="downstream_ids")
        extended = fabric.river_network(all_ids)
        # A source feature reached through another selected feature is wholly downstream.
        downstream = {neighbor for row in network.relationships["downstream_ids"].to_pylist() for neighbor in row}
        reached = (
            set(fabric.traverse("rivers", sorted(downstream), relationship="downstream_ids")) if downstream else set()
        )
        for river in extended.rivers:
            if river.attributes.get("level") != level:
                raise ValueError("downstream relationship crosses river levels")
            if river.id not in pieces or river.id in reached:
                pieces[river.id] = river.geometry
        network = extended
    return RiverNetwork(
        [ClippedRiver(river.table, river.row, pieces[river.id]) for river in network.rivers],
        network.relationships,
        network.nodes,
        network.node_relationships,
        network.declarations,
    )


type SnapGeometry = RiverNetwork | River | BaseGeometry | Sequence[River]


def snap(*, target: BaseGeometry, onto: SnapGeometry, radius: float) -> Point:
    """Nearest point on supplied EPSG:4326 line segments, measured on WGS84 in metres.

    Segment positions retain source straight longitude/latitude interpolation, not
    great-circle arcs. Input vertices must be finite WGS84 longitude/latitude values.
    Segments spanning 180 degrees or more of longitude are rejected: split stream
    geometry at the antimeridian before calling, rather than relying on unwrapping.
    Golden-section minimization is intended for local stream segments; it is not a
    global optimizer for arbitrary globe-spanning lines. Distance is geodesic, not
    degree distance or stream rank; equal minima retain input order.
    """
    if not isinstance(target, Point) or target.is_empty or not all(isfinite(v) for v in target.coords[0]):
        raise OperationError("snap.point_required", "snap target must be a nonempty finite Point")
    if not -180 <= target.x <= 180 or not -90 <= target.y <= 90:
        raise OperationError("snap.point_required", "snap target must use WGS84 longitude/latitude")
    if not isfinite(radius) or radius < 0:
        raise ValueError("radius must be finite and nonnegative")
    if isinstance(onto, RiverNetwork):
        geometries = [river.geometry for river in onto.rivers]
    elif isinstance(onto, River):
        geometries = [onto.geometry]
    elif isinstance(onto, BaseGeometry):
        geometries = [onto]
    else:
        geometries = [river.geometry for river in onto]
    for geometry in geometries:
        if not isinstance(geometry, (LineString, MultiLineString)):
            raise ValueError("snap requires stream line geometry")
        for part in shapely.get_parts(geometry):
            coordinates = list(part.coords)
            if any(
                not (isfinite(x) and isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90) for x, y, *_ in coordinates
            ):
                raise ValueError("snap streams must use finite WGS84 longitude/latitude")
            if any(abs(a[0] - b[0]) >= 180 for a, b in zip(coordinates, coordinates[1:], strict=False)):
                raise ValueError("split antimeridian-spanning stream segments before snapping")
    geod = Geod(ellps="WGS84")
    best_distance = float("inf")
    best: Point | None = None
    ratio = (sqrt(5) - 1) / 2
    for geometry in geometries:
        if geometry.covers(target):
            return target
        for part in shapely.get_parts(geometry):
            coordinates = list(part.coords)
            for start, end in zip(coordinates, coordinates[1:], strict=False):

                def point(t: float, start: tuple[float, ...] = start, end: tuple[float, ...] = end) -> Point:
                    return Point(start[0] + t * (end[0] - start[0]), start[1] + t * (end[1] - start[1]))

                def distance(t: float) -> float:
                    p = point(t)
                    return geod.inv(target.x, target.y, p.x, p.y)[2]

                low, high = 0.0, 1.0
                for _ in range(64):
                    left, right = high - ratio * (high - low), low + ratio * (high - low)
                    if distance(left) <= distance(right):
                        high = right
                    else:
                        low = left
                t = min((0.0, 1.0, (low + high) / 2), key=distance)
                value = distance(t)
                if value < best_distance:
                    best_distance, best = value, point(t)
    if best is None or best_distance > radius:
        raise OperationError("snap.no_match", "No supplied stream is in range")
    return best
