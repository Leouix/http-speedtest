#!/usr/bin/env python3
"""Measure sequential HTTP download speed.

The script requests one URL N times in a row (10 by default), waits for the
complete response body each time, then reports average request time, total
downloaded volume and resulting download speed.

Only the Python standard library is used, so there is nothing to install.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

CHUNK_SIZE = 64 * 1024  # 64 KiB per read: large enough to keep syscalls cheap
USER_AGENT = "http-speedtest/1.0 (+https://github.com/Leouix/http-speedtest)"


def fetch_once(url: str, timeout: float) -> dict:
    """Download the whole response body once and time it.

    The measured interval starts before DNS/TCP/TLS and ends after the last
    body byte, so it is the user-visible latency of a complete request.
    """
    request = urllib.request.Request(
        url,
        headers={
            # Ask for the body as-is: gzip would shrink the wire size and
            # distort "bytes downloaded" for compressible files.
            "Accept-Encoding": "identity",
            # Avoid a proxy or CDN answering from its own cache.
            "Cache-Control": "no-cache",
            "User-Agent": USER_AGENT,
        },
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared_length = response.headers.get("Content-Length")
            received = 0
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                received += len(chunk)
            status = response.status
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed = time.perf_counter() - started
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()  # release the socket before reporting the error
        return {
            "ok": False,
            "elapsed_s": elapsed,
            "bytes": 0,
            "status": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    elapsed = time.perf_counter() - started
    return {
        "ok": True,
        "elapsed_s": elapsed,
        "bytes": received,
        "status": status,
        "declared_length": int(declared_length) if declared_length else None,
        "error": None,
    }


def bits_per_second(byte_count: int, seconds: float) -> float:
    """Convert a byte count over an interval into bits per second."""
    return (byte_count * 8) / seconds if seconds > 0 else 0.0


def summarize(results: list[dict]) -> dict:
    """Aggregate per-request results into the numbers printed at the end."""
    finished = [r for r in results if r["ok"]]
    total_bytes = sum(r["bytes"] for r in finished)
    total_seconds = sum(r["elapsed_s"] for r in finished)
    times = [r["elapsed_s"] for r in finished]
    speeds = [bits_per_second(r["bytes"], r["elapsed_s"]) for r in finished]

    return {
        "requests": len(results),
        "succeeded": len(finished),
        "failed": len(results) - len(finished),
        "total_bytes": total_bytes,
        "total_seconds": total_seconds,
        "avg_seconds": statistics.fmean(times) if times else 0.0,
        "min_seconds": min(times) if times else 0.0,
        "max_seconds": max(times) if times else 0.0,
        # Mean of per-request speeds answers "how fast is one request".
        "avg_speed_bps": statistics.fmean(speeds) if speeds else 0.0,
        # Total bytes over total time answers "how fast did data actually flow".
        "effective_speed_bps": bits_per_second(total_bytes, total_seconds),
    }


def format_bytes(byte_count: float) -> str:
    """Human-readable size using decimal units (1 MB = 1 000 000 bytes)."""
    for unit, factor in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if byte_count >= factor or unit == "KB":
            return f"{byte_count / factor:.2f} {unit}"
    return f"{byte_count:.0f} B"


def format_speed(bits_per_s: float) -> str:
    """Human-readable speed using decimal units (1 Mbit/s = 1 000 000 bit/s)."""
    for unit, factor in (("Gbit/s", 1e9), ("Mbit/s", 1e6), ("Kbit/s", 1e3)):
        if bits_per_s >= factor or unit == "Kbit/s":
            return f"{bits_per_s / factor:.2f} {unit}"
    return f"{bits_per_s:.0f} bit/s"


def run(url: str, runs: int, timeout: float, cache_bust: bool) -> int:
    """Execute the measurement and print a per-request table plus a summary."""
    print(f"URL:   {url}")
    print(f"Runs:  {runs} sequential requests, timeout {timeout:g}s per request")
    print()

    header = f"{'#':>3}  {'status':>6}  {'time, s':>8}  {'size':>10}  {'speed':>12}"
    print(header)
    print("-" * len(header))

    results: list[dict] = []
    for index in range(1, runs + 1):
        target = url
        if cache_bust:
            separator = "&" if "?" in url else "?"
            target = f"{url}{separator}run={index}-{int(time.time() * 1000)}"

        result = fetch_once(target, timeout)
        results.append(result)

        if result["ok"]:
            speed = bits_per_second(result["bytes"], result["elapsed_s"])
            print(
                f"{index:>3}  {result['status']:>6}  {result['elapsed_s']:>8.3f}"
                f"  {format_bytes(result['bytes']):>10}  {format_speed(speed):>12}"
            )
            declared = result["declared_length"]
            if declared is not None and declared != result["bytes"]:
                print(
                    f"     warning: got {result['bytes']} bytes, "
                    f"Content-Length said {declared}",
                    file=sys.stderr,
                )
        else:
            print(f"{index:>3}  {'FAIL':>6}  {result['elapsed_s']:>8.3f}  {'-':>10}  {'-':>12}")
            print(f"     {result['error']}", file=sys.stderr)

    stats = summarize(results)
    print()
    if stats["succeeded"] == 0:
        print("No request completed successfully.", file=sys.stderr)
        return 1

    print(f"Requests:        {stats['succeeded']} ok, {stats['failed']} failed")
    print(f"Downloaded:      {format_bytes(stats['total_bytes'])}")
    print(f"Average time:    {stats['avg_seconds']:.3f} s "
          f"(min {stats['min_seconds']:.3f}, max {stats['max_seconds']:.3f})")
    print(f"Average speed:   {format_speed(stats['avg_speed_bps'])} "
          f"({stats['avg_speed_bps'] / 8e6:.2f} MB/s per request)")
    print(f"Overall speed:   {format_speed(stats['effective_speed_bps'])} "
          f"({stats['effective_speed_bps'] / 8e6:.2f} MB/s for all requests)")

    return 0 if stats["failed"] == 0 else 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure download speed with sequential full-body HTTP requests.",
        epilog="Speeds are reported in Mbit/s (1 Mbit/s = 1 000 000 bit/s).",
    )
    parser.add_argument("url", help="URL to download, for example a large image")
    parser.add_argument(
        "-n", "--runs", type=int, default=10,
        help="number of sequential requests (default: 10)",
    )
    parser.add_argument(
        "-t", "--timeout", type=float, default=30.0,
        help="timeout per request in seconds (default: 30)",
    )
    parser.add_argument(
        "--cache-bust", action="store_true",
        help="append a unique query string to every request to bypass caches",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print the summary as JSON instead of the table",
    )
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.json:
        return run(args.url, args.runs, args.timeout, args.cache_bust)

    results = [fetch_once(args.url, args.timeout) for _ in range(args.runs)]
    payload = {"url": args.url, **summarize(results)}
    payload["results"] = results
    print(json.dumps(payload, indent=2))
    return 0 if payload["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
