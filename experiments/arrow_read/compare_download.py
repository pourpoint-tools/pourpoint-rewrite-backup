"""Fresh-process follow-up: native projection versus sequential full-object download."""

from __future__ import annotations

import time

PROCESS_START = time.perf_counter()

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
from pathlib import Path

import boto3
import numpy as np
import psutil
import pyarrow as pa
import pyarrow.fs as fs
from botocore.config import Config

from read_graph import PROJECTION, Graph, read_graph

IMPORT_SECONDS = time.perf_counter() - PROCESS_START
CHUNK_BYTES = 8 * 1024 * 1024


def assert_same_graph(actual: Graph, expected: Graph) -> None:
    """Exact ordered values, including levels and adjacency boundaries, not hashes alone."""
    for name in ("ids", "levels", "offsets", "edges"):
        if not np.array_equal(getattr(actual, name), getattr(expected, name)):
            raise AssertionError(f"graph differs: {name}")
    if not actual.table.equals(expected.table):
        raise AssertionError("graph differs: Arrow table")


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["native", "download", "reuse"])
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrent-activity", required=True)
    args = parser.parse_args()
    identity = json.loads(args.identity.read_text())
    setup_start = time.perf_counter()
    pa.set_cpu_count(8)
    pa.set_io_thread_count(8)
    filesystem = None
    client = None
    before = None
    check_seconds = 0.0
    source = str(args.reference)
    if args.mode != "reuse":
        session = boto3.Session(profile_name="hetzner-pourpoint", region_name="fsn1")
        client = session.client(
            "s3",
            endpoint_url=identity["endpoint"],
            config=Config(
                connect_timeout=10,
                read_timeout=60,
                retries={"mode": "standard", "total_max_attempts": 3},
                max_pool_connections=10,
                s3={"addressing_style": "path"},
            ),
        )
        if args.mode == "native":
            credentials = session.get_credentials()
            if credentials is None:
                raise RuntimeError("profile credentials unavailable")
            frozen = credentials.get_frozen_credentials()
            filesystem = fs.S3FileSystem(
                access_key=frozen.access_key,
                secret_key=frozen.secret_key,
                session_token=frozen.token,
                endpoint_override=identity["endpoint"],
                region="fsn1",
                connect_timeout=10,
                request_timeout=60,
                retry_strategy=fs.AwsStandardS3RetryStrategy(max_attempts=3),
                force_virtual_addressing=False,
            )
            source = identity["bucket"] + "/" + identity["key"]
    setup_seconds = time.perf_counter() - setup_start
    if client is not None:
        check_start = time.perf_counter()
        head = client.head_object(Bucket=identity["bucket"], Key=identity["key"])
        before = {"etag": head["ETag"], "size_bytes": head["ContentLength"]}
        if before != {k: identity[k] for k in before}:
            raise AssertionError("remote identity changed before read")
        check_seconds = time.perf_counter() - check_start
    download_seconds = 0.0
    downloaded_bytes = 0
    if args.mode == "download":
        if args.destination is None or client is None:
            raise ValueError("download requires a fresh --destination")
        source = str(args.destination)
        download_start = time.perf_counter()
        # Exclusive creation prevents silently reusing a previous download.
        with args.destination.open("xb") as target:
            response = client.get_object(Bucket=identity["bucket"], Key=identity["key"])
            body = response["Body"]
            try:
                for chunk in body.iter_chunks(chunk_size=CHUNK_BYTES):
                    target.write(chunk)
                    downloaded_bytes += len(chunk)
            finally:
                body.close()
        download_seconds = time.perf_counter() - download_start
    graph, timings, storage = read_graph(source, "prefetch", filesystem)
    ready_seconds = time.perf_counter() - PROCESS_START
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
        1 if platform.system() == "Darwin" else 1024
    )
    rss = psutil.Process().memory_info().rss
    arrow_live = pa.total_allocated_bytes()
    arrow_peak = pa.default_memory_pool().max_memory()
    # Everything below is post-readiness verification, excluded from time and peak.
    after = None
    if client is not None:
        head = client.head_object(Bucket=identity["bucket"], Key=identity["key"])
        after = {"etag": head["ETag"], "size_bytes": head["ContentLength"]}
        if before != after:
            raise AssertionError("remote identity changed during read")
    reference_hash = sha256_file(args.reference)
    if reference_hash != identity["sha256"]:
        raise AssertionError("reference checksum mismatch")
    download_hash = None
    if args.mode == "download":
        download_hash = sha256_file(args.destination)
        if download_hash != reference_hash or downloaded_bytes != identity["size_bytes"]:
            raise AssertionError("download differs from original artifact")
    expected, _, _ = read_graph(str(args.reference), "prefetch", None)
    assert_same_graph(graph, expected)
    record = {
        "status": "ok",
        "mode": args.mode,
        "experiment_commit": args.commit,
        "object_identity": identity,
        "source": source,
        "reference": str(args.reference),
        "remote_before": before,
        "remote_after": after,
        "reference_sha256": reference_hash,
        "download_sha256": download_hash,
        "exact_graph_equality": True,
        "equality_method": "post-ready array_equal ids/levels/offsets/edges and Arrow Table.equals",
        "rows": len(graph.ids),
        "edges": len(graph.edges),
        "timings": {
            "imports_seconds": IMPORT_SECONDS,
            "setup_seconds": setup_seconds,
            "pre_read_identity_check_seconds": check_seconds,
            "download_seconds": download_seconds,
            **timings,
            "total_ready_seconds": ready_seconds,
        },
        "process_peak_at_ready_bytes": peak,
        "rss_at_ready_bytes": rss,
        "peak_method": "process ru_maxrss captured at ready, before verification",
        "arrow_live_at_ready_bytes": arrow_live,
        "arrow_pool_peak_at_ready_bytes": arrow_peak,
        "buffers": graph.buffers(),
        "storage": storage,
        "downloaded_object_bytes": downloaded_bytes if args.mode == "download" else None,
        "settings": {
            "projection": PROJECTION,
            "pre_buffer": True,
            "use_threads": True,
            "buffer_size": 0,
            "memory_map": False,
            "arrow_cpu_count": pa.cpu_count(),
            "arrow_io_thread_count": pa.io_thread_count(),
            "arrow_s3": {
                "connect_timeout_seconds": 10,
                "request_timeout_seconds": 60,
                "retry_strategy": "AwsStandardS3RetryStrategy",
                "max_attempts": 3,
                "force_virtual_addressing": False,
            },
            "boto3": {
                "profile": "hetzner-pourpoint",
                "region": "fsn1",
                "connect_timeout_seconds": 10,
                "read_timeout_seconds": 60,
                "retry_mode": "standard",
                "total_max_attempts": 3,
                "max_pool_connections": 10,
                "addressing_style": "path",
            },
            "download": {
                "method": "single sequential get_object StreamingBody.iter_chunks",
                "chunk_bytes": CHUNK_BYTES,
                "transfer_manager": False,
                "destination_creation": "exclusive xb",
                "fsync": False,
            },
        },
        "versions": {
            name: importlib.metadata.version(name)
            for name in ["pyarrow", "numpy", "psutil", "boto3", "botocore"]
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "state": "fresh process; new destination for download; OS/remote cache uncontrolled",
        "concurrent_activity": args.concurrent_activity,
        "remote_requests": "not measured",
        "remote_response_bytes": "not measured",
        "actual_retries": "not measured",
        "excluded": [
            "interpreter startup",
            "post-ready identity/equality checks",
            "traversal",
            "parents",
            "catchment locator",
            "geometry",
            "snap",
            "rasters",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
