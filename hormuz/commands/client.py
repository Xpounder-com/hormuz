"""Supported-client configuration and credential-helper commands."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import subprocess
from urllib.parse import urlparse

from ..config import ConfigError, GatewayConfig
from ..policy_runtime import PolicyRuntime


def add_client_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    client = subparsers.add_parser("client", help="Configure supported AI clients")
    client_subparsers = client.add_subparsers(dest="client_command", required=True)
    connect = client_subparsers.add_parser("config", help="Print client configuration for this gateway")
    _client_config_arguments(connect)

    for command in ("setup", "connect", "status", "diagnose", "disconnect"):
        lifecycle = client_subparsers.add_parser(command, help="Manage persistent Codex CLI connection")
        lifecycle.add_argument("client", choices=["codex"])
        lifecycle.add_argument("--client-home", help="Isolated Codex home (default CODEX_HOME or ~/.codex)")
        if command == "diagnose":
            lifecycle.add_argument("--executable", help="Optional target executable for a bounded version check")
            lifecycle.add_argument("--project", help="Project working directory to inspect (default current directory)")
            lifecycle.add_argument("--launch-arg", action="append", help="One supplied Codex launch argument; repeat with =--flag syntax; never executed")
        if command == "disconnect":
            lifecycle.add_argument("--generation", help="Disconnect only the receipt generation that you own")
        if command in {"connect", "setup"}:
            lifecycle.add_argument("--executable", default="codex", help="Codex CLI executable to qualify")
            lifecycle.add_argument("--actor", help="Configured actor ID")
            lifecycle.add_argument("--dry-run", action="store_true", help="Preview precise changes without activating routing")
        if command == "setup":
            lifecycle.add_argument("--runtime-archive", required=True, help="Existing verified llama.cpp archive")
            lifecycle.add_argument("--llama-executable", required=True, help="Existing extracted llama-server executable")
            lifecycle.add_argument("--model-file", required=True, help="Existing verified GGUF model")
            lifecycle.add_argument("--qualification", help="Reviewed local setup profile (default bundled pinned experiment)")
            lifecycle.add_argument("--credential-env", help="Use an existing token environment variable instead of managed local authentication")
            lifecycle.add_argument("--shadow-router", action="store_true", help="Record bounded local model routing advice without changing routes")

    auth = subparsers.add_parser("auth", help="Credential helpers for AI clients")
    auth_subparsers = auth.add_subparsers(dest="auth_command", required=True)
    auth_token = auth_subparsers.add_parser("token", help="Print a credential from an environment variable")
    auth_token.add_argument("--env", default="HORMUZ_GATEWAY_TOKEN", help="Credential environment variable")
    local_token = auth_subparsers.add_parser("local-token", help="Generation-bound credential helper for Codex")
    local_token.add_argument("--client-home", required=True)
    local_token.add_argument("--generation", required=True)


def local_token(args):
    from pathlib import Path
    from ..connected_client import ConnectedClientLifetime, ConnectedClientError, lifetime_error
    try:
        print(ConnectedClientLifetime(Path(args.client_home)).local_token(args.generation))
        return 0
    except (ConnectedClientError, ConfigError, OSError) as error:
        print(json.dumps(lifetime_error(error)), file=sys.stderr)
        return 2


def _client_config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("client", choices=["codex", "claude"])
    parser.add_argument("--url", help="Externally reachable gateway URL; defaults to configured listener")
    parser.add_argument("--actor", help="Configured actor ID; defaults to the first configured actor")
    parser.add_argument(
        "--auth-mode",
        choices=["auto", "static"],
        default="auto",
        help="Local router credential source",
    )
    parser.add_argument(
        "--credential-env",
        help="Environment variable containing the local router credential",
    )
    parser.add_argument("--work-id", help="Attach an existing owned AI Work job to every request in this configuration")


def _client(config: GatewayConfig, args: argparse.Namespace) -> int:
    if args.client_command != "config":
        return 2
    return _client_config(
        config,
        args.client,
        args.url,
        actor_id=args.actor,
        auth_mode=args.auth_mode,
        credential_env=args.credential_env,
        work_id=getattr(args, "work_id", None),
    )


def _client_config(
    config: GatewayConfig,
    client: str,
    url: str | None,
    *,
    actor_id: str | None = None,
    auth_mode: str = "auto",
    credential_env: str | None = None,
    work_id: str | None = None,
) -> int:
    work_headers = _work_headers(work_id)
    base_url = _client_base_url(url or f"http://{config.listen.host}:{config.listen.port}")
    if actor_id is None:
        identity = next(iter(config.identities_by_actor.values()))
    else:
        identity = config.identities_by_actor.get(actor_id)
        if identity is None:
            raise ConfigError(f"Unknown actor: {actor_id}")
    static_identity = next(
        (item for item in config.identities_by_token.values() if item.actor_id == identity.actor_id),
        None,
    )
    if auth_mode not in {"auto", "static"} or static_identity is None:
        raise ConfigError("router_authentication_unsupported")
    selected_identity = static_identity
    env_name = credential_env or selected_identity.token_env
    if not env_name or not env_name.replace("_", "A").isalnum() or env_name[0].isdigit():
        raise ConfigError("credential environment variable must contain only letters, digits, and underscores")
    if client == "codex":
        policy = PolicyRuntime(config).snapshot_for(selected_identity).effective_policy
        allowed_models = set(policy.allowed_models) if policy.allowed_models is not None else None
        default_model = next(
            (
                alias
                for alias, route in config.model_routes.items()
                if route.protocol == "openai" and (allowed_models is None or alias in allowed_models)
            ),
            None,
        )
        if default_model is None:
            raise ConfigError(f"Identity {selected_identity.actor_id} has no allowed OpenAI model for Codex")
        lines = [
            "# Put this in the user-level ~/.codex/config.toml",
            f"model = {json.dumps(default_model)}",
            'model_provider = "hormuz"',
            *(['web_search = "disabled"'] if work_headers else []),
            "",
            "[model_providers.hormuz]",
            'name = "Hormuz"',
            f"base_url = {json.dumps(base_url + '/v1')}",
            'wire_api = "responses"',
        ]
        if work_headers:
            lines.append('http_headers = { "X-Hormuz-Work-Id" = ' + json.dumps(work_id) + ' }')
        lines.insert(-1, f'env_key = "{env_name}"')
        print("\n".join(lines))
    else:
        print(f"export ANTHROPIC_BASE_URL={shlex.quote(base_url)}")
        print(f'export ANTHROPIC_AUTH_TOKEN="${{{env_name}}}"')
        if work_headers:
            print("export ANTHROPIC_CUSTOM_HEADERS=" + shlex.quote("X-Hormuz-Work-Id: " + work_id))
        print("claude")
    return 0


def _work_headers(work_id: str | None) -> dict[str, str]:
    if work_id is None:
        return {}
    if not isinstance(work_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", work_id) is None:
        raise ConfigError("work ID must be a bounded ASCII job identifier")
    return {"X-Hormuz-Work-Id": work_id}


def _auth_token(env_name: str) -> int:
    value = os.environ.get(env_name, "")
    if not value:
        print(f"credential environment variable is not set: {env_name}", file=sys.stderr)
        return 1
    if len(value.encode("utf-8")) > 64 * 1024 or "\n" in value or "\r" in value:
        print(f"credential environment variable is invalid: {env_name}", file=sys.stderr)
        return 1
    print(value)
    return 0


def _client_base_url(value: str) -> str:
    result = value.rstrip("/")
    parsed = urlparse(result)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(character in result for character in ("\n", "\r", "\x00"))
    ):
        raise ConfigError("client gateway URL must be an HTTP(S) URL without credentials, query, or fragment")
    return result


def run_lifetime(args: argparse.Namespace) -> int:
    from pathlib import Path
    from ..connected_client import ConnectedClientError, ConnectedClientLifetime, lifetime_error
    try:
        lifetime = ConnectedClientLifetime(Path(args.client_home or os.environ.get("CODEX_HOME", "~/.codex")))
        if args.client_command == "setup":
            from ..local_setup import setup_local
            result = setup_local(lifetime, args)
        elif args.client_command == "connect":
            result = lifetime.connect(Path(args.config), executable=args.executable, actor_id=args.actor, preview=args.dry_run)
        elif args.client_command == "diagnose":
            from ..plugin_diagnostics import diagnose
            result = diagnose(lifetime, Path(args.config), args.executable, args.project, args.launch_arg)
        elif args.client_command == "status":
            result = lifetime.status()
        else:
            result = lifetime.disconnect(generation=args.generation)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ConnectedClientError, ConfigError, OSError, subprocess.SubprocessError) as error:
        if args.client_command == "diagnose":
            print(json.dumps({"schema_id": "hormuz.client-diagnosis", "schema_version": 1,
                              "error": {"code": "configuration_invalid" if isinstance(error, ConfigError)
                                        else "diagnosis_unavailable"}}))
            return 2
        print(json.dumps(lifetime_error(error)), file=sys.stderr)
        return 2
