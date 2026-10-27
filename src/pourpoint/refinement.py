"""Whole-footprint reverse-D8 refinement under vector/raster equivalence.

Vector inputs and outputs use WGS84. Native cells are admitted by center using
GDAL's rasterization convention. Pixel polygons are clipped to the vector mask;
source contributions partition that same geometry at original unit boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
    """A contributing polygonal portion retaining its whole source unit.

    Attributes
    ----------
    source
        Original sub-basin with unchanged attributes, metadata and source WKB.
    geometry
        Derived contribution in EPSG:4326. Source area and other quantities are
        not recomputed for this geometry. Materialized portions survive closure."""

    source: Subbasin
    geometry: BaseGeometry

    @property
    def id(self) -> int:
        """Original source sub-basin ID."""
        return self.source.id

    @property
    def attributes(self) -> Attributes:
        """Unchanged source attributes, including nulls and original quantities."""
        return self.source.attributes


@dataclass(frozen=True)
class OutletCell:
    """A selected cell addressed on the original native raster grid.

    Attributes
    ----------
    grid
        Source grid identity, CRS, affine transform and dimensions.
    row, col
        Zero-based source-grid indices, not offsets within a read window."""

    grid: RasterGrid
    row: int
    col: int

    @property
    def center(self) -> Point:
        """Cell center in the native grid CRS; a display location, not a station correction."""
        return Point(self.grid.transform @ (self.col + 0.5, self.row + 0.5))

    @property
    def geometry(self) -> Polygon:
        """Cell boundary polygon derived from the native grid affine transform."""
        return Polygon(
            [self.grid.transform @ (self.col + x, self.row + y) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
        )


@dataclass(frozen=True)
class RasterSubset:
    """Materialized native raster window with the refinement mask.

    Attributes
    ----------
    window
        Retained paired-band values, validity, native CRS and affine transform.
    mask
        Boolean array aligned with the window; True admits cells in the whole
        vector delineation mask, not just cells in the final watershed.
    band
        Selects direction or accumulation values for export.

    Notes
    -----
    Export keeps source values at valid masked cells and uses source nodata
    elsewhere. The window is already read, not a deferred fabric request."""

    window: RasterWindow
    mask: npt.NDArray[np.bool_]
    band: Literal["direction", "accumulation"]


@dataclass(frozen=True)
class RefinementRasters:
    """Paired raster subsets over the same whole vector mask.

    Attributes
    ----------
    direction, accumulation
        Native-grid subsets for the corresponding bands. Neither is restricted
        to the final contributing watershed. Source values and nodata are retained."""

    direction: RasterSubset
    accumulation: RasterSubset


@dataclass(frozen=True)
class GranularRefinement:
    """Point refinement partitioned at original sub-basin boundaries.

    Attributes
    ----------
    subbasins
        Nonempty contributing portions in source input order, with unchanged
        source identity and attributes. Geometries use EPSG:4326.
    outlet_cell
        Selected source-grid cell, whose geometry uses the native raster CRS.
    rasters
        Materialized band subsets over the complete combined vector mask.
    provenance
        Source manifest declarations; not proof of vector/raster equivalence."""

    subbasins: list[SubbasinContribution]
    outlet_cell: OutletCell
    rasters: RefinementRasters
    provenance: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FootprintRefinement:
    """Point refinement returned as one merged footprint.

    Attributes
    ----------
    footprint
        Polygon or MultiPolygon contribution in EPSG:4326, not a fabricated
        source sub-basin. Equivalent to merging granular refinement of the
        same combined mask and target.
    outlet_cell
        Selected source-grid cell in the native raster CRS.
    rasters
        Materialized band subsets over the complete input vector mask.
    provenance
        Source manifest declarations; not proof of vector/raster equivalence."""

    footprint: Polygon | MultiPolygon
    outlet_cell: OutletCell
    rasters: RefinementRasters
    provenance: Mapping[str, object] = field(default_factory=dict)


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
    """Merge sub-basin geometries without creating a new source feature.

    Parameters
    ----------
    subbasins
        Whole units or contributions forming one footprint, or nested groups
        representing independent footprints. Geometries use EPSG:4326.

    Returns
    -------
    Polygon or MultiPolygon or list
        Polygonal union for one group; an ordered list of unions for nested
        groups. An empty outer input returns an empty Polygon, not a list.
        Empty inner groups produce empty Polygons. No source attributes are
        aggregated, and no source access or geometry repair is performed.
    """
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
        raise OperationError("refinement.point_required", "refinement requires a nonempty point target")
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
        return FootprintRefinement(geometry, cell, rasters, fabric.manifest.declarations)
    contributions = []
    for source in subbasins:
        piece = _polygonal(geometry.intersection(source.geometry))
        if not piece.is_empty:
            contributions.append(SubbasinContribution(source, piece))
    return GranularRefinement(contributions, cell, rasters, fabric.manifest.declarations)


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
    """Trace incoming native D8 flow inside a complete vector mask.

    Parameters
    ----------
    fabric
        Open fabric supplying the native direction/accumulation pair.
    target
        Nonempty point in EPSG:4326 covered by the mask. Omit for a selection
        batch, which carries its own target geometries.
    subbasins
        Whole source units forming one mask, or a selection `BatchResult`.
        Mutually exclusive with ``footprint``. Batch identity, order and prior
        issues are preserved; failed slots are not retried.
    footprint
        Nonempty Polygon or MultiPolygon in EPSG:4326, used as one mask.
        Supply exactly one mask form. Batch footprints come from ``subbasins``.
    window_size
        Positive odd search width in native cells around the cell containing
        the target. Size 1 permits no move beyond that cell. Selects the highest
        valid accumulation inside the mask, breaking ties by source row then
        column. Both bands must be valid for outlet selection.
    n_jobs
        Batch worker limit: 1 is sequential, -1 uses available CPU capacity,
        and positive values bound worker-owned readers.

    Returns
    -------
    GranularRefinement or FootprintRefinement or BatchResult
        Contributions for sub-basin input or one footprint for geometry input,
        plus the selected native outlet cell and materialized raster subsets.
        Empty source pieces are omitted. Both forms trace the same combined
        mask; merging granular contributions yields the footprint result.

    Raises
    ------
    OperationError
        ``refinement.point_required`` for a nonpoint or empty point geometry,
        ``refinement.empty_mask`` for an empty or nonpolygon mask,
        ``refinement.outside_mask`` when the point is not covered,
        ``refinement.missing_rasters`` for an absent raster pair,
        ``refinement.no_coverage`` for inadequate raster coverage, or
        ``refinement.no_outlet`` when the local search has no valid masked cell.
        Batches retain these failures as issues with None slots.
    ValueError
        Invalid window/worker count or mutually incompatible mask arguments.
        Integrity and unexpected errors also propagate, including in batches.
    TypeError
        A single call omits ``target``.

    Notes
    -----
    Assumes the whole vector mask equals the raster watershed at the terminal
    unit outlet, and that the requested point drains to that outlet. Execution
    does not verify this scientific equivalence. The default local search is a
    practical choice, not a universal optimum; larger windows can switch branches.

    Mask cells use GDAL center-based rasterization. Reverse D8 tracing spans
    the entire mask and needs only valid direction, not valid accumulation;
    zero and signed negative directions are terminal. Paths cannot leave the
    mask. Native direction pointers are never warped. Selected cell polygons
    are transformed to EPSG:4326 and clipped to the vector mask, then partitioned
    at source boundaries for granular output. No hole filling is applied.
    Materialized vectors survive fabric closure; perform source-dependent
    operations and export within the fabric context.
    """
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
