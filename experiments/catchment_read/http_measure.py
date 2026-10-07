"""Fresh HTTP worker; reuse local timing, memory and witness boundaries."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pyarrow import ipc

from http_reader import HttpCatchmentReader
from measure import measure
from range_server import RangeServer


def maximum_overlap(intervals: Iterable[tuple[int, int]]) -> int:
    """Maximum simultaneous half-open intervals; ends precede tied starts."""
    endpoints = [
        (point, delta)
        for start, end in intervals
        if end > start
        for point, delta in ((start, 1), (end, -1))
    ]
    active = peak = 0
    for _, delta in sorted(endpoints):
        active += delta
        peak = max(peak, active)
    return peak


class MeasuredReader(HttpCatchmentReader):
    def __init__(self, root: str, *, experimental: bool) -> None:
        self.boundaries: list[tuple[int, int]] = []
        super().__init__(root, experimental=experimental)

    def io_counts(self) -> dict[str, dict[str, int]]:
        counts = super().io_counts()
        self.boundaries.append((len(self.transport.events), len(self.transport.plans)))
        return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--selections", type=Path, required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--witness-dir", type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--experimental", action="store_true")
    parser.add_argument("--delay-seconds", type=float, choices=[0, 0.02], required=True)
    args = parser.parse_args()
    requests = json.loads(args.selections.read_text())["workloads"][args.workload]
    readers: list[MeasuredReader] = []
    with RangeServer({"data": args.data}, delay_seconds=args.delay_seconds) as server:

        def factory(_root: Path, experimental: bool) -> MeasuredReader:
            reader = MeasuredReader(server.url + "/data", experimental=experimental)
            readers.append(reader)
            return reader

        result, tables = measure(
            args.data, requests, experimental=args.experimental, reader_factory=factory
        )
    # Reader has closed. Server exit waits for its final completed event records.
    reader = readers[0]
    events = reader.transport.events
    server_events = server.events
    by_id = {e["request_id"]: e for e in server_events}
    if len(by_id) != len(server_events) or len(
        {e["request_id"] for e in events}
    ) != len(events):
        raise AssertionError("duplicate HTTP request IDs")
    for event in events:
        served_event = by_id[event["request_id"]]
        expected_range = (
            f"bytes={event['start']}-{event['end'] - 1}"
            if event["method"] == "GET"
            else None
        )
        if (
            served_event["method"],
            served_event["path"],
            served_event["status"],
            served_event["payload_bytes"],
            served_event["range"],
        ) != (
            event["method"],
            "/data/" + event["path"],
            event["status"],
            event["body_bytes"],
            expected_range,
        ):
            raise AssertionError("per-request HTTP accounting mismatch")
    phases: dict[str, object] = {}
    previous_event = previous_plan = 0
    for name, (end_event, end_plan) in zip(
        ["open", "prepare", "call-0", "call-1"], reader.boundaries, strict=True
    ):
        client = events[previous_event:end_event]
        ids = {e["request_id"] for e in client}
        served = [e for e in server_events if e["request_id"] in ids]
        if len(served) != len(client) or sum(e["payload_bytes"] for e in served) != sum(
            e["body_bytes"] for e in client
        ):
            raise AssertionError("client/server accounting mismatch")
        plans = reader.transport.plans[previous_plan:end_plan]
        client_overlap = maximum_overlap((e["start_ns"], e["end_ns"]) for e in client)
        server_overlap = maximum_overlap((e["start_ns"], e["end_ns"]) for e in served)
        if client_overlap > 4 or server_overlap > 4:
            raise AssertionError("HTTP concurrency limit exceeded")
        phases[name] = {
            "requests": len(client),
            "head_requests": sum(e["method"] == "HEAD" for e in client),
            "get_requests": sum(e["method"] == "GET" for e in client),
            "client_body_bytes": sum(e["body_bytes"] for e in client),
            "server_payload_bytes": sum(e["payload_bytes"] for e in served),
            "client_max_overlap": client_overlap,
            "server_max_overlap": server_overlap,
            "oversized_ranges": sum(int(p["oversized_ranges"]) for p in plans),
            "coalescing_requested_bytes": sum(int(p["requested_bytes"]) for p in plans),
            "coalescing_fetched_bytes": sum(int(p["fetched_bytes"]) for p in plans),
            "coalescing_overfetch_bytes": sum(
                int(p["fetched_bytes"]) - int(p["requested_bytes"]) for p in plans
            ),
            "request_ids": sorted(ids, key=int),
        }
        previous_event, previous_plan = end_event, end_plan
    if previous_event != len(events) or len(server_events) != len(events):
        raise AssertionError("unassigned HTTP requests")
    args.witness_dir.mkdir(parents=True, exist_ok=False)
    for index, outputs in enumerate(tables):
        for request, table in enumerate(outputs):
            path = args.witness_dir / f"call-{index}-request-{request}.arrow"
            with ipc.new_file(path, table.schema) as writer:
                writer.write_table(table)
            result["calls"][index]["witnesses"].append(str(path))
    document = dict(
        result,
        workload=args.workload,
        data=str(args.data),
        code_commit=args.code_commit,
        utc=datetime.now(UTC).isoformat(),
        python=platform.python_version(),
        platform=platform.platform(),
        versions={
            p: importlib.metadata.version(p)
            for p in ["pyarrow", "numpy", "psutil", "shapely", "httpx", "flatbuffers"]
        },
        delay_seconds=args.delay_seconds,
        pinned_objects={
            name: {"size_bytes": file.size, "etag": file.etag}
            for name, file in reader.transport.files.items()
        },
        transport={
            "phases": phases,
            "client_events": events,
            "server_events": server_events,
            "plans": reader.transport.plans,
        },
        settings={
            "pre_buffer": False,
            "use_threads": False,
            "memory_map": False,
            "projection": "all source columns",
            "calls": 2,
            "cache": "metadata/locator only; no lookup/data cache between calls",
            "workers": 4,
            "coalescing_gap_bytes": 65536,
            "merged_range_limit_bytes": 8388608,
        },
        scope={
            "time": "imports, selections, server startup excluded; same local measure phase boundaries",
            "memory": "fresh process including loopback server threads/buffers; initial snapshot after server startup; no graph/reference/witness work; first output retained during repeat; both outputs and second-call geometries live at READY",
            "io": "HTTP response body bytes and completed requests, not headers/TCP/TLS bytes; server payload bytes accepted by socket",
            "overlap": "endpoint sweep of completed request timestamps; client and server measured separately",
            "cache_state": "uncontrolled filesystem cache, fresh HTTP client per process",
            "latency": "loopback plus fixed simulated per-request delay; not a production object-store model",
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n")


if __name__ == "__main__":
    main()
