import hashlib
import json
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
import rasterio
from affine import Affine
from rasterio.transform import from_origin


@pytest.fixture
def spatial_hfx(tmp_path: Path) -> Path:
    from tests.spatial_fixture import make_spatial_bundle

    return make_spatial_bundle(tmp_path)


def fingerprint(path: Path) -> dict[str, int | str]:
    data = path.read_bytes()
    return {
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "head_sha256": hashlib.sha256(data[:65536]).hexdigest(),
        "tail_sha256": hashlib.sha256(data[-65536:]).hexdigest(),
    }


def refresh_fingerprint(root: Path, name: str) -> None:
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["artifacts"][name] = fingerprint(root / name)
    path.write_text(json.dumps(manifest))


def add_pair(
    root: Path,
    *,
    encoding: str = "grass",
    direction: npt.NDArray[np.uint8] | npt.NDArray[np.int8] | None = None,
    accumulation: npt.NDArray[np.int32] | npt.NDArray[np.float32] | None = None,
    crs: str = "EPSG:4326",
    transform: Affine | None = None,
    prefix: str = "",
) -> None:
    direction = np.array([[8, 7, 0, 255], [6, 5, 4, 3]], dtype="uint8") if direction is None else direction
    accumulation = (
        np.array([[16777217, 3, 4, 5], [-999, 7, 8, 9]], dtype="int32") if accumulation is None else accumulation
    )
    transform = from_origin(0, 2, 1, 1) if transform is None else transform
    names = [f"{prefix}dir.tif", f"{prefix}acc.tif"]
    for name, data, nodata in zip(
        names, [direction, accumulation], [255 if direction.dtype == np.uint8 else -128, -999], strict=True
    ):
        with rasterio.open(
            root / name,
            "w",
            driver="GTiff",
            width=data.shape[1],
            height=data.shape[0],
            count=1,
            dtype=data.dtype,
            nodata=nodata,
            crs=crs,
            transform=transform,
            tiled=True,
            blockxsize=16,
            blockysize=16,
        ) as out:
            out.write(data, 1)
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.setdefault("auxiliary", []).append(
        {
            "schema": "hfx.aux.d8_raster.v2",
            "artifacts": dict(zip(["flow_dir", "flow_acc"], names, strict=True)),
            "metadata": {"crs": crs, "flow_dir_encoding": encoding, "flow_acc_units": "km2"},
        }
    )
    manifest["artifacts"].update({name: fingerprint(root / name) for name in names})
    path.write_text(json.dumps(manifest))
