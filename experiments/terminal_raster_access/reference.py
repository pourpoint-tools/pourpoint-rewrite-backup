"""Untimed independent cell check: bounded BigTIFF metadata and selected tiles only.

No GDAL or access.py imports. Original response bytes are kept outside the repo.
The in-memory header copy replaces the huge tile index tags with placeholders;
only the selected original index entries are fetched before tifffile decoding.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import imagecodecs
import numpy as np
import tifffile
from numpy.typing import NDArray

Fetch = Callable[[int, int], bytes]
Window = tuple[int, int, int, int]
HEADER_BYTES = 16384


def decode_window(fetch: Fetch, window: Window) -> tuple[NDArray[Any], NDArray[np.bool_], dict[str, Any]]:
    """Read one native window from a single-band BigTIFF; no resampling."""
    header = bytearray(fetch(0, HEADER_BYTES))
    endian = {b"II": "<", b"MM": ">"}.get(bytes(header[:2]))
    if endian is None or struct.unpack_from(endian + "HHH", header, 2) != (43, 8, 0):
        raise ValueError("reference requires BigTIFF with 8-byte offsets")
    ifd = struct.unpack_from(endian + "Q", header, 8)[0]
    if ifd + 8 > len(header):
        raise ValueError("IFD outside bounded header")
    entries = struct.unpack_from(endian + "Q", header, ifd)[0]
    end = ifd + 8 + entries * 20
    if end + 8 > len(header):
        raise ValueError("IFD outside bounded header")
    indexes: dict[int, tuple[int, int, int]] = {}
    for position in range(ifd + 8, end, 20):
        tag, dtype, count, value = struct.unpack_from(endian + "HHQQ", header, position)
        if tag in (324, 325):
            indexes[tag] = (dtype, count, position + 12 if count * {3: 2, 4: 4, 16: 8}[dtype] <= 8 else value)
            struct.pack_into(endian + "QQ", header, position + 4, 1, 0)
    if set(indexes) != {324, 325}:
        raise ValueError("requires tiled TIFF")
    struct.pack_into(endian + "Q", header, end, 0)  # Do not follow overview IFDs.

    def index_value(tag: int, index: int) -> int:
        dtype, count, offset = indexes[tag]
        if index >= count:
            raise ValueError("tile index outside table")
        code, size = {3: ("H", 2), 4: ("I", 4), 16: ("Q", 8)}[dtype]
        return int(struct.unpack(endian + code, fetch(offset + index * size, size))[0])

    with tifffile.TiffFile(io.BytesIO(header)) as tif:
        page = tif.pages[0]
        if not isinstance(page, tifffile.TiffPage):
            raise ValueError("requires a complete first TIFF page")
        if page.samplesperpixel != 1 or not page.is_tiled:
            raise ValueError("requires single-band tiled TIFF")
        col, row, width, height = window
        if (
            min(col, row) < 0
            or min(width, height) <= 0
            or col + width > page.imagewidth
            or row + height > page.imagelength
        ):
            raise ValueError("window outside raster bounds")
        nodata = float(page.tags[42113].value.rstrip("\x00"))
        result = np.empty((height, width), dtype=page.dtype)
        tw, th = page.tilewidth, page.tilelength
        across = (page.imagewidth + tw - 1) // tw
        tiles: list[int] = []
        for tile_row in range(row // th, (row + height - 1) // th + 1):
            for tile_col in range(col // tw, (col + width - 1) // tw + 1):
                index = tile_row * across + tile_col
                offset, count = index_value(324, index), index_value(325, index)
                if not offset or not count:
                    raise ValueError("sparse tiles unsupported by this reference")
                decoded, _, _ = page.decode(fetch(offset, count), index)
                if decoded is None:
                    raise ValueError("missing decoded tile")
                tile = decoded.reshape(th, tw)
                x0, y0 = max(col, tile_col * tw), max(row, tile_row * th)
                x1, y1 = min(col + width, (tile_col + 1) * tw), min(row + height, (tile_row + 1) * th)
                result[y0 - row : y1 - row, x0 - col : x1 - col] = tile[
                    y0 - tile_row * th : y1 - tile_row * th, x0 - tile_col * tw : x1 - tile_col * tw
                ]
                tiles.append(index)
        valid = ~np.isnan(result) if np.isnan(nodata) else result != nodata
        return (
            result,
            valid,
            {
                "tiles": tiles,
                "nodata": "NaN" if np.isnan(nodata) else nodata,
                "dtype": str(page.dtype),
                "shape": [page.imagelength, page.imagewidth],
            },
        )


class SavedRanges:
    """Serial bounded requests with exact raw payload evidence (not a file engine)."""

    def __init__(self, client: httpx.Client, url: str, directory: Path) -> None:
        self.client, self.url, self.directory = client, url, directory
        directory.mkdir(parents=True, exist_ok=True)
        self.records: list[dict[str, Any]] = []

    def __call__(self, offset: int, count: int) -> bytes:
        if count <= 0 or count > 32 * 1024 * 1024:
            raise ValueError("range exceeds experiment safety bound")
        name = f"{offset}-{count}.bin"
        path = self.directory / name
        if path.exists():
            return path.read_bytes()
        with self.client.stream(
            "GET", self.url, headers={"Range": f"bytes={offset}-{offset + count - 1}", "Accept-Encoding": "identity"}
        ) as response:
            if response.status_code != 206:
                raise ValueError(f"server did not honor bounded range: {response.status_code}")
            expected = f"bytes {offset}-{offset + count - 1}/"
            if not response.headers.get("content-range", "").startswith(expected):
                raise ValueError("unexpected Content-Range")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > count:
                    raise ValueError("oversized range response")
            if len(data) != count:
                raise ValueError("short range response")
            record = {
                "offset": offset,
                "count": count,
                "sha256": hashlib.sha256(data).hexdigest(),
                "file": name,
                "headers": dict(response.headers),
            }
        path.write_bytes(data)
        self.records.append(record)
        with (self.directory / "ranges.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        return bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selections", type=Path, required=True)
    parser.add_argument("--arrays", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="External raw evidence directory")
    parser.add_argument("--summary", type=Path, help="Compact JSON output (default: output/comparison.json)")
    args = parser.parse_args()
    specification = json.loads(args.selections.read_text())
    comparisons: list[dict[str, Any]] = []
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        base = "https://basin-delineations-public.upstream.tech/grit/hfx-v0.3.0/aux/d8/"
        sources = {
            name: SavedRanges(client, base + filename, args.output / name)
            for name, filename in (("direction", "flow_dir.tif"), ("accumulation", "flow_acc.tif"))
        }
        for index, selection in enumerate(specification["windows"]):
            col, row, width, height = selection["window"]
            window = (col, row, width, height)
            measured_path = args.arrays / f"window-{index}.npz"
            with measured_path.open("rb") as stream:
                measured_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            with np.load(measured_path) as measured:
                for name in ("direction", "accumulation"):
                    values, valid, metadata = decode_window(sources[name], window)
                    np.testing.assert_array_equal(values, measured[name], strict=True)
                    np.testing.assert_array_equal(valid, measured[name + "_valid"], strict=True)
                    comparisons.append(
                        {
                            "selection_index": index,
                            "measured_npz": str(measured_path),
                            "measured_npz_sha256": measured_sha256,
                            "asset": name,
                            "window": window,
                            "cells": values.size,
                            "valid_cells": int(valid.sum()),
                            "values_equal": True,
                            "validity_equal": True,
                            **metadata,
                        }
                    )
    summary = args.summary or args.output / "comparison.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        json.dumps(
            {
                "decoder": "tifffile+imagecodecs; no GDAL",
                "versions": {"tifffile": tifffile.__version__, "imagecodecs": imagecodecs.__version__},
                "reference_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "untimed": True,
                "evidence": str(args.output),
                "comparisons": comparisons,
            },
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
