"""Source-scoped storage, pinned range access and verified original graph files.

obstore owns transport/authentication; fsspec owns file positioning and buffering.
The narrow range hook adds HFX integrity checks not exposed by obstore.open_reader.
Readers are source-owned, reusable, and not a concurrent-query interface.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from types import TracebackType
from typing import TYPE_CHECKING, Any, BinaryIO, cast
from urllib.parse import unquote, urlsplit
from weakref import WeakSet

import obstore
import pyarrow.parquet as pq
from fsspec.caching import ReadAheadCache
from fsspec.spec import AbstractBufferedFile
from obstore.store import HTTPStore, LocalStore, ObjectStore, S3Store

from pourpoint.cache import RangeCache
from pourpoint.metadata import Fingerprint, artifact_path


@dataclass
class TransferStats:
    """Source transport calls (including HEAD), payload bytes and disk-cache hits."""

    requests: int = 0
    transferred_bytes: int = 0
    cache_hit_bytes: int = 0


def default_cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "pourpoint"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "pourpoint"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "pourpoint"


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
        super().__init__(
            None,
            path,
            mode="rb",
            size=identity.size,
            block_size=65536,
            cache_type="none" if source.cache else "readahead",
        )

    # GDAL tracks opener cursors by identity, not fsspec filesystem metadata.
    __hash__ = object.__hash__

    def __eq__(self, other: object) -> bool:
        return self is other

    def _fetch_range(self, start: int, end: int) -> bytes:
        end = min(end, self.identity.size)
        if start >= end:
            return b""
        return self.source._range(self.path, self.identity, start, end)


class Source:
    """Own a dataset's configured store and open artifacts.

    HTTPS is anonymous HTTP, never S3 autodetection. S3 constructor options accept
    endpoint/region/credentials and credential providers. Never persist credentials.
    Manifest fingerprints bind content; strong ETags or versions bind remote reads.
    Local files are checked using inode, device, size and nanosecond modification time.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        storage_options: Mapping[str, Any] | None = None,
        cache_dir: str | Path | None = None,
        cache: bool = False,
        offline: bool = False,
    ) -> None:
        if offline and not cache:
            raise ValueError("offline access requires cache=True")
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self.cache = cache
        self.offline = offline
        self.stats = TransferStats()
        self.range_workers = 4
        self._stats_lock = Lock()
        self._storage_options = dict(storage_options or {})
        self._children: dict[str, Source] = {}
        options = dict(storage_options or {})
        parsed = urlsplit(str(root))
        self._origin = parsed.scheme, parsed.netloc
        self._local_root: Path | None = None
        self._closed = False
        self._files: dict[str, _ArtifactFile] = {}
        self._independent_files: WeakSet[_ArtifactFile] = WeakSet()
        self._graphs: dict[Path, tuple[int, int, int, int]] = {}
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
            # The root manifest can be amended by HFX publication. Revalidate
            # origin responses; read_json still retains one lifetime snapshot.
            headers = {k: v for k, v in headers.items() if k.lower() != "cache-control"}
            headers["Cache-Control"] = "no-cache"
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
        self._cache = RangeCache(self.cache_dir, self.identity) if cache else None

    def external(self, url: str) -> tuple[Source, str]:
        """Share lifecycle/cache/counters; cross-origin external access is anonymous.

        Transport options, including credentials, are forwarded only to the same
        scheme and authority. External private hosts require separate configuration.
        """
        root, path = url.rsplit("/", 1)
        parsed = urlsplit(url)
        options = self._storage_options if (parsed.scheme, parsed.netloc) == self._origin else {}
        if root not in self._children:
            child = Source(
                root,
                storage_options=options,
                cache_dir=self.cache_dir,
                cache=self.cache,
                offline=self.offline,
            )
            child.stats = self.stats
            child._stats_lock = self._stats_lock
            self._children[root] = child
        return self._children[root], path

    def _cache_key(self, path: str) -> str:
        fingerprint = self._fingerprints.get(path)
        return json.dumps([path, asdict(fingerprint) if fingerprint else None], sort_keys=True)

    def read_ranges(
        self, path: str, fingerprint: Fingerprint, ranges: list[tuple[int, int]], *, max_workers: int = 4
    ) -> list[bytes]:
        """Acquire exact ranges concurrently without sharing mutable file cursors.

        At most max_workers transfers run at once; returned bytes occupy the sum
        of requested lengths. This is explicit batch acquisition, not unbounded prefetch.
        """
        self.open_artifact(path, fingerprint)
        identity = self._files[path].identity
        merged: list[tuple[int, int]] = []
        for start, end in sorted(ranges):
            if not 0 <= start < end <= identity.size:
                raise ValueError("artifact range out of bounds")
            if merged and start < merged[-1][1]:
                merged[-1] = merged[-1][0], max(end, merged[-1][1])
            else:
                merged.append((start, end))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            acquired = list(executor.map(lambda bounds: self._range(path, identity, *bounds), merged))
        return [
            data[start - left : end - left]
            for start, end in ranges
            for (left, right), data in zip(merged, acquired, strict=True)
            if left <= start and end <= right
        ]

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
        with self._stats_lock:
            self.stats.requests += 1
        meta = obstore.head(self._store, path)
        etag, version = meta["e_tag"], meta["version"]
        if not version and (not etag or etag.startswith("W/")):
            raise ValueError(f"{path}: a strong ETag or immutable object version is required")
        return _Identity(meta["size"], etag, version)

    def _range(self, path: str, identity: _Identity, start: int, end: int) -> bytes:
        self._path(path)
        if not 0 <= start < end <= identity.size:
            raise ValueError("artifact range out of bounds")
        if not self.offline and identity.local_stat is not None and self._stat(path) != identity.local_stat:
            raise ValueError(f"{path}: artifact changed")
        if self._cache is None:
            return self._fetch_range(path, identity, start, end)
        key = self._cache_key(path)
        pieces = self._cache.pieces(key, start, end)
        output = bytearray()
        position = start
        for offset, data in pieces:
            if position >= end:
                break
            if offset > position:
                stop = min(offset, end)
                output.extend(self._fetch_range(path, identity, position, stop))
                position = stop
            stop = min(offset + len(data), end)
            if stop > position:
                output.extend(data[position - offset : stop - offset])
                with self._stats_lock:
                    self.stats.cache_hit_bytes += stop - position
                position = stop
        if position < end:
            output.extend(self._fetch_range(path, identity, position, end))
        return bytes(output)

    def _fetch_range(self, path: str, identity: _Identity, start: int, end: int) -> bytes:
        if self.offline:
            raise ValueError(f"offline cache missing bytes for {path} [{start}, {end})")
        options: GetOptions = {"range": (start, end)}
        if identity.version:
            options["version"] = identity.version
        if identity.etag:
            options["if_match"] = identity.etag
        if identity.local_stat is not None and self._stat(path) != identity.local_stat:
            raise ValueError(f"{path}: artifact changed")
        with self._stats_lock:
            self.stats.requests += 1
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
        with self._stats_lock:
            self.stats.transferred_bytes += len(data)
        if self._cache is not None:
            self._cache.put(self._cache_key(path), start, data)
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
            self._fingerprints[path] = fingerprint
            key = "identity:" + self._cache_key(path)
            saved = self._cache.metadata(key) if self._cache else None
            if saved is not None:
                value = json.loads(saved)
                if value["local_stat"] is not None:
                    value["local_stat"] = tuple(value["local_stat"])
                identity = _Identity(**value)
            else:
                if self.offline:
                    raise ValueError(f"offline cache missing identity for {path}")
                identity = self._identity(path)
                if self._cache:
                    saved = self._cache.pin(key, json.dumps(asdict(identity)).encode())
                    value = json.loads(saved)
                    if value["local_stat"] is not None:
                        value["local_stat"] = tuple(value["local_stat"])
                    identity = _Identity(**value)
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

    def raster_file(self, path: str, fingerprint: Fingerprint) -> BinaryIO:
        """Caller-owned cursor over this source's existing pinned artifact identity.

        Used by GDAL's opener, not Rasterio's eager file-object/MemoryFile path.
        Closing the returned cursor does not close other readers of the artifact.
        """
        self.open_artifact(path, fingerprint, bounded=True)
        stream = _ArtifactFile(self, path, self._files[path].identity)
        self._independent_files.add(stream)
        return cast(BinaryIO, stream)

    def check_artifact(self, path: str) -> None:
        """Check local identity even when a native reader satisfies a cached read.

        Remote cached bytes retain their pinned identity; new ranges enforce that
        identity. This does not check whether the latest remote version changed.
        """
        self._path(path)
        identity = self._files[path].identity
        if not self.offline and identity.local_stat is not None and self._stat(path) != identity.local_stat:
            raise ValueError(f"{path}: artifact changed")

    def read_json(self, path: str, fingerprint: Fingerprint | None = None) -> dict[str, object]:
        path = self._path(path)
        if path in self._json:
            saved, value = self._json[path]
            if fingerprint != saved:
                raise ValueError(f"{path}: inconsistent artifact fingerprint")
            return dict(value)
        if fingerprint is None:
            snapshot = self._cache.metadata("json:" + path) if self._cache else None
            if snapshot is not None:
                data = snapshot
            else:
                if self.offline:
                    raise ValueError(f"offline cache missing JSON snapshot for {path}")
                identity = self._identity(path)
                data = self._fetch_range(path, identity, 0, identity.size)
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}: expected JSON object")
                if self._cache:
                    data = self._cache.pin("json:" + path, data)
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

    def prefetch_parquet(
        self,
        path: str,
        fingerprint: Fingerprint,
        group: int,
        columns: list[str] | None = None,
    ) -> None:
        """Cache projected column chunks with bounded concurrent exact transfers.

        Runs only with persistent caching. Each transfer is at most 8 MiB, with
        at most range_workers payloads alive; no whole-world scheduling or output
        list. This bounds transfer buffers, not Parquet decompression or RSS.
        """
        if not self.cache or self.offline:
            return
        reader = self.parquet(path, fingerprint)
        metadata = reader.metadata.row_group(group)
        ranges: list[tuple[int, int]] = []
        for index in range(metadata.num_columns):
            chunk = metadata.column(index)
            if columns is not None and not any(
                chunk.path_in_schema == name or chunk.path_in_schema.startswith(name + ".") for name in columns
            ):
                continue
            start = chunk.data_page_offset
            if chunk.has_dictionary_page:
                start = min(start, chunk.dictionary_page_offset)
            end = start + chunk.total_compressed_size
            for offset in range(start, end, 8 * 1024 * 1024):
                ranges.append((offset, min(offset + 8 * 1024 * 1024, end)))
        identity = self._files[path].identity

        def acquire(bounds: tuple[int, int]) -> None:
            self._range(path, identity, *bounds)

        with ThreadPoolExecutor(max_workers=self.range_workers) as executor:
            for offset in range(0, len(ranges), self.range_workers):
                list(executor.map(acquire, ranges[offset : offset + self.range_workers]))

    def graph_path(self, cache_dir: str | Path, path: str, fingerprint: Fingerprint) -> Path:
        """Verify/reuse an original graph; publish completed downloads atomically.

        Local originals are used in place. Remote cache keys include source and full
        manifest digest. Reopening checks full cached graph SHA-256, not catchments.
        """
        path = self._path(path)
        if self._local_root is not None:
            destination = self._local_root / path
            self._graphs[destination] = self._verify_file(destination, fingerprint)
            return destination
        directory = Path(cache_dir) / self.identity
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{fingerprint.sha256}.parquet"
        if destination.exists():
            try:
                self._graphs[destination] = self._verify_file(destination, fingerprint)
                return destination
            except ValueError:
                pass
        self.open_artifact(path, fingerprint)
        identity = self._files[path].identity
        if self.offline:
            raise ValueError(f"offline cache missing original graph {path}")
        options: GetOptions = {}
        if identity.etag:
            options["if_match"] = identity.etag
        if identity.version:
            options["version"] = identity.version
        with self._stats_lock:
            self.stats.requests += 1
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
                    with self._stats_lock:
                        self.stats.transferred_bytes += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            identity = self._verify_file(temporary, fingerprint)
            os.replace(temporary, destination)
            self._graphs[destination] = identity
            self.check_graph(destination)
            return destination
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int, int, int]:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    def check_graph(self, path: Path) -> None:
        """Check the verified original before/after local Arrow reads, without hashing.

        The local immutable-file trust model detects replacement, resizing and normal
        modification. It does not defend against a writer restoring file timestamps.
        """
        if self._closed:
            raise ValueError("source is closed")
        if path not in self._graphs:
            raise ValueError("graph was not verified by this source")
        try:
            identity = self._file_identity(path)
        except OSError as exc:
            raise ValueError(f"{path.name}: graph artifact changed") from exc
        if identity != self._graphs[path]:
            raise ValueError(f"{path.name}: graph artifact changed")

    @classmethod
    def _verify_file(cls, path: Path, fingerprint: Fingerprint) -> tuple[int, int, int, int]:
        before = cls._file_identity(path)
        if before[2] != fingerprint.size_bytes:
            raise ValueError(f"{path.name}: artifact size mismatch")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if cls._file_identity(path) != before:
            raise ValueError(f"{path.name}: graph artifact changed during verification")
        if digest != fingerprint.sha256:
            raise ValueError(f"{path.name}: full SHA-256 mismatch")
        return before

    @property
    def buffer_bytes(self) -> int:
        """Bytes retained by fsspec read-ahead, excluding Arrow's internal buffers.

        This counts serialized response buffers, not total process memory. Parquet
        pre-buffering and decoded Arrow batches have separate ownership/accounting.
        """
        return sum(child.buffer_bytes for child in self._children.values()) + sum(
            len(stream.cache.cache)
            for stream in (*self._files.values(), *self._independent_files)
            if not stream.closed and isinstance(stream.cache, ReadAheadCache)
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            for reader in self._parquet.values():
                reader.close(force=True)
        finally:
            for stream in (*self._files.values(), *self._independent_files):
                stream.close()
            self._parquet.clear()
            self._files.clear()
            self._json.clear()
            for child in self._children.values():
                child.close()
            if self._cache:
                self._cache.close()

    def __enter__(self) -> Source:
        if self._closed:
            raise ValueError("source is closed")
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()
