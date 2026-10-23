"""Rejected array-valued geodesic prototype; loaded only by the warm study."""

from __future__ import annotations

from math import isfinite, sqrt

import shapely
from pyproj import Geod
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry

from pourpoint.features import River, RiverNetwork
from pourpoint.results import OperationError
from pourpoint.rivers import SnapGeometry


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
    target_x, target_y = target.x, target.y
    best_distance = float("inf")
    best: Point | None = None
    ratio = (sqrt(5) - 1) / 2
    import numpy as np

    for geometry in geometries:
        if geometry.covers(target):
            return target
        segments = [
            (a, b)
            for part in shapely.get_parts(geometry)
            for a, b in zip(list(part.coords), list(part.coords)[1:], strict=False)
        ]
        if not segments:
            continue
        starts = np.array([a[:2] for a, _ in segments])
        ends = np.array([b[:2] for _, b in segments])
        count = len(segments)
        target_xs = np.full(count, target_x)
        target_ys = np.full(count, target_y)

        def distances(
            t: np.ndarray,
            starts: np.ndarray = starts,
            ends: np.ndarray = ends,
            target_xs: np.ndarray = target_xs,
            target_ys: np.ndarray = target_ys,
        ) -> np.ndarray:
            xy = starts + t[:, None] * (ends - starts)
            return geod.inv(target_xs, target_ys, xy[:, 0], xy[:, 1])[2]

        low, high = np.zeros(count), np.ones(count)
        for _ in range(64):
            left, right = high - ratio * (high - low), low + ratio * (high - low)
            nearer = distances(left) <= distances(right)
            high = np.where(nearer, right, high)
            low = np.where(nearer, low, left)
        choices = np.array([np.zeros(count), np.ones(count), (low + high) / 2])
        values = np.array([distances(t) for t in choices])
        indices = np.argmin(values, axis=0)
        for i in range(count):
            value = values[indices[i], i]
            if value < best_distance:
                t = choices[indices[i], i]
                xy = starts[i] + t * (ends[i] - starts[i])
                best_distance, best = value, Point(xy)
    if best is None or best_distance > radius:
        raise OperationError("snap.no_match", "No supplied stream is in range")
    return best
