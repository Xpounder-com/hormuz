#!/usr/bin/env python3
"""Prove bounded Hormuz behavior across a disposable PostgreSQL interruption.

The tool is intentionally narrow.  It creates no customer data, accepts
database credentials only from its process environment, and writes one
content-free evidence summary only after it has proved all of these facts:

* a live gateway is ready and can relay a governed request;
* an abrupt stop of its labelled disposable database withdraws readiness and
  prevents provider egress;
* restarting that same disposable database lets the same gateway and public
  pool boundary recover;
* a later, new request succeeds without replaying the failed request; and
* the retained evidence and tenant boundary remain usable after recovery.

It is not an HA, failover, PITR, RPO/RTO, or production recovery claim.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
import http.client
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from uuid import uuid4

from hormuz.config import GatewayConfig, Identity
from hormuz.policy_control import PolicyControlService
from hormuz.postgres import POSTGRES_SCHEMA_VERSION, PostgresStorageError, migrate_postgres, validate_postgres_identifier
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.server import GatewayServer, serve_in_thread

try:
    from tools._verification_runtime import (
        is_pinned_image_reference,
        run_container_command,
        write_private_json_evidence,
    )
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import (  # type: ignore[no-redef]
        is_pinned_image_reference,
        run_container_command,
        write_private_json_evidence,
    )


SUMMARY_SCHEMA_ID = "hormuz.postgresql-interruption-recovery"
SUMMARY_SCHEMA_VERSION = 1
SUMMARY_COVERAGE = "ephemeral_single_database_abrupt_interrupt_restart_only"
_POSTGRES_VERSION_PATTERN = re.compile(r"\d+\.\d+(?:\.\d+)?\Z")
_CONTAINER_PATTERN = re.compile(r"hormuz-postgres-interruption-[a-z0-9-]{8,80}\Z")
_DISPOSABLE_LABEL = "io.hormuz.disposable-interruption"
_REQUIRED_ENVIRON = (
    "HORMUZ_POSTGRES_INTERRUPTION_OPERATOR_DSN",
    "HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_DSN",
    "HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_DSN",
    "HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_ROLE",
    "HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_PASSWORD",
    "HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_ROLE",
    "HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_PASSWORD",
)
_CONFIRMATION_VALUE = "I_UNDERSTAND_DISPOSABLE_DATABASE_INTERRUPTION"
_CHECK_KEYS = (
    "initial_readiness",
    "initial_governed_request",
    "readiness_withdrawn_during_interruption",
    "egress_blocked_during_interruption",
    "same_pool_recovered",
    "post_recovery_governed_request",
    "shared_evidence_preserved",
    "tenant_rls_preserved",
    "no_automatic_provider_replay",
)
_DURATION_KEYS = (
    "initialization",
    "interruption_detection",
    "recovery",
    "total",
)


class InterruptionRecoveryError(RuntimeError):
    """A stable, content-free failure from the disposable interruption drill."""


class _ProviderHandler(BaseHTTPRequestHandler):
    """Count admission without retaining any caller payload or response content."""

    protocol_version = "HTTP/1.1"
    request_count = 0
    request_lock = threading.Lock()

    @classmethod
    def reset(cls) -> None:
        with cls.request_lock:
            cls.request_count = 0

    @classmethod
    def count(cls) -> int:
        with cls.request_lock:
            return cls.request_count

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        self.rfile.read(max(0, length))
        with type(self).request_lock:
            type(self).request_count += 1
        body = json.dumps(
            {
                "id": "resp_interruption_fixture",
                "object": "response",
                "status": "completed",
                "model": "gpt-5.4-mini",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-request-id", "req_interruption_fixture")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="exercise one labelled disposable PostgreSQL interruption")
    run.add_argument("--container", required=True)
    run.add_argument("--database-image", required=True)
    run.add_argument("--database-version", required=True)
    run.add_argument("--evidence-out", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command != "run":  # pragma: no cover - argparse enforces the sole command
            raise InterruptionRecoveryError("interruption_command_invalid")
        summary = run_interruption_recovery(
            container=args.container,
            database_image=args.database_image,
            database_version=args.database_version,
        )
        write_summary(args.evidence_out, summary)
        return 0
    except InterruptionRecoveryError as error:
        print(f"PostgreSQL interruption recovery failed: {error}", file=sys.stderr)
        return 1
    except (PostgresStorageError, OSError, ValueError, http.client.HTTPException, subprocess.SubprocessError):
        print("PostgreSQL interruption recovery failed: interruption_runtime_unavailable", file=sys.stderr)
        return 1
    except Exception:  # Keep diagnostics content-free even for an unexpected library failure.
        print("PostgreSQL interruption recovery failed: interruption_unexpected_failure", file=sys.stderr)
        return 1


def run_interruption_recovery(
    *,
    container: str,
    database_image: str,
    database_version: str,
) -> dict[str, object]:
    """Run the end-to-end proof without exposing fixture inputs in evidence."""

    _require_explicit_opt_in()
    container = validate_disposable_container_name(container)
    _validate_database_identity(database_image=database_image, database_version=database_version)
    _assert_disposable_container(container)
    _assert_database_image(container, expected_image=database_image)
    _assert_database_version(container, expected_version=database_version)
    environment = _required_environment()
    started = time.monotonic()
    _ProviderHandler.reset()

    provider: ThreadingHTTPServer | None = None
    provider_thread: threading.Thread | None = None
    gateway: GatewayServer | None = None
    gateway_thread: threading.Thread | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="hormuz-postgres-interruption-") as directory_name:
            directory = Path(directory_name)
            suffix = uuid4().hex[:12]
            schema = f"hormuz_interrupt_{suffix}"
            runtime_role = validate_postgres_identifier(
                environment["HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_ROLE"],
                "postgres_runtime_role",
            )
            policy_control_role = validate_postgres_identifier(
                environment["HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_ROLE"],
                "postgres_policy_control_role",
            )
            runtime_password = environment["HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_PASSWORD"]
            policy_control_password = environment["HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_PASSWORD"]
            _provision_restricted_roles(
                operator_dsn=environment["HORMUZ_POSTGRES_INTERRUPTION_OPERATOR_DSN"],
                runtime_role=runtime_role,
                runtime_password=runtime_password,
                policy_control_role=policy_control_role,
                policy_control_password=policy_control_password,
            )
            status = migrate_postgres(
                environment["HORMUZ_POSTGRES_INTERRUPTION_OPERATOR_DSN"],
                schema=schema,
                runtime_role=runtime_role,
                policy_control_role=policy_control_role,
                custody_control_role=_custody_control_role(policy_control_role),
            )
            if not status.complete or status.version != POSTGRES_SCHEMA_VERSION:
                raise InterruptionRecoveryError("interruption_migration_not_current")

            config, gateway_environment, identity = _fixture_config(
                directory,
                runtime_dsn=environment["HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_DSN"],
                policy_control_dsn=environment["HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_DSN"],
                schema=schema,
                runtime_role=runtime_role,
                policy_control_role=policy_control_role,
            )
            service = PolicyControlService(config, environ=gateway_environment)
            service.bootstrap(
                organization_id=identity.organization_id,
                credential_env="HORMUZ_INTERRUPTION_ADMIN_TOKEN",
            )
            policy_path = directory / "policy.json"
            policy_path.write_text(
                json.dumps(_policy_document(identity), sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            staged = service.stage(
                organization_id=identity.organization_id,
                credential_env="HORMUZ_INTERRUPTION_ADMIN_TOKEN",
                policy_path=policy_path,
            )
            service.activate(
                organization_id=identity.organization_id,
                credential_env="HORMUZ_INTERRUPTION_ADMIN_TOKEN",
                version_id=staged.version_id,
            )

            provider = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
            provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
            provider_thread.start()
            upstreams = dict(config.upstreams)
            upstreams["openai"] = replace(
                upstreams["openai"],
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                api_key_env="HORMUZ_INTERRUPTION_OPENAI_KEY",
            )
            config = replace(config, upstreams=upstreams)
            gateway_environment["HORMUZ_INTERRUPTION_OPENAI_KEY"] = "interruption-fixture-provider-key"

            initialization_finished = time.monotonic()
            with mock.patch.dict(os.environ, gateway_environment, clear=False):
                gateway = GatewayServer(config)
                gateway_thread = serve_in_thread(gateway)
                _require_ready(gateway)
                _require_successful_request(gateway, identity.token, input_value="initial fixture request")
                if _ProviderHandler.count() != 1:
                    raise InterruptionRecoveryError("interruption_initial_provider_admission_invalid")

                with _quiet_pool_connection_logs():
                    interruption_started = time.monotonic()
                    _docker(("kill", container), timeout_seconds=10)
                    _require_not_ready(gateway)
                    _require_blocked_request(
                        gateway,
                        identity.token,
                        input_value="interruption request must not reach provider",
                    )
                    if _ProviderHandler.count() != 1:
                        raise InterruptionRecoveryError("interruption_provider_egress_not_blocked")
                    interruption_finished = time.monotonic()

                    recovery_started = time.monotonic()
                    _docker(("start", container), timeout_seconds=10)
                    _wait_for_postgres(container)
                    _wait_for_gateway_ready(gateway)
                if gateway.postgres_pool is None or gateway.postgres_pool.closed:
                    raise InterruptionRecoveryError("interruption_pool_did_not_remain_open")
                _require_successful_request(gateway, identity.token, input_value="post-recovery fixture request")
                if _ProviderHandler.count() != 2:
                    raise InterruptionRecoveryError("interruption_provider_replay_or_admission_invalid")

                totals = gateway.store.monthly_totals(organization_id=identity.organization_id)
                if totals.requests != 2 or totals.denied_requests != 0:
                    raise InterruptionRecoveryError("interruption_shared_evidence_invalid")
                independent_store = PostgresUsageStore(
                    environment["HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_DSN"],
                    organization_ids=(identity.organization_id, "beta"),
                    schema=schema,
                    runtime_role=runtime_role,
                )
                if independent_store.monthly_totals(organization_id="beta").requests != 0:
                    raise InterruptionRecoveryError("interruption_tenant_rls_invalid")
                recovery_finished = time.monotonic()

            return build_summary(
                database_image=database_image,
                database_version=database_version,
                checks={key: True for key in _CHECK_KEYS},
                durations_ms={
                    "initialization": _duration_ms(started, initialization_finished),
                    "interruption_detection": _duration_ms(interruption_started, interruption_finished),
                    "recovery": _duration_ms(recovery_started, recovery_finished),
                    "total": _duration_ms(started, recovery_finished),
                },
            )
    finally:
        if gateway is not None:
            try:
                gateway.shutdown()
            finally:
                gateway.server_close()
        if gateway_thread is not None:
            gateway_thread.join(timeout=10)
        if provider is not None:
            provider.shutdown()
            provider.server_close()
        if provider_thread is not None:
            provider_thread.join(timeout=10)


def validate_disposable_container_name(value: str) -> str:
    """Reject names that could refer to a non-fixture Docker container."""

    if not isinstance(value, str) or _CONTAINER_PATTERN.fullmatch(value) is None:
        raise InterruptionRecoveryError("interruption_container_not_disposable")
    return value


def build_summary(
    *,
    database_image: str,
    database_version: str,
    checks: Mapping[str, object],
    durations_ms: Mapping[str, object],
) -> dict[str, object]:
    """Build the only retained result and validate it before writing anything."""

    _validate_database_identity(database_image=database_image, database_version=database_version)
    if set(checks) != set(_CHECK_KEYS) or any(checks[key] is not True for key in checks):
        raise InterruptionRecoveryError("interruption_summary_checks_invalid")
    if set(durations_ms) != set(_DURATION_KEYS):
        raise InterruptionRecoveryError("interruption_summary_durations_invalid")
    if any(
        not isinstance(durations_ms[key], int)
        or isinstance(durations_ms[key], bool)
        or int(durations_ms[key]) < 0
        for key in durations_ms
    ):
        raise InterruptionRecoveryError("interruption_summary_durations_invalid")
    summary: dict[str, object] = {
        "schema_id": SUMMARY_SCHEMA_ID,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "coverage": SUMMARY_COVERAGE,
        "verdict": "pass",
        "database": {"image": database_image, "version": database_version},
        "checks": dict(checks),
        "durations_ms": dict(durations_ms),
    }
    validate_summary(summary)
    return summary


def validate_summary(value: Mapping[str, object]) -> None:
    """Require a fixed, content-free evidence shape with no optional fields."""

    if set(value) != {
        "schema_id",
        "schema_version",
        "coverage",
        "verdict",
        "database",
        "checks",
        "durations_ms",
    }:
        raise InterruptionRecoveryError("interruption_summary_schema_invalid")
    if value.get("schema_id") != SUMMARY_SCHEMA_ID or value.get("schema_version") != SUMMARY_SCHEMA_VERSION:
        raise InterruptionRecoveryError("interruption_summary_schema_invalid")
    if value.get("coverage") != SUMMARY_COVERAGE or value.get("verdict") != "pass":
        raise InterruptionRecoveryError("interruption_summary_schema_invalid")
    database = _mapping(value.get("database"), "interruption_summary_schema_invalid")
    if set(database) != {"image", "version"}:
        raise InterruptionRecoveryError("interruption_summary_schema_invalid")
    _validate_database_identity(
        database_image=database.get("image"),
        database_version=database.get("version"),
    )
    checks = _mapping(value.get("checks"), "interruption_summary_schema_invalid")
    if set(checks) != set(_CHECK_KEYS) or any(checks[key] is not True for key in checks):
        raise InterruptionRecoveryError("interruption_summary_schema_invalid")
    durations = _mapping(value.get("durations_ms"), "interruption_summary_schema_invalid")
    if set(durations) != set(_DURATION_KEYS) or any(
        not isinstance(durations[key], int) or isinstance(durations[key], bool) or int(durations[key]) < 0
        for key in durations
    ):
        raise InterruptionRecoveryError("interruption_summary_schema_invalid")


def write_summary(path: Path, summary: Mapping[str, object]) -> None:
    """Atomically write the validated, owner-only summary and nothing else."""

    validate_summary(summary)
    try:
        write_private_json_evidence(
            path,
            summary,
            temporary_prefix=".hormuz-interruption-",
            parent_mode=0o700,
        )
    except OSError as error:
        raise InterruptionRecoveryError("interruption_summary_write_failed") from error


def _fixture_config(
    directory: Path,
    *,
    runtime_dsn: str,
    policy_control_dsn: str,
    schema: str,
    runtime_role: str,
    policy_control_role: str,
) -> tuple[GatewayConfig, dict[str, str], Identity]:
    identity_value = {
        "token_env": "HORMUZ_INTERRUPTION_ADMIN_TOKEN",
        "actor_id": "interruption-alice",
        "actor_name": "Interruption Fixture",
        "team_id": "engineering",
        "team_name": "Engineering",
        "organization_id": "xpounder",
        "identity_type": "human",
        "clearance": "confidential",
        "allowed_clients": ["codex"],
    }
    payload: dict[str, object] = {
        "listen": {"host": "127.0.0.1", "port": _free_port()},
        "database": "./unused.sqlite3",
        "upstreams": {
            "openai": {
                "base_url": "http://127.0.0.1:1/v1",
                "api_key_env": "HORMUZ_INTERRUPTION_OPENAI_KEY",
                "allow_response_storage": False,
                "allow_background": False,
            },
            "anthropic": {
                "base_url": "http://127.0.0.1:1/v1",
                "api_key_env": "HORMUZ_INTERRUPTION_ANTHROPIC_KEY",
            },
        },
        "authentication": {"oidc": {"issuers": []}},
        "identities": [identity_value],
        "model_routes": {
            "gpt-5.4-mini": {
                "protocol": "openai",
                "upstream_model": "gpt-5.4-mini",
                "input_cost_per_million": 0.75,
                "cache_read_cost_per_million": 0.075,
                "cache_write_cost_per_million": 0.75,
                "output_cost_per_million": 4.5,
            }
        },
        "egress_controls": {"secrets": {"mode": "redact", "builtins": True, "custom_secret_envs": []}},
        "usage_storage": {
            "backend": "postgresql",
            "postgres_dsn_env": "HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_DSN",
            "postgres_migration_dsn_env": "HORMUZ_POSTGRES_INTERRUPTION_OPERATOR_DSN",
            "postgres_schema": schema,
            "postgres_runtime_role": runtime_role,
            "postgres_pool": {
                "min_connections": 1,
                "max_connections": 2,
                "acquire_timeout_seconds": 1,
                "max_waiting": 4,
                "max_lifetime_seconds": 300,
                "max_idle_seconds": 60,
            },
        },
        "policy_control": {
            "mode": "postgresql",
            "postgres_control_dsn_env": "HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_DSN",
            "postgres_control_role": policy_control_role,
            "bootstrap_administrators": [
                {"organization_id": "xpounder", "actor_id": "interruption-alice"}
            ],
        },
    }
    config_path = directory / "interruption-fixture.json"
    config_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    environment = {
        "HORMUZ_POSTGRES_INTERRUPTION_RUNTIME_DSN": runtime_dsn,
        "HORMUZ_POSTGRES_INTERRUPTION_OPERATOR_DSN": "operator-dsn-not-used-by-runtime",
        "HORMUZ_POSTGRES_INTERRUPTION_POLICY_CONTROL_DSN": policy_control_dsn,
        "HORMUZ_INTERRUPTION_ADMIN_TOKEN": "interruption-fixture-identity-token",
        "HORMUZ_INTERRUPTION_ANTHROPIC_KEY": "interruption-fixture-anthropic-key",
    }
    config = GatewayConfig.load(config_path, environ=environment)
    return config, environment, config.identities_by_actor["interruption-alice"]


def _policy_document(identity: Identity) -> dict[str, object]:
    return {
        "schema_id": "hormuz.policy-document",
        "schema_version": 1,
        "organization_id": identity.organization_id,
        "policies": {
            "organization": {
                "allowed_clients": ["codex"],
                "allowed_models": ["gpt-5.4-mini"],
                "max_output_tokens": 1_000,
                "monthly_budget_usd": 100,
                "per_actor_monthly_budget_usd": 50,
            },
            "teams": {
                identity.team_id: {
                    "allowed_models": ["gpt-5.4-mini"],
                    "fallback_models": {"openai": "gpt-5.4-mini"},
                    "max_output_tokens": 800,
                    "monthly_budget_usd": 75,
                }
            },
            "actors": {},
        },
        "egress_controls": {
            "openai": {"allow_response_storage": False, "allow_background": False},
            "secrets": {"mode": "redact"},
        },
    }


def _provision_restricted_roles(
    *,
    operator_dsn: str,
    runtime_role: str,
    runtime_password: str,
    policy_control_role: str,
    policy_control_password: str,
) -> None:
    runtime_role = validate_postgres_identifier(runtime_role, "postgres_runtime_role")
    policy_control_role = validate_postgres_identifier(policy_control_role, "postgres_policy_control_role")
    if runtime_role == policy_control_role:
        raise InterruptionRecoveryError("interruption_roles_must_be_distinct")
    custody_control_role = _custody_control_role(policy_control_role)
    try:
        import psycopg
        from psycopg import sql
    except ImportError as error:  # pragma: no cover - package-install CI gate covers this path
        raise InterruptionRecoveryError("interruption_postgres_driver_unavailable") from error
    try:
        with psycopg.connect(operator_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                for role, password in (
                    (runtime_role, runtime_password),
                    (policy_control_role, policy_control_password),
                ):
                    cursor.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN PASSWORD {} "
                            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                        ).format(sql.Identifier(role), sql.Literal(password))
                    )
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                    ).format(sql.Identifier(custody_control_role))
                )
    except psycopg.Error as error:
        raise InterruptionRecoveryError("interruption_role_provisioning_failed") from error


def _custody_control_role(policy_control_role: str) -> str:
    return validate_postgres_identifier(
        f"{policy_control_role}_custody",
        "postgres_custody_control_role",
    )


def _require_ready(gateway: GatewayServer) -> None:
    status, body = _get(gateway, "/ready")
    if status != 200:
        raise InterruptionRecoveryError("interruption_initial_readiness_failed")
    response = _json_object(body, "interruption_initial_readiness_failed")
    if response.get("status") != "ready" or response.get("reason") is not None:
        raise InterruptionRecoveryError("interruption_initial_readiness_failed")


def _require_not_ready(gateway: GatewayServer) -> None:
    status, body = _get(gateway, "/ready", timeout_seconds=12)
    if status != 503:
        raise InterruptionRecoveryError("interruption_readiness_not_withdrawn")
    response = _json_object(body, "interruption_readiness_not_withdrawn")
    if response.get("reason") != "dependency_unavailable":
        raise InterruptionRecoveryError("interruption_readiness_not_withdrawn")


def _wait_for_gateway_ready(gateway: GatewayServer) -> None:
    # Psycopg reconnects with exponential backoff. This is a correctness
    # exercise, not an RTO claim, so allow a bounded window for a fresh
    # background cycle after the disposable database is accepting clients.
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            status, body = _get(gateway, "/ready", timeout_seconds=5)
            response = _json_object(body, "interruption_gateway_recovery_failed")
            if status == 200 and response.get("status") == "ready" and response.get("reason") is None:
                return
        except (InterruptionRecoveryError, OSError, http.client.HTTPException):
            pass
        time.sleep(0.25)
    raise InterruptionRecoveryError("interruption_gateway_recovery_failed")


def _require_successful_request(gateway: GatewayServer, token: str, *, input_value: str) -> None:
    status, _body = _post(gateway, token=token, input_value=input_value, timeout_seconds=12)
    if status != 200:
        raise InterruptionRecoveryError("interruption_governed_request_failed")


def _require_blocked_request(gateway: GatewayServer, token: str, *, input_value: str) -> None:
    status, body = _post(gateway, token=token, input_value=input_value, timeout_seconds=12)
    if status != 503:
        raise InterruptionRecoveryError("interruption_egress_not_blocked")
    response = _json_object(body, "interruption_egress_not_blocked")
    error = _mapping(response.get("error"), "interruption_egress_not_blocked")
    if error.get("code") != "hormuz_storage_unavailable":
        raise InterruptionRecoveryError("interruption_egress_not_blocked")
    if input_value in json.dumps(response, sort_keys=True):
        raise InterruptionRecoveryError("interruption_error_not_content_free")


def _get(gateway: GatewayServer, path: str, *, timeout_seconds: int = 6) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=timeout_seconds)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _post(
    gateway: GatewayServer,
    *,
    token: str,
    input_value: str,
    timeout_seconds: int,
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=timeout_seconds)
    try:
        connection.request(
            "POST",
            "/v1/responses",
            body=json.dumps({"model": "gpt-5.4-mini", "input": input_value}),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _require_explicit_opt_in() -> None:
    if os.environ.get("HORMUZ_RUN_POSTGRES_INTERRUPTION_RECOVERY") != "1":
        raise InterruptionRecoveryError("interruption_opt_in_required")
    if os.environ.get("HORMUZ_POSTGRES_INTERRUPTION_CONFIRMATION") != _CONFIRMATION_VALUE:
        raise InterruptionRecoveryError("interruption_confirmation_required")


def _required_environment() -> dict[str, str]:
    values = {name: os.environ.get(name, "") for name in _REQUIRED_ENVIRON}
    if any(not value for value in values.values()):
        raise InterruptionRecoveryError("interruption_database_credential_unavailable")
    return values


def _assert_disposable_container(container: str) -> None:
    result = _docker(
        ("inspect", "--format", "{{ index .Config.Labels \"io.hormuz.disposable-interruption\" }}", container),
        timeout_seconds=5,
    )
    if result.stdout.strip() != "true":
        raise InterruptionRecoveryError("interruption_container_not_disposable")


def _assert_database_image(container: str, *, expected_image: str) -> None:
    result = _docker(("inspect", "--format", "{{ .Config.Image }}", container), timeout_seconds=5)
    if result.stdout.strip() != expected_image:
        raise InterruptionRecoveryError("interruption_database_image_mismatch")


def _assert_database_version(container: str, *, expected_version: str) -> None:
    result = _docker(("exec", container, "postgres", "--version"), timeout_seconds=5)
    pattern = re.compile(rf"\bPostgreSQL\)?\s+{re.escape(expected_version)}(?:\s|\Z)")
    if pattern.search(result.stdout) is None:
        raise InterruptionRecoveryError("interruption_database_version_mismatch")


def _wait_for_postgres(container: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = _docker(
            ("exec", container, "pg_isready", "--username=postgres"),
            timeout_seconds=5,
            require_success=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise InterruptionRecoveryError("interruption_database_restart_failed")


def _docker(
    arguments: tuple[str, ...],
    *,
    timeout_seconds: int,
    require_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = run_container_command(
            ("docker", *arguments),
            timeout_seconds=timeout_seconds,
            capture_stderr=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise InterruptionRecoveryError("interruption_docker_unavailable") from error
    if require_success and result.returncode != 0:
        raise InterruptionRecoveryError("interruption_docker_command_failed")
    return result


def _validate_database_identity(*, database_image: object, database_version: object) -> None:
    if not is_pinned_image_reference(database_image, image_name="postgres"):
        raise InterruptionRecoveryError("interruption_database_image_not_pinned")
    if not isinstance(database_version, str) or _POSTGRES_VERSION_PATTERN.fullmatch(database_version) is None:
        raise InterruptionRecoveryError("interruption_database_version_invalid")


def _json_object(body: bytes, code: str) -> dict[str, object]:
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InterruptionRecoveryError(code) from error
    return dict(_mapping(value, code))


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InterruptionRecoveryError(code)
    return value


def _duration_ms(started: float, finished: float) -> int:
    return max(0, int((finished - started) * 1000))


@contextmanager
def _quiet_pool_connection_logs():
    """Keep the intentional outage probe limited to its final stable result."""

    loggers = tuple(
        logging.getLogger(name)
        for name in ("psycopg", "psycopg.pool", "psycopg_pool", "hormuz", "hormuz.server")
    )
    original_levels = tuple(logger.level for logger in loggers)
    try:
        for logger in loggers:
            logger.setLevel(logging.CRITICAL + 1)
        yield
    finally:
        for logger, level in zip(loggers, original_levels, strict=True):
            logger.setLevel(level)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


if __name__ == "__main__":  # pragma: no cover - exercised by the shell/CI gate
    raise SystemExit(main())
