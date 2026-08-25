from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen


def _probe(path: str, expected_schema: str, expected_status: str) -> None:
    credential = Path("/run/secrets/hormuz_ingress_credential").read_text(encoding="utf-8").strip()
    request = Request(
        f"http://127.0.0.1:8787{path}",
        headers={"X-Hormuz-Ingress-Credential": credential},
    )
    with urlopen(request, timeout=3) as response:
        value = json.loads(response.read())
    if response.status != 200:
        raise SystemExit(1)
    if value.get("schema_id") != expected_schema or value.get("schema_version") != 1:
        raise SystemExit(1)
    if value.get("status") != expected_status:
        raise SystemExit(1)


_probe("/health", "hormuz.gateway-health", "ok")
_probe("/ready", "hormuz.gateway-readiness", "ready")
