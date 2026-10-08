"""Verify frozen source WKB and derive native windows offline; no raster reads.

Run: uv run python select_windows.py [--artifacts DIRECTORY]
The optional directory receives exact original EPSG:4326 WKB witnesses.
"""

import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
from typing import TypedDict, cast

import shapely
from pyproj import Transformer


class Selection(TypedDict):
    id: int
    source_wkb_base64: str
    wkb_sha256: str
    bounds: list[float]
    window: list[int]
    tile_shape: list[int]


class Document(TypedDict):
    windows: list[Selection]


def pixel_window(bounds: tuple[float, float, float, float]) -> list[int]:
    """Enclose every projected vertex using the recorded native 30 m grid."""
    xmin, ymin, xmax, ymax = bounds
    col = math.floor((xmin + 15000000) / 30)
    row = math.floor((8400000 - ymax) / 30)
    end_col = math.ceil((xmax + 15000000) / 30)
    end_row = math.ceil((8400000 - ymin) / 30)
    return [col, row, end_col - col, end_row - row]


def verify(path: Path, artifacts: Path | None = None) -> int:
    """Check source hashes and derive bounds from projected coordinate extrema."""
    document = cast(Document, json.loads(path.read_text()))
    project = Transformer.from_crs(4326, 8857, always_xy=True)
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=True)
    for item in document["windows"]:
        wkb = base64.b64decode(item["source_wkb_base64"], validate=True)
        if hashlib.sha256(wkb).hexdigest() != item["wkb_sha256"]:
            raise ValueError(f"Source WKB hash mismatch: {item['id']}")
        coords = shapely.get_coordinates(shapely.from_wkb(wkb))
        xs, ys = project.transform(coords[:, 0], coords[:, 1])
        bounds = (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))
        if any(abs(a - b) > 1e-7 for a, b in zip(bounds, item["bounds"], strict=True)):
            raise ValueError(f"Projected bounds mismatch: {item['id']}")
        window = pixel_window(bounds)
        if window != item["window"]:
            raise ValueError(f"Pixel window mismatch: {item['id']}")
        col, row, width, height = window
        tiles = [(col + width - 1) // 512 - col // 512 + 1, (row + height - 1) // 512 - row // 512 + 1]
        if tiles != item["tile_shape"]:
            raise ValueError(f"Tile shape mismatch: {item['id']}")
        if artifacts is not None:
            (artifacts / f"unit-{item['id']}-source-4326.wkb").write_bytes(wkb)
    return len(document["windows"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selections", type=Path, default=Path(__file__).with_name("selections.json"))
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    print(f"Verified {verify(args.selections, args.artifacts)} source polygons and native windows offline")
