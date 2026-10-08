"""Source-scoped storage, pinned range access and verified original graph files.

obstore owns transport/authentication; fsspec owns file positioning and buffering.
The narrow range hook adds HFX integrity checks not exposed by obstore.open_reader.
Readers are source-owned, reusable, and not a concurrent-query interface.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, BinaryIO, cast
from urllib.parse import unquote, urlsplit

import obstore
import pyarrow.parquet as pq
from fsspec.spec import AbstractBufferedFile
from obstore.store import HTTPStore, LocalStore, ObjectStore, S3Store

from pourpoint.metadata import Fingerprint, artifact_path

if TYPE_CHECKING:
    from obstore import GetOptions, GetResult


@dataclass(frozen=True)
class _Identity:
    size: int
    etag: str | None
    version: str | None
    local_stat: tuple[int, int, int, int] | None = None


class _ArtifactFile(AbstractBufferedFile):
    """Established fsspec buffering with a checked obstore range operation."""

    def __init__(self, source: Source, path: str, identity: _Identity) -> None:
        self.source = source
        self.identity = identity
        super().__init__(None, path, mode="rb", size=identity.size, block_size=65536, cache_type="readahead")

    def _fetch_range(self, start: int, end: int) -> bytes:
        return self.source._range(self.path, self.identity, start, end)


class Source:
    """Own a dataset's configured store and open artifacts.

    HTTPS is anonymous HTTP, never S3 autodetection. S3 constructor options accept
    endpoint/region/credentials and credential providers. Never persist credentials.
    Manifest fingerprints bind content; strong ETags or versions bind remote reads.
    Local files are checked using inode, device, size and nanosecond modification time.
    """

    def __init__(self, root: str | Path, *, storage_options: Mapping[str, Any] | None = None) -> None:
        options = dict(storage_options or {})
        parsed = urlsplit(str(root))
        self._local_root: Path | None = None
        self._closed = False
        self._files: dict[str, _ArtifactFile] = {}
        self._fingerprints: dict[str, Fingerprint] = {}
        self._bounded: set[str] = set()
        self._parquet: dict[str, pq.ParquetFile] = {}
        self._json: dict[str, tuple[Fingerprint | None, dict[str, object]]] = {}
        if isinstance(root, Path) or not parsed.scheme or parsed.scheme == "file":
            if options or (parsed.scheme == "file" and parsed.netloc):
                raise ValueError("local sources require a local path and no storage options")
            self._local_root = Path(unquote(parsed.path) if parsed.scheme == "file" else root).expanduser().resolve()
            self._store: ObjectStore = LocalStore(self._local_root)
            location: object = self._local_root.as_uri()
        else:
            if parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError("dataset root must not contain credentials, query or fragment")
            client = dict(options.pop("client_options", {}))
            headers = dict(client.pop("default_headers", {}))
            headers = {k: v for k, v in headers.items() if k.lower() != "accept-encoding"}
            headers["Accept-Encoding"] = "identity"
            client["default_headers"] = headers
            options["client_options"] = client
            if parsed.scheme in {"http", "https"}:
                self._store = HTTPStore.from_url(str(root).rstrip("/"), **options)
                location = str(root).rstrip("/")
            elif parsed.scheme == "s3":
                self._store = S3Store.from_url(str(root).rstrip("/"), **options)
                config = self._store.config
                location = {
                    "root": str(root).rstrip("/"),
                    "prefix": self._store.prefix,
                    "endpoint": config.get("endpoint"),
                    "region": config.get("region"),
                    "environment": {
                        k: os.environ.get(k)
                        for k in (
                            "AWS_ENDPOINT",
                            "AWS_ENDPOINT_URL",
                            "AWS_ENDPOINT_URL_S3",
                            "AWS_REGION",
                            "AWS_DEFAULT_REGION",
                        )
                    },
                }
            else:
                raise ValueError("dataset root must be local, HTTP(S), or s3://")
        self.identity = hashlib.sha256(json.dumps(location, sort_keys=True).encode()).hexdigest()

    def _path(self, relative: str) -> str:
        if self._closed:
            raise ValueError("source is closed")
        artifact_path(relative)
        if unquote(relative) != relative or relative in {".", ""}:
            raise ValueError("artifact path must be an unescaped relative path")
        if self._local_root is not None and not (self._local_root / relative).resolve().is_relative_to(
            self._local_root
        ):
            raise ValueError("artifact path escapes dataset root")
        return relative

    def _stat(self, path: str) -> tuple[int, int, int, int]:
        assert self._local_root is not None
        stat = (self._local_root / path).stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    def _identity(self, path: str) -> _Identity:
        if self._local_root is not None:
            stat = self._stat(path)
            return _Identity(stat[2], None, None, stat)
        meta = obstore.head(self._store, path)
        etag, version = meta["e_tag"], meta["version"]
        if not version and (not etag or etag.startswith("W/")):
            raise ValueError(f"{path}: a strong ETag or immutable object version is required")
        return _Identity(meta["size"], etag, version)

    def _range(self, path: str, identity: _Identity, start: int, end: int) -> bytes:
        self._path(path)
        if not 0 <= start < end <= identity.size:
            raise ValueError("artifact range out of bounds")
        options: GetOptions = {"range": (start, end)}
        if identity.version:
            options["version"] = identity.version
        if identity.etag:
            options["if_match"] = identity.etag
        if identity.local_stat is not None and self._stat(path) != identity.local_stat:
            raise ValueError(f"{path}: artifact changed")
        # object_store checks 206 and Content-Range for remote range requests.
        result = obstore.get(self._store, path, options=options)
        self._check_response(path, identity, result)
        if result.range != (start, end):
            raise ValueError(f"{path}: invalid response range")
        data = bytes(result.bytes())
        if len(data) != end - start:
            raise ValueError(f"{path}: incomplete or oversized range response")
        if identity.local_stat is not None and self._stat(path) != identity.local_stat:
            raise ValueError(f"{path}: artifact changed")
        return data

    @staticmethod
    def _check_response(path: str, identity: _Identity, result: GetResult) -> None:
        meta = result.meta
        if meta["size"] != identity.size:
            raise ValueError(f"{path}: invalid response size")
        if identity.etag and meta["e_tag"] != identity.etag:
            raise ValueError(f"{path}: artifact ETag changed")
        if identity.version and meta["version"] != identity.version:
            raise ValueError(f"{path}: artifact version changed")
        if result.attributes.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError(f"{path}: encoded response")

    def open_artifact(self, path: str, fingerprint: Fingerprint, *, bounded: bool = False) -> BinaryIO:
        """Return a source-owned file; bounded=True verifies head and tail digests."""
        path = self._path(path)
        if path in self._fingerprints and self._fingerprints[path] != fingerprint:
            raise ValueError(f"{path}: inconsistent artifact fingerprint")
        if path not in self._files:
            identity = self._identity(path)
            if identity.size != fingerprint.size_bytes:
                raise ValueError(f"{path}: artifact size mismatch")
            self._files[path] = _ArtifactFile(self, path, identity)
            self._fingerprints[path] = fingerprint
        stream = self._files[path]
        if bounded and path not in self._bounded:
            size = fingerprint.size_bytes
            count = min(65536, size)
            for start, digest in ((0, fingerprint.head_sha256), (size - count, fingerprint.tail_sha256)):
                data = self._range(path, stream.identity, start, start + count)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f"{path}: bounded SHA-256 mismatch")
            self._bounded.add(path)
        stream.seek(0)
        return cast(BinaryIO, stream)

    def read_json(self, path: str, fingerprint: Fingerprint | None = None) -> dict[str, object]:
        path = self._path(path)
        if path in self._json:
            saved, value = self._json[path]
            if fingerprint != saved:
                raise ValueError(f"{path}: inconsistent artifact fingerprint")
            return dict(value)
        if fingerprint is None:
            identity = self._identity(path)
            data = self._range(path, identity, 0, identity.size)
        else:
            stream = self.open_artifact(path, fingerprint)
            data = stream.read()
            if hashlib.sha256(data).hexdigest() != fingerprint.sha256:
                raise ValueError(f"{path}: full SHA-256 mismatch")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected JSON object")
        self._json[path] = fingerprint, value
        return dict(value)

    def parquet(self, path: str, fingerprint: Fingerprint) -> pq.ParquetFile:
        self._path(path)
        if path not in self._parquet:
            stream = self.open_artifact(path, fingerprint)
            self._parquet[path] = pq.ParquetFile(stream, pre_buffer=True, buffer_size=0)
        elif self._fingerprints[path] != fingerprint:
            raise ValueError(f"{path}: inconsistent artifact fingerprint")
        return self._parquet[path]

    def graph_path(self, cache_dir: str | Path, path: str, fingerprint: Fingerprint) -> Path:
        """Verify/reuse an original graph; publish completed downloads atomically.

        Local originals are used in place. Remote cache keys include source and full
        manifest digest. Reopening checks full cached graph SHA-256, not catchments.
        """
        path = self._path(path)
        if self._local_root is not None:
            destination = self._local_root / path
            self._verify_file(destination, fingerprint)
            return destination
        directory = Path(cache_dir) / self.identity
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{fingerprint.sha256}.parquet"
        if destination.exists():
            try:
                self._verify_file(destination, fingerprint)
                return destination
            except ValueError:
                pass
        self.open_artifact(path, fingerprint)
        identity = self._files[path].identity
        options: GetOptions = {}
        if identity.etag:
            options["if_match"] = identity.etag
        if identity.version:
            options["version"] = identity.version
        result = obstore.get(self._store, path, options=options)
        self._check_response(path, identity, result)
        if result.range != (0, identity.size):
            raise ValueError(f"{path}: incomplete original artifact")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                # A single whole-object GET avoids serial graph range requests.
                # obstore streams bounded transfer chunks, not one huge allocation.
                for chunk in result.stream():
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            self._verify_file(temporary, fingerprint)
            os.replace(temporary, destination)
            return destination
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _verify_file(path: Path, fingerprint: Fingerprint) -> None:
        if path.stat().st_size != fingerprint.size_bytes:
            raise ValueError(f"{path.name}: artifact size mismatch")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != fingerprint.sha256:
            raise ValueError(f"{path.name}: full SHA-256 mismatch")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            for reader in self._parquet.values():
                reader.close(force=True)
        finally:
            for stream in self._files.values():
                stream.close()
            self._parquet.clear()
            self._files.clear()
            self._json.clear()

    def __enter__(self) -> Source:
        if self._closed:
            raise ValueError("source is closed")
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()
