from __future__ import annotations

import argparse
import math
import re
import ssl
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_USER_AGENT = "epm-load-test/1.0"
_DURATION_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhSMH]?)\s*$")


@dataclass(slots=True)
class RequestResult:
    ok: bool
    status_code: Optional[int]
    latency_ms: float
    error: Optional[str]


def parse_header_values(header_values: Optional[Iterable[str]]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for raw_header in header_values or []:
        if ":" not in raw_header:
            raise ValueError(f"Invalid header '{raw_header}'. Expected format: Name: Value")
        name, value = raw_header.split(":", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            raise ValueError(f"Invalid header '{raw_header}'. Header name cannot be empty")
        headers[name] = value
    return headers


def parse_duration_value(raw_duration: str) -> float:
    match = _DURATION_PATTERN.match(raw_duration)
    if not match:
        raise ValueError(
            f"Invalid duration '{raw_duration}'. Use values like 600, 90s, 10m, or 1.5h"
        )

    value = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    multiplier = {"s": 1, "m": 60, "h": 3600}[unit]
    return value * multiplier


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]

    rank = math.ceil((pct / 100.0) * len(sorted_values)) - 1
    rank = max(0, min(rank, len(sorted_values) - 1))
    return sorted_values[rank]


def build_ssl_context(insecure: bool, ca_file: Optional[str]) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()
    if ca_file:
        return ssl.create_default_context(cafile=ca_file)
    return ssl.create_default_context()


def perform_request(
    url: str,
    timeout: float,
    method: str,
    headers: Dict[str, str],
    body: Optional[bytes],
    ssl_context: ssl.SSLContext,
) -> RequestResult:
    started_at = time.perf_counter()
    request = Request(url, data=body, method=method, headers=headers)

    try:
        with urlopen(request, timeout=timeout, context=ssl_context) as response:
            response.read()
            latency_ms = (time.perf_counter() - started_at) * 1000
            return RequestResult(
                ok=200 <= response.status < 400,
                status_code=response.status,
                latency_ms=latency_ms,
                error=None,
            )
    except HTTPError as exc:
        latency_ms = (time.perf_counter() - started_at) * 1000
        return RequestResult(
            ok=False,
            status_code=exc.code,
            latency_ms=latency_ms,
            error=f"HTTP {exc.code}",
        )
    except URLError as exc:
        latency_ms = (time.perf_counter() - started_at) * 1000
        reason = getattr(exc.reason, "strerror", None) or str(exc.reason)
        return RequestResult(
            ok=False,
            status_code=None,
            latency_ms=latency_ms,
            error=reason,
        )
    except Exception as exc:  # pragma: no cover - defensive catch for CLI use
        latency_ms = (time.perf_counter() - started_at) * 1000
        return RequestResult(
            ok=False,
            status_code=None,
            latency_ms=latency_ms,
            error=str(exc),
        )


def run_load_test(
    url: str,
    total_requests: int,
    concurrency: int,
    timeout: float,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> list[RequestResult]:
    merged_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        merged_headers.update(headers)

    request_context = ssl_context or build_ssl_context(insecure=False, ca_file=None)
    results: list[RequestResult] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(
                perform_request,
                url,
                timeout,
                method,
                merged_headers,
                body,
                request_context,
            )
            for _ in range(total_requests)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def run_load_test_for_duration(
    url: str,
    duration_seconds: float,
    concurrency: int,
    timeout: float,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> list[RequestResult]:
    merged_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        merged_headers.update(headers)

    request_context = ssl_context or build_ssl_context(insecure=False, ca_file=None)
    deadline = time.perf_counter() + duration_seconds
    results: list[RequestResult] = []
    results_lock = threading.Lock()

    def worker() -> None:
        local_results: list[RequestResult] = []
        while time.perf_counter() < deadline:
            local_results.append(
                perform_request(
                    url,
                    timeout,
                    method,
                    merged_headers,
                    body,
                    request_context,
                )
            )
        with results_lock:
            results.extend(local_results)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker) for _ in range(concurrency)]
        for future in as_completed(futures):
            future.result()

    return results


def summarize_results(results: list[RequestResult], elapsed_seconds: float) -> dict:
    latencies = sorted(result.latency_ms for result in results)
    status_codes = Counter(result.status_code for result in results if result.status_code is not None)
    errors = Counter(result.error for result in results if result.error)
    success_count = sum(1 for result in results if result.ok)
    failure_count = len(results) - success_count

    latency_summary = {
        "min": min(latencies) if latencies else 0.0,
        "avg": statistics.fmean(latencies) if latencies else 0.0,
        "p50": percentile(latencies, 50),
        "p95": percentile(latencies, 95),
        "max": max(latencies) if latencies else 0.0,
    }

    return {
        "total_requests": len(results),
        "success_count": success_count,
        "failure_count": failure_count,
        "elapsed_seconds": elapsed_seconds,
        "requests_per_second": (len(results) / elapsed_seconds) if elapsed_seconds > 0 else 0.0,
        "status_codes": dict(status_codes),
        "errors": dict(errors),
        "latency_ms": latency_summary,
    }


def format_summary(summary: dict) -> str:
    lines = [
        f"Total requests : {summary['total_requests']}",
        f"Successful     : {summary['success_count']}",
        f"Failed         : {summary['failure_count']}",
        f"Elapsed time   : {summary['elapsed_seconds']:.2f}s",
        f"Requests/sec   : {summary['requests_per_second']:.2f}",
        "Latency (ms)   : "
        f"min={summary['latency_ms']['min']:.2f} "
        f"avg={summary['latency_ms']['avg']:.2f} "
        f"p50={summary['latency_ms']['p50']:.2f} "
        f"p95={summary['latency_ms']['p95']:.2f} "
        f"max={summary['latency_ms']['max']:.2f}",
    ]

    if summary["status_codes"]:
        lines.append("Status codes   : " + ", ".join(
            f"{status}={count}" for status, count in sorted(summary["status_codes"].items())
        ))

    if summary["errors"]:
        lines.append("Errors         : " + ", ".join(
            f"{error}={count}" for error, count in sorted(summary["errors"].items())
        ))

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simple HTTP load test script.")
    parser.add_argument("url", nargs="?", default="https://nhu-quynh-di-mat-roi.pages.dev/")
    parser.add_argument("-n", "--requests", type=int, default=100, help="Total number of requests.")
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=10,
        help="Number of concurrent workers.",
    )
    parser.add_argument(
        "--duration",
        default=None,
        help="Run continuously for a duration like 600, 90s, 10m, or 1.5h.",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds.")
    parser.add_argument("--method", default="GET", help="HTTP method to use.")
    parser.add_argument(
        "-H",
        "--header",
        action="append",
        default=[],
        help="Repeatable HTTP header, example: -H 'Authorization: Bearer token'",
    )
    parser.add_argument("--body", default=None, help="Optional request body string.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for trusted targets only.",
    )
    parser.add_argument(
        "--ca-file",
        default=None,
        help="Path to a custom CA bundle PEM file for TLS verification.",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.requests <= 0:
        raise ValueError("--requests must be greater than 0")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be greater than 0")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than 0")
    if args.duration is not None and parse_duration_value(args.duration) <= 0:
        raise ValueError("--duration must be greater than 0")
    if args.insecure and args.ca_file:
        raise ValueError("--insecure and --ca-file cannot be used together")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        validate_args(args)
        headers = parse_header_values(args.header)
        ssl_context = build_ssl_context(insecure=args.insecure, ca_file=args.ca_file)
    except ValueError as exc:
        parser.error(str(exc))

    body = args.body.encode("utf-8") if args.body is not None else None
    method = args.method.upper()
    duration_seconds = parse_duration_value(args.duration) if args.duration is not None else None

    if duration_seconds is not None:
        print(
            f"Running load test against {args.url} "
            f"for {duration_seconds:.2f}s at concurrency {args.concurrency}..."
        )
    else:
        print(
            f"Running load test against {args.url} "
            f"with {args.requests} requests at concurrency {args.concurrency}..."
        )

    started_at = time.perf_counter()
    if duration_seconds is not None:
        results = run_load_test_for_duration(
            args.url,
            duration_seconds=duration_seconds,
            concurrency=args.concurrency,
            timeout=args.timeout,
            method=method,
            headers=headers,
            body=body,
            ssl_context=ssl_context,
        )
    else:
        results = run_load_test(
            args.url,
            total_requests=args.requests,
            concurrency=args.concurrency,
            timeout=args.timeout,
            method=method,
            headers=headers,
            body=body,
            ssl_context=ssl_context,
        )
    elapsed_seconds = time.perf_counter() - started_at
    summary = summarize_results(results, elapsed_seconds=elapsed_seconds)
    print(format_summary(summary))

    return 0 if summary["failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
