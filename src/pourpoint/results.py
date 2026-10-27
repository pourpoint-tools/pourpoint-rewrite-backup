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
    """An expected target-local failure shared by single and batch calls.

    Attributes
    ----------
    code
        Stable machine-readable operation code; conditions are documented on
        the operation that raises it.
    reason
        Human-readable explanation, also used as the exception message.

    Notes
    -----
    Batch operations collect this exception only. Configuration, integrity and
    unexpected errors propagate rather than becoming target issues."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class Target:
    """One accepted batch target, independent of source feature identity.

    Attributes
    ----------
    index
        Zero-based position in the original input order.
    name
        Dictionary key or UUID generated once for a list entry. Preserved by
        subsequent batch refinement and export; list calls get new names.
    geometry
        Original target geometry in EPSG:4326, including multipart targets."""

    index: int
    name: str
    geometry: BaseGeometry


@dataclass(frozen=True)
class BatchIssue:
    """An expected failure attached to one batch input.

    Attributes
    ----------
    index, name
        Original target position and name, matching the batch target metadata.
    code
        Machine-readable code with the same meaning as `OperationError.code`.
    reason
        Human-readable failure explanation."""

    index: int
    name: str
    code: str
    reason: str


@dataclass(frozen=True)
class BatchResult[T]:
    """Ordered results and durable target-local failures.

    Attributes
    ----------
    results
        One slot per input. None means failure, not a valid empty selection.
        Successful selection slots are ordinary lists, which may be empty.
    targets
        Input identities in the same order as result slots. Duplicate geometries
        retain independent identities.
    issues
        Expected failures with input position, name, code and reason. Fatal
        configuration or integrity errors raise instead of returning this object."""

    results: list[T | None]
    targets: list[Target]
    issues: list[BatchIssue]


@dataclass(frozen=True)
class DelineationResult:
    """Materialized outputs of one convenience delineation.

    Attributes
    ----------
    subbasins
        Whole source units without refinement; contributing portions otherwise.
        Source attributes on portions are not recomputed for their smaller area.
    geometry
        EPSG:4326 polygon union when merging was requested, otherwise None.
    outlet_cell
        Selected native raster cell when refined, otherwise None.
    rasters
        Materialized raster subsets over the whole vector mask used in
        refinement, otherwise None. They are not cropped to the final watershed.
    original_target
        Input geometry in EPSG:4326 before optional vector snapping.
    target
        EPSG:4326 target after optional vector snapping. Raster outlet adjustment
        is represented separately by ``outlet_cell``, not by changing this field.
    provenance
        Source manifest declarations, not a certification of vector/raster
        equivalence or recomputed hydrological quantities."""

    subbasins: list[Subbasin | SubbasinContribution]
    geometry: Polygon | MultiPolygon | None
    outlet_cell: OutletCell | None
    rasters: RefinementRasters | None
    original_target: BaseGeometry
    target: BaseGeometry
    provenance: Mapping[str, object]
