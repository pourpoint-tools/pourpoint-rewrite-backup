"""Eager hydrological operations and HFX source access."""

from pourpoint.dataset import Dataset
from pourpoint.fabric import HydroFabric
from pourpoint.features import River, RiverNetwork, SourceNode, Subbasin
from pourpoint.geometry import DecodedGeometry, GeometryBatch
from pourpoint.metadata import Manifest, SnapSet
from pourpoint.operations import delineate, select_upstream_subbasins
from pourpoint.qgis import QGISBundle
from pourpoint.refinement import (
    FootprintRefinement,
    GranularRefinement,
    OutletCell,
    RasterSubset,
    RefinementRasters,
    SubbasinContribution,
    merge,
    refine,
)
from pourpoint.results import BatchIssue, BatchResult, DelineationResult, OperationError, Target
from pourpoint.rivers import select_rivers, snap
from pourpoint.topology import UnitSelection

__all__ = [
    "QGISBundle",
    "select_upstream_subbasins",
    "delineate",
    "refine",
    "merge",
    "snap",
    "select_rivers",
    "SubbasinContribution",
    "OutletCell",
    "RasterSubset",
    "RefinementRasters",
    "GranularRefinement",
    "FootprintRefinement",
    "BatchIssue",
    "BatchResult",
    "DelineationResult",
    "OperationError",
    "Target",
    "HydroFabric",
    "Subbasin",
    "River",
    "SourceNode",
    "RiverNetwork",
    "Dataset",
    "DecodedGeometry",
    "GeometryBatch",
    "Manifest",
    "SnapSet",
    "UnitSelection",
]
