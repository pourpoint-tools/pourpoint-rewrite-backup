"""Native-grid D8 access. Direction codes are clockwise from east (1..8).

Zero is terminal. Negative codes retain signed GRASS exiting-flow meaning.
Only the separate validity mask determines nodata; its direction payload is zero.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from io import IOBase
from math import ceil, floor, isfinite
from threading import Lock, get_ident
from types import TracebackType
from typing import BinaryIO, Literal, Protocol, Self, cast
from urllib.parse import urlsplit

import numpy as np
import numpy.typing as npt
import rasterio
from affine import Affine
from pyproj import Transformer
from pyproj.exceptions import ProjError
from rasterio.crs import CRS  # ty: ignore[unresolved-import]  # Cython module without stubs
from rasterio.io import DatasetReader
from rasterio.windows import Window

from pourpoint.metadata import Bounds, Fingerprint, artifact_path, bounds
from pourpoint.storage import Source


def normalize_direction(
    values: npt.NDArray[np.uint8] | npt.NDArray[np.int8],
    valid: npt.NDArray[np.bool_],
    encoding: str,
) -> npt.NDArray[np.int8]:
    codes = {
        "esri": (1, 2, 4, 8, 16, 32, 64, 128),
        "taudem": (1, 8, 7, 6, 5, 4, 3, 2),
        "grass": (8, 7, 6, 5, 4, 3, 2, 1),
    }[encoding]
    selected = values[valid].astype(np.int16)
    allowed = (*codes, 0, *(-v for v in codes)) if encoding == "grass" else codes
    if np.any(~np.isin(selected, allowed)):
        raise ValueError("invalid consumed direction code")
    result = np.zeros(values.shape, dtype=np.int8)
    for normalized, code in enumerate(codes, 1):
        result[valid & (values == code)] = normalized
        if encoding == "grass" and values.dtype.kind == "i":
            result[valid & (values == -code)] = -normalized
    return result


def _valid_mask(values: npt.NDArray[np.generic], mask: npt.NDArray[np.uint8], nodata: float) -> npt.NDArray[np.bool_]:
    # GDAL prefers an internal TIFF mask over the nodata tag. HFX still requires
    # header nodata to be absent, even if an internal mask marks that cell valid.
    return (mask != 0) & (~np.isnan(values) if np.isnan(nodata) else values != nodata)


class MissingRastersError(LookupError):
    """This manifest snapshot declares no D8 pair."""


class RasterCoverageError(LookupError):
    """No declared pair covers the entire request."""


@dataclass(frozen=True)
class RasterGrid:
    crs: CRS
    transform: Affine
    width: int
    height: int
    direction_sha256: str
    accumulation_sha256: str


@dataclass(frozen=True)
class RasterRequest:
    col: int
    row: int
    width: int
    height: int
    grid: RasterGrid
    _source: object = field(repr=False)
    _pair: int = field(repr=False)


@dataclass(frozen=True)
class RasterWindow:
    """Caller-owned arrays on the unwarped grid, with independent validity masks.

    Direction: 1 E, 2 SE, 3 S, 4 SW, 5 W, 6 NW, 7 N, 8 NE; 0 terminal.
    Negative directions are exiting flow (terminal for upstream tracing).
    Mask-false direction payloads are zero, not valid terminal cells.
    Accumulation keeps native dtype and declared units, without area conversion.
    """

    direction: npt.NDArray[np.int8]
    accumulation: npt.NDArray[np.int32] | npt.NDArray[np.float32]
    direction_valid: npt.NDArray[np.bool_]
    accumulation_valid: npt.NDArray[np.bool_]
    transform: Affine
    crs: CRS
    accumulation_units: Literal["cells", "km2"]
    request: RasterRequest


class RasterManifest(Protocol):
    @property
    def artifacts(self) -> Mapping[str, Fingerprint]: ...

    @property
    def declarations(self) -> Mapping[str, object]: ...


type _Reference = str | Mapping[str, str]


@dataclass(frozen=True)
class _Declaration:
    direction: _Reference
    accumulation: _Reference
    crs: CRS
    encoding: Literal["esri", "taudem", "grass"]
    units: Literal["cells", "km2"]

    @classmethod
    def parse(cls, entry: Mapping[str, object]) -> Self:
        data = entry.get("metadata")
        artifacts = entry.get("artifacts")
        if not isinstance(data, dict) or not isinstance(artifacts, dict):
            raise ValueError("invalid D8 raster declaration")
        crs = data.get("crs")
        encoding, units = data.get("flow_dir_encoding"), data.get("flow_acc_units")
        if (
            set(data) != {"crs", "flow_dir_encoding", "flow_acc_units"}
            or not isinstance(crs, str)
            or re.fullmatch(r"EPSG:[1-9][0-9]*", crs) is None
            or encoding not in ("esri", "taudem", "grass")
            or units not in ("cells", "km2")
            or set(artifacts) != {"flow_dir", "flow_acc"}
        ):
            raise ValueError("invalid D8 raster declaration")
        for reference in artifacts.values():
            if isinstance(reference, str):
                artifact_path(reference)
            elif not (
                isinstance(reference, dict)
                and set(reference) == {"external"}
                and isinstance(reference["external"], str)
            ):
                raise ValueError("invalid D8 raster artifact reference")
        return cls(
            artifacts["flow_dir"],
            artifacts["flow_acc"],
            CRS.from_string(crs),
            cast(Literal["esri", "taudem", "grass"], encoding),
            cast(Literal["cells", "km2"], units),
        )


@dataclass
class _Pair:
    direction: DatasetReader
    accumulation: DatasetReader
    grid: RasterGrid


class _RasterCursor(IOBase):
    """Keep Python transport exceptions out of GDAL's C callback frames."""

    def __init__(self, stream: BinaryIO, errors: list[Exception]) -> None:
        self._stream = stream
        self._errors = errors

    def read(self, size: int = -1) -> bytes:
        if self._errors:
            return b""
        try:
            return self._stream.read(size)
        except Exception as error:
            self._errors.append(error)
            return b""

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()

    def close(self) -> None:
        self._stream.close()
        super().close()


class RasterReader:
    """Reusable synchronous pairs belonging to one Source and manifest snapshot.

    No topology or catchment acquisition. Headers open lazily during prepare().
    Use one reader and Source per worker, on the thread that creates them. Calls
    must not overlap. Close this reader before its Source. GDAL owns native caches;
    close releases handles, not necessarily process-wide cache allocations.
    """

    def __init__(self, source: Source, manifest: RasterManifest) -> None:
        self._source = source
        self._manifest = manifest
        entries = manifest.declarations.get("auxiliary", [])
        if not isinstance(entries, (list, tuple)):
            raise ValueError("invalid raster auxiliary declarations")
        self._declarations = tuple(
            _Declaration.parse(e) for e in entries if isinstance(e, dict) and e.get("schema") == "hfx.aux.d8_raster.v2"
        )
        self._bands: dict[str, tuple[Source, str, Fingerprint]] = {}
        self._pairs: dict[int, _Pair] = {}
        self._io_errors: list[Exception] = []
        self._resources = ExitStack()
        self._identity = object()
        self._thread = get_ident()
        self._lock = Lock()
        self._closed = False

    @contextmanager
    def _call(self) -> Iterator[None]:
        if self._closed:
            raise RuntimeError("raster reader is closed")
        if get_ident() != self._thread:
            raise RuntimeError("raster reader belongs to another thread")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("raster reader already has an active call")
        try:
            self._io_errors.clear()
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
                yield
        finally:
            self._lock.release()

    def _band(self, reference: _Reference) -> tuple[Source, str, Fingerprint]:
        key = reference if isinstance(reference, str) else "external:" + reference["external"]
        if key not in self._bands:
            if isinstance(reference, str):
                result = self._source, reference, self._manifest.artifacts[reference]
            else:
                external = self._manifest.declarations.get("external_artifacts")
                entry = external.get(reference["external"]) if isinstance(external, dict) else None
                if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
                    raise ValueError("missing external raster identity")
                url = urlsplit(entry["url"])
                retention = entry.get("retention")
                if (
                    url.scheme != "https"
                    or not url.netloc
                    or not url.path.rsplit("/", 1)[-1]
                    or url.query
                    or url.fragment
                    or url.username
                    or url.password
                    or not isinstance(retention, str)
                    or not retention.strip()
                ):
                    raise ValueError("invalid external raster URL or retention")
                fingerprint = Fingerprint.parse(entry)
                source, path = self._source.external(entry["url"])
                result = source, path, fingerprint
            self._bands[key] = result
        return self._bands[key]

    def _check(self, reference: _Reference) -> None:
        source, path, _ = self._band(reference)
        source.check_artifact(path)

    def _open(self, index: int) -> _Pair:
        if index in self._pairs:
            return self._pairs[index]
        declaration = self._declarations[index]
        with ExitStack() as resources:

            def open_band(reference: _Reference) -> DatasetReader:
                source, path, fingerprint = self._band(reference)
                source.open_artifact(path, fingerprint, bounded=True)
                source.check_artifact(path)

                def opener(name: str, mode: str = "rb") -> BinaryIO:
                    if name != path or mode not in ("r", "rb"):
                        raise FileNotFoundError(name)
                    stream = resources.enter_context(source.raster_file(path, fingerprint))
                    return cast(BinaryIO, _RasterCursor(stream, self._io_errors))

                try:
                    return resources.enter_context(rasterio.open(path, opener=opener))
                finally:
                    if self._io_errors:
                        raise self._io_errors[0]
                    source.check_artifact(path)

            direction = open_band(declaration.direction)
            accumulation = open_band(declaration.accumulation)
            for band in (direction, accumulation):
                if band.count != 1 or band.crs != declaration.crs or band.nodata is None:
                    raise ValueError("raster pair: invalid CRS, band count or missing nodata")
                if not all(isfinite(v) for v in band.transform) or band.transform.determinant == 0:
                    raise ValueError("raster pair: invalid affine transform")
            if direction.transform != accumulation.transform or direction.shape != accumulation.shape:
                raise ValueError("raster pair: grid mismatch")
            if direction.dtypes[0] not in ("uint8", "int8"):
                raise ValueError("raster pair: invalid direction dtype")
            if accumulation.dtypes[0] not in (("float32",) if declaration.units == "cells" else ("int32", "float32")):
                raise ValueError("raster pair: invalid accumulation dtype")
            grid = RasterGrid(
                declaration.crs,
                direction.transform,
                direction.width,
                direction.height,
                self._band(declaration.direction)[2].sha256,
                self._band(declaration.accumulation)[2].sha256,
            )
            pair = _Pair(direction, accumulation, grid)
            # Keep the opener closure attached to the live ownership stack if
            # GDAL opens another cursor after initial header acquisition.
            resources = resources.pop_all()
            self._resources.enter_context(resources)
            self._pairs[index] = pair
            return pair

    def prepare(self, envelope: Bounds, *, crs: str | CRS = "EPSG:4326") -> RasterRequest:
        """Resolve bounds into enclosing whole native cells, without pixel reads.

        Transform all four edges with 101 interior samples per edge, rather than
        only opposite corners. Bounds must not wrap the antimeridian. Projected
        bounds can be supplied directly to avoid a geographic bounding envelope.
        """
        with self._call():
            region = bounds(envelope)
            if region[0] == region[2] or region[1] == region[3]:
                raise ValueError("raster bounds must have positive area")
            if not self._declarations:
                raise MissingRastersError("manifest declares no D8 rasters")
            source_crs = CRS.from_user_input(crs)
            if source_crs == CRS.from_epsg(4326) and not (
                -180 <= region[0] < region[2] <= 180 and -90 <= region[1] < region[3] <= 90
            ):
                raise ValueError("geographic raster bounds must be within longitude/latitude limits")
            for index, declaration in enumerate(self._declarations):
                native = region
                if source_crs != declaration.crs:
                    transformer = Transformer.from_crs(source_crs, declaration.crs, always_xy=True)
                    try:
                        native = bounds(transformer.transform_bounds(*region, densify_pts=101, errcheck=True))
                    except ProjError:
                        # A valid region outside this projection's domain cannot
                        # be covered by this pair. Other entries may still cover it.
                        continue
                pair = self._open(index)
                grid = pair.grid
                inverse = ~grid.transform
                corners = [inverse @ (x, y) for x in (native[0], native[2]) for y in (native[1], native[3])]
                col, row = floor(min(p[0] for p in corners)), floor(min(p[1] for p in corners))
                right, bottom = ceil(max(p[0] for p in corners)), ceil(max(p[1] for p in corners))
                if col >= 0 and row >= 0 and right <= grid.width and bottom <= grid.height:
                    self._check(declaration.direction)
                    self._check(declaration.accumulation)
                    return RasterRequest(col, row, right - col, bottom - row, grid, self._identity, index)
            raise RasterCoverageError("no raster pair covers the entire request")

    def read(self, request: RasterRequest) -> RasterWindow:
        with self._call():
            if request._source is not self._identity or request._pair not in self._pairs:
                raise ValueError("raster request belongs to another reader")
            pair = self._pairs[request._pair]
            if (
                request.grid != pair.grid
                or any(type(v) is not int for v in (request.col, request.row, request.width, request.height))
                or not (
                    0 <= request.col < request.col + request.width <= pair.grid.width
                    and 0 <= request.row < request.row + request.height <= pair.grid.height
                )
            ):
                raise ValueError("invalid raster request grid or window")
            declaration = self._declarations[request._pair]
            for reference in (declaration.direction, declaration.accumulation):
                self._check(reference)
            window = Window.from_slices(
                (request.row, request.row + request.height), (request.col, request.col + request.width)
            )
            try:
                direction = pair.direction.read(1, window=window)
                accumulation = pair.accumulation.read(1, window=window)
                direction_valid = _valid_mask(
                    direction, pair.direction.read_masks(1, window=window), pair.direction.nodata
                )
                accumulation_valid = _valid_mask(
                    accumulation, pair.accumulation.read_masks(1, window=window), pair.accumulation.nodata
                )
            finally:
                if self._io_errors:
                    raise self._io_errors[0]
                for reference in (declaration.direction, declaration.accumulation):
                    self._check(reference)
            if np.any(~np.isfinite(accumulation[accumulation_valid])) or np.any(accumulation[accumulation_valid] < 0):
                raise ValueError("invalid consumed accumulation value")
            return RasterWindow(
                normalize_direction(direction, direction_valid, declaration.encoding),
                accumulation,
                direction_valid,
                accumulation_valid,
                pair.direction.window_transform(window),
                pair.grid.crs,
                declaration.units,
                request,
            )

    def close(self) -> None:
        if self._closed:
            return
        with self._call():
            self._resources.close()
            self._pairs.clear()
            self._closed = True

    def __enter__(self) -> Self:
        if self._closed:
            raise RuntimeError("raster reader is closed")
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()
