"""Synthetic state helpers, never a real IdP or a customer onboarding session."""

import base64
import json
import secrets
from pathlib import Path

from hormuz._hosted_config import load_profile
from hormuz.console_store import ConsoleStore


def profile(root: Path):
    settings = {
        "HORMUZ_INGRESS_CREDENTIAL": "synthetic_ingress_" + "i" * 43,
        "HORMUZ_SESSION_MASTER_KEY": base64.b64encode(b"m" * 32).decode(),
        "HORMUZ_OIDC_CLIENT_SECRET": "synthetic-oidc-client-secret",
        "HORMUZ_HOSTED_MODE": "active", "PORT": "10000",
    }
    document = {"schema": "hormuz.hosted-auth-staging/v1", "public_origin": "https://gateway.example.test",
                "oidc_issuer": "https://idp.example.test", "oidc_client_id": "fixture-login",
                "state_directory": str(root / "state")}
    path = root / "profile.json"
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    settings["HORMUZ_CONFIG"] = str(path)
    return load_profile(path, settings), settings, document


def directory_setup(directory, config):
    directory.create_organization(organization_id="customer-a", name="Synthetic A", issuer=next(iter(config.oidc_issuers)))
    directory.create_team(organization_id="customer-a", team_id="customer-a-eng", name="Engineering")


def console_credential(store, directory):
    console = ConsoleStore(store, directory)
    state, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    console.begin_login(organization_id="customer-a", state=state, browser_cookie=cookie,
                        nonce=secrets.token_urlsafe(32), pkce_verifier=secrets.token_urlsafe(64))
    flow = console.consume_callback(state=state, browser_cookie=cookie)
    return console, console.complete_login(flow, {"iss": next(iter(directory.config.oidc_issuers)), "sub": "admin-subject"})
