#!/usr/bin/env python3
"""Probe the closed HTTPS preflight, without credentials or provider requests."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit


CANARY = "synthetic_hormuz_preflight_log_canary"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="The exact HTTPS service origin")
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument(
        "--trace-response", choices=("application", "edge-405"), default="application",
        help="Require the application response, or separately verify an HTTPS edge's TRACE denial",
    )
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = parsed.hostname == "localhost"
    try:
        port = parsed.port
    except ValueError:
        parser.error("invalid origin port")
    if (
        not parsed.hostname or parsed.username is not None or parsed.password is not None
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
        or (port is not None and port < 1)
        or not (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and loopback and args.allow_loopback_http)
        )
    ):
        parser.error("use an HTTPS origin, or explicitly permit loopback HTTP")
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_source_commit):
        parser.error("expected source commit must be a complete lowercase Git SHA-1")
    if args.trace_response == "edge-405" and (parsed.scheme != "https" or loopback):
        parser.error("edge TRACE verification requires a non-loopback HTTPS origin")
    origin = args.url.rstrip("/")
    opener = urllib.request.build_opener(NoRedirect)
    cases = [
        ("health", "GET", "/health", 200),
        ("head_health", "HEAD", "/health", 200),
        ("gateway_not_ready", "GET", "/ready", 503),
        ("root_is_preflight", "GET", "/", 503),
        ("console_disabled", "GET", "/console", 503),
        ("native_login_disabled", "GET", "/v1/auth/login?enrollment=" + CANARY, 503),
        ("native_callback_disabled", "POST", "/v1/auth/callback", 503),
        ("console_callback_disabled", "POST", "/v1/admin/auth/callback", 503),
        ("enrollment_disabled", "POST", "/v1/auth/enrollments", 503),
        ("model_requests_disabled", "POST", "/v1/chat/completions", 503),
        ("messages_disabled", "POST", "/v1/messages", 503),
        ("health_is_read_only", "POST", "/health", 503),
        ("no_environment_file", "GET", "/.env", 503),
        ("no_secret_file", "GET", "/etc/secrets/hormuz.json", 503),
        ("no_cors_preflight", "OPTIONS", "/v1/auth/enrollments", 503),
        ("no_trace_echo", "TRACE", "/", 405 if args.trace_response == "edge-405" else 503),
    ]
    results = []
    failed = False
    for name, method, path, expected_status in cases:
        edge_trace = method == "TRACE" and args.trace_response == "edge-405"
        scope = "edge_method_rejection" if edge_trace else "preflight_application"
        status = None
        headers = {
            "Authorization": "Bearer " + CANARY,
            "Cookie": "preflight_canary=" + CANARY,
            "Origin": "https://untrusted.example",
            "X-Hormuz-Ingress-Credential": CANARY,
        }
        body = None
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = ("code=" + CANARY + "&state=" + CANARY).encode("ascii")
        request = urllib.request.Request(origin + path, data=body, headers=headers, method=method)
        try:
            try:
                response = opener.open(request, timeout=30)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.status
                payload = response.read(8193)
                response_headers = response.headers
            if status != expected_status:
                raise ValueError("unexpected_status")
            if len(payload) > 8192 or CANARY.encode() in payload:
                raise ValueError("unsafe_response_body")
            if response_headers.get("Set-Cookie") or response_headers.get("Access-Control-Allow-Origin"):
                raise ValueError("unexpected_cookie_or_cors")
            if response_headers.get("Location"):
                raise ValueError("unexpected_redirect")
            if edge_trace:
                # A fixed 405 without reflection is a method-denial check only.
                # It cannot prove any application response headers or revision.
                # Every other request still requires the full application contract.
                results.append({
                    "check": name, "passed": True, "status": status,
                    "validation_scope": scope, "application_response_verified": False,
                })
                continue
            if response_headers.get("Cache-Control") != "no-store":
                raise ValueError("cache_control_missing")
            if response_headers.get("Referrer-Policy") != "no-referrer":
                raise ValueError("referrer_policy_missing")
            if response_headers.get("X-Content-Type-Options") != "nosniff":
                raise ValueError("content_type_protection_missing")
            if "default-src 'none'" not in response_headers.get("Content-Security-Policy", ""):
                raise ValueError("content_security_policy_missing")
            if "noindex" not in response_headers.get("X-Robots-Tag", ""):
                raise ValueError("noindex_missing")
            if response_headers.get("X-Hormuz-Preflight-Revision") != args.expected_source_commit:
                raise ValueError("revision_mismatch")
            if method != "HEAD":
                result = json.loads(payload)
                if (
                    not isinstance(result, dict)
                    or result.get("schema_id") != "hormuz.https-preflight"
                    or result.get("schema_version") != 1
                    or result.get("stage") != "https_preflight_only"
                    or result.get("source_commit") != args.expected_source_commit
                    or result.get("gateway_ready") is not False
                    or result.get("sign_in_enabled") is not False
                ):
                    raise ValueError("preflight_contract_mismatch")
            elif payload:
                raise ValueError("head_has_body")
        except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError) as error:
            # Never print the supplied URL, a request, response body or exception
            # containing caller-controlled data into qualification evidence.
            print(json.dumps({"check": name, "passed": False}), file=sys.stderr)
            results.append({
                "check": name, "passed": False, "status": status,
                "expected_status": expected_status, "validation_scope": scope,
                "error_class": type(error).__name__,
            })
            failed = True
            break
        results.append({"check": name, "passed": True, "status": status, "validation_scope": scope})
    print(json.dumps({
        "schema_id": "hormuz.https-preflight-verification",
        "schema_version": 1,
        "source_commit": args.expected_source_commit,
        "passed": not failed,
        "trace_response": args.trace_response,
        "checks": results,
        "gateway_qualified": False,
        "real_oidc_qualified": False,
    }, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
