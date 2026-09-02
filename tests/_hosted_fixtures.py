"""Synthetic state helpers, never a real IdP or a customer onboarding session."""

import base64
import json
import secrets
from pathlib import Path

from hormuz._hosted_config import load_profile
from hormuz._hosted_provider import load_provider_profile
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


def provider_profile(root: Path):
    staging, settings, hosted_document = profile(root)
    settings.update({
        "HORMUZ_HOSTED_MODE": "provider-pilot",
        "HORMUZ_OPENAI_PROVIDER_KEY": "synthetic-openai-provider-key",
        "HORMUZ_ANTHROPIC_PROVIDER_KEY": "synthetic-anthropic-provider-key",
        "HORMUZ_FAILOVER_REHEARSAL_KEY": "synthetic_rehearsal_" + "r" * 43,
        "HORMUZ_POSTGRES_DSN": "postgresql://runtime:synthetic@db.example.test/hormuz",
    })
    state = Path(hosted_document["state_directory"])
    routes = {
        "openai-primary": {
            "protocol": "openai", "upstream_model": "openai-primary-model",
            "input_cost_per_million": 1, "cache_read_cost_per_million": 1,
            "cache_write_cost_per_million": 1, "output_cost_per_million": 2,
            "failover_alias": "openai-secondary",
        },
        "openai-secondary": {
            "protocol": "openai", "upstream_model": "openai-secondary-model",
            "input_cost_per_million": 2, "cache_read_cost_per_million": 2,
            "cache_write_cost_per_million": 2, "output_cost_per_million": 4,
        },
        "anthropic-primary": {
            "protocol": "anthropic", "upstream_model": "anthropic-primary-model",
            "input_cost_per_million": 3, "cache_read_cost_per_million": 3,
            "cache_write_cost_per_million": 3, "output_cost_per_million": 6,
            "failover_alias": "anthropic-secondary",
        },
        "anthropic-secondary": {
            "protocol": "anthropic", "upstream_model": "anthropic-secondary-model",
            "input_cost_per_million": 4, "cache_read_cost_per_million": 4,
            "cache_write_cost_per_million": 4, "output_cost_per_million": 8,
        },
    }
    document = {
        "listen": {"host": "127.0.0.1", "port": 8787},
        "ingress": {
            "mode": "external_tls_proxy", "trusted_proxy_cidrs": ["127.0.0.1/32"],
            "credential_env": "HORMUZ_INGRESS_CREDENTIAL",
        },
        "database": str(state / "usage.sqlite3"),
        "usage_storage": {
            "backend": "postgresql",
            "postgres_dsn_env": "HORMUZ_POSTGRES_DSN",
            "postgres_migration_dsn_env": "HORMUZ_POSTGRES_MIGRATION_DSN",
            "postgres_schema": "hormuz",
            "postgres_runtime_role": "hormuz_runtime",
            "postgres_pool": {
                "min_connections": 1,
                "max_connections": 4,
                "acquire_timeout_seconds": 5,
                "max_waiting": 8,
                "max_lifetime_seconds": 1800,
                "max_idle_seconds": 300,
            },
        },
        "max_request_bytes": 2 * 1024 * 1024,
        "upstream_timeout_seconds": 60,
        "upstreams": {
            "openai": {
                "base_url": "https://api.openai.com",
                "api_key_env": "HORMUZ_OPENAI_PROVIDER_KEY",
                "allow_response_storage": False,
                "allow_background": False,
            },
            "anthropic": {
                "base_url": "https://api.anthropic.com",
                "api_key_env": "HORMUZ_ANTHROPIC_PROVIDER_KEY",
                "allow_response_storage": False,
                "allow_background": False,
            },
        },
        "authentication": {
            "session_broker": {
                "enabled": True, "public_base_url": hosted_document["public_origin"],
                "database": str(state / "sessions.sqlite3"),
                "master_key_env": "HORMUZ_SESSION_MASTER_KEY",
                "access_ttl_seconds": 600, "absolute_ttl_seconds": 43200,
                "enrollment_ttl_seconds": 300, "onboarding_enabled": True,
                "console_enabled": True,
            },
            "oidc": {"issuers": [{
                "issuer": hosted_document["oidc_issuer"],
                "audiences": ["hormuz-staging-api"],
                "login": {
                    "client_id": hosted_document["oidc_client_id"],
                    "client_secret_env": "HORMUZ_OIDC_CLIENT_SECRET",
                    "scopes": ["openid", "email"],
                    "token_endpoint_auth_method": "client_secret_basic",
                },
                "subjects": [],
            }]},
        },
        "identities": [],
        "model_routes": routes,
        "egress_controls": {"secrets": {"mode": "redact", "builtins": True, "custom_secret_envs": []}},
        "policies": {
            "organization": {
                "allowed_clients": ["codex", "claude-code"],
                "allowed_models": list(routes),
                "fallback_models": {"openai": "openai-primary", "anthropic": "anthropic-primary"},
                "max_output_tokens": 4096,
                "monthly_budget_usd": 100,
                "per_actor_monthly_budget_usd": 25,
            },
            "teams": {}, "actors": {},
        },
    }
    path = root / "provider.json"
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    settings["HORMUZ_PROVIDER_CONFIG"] = str(path)
    return load_provider_profile(staging.source_path, path, settings), staging, settings, document


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
