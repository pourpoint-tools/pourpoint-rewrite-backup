"""Whole-footprint reverse-D8 refinement under vector/raster equivalence.

Vector inputs and outputs use WGS84. Native cells are admitted by center using
GDAL's rasterization convention. Pixel polygons are clipped to the vector mask;
source contributions partition that same geometry at original unit boundaries.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import floor
from typing import Literal, cast, overload

import numpy as np
import numpy.typing as npt
import shapely
from pyproj import Transformer
from rasterio.features import geometry_mask, shapes
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.geometry.base import BaseGeometry

from pourpoint._batch import execute, workers
from pourpoint.fabric import HydroFabric
from pourpoint.features import Attributes, Subbasin
from pourpoint.rasters import MissingRastersError, RasterCoverageError, RasterGrid, RasterWindow
from pourpoint.results import BatchResult, OperationError, Target


@dataclass(frozen=True)
class SubbasinContribution:
    source: Subbasin
    geometry: BaseGeometry

    @property
    def id(self) -> int:
        return self.source.id

    @property
    def attributes(self) -> Attributes:
        return self.source.attributes


@dataclass(frozen=True)
class OutletCell:
    grid: RasterGrid
    row: int
    col: int

    @property
    def center(self) -> Point:
        return Point(self.grid.transform @ (self.col + 0.5, self.row + 0.5))

    @property
    def geometry(self) -> Polygon:
        return Polygon(
            [self.grid.transform @ (self.col + x, self.row + y) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
        )


@dataclass(frozen=True)
class RasterSubset:
    window: RasterWindow
    mask: npt.NDArray[np.bool_]
    band: Literal["direction", "accumulation"]


@dataclass(frozen=True)
class RefinementRasters:
    direction: RasterSubset
    accumulation: RasterSubset


@dataclass(frozen=True)
class GranularRefinement:
    subbasins: list[SubbasinContribution]
    outlet_cell: OutletCell
    rasters: RefinementRasters


@dataclass(frozen=True)
class FootprintRefinement:
    footprint: Polygon | MultiPolygon
    outlet_cell: OutletCell
    rasters: RefinementRasters


type BasinPart = Subbasin | SubbasinContribution


def _polygonal(geometry: BaseGeometry) -> Polygon | MultiPolygon:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    polygons = [part for part in shapely.get_parts(geometry) if isinstance(part, (Polygon, MultiPolygon))]
    return cast(Polygon | MultiPolygon, shapely.union_all(polygons)) if polygons else Polygon()


@overload
def merge(*, subbasins: Sequence[BasinPart]) -> Polygon | MultiPolygon: ...


@overload
def merge(*, subbasins: Sequence[Sequence[BasinPart]]) -> list[Polygon | MultiPolygon]: ...


def merge(
    *, subbasins: Sequence[BasinPart] | Sequence[Sequence[BasinPart]]
) -> Polygon | MultiPolygon | list[Polygon | MultiPolygon]:
    """Union one collection; nested collections remain separate. Empty means empty polygon."""
    if not subbasins:
        return Polygon()
    if isinstance(subbasins[0], (Subbasin, SubbasinContribution)):
        parts = cast(Sequence[BasinPart], subbasins)
        return _polygonal(shapely.union_all([part.geometry for part in parts]))
    groups = cast(Sequence[Sequence[BasinPart]], subbasins)
    return [merge(subbasins=group) for group in groups]


def _trace(window: RasterWindow, mask: npt.NDArray[np.bool_], row: int, col: int) -> npt.NDArray[np.bool_]:
    selected = np.zeros(mask.shape, dtype=np.bool_)
    selected[row, col] = True
    stack = [(row, col)]
    # Incoming neighbor position is the inverse of its outgoing direction.
    offsets = ((0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1))
    height, width = mask.shape
    while stack:
        row, col = stack.pop()
        for code, (dy, dx) in enumerate(offsets, 1):
            y, x = row - dy, col - dx
            if (
                0 <= y < height
                and 0 <= x < width
                and mask[y, x]
                and not selected[y, x]
                and window.direction_valid[y, x]
                and window.direction[y, x] == code
            ):
                selected[y, x] = True
                stack.append((y, x))
    return selected


@overload
def _refine(
    *, fabric: HydroFabric, target: Point, subbasins: Sequence[Subbasin], footprint: None = None, window_size: int = 3
) -> GranularRefinement: ...


@overload
def _refine(
    *, fabric: HydroFabric, target: Point, footprint: BaseGeometry, subbasins: None = None, window_size: int = 3
) -> FootprintRefinement: ...


def _refine(
    *,
    fabric: HydroFabric,
    target: Point,
    subbasins: Sequence[Subbasin] | None = None,
    footprint: BaseGeometry | None = None,
    window_size: int = 3,
) -> GranularRefinement | FootprintRefinement:
    """Select the largest valid local accumulation, ties in source row/column order.

    Outlet selection needs both valid bands. Upstream tracing needs only valid
    direction, and treats zero and signed negative directions as terminals.
    """
    if type(window_size) is not int or window_size <= 0 or window_size % 2 != 1:
        raise ValueError("window_size must be a positive odd integer")
    if (subbasins is None) == (footprint is None):
        raise ValueError("provide exactly one of subbasins or footprint")
    if not isinstance(target, Point) or target.is_empty:
        raise ValueError("refinement target must be a nonempty Point")
    combined = merge(subbasins=subbasins) if subbasins is not None else footprint
    if not isinstance(combined, (Polygon, MultiPolygon)) or combined.is_empty:
        raise OperationError("refinement.empty_mask", "refinement requires a nonempty polygon mask")
    if not combined.covers(target):
        raise OperationError("refinement.outside_mask", "target lies outside the vector mask")
    try:
        request = fabric.rasters.prepare(combined.bounds)
        window = fabric.rasters.read(request)
    except MissingRastersError as error:
        raise OperationError("refinement.missing_rasters", str(error)) from error
    except RasterCoverageError as error:
        raise OperationError("refinement.no_coverage", str(error)) from error
    to_native = Transformer.from_crs("EPSG:4326", window.crs, always_xy=True)
    native_mask = shapely.transform(combined, to_native.transform, interleaved=False)
    mask = geometry_mask([native_mask], out_shape=window.direction.shape, transform=window.transform, invert=True)
    native_point = to_native.transform(target.x, target.y)
    x, y = ~window.transform @ native_point
    col, row = floor(x), floor(y)
    half = window_size // 2
    top, left = max(0, row - half), max(0, col - half)
    bottom, right = min(mask.shape[0], row + half + 1), min(mask.shape[1], col + half + 1)
    valid = (
        mask[top:bottom, left:right]
        & window.direction_valid[top:bottom, left:right]
        & window.accumulation_valid[top:bottom, left:right]
    )
    ys, xs = np.nonzero(valid)
    if not len(ys):
        raise OperationError("refinement.no_outlet", "local window contains no valid outlet cell inside the mask")
    index = int(np.argmax(window.accumulation[ys + top, xs + left]))
    row, col = int(ys[index]) + top, int(xs[index]) + left
    selected = _trace(window, mask, row, col)
    polygons = [
        shape(geometry)
        for geometry, value in shapes(selected.astype(np.uint8), mask=selected, transform=window.transform)
        if value == 1
    ]
    native_geometry = shapely.union_all(polygons)
    to_wgs84 = Transformer.from_crs(window.crs, "EPSG:4326", always_xy=True)
    geometry = _polygonal(
        shapely.transform(native_geometry, to_wgs84.transform, interleaved=False).intersection(combined)
    )
    cell = OutletCell(request.grid, row + request.row, col + request.col)
    rasters = RefinementRasters(RasterSubset(window, mask, "direction"), RasterSubset(window, mask, "accumulation"))
    if subbasins is None:
        return FootprintRefinement(geometry, cell, rasters)
    contributions = []
    for source in subbasins:
        piece = _polygonal(geometry.intersection(source.geometry))
        if not piece.is_empty:
            contributions.append(SubbasinContribution(source, piece))
    return GranularRefinement(contributions, cell, rasters)


@overload
def refine(
    *,
    fabric: HydroFabric,
    subbasins: BatchResult[list[Subbasin]],
    target: None = None,
    footprint: None = None,
    window_size: int = 3,
    n_jobs: int = 1,
) -> BatchResult[GranularRefinement]: ...


@overload
def refine(
    *,
    fabric: HydroFabric,
    target: Point,
    subbasins: Sequence[Subbasin],
    footprint: None = None,
    window_size: int = 3,
    n_jobs: int = 1,
) -> GranularRefinement: ...


@overload
def refine(
    *,
    fabric: HydroFabric,
    target: Point,
    footprint: BaseGeometry,
    subbasins: None = None,
    window_size: int = 3,
    n_jobs: int = 1,
) -> FootprintRefinement: ...


def refine(
    *,
    fabric: HydroFabric,
    target: Point | None = None,
    subbasins: Sequence[Subbasin] | BatchResult[list[Subbasin]] | None = None,
    footprint: BaseGeometry | None = None,
    window_size: int = 3,
    n_jobs: int = 1,
) -> GranularRefinement | FootprintRefinement | BatchResult[GranularRefinement]:
    """Refine one point or an identified selection batch without losing identity."""
    workers(n_jobs)
    if type(window_size) is not int or window_size < 1 or window_size % 2 != 1:
        raise ValueError("window_size must be a positive odd integer")
    fabric._ensure_open()
    if isinstance(subbasins, BatchResult):
        if target is not None or footprint is not None:
            raise ValueError("selection batches carry their own targets and footprints")

        def one(reader: HydroFabric, item: Target) -> GranularRefinement | None:
            units = subbasins.results[item.index]
            if units is None:
                return None
            if not isinstance(item.geometry, Point):
                raise OperationError("refinement.point_required", "refinement requires a point target")
            return _refine(fabric=reader, subbasins=units, target=item.geometry, window_size=window_size)

        result = execute(fabric, subbasins.targets, one, n_jobs)
        return BatchResult(
            result.results,
            subbasins.targets,
            sorted([*subbasins.issues, *result.issues], key=lambda issue: issue.index),
        )
    if target is None:
        raise TypeError("target is required for single refinement")
    if subbasins is not None:
        if footprint is not None:
            raise ValueError("provide exactly one of subbasins or footprint")
        return _refine(fabric=fabric, target=target, subbasins=subbasins, window_size=window_size)
    if footprint is None:
        raise ValueError("provide exactly one of subbasins or footprint")
    return _refine(fabric=fabric, target=target, footprint=footprint, window_size=window_size)
