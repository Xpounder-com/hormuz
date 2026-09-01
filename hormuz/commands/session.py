"""Customer login commands that do not require server configuration or secrets."""

from __future__ import annotations

import argparse
import json
import shlex
import sys

from ..credential_store import CredentialStoreError, validate_profile
from ..session_client import SessionClientError, access_token, login, logout, validate_session_gateway


def add_session_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    for name, help_text in (("login", "Sign in through your browser"), ("logout", "Revoke your session and remove local credentials")):
        parser = subparsers.add_parser(name, help=help_text)
        _connection_arguments(parser)
        if name == "login":
            parser.add_argument("--client", choices=["codex", "claude-code"], required=True)
            parser.add_argument("--issuer", help="Configured OIDC issuer, required when the gateway has several")
            parser.add_argument("--organization", help="Configured organization, required for a shared issuer")
            parser.add_argument("--no-open", action="store_true", help="Print the login URL instead of opening a browser")
            parser.add_argument("--wait-seconds", type=int, default=300)


def add_session_token_arguments(parser: argparse.ArgumentParser) -> None:
    _connection_arguments(parser)
    parser.add_argument("--force-refresh", action="store_true", help="Rotate credentials even before their normal refresh time")


def _connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gateway", required=True, help="Your team's Hormuz gateway origin")
    parser.add_argument("--profile", default="default", help="Local OS secure-store profile")
    parser.add_argument("--allow-insecure-http", action="store_true", help="Allow loopback HTTP for local development only")


def run(args: argparse.Namespace) -> int:
    try:
        common = dict(gateway=args.gateway, profile=args.profile, allow_insecure_http=args.allow_insecure_http)
        if args.command == "login":
            if not 1 <= args.wait_seconds <= 600:
                raise SessionClientError("invalid_login_wait")
            login(**common, client=args.client, issuer=args.issuer, organization=args.organization, no_open=args.no_open, wait_seconds=args.wait_seconds)
            print("Signed in. Credentials are held in your operating system's secure store.")
        elif args.command == "logout":
            removed = logout(**common)
            print("Session revoked and local credentials removed." if removed else "No saved session for this profile.")
        else:
            # This is an intentional machine credential channel; callers must
            # capture stdout, never echo it into a transcript or log.
            print(access_token(**common, force_refresh=args.force_refresh))
        return 0
    except (CredentialStoreError, SessionClientError) as error:
        print(f"session error: {error.code}", file=sys.stderr)
        if error.code == "secure_store_dependency_missing":
            print('Install the client extra: python -m pip install "hormuz[client]"', file=sys.stderr)
        return 1


def client_config(args: argparse.Namespace) -> int:
    """Print helper-based setup without opening a server config or editing files."""
    try:
        validate_profile(args.profile)
        if not args.url:
            raise SessionClientError("gateway_url_required")
        gateway = validate_session_gateway(args.url, allow_insecure_http=args.allow_insecure_http)
        command = ["auth", "session", "--gateway", gateway, "--profile", args.profile]
        if args.allow_insecure_http:
            command.append("--allow-insecure-http")
        if args.client == "codex":
            if not args.model or len(args.model) > 256 or not args.model.isascii() or any(c.isspace() or ord(c) < 32 for c in args.model):
                raise SessionClientError("configured_model_alias_required")
            print("\n".join([
                "# Merge into ~/.codex/config.toml; no credential values belong here.",
                "model = " + json.dumps(args.model), 'model_provider = "hormuz"',
                "", "[model_providers.hormuz]", 'name = "Hormuz"',
                "base_url = " + json.dumps(gateway + "/v1"), 'wire_api = "responses"',
                "", "[model_providers.hormuz.auth]", 'command = "hormuz"',
                "args = " + json.dumps(command), "refresh_interval_ms = 300000",
            ]))
        else:
            print(json.dumps({
                "apiKeyHelper": shlex.join(["hormuz", *command]),
                "env": {"ANTHROPIC_BASE_URL": gateway, "CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "300000"},
            }, indent=2))
        return 0
    except (CredentialStoreError, SessionClientError) as error:
        print(f"session error: {error.code}", file=sys.stderr)
        return 1
