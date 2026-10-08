"""One bounded worker; run workers sequentially, in fresh processes."""

import argparse
import hashlib
import json
import logging
import platform
import re
import resource
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import rasterio
from rasterio.windows import Window

from access import PairedRaster, PairedWindow, pixel_window

BASE = "https://basin-delineations-public.upstream.tech/grit/hfx-v0.3.0/"
SETTINGS = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_TIMEOUT": "30",
    "GDAL_HTTP_CONNECTTIMEOUT": "15",
    "GDAL_HTTP_MAX_RETRY": "0",
    "CPL_CURL_VERBOSE": True,
    "CPL_DEBUG": True,
}


class Traffic(logging.Handler):
    """Count libcurl sent request headers, including retries and errors."""

    def __init__(self) -> None:
        super().__init__()
        self.requests = 0
        self.responses = 0
        self.failed = 0
        self.phase = "initialization"
        self.events: list[dict[str, object]] = []

    def observe(self, message: str) -> None:
        request = re.search(r"CURL_INFO_HEADER_OUT: (GET|HEAD) (\S+) HTTP/", message)
        response = re.search(r"CURL_INFO_HEADER_IN: HTTP/\S+ (\d{3})", message)
        if request:
            self.requests += 1
            self.events.append({"phase": self.phase, "method": request[1], "path": request[2]})
        if response:
            self.responses += 1
            self.failed += int(int(response[1]) >= 400)
            self.events.append({"phase": self.phase, "status": int(response[1])})

    def emit(self, record: logging.LogRecord) -> None:
        self.observe(record.getMessage())

    def snapshot(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "responses": self.responses,
            "failed_responses": self.failed,
            "response_bytes": "not measured",
        }


def peak_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selections", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--first", type=int, default=0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    selection = json.loads(args.selections.read_text())
    traffic = Traffic()
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(traffic)
    logger.addHandler(logging.FileHandler(args.output / "gdal.log"))
    records: list[dict[str, Any]] = []
    retained: dict[str, PairedWindow] = {}
    started = time.perf_counter()

    def phase[T](name: str, action: Callable[[], T]) -> T:
        traffic.phase = name
        before = traffic.snapshot()
        start = time.perf_counter()
        record: dict[str, Any] = {"phase": name}
        try:
            result = action()
            record["status"] = "ok"
            return result
        except Exception as error:
            record.update(status="failed", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            after = traffic.snapshot()
            record.update(
                seconds=time.perf_counter() - start,
                http_requests=after["requests"] - before["requests"],
                http_responses=after["responses"] - before["responses"],
                http_failed_responses=after["failed_responses"] - before["failed_responses"],
                http_response_bytes="not measured",
                process_peak_bytes=peak_bytes(),
            )
            records.append(record)

    def timeout(signum: int, frame: object) -> None:
        raise TimeoutError("180 second worker limit")

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(180)
    report: dict[str, Any] = {
        "settings": SETTINGS,
        "python": sys.version,
        "platform": platform.platform(),
        "versions": {"rasterio": rasterio.__version__, "gdal": rasterio.__gdal_version__, "numpy": np.__version__},
        "first_index": args.first,
        "selection_sha256": hashlib.sha256(args.selections.read_bytes()).hexdigest(),
        "cache_state": "fresh process; remote/CDN caches uncontrolled; normal GDAL caches",
    }
    try:

        def declaration() -> dict[str, Any]:
            traffic.requests += 1
            response = httpx.get(BASE + "manifest.json", timeout=30)
            traffic.responses += 1
            traffic.failed += int(response.status_code >= 400)
            report["manifest"] = {
                "sha256": hashlib.sha256(response.content).hexdigest(),
                "headers": dict(response.headers),
                "decoded_manifest_bytes": len(response.content),
            }
            response.raise_for_status()
            entries = [e for e in response.json()["auxiliary"] if e["schema"] == "hfx.aux.d8_raster.v2"]
            if len(entries) != 1 or entries[0]["metadata"] != {
                "crs": "EPSG:8857",
                "flow_dir_encoding": "grass",
                "flow_acc_units": "km2",
            }:
                raise ValueError("unexpected raster declaration")
            return entries[0]

        entry = phase("declaration", declaration)
        with rasterio.Env(**SETTINGS):  # noqa: SIM117 -- separately charged opens
            with phase("open_direction", lambda: rasterio.open(BASE + entry["artifacts"]["flow_dir"])) as fd:
                with phase("open_accumulation", lambda: rasterio.open(BASE + entry["artifacts"]["flow_acc"])) as fa:
                    pair = phase("grid_metadata", lambda: PairedRaster(fd, fa))
                    report["grid"] = {
                        "transform": list(fd.transform),
                        "crs": fd.crs.to_string(),
                        "shape": fd.shape,
                        "block_shapes": fd.block_shapes,
                        "dtypes": [fd.dtypes[0], fa.dtypes[0]],
                        "nodata": ["NaN" if np.isnan(v) else v for v in (fd.nodata, fa.nodata)],
                    }

                    def read(index: int) -> PairedWindow:
                        item = selection["windows"][index]
                        col, row, width, height = item["window"]
                        window = Window(col, row, width, height)  # ty: ignore[too-many-positional-arguments]
                        if window != pixel_window(tuple(item["bounds"]), fd.transform, fd.width, fd.height):
                            raise ValueError("selection window disagrees with current grid")
                        return pair.read(window)

                    first = phase("first_pair", lambda: read(args.first))
                    retained[str(args.first)] = first
                    report["first_usable_seconds"] = time.perf_counter() - started
                    records[-1]["retained_array_bytes"] = first.retained_bytes
                    records[-1]["logical_paired_reads"] = 1
                    for repeat in range(2):
                        result = phase(f"repeat_{repeat + 1}", lambda: read(args.first))
                        records[-1].update(retained_array_bytes=result.retained_bytes, logical_paired_reads=1)
                        del result

                    def batch() -> list[PairedWindow]:
                        return [read(index) for index in range(len(selection["windows"]))]

                    results = phase("fixed_batch", batch)
                    records[-1].update(
                        retained_array_bytes=sum(r.retained_bytes for r in results), logical_paired_reads=len(results)
                    )
                    retained.update({str(i): result for i, result in enumerate(results)})
        report["status"] = "ok"
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
    finally:
        signal.alarm(0)
        report.update(
            phases=records,
            traffic=traffic.snapshot(),
            events=traffic.events,
            process_peak_bytes=peak_bytes(),
            access_seconds=time.perf_counter() - started,
        )
        # Evidence writes are after timing, not preparation hidden before first use.
        for index, result in retained.items():
            np.savez(
                args.output / f"window-{index}.npz",
                direction=result.direction,
                accumulation=result.accumulation,
                direction_valid=result.direction_valid,
                accumulation_valid=result.accumulation_valid,
            )
        (args.output / "measurement.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
