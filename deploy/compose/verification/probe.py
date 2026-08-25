#!/usr/bin/env python3
"""Exercise the host-restricted Hormuz proof endpoint without exposing secrets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SYNTHETIC_SECRET = "sk-proj-CCCCCCCCCCCCCCCCCCCCCCCC"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("health", "request"))
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--expected-status", required=True, type=int)
    parser.add_argument("--expected-policy")
    parser.add_argument("--without-ingress", action="store_true")
    args = parser.parse_args()

    secrets = args.runtime_root / "secrets"
    headers: dict[str, str] = {}
    if not args.without_ingress:
        headers["X-Hormuz-Ingress-Credential"] = _read(secrets / "hormuz-ingress-credential")
    if args.command == "health":
        status, response_headers, body = _request("GET", "/health", headers=headers)
        if status == 200:
            value = json.loads(body)
            if value.get("schema_id") != "hormuz.gateway-health" or value.get("status") != "ok":
                raise SystemExit("health_contract_invalid")
    else:
        headers.update(
            {
                "Authorization": f"Bearer {_read(secrets / 'hormuz-identity-token')}",
                "Content-Type": "application/json",
            }
        )
        payload = {
            "model": "unapproved-compose-proof-model",
            "input": f"Synthetic credential for redaction proof: {SYNTHETIC_SECRET}",
            "max_output_tokens": 900,
            "stream": False,
        }
        status, response_headers, body = _request(
            "POST",
            "/v1/responses",
            headers=headers,
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )
        policy = response_headers.get("x-hormuz-policy-decision")
        if args.expected_policy is not None and policy != args.expected_policy:
            raise SystemExit("policy_decision_invalid")
        if status == 200:
            value = json.loads(body)
            if value.get("model") != "gpt-compose-proof":
                raise SystemExit("routed_model_invalid")
        elif status == 403:
            value = json.loads(body)
            if value.get("error", {}).get("code") != "hormuz_secret_detected":
                raise SystemExit("deny_contract_invalid")
    if status != args.expected_status:
        raise SystemExit(f"unexpected_status:{status}")
    print(
        json.dumps(
            {
                "command": args.command,
                "status": status,
                "policy": response_headers.get("x-hormuz-policy-decision"),
                "redactions": int(response_headers.get("x-hormuz-redactions", "0")),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _read(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SystemExit("proof_secret_unavailable")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit("proof_secret_empty")
    return value


def _request(
    method: str,
    path: str,
    *,
    headers: dict[str, str],
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request = Request(f"http://127.0.0.1:8787{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, {name.lower(): value for name, value in response.headers.items()}, response.read()
    except HTTPError as error:
        return error.code, {name.lower(): value for name, value in error.headers.items()}, error.read()


if __name__ == "__main__":
    raise SystemExit(main())
