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
    """Select intersecting whole units and their inclusive upstream closure."""
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
    """Compose explicit operations without a separate delineation algorithm.

    Targets and supplied stream geometries use WGS84 longitude/latitude.
    ``radius`` is metres; ``window_size`` is an odd number of native cells.
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
