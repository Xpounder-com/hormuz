#!/usr/bin/env python3
"""Run an opt-in synthetic PostgreSQL tenant-isolation feasibility proof."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any, Callable


EVIDENCE_SCHEMA = "hormuz.postgres-rls-feasibility.v1"
DEFAULT_IMAGE = (
    "postgres@sha256:"
    "57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
)
IMAGE_REFERENCE = re.compile(r"postgres@sha256:[0-9a-f]{64}\Z")
VERSION_NUMBER = re.compile(r"[0-9]{5,6}\Z")
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
FINAL_STARTUP_MARKER = "PostgreSQL init process complete; ready for start up."

SETUP_SQL = """
CREATE ROLE hormuz_owner
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE ROLE hormuz_runtime
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE SCHEMA rls_spike AUTHORIZATION hormuz_owner;
SET ROLE hormuz_owner;
CREATE TABLE rls_spike.workspaces (
  tenant_id text NOT NULL,
  workspace_id text NOT NULL,
  name text NOT NULL,
  PRIMARY KEY (tenant_id, workspace_id)
);
CREATE TABLE rls_spike.records (
  tenant_id text NOT NULL,
  record_id text NOT NULL,
  workspace_id text NOT NULL,
  value text NOT NULL,
  PRIMARY KEY (tenant_id, record_id),
  FOREIGN KEY (tenant_id, workspace_id)
    REFERENCES rls_spike.workspaces (tenant_id, workspace_id)
);
ALTER TABLE rls_spike.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE rls_spike.workspaces FORCE ROW LEVEL SECURITY;
ALTER TABLE rls_spike.records ENABLE ROW LEVEL SECURITY;
ALTER TABLE rls_spike.records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rls_spike.workspaces
  USING (
    tenant_id = NULLIF(current_setting('hormuz.tenant_id', true), '')
  )
  WITH CHECK (
    tenant_id = NULLIF(current_setting('hormuz.tenant_id', true), '')
  );
CREATE POLICY tenant_isolation ON rls_spike.records
  USING (
    tenant_id = NULLIF(current_setting('hormuz.tenant_id', true), '')
  )
  WITH CHECK (
    tenant_id = NULLIF(current_setting('hormuz.tenant_id', true), '')
  );
GRANT USAGE ON SCHEMA rls_spike TO hormuz_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA rls_spike TO hormuz_runtime;
BEGIN;
SET LOCAL hormuz.tenant_id = 'tenant-a';
INSERT INTO rls_spike.workspaces
  VALUES ('tenant-a', 'workspace-a', 'A');
INSERT INTO rls_spike.records
  VALUES ('tenant-a', 'record-a', 'workspace-a', 'A-value');
COMMIT;
BEGIN;
SET LOCAL hormuz.tenant_id = 'tenant-b';
INSERT INTO rls_spike.workspaces
  VALUES ('tenant-b', 'workspace-b', 'B');
INSERT INTO rls_spike.records
  VALUES ('tenant-b', 'record-b', 'workspace-b', 'B-value');
COMMIT;
RESET ROLE;
""".strip()

PROOF_SQL = """
SHOW server_version_num;
SET ROLE hormuz_runtime;
SELECT 'runtime_role|' || rolsuper || '|' || rolbypassrls
  FROM pg_roles WHERE rolname = 'hormuz_runtime';
SELECT 'workspace_rls_flags|' || relrowsecurity || '|' || relforcerowsecurity
  FROM pg_class WHERE oid = 'rls_spike.workspaces'::regclass;
SELECT 'record_rls_flags|' || relrowsecurity || '|' || relforcerowsecurity
  FROM pg_class WHERE oid = 'rls_spike.records'::regclass;
SELECT 'record_owner|' || pg_get_userbyid(relowner)
  FROM pg_class WHERE oid = 'rls_spike.records'::regclass;
SELECT 'missing_context_rows|' || count(*) FROM rls_spike.records;
BEGIN;
SET LOCAL hormuz.tenant_id = 'tenant-a';
SELECT 'tenant_a_visible|' || string_agg(record_id, ',')
  FROM rls_spike.records;
SELECT 'tenant_a_cross_read|' || count(*)
  FROM rls_spike.records WHERE tenant_id = 'tenant-b';
COMMIT;
SELECT 'reused_after_commit_rows|' || count(*) FROM rls_spike.records;
BEGIN;
SET LOCAL hormuz.tenant_id = 'tenant-b';
SELECT 'tenant_b_visible|' || string_agg(record_id, ',')
  FROM rls_spike.records;
COMMIT;
RESET ROLE;
SET ROLE hormuz_owner;
SELECT 'forced_owner_missing_context_rows|' || count(*)
  FROM rls_spike.records;
RESET ROLE;
""".strip()

CROSS_TENANT_WRITE_SQL = """
SET ROLE hormuz_runtime;
BEGIN;
SET LOCAL hormuz.tenant_id = 'tenant-a';
INSERT INTO rls_spike.records
  VALUES ('tenant-b', 'forbidden', 'workspace-b', 'forbidden');
COMMIT;
""".strip()

CROSS_TENANT_FOREIGN_KEY_SQL = """
SET ROLE hormuz_runtime;
BEGIN;
SET LOCAL hormuz.tenant_id = 'tenant-a';
INSERT INTO rls_spike.records
  VALUES ('tenant-a', 'bad-fk', 'workspace-b', 'forbidden');
COMMIT;
""".strip()


class PostgresRLSFeasibilityError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _execute(
    command: list[str],
    *,
    runner: CommandRunner,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = runner(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise PostgresRLSFeasibilityError("docker_unavailable") from None
    for value in (completed.stdout, completed.stderr):
        if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES:
            raise PostgresRLSFeasibilityError("docker_output_invalid")
    return completed


def _psql_command(container: str, sql: str, *, tuples_only: bool = False) -> list[str]:
    command = [
        "docker",
        "exec",
        container,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-X",
        "-q",
    ]
    if tuples_only:
        command.append("-At")
    command.extend(["-c", sql])
    return command


def _proof_values(output: str) -> tuple[str, set[str]]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    versions = [line for line in lines if VERSION_NUMBER.fullmatch(line)]
    observation_lines = [line for line in lines if "|" in line]
    expected = {
        "runtime_role|false|false",
        "workspace_rls_flags|true|true",
        "record_rls_flags|true|true",
        "record_owner|hormuz_owner",
        "missing_context_rows|0",
        "tenant_a_visible|record-a",
        "tenant_a_cross_read|0",
        "reused_after_commit_rows|0",
        "tenant_b_visible|record-b",
        "forced_owner_missing_context_rows|0",
    }
    observations = set(observation_lines)
    if (
        len(versions) != 1
        or len(observation_lines) != len(expected)
        or observations != expected
    ):
        raise PostgresRLSFeasibilityError("rls_observation_mismatch")
    return versions[0], observations


def run_feasibility(
    *,
    image: str = DEFAULT_IMAGE,
    runner: CommandRunner = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
    nonce_factory: Callable[[], str] = lambda: secrets.token_hex(8),
    password_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
) -> dict[str, Any]:
    if not isinstance(image, str) or IMAGE_REFERENCE.fullmatch(image) is None:
        raise PostgresRLSFeasibilityError("invalid_postgres_image")
    nonce = nonce_factory()
    password = password_factory()
    if (
        not isinstance(nonce, str)
        or re.fullmatch(r"[0-9a-f]{16}", nonce) is None
        or not isinstance(password, str)
        or not 32 <= len(password) <= 128
        or any(ord(character) < 0x21 or ord(character) == 0x7F for character in password)
    ):
        raise PostgresRLSFeasibilityError("invalid_ephemeral_credential")
    container = "hormuz-rls-feasibility-" + nonce
    launch_attempted = False
    removed = False
    primary_error: PostgresRLSFeasibilityError | None = None
    cleanup_error: PostgresRLSFeasibilityError | None = None
    postgres_version = ""
    observations: set[str] = set()
    runtime_image = image
    try:
        inspected = _execute(
            ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
            runner=runner,
            timeout=15,
        )
        if inspected.returncode != 0:
            local_image_id = image.split("@", maxsplit=1)[1]
            inspected = _execute(
                [
                    "docker",
                    "image",
                    "inspect",
                    local_image_id,
                    "--format",
                    "{{json .RepoDigests}}",
                ],
                runner=runner,
                timeout=15,
            )
            runtime_image = local_image_id
        try:
            digests = json.loads(inspected.stdout)
        except (json.JSONDecodeError, RecursionError):
            raise PostgresRLSFeasibilityError("postgres_image_unavailable") from None
        if inspected.returncode != 0 or not isinstance(digests, list) or image not in digests:
            raise PostgresRLSFeasibilityError("postgres_image_unavailable")

        launch_attempted = True
        launched = _execute(
            [
                "docker",
                "run",
                "--rm",
                "--detach",
                "--pull",
                "never",
                "--network",
                "none",
                "--name",
                container,
                "--env",
                "POSTGRES_PASSWORD=" + password,
                runtime_image,
            ],
            runner=runner,
            timeout=30,
        )
        if launched.returncode != 0:
            raise PostgresRLSFeasibilityError("container_start_failed")
        for _attempt in range(120):
            logs = _execute(
                ["docker", "logs", container],
                runner=runner,
                timeout=5,
            )
            startup_complete = FINAL_STARTUP_MARKER in (logs.stdout + logs.stderr)
            if startup_complete:
                ready = _execute(
                    [
                        "docker",
                        "exec",
                        container,
                        "pg_isready",
                        "-U",
                        "postgres",
                        "-d",
                        "postgres",
                    ],
                    runner=runner,
                    timeout=5,
                )
                if ready.returncode == 0:
                    break
            sleeper(0.25)
        else:
            raise PostgresRLSFeasibilityError("postgres_not_ready")

        setup = _execute(
            _psql_command(container, SETUP_SQL),
            runner=runner,
            timeout=30,
        )
        if setup.returncode != 0:
            raise PostgresRLSFeasibilityError("rls_setup_failed")

        proof = _execute(
            _psql_command(container, PROOF_SQL, tuples_only=True),
            runner=runner,
            timeout=30,
        )
        if proof.returncode != 0:
            raise PostgresRLSFeasibilityError("rls_proof_failed")
        postgres_version, observations = _proof_values(proof.stdout)

        cross_write = _execute(
            _psql_command(container, CROSS_TENANT_WRITE_SQL),
            runner=runner,
            timeout=15,
        )
        if (
            cross_write.returncode == 0
            or "violates row-level security policy" not in cross_write.stderr
        ):
            raise PostgresRLSFeasibilityError("cross_tenant_write_not_denied")

        cross_fk = _execute(
            _psql_command(container, CROSS_TENANT_FOREIGN_KEY_SQL),
            runner=runner,
            timeout=15,
        )
        if (
            cross_fk.returncode == 0
            or "violates foreign key constraint" not in cross_fk.stderr
        ):
            raise PostgresRLSFeasibilityError("cross_tenant_foreign_key_not_denied")
    except PostgresRLSFeasibilityError as error:
        primary_error = error
    finally:
        if launch_attempted:
            try:
                _execute(
                    ["docker", "stop", container],
                    runner=runner,
                    timeout=30,
                )
                for _attempt in range(40):
                    inspected = _execute(
                        ["docker", "container", "inspect", container],
                        runner=runner,
                        timeout=5,
                    )
                    if inspected.returncode != 0:
                        listed = _execute(
                            [
                                "docker",
                                "ps",
                                "--all",
                                "--quiet",
                                "--filter",
                                f"name=^/{container}$",
                            ],
                            runner=runner,
                            timeout=5,
                        )
                        if listed.returncode == 0 and not listed.stdout.strip():
                            removed = True
                            break
                    sleeper(0.1)
            except PostgresRLSFeasibilityError:
                cleanup_error = PostgresRLSFeasibilityError(
                    "container_cleanup_failed"
                )
    password = ""
    if cleanup_error is not None or (launch_attempted and not removed):
        raise PostgresRLSFeasibilityError("container_cleanup_failed")
    if primary_error is not None:
        raise primary_error

    return {
        "schema_version": EVIDENCE_SCHEMA,
        "status": "verified",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner": {
            "python_version": platform.python_version(),
            "postgres_image": image,
            "docker_runtime_reference": runtime_image,
            "postgres_server_version_num": postgres_version,
        },
        "scope": {
            "tenant_count": 2,
            "table_count": 2,
            "synthetic_data_only": True,
            "production_schema_accepted": False,
            "production_persistence_verified": False,
        },
        "assurances": {
            "runtime_role_non_superuser": "runtime_role|false|false" in observations,
            "runtime_role_no_bypassrls": "runtime_role|false|false" in observations,
            "runtime_role_non_owner": "record_owner|hormuz_owner" in observations,
            "image_digest_verified": True,
            "final_postgres_startup_confirmed": True,
            "row_security_enabled_on_both_tables": (
                "workspace_rls_flags|true|true" in observations
                and "record_rls_flags|true|true" in observations
            ),
            "force_row_security_enabled_on_both_tables": (
                "workspace_rls_flags|true|true" in observations
                and "record_rls_flags|true|true" in observations
            ),
            "missing_context_denied": "missing_context_rows|0" in observations,
            "tenant_a_read_isolated": "tenant_a_cross_read|0" in observations,
            "tenant_b_read_isolated": "tenant_b_visible|record-b" in observations,
            "transaction_local_context_cleared": "reused_after_commit_rows|0" in observations,
            "forced_owner_denied_without_context": (
                "forced_owner_missing_context_rows|0" in observations
            ),
            "cross_tenant_write_denied": True,
            "composite_foreign_key_denied": True,
            "container_network_disabled": True,
            "container_has_no_host_mounts": True,
            "container_has_no_published_ports": True,
            "container_removed": True,
            "ephemeral_credential_absent_from_evidence": True,
            "sql_output_absent_from_evidence": True,
        },
    }


def _write_evidence(value: dict[str, Any], output: str, *, force: bool) -> None:
    serialized = json.dumps(
        value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    if output == "-":
        sys.stdout.write(serialized)
        return
    path = Path(output).expanduser().absolute()
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | (os.O_TRUNC if force else os.O_EXCL)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        raise PostgresRLSFeasibilityError("evidence_open_failed") from None
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise PostgresRLSFeasibilityError("evidence_write_failed") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a synthetic digest-pinned PostgreSQL RLS feasibility proof; "
            "this does not accept a production schema"
        )
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--output", default="-")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if shutil.which("docker") is None:
        print("PostgreSQL RLS feasibility failed: docker_unavailable", file=sys.stderr)
        return 1
    try:
        result = run_feasibility(image=args.image)
        _write_evidence(result, args.output, force=args.force)
    except PostgresRLSFeasibilityError as error:
        print(f"PostgreSQL RLS feasibility failed: {error.code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
