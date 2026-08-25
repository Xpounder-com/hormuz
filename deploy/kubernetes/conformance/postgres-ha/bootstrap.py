#!/usr/bin/env python3
"""Create restricted Hormuz roles and apply the bundled schema exactly once."""

from __future__ import annotations

import json
import os

import psycopg
from psycopg import sql

from hormuz.postgres import migrate_postgres


ROLES = (
    ("hormuz_runtime", "HORMUZ_RUNTIME_PASSWORD"),
    ("hormuz_policy_control", "HORMUZ_POLICY_CONTROL_PASSWORD"),
    ("hormuz_custody_control", "HORMUZ_CUSTODY_CONTROL_PASSWORD"),
    ("hormuz_custody_executor", "HORMUZ_CUSTODY_EXECUTOR_PASSWORD"),
)


def main() -> int:
    dsn = _required("HORMUZ_POSTGRES_MIGRATION_DSN")
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                for role, password_env in ROLES:
                    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                    if cursor.fetchone() is not None:
                        raise SystemExit("postgres_role_already_exists")
                    cursor.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN PASSWORD {} "
                            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                        ).format(sql.Identifier(role), sql.Literal(_required(password_env)))
                    )

    applied = migrate_postgres(
        dsn,
        schema="hormuz",
        runtime_role="hormuz_runtime",
        policy_control_role="hormuz_policy_control",
        custody_control_role="hormuz_custody_control",
        custody_executor_role="hormuz_custody_executor",
    )
    print(
        json.dumps(
            {
                "command": "postgres-ha-bootstrap",
                "migration_count": len(applied),
                "restricted_login_roles": len(ROLES),
                "schema": "hormuz",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or len(value) > 4096 or "\x00" in value or "\n" in value or "\r" in value:
        raise SystemExit("postgres_bootstrap_secret_invalid")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
