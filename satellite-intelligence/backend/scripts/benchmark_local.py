from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import logging
import math
import mmap
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import threading
import time
from uuid import uuid4


BACKEND_DIR = Path(__file__).resolve().parents[1]
CLOUD_ENVIRONMENT_KEYS = (
    "DATABASE_URL",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ANON_KEY",
)


def _resident_bytes() -> int | None:
    if platform.system() == "Windows":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        succeeded = psapi.GetProcessMemoryInfo(
            process, ctypes.byref(counters), counters.cb
        )
        return int(counters.WorkingSetSize) if succeeded else None
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return pages * mmap.PAGESIZE
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _write_synthetic_bands(data_directory: Path, size: int) -> None:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    current = data_directory / "current"
    current.mkdir(parents=True, exist_ok=True)
    generator = np.random.default_rng(1401)
    for code in ("B02", "B03", "B04", "B08", "B11"):
        path = current / f"{code}.tif"
        profile = {
            "driver": "GTiff",
            "width": size,
            "height": size,
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:32644",
            "transform": from_origin(500000, 3000000, 10, 10),
            "nodata": -9999,
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
        }
        values = generator.uniform(0.1, 0.8, size=(size, size)).astype(np.float32)
        with rasterio.open(path, "w", **profile) as output:
            output.write(values, 1)
            output.update_tags(
                SATELLITE_VISION_DATA_KIND="synthetic",
                SOURCE="local performance benchmark fixture",
            )


def _parse_clients(value: str) -> list[int]:
    try:
        clients = [int(part) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Client levels must be comma-separated integers.") from exc
    if not clients or any(client < 1 or client > 25 for client in clients):
        raise argparse.ArgumentTypeError("Each client level must be between 1 and 25.")
    if len(set(clients)) != len(clients):
        raise argparse.ArgumentTypeError("Client levels must not repeat.")
    return clients


def _percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _run_level(app, clients: int, requests_per_client: int) -> dict:
    from fastapi.testclient import TestClient

    barrier = threading.Barrier(clients)
    measurements: list[tuple[float, int | None, str | None]] = []
    measurements_lock = threading.Lock()
    peak_rss = {"bytes": _resident_bytes()}
    stop_monitor = threading.Event()

    def sample_rss() -> None:
        while not stop_monitor.wait(0.025):
            value = _resident_bytes()
            if value is not None:
                with measurements_lock:
                    if peak_rss["bytes"] is None or value > peak_rss["bytes"]:
                        peak_rss["bytes"] = value

    def run_client() -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            barrier.wait(timeout=15)
            for _ in range(requests_per_client):
                started = time.perf_counter()
                try:
                    response = client.post(
                        "/api/analyze/ndvi",
                        headers={"Idempotency-Key": uuid4().hex},
                    )
                    elapsed = time.perf_counter() - started
                    with measurements_lock:
                        measurements.append((elapsed, response.status_code, None))
                except Exception as exc:
                    elapsed = time.perf_counter() - started
                    with measurements_lock:
                        measurements.append((elapsed, None, type(exc).__name__))

    monitor = threading.Thread(target=sample_rss, daemon=True)
    monitor.start()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=clients) as executor:
        futures = [executor.submit(run_client) for _ in range(clients)]
        for future in futures:
            future.result()
    elapsed = time.perf_counter() - started
    stop_monitor.set()
    monitor.join(timeout=1)

    latencies = [entry[0] for entry in measurements]
    p95_latency = _percentile95(latencies)
    successful_latencies = [
        latency for latency, status, exception in measurements
        if status is not None and 200 <= status < 300 and exception is None
    ]
    successful_p95 = _percentile95(successful_latencies)
    statuses: dict[str, int] = {}
    exceptions: dict[str, int] = {}
    for _, status, exception in measurements:
        if status is not None:
            key = str(status)
            statuses[key] = statuses.get(key, 0) + 1
        if exception is not None:
            exceptions[exception] = exceptions.get(exception, 0) + 1
    successful = sum(count for status, count in statuses.items() if status.startswith("2"))
    capacity_rejections = statuses.get("503", 0)
    total = len(measurements)
    return {
        "simulated_clients": clients,
        "requests": total,
        "successful_requests": successful,
        "capacity_rejections": capacity_rejections,
        "http_status_counts": statuses,
        "exception_counts": exceptions,
        "error_rate_percent": round((total - successful) / total * 100, 2) if total else 0,
        "throughput_requests_per_second": round(total / elapsed, 2) if elapsed else None,
        "successful_analysis_throughput_per_second": round(
            successful / elapsed, 2
        ) if elapsed else None,
        "median_latency_ms": round(statistics.median(latencies) * 1000, 2) if latencies else None,
        "p95_latency_ms": round(p95_latency * 1000, 2)
        if p95_latency is not None
        else None,
        "successful_request_median_latency_ms": round(
            statistics.median(successful_latencies) * 1000, 2
        ) if successful_latencies else None,
        "successful_request_p95_latency_ms": round(successful_p95 * 1000, 2)
        if successful_p95 is not None
        else None,
        "workload_elapsed_seconds": round(elapsed, 3),
        "peak_resident_memory_bytes": peak_rss["bytes"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a bounded, local-only synthetic raster API benchmark."
    )
    parser.add_argument("--raster-size", type=int, default=512)
    parser.add_argument("--clients", type=_parse_clients, default=_parse_clients("1,5,10,25"))
    parser.add_argument("--requests-per-client", type=int, default=1)
    args = parser.parse_args()
    if not 32 <= args.raster_size <= 4096:
        parser.error("--raster-size must be between 32 and 4096 pixels per side.")
    if not 1 <= args.requests_per_client <= 5:
        parser.error("--requests-per-client must be between 1 and 5.")
    if args.raster_size * args.raster_size > 25_000_000:
        parser.error("The synthetic benchmark raster exceeds the hard 25-million-pixel ceiling.")

    configured_cloud_keys = [
        key for key in CLOUD_ENVIRONMENT_KEYS if os.environ.get(key)
    ]
    if configured_cloud_keys:
        parser.error(
            "Local load test refused because cloud credentials/configuration are present: "
            + ", ".join(configured_cloud_keys)
            + ". Remove them from this local process before running the synthetic test."
        )

    sys.path.insert(0, str(BACKEND_DIR))
    with tempfile.TemporaryDirectory(prefix="satellite-local-benchmark-") as temporary:
        root = Path(temporary)
        data_directory = root / "data"
        output_directory = data_directory / "outputs"
        os.environ["DATA_DIR"] = str(data_directory)
        os.environ["OUTPUT_DIR"] = str(output_directory)
        os.environ["SATELLITE_CACHE_DIR"] = str(data_directory / "live")
        os.environ["REQUIRE_AUTH"] = "false"
        _write_synthetic_bands(data_directory, args.raster_size)

        from main import app

        logging.getLogger("satellite-intelligence").setLevel(logging.ERROR)
        results = [
            _run_level(app, level, args.requests_per_client)
            for level in args.clients
        ]
        print(
            json.dumps(
                {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "environment": {
                        "mode": "local-only",
                        "os": platform.platform(),
                        "python": platform.python_version(),
                        "data": "deterministic synthetic GeoTIFFs",
                        "raster_pixels": args.raster_size * args.raster_size,
                        "database": "not configured; database pool not exercised",
                        "storage": "local temporary disk; Supabase not exercised",
                        "cloud_probe": False,
                    },
                    "requests_per_client": args.requests_per_client,
                    "results": results,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
