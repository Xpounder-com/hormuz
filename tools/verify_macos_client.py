#!/usr/bin/env python3
"""Provider-free Mac protocol proof, or a loopback fixture for manual UI checks.

This imports test-only IdP/model simulators. It must never be deployed as a
service. It supplies no real identities or provider credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests._session_fixtures import LocalIdentityProvider, SessionHTTPTestCase
from tests.test_gateway import FakeProviderHandler
from hormuz.server import GatewayRequestHandler


class MacFixtureGatewayHandler(GatewayRequestHandler):
    def do_GET(self):
        if self.path == "/__hormuz_macos_fixture":
            self.send_response(200)
            body = b'{"service":"hormuz-macos-test-fixture","external_providers":false}'
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()


class MacFixtureIdentityProvider(LocalIdentityProvider, FakeProviderHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/v1/fixture/redirect":
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/must-not-follow")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif parsed.path == "/v1/fixture/oversized":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(128 * 1024 + 1))
            self.end_headers()
            try:
                self.wfile.write(b" " * (128 * 1024 + 1))
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif parsed.path == "/authorize" and "text/html" in self.headers.get("Accept", ""):
            fields = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            code = "fixture-authorization-code-" + str(len(self.server.codes))
            self.server.codes[code] = fields
            hidden = {"code": code, "state": fields["state"], "iss": self.server.origin}
            controls = "".join(
                '<input type="hidden" name="' + html.escape(key, quote=True) + '" value="' + html.escape(value, quote=True) + '">'
                for key, value in hidden.items()
            )
            page = (
                '<!doctype html><html><meta charset="utf-8"><title>Hormuz local identity fixture</title>'
                '<body style="font:18px system-ui;max-width:650px;margin:80px auto">'
                '<h1>Local identity fixture</h1><p>No real account or password is used.</p>'
                '<form method="post" action="' + html.escape(fields["redirect_uri"], quote=True) + '">'
                + controls + '<button type="submit">Sign in as fixture Alice</button></form></body></html>'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)
        else:
            super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path in {"/v1/responses", "/v1/messages", "/v1/messages/count_tokens"}:
            if not self.path.endswith("/count_tokens"):
                self.server.model_requests += 1
            FakeProviderHandler.do_POST(self)
        elif self.path == "/fixture/subject/bob":
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.subject = "bob-subject"
            self.reply(200, {"selected": True})
        else:
            super().do_POST()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve", action="store_true", help="Keep the local fake gateway running for GUI verification")
    args = parser.parse_args()
    if not args.serve and (args.probe is None or args.output is None):
        parser.error("--probe and --output are required unless --serve is set")
    fixture = SessionHTTPTestCase()
    fixture.setUp()
    fixture.idp.RequestHandlerClass = MacFixtureIdentityProvider
    fixture.gateway.RequestHandlerClass = MacFixtureGatewayHandler
    try:
        if args.serve:
            print(json.dumps({"gateway": fixture.gateway_url, "identity_provider": fixture.idp.origin,
                "organization": "org-a", "codex_model": "safe-openai", "claude_model": "safe-claude",
                "provider_calls": "local simulator only", "pid": os.getpid()}), flush=True)
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
            while True:
                signal.pause()
        root = fixture.root / "native-client"
        result = subprocess.run([str(args.probe.resolve()), fixture.gateway_url, fixture.idp.origin, str(root)],
            text=True, capture_output=True, timeout=90, check=False,
            env={"PATH": "/usr/bin:/bin", "TMPDIR": os.environ.get("TMPDIR", "/tmp")})
        if result.returncode != 0:
            # Never dump native stdout/stderr: a broken helper might emit a token.
            print("Mac local protocol proof failed. No credential-bearing output was retained.", file=sys.stderr)
            return 1
        summary = json.loads(result.stdout)
        checks = {"browser_login", "identity", "personal_usage", "client_isolation", "tenant_isolation",
            "refresh_rotation", "old_access_rejected", "logout_revocation", "connector_written",
            "redirect_rejected", "response_limit", "no_credentials_in_files"}
        if set(summary) != checks | {"schema_id", "schema_version", "credential_store"} or any(summary[key] is not True for key in checks):
            raise ValueError("native proof did not pass its exact check set")
        if summary["schema_id"] != "hormuz.macos-local-proof" or summary["schema_version"] != 1 or summary["credential_store"] != "in_memory_fixture":
            raise ValueError("unsupported native proof contract")
        if fixture.idp.model_requests != 1:
            raise ValueError("unexpected simulator request count")
        summary.update({"live_provider_calls": 0, "model_simulator_requests": fixture.idp.model_requests,
            "probe_sha256": hashlib.sha256(args.probe.read_bytes()).hexdigest()})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        print("Mac local protocol proof passed: 12 checks; 1 simulated model request; 0 live provider calls.")
        return 0
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    raise SystemExit(main())
