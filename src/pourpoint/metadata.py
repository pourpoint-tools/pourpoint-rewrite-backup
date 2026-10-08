"""Typed HFX declarations. Auxiliary payloads remain metadata, not raster APIs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Literal, cast
from urllib.parse import unquote, urlsplit

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type Bounds = tuple[float, float, float, float]


def bounds(value: object) -> Bounds:
    """Validate a non-wrapping EPSG:4326 search envelope (degrees)."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("bbox must contain four coordinates")
    if any(type(v) not in (int, float) or not isfinite(v) for v in value):
        raise ValueError("bbox must be finite numbers")
    result = cast(Bounds, tuple(float(v) for v in value))
    if result[0] > result[2] or result[1] > result[3]:
        raise ValueError("bbox must be ordered")
    return result


def artifact_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("artifact path must be relative")
    decoded = unquote(value)
    if (
        "\\" in decoded
        or any(ord(c) < 32 for c in decoded)
        or any(part in ("", ".", "..") for part in decoded.split("/"))
    ):
        raise ValueError("artifact path must name an unambiguous relative file")
    parsed = urlsplit(decoded)
    path = PurePosixPath(decoded)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"artifact path escapes dataset root: {value!r}")
    return value


def _string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"manifest {key} must be a nonempty string")
    return value


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError(f"manifest {name} must be an object")
    return value


@dataclass(frozen=True)
class Auxiliary:
    schema: str
    artifacts: Mapping[str, str]
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class SnapSet:
    name: str
    path: str
    references_levels: tuple[int, ...]
    description: str
    weight_semantics: str


@dataclass(frozen=True)
class Fingerprint:
    size_bytes: int
    sha256: str
    head_sha256: str
    tail_sha256: str

    @classmethod
    def parse(cls, value: object) -> Fingerprint:
        data = _mapping(value, "fingerprint")
        size = data.get("size_bytes")
        if type(size) is not int or size < 0:
            raise ValueError("artifact size_bytes must be nonnegative")
        digests = tuple(_string(data, key) for key in ("sha256", "head_sha256", "tail_sha256"))
        if any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in digests):
            raise ValueError("artifact fingerprint must contain lowercase SHA-256 digests")
        return cls(size, *digests)


@dataclass(frozen=True)
class Manifest:
    graph: str
    catchments: str
    lookup: str
    directory: str
    source_manifest: str
    artifacts: Mapping[str, Fingerprint]
    fabric_name: str
    unit_count: int
    topology: Literal["tree", "dag"]
    has_up_area: bool
    bbox: Bounds
    auxiliary: tuple[Auxiliary, ...]
    snap_sets: Mapping[str, SnapSet]
    declarations: Mapping[str, object]

    @classmethod
    def parse(cls, data: Mapping[str, object]) -> Manifest:
        if data.get("format_version") != "0.4.0":
            raise ValueError("manifest format_version: expected HFX 0.4.0")
        if data.get("crs") != "EPSG:4326":
            raise ValueError("manifest crs: expected EPSG:4326")
        name = _string(data, "fabric_name")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
            raise ValueError("manifest fabric_name must be lowercase ASCII without whitespace")
        count = data.get("unit_count")
        if type(count) is not int or not 0 < count <= 2**63 - 1:
            raise ValueError("manifest unit_count must be a positive int64 integer")
        topology = data.get("topology")
        if topology not in ("tree", "dag"):
            raise ValueError("manifest topology must be tree or dag")
        has_up_area = data.get("has_up_area")
        if type(has_up_area) is not bool:
            raise ValueError("manifest has_up_area must be boolean")
        created = _string(data, "created_at")
        try:
            stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("missing timezone")
        except ValueError as exc:
            raise ValueError("manifest created_at must be an RFC3339 timestamp") from exc
        _string(data, "adapter_version")
        auxiliary: list[Auxiliary] = []
        snaps: dict[str, SnapSet] = {}
        entries = data.get("auxiliary")
        if not isinstance(entries, list):
            raise ValueError("manifest auxiliary must be an array")
        for entry in entries:
            item = _mapping(entry, "auxiliary")
            schema = _string(item, "schema")
            if not re.fullmatch(r"[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)+", schema):
                raise ValueError("manifest auxiliary schema is invalid")
            paths = _mapping(item.get("artifacts"), "artifacts")
            if not paths or any(not k for k in paths):
                raise ValueError("manifest artifacts must have nonempty keys")
            artifacts = {key: artifact_path(value) for key, value in paths.items()}
            metadata = _mapping(item.get("metadata"), "metadata")
            auxiliary.append(Auxiliary(schema, MappingProxyType(artifacts), MappingProxyType(dict(metadata))))
            if schema == "hfx.aux.snap.v2":
                snap_name = _string(metadata, "name")
                if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", snap_name) or snap_name in snaps:
                    raise ValueError("manifest snap name must be unique kebab-case")
                levels = metadata.get("references_levels")
                if (
                    not isinstance(levels, list)
                    or not levels
                    or any(type(v) is not int or not 0 <= v <= 32767 for v in levels)
                ):
                    raise ValueError("manifest references_levels must be nonempty nonnegative int16 levels")
                if "snap" not in artifacts:
                    raise ValueError("manifest snap artifact missing")
                snaps[snap_name] = SnapSet(
                    snap_name,
                    artifacts["snap"],
                    tuple(levels),
                    _string(metadata, "description"),
                    _string(metadata, "weight_semantics"),
                )
        paths = [
            artifact_path(data.get(key)) for key in ("graph", "catchments", "lookup", "directory", "source_manifest")
        ]
        fingerprints = {
            artifact_path(k): Fingerprint.parse(v) for k, v in _mapping(data.get("artifacts"), "artifacts").items()
        }
        for path in [*paths, *(p for entry in auxiliary for p in entry.artifacts.values())]:
            if path not in fingerprints:
                raise ValueError(f"manifest fingerprint missing: {path}")
        for key, expected in (("index_batch_rows", 65536), ("catchment_batch_bytes", 8388608)):
            if type(data.get(key)) is not int or data[key] != expected:
                raise ValueError(f"manifest {key}: expected {expected}")
        return cls(
            paths[0],
            paths[1],
            paths[2],
            paths[3],
            paths[4],
            MappingProxyType(fingerprints),
            name,
            count,
            cast(Literal["tree", "dag"], topology),
            has_up_area,
            bounds(data.get("bbox")),
            tuple(auxiliary),
            MappingProxyType(snaps),
            MappingProxyType(dict(data)),
        )
