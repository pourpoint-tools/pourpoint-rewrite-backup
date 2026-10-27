"""Whole-unit upstream selection and eager watershed composition."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from pourpoint.refinement import SubbasinContribution
    from pourpoint.rivers import SnapGeometry

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from pourpoint._batch import Targets, execute, identify, workers
from pourpoint.fabric import HydroFabric
from pourpoint.features import Subbasin
from pourpoint.results import BatchResult, DelineationResult, OperationError


def _level(level: int) -> None:
    if type(level) is not int or level < 0:
        raise ValueError("level must be a nonnegative integer")


def _select(*, fabric: HydroFabric, target: BaseGeometry, level: int) -> list[Subbasin]:
    if not isinstance(target, BaseGeometry):
        raise TypeError("target must be a Shapely geometry")
    if target.is_empty:
        return []
    if not target.is_valid:
        raise OperationError("selection.invalid_target", "target geometry is invalid")
    connectivity = fabric.manifest.declarations.get("catchment_connectivity", [])
    if isinstance(connectivity, (list, tuple)):
        for entry in connectivity:
            if isinstance(entry, dict) and entry.get("level") == level and entry.get("status") == "not_supplied":
                raise OperationError(
                    "selection.connectivity_unavailable", f"upstream connectivity is not supplied for level {level}"
                )
    declaration = fabric.manifest.relationships.get("catchments", {})
    columns = declaration.get("columns", {})
    if not isinstance(columns, dict) or "upstream_ids" not in columns:
        raise OperationError("selection.connectivity_unavailable", "upstream connectivity is not supplied")
    table = fabric.candidates("catchments", target.bounds, levels=[level])
    seeds = sorted(
        Subbasin(table, row).id for row in range(table.num_rows) if Subbasin(table, row).geometry.intersects(target)
    )
    if not seeds:
        raise OperationError("selection.no_coverage", f"no catchment coverage for target at level {level}")
    # Shared polygon edges belong to the smallest supplied ID for point targets.
    if isinstance(target, Point):
        seeds = seeds[:1]
    units = fabric.subbasins(fabric.traverse("catchments", seeds))
    if any(unit.attributes["level"] != level for unit in units):
        raise ValueError("catchment flow relationship crosses levels")
    return units


@overload
def select_upstream_subbasins(
    *, fabric: HydroFabric, target: BaseGeometry, level: int = 1, n_jobs: int = 1
) -> list[Subbasin]: ...


@overload
def select_upstream_subbasins(
    *, fabric: HydroFabric, target: Targets, level: int = 1, n_jobs: int = 1
) -> BatchResult[list[Subbasin]]: ...


def select_upstream_subbasins(
    *, fabric: HydroFabric, target: BaseGeometry | Targets, level: int = 1, n_jobs: int = 1
) -> list[Subbasin] | BatchResult[list[Subbasin]]:
    """Select whole sub-basins approximating land draining to a target.

    Parameters
    ----------
    fabric
        Open HFX fabric supplying catchments and upstream connectivity.
    target
        EPSG:4326 geometry, or a dictionary of named geometries or list for a
        batch. A multipart geometry is one target. Regions include boundary
        intersections. A point on a shared boundary belongs to the smallest
        supplied catchment ID; river associations do not determine ownership.
    level
        Nonnegative catchment level, selected explicitly without fallback.
    n_jobs
        Batch worker limit: 1 is sequential, -1 uses available CPU capacity,
        and positive values bound worker-owned readers. Single calls are serial.

    Returns
    -------
    list or BatchResult
        Whole intersecting units and their inclusive upstream closure. An empty
        geometry succeeds with an empty list. Batch slots preserve input order;
        dictionary keys are names, while list entries get new UUID names on
        each call. Duplicate geometries remain independent targets.

    Raises
    ------
    OperationError
        ``selection.invalid_target`` for invalid geometry,
        ``selection.no_coverage`` for a nonempty target without coverage, or
        ``selection.connectivity_unavailable`` for unsupplied connectivity.
        Batches retain these failures as issues with None result slots.
    ValueError
        Invalid level or worker count, or a flow relationship crossing levels.
    TypeError
        Invalid target type or batch names.

    Notes
    -----
    This is whole-unit selection, not exact-point raster delineation. Returned
    vectors survive fabric closure. Integrity, configuration and unexpected
    failures propagate even in batches; they are not empty selections.
    """
    _level(level)
    workers(n_jobs)
    fabric._ensure_open()
    if isinstance(target, (dict, list)):
        return execute(
            fabric,
            identify(target),
            lambda reader, geometry: _select(fabric=reader, target=geometry.geometry, level=level),
            n_jobs,
        )
    return _select(fabric=fabric, target=target, level=level)


@overload
def delineate(
    *,
    fabric: HydroFabric,
    target: BaseGeometry,
    level: int = 1,
    snap_onto: SnapGeometry | None = None,
    radius: float = 100.0,
    refine: bool = False,
    merge: bool = True,
    window_size: int = 3,
    n_jobs: int = 1,
) -> DelineationResult: ...


@overload
def delineate(
    *,
    fabric: HydroFabric,
    target: Targets,
    level: int = 1,
    snap_onto: SnapGeometry | None = None,
    radius: float = 100.0,
    refine: bool = False,
    merge: bool = True,
    window_size: int = 3,
    n_jobs: int = 1,
) -> BatchResult[DelineationResult]: ...


def delineate(
    *,
    fabric: HydroFabric,
    target: BaseGeometry | Targets,
    level: int = 1,
    snap_onto: SnapGeometry | None = None,
    radius: float = 100.0,
    refine: bool = False,
    merge: bool = True,
    window_size: int = 3,
    n_jobs: int = 1,
) -> DelineationResult | BatchResult[DelineationResult]:
    """Compose snapping, upstream selection, optional refinement and merging.

    Parameters
    ----------
    fabric
        Open fabric used for all source access.
    target
        EPSG:4326 geometry, named dictionary or list of geometries. Batch
        naming and whole-unit selection follow `select_upstream_subbasins`.
    level
        Explicit catchment level; no fallback or automatic finest-level choice.
    snap_onto
        Optional supplied stream geometry accepted by `snap`. Snapping occurs
        before selection and requires a point; it is never inferred.
    radius
        Maximum snap distance in WGS84 ground metres, finite and nonnegative.
        Validated even when snapping is disabled.
    refine
        Request raster refinement of the adjusted point under the equivalence
        assumption described by `pourpoint.refinement.refine`. Failure does
        not fall back to a coarse result.
    merge
        Populate the merged geometry as well as retaining individual sub-basins.
    window_size
        Positive odd native-cell outlet search width. Validated even without
        refinement. This is a practical choice, not a universal scientific optimum.
    n_jobs
        Batch worker limit: 1 is sequential, -1 uses available CPU capacity,
        and positive values bound worker-owned readers.

    Returns
    -------
    DelineationResult or BatchResult
        Whole units or refined contributions, optional merged geometry, original
        and adjusted targets, source declarations, and any selected outlet and
        raster subsets. Batch identity and order are retained.

    Raises
    ------
    OperationError
        Single-target failures use the codes of `snap`,
        `select_upstream_subbasins` and `pourpoint.refinement.refine`.
        Batches record them as issues and None slots, not coarse successes.
    ValueError
        Invalid level, worker count, radius or window size, or invalid supplied
        stream geometry. Shared configuration and integrity failures propagate
        rather than becoming batch issues.
    TypeError
        Invalid target types or nonboolean refinement/merge controls.

    Notes
    -----
    No separate delineation algorithm is used. Vector outputs are materialized
    and remain usable after fabric closure. Vector snapping and raster outlet
    adjustment are distinct: the latter is retained in ``outlet_cell``.
    """
    _level(level)
    workers(n_jobs)
    if type(window_size) is not int or window_size < 1 or window_size % 2 != 1:
        raise ValueError("window_size must be a positive odd integer")
    if type(refine) is not bool or type(merge) is not bool:
        raise TypeError("refine and merge must be boolean")
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("radius must be finite and nonnegative")
    fabric._ensure_open()

    def one(reader: HydroFabric, geometry: BaseGeometry) -> DelineationResult:
        adjusted = geometry
        if snap_onto is not None:
            from pourpoint.rivers import snap

            adjusted = snap(target=geometry, onto=snap_onto, radius=radius)
        units = _select(fabric=reader, target=adjusted, level=level)
        outlet = None
        rasters = None
        parts: list[Subbasin | SubbasinContribution] = list(units)
        if refine:
            from pourpoint.refinement import refine as refine_point

            if not isinstance(adjusted, Point):
                raise OperationError("refinement.point_required", "refinement requires a point target")
            refined = refine_point(fabric=reader, subbasins=units, target=adjusted, window_size=window_size)
            parts = list(refined.subbasins)
            outlet, rasters = refined.outlet_cell, refined.rasters
        merged = None
        if merge:
            from pourpoint.refinement import merge as merge_subbasins

            merged = merge_subbasins(subbasins=parts)
        return DelineationResult(parts, merged, outlet, rasters, geometry, adjusted, reader.manifest.declarations)

    if isinstance(target, (dict, list)):
        return execute(fabric, identify(target), lambda reader, item: one(reader, item.geometry), n_jobs)
    return one(fabric, target)
