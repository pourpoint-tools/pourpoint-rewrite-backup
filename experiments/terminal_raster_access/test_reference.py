"""Independent hand-worked cells across compressed TIFF tiles."""

import struct
from pathlib import Path

import numpy as np
import pytest
import tifffile

from reference import decode_window


@pytest.mark.parametrize("remote_index", [False, True])
def test_tile_boundary_and_nodata(tmp_path: Path, remote_index: bool) -> None:
    path = tmp_path / "fixture.tif"
    cells = np.arange(32 * 32, dtype=np.int32).reshape(32, 32)
    cells[15, 16] = -9999
    tifffile.imwrite(
        path,
        cells,
        bigtiff=True,
        tile=(16, 16),
        compression="deflate",
        predictor=True,
        extratags=[(42113, "s", 0, "-9999", False)],
    )
    raw = bytearray(path.read_bytes())
    if remote_index:
        # Put the original tile indexes beyond the initial header read.
        ifd = struct.unpack_from("<Q", raw, 8)[0]
        entries = struct.unpack_from("<Q", raw, ifd)[0]
        raw.extend(bytes(20000))
        for position in range(ifd + 8, ifd + 8 + entries * 20, 20):
            tag, dtype, count, offset = struct.unpack_from("<HHQQ", raw, position)
            if tag in (324, 325):
                size = {3: 2, 4: 4, 16: 8}[dtype]
                if count * size > 8:
                    payload = raw[offset : offset + count * size]
                    struct.pack_into("<Q", raw, position + 12, len(raw))
                    raw.extend(payload)
    reads: list[tuple[int, int]] = []

    def fetch(offset: int, count: int) -> bytes:
        reads.append((offset, count))
        return bytes(raw[offset : offset + count])

    values, valid, metadata = decode_window(fetch, (15, 15, 2, 2))
    np.testing.assert_array_equal(values, [[495, -9999], [527, 528]])
    np.testing.assert_array_equal(valid, [[True, False], [True, True]])
    assert metadata["tiles"] == [0, 1, 2, 3]
    assert metadata["nodata"] == -9999
    with pytest.raises(ValueError, match="bounds"):
        decode_window(fetch, (31, 31, 2, 2))


def test_nan_nodata_is_json_safe(tmp_path: Path) -> None:
    import json

    path = tmp_path / "nan.tif"
    cells = np.zeros((16, 16), dtype=np.float32)
    cells[0, 0] = np.nan
    tifffile.imwrite(
        path, cells, bigtiff=True, tile=(16, 16), compression="deflate", extratags=[(42113, "s", 0, "nan", False)]
    )
    raw = path.read_bytes()
    values, valid, metadata = decode_window(lambda offset, count: raw[offset : offset + count], (0, 0, 2, 1))
    np.testing.assert_array_equal(values, [[np.nan, 0]])
    np.testing.assert_array_equal(valid, [[False, True]])
    assert metadata["nodata"] == "NaN"
    json.dumps(metadata, allow_nan=False)
