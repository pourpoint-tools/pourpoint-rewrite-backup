import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.io import MemoryFile
from rasterio.windows import Window

from access import PairedRaster, pixel_window


def test_covering_window() -> None:
    transform = Affine(30, 0, -15000000, 0, -30, 8400000)
    assert pixel_window((-14999969, 8399909, -14999909, 8399969), transform, 100, 100) == Window(1, 1, 3, 3)  # ty: ignore[too-many-positional-arguments]
    with pytest.raises(ValueError, match="outside"):
        pixel_window((-15000001, 8399909, -14999909, 8399969), transform, 100, 100)


@pytest.mark.parametrize("misaligned", [False, True])
def test_native_values_boundary_and_separate_validity(misaligned: bool) -> None:
    direction = np.full((32, 32), 8, dtype="uint8")
    accumulation = np.arange(1024, dtype="int32").reshape(32, 32)
    direction[15, 16] = 255
    accumulation[16, 15] = -9999
    # Above float32's exact integer range: must not coerce.
    accumulation[16, 16] = 16777217
    with MemoryFile() as fd_file, MemoryFile() as fa_file:
        for mem, data, nodata in [(fd_file, direction, 255), (fa_file, accumulation, -9999)]:
            with mem.open(
                driver="GTiff",
                width=32,
                height=32,
                count=1,
                dtype=data.dtype,
                crs="EPSG:8857",
                transform=Affine(30, 0, 30 if misaligned and mem is fa_file else 0, 0, -30, 960),
                nodata=nodata,
                tiled=True,
                blockxsize=16,
                blockysize=16,
            ) as dst:
                dst.write(data, 1)
        with rasterio.open(fd_file.name) as fd, rasterio.open(fa_file.name) as fa:
            if misaligned:
                with pytest.raises(ValueError, match="unaligned"):
                    PairedRaster(fd, fa)
                return
            pair = PairedRaster(fd, fa)
            result = pair.read(Window(15, 15, 2, 2))  # ty: ignore[too-many-positional-arguments]
            np.testing.assert_array_equal(result.direction, [[8, 255], [8, 8]])
            np.testing.assert_array_equal(result.accumulation, [[495, 496], [-9999, 16777217]])
            np.testing.assert_array_equal(result.direction_valid, [[True, False], [True, True]])
            np.testing.assert_array_equal(result.accumulation_valid, [[True, True], [False, True]])
            assert result.transform == Affine(30, 0, 450, 0, -30, 510)
            assert result.retained_bytes == 28
