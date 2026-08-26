#!/usr/bin/env python3
"""Exercise the private Hormuz Service without exposing proof credentials."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SYNTHETIC_SECRET = "sk-proj-KKKKKKKKKKKKKKKKKKKKKKKK"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "ambiguous-request",
            "blocking-request",
            "health",
            "ready",
            "request",
            "replacement-traffic",
            "storage-backpressure",
            "network-denied",
        ),
    )
    parser.add_argument("--target", required=True)
    parser.add_argument("--expected-status", type=int)
    parser.add_argument("--expected-policy")
    parser.add_argument("--duration-seconds", type=int)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--without-ingress", action="store_true")
    args = parser.parse_args()

    if args.command == "network-denied":
        try:
            urlopen(f"{args.target}/health", timeout=3).read()
        except HTTPError:
            raise SystemExit("network_was_not_denied") from None
        except (URLError, TimeoutError, OSError):
            print(json.dumps({"command": "network-denied", "network_denied": True}))
            return 0
        raise SystemExit("network_was_not_denied")

    headers: dict[str, str] = {}
    if not args.without_ingress:
        headers["X-Hormuz-Ingress-Credential"] = _read("ingress-credential")
    if args.command in {"health", "ready"}:
        path = "/health" if args.command == "health" else "/ready"
        status, response_headers, body = _request("GET", f"{args.target}{path}", headers=headers)
        if status == 200:
            value = json.loads(body)
            expected_schema = "hormuz.gateway-health" if args.command == "health" else "hormuz.gateway-readiness"
            expected_value = "ok" if args.command == "health" else "ready"
            if value.get("schema_id") != expected_schema or value.get("status") != expected_value:
                raise SystemExit(f"{args.command}_contract_invalid")
        elif status == 503 and args.command == "ready":
            value = json.loads(body)
            if (
                value.get("schema_id") != "hormuz.gateway-readiness"
                or value.get("status") != "not_ready"
                or value.get("reason") != "dependency_unavailable"
            ):
                raise SystemExit("ready_contract_invalid")
        elif status == 401:
            value = json.loads(body)
            if value.get("error", {}).get("code") != "unauthorized":
                raise SystemExit("ingress_denial_contract_invalid")
    else:
        headers.update(
            {
                "Authorization": f"Bearer {_read('identity-token')}",
                "Content-Type": "application/json",
            }
        )
        if args.command == "replacement-traffic":
            if args.duration_seconds is None or not 15 <= args.duration_seconds <= 120:
                raise SystemExit("replacement_duration_invalid")
            return _run_replacement_traffic(
                target=args.target,
                headers=headers,
                expected_policy=args.expected_policy,
                duration_seconds=args.duration_seconds,
            )
        if args.command in {"blocking-request", "ambiguous-request"}:
            return _run_blocking_request(
                command=args.command,
                target=args.target,
                headers=headers,
                expected_policy=args.expected_policy,
            )
        if args.command == "storage-backpressure":
            if args.concurrency is None or not 8 <= args.concurrency <= 64:
                raise SystemExit("storage_backpressure_concurrency_invalid")
            return _run_storage_backpressure(
                target=args.target,
                headers=headers,
                concurrency=args.concurrency,
                expected_status=args.expected_status,
            )
        result = _governed_request(
            target=args.target,
            headers=headers,
            expected_status=args.expected_status,
            expected_policy=args.expected_policy,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0

    if status != args.expected_status:
        raise SystemExit(f"unexpected_status:{status}")
    result: dict[str, object] = {
        "command": args.command,
        "status": status,
        "policy": response_headers.get("x-hormuz-policy-decision"),
        "redactions": int(response_headers.get("x-hormuz-redactions", "0")),
    }
    if args.command == "ready":
        result.update(
            {
                "readiness": value.get("status"),
                "reason": value.get("reason"),
            }
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def _governed_request(
    *,
    target: str,
    headers: dict[str, str],
    expected_status: int | None,
    expected_policy: str | None,
    blocking: bool = False,
    timeout: int = 10,
) -> dict[str, object]:
    payload = {
        "model": "unapproved-kubernetes-proof-model",
        "input": (
            "HORMUZ_BLOCKING_OPERATION_PROBE " if blocking else ""
        ) + f"Synthetic credential for redaction proof: {SYNTHETIC_SECRET}",
        "max_output_tokens": 900,
        "stream": False,
    }
    status, response_headers, body = _request(
        "POST",
        f"{target}/v1/responses",
        headers=headers,
        body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        timeout=timeout,
    )
    if status != expected_status:
        raise SystemExit(f"unexpected_status:{status}")
    policy = response_headers.get("x-hormuz-policy-decision")
    if expected_policy is not None and policy != expected_policy:
        raise SystemExit("policy_decision_invalid")
    if status == 200:
        value = json.loads(body)
        if value.get("model") != "gpt-kubernetes-proof":
            raise SystemExit("routed_model_invalid")
    elif status == 403:
        value = json.loads(body)
        if value.get("error", {}).get("code") != "hormuz_secret_detected":
            raise SystemExit("deny_contract_invalid")
    elif status == 503:
        value = json.loads(body)
        if value.get("error", {}).get("code") != "hormuz_storage_unavailable":
            raise SystemExit("storage_denial_contract_invalid")
    return {
        "command": "request",
        "status": status,
        "policy": policy,
        "redactions": int(response_headers.get("x-hormuz-redactions", "0")),
    }


def _run_blocking_request(
    *,
    command: str,
    target: str,
    headers: dict[str, str],
    expected_policy: str | None,
) -> int:
    print(json.dumps({"event": "blocking_request_started"}, separators=(",", ":")), flush=True)
    try:
        result = _governed_request(
            target=target,
            headers=headers,
            expected_status=200,
            expected_policy=expected_policy,
            blocking=True,
            timeout=90,
        )
    except (URLError, TimeoutError, OSError):
        if command != "ambiguous-request":
            raise
        print(
            json.dumps(
                {"command": command, "transport_outcome": "ambiguous"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except SystemExit as error:
        if command != "ambiguous-request" or str(error) not in {
            "unexpected_status:502",
            "unexpected_status:503",
        }:
            raise
        print(
            json.dumps(
                {"command": command, "transport_outcome": "ambiguous"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    if command == "ambiguous-request":
        raise SystemExit("ambiguous_request_unexpectedly_completed")
    print(json.dumps({**result, "command": command}, sort_keys=True, separators=(",", ":")))
    return 0


def _run_replacement_traffic(
    *,
    target: str,
    headers: dict[str, str],
    expected_policy: str | None,
    duration_seconds: int,
) -> int:
    deadline = time.monotonic() + duration_seconds
    successful_requests = 0
    failed_requests = 0
    started = False
    while time.monotonic() < deadline:
        try:
            _governed_request(
                target=target,
                headers=headers,
                expected_status=200,
                expected_policy=expected_policy,
            )
        except (URLError, TimeoutError, OSError):
            failed_requests += 1
        else:
            successful_requests += 1
            if not started:
                print(
                    json.dumps({"event": "traffic_started"}, separators=(",", ":")),
                    flush=True,
                )
                started = True
        time.sleep(0.5)
    if successful_requests < 2 or failed_requests:
        raise SystemExit("replacement_traffic_failed")
    print(
        json.dumps(
            {
                "command": "replacement-traffic",
                "failed_requests": failed_requests,
                "successful_requests": successful_requests,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _run_storage_backpressure(
    *,
    target: str,
    headers: dict[str, str],
    concurrency: int,
    expected_status: int | None,
) -> int:
    if expected_status != 503:
        raise SystemExit("storage_backpressure_status_invalid")
    started = time.monotonic_ns()

    def request(_: int) -> dict[str, object]:
        return _governed_request(
            target=target,
            headers=headers,
            expected_status=503,
            expected_policy=None,
            timeout=15,
        )

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(request, range(concurrency)))
    duration_ms = max(1, (time.monotonic_ns() - started + 999_999) // 1_000_000)
    if any(result.get("status") != 503 for result in results):
        raise SystemExit("storage_backpressure_result_invalid")
    print(
        json.dumps(
            {
                "command": "storage-backpressure",
                "duration_ms": duration_ms,
                "requests": concurrency,
                "status": 503,
                "storage_denials": len(results),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _read(name: str) -> str:
    return _read_projected_secret(Path("/run/hormuz-proof"), name)


def _read_projected_secret(root: Path, name: str) -> str:
    if Path(name).name != name:
        raise SystemExit("proof_secret_unavailable")
    try:
        mount_root = root.resolve(strict=True)
        resolved = (mount_root / name).resolve(strict=True)
        resolved.relative_to(mount_root)
    except (FileNotFoundError, OSError, ValueError):
        raise SystemExit("proof_secret_unavailable") from None
    if not resolved.is_file():
        raise SystemExit("proof_secret_unavailable")
    # Kubernetes projected Secret keys are symlinks through ..data. Resolve
    # them only when the final regular file stays inside the read-only mount.
    value = resolved.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit("proof_secret_empty")
    return value


def _request(
    method: str,
    target: str,
    *,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: int = 10,
) -> tuple[int, dict[str, str], bytes]:
    request = Request(target, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                {name.lower(): value for name, value in response.headers.items()},
                response.read(),
            )
    except HTTPError as error:
        return (
            error.code,
            {name.lower(): value for name, value in error.headers.items()},
            error.read(),
        )


if __name__ == "__main__":
    raise SystemExit(main())
