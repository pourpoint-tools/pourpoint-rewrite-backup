"""Typed eager operation results and target-local failures."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from pourpoint.features import Subbasin

if TYPE_CHECKING:
    from pourpoint.refinement import OutletCell, RefinementRasters, SubbasinContribution


class OperationError(Exception):
    """An expected failure for one target, shared by single and batch calls."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class Target:
    index: int
    name: str
    geometry: BaseGeometry


@dataclass(frozen=True)
class BatchIssue:
    index: int
    name: str
    code: str
    reason: str


@dataclass(frozen=True)
class BatchResult[T]:
    results: list[T | None]
    targets: list[Target]
    issues: list[BatchIssue]


@dataclass(frozen=True)
class DelineationResult:
    subbasins: list[Subbasin | SubbasinContribution]
    geometry: Polygon | MultiPolygon | None
    outlet_cell: OutletCell | None
    rasters: RefinementRasters | None
    original_target: BaseGeometry
    target: BaseGeometry
    provenance: Mapping[str, object]
