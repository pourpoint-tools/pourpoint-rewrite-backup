"""Independent native-grid examples, not producer certification."""

import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import Any, Self, cast

import numpy as np
import pytest
import rasterio
from conftest import add_pair
from rasterio.io import DatasetReader
from rasterio.transform import from_origin
from test_storage import OriginState, fingerprint
from test_storage import origin as origin

from pourpoint import Dataset
from pourpoint.metadata import Fingerprint, Manifest
from pourpoint.rasters import RasterReader, normalize_direction
from pourpoint.storage import Source


@pytest.mark.parametrize(
    ("encoding", "values"),
    [
        ("esri", [1, 2, 4, 8, 16, 32, 64, 128]),
        ("taudem", [1, 8, 7, 6, 5, 4, 3, 2]),
        ("grass", [8, 7, 6, 5, 4, 3, 2, 1]),
    ],
)
def test_direction_compass(encoding: str, values: list[int]) -> None:
    actual = normalize_direction(np.array([values], dtype="uint8"), np.ones((1, 8), dtype=bool), encoding)
    np.testing.assert_array_equal(actual, [[1, 2, 3, 4, 5, 6, 7, 8]])
    assert actual.dtype == np.int8


def test_signed_terminal_and_header_nodata() -> None:
    values = np.array([[0, -8, -1, -128, 8]], dtype="int8")
    valid = np.array([[True, True, True, False, True]])
    np.testing.assert_array_equal(normalize_direction(values, valid, "grass"), [[0, -1, -8, 0, 1]])
    with pytest.raises(ValueError, match="direction"):
        normalize_direction(values, np.ones_like(valid), "grass")


@pytest.mark.parametrize(("encoding", "value"), [("esri", 3), ("taudem", 0), ("grass", 9)])
def test_invalid_consumed_direction(encoding: str, value: int) -> None:
    with pytest.raises(ValueError, match="direction"):
        normalize_direction(np.array([[value]], dtype="uint8"), np.ones((1, 1), dtype=bool), encoding)


def test_native_window_precision_masks_and_lifetime(hfx: Path) -> None:
    add_pair(hfx)
    with Dataset.open(hfx) as dataset:
        request = dataset.rasters.prepare((0.2, 0.2, 3.8, 1.8))
        assert (request.col, request.row, request.width, request.height) == (0, 0, 4, 2)
        with pytest.raises(FrozenInstanceError):
            request.col = 3
        result = dataset.rasters.read(request)
        assert result.request is request
        np.testing.assert_array_equal(result.direction, [[1, 2, 0, 0], [3, 4, 5, 6]])
        np.testing.assert_array_equal(result.direction_valid, [[True, True, True, False], [True] * 4])
        np.testing.assert_array_equal(result.accumulation_valid, [[True] * 4, [False, True, True, True]])
        assert result.accumulation[0, 0] == 16777217
        assert result.accumulation.dtype == np.int32
        assert result.accumulation_units == "km2"
        assert result.transform == from_origin(0, 2, 1, 1)
        assert result.crs == rasterio.crs.CRS.from_epsg(4326)
        later = dataset.rasters.read(request)
        later.direction[:] = 7
        later.accumulation[:] = 0
        assert dataset._topology is None
    assert result.direction[0, 0] == 1
    assert result.accumulation[0, 0] == 16777217
    with pytest.raises(RuntimeError, match="closed"):
        dataset.rasters.read(request)


def test_raster_only_and_foreign_request(hfx: Path) -> None:
    add_pair(hfx)
    with Source(hfx) as source:
        manifest = Manifest.parse(source.read_json("manifest.json"))
        (hfx / "catchments.arrow").unlink()
        (hfx / "graph.parquet").unlink()
        with RasterReader(source, manifest) as first, RasterReader(source, manifest) as second:
            request = first.prepare((1, 0, 3, 1))
            assert first.read(request).direction.shape == (1, 2)
            with pytest.raises(ValueError, match="another"):
                second.read(request)


def test_vector_laziness_and_prepare_without_pixels(hfx: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    add_pair(hfx)
    with Dataset.open(hfx) as data:
        assert not any(name.endswith(".tif") for name in data._source._files)
        original_open = rasterio.open
        opened = []

        class HeaderOnly:
            def __init__(self, reader: DatasetReader) -> None:
                self.reader = reader

            def __getattr__(self, name: str) -> Any:
                if name in ("read", "read_masks"):
                    raise AssertionError("prepare must not read pixels")
                return getattr(self.reader, name)

            def __enter__(self) -> Self:
                return self

            def __exit__(self, *args: object) -> None:
                self.reader.close()

        def open_header(*args: Any, **kwargs: Any) -> HeaderOnly:
            opened.append(args[0])
            return HeaderOnly(original_open(*args, **kwargs))

        monkeypatch.setattr(rasterio, "open", open_header)
        first = data.rasters.prepare((0, 0, 4, 2))
        second = data.rasters.prepare((1, 0, 2, 1))
        assert first.grid is second.grid
        assert opened == ["dir.tif", "acc.tif"]


def test_missing_coverage_and_declarations(hfx: Path) -> None:
    from pourpoint.rasters import MissingRastersError, RasterCoverageError

    with Dataset.open(hfx) as data, pytest.raises(MissingRastersError):
        data.rasters.prepare((0, 0, 1, 1))
    add_pair(hfx)
    with Dataset.open(hfx) as data, pytest.raises(RasterCoverageError):
        data.rasters.prepare((0, 0, 5, 2))
    manifest = json.loads((hfx / "manifest.json").read_text())
    manifest["auxiliary"][-1]["metadata"]["flow_dir_encoding"] = "unknown"
    (hfx / "manifest.json").write_text(json.dumps(manifest))
    with Dataset.open(hfx) as data, pytest.raises(ValueError, match="declaration"):
        _ = data.rasters


@pytest.mark.parametrize("change", ["transform", "crs", "dtype", "nodata"])
def test_invalid_pair_headers(hfx: Path, change: str) -> None:
    from conftest import refresh_fingerprint

    add_pair(hfx)
    if change == "dtype":
        with rasterio.open(hfx / "acc.tif") as old:
            profile = old.profile
        profile["dtype"] = "float64"
        with rasterio.open(hfx / "acc.tif", "w", **profile) as changed:
            changed.write(np.ones((2, 4)), 1)
    else:
        with rasterio.open(hfx / "acc.tif", "r+") as changed:
            if change == "transform":
                changed.transform = from_origin(0.5, 2, 1, 1)
            elif change == "crs":
                changed.crs = "EPSG:3857"
            else:
                changed.nodata = None
    refresh_fingerprint(hfx, "acc.tif")
    with Dataset.open(hfx) as data, pytest.raises(ValueError, match="raster pair"):
        data.rasters.prepare((0, 0, 1, 1))


def test_projected_edges_and_pair_selection(hfx: Path) -> None:
    # North polar rectangle: the south edge curves below its two corners.
    # EPSG:3413 longitude -45 is the edge's extremum; corner-only transforms miss it.
    add_pair(hfx)
    shape = (40, 40)
    add_pair(
        hfx,
        prefix="polar-",
        crs="EPSG:3413",
        transform=from_origin(-2000000, 0, 100000, 100000),
        direction=np.ones(shape, dtype="uint8"),
        accumulation=np.ones(shape, dtype="float32"),
    )
    with Dataset.open(hfx) as data:
        request = data.rasters.prepare((-90, 70, 0, 80))
        assert request.grid.crs == rasterio.crs.CRS.from_epsg(3413)
        # Southern edge y=-2187927.65 at -45 degrees, beyond corners at -1547098.48.
        assert request.row + request.height == 22
        assert request.col == 4
        assert request.col + request.width == 36
        assert data.rasters.read(request).direction.shape == (15, 32)


def test_tile_boundary_signed_grass_and_invalid_accumulation(hfx: Path) -> None:
    direction = np.full((32, 32), -8, dtype="int8")
    direction[16, 16] = 0
    accumulation = np.full((32, 32), 2, dtype="float32")
    accumulation[15, 15] = -1
    add_pair(hfx, direction=direction, accumulation=accumulation, transform=from_origin(0, 32, 1, 1))
    with Dataset.open(hfx) as data:
        window = data.rasters.read(data.rasters.prepare((15, 14, 18, 16)))
        np.testing.assert_array_equal(window.direction, [[-1, 0, -1], [-1, -1, -1]])
        with pytest.raises(ValueError, match="accumulation"):
            data.rasters.read(data.rasters.prepare((15, 16, 16, 17)))


def test_cached_local_mutation_and_thread_ownership(hfx: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    add_pair(hfx)
    with Dataset.open(hfx) as data:
        reader = data.rasters
        request = reader.prepare((0, 0, 1, 1))
        reader.read(request)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(RuntimeError, match="thread"):
                pool.submit(reader.read, request).result()

            def private_pair() -> np.ndarray:
                with Source(hfx) as source, RasterReader(source, data.manifest) as worker:
                    return worker.read(worker.prepare((0, 0, 1, 1))).direction

            np.testing.assert_array_equal(pool.submit(private_pair).result(), [[3]])
        with reader._call(), pytest.raises(RuntimeError, match="active call"):
            reader.read(request)
        (hfx / "dir.tif").write_bytes((hfx / "dir.tif").read_bytes())
        with pytest.raises(ValueError, match="changed"):
            reader.read(request)


@pytest.mark.parametrize("units", ["cells", "km2"])
def test_float_bits_and_header_nodata_precedence(hfx: Path, units: str) -> None:
    from conftest import refresh_fingerprint

    add_pair(
        hfx,
        direction=np.array([[8, 0, 7, 255]], dtype="uint8"),
        accumulation=np.array([[0.1, np.nan, 16777218, 0]], dtype="float32"),
    )
    with rasterio.open(hfx / "dir.tif", "r+") as band:
        band.nodata = 255
    with rasterio.open(hfx / "acc.tif", "r+") as band:
        band.nodata = float("nan")
    refresh_fingerprint(hfx, "acc.tif")
    path = hfx / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"][-1]["metadata"]["flow_acc_units"] = units
    path.write_text(json.dumps(manifest))
    with Dataset.open(hfx) as data:
        result = data.rasters.read(data.rasters.prepare((0, 1, 4, 2)))
        assert result.accumulation_units == units
        np.testing.assert_array_equal(
            result.accumulation.view("uint32"), np.array([[0.1, np.nan, 16777218, 0]], dtype="float32").view("uint32")
        )
        np.testing.assert_array_equal(result.accumulation_valid, [[True, False, True, True]])
        np.testing.assert_array_equal(result.direction_valid, [[True, True, True, False]])
    # Header may identify a normally valid direction code as nodata.
    with rasterio.open(hfx / "dir.tif", "r+") as band:
        band.nodata = 8
    refresh_fingerprint(hfx, "dir.tif")
    with Dataset.open(hfx) as data:
        result = data.rasters.read(data.rasters.prepare((0, 1, 3, 2)))
        np.testing.assert_array_equal(result.direction_valid, [[False, True, True]])
        np.testing.assert_array_equal(result.direction, [[0, 0, 2]])
        # 255 now has no nodata meaning and must fail explicitly.
        with pytest.raises(ValueError, match="direction"):
            data.rasters.read(data.rasters.prepare((3, 1, 4, 2)))


def test_pair_open_failure_closes_acquired_cursors(hfx: Path) -> None:
    from conftest import refresh_fingerprint
    from rasterio.errors import RasterioIOError

    add_pair(hfx)
    (hfx / "acc.tif").write_bytes(b"not a TIFF")
    refresh_fingerprint(hfx, "acc.tif")
    with Dataset.open(hfx) as data:
        with pytest.raises(RasterioIOError):
            data.rasters.prepare((0, 0, 1, 1))
        assert not data.rasters._pairs
        assert all(stream.closed for stream in data._source._independent_files)


def test_projection_domain_does_not_hide_later_covering_pair(hfx: Path) -> None:
    add_pair(hfx, prefix="utm-", crs="EPSG:32631", transform=from_origin(500000, 2, 1, 1))
    add_pair(hfx, prefix="geographic-", transform=from_origin(90, 2, 1, 1))
    with Dataset.open(hfx) as data:
        request = data.rasters.prepare((92, 0, 94, 1))
        assert request.grid.crs == rasterio.crs.CRS.from_epsg(4326)
        assert (request.col, request.row, request.width, request.height) == (2, 1, 2, 1)
        np.testing.assert_array_equal(data.rasters.read(request).direction, [[5, 6]])
        with pytest.raises(ValueError, match="geographic"):
            data.rasters.prepare((92, 100, 94, 101))


@pytest.mark.parametrize("nodata", [255, 8])
def test_header_nodata_precedes_internal_masks(hfx: Path, nodata: int) -> None:
    from conftest import refresh_fingerprint

    add_pair(
        hfx,
        direction=np.array([[8, 7, 0, nodata], [6, 5, 4, 3]], dtype="uint8"),
        accumulation=np.array([[1, 2, 3, 4], [np.nan, 6, 7, 8]], dtype="float32"),
    )
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        for name in ("dir.tif", "acc.tif"):
            with rasterio.open(hfx / name, "r+") as band:
                band.nodata = nodata if name == "dir.tif" else float("nan")
                band.write_mask(np.full((2, 4), 255, dtype="uint8"))
            refresh_fingerprint(hfx, name)
    with Dataset.open(hfx) as data:
        result = data.rasters.read(data.rasters.prepare((0, 0, 4, 2)))
        np.testing.assert_array_equal(result.direction_valid, [[nodata != 8, True, True, False], [True] * 4])
        np.testing.assert_array_equal(result.accumulation_valid, [[True] * 4, [False, True, True, True]])


def test_native_geographic_bounds_use_declared_angular_units(hfx: Path) -> None:
    # NTF (Paris) uses grads: latitude 95 grads is valid, not 95 degrees.
    add_pair(hfx, crs="EPSG:4807", transform=from_origin(0, 96, 1, 1))
    with Dataset.open(hfx) as data:
        request = data.rasters.prepare((0, 94, 1, 95), crs="EPSG:4807")
        assert (request.col, request.row, request.width, request.height) == (0, 1, 1, 1)
        np.testing.assert_array_equal(data.rasters.read(request).direction, [[3]])


@pytest.fixture
def external_origin(origin: tuple[str, OriginState], monkeypatch: pytest.MonkeyPatch) -> tuple[str, OriginState]:
    url, state = origin
    original = Source.external

    def local_transport(source: Source, external_url: str) -> tuple[Source, str]:
        return original(source, external_url.replace("https://", "http://", 1))

    monkeypatch.setattr(Source, "external", local_transport)
    return url, state


@dataclass(frozen=True)
class RasterManifestFixture:
    artifacts: Mapping[str, Fingerprint]
    declarations: Mapping[str, object]


def external_manifest(root: Path, url: str) -> RasterManifestFixture:
    raw = json.loads((root / "manifest.json").read_text())
    external = {}
    for role, name in raw["auxiliary"][-1]["artifacts"].items():
        external[role] = {
            "url": f"{url.replace('http://', 'https://')}/{name}",
            **raw["artifacts"].pop(name),
            "retention": "retained",
        }
        raw["auxiliary"][-1]["artifacts"][role] = {"external": role}
    raw["external_artifacts"] = external
    return RasterManifestFixture({k: Fingerprint.parse(v) for k, v in raw["artifacts"].items()}, raw)


def test_external_native_pair(hfx: Path, external_origin: tuple[str, OriginState]) -> None:
    add_pair(hfx)
    url, state = external_origin
    state["files"] = {name: (hfx / name).read_bytes() for name in ("dir.tif", "acc.tif")}
    manifest = external_manifest(hfx, url)
    with (
        Source(url, storage_options={"client_options": {"allow_http": True}}) as source,
        RasterReader(source, manifest) as reader,
    ):
        request = reader.prepare((0, 0, 4, 2))
        window = reader.read(request)
        assert request.grid.direction_sha256 == fingerprint(state["files"]["dir.tif"]).sha256
        assert window.transform == from_origin(0, 2, 1, 1)
        np.testing.assert_array_equal(window.direction, [[1, 2, 0, 0], [3, 4, 5, 6]])
        assert window.accumulation[0, 0] == 16777217
    assert state["requests"]
    assert all(item[2:] == ('"v1"', "identity") for item in state["requests"])


def test_external_raster_identity_rejected(hfx: Path, external_origin: tuple[str, OriginState]) -> None:
    add_pair(hfx)
    url, state = external_origin
    manifest = external_manifest(hfx, url)
    state["files"] = {name: (hfx / name).read_bytes() for name in ("dir.tif", "acc.tif")}
    state["files"]["dir.tif"] = b"x" + state["files"]["dir.tif"][1:]
    with (
        Source(url, storage_options={"client_options": {"allow_http": True}}) as source,
        RasterReader(source, manifest) as reader,
        pytest.raises(ValueError, match="fingerprint|SHA|digest"),
    ):
        reader.prepare((0, 0, 4, 2))


def test_external_raster_persistent_native_replay(
    hfx: Path, external_origin: tuple[str, OriginState], monkeypatch: pytest.MonkeyPatch
) -> None:
    import obstore

    add_pair(hfx)
    url, state = external_origin
    manifest = external_manifest(hfx, url)
    state["files"] = {name: (hfx / name).read_bytes() for name in ("dir.tif", "acc.tif")}
    cache_dir = hfx / "cache"
    with (
        Source(
            url, storage_options={"client_options": {"allow_http": True}}, cache=True, cache_dir=cache_dir
        ) as source,
        RasterReader(source, manifest) as reader,
    ):
        first = reader.read(reader.prepare((0, 0, 4, 2)))

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline replay must not use the network")

    monkeypatch.setattr(obstore, "get", no_network)
    monkeypatch.setattr(obstore, "head", no_network)
    with (
        Source(
            url, storage_options={"client_options": {"allow_http": True}}, cache=True, cache_dir=cache_dir, offline=True
        ) as source,
        RasterReader(source, manifest) as reader,
    ):
        replay = reader.read(reader.prepare((0, 0, 4, 2)))
        np.testing.assert_array_equal(replay.direction, first.direction)
        np.testing.assert_array_equal(replay.accumulation, first.accumulation)
        np.testing.assert_array_equal(replay.direction_valid, first.direction_valid)
        np.testing.assert_array_equal(replay.accumulation_valid, first.accumulation_valid)
        assert replay.transform == first.transform
        assert replay.crs == first.crs


@pytest.mark.parametrize("change", ["url", "retention"])
def test_invalid_external_raster_declaration(hfx: Path, change: str) -> None:
    add_pair(hfx)
    manifest = external_manifest(hfx, "https://example.com")
    raw = dict(manifest.declarations)
    external = cast(dict[str, dict[str, object]], raw["external_artifacts"])
    external["flow_dir"][change] = "http://example.com/dir.tif" if change == "url" else ""
    with (
        Source(hfx) as source,
        RasterReader(source, manifest) as reader,
        pytest.raises(ValueError, match="external raster"),
    ):
        reader.prepare((0, 0, 4, 2))


def test_offline_missing_raster_pixels_fail_cleanly(hfx: Path) -> None:
    shape = (512, 512)
    add_pair(
        hfx,
        direction=np.full(shape, 8, dtype="uint8"),
        accumulation=np.ones(shape, dtype="float32"),
        transform=from_origin(0, 512, 1, 1),
    )
    manifest = Manifest.parse(json.loads((hfx / "manifest.json").read_text()))
    with Source(hfx, cache=True, cache_dir=hfx / "cache") as source, RasterReader(source, manifest) as reader:
        reader.prepare((100, 50, 101, 51))
    with (
        Source(hfx, cache=True, cache_dir=hfx / "cache", offline=True) as source,
        RasterReader(source, manifest) as reader,
    ):
        request = reader.prepare((100, 50, 101, 51))
        with pytest.raises(ValueError, match="offline cache missing bytes"):
            reader.read(request)
    # A failed native callback must not poison the next reader.
    with Source(hfx) as source, RasterReader(source, manifest) as reader:
        np.testing.assert_array_equal(reader.read(reader.prepare((100, 50, 101, 51))).direction, [[1]])


def test_raster_transport_identity_error_during_pixels(hfx: Path, external_origin: tuple[str, OriginState]) -> None:
    shape = (512, 512)
    add_pair(
        hfx,
        direction=np.full(shape, 8, dtype="uint8"),
        accumulation=np.ones(shape, dtype="float32"),
        transform=from_origin(0, 512, 1, 1),
    )
    url, state = external_origin
    state["files"] = {name: (hfx / name).read_bytes() for name in ("dir.tif", "acc.tif")}
    manifest = external_manifest(hfx, url)
    with (
        Source(url, storage_options={"client_options": {"allow_http": True}}) as source,
        RasterReader(source, manifest) as reader,
    ):
        request = reader.prepare((100, 50, 101, 51))
        state["mode"] = "etag"
        with pytest.raises(ValueError, match="ETag changed"):
            reader.read(request)
    state["mode"] = "good"
    with (
        Source(url, storage_options={"client_options": {"allow_http": True}}) as source,
        RasterReader(source, manifest) as reader,
    ):
        np.testing.assert_array_equal(reader.read(reader.prepare((100, 50, 101, 51))).direction, [[1]])


def test_missing_raster_header_bytes_fail_cleanly(hfx: Path) -> None:
    import sqlite3

    add_pair(hfx)
    manifest = Manifest.parse(json.loads((hfx / "manifest.json").read_text()))
    cache_dir = hfx / "cache"
    with Source(hfx, cache=True, cache_dir=cache_dir) as source, RasterReader(source, manifest) as reader:
        reader.read(reader.prepare((0, 0, 1, 1)))
    with Source(hfx, cache=True, cache_dir=cache_dir, offline=True) as source:
        # Validate identities first, then lose persisted header bytes before GDAL's cursor reads them.
        for name in ("dir.tif", "acc.tif"):
            source.open_artifact(name, manifest.artifacts[name], bounded=True)
        with sqlite3.connect(cache_dir / source.identity / "ranges.sqlite3") as db:
            db.execute("DELETE FROM ranges")
        with RasterReader(source, manifest) as reader, pytest.raises(ValueError, match="offline cache missing bytes"):
            reader.prepare((0, 0, 1, 1))
    with Source(hfx) as source, RasterReader(source, manifest) as reader:
        np.testing.assert_array_equal(reader.read(reader.prepare((0, 0, 1, 1))).direction, [[3]])
