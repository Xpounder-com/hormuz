"""Tenant-scoped immutable policy versions and atomic activation state."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import uuid
from typing import Iterator

from .config import GatewayConfig, Identity
from .policy_projection import policy_projection, policy_projection_sha256
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


_VERSION_ID = re.compile(r"hpv_v1_[0-9a-f]{64}\Z")
_SUMMARY_SCHEMA = "hormuz.policy-change-summary.v1"
_PROJECTION_SECTIONS = (
    "model_routes",
    "organization_policy",
    "team_policies",
    "actor_policies",
    "secret_controls",
    "dlp_controls",
    "team_dlp_overlays",
    "actor_dlp_overlays",
)


class PolicyAdminError(PostgresStorageError):
    """Content-free policy administration failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PolicyVersion:
    version_id: str
    projection_sha256: str
    projection_schema: str
    created_at: str
    created_by_actor_id: str
    created_by_actor_name: str
    changed_sections: tuple[str, ...]
    staged: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "hormuz.policy-version.v1",
            "version_id": self.version_id,
            "projection_sha256": self.projection_sha256,
            "projection_schema": self.projection_schema,
            "created_at": self.created_at,
            "created_by_actor_id": self.created_by_actor_id,
            "created_by_actor_name": self.created_by_actor_name,
            "change_summary": {
                "schema": _SUMMARY_SCHEMA,
                "changed_sections": list(self.changed_sections),
                "section_count": len(self.changed_sections),
            },
            "staged": self.staged,
        }


@dataclass(frozen=True)
class PolicyActivation:
    version_id: str
    prior_version_id: str | None
    activated_at: str
    activated_by_actor_id: str
    activated_by_actor_name: str
    activation_sequence: int
    action: str
    changed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "hormuz.policy-activation.v1",
            "version_id": self.version_id,
            "prior_version_id": self.prior_version_id,
            "activated_at": self.activated_at,
            "activated_by_actor_id": self.activated_by_actor_id,
            "activated_by_actor_name": self.activated_by_actor_name,
            "activation_sequence": self.activation_sequence,
            "action": self.action,
            "changed": self.changed,
        }


@dataclass(frozen=True)
class ActivePolicy:
    version_id: str
    projection_sha256: str
    projection: dict[str, object]
    activated_at: str
    activation_sequence: int


def _iso(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PolicyAdminError("policy_history_corrupt")
    return value.astimezone(timezone.utc).isoformat()


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, RecursionError, ValueError):
            raise PolicyAdminError("policy_history_corrupt") from None
        if isinstance(decoded, dict) and all(
            isinstance(key, str) for key in decoded
        ):
            return decoded
    raise PolicyAdminError("policy_history_corrupt")


def _summary(
    prior: dict[str, object] | None,
    candidate: dict[str, object],
) -> dict[str, object]:
    changed = [
        section
        for section in _PROJECTION_SECTIONS
        if prior is None or prior.get(section) != candidate.get(section)
    ]
    return {
        "schema": _SUMMARY_SCHEMA,
        "changed_sections": changed,
        "section_count": len(changed),
    }


def _summary_sections(value: object) -> tuple[str, ...]:
    summary = _json_object(value)
    if (
        set(summary) != {"schema", "changed_sections", "section_count"}
        or summary.get("schema") != _SUMMARY_SCHEMA
    ):
        raise PolicyAdminError("policy_history_corrupt")
    sections = summary.get("changed_sections")
    if (
        not isinstance(sections, list)
        or any(not isinstance(item, str) or item not in _PROJECTION_SECTIONS for item in sections)
        or sections != sorted(set(sections), key=_PROJECTION_SECTIONS.index)
        or summary.get("section_count") != len(sections)
    ):
        raise PolicyAdminError("policy_history_corrupt")
    return tuple(sections)


class PostgresPolicyStore:
    """Immutable policy history with one transactionally active version per tenant."""

    def __init__(
        self,
        dsn: str,
        *,
        organization_ids: tuple[str, ...],
        schema: str = DEFAULT_POSTGRES_SCHEMA,
        runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
        connect: object | None = None,
    ):
        if not isinstance(dsn, str) or not dsn:
            raise PolicyAdminError("postgres_dsn_unavailable")
        normalized = tuple(sorted(set(organization_ids)))
        if not normalized:
            raise PolicyAdminError("policy_admin_tenant_set_empty")
        for organization_id in normalized:
            validate_tenant_id(organization_id)
        self._dsn = dsn
        self.organization_ids = normalized
        self.schema = validate_postgres_identifier(schema, "postgres_schema")
        self.runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
        self._connect = connect
        self._qualified = '"' + self.schema + '"'

    def _organization(self, organization_id: str) -> str:
        validate_tenant_id(organization_id)
        if organization_id not in self.organization_ids:
            raise PolicyAdminError("policy_admin_tenant_not_configured")
        return organization_id

    @staticmethod
    def _require_admin(identity: Identity) -> None:
        if "policy_admin" not in identity.capabilities:
            raise PolicyAdminError("policy_admin_capability_required")

    @contextmanager
    def _transaction(self, identity: Identity) -> Iterator[object]:
        organization_id = self._organization(identity.organization_id)
        connection = None
        try:
            connection = _open_connection(self._dsn, self._connect)  # type: ignore[arg-type]
            with tenant_transaction(
                connection,
                TenantContext(organization_id, identity.actor_id, "hormuz-policy-admin", 1),
                runtime_role=self.runtime_role,
            ):
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(f"SET LOCAL search_path TO {self._qualified}, pg_catalog")
                yield connection
        except PolicyAdminError:
            raise
        except PostgresStorageError as error:
            raise PolicyAdminError(error.code) from None
        except Exception:
            raise PolicyAdminError("policy_admin_store_unavailable") from None
        finally:
            if connection is not None:
                connection.close()

    def stage(self, *, identity: Identity, config: GatewayConfig) -> PolicyVersion:
        """Store one validated, secret-free projection without activating it."""

        self._require_admin(identity)
        organization_id = self._organization(identity.organization_id)
        try:
            projection = policy_projection(config, organization_id)
        except PostgresStorageError as error:
            raise PolicyAdminError(error.code) from None
        fingerprint = policy_projection_sha256(projection)
        version_id = "hpv_v1_" + fingerprint
        serialized = json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        now = datetime.now(timezone.utc)
        with self._transaction(identity) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT v.projection_json FROM gateway_active_policies a "
                    "JOIN gateway_policy_versions v "
                    "ON v.tenant_id = a.tenant_id AND v.version_id = a.version_id "
                    "WHERE a.tenant_id = %s",
                    (organization_id,),
                )
                active_row = cursor.fetchone()
                prior = _json_object(active_row[0]) if active_row is not None else None
                summary = _summary(prior, projection)
                summary_json = json.dumps(summary, sort_keys=True, separators=(",", ":"))
                cursor.execute(
                    "INSERT INTO gateway_policy_versions "
                    "(tenant_id, version_id, projection_sha256, projection_schema, "
                    "projection_json, created_at, created_by_actor_id, "
                    "created_by_actor_name, change_summary_json) "
                    "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb) "
                    "ON CONFLICT (tenant_id, version_id) DO NOTHING "
                    "RETURNING version_id",
                    (
                        organization_id,
                        version_id,
                        fingerprint,
                        "hormuz.policy-projection.v2",
                        serialized,
                        now,
                        identity.actor_id,
                        identity.actor_name,
                        summary_json,
                    ),
                )
                inserted = cursor.fetchone() is not None
                if inserted:
                    cursor.execute(
                        "INSERT INTO gateway_policy_events "
                        "(tenant_id, id, occurred_at, decision_actor_id, "
                        "decision_actor_name, action, version_id, prior_version_id, "
                        "change_summary_json, activation_sequence) "
                        "VALUES (%s, %s, %s, %s, %s, 'staged', %s, NULL, %s::jsonb, NULL)",
                        (
                            organization_id,
                            "hpe_" + uuid.uuid4().hex,
                            now,
                            identity.actor_id,
                            identity.actor_name,
                            version_id,
                            summary_json,
                        ),
                    )
                    created_at = now
                    created_by_actor_id = identity.actor_id
                    created_by_actor_name = identity.actor_name
                    changed_sections = tuple(summary["changed_sections"])
                else:
                    cursor.execute(
                        "SELECT projection_sha256, projection_schema, projection_json, "
                        "created_at, created_by_actor_id, created_by_actor_name, "
                        "change_summary_json FROM gateway_policy_versions "
                        "WHERE tenant_id = %s AND version_id = %s",
                        (organization_id, version_id),
                    )
                    existing = cursor.fetchone()
                    if existing is None:
                        raise PolicyAdminError("policy_history_corrupt")
                    existing_projection = _json_object(existing[2])
                    if (
                        str(existing[0]) != fingerprint
                        or str(existing[1]) != "hormuz.policy-projection.v2"
                        or existing_projection != projection
                        or policy_projection_sha256(existing_projection) != fingerprint
                    ):
                        raise PolicyAdminError("policy_history_corrupt")
                    created_at = existing[3]
                    created_by_actor_id = str(existing[4])
                    created_by_actor_name = str(existing[5])
                    changed_sections = _summary_sections(existing[6])
        return PolicyVersion(
            version_id=version_id,
            projection_sha256=fingerprint,
            projection_schema="hormuz.policy-projection.v2",
            created_at=_iso(created_at),
            created_by_actor_id=created_by_actor_id,
            created_by_actor_name=created_by_actor_name,
            changed_sections=changed_sections,
            staged=inserted,
        )

    def activate(
        self,
        *,
        identity: Identity,
        version_id: str,
        expected_active_version_id: str | None,
        rollback: bool = False,
    ) -> PolicyActivation:
        """Atomically change the tenant pointer using an explicit compare value."""

        self._require_admin(identity)
        organization_id = self._organization(identity.organization_id)
        if _VERSION_ID.fullmatch(version_id) is None:
            raise PolicyAdminError("policy_version_id_invalid")
        if (
            expected_active_version_id is not None
            and _VERSION_ID.fullmatch(expected_active_version_id) is None
        ):
            raise PolicyAdminError("policy_expected_version_id_invalid")
        now = datetime.now(timezone.utc)
        with self._transaction(identity) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT change_summary_json, projection_json "
                    "FROM gateway_policy_versions "
                    "WHERE tenant_id = %s AND version_id = %s",
                    (organization_id, version_id),
                )
                version_row = cursor.fetchone()
                if version_row is None:
                    raise PolicyAdminError("policy_version_not_found")
                _summary_sections(version_row[0])
                target_projection = _json_object(version_row[1])
                cursor.execute(
                    "SELECT a.version_id, a.activated_at, a.activated_by_actor_id, "
                    "a.activated_by_actor_name, a.activation_sequence, v.projection_json "
                    "FROM gateway_active_policies a "
                    "JOIN gateway_policy_versions v "
                    "ON v.tenant_id = a.tenant_id AND v.version_id = a.version_id "
                    "WHERE a.tenant_id = %s FOR UPDATE OF a",
                    (organization_id,),
                )
                active_row = cursor.fetchone()
                current_id = str(active_row[0]) if active_row is not None else None
                if current_id != expected_active_version_id:
                    raise PolicyAdminError("policy_activation_conflict")
                if current_id == version_id:
                    return PolicyActivation(
                        version_id=version_id,
                        prior_version_id=current_id,
                        activated_at=_iso(active_row[1]),
                        activated_by_actor_id=str(active_row[2]),
                        activated_by_actor_name=str(active_row[3]),
                        activation_sequence=int(active_row[4]),
                        action="rolled_back" if rollback else "activated",
                        changed=False,
                    )
                current_projection = (
                    _json_object(active_row[5]) if active_row is not None else None
                )
                summary = _summary(current_projection, target_projection)
                if rollback:
                    cursor.execute(
                        "SELECT 1 FROM gateway_policy_events "
                        "WHERE tenant_id = %s AND version_id = %s "
                        "AND action IN ('activated', 'rolled_back') LIMIT 1",
                        (organization_id, version_id),
                    )
                    if cursor.fetchone() is None:
                        raise PolicyAdminError("policy_rollback_target_not_previously_active")
                sequence = int(active_row[4]) + 1 if active_row is not None else 1
                cursor.execute(
                    "INSERT INTO gateway_active_policies "
                    "(tenant_id, version_id, activated_at, activated_by_actor_id, "
                    "activated_by_actor_name, activation_sequence) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET "
                    "version_id = EXCLUDED.version_id, "
                    "activated_at = EXCLUDED.activated_at, "
                    "activated_by_actor_id = EXCLUDED.activated_by_actor_id, "
                    "activated_by_actor_name = EXCLUDED.activated_by_actor_name, "
                    "activation_sequence = EXCLUDED.activation_sequence",
                    (
                        organization_id,
                        version_id,
                        now,
                        identity.actor_id,
                        identity.actor_name,
                        sequence,
                    ),
                )
                action = "rolled_back" if rollback else "activated"
                cursor.execute(
                    "INSERT INTO gateway_policy_events "
                    "(tenant_id, id, occurred_at, decision_actor_id, "
                    "decision_actor_name, action, version_id, prior_version_id, "
                    "change_summary_json, activation_sequence) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                    (
                        organization_id,
                        "hpe_" + uuid.uuid4().hex,
                        now,
                        identity.actor_id,
                        identity.actor_name,
                        action,
                        version_id,
                        current_id,
                        json.dumps(summary, sort_keys=True, separators=(",", ":")),
                        sequence,
                    ),
                )
        return PolicyActivation(
            version_id=version_id,
            prior_version_id=current_id,
            activated_at=_iso(now),
            activated_by_actor_id=identity.actor_id,
            activated_by_actor_name=identity.actor_name,
            activation_sequence=sequence,
            action=action,
            changed=True,
        )

    def active(self, *, identity: Identity) -> ActivePolicy | None:
        """Read the exact active snapshot through the caller's tenant scope."""

        organization_id = self._organization(identity.organization_id)
        with self._transaction(identity) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT a.version_id, v.projection_sha256, v.projection_json, "
                    "a.activated_at, a.activation_sequence "
                    "FROM gateway_active_policies a "
                    "JOIN gateway_policy_versions v "
                    "ON v.tenant_id = a.tenant_id AND v.version_id = a.version_id "
                    "WHERE a.tenant_id = %s",
                    (organization_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        projection = _json_object(row[2])
        if (
            projection.get("schema") != "hormuz.policy-projection.v2"
            or projection.get("organization_id") != organization_id
            or policy_projection_sha256(projection) != str(row[1])
            or version_id_from_sha256(str(row[1])) != str(row[0])
        ):
            raise PolicyAdminError("policy_history_corrupt")
        return ActivePolicy(
            version_id=str(row[0]),
            projection_sha256=str(row[1]),
            projection=projection,
            activated_at=_iso(row[3]),
            activation_sequence=int(row[4]),
        )


def version_id_from_sha256(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PolicyAdminError("policy_projection_sha256_invalid")
    return "hpv_v1_" + value
