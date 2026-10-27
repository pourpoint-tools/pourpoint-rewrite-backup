"""Typed final-HFX publication declarations and artifact identities."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import cast
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
class Fingerprint:
    """Immutable artifact size and SHA-256 identities supplied by HFX.

    Attributes
    ----------
    size_bytes
        Complete artifact length in bytes.
    sha256
        Digest of the complete artifact; binds indexes and relationships to it.
    head_sha256, tail_sha256
        Digests of bounded leading and trailing byte windows used for selective
        integrity checks. These checks do not verify every artifact byte.
    """

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
class DomainFamily:
    """Published feature table and its ID/spatial addressing artifacts.

    Attributes
    ----------
    data
        Relative GeoParquet artifact path.
    data_sha256
        Complete feature artifact digest to which the indexes are bound.
    count
        Number of source features.
    lookup, directory
        Relative paths of the published ID lookup and spatial directory.
    """

    data: str
    data_sha256: str
    count: int
    lookup: str
    directory: str


@dataclass(frozen=True)
class SpatialManifest:
    """Final HFX publication declarations, including unchanged source metadata.

    Attributes
    ----------
    families
        Domain names mapped to their feature tables and addressing artifacts.
    relationships
        Supplied immediate relationship declarations by domain. Columns retain
        their source meanings, target domains and artifact bindings; an absent
        declaration does not establish a natural outlet.
    artifacts
        Relative artifact paths mapped to immutable size and digest declarations.
    declarations
        Complete source manifest, including provenance, raster declarations and
        otherwise unrecognized metadata. No source attributes are standardized.
    """

    families: Mapping[str, DomainFamily]
    relationships: Mapping[str, Mapping[str, object]]
    artifacts: Mapping[str, Fingerprint]
    declarations: Mapping[str, object]

    @classmethod
    def parse(cls, data: Mapping[str, object]) -> SpatialManifest:
        if data.get("format_version") != "0.4.0":
            raise ValueError("expected HFX 0.4.0")
        if (
            data.get("representation") != "spatial-geoparquet-adjacency"
            or type(data.get("representation_version")) is not int
            or data["representation_version"] != 1
        ):
            raise ValueError("expected spatial-geoparquet-adjacency representation version 1")
        if data.get("crs") != "EPSG:4326":
            raise ValueError("expected EPSG:4326 geometry CRS")
        artifacts = {
            artifact_path(k): Fingerprint.parse(v) for k, v in _mapping(data.get("artifacts"), "artifacts").items()
        }

        def path(value: object) -> str:
            result = artifact_path(value)
            if result not in artifacts:
                raise ValueError(f"manifest fingerprint missing: {result}")
            return result

        families: dict[str, DomainFamily] = {}
        for name, value in _mapping(data.get("families"), "families").items():
            family = _mapping(value, f"family {name}")
            feature = path(family.get("data"))
            digest = _string(family, "data_sha256")
            if digest != artifacts[feature].sha256:
                raise ValueError(f"{name}: feature digest binding mismatch")
            count = family.get("count")
            if type(count) is not int or count < 0:
                raise ValueError(f"{name}: count must be nonnegative")
            families[name] = DomainFamily(
                feature, digest, count, path(family.get("lookup")), path(family.get("directory"))
            )
        if not families:
            raise ValueError("manifest families must not be empty")
        relationships: dict[str, Mapping[str, object]] = {}
        for name, value in _mapping(data.get("relationships"), "relationships").items():
            relation = _mapping(value, f"relationships {name}")
            if name not in families or relation.get("count") != families[name].count:
                raise ValueError(f"{name}: relationship domain/count mismatch")
            path(relation.get("data"))
            path(relation.get("directory"))
            bindings = _mapping(relation.get("feature_artifacts"), "feature_artifacts")
            for domain, binding_value in bindings.items():
                binding = _mapping(binding_value, "feature binding")
                if (
                    domain not in families
                    or binding.get("data") != families[domain].data
                    or binding.get("data_sha256") != families[domain].data_sha256
                ):
                    raise ValueError(f"{name}: relationship feature binding mismatch")
            for column, column_value in _mapping(relation.get("columns"), "relationship columns").items():
                declaration = _mapping(column_value, column)
                target = declaration.get("target_domain")
                if target not in families or target not in bindings or name not in bindings:
                    raise ValueError(f"{name}.{column}: missing target domain binding")
            relationships[name] = MappingProxyType(dict(relation))
        return cls(
            MappingProxyType(families),
            MappingProxyType(relationships),
            MappingProxyType(artifacts),
            MappingProxyType(dict(data)),
        )
