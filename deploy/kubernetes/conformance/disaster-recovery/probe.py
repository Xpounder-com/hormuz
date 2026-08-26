#!/usr/bin/env python3
"""Probe the isolated recovered gateway without printing protected inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SYNTHETIC_SECRET = "sk-proj-KKKKKKKKKKKKKKKKKKKKKKKK"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ready", "request"))
    parser.add_argument("--target", required=True)
    parser.add_argument("--expected-status", required=True, type=int)
    parser.add_argument("--expected-policy")
    args = parser.parse_args()
    headers = {"X-Hormuz-Ingress-Credential": _read("ingress-credential")}
    if args.command == "ready":
        status, response_headers, body = _request(
            "GET", f"{args.target}/ready", headers=headers
        )
        value = json.loads(body)
        if (
            status != args.expected_status
            or value.get("schema_id") != "hormuz.gateway-readiness"
            or value.get("status") != "ready"
        ):
            raise SystemExit("recovery_readiness_invalid")
        result = {"command": "ready", "status": status, "readiness": "ready"}
    else:
        headers.update(
            {
                "Authorization": f"Bearer {_read('identity-token')}",
                "Content-Type": "application/json",
            }
        )
        payload = {
            "model": "gpt-ha-proof",
            "input": f"Synthetic credential for redaction proof: {SYNTHETIC_SECRET}",
            "max_output_tokens": 900,
            "stream": False,
        }
        status, response_headers, body = _request(
            "POST",
            f"{args.target}/v1/responses",
            headers=headers,
            body=json.dumps(payload, separators=(",", ":")).encode(),
        )
        if status != args.expected_status:
            raise SystemExit(f"unexpected_status:{status}")
        policy = response_headers.get("x-hormuz-policy-decision")
        if args.expected_policy is not None and policy != args.expected_policy:
            raise SystemExit("recovery_policy_decision_invalid")
        value = json.loads(body)
        if status == 200 and value.get("model") != "gpt-ha-proof":
            raise SystemExit("recovery_routed_model_invalid")
        result = {
            "command": "request",
            "status": status,
            "policy": policy,
            "redactions": int(response_headers.get("x-hormuz-redactions", "0")),
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def _read(name: str) -> str:
    value = (Path("/run/hormuz-proof") / name).read_text(encoding="utf-8").strip()
    if not value or len(value) > 4096:
        raise SystemExit("recovery_probe_secret_invalid")
    return value


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request = Request(url, method=method, headers=headers, data=body)
    try:
        with urlopen(request, timeout=15) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()


if __name__ == "__main__":
    raise SystemExit(main())
