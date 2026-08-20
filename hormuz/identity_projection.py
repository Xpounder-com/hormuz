"""Configuration-seeded PostgreSQL identity projection.

The live gateway only reads these directory tables. Operators apply desired
state with an owner-scoped deployment command before starting new binaries.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Iterator

from .config import GatewayConfig, Identity
from .postgres import (
    DEFAULT_POSTGRES_RUNTIME_ROLE,
    DEFAULT_POSTGRES_SCHEMA,
    PostgresStorageError,
    TenantContext,
    _open_connection,
    tenant_transaction,
    validate_postgres_identifier,
    validate_tenant_id,
)


@dataclass(frozen=True)
class IdentitySyncResult:
    organizations: int
    changed_organizations: int
    changed_principals: int
    revoked_sessions: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "hormuz.identity-sync.v1",
            "organizations": self.organizations,
            "changed_organizations": self.changed_organizations,
            "changed_principals": self.changed_principals,
            "revoked_sessions": self.revoked_sessions,
        }


def configured_organization_ids(config: GatewayConfig) -> tuple[str, ...]:
    values = {
        identity.organization_id
        for identity in (*config.identities_by_token.values(), *config.identities_by_subject.values())
    }
    if not values:
        raise PostgresStorageError("identity_projection_empty")
    for value in values:
        validate_tenant_id(value)
    return tuple(sorted(values))


def identity_projection(config: GatewayConfig, organization_id: str) -> dict[str, object]:
    validate_tenant_id(organization_id)
    identities: dict[str, Identity] = {}
    for identity in (*config.identities_by_token.values(), *config.identities_by_subject.values()):
        if identity.organization_id == organization_id:
            identities.setdefault(identity.actor_id, identity)
    subjects = [
        {
            "issuer": issuer,
            "subject": subject,
            "actor_id": identity.actor_id,
        }
        for (issuer, subject), identity in sorted(config.identities_by_subject.items())
        if identity.organization_id == organization_id
    ]
    principals = [
        {
            "actor_id": identity.actor_id,
            "actor_name": identity.actor_name,
            "team_id": identity.team_id,
            "team_name": identity.team_name,
            "clearance": identity.clearance,
            "allowed_clients": sorted(set(identity.allowed_clients)),
            "capabilities": sorted(set(identity.capabilities)),
            "subjects": [
                {"issuer": item["issuer"], "subject": item["subject"]}
                for item in subjects
                if item["actor_id"] == identity.actor_id
            ],
        }
        for identity in sorted(identities.values(), key=lambda item: item.actor_id)
    ]
    return {
        "organization_id": organization_id,
        "principals": principals,
        "subjects": subjects,
    }


def projection_sha256(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def principal_projection_sha256(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def _owner_tenant_transaction(
    connection: object,
    *,
    schema: str,
    organization_id: str,
) -> Iterator[object]:
    quoted_schema = '"' + schema + '"'
    try:
        with connection.transaction():  # type: ignore[attr-defined]
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT pg_get_userbyid(nspowner), current_user "
                    "FROM pg_namespace WHERE nspname = %s",
                    (schema,),
                )
                owner = cursor.fetchone()
                if not isinstance(owner, (tuple, list)) or len(owner) != 2 or owner[0] != owner[1]:
                    raise PostgresStorageError("identity_sync_role_not_schema_owner")
                cursor.execute(
                    "SELECT set_config('hormuz.tenant_id', %s, true), "
                    "set_config('hormuz.principal_id', 'identity-sync', true), "
                    "set_config('hormuz.client_id', 'hormuz-cli', true), "
                    "set_config('hormuz.authorization_version', '1', true)",
                    (organization_id,),
                )
                if cursor.fetchone() != (
                    organization_id,
                    "identity-sync",
                    "hormuz-cli",
                    "1",
                ):
                    raise PostgresStorageError("identity_sync_scope_not_bound")
                cursor.execute(f"SET LOCAL search_path TO {quoted_schema}, pg_catalog")
            yield connection
    except PostgresStorageError:
        raise
    except Exception:
        raise PostgresStorageError("identity_sync_failed") from None


def sync_identity_projection(
    config: GatewayConfig,
    dsn: str,
    *,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
    connect: object | None = None,
) -> IdentitySyncResult:
    schema = validate_postgres_identifier(schema, "postgres_schema")
    organizations = configured_organization_ids(config)
    connection = _open_connection(dsn, connect)  # type: ignore[arg-type]
    changed_organizations = 0
    changed_principals = 0
    revoked_sessions = 0
    try:
        for organization_id in organizations:
            projection = identity_projection(config, organization_id)
            aggregate_hash = projection_sha256(projection)
            now = datetime.now(timezone.utc)
            with _owner_tenant_transaction(
                connection,
                schema=schema,
                organization_id=organization_id,
            ):
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(
                        "SELECT authorization_version FROM tenants WHERE tenant_id = %s FOR UPDATE",
                        (organization_id,),
                    )
                    tenant = cursor.fetchone()
                    if tenant is None:
                        cursor.execute(
                            "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s)",
                            (organization_id, organization_id),
                        )
                    cursor.execute(
                        "SELECT projection_sha256 FROM gateway_identity_projections "
                        "WHERE tenant_id = %s",
                        (organization_id,),
                    )
                    aggregate = cursor.fetchone()
                    if aggregate is not None and str(aggregate[0]) == aggregate_hash:
                        continue
                    changed_organizations += 1
                    if tenant is not None:
                        cursor.execute(
                            "UPDATE tenants SET authorization_version = authorization_version + 1, "
                            "updated_at = %s WHERE tenant_id = %s",
                            (now, organization_id),
                        )

                    desired_principals = {
                        str(item["actor_id"]): item
                        for item in projection["principals"]  # type: ignore[index]
                    }
                    cursor.execute(
                        "SELECT principal.principal_id, projection.projection_sha256, "
                        "principal.authorization_version, principal.disabled_at "
                        "FROM principals AS principal LEFT JOIN "
                        "gateway_principal_projections AS projection USING (tenant_id, principal_id) "
                        "WHERE principal.tenant_id = %s FOR UPDATE OF principal",
                        (organization_id,),
                    )
                    existing = {
                        str(row[0]): (
                            str(row[1]) if row[1] is not None else None,
                            int(row[2]),
                            row[3],
                        )
                        for row in cursor.fetchall()
                    }
                    affected: set[str] = set()
                    for actor_id, item in desired_principals.items():
                        item_hash = principal_projection_sha256(item)
                        current = existing.get(actor_id)
                        changed = current is None or current[0] != item_hash or current[2] is not None
                        if changed:
                            changed_principals += 1
                            affected.add(actor_id)
                        version = 1 if current is None else current[1] + (1 if changed else 0)
                        cursor.execute(
                            "INSERT INTO teams (tenant_id, team_id, display_name) VALUES (%s, %s, %s) "
                            "ON CONFLICT (tenant_id, team_id) DO UPDATE SET "
                            "display_name = EXCLUDED.display_name, updated_at = %s",
                            (organization_id, item["team_id"], item["team_name"], now),
                        )
                        cursor.execute(
                            "INSERT INTO principals (tenant_id, principal_id, principal_kind, "
                            "display_name, authorization_version) VALUES (%s, %s, 'human', %s, %s) "
                            "ON CONFLICT (tenant_id, principal_id) DO UPDATE SET "
                            "display_name = EXCLUDED.display_name, disabled_at = NULL, "
                            "authorization_version = EXCLUDED.authorization_version, updated_at = %s",
                            (organization_id, actor_id, item["actor_name"], version, now),
                        )
                        cursor.execute(
                            "INSERT INTO gateway_principal_projections ("
                            "tenant_id, principal_id, projection_sha256, actor_name, team_id, "
                            "team_name, clearance, allowed_clients_json, capabilities_json, applied_at"
                            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s) "
                            "ON CONFLICT (tenant_id, principal_id) DO UPDATE SET "
                            "projection_sha256 = EXCLUDED.projection_sha256, "
                            "actor_name = EXCLUDED.actor_name, team_id = EXCLUDED.team_id, "
                            "team_name = EXCLUDED.team_name, clearance = EXCLUDED.clearance, "
                            "allowed_clients_json = EXCLUDED.allowed_clients_json, "
                            "capabilities_json = EXCLUDED.capabilities_json, applied_at = EXCLUDED.applied_at",
                            (
                                organization_id,
                                actor_id,
                                item_hash,
                                item["actor_name"],
                                item["team_id"],
                                item["team_name"],
                                item["clearance"],
                                json.dumps(item["allowed_clients"], separators=(",", ":")),
                                json.dumps(item["capabilities"], separators=(",", ":")),
                                now,
                            ),
                        )

                    removed = set(existing) - set(desired_principals)
                    for actor_id in sorted(removed):
                        changed_principals += 1
                        affected.add(actor_id)
                        cursor.execute(
                            "UPDATE principals SET disabled_at = %s, "
                            "authorization_version = authorization_version + 1, updated_at = %s "
                            "WHERE tenant_id = %s AND principal_id = %s",
                            (now, now, organization_id, actor_id),
                        )

                    cursor.execute(
                        "DELETE FROM external_identities WHERE tenant_id = %s",
                        (organization_id,),
                    )
                    for item in projection["subjects"]:  # type: ignore[index]
                        cursor.execute(
                            "INSERT INTO external_identities "
                            "(tenant_id, issuer, subject, principal_id) VALUES (%s, %s, %s, %s)",
                            (
                                organization_id,
                                item["issuer"],
                                item["subject"],
                                item["actor_id"],
                            ),
                        )

                    cursor.execute(
                        "DELETE FROM team_memberships WHERE tenant_id = %s "
                        "AND membership_id LIKE 'hormuz_config_%%'",
                        (organization_id,),
                    )
                    cursor.execute(
                        "DELETE FROM role_capabilities WHERE tenant_id = %s "
                        "AND role_id LIKE 'hormuz_config_%%'",
                        (organization_id,),
                    )
                    cursor.execute(
                        "DELETE FROM roles WHERE tenant_id = %s AND role_id LIKE 'hormuz_config_%%'",
                        (organization_id,),
                    )
                    for actor_id, item in desired_principals.items():
                        suffix = hashlib.sha256(actor_id.encode("utf-8")).hexdigest()[:24]
                        role_id = "hormuz_config_" + suffix
                        membership_id = "hormuz_config_" + suffix
                        cursor.execute(
                            "INSERT INTO roles (tenant_id, role_id, display_name, built_in) "
                            "VALUES (%s, %s, %s, true)",
                            (organization_id, role_id, "Hormuz configured identity"),
                        )
                        for capability in item["capabilities"]:
                            cursor.execute(
                                "INSERT INTO role_capabilities (tenant_id, role_id, capability) "
                                "VALUES (%s, %s, %s)",
                                (organization_id, role_id, capability),
                            )
                        cursor.execute(
                            "SELECT authorization_version FROM principals "
                            "WHERE tenant_id = %s AND principal_id = %s",
                            (organization_id, actor_id),
                        )
                        version = int(cursor.fetchone()[0])
                        cursor.execute(
                            "INSERT INTO team_memberships (tenant_id, membership_id, principal_id, "
                            "team_id, role_id, effective_from, authorization_version) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (
                                organization_id,
                                membership_id,
                                actor_id,
                                item["team_id"],
                                role_id,
                                now,
                                version,
                            ),
                        )

                    if removed:
                        cursor.execute(
                            "DELETE FROM gateway_principal_projections WHERE tenant_id = %s "
                            "AND principal_id = ANY(%s)",
                            (organization_id, list(removed)),
                        )
                    if affected:
                        cursor.execute(
                            "SELECT id, actor_id, team_id FROM gateway_human_sessions "
                            "WHERE tenant_id = %s AND actor_id = ANY(%s) "
                            "AND revoked_at IS NULL FOR UPDATE",
                            (organization_id, list(affected)),
                        )
                        revoked = cursor.fetchall()
                        for session_id, actor_id, team_id in revoked:
                            cursor.execute(
                                "INSERT INTO gateway_session_security_events ("
                                "tenant_id, id, occurred_at, session_id, event_type, "
                                "target_actor_id, target_team_id) "
                                "VALUES (%s, %s, %s, %s, 'authorization_mapping_removed', %s, %s)",
                                (
                                    organization_id,
                                    "sev_" + uuid.uuid4().hex,
                                    now,
                                    session_id,
                                    actor_id,
                                    team_id,
                                ),
                            )
                        cursor.execute(
                            "UPDATE gateway_human_sessions SET revoked_at = COALESCE(revoked_at, %s) "
                            "WHERE tenant_id = %s AND actor_id = ANY(%s) AND revoked_at IS NULL",
                            (now, organization_id, list(affected)),
                        )
                        revoked_sessions += len(revoked)
                    cursor.execute(
                        "INSERT INTO gateway_identity_projections "
                        "(tenant_id, projection_sha256, applied_at) VALUES (%s, %s, %s) "
                        "ON CONFLICT (tenant_id) DO UPDATE SET "
                        "projection_sha256 = EXCLUDED.projection_sha256, applied_at = EXCLUDED.applied_at",
                        (organization_id, aggregate_hash, now),
                    )
    finally:
        connection.close()
    return IdentitySyncResult(
        organizations=len(organizations),
        changed_organizations=changed_organizations,
        changed_principals=changed_principals,
        revoked_sessions=revoked_sessions,
    )


def verify_runtime_identity_projection(
    config: GatewayConfig,
    dsn: str,
    *,
    schema: str = DEFAULT_POSTGRES_SCHEMA,
    runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
    connect: object | None = None,
) -> None:
    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
    connection = _open_connection(dsn, connect)  # type: ignore[arg-type]
    quoted_schema = '"' + schema + '"'
    try:
        for organization_id in configured_organization_ids(config):
            context = TenantContext(organization_id, "identity-verifier", "hormuz-startup", 1)
            with tenant_transaction(connection, context, runtime_role=runtime_role):
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(f"SET LOCAL search_path TO {quoted_schema}, pg_catalog")
                    cursor.execute(
                        "SELECT projection_sha256 FROM gateway_identity_projections "
                        "WHERE tenant_id = %s",
                        (organization_id,),
                    )
                    row = cursor.fetchone()
                    expected = projection_sha256(identity_projection(config, organization_id))
                    if row is None or str(row[0]) != expected:
                        raise PostgresStorageError("identity_projection_stale")
    finally:
        connection.close()
