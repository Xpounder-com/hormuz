"""Bounded stdin bridge to the existing Python optimizer, invoked only when On.

The Rust relay owns authentication and forwarding. This module neither sees a
gateway credential nor opens a provider request. Stdin is a two-byte gateway
origin length, the origin, then the request body. It writes one status byte and
optional transformed request bytes to stdout; diagnostics contain no content.
"""
from __future__ import annotations

import argparse
import sys

from .client_relay import RelayOptimizer, probe_gateway_capability
from .compaction_formats import MAX_REQUEST_BYTES


class _EnabledPreference:
    enabled = True


class _PreferenceStore:
    def load(self) -> _EnabledPreference:
        return _EnabledPreference()


def transform(body: bytes, path: str, client: str, gateway: str) -> tuple[bytes, bool]:
    routes = {
        "codex": {"/v1/responses", "/v1/responses/compact"},
        "claude-code": {"/v1/messages", "/v1/messages/count_tokens"},
    }
    if path not in routes.get(client, ()) or len(body) > MAX_REQUEST_BYTES:
        return body, False
    optimizer = RelayOptimizer(
        preference_store=_PreferenceStore(),  # type: ignore[arg-type]
        client=client,
        gateway_compatible=probe_gateway_capability(gateway),
    )
    changed, _headers = optimizer.prepare(body, path)
    return changed, changed != body and len(changed) <= MAX_REQUEST_BYTES


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--client", choices=("codex", "claude-code"), required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args()
    length = sys.stdin.buffer.read(2)
    if len(length) != 2:
        return 2
    gateway_length = int.from_bytes(length, "big")
    if not 1 <= gateway_length <= 2048:
        return 2
    gateway_bytes = sys.stdin.buffer.read(gateway_length)
    if len(gateway_bytes) != gateway_length:
        return 2
    try:
        gateway = gateway_bytes.decode("ascii")
    except UnicodeDecodeError:
        return 2
    body = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(body) > MAX_REQUEST_BYTES:
        return 2
    try:
        changed, modified = transform(body, args.path, args.client, gateway)
    except Exception:
        return 1
    if modified:
        sys.stdout.buffer.write(b"\x01" + changed)
    else:
        sys.stdout.buffer.write(b"\x00")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
