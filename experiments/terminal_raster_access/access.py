"""Native, unresampled paired terminal windows. Experimental only."""

from dataclasses import dataclass
from math import ceil, floor

import numpy as np
from affine import Affine
from numpy.typing import NDArray
from rasterio.io import DatasetReader
from rasterio.windows import Window, from_bounds


@dataclass
class PairedWindow:
    direction: NDArray[np.integer]
    accumulation: NDArray[np.integer | np.floating]
    direction_valid: NDArray[np.bool_]
    accumulation_valid: NDArray[np.bool_]
    transform: Affine
    crs: str
    direction_encoding: str = "grass"
    accumulation_units: str = "km2"

    @property
    def retained_bytes(self) -> int:
        return sum(a.nbytes for a in (self.direction, self.accumulation, self.direction_valid, self.accumulation_valid))


def pixel_window(bounds: tuple[float, float, float, float], transform: Affine, width: int, height: int) -> Window:
    """Smallest whole-cell window covering native polygon bounds; never clip."""
    if transform.b != 0 or transform.d != 0 or transform.a <= 0 or transform.e >= 0:
        raise ValueError("requires north-up grid")
    fractional = from_bounds(*bounds, transform=transform)
    left, top = floor(fractional.col_off), floor(fractional.row_off)
    right = ceil(fractional.col_off + fractional.width)
    bottom = ceil(fractional.row_off + fractional.height)
    if left < 0 or top < 0 or right > width or bottom > height or right <= left or bottom <= top:
        raise ValueError("empty window or bounds outside raster")
    return Window(left, top, right - left, bottom - top)  # ty: ignore[too-many-positional-arguments]


class PairedRaster:
    def __init__(self, direction: DatasetReader, accumulation: DatasetReader) -> None:
        for dataset, dtypes in ((direction, ("uint8", "int8")), (accumulation, ("float32", "int32"))):
            if dataset.count != 1 or dataset.dtypes[0] not in dtypes or dataset.nodata is None:
                raise ValueError("unsupported band, dtype or absent nodata")
            if dataset.crs is None or dataset.crs.to_epsg() != 8857 or not dataset.is_tiled:
                raise ValueError("requires tiled EPSG:8857 rasters")
        if (direction.crs, direction.transform, direction.shape) != (
            accumulation.crs,
            accumulation.transform,
            accumulation.shape,
        ):
            raise ValueError("unaligned raster pair")
        self.direction = direction
        self.accumulation = accumulation

    def read(self, window: Window) -> PairedWindow:
        if (
            window.col_off < 0
            or window.row_off < 0
            or window.width <= 0
            or window.height <= 0
            or window.col_off + window.width > self.direction.width
            or window.row_off + window.height > self.direction.height
            or any(int(v) != v for v in (window.col_off, window.row_off, window.width, window.height))
        ):
            raise ValueError("requires whole-cell in-bounds window")
        direction = self.direction.read(1, window=window)
        accumulation = self.accumulation.read(1, window=window)
        return PairedWindow(
            direction,
            accumulation,
            self.direction.read_masks(1, window=window) != 0,
            self.accumulation.read_masks(1, window=window) != 0,
            self.direction.window_transform(window),
            self.direction.crs.to_string(),
        )
