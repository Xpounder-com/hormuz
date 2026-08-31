"""Manual local-browser proof; no real identity, model, cloud or customer data.

Run with --handoff-file pointing to a NEW private temporary JSON file. Open the
printed login URL and use the invitation code from that file. After the browser
says Connected, type verify on stdin. The fixture redeems the enrollment, checks
identity/usage and then removes the member and verifies 401 responses. Exit or
Ctrl-C removes its private state and stops both local servers.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest import mock

from tests._session_fixtures import LocalIdentityProvider
from tests.test_onboarding_http import OnboardingHTTPTests


class BrowserIdentitySimulator(LocalIdentityProvider):
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path != "/authorize":
            super().do_GET()
            return
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if params.get("redirect_uri") != self.server.gateway_url + "/v1/auth/callback":
            self.reply(400, {"error": "fixture_callback_mismatch"})
            return
        code = "local-fixture-code-" + secrets.token_urlsafe(24)
        self.server.codes[code] = params
        fields = {"code": code, "state": params["state"], "iss": self.server.origin}
        content = (
            '<!doctype html><html lang="en"><meta charset="utf-8"><title>Local identity simulator</title>'
            '<h1>Local identity simulator</h1><p>No real account or external service is involved.</p>'
            '<p>Invited member: new@example.test</p>'
            f'<form method="post" action="{html.escape(params["redirect_uri"], quote=True)}">'
            + "".join(f'<input type="hidden" name="{key}" value="{html.escape(value, quote=True)}">' for key, value in fields.items())
            + '<button type="submit">Sign in as invited member</button></form></html>'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-file", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    case = OnboardingHTTPTests()
    handoff_created = False
    try:
        with mock.patch("tests._session_fixtures.LocalIdentityProvider", BrowserIdentitySimulator):
            case.setUp()
        case.idp.gateway_url = case.gateway_url
        enrollment, secret = case.enroll(organization="customer-a")
        descriptor = os.open(args.handoff_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        handoff_created = True
        with os.fdopen(descriptor, "w") as output:
            json.dump({"invitation_code": case.invitation.code}, output)
        print(json.dumps({"login_url": enrollment["login_url"], "handoff_file": str(args.handoff_file), "local_simulator_only": True}), flush=True)
        for command in sys.stdin:
            if command.strip() == "exit":
                break
            if command.strip() != "verify":
                continue
            status, _, pair = case.request("POST", "/v1/auth/enrollments/" + enrollment["enrollment_id"] + "/redeem", {"enrollment_secret": secret})
            if status != 200:
                print(json.dumps({"ready": False, "redemption_status": status}), flush=True)
                continue
            headers = {"Authorization": "Bearer " + pair["access_token"]}
            identity_status, _, identity = case.request("GET", "/v1/gateway/whoami", headers=headers)
            usage_status, _, _ = case.request("GET", "/v1/gateway/usage", headers=headers)
            case.directory.disable_member(organization_id="customer-a", membership_id=case.invitation.membership_id)
            removed_status = case.request("GET", "/v1/gateway/whoami", headers=headers)[0]
            refresh_status = case.request("POST", "/v1/auth/refresh", {"refresh_token": pair["refresh_token"]})[0]
            result = {"redemption_status": status, "identity_status": identity_status, "organization_correct": identity.get("organization_id") == "customer-a",
                      "member_correct": identity.get("actor_id") == case.invitation.membership_id, "usage_status": usage_status,
                      "removed_access_status": removed_status, "removed_refresh_status": refresh_status, "model_requests": case.idp.model_requests}
            assert result == {"redemption_status": 200, "identity_status": 200, "organization_correct": True, "member_correct": True,
                              "usage_status": 200, "removed_access_status": 401, "removed_refresh_status": 401, "model_requests": 0}
            print(json.dumps(result), flush=True)
    finally:
        case.doCleanups()
        if handoff_created:
            args.handoff_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
