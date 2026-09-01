"""Disposable browser fixture: local simulated IdP, seeded team, no external calls.

Run as a module, open the printed console URL, and use organization customer-a.
After removing Mina Member in the UI, enter verify on stdin for content-free
server-side revocation evidence. Ctrl-C or EOF closes both servers and deletes DBs.
"""

from __future__ import annotations

import html
import json
import secrets
import sys
from urllib.parse import parse_qs, urlsplit
from unittest import mock

from hormuz.server import GatewayRequestHandler
from tests._console_fixtures import ConsoleHTTPTestCase
from tests._session_fixtures import LocalIdentityProvider


class ConsoleIdentitySimulator(LocalIdentityProvider):
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path != "/authorize":
            return super().do_GET()
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if (params.get("redirect_uri") != self.server.gateway_url + "/v1/admin/auth/callback"
                or params.get("response_mode") != "form_post" or params.get("code_challenge_method") != "S256"):
            return self.reply(400, {"error": "fixture_callback_mismatch"})
        code = "console-local-fixture-" + secrets.token_urlsafe(24)
        self.server.codes[code] = params
        fields = {"code": code, "state": params["state"], "iss": self.server.origin}
        body = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                '<title>Local console identity simulator</title><h1>Local identity simulator</h1>'
                '<p>This is a disposable fixture, not Okta. No account or real credentials are used.</p>'
                '<form method="post" action="' + html.escape(params["redirect_uri"], quote=True) + '">'
                + "".join('<input type="hidden" name="' + key + '" value="' + html.escape(value, quote=True) + '">' for key, value in fields.items())
                + '<button type="submit">Confirm simulated administrator identity</button></form></html>').encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)


class ConsoleBoundaryObserver(GatewayRequestHandler):
    # Bound idle/preconnected browser sockets in this disposable fixture only.
    timeout = 5

    def do_POST(self):
        if self.path == "/v1/admin/auth/start":
            origins = self.headers.get_all("Origin", [])
            print(json.dumps({"fixture_form_boundary": True, "origin_present": bool(origins),
                              "origin_is_null": origins == ["null"],
                              "origin_matches_gateway": origins == [self.server.config.session_broker.public_base_url]}), flush=True)
        super().do_POST()


def main():
    case = ConsoleHTTPTestCase()
    try:
        with mock.patch("tests._session_fixtures.LocalIdentityProvider", ConsoleIdentitySimulator):
            case.setUp()
        case.idp.gateway_url = case.gateway_url
        case.gateway.RequestHandlerClass = ConsoleBoundaryObserver
        case.request("POST", "/v1/responses", {"model": "safe-openai", "input": "Synthetic console fixture", "max_output_tokens": 16},
                     {"Authorization": "Bearer " + case.native.access_token})
        print(json.dumps({"console_url": case.gateway_url + "/console", "organization": "customer-a", "real_provider_calls": 0}), flush=True)
        for line in sys.stdin:
            if line.strip() == "exit":
                break
            if line.strip() != "verify":
                continue
            status = case.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + case.member_native.access_token})[0]
            with case.store._connection() as connection:
                removed = connection.execute("SELECT status FROM onboarding_memberships WHERE id = ?", (case.member.membership_id,)).fetchone()[0] == "disabled"
                attributed = connection.execute("SELECT COUNT(*) FROM onboarding_events WHERE event_type = 'member_disabled' AND decision_actor = ?",
                                                (case.admin.membership_id,)).fetchone()[0] == 1
            print(json.dumps({"member_removed": removed, "removed_native_access_status": status,
                              "verified_admin_actor_recorded": attributed, "simulated_model_calls": case.idp.model_requests,
                              "real_provider_calls": 0}), flush=True)
    finally:
        case.doCleanups()


if __name__ == "__main__":
    main()
