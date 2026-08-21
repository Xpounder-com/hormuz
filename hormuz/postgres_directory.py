"""Shared PostgreSQL SCIM directory with opaque cross-tenant routing.

The local SQLite directory remains useful for a single process.  This adapter
uses the same SCIM contract but stores tenant records behind PostgreSQL RLS.
The sole global relation contains keyed HMAC tags, never raw OIDC issuer or
subject values; it exists only to discover the tenant before an RLS-scoped
transaction can be established.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import uuid
from typing import Any, Iterator

from .config import (
    Identity,
    ResolvedSCIMGroupAuthorization,
    SCIMGroupAuthorizationError,
)
from .directory import (
    DirectoryAuthorizationResolver,
    HORMUZ_GROUP_EXTENSION,
    HORMUZ_USER_EXTENSION,
    SCIM_GROUP_SCHEMA,
    SCIM_LIST_SCHEMA,
    SCIM_USER_SCHEMA,
    DirectoryError,
    DirectoryMutation,
    _GROUP_STORAGE_SENTINEL,
    _RESOURCE_TYPES,
    _parse_if_match,
    _resource_id,
    _version,
    parse_group,
    parse_user,
    parse_workload,
)
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise DirectoryError("directory_record_corrupt")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, str):
        return value
    raise DirectoryError("directory_record_corrupt")


def _tuple_json(value: object) -> tuple[str, ...]:
    decoded = value
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except (json.JSONDecodeError, RecursionError, ValueError):
            raise DirectoryError("directory_record_corrupt") from None
    if not isinstance(decoded, (list, tuple)) or not all(isinstance(item, str) for item in decoded):
        raise DirectoryError("directory_record_corrupt")
    return tuple(decoded)


def _json_array(value: tuple[str, ...]) -> str:
    return json.dumps(list(value), separators=(",", ":"), ensure_ascii=False)


def _projection_sha256(
    *,
    actor_name: str,
    team_id: str,
    team_name: str,
    clearance: str,
    allowed_clients: tuple[str, ...],
    capabilities: tuple[str, ...],
    identity_type: str,
    issuer: str,
    subject: str,
    authorization_profile_id: str | None = None,
) -> str:
    value = {
        "actor_name": actor_name,
        "team_id": team_id,
        "team_name": team_name,
        "clearance": clearance,
        "allowed_clients": list(allowed_clients),
        "capabilities": list(capabilities),
        "identity_type": identity_type,
        "issuer": issuer,
        "subject": subject,
        "authorization_profile_id": authorization_profile_id,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class PostgresDirectoryStore:
    """SCIM lifecycle records and current identity projection for shared use."""

    backend = "postgresql"

    def __init__(
        self,
        dsn: str,
        *,
        trusted_issuers: tuple[str, ...],
        routing_key: bytes,
        authorization_resolver: DirectoryAuthorizationResolver,
        schema: str = DEFAULT_POSTGRES_SCHEMA,
        runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
        connect: object | None = None,
    ):
        if not isinstance(dsn, str) or not dsn:
            raise DirectoryError("directory_postgres_dsn_unavailable")
        if not trusted_issuers:
            raise DirectoryError("directory_oidc_issuer_set_empty")
        if not isinstance(routing_key, bytes) or len(routing_key) != 32:
            raise DirectoryError("directory_routing_key_invalid")
        if authorization_resolver is None:
            raise DirectoryError("directory_authorization_resolver_missing")
        self._dsn = dsn
        self._trusted_issuers = frozenset(trusted_issuers)
        self._routing_key = routing_key
        self._authorization_resolver = authorization_resolver
        self.schema = validate_postgres_identifier(schema, "postgres_schema")
        self.runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
        self._qualified = '"' + self.schema + '"'
        self._connect = connect

    def _tag(self, purpose: str, *values: str) -> bytes:
        payload = purpose.encode("ascii") + b"\x00" + b"\x00".join(
            value.encode("utf-8") for value in values
        )
        return hmac.new(self._routing_key, payload, hashlib.sha256).digest()

    def _subject_tag(self, issuer: str, subject: str) -> bytes:
        return self._tag("hormuz/directory/subject/v1", issuer, subject)

    def _issuer_tag(self, issuer: str) -> bytes:
        return self._tag("hormuz/directory/issuer/v1", issuer)

    def _check_issuer(self, issuer: str) -> None:
        if issuer not in self._trusted_issuers:
            raise DirectoryError("directory_issuer_untrusted")

    @staticmethod
    def _require_admin(identity: Identity) -> None:
        if "identity_admin" not in identity.capabilities:
            raise DirectoryError("identity_admin_capability_required")

    @staticmethod
    def _require_policy_admin(identity: Identity) -> None:
        if "policy_admin" not in identity.capabilities:
            raise DirectoryError("policy_admin_capability_required")

    @contextmanager
    def _global_transaction(self) -> Iterator[object]:
        connection = _open_connection(self._dsn, self._connect)  # type: ignore[arg-type]
        try:
            with connection.transaction():  # type: ignore[attr-defined]
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(
                        "SELECT current_user, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                    if cursor.fetchone() != (self.runtime_role, False, False):
                        raise PostgresStorageError("directory_runtime_role_invalid")
                    cursor.execute(f"SET LOCAL search_path TO {self._qualified}, pg_catalog")
                yield connection
        except DirectoryError:
            raise
        except PostgresStorageError as error:
            raise DirectoryError("directory_store_unavailable") from error
        except Exception as error:
            if getattr(error, "sqlstate", None) == "23505":
                raise DirectoryError("directory_subject_conflict") from None
            raise DirectoryError("directory_store_unavailable") from None
        finally:
            connection.close()

    @contextmanager
    def _tenant_transaction(
        self,
        organization_id: str,
        *,
        principal_id: str = "directory-resolver",
    ) -> Iterator[object]:
        validate_tenant_id(organization_id)
        connection = _open_connection(self._dsn, self._connect)  # type: ignore[arg-type]
        try:
            context = TenantContext(
                organization_id,
                principal_id,
                "hormuz-directory",
                1,
            )
            with tenant_transaction(
                connection,
                context,
                runtime_role=self.runtime_role,
                schema=self.schema,
            ):
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(f"SET LOCAL search_path TO {self._qualified}, pg_catalog")
                yield connection
        except DirectoryError:
            raise
        except PostgresStorageError as error:
            if error.code == "tenant_inactive":
                raise DirectoryError("directory_identity_inactive") from None
            if error.code == "tenant_uniqueness_denied":
                raise DirectoryError("directory_subject_conflict") from None
            raise DirectoryError("directory_store_unavailable") from error
        except Exception as error:
            if getattr(error, "sqlstate", None) == "23505":
                raise DirectoryError("directory_subject_conflict") from None
            raise DirectoryError("directory_store_unavailable") from None
        finally:
            connection.close()

    @staticmethod
    def _resource_row(
        cursor: object,
        organization_id: str,
        resource_type: str,
        resource_id: str,
        *,
        lock: bool = False,
    ) -> tuple[object, ...]:
        query = (
            "SELECT external_id, active, revision, created_at, updated_at "
            "FROM gateway_directory_resources WHERE tenant_id = %s "
            "AND resource_type = %s AND resource_id = %s"
            + (" FOR UPDATE" if lock else "")
        )
        cursor.execute(query, (organization_id, resource_type, resource_id))  # type: ignore[attr-defined]
        row = cursor.fetchone()  # type: ignore[attr-defined]
        if not isinstance(row, (tuple, list)) or len(row) != 5:
            raise DirectoryError("scim_resource_not_found")
        return tuple(row)

    @staticmethod
    def _member_ids(cursor: object, organization_id: str, group_id: str) -> tuple[str, ...]:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT user_id FROM gateway_directory_group_memberships "
            "WHERE tenant_id = %s AND group_id = %s ORDER BY user_id",
            (organization_id, group_id),
        )
        return tuple(str(row[0]) for row in cursor.fetchall())  # type: ignore[attr-defined]

    def _assert_members_exist(
        self,
        cursor: object,
        organization_id: str,
        members: tuple[str, ...],
    ) -> None:
        for user_id in members:
            self._resource_row(cursor, organization_id, "User", user_id)

    @staticmethod
    def _event(
        cursor: object,
        *,
        organization_id: str,
        decision: Identity,
        action: str,
        resource_type: str,
        resource_id: str,
        target_actor_id: str,
        prior_revision: int | None,
        revision: int,
        occurred_at: datetime,
    ) -> None:
        cursor.execute(  # type: ignore[attr-defined]
            "INSERT INTO gateway_directory_events (tenant_id, id, occurred_at, "
            "decision_actor_id, decision_actor_name, action, resource_type, resource_id, "
            "target_actor_id, prior_revision, revision) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                organization_id,
                "ide_" + uuid.uuid4().hex,
                occurred_at,
                decision.actor_id,
                decision.actor_name,
                action,
                resource_type,
                resource_id,
                target_actor_id,
                prior_revision,
                revision,
            ),
        )

    def _user_resource(
        self, cursor: object, organization_id: str, resource_id: str
    ) -> dict[str, object]:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT resource.external_id, resource.active, resource.revision, resource.created_at, "
            "resource.updated_at, entry.issuer, entry.subject, entry.user_name, entry.display_name "
            "FROM gateway_directory_resources AS resource JOIN gateway_directory_users AS entry "
            "ON entry.tenant_id = resource.tenant_id AND entry.resource_id = resource.resource_id "
            "WHERE resource.tenant_id = %s AND resource.resource_type = 'User' "
            "AND resource.resource_id = %s",
            (organization_id, resource_id),
        )
        row = cursor.fetchone()  # type: ignore[attr-defined]
        if not isinstance(row, (tuple, list)) or len(row) != 9:
            raise DirectoryError("scim_resource_not_found")
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT membership.group_id, groups.display_name "
            "FROM gateway_directory_group_memberships AS membership "
            "JOIN gateway_directory_groups AS groups ON groups.tenant_id = membership.tenant_id "
            "AND groups.resource_id = membership.group_id "
            "WHERE membership.tenant_id = %s AND membership.user_id = %s "
            "ORDER BY membership.group_id",
            (organization_id, resource_id),
        )
        groups = cursor.fetchall()  # type: ignore[attr-defined]
        return {
            "schemas": [SCIM_USER_SCHEMA, HORMUZ_USER_EXTENSION],
            "id": resource_id,
            "externalId": str(row[0]),
            "userName": str(row[7]),
            "displayName": str(row[8]),
            "active": bool(row[1]),
            "groups": [{"value": str(item[0]), "display": str(item[1])} for item in groups],
            HORMUZ_USER_EXTENSION: {"issuer": str(row[5]), "subject": str(row[6])},
            "meta": {
                "resourceType": "User",
                "created": _time(row[3]),
                "lastModified": _time(row[4]),
                "version": _version(int(row[2])),
            },
        }

    def _group_resource(
        self, cursor: object, organization_id: str, resource_id: str
    ) -> dict[str, object]:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT resource.external_id, resource.active, resource.revision, resource.created_at, "
            "resource.updated_at, entry.display_name "
            "FROM gateway_directory_resources AS resource JOIN gateway_directory_groups AS entry "
            "ON entry.tenant_id = resource.tenant_id AND entry.resource_id = resource.resource_id "
            "WHERE resource.tenant_id = %s AND resource.resource_type = 'Group' "
            "AND resource.resource_id = %s",
            (organization_id, resource_id),
        )
        row = cursor.fetchone()  # type: ignore[attr-defined]
        if not isinstance(row, (tuple, list)) or len(row) != 6:
            raise DirectoryError("scim_resource_not_found")
        members = self._member_ids(cursor, organization_id, resource_id)
        return {
            "schemas": [SCIM_GROUP_SCHEMA, HORMUZ_GROUP_EXTENSION],
            "id": resource_id,
            "externalId": str(row[0]),
            "displayName": str(row[5]),
            "members": [{"value": member} for member in members],
            HORMUZ_GROUP_EXTENSION: {
                "active": bool(row[1]),
            },
            "meta": {
                "resourceType": "Group",
                "created": _time(row[3]),
                "lastModified": _time(row[4]),
                "version": _version(int(row[2])),
            },
        }

    def _workload_resource(
        self, cursor: object, organization_id: str, resource_id: str
    ) -> dict[str, object]:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT resource.external_id, resource.active, resource.revision, resource.created_at, "
            "resource.updated_at, entry.issuer, entry.subject, entry.display_name, "
            "entry.identity_type, entry.team_id, entry.team_name, entry.clearance, "
            "entry.allowed_clients_json, entry.capabilities_json "
            "FROM gateway_directory_resources AS resource JOIN gateway_directory_workloads AS entry "
            "ON entry.tenant_id = resource.tenant_id AND entry.resource_id = resource.resource_id "
            "WHERE resource.tenant_id = %s AND resource.resource_type = 'Workload' "
            "AND resource.resource_id = %s",
            (organization_id, resource_id),
        )
        row = cursor.fetchone()  # type: ignore[attr-defined]
        if not isinstance(row, (tuple, list)) or len(row) != 14:
            raise DirectoryError("scim_resource_not_found")
        return {
            "schema": "hormuz.workload-identity.v1",
            "id": resource_id,
            "externalId": str(row[0]),
            "displayName": str(row[7]),
            "identityType": str(row[8]),
            "active": bool(row[1]),
            "issuer": str(row[5]),
            "subject": str(row[6]),
            "teamId": str(row[9]),
            "teamName": str(row[10]),
            "clearance": str(row[11]),
            "allowedClients": list(_tuple_json(row[12])),
            "capabilities": list(_tuple_json(row[13])),
            "meta": {
                "resourceType": "Workload",
                "created": _time(row[3]),
                "lastModified": _time(row[4]),
                "version": _version(int(row[2])),
            },
        }

    def _resource(
        self, cursor: object, organization_id: str, resource_type: str, resource_id: str
    ) -> dict[str, object]:
        if resource_type == "User":
            return self._user_resource(cursor, organization_id, resource_id)
        if resource_type == "Group":
            return self._group_resource(cursor, organization_id, resource_id)
        if resource_type == "Workload":
            return self._workload_resource(cursor, organization_id, resource_id)
        raise DirectoryError("scim_resource_not_found")

    def _route_upsert(
        self,
        cursor: object,
        *,
        issuer: str,
        subject: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT gateway_directory_subject_route_upsert(%s, %s, %s, %s)",
            (
                self._subject_tag(issuer, subject),
                self._issuer_tag(issuer),
                resource_type,
                resource_id,
            ),
        )

    def _route_delete(
        self,
        cursor: object,
        *,
        issuer: str,
        subject: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT gateway_directory_subject_route_delete(%s, %s, %s)",
            (self._subject_tag(issuer, subject), resource_type, resource_id),
        )

    @staticmethod
    def _same_user(resource: dict[str, object], candidate: dict[str, object]) -> bool:
        extension = resource.get(HORMUZ_USER_EXTENSION)
        return (
            resource.get("externalId") == candidate["external_id"]
            and resource.get("userName") == candidate["user_name"]
            and resource.get("displayName") == candidate["display_name"]
            and resource.get("active") is candidate["active"]
            and isinstance(extension, dict)
            and extension.get("issuer") == candidate["issuer"]
            and extension.get("subject") == candidate["subject"]
        )

    @staticmethod
    def _same_group(resource: dict[str, object], candidate: dict[str, object]) -> bool:
        extension = resource.get(HORMUZ_GROUP_EXTENSION)
        members = tuple(
            str(item.get("value"))
            for item in resource.get("members", [])
            if isinstance(item, dict)
        )
        return (
            resource.get("externalId") == candidate["external_id"]
            and resource.get("displayName") == candidate["display_name"]
            and members == tuple(candidate["members"])
            and isinstance(extension, dict)
            and extension.get("active") is candidate["active"]
        )

    @staticmethod
    def _same_workload(resource: dict[str, object], candidate: dict[str, object]) -> bool:
        return (
            resource.get("externalId") == candidate["external_id"]
            and resource.get("displayName") == candidate["display_name"]
            and resource.get("identityType") == candidate["identity_type"]
            and resource.get("active") is candidate["active"]
            and resource.get("issuer") == candidate["issuer"]
            and resource.get("subject") == candidate["subject"]
            and resource.get("teamId") == candidate["team_id"]
            and resource.get("teamName") == candidate["team_name"]
            and resource.get("clearance") == candidate["clearance"]
            and tuple(resource.get("allowedClients", [])) == tuple(candidate["allowed_clients"])
            and tuple(resource.get("capabilities", [])) == tuple(candidate["capabilities"])
        )

    def _user_subject(self, cursor: object, organization_id: str, user_id: str) -> tuple[str, str, str, bool]:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT entry.issuer, entry.subject, entry.display_name, resource.active "
            "FROM gateway_directory_resources AS resource JOIN gateway_directory_users AS entry "
            "ON entry.tenant_id = resource.tenant_id AND entry.resource_id = resource.resource_id "
            "WHERE resource.tenant_id = %s AND resource.resource_type = 'User' "
            "AND resource.resource_id = %s",
            (organization_id, user_id),
        )
        row = cursor.fetchone()  # type: ignore[attr-defined]
        if not isinstance(row, (tuple, list)) or len(row) != 4:
            raise DirectoryError("directory_record_corrupt")
        return str(row[0]), str(row[1]), str(row[2]), bool(row[3])

    def _active_group_external_ids_for_user(
        self, cursor: object, organization_id: str, user_id: str
    ) -> tuple[str, ...]:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT group_resource.active, group_resource.external_id "
            "FROM gateway_directory_group_memberships AS membership "
            "JOIN gateway_directory_resources AS group_resource "
            "ON group_resource.tenant_id = membership.tenant_id "
            "AND group_resource.resource_type = 'Group' "
            "AND group_resource.resource_id = membership.group_id "
            "WHERE membership.tenant_id = %s AND membership.user_id = %s",
            (organization_id, user_id),
        )
        return tuple(
            sorted(
                str(row[1])
                for row in cursor.fetchall()  # type: ignore[attr-defined]
                if isinstance(row, (tuple, list)) and len(row) == 2 and bool(row[0])
            )
        )

    def _human_authorization(
        self,
        cursor: object,
        *,
        organization_id: str,
        user_id: str,
    ) -> ResolvedSCIMGroupAuthorization:
        try:
            return self._authorization_resolver.resolve_scim_group_authorization(
                organization_id,
                self._active_group_external_ids_for_user(
                    cursor,
                    organization_id,
                    user_id,
                ),
            )
        except SCIMGroupAuthorizationError as error:
            raise DirectoryError(error.code) from None
        except Exception:
            # Never fall back to the (legacy) group row fields if policy
            # materialization is unavailable or corrupt.
            raise DirectoryError("directory_policy_unavailable") from None

    @staticmethod
    def _sync_projection(
        cursor: object,
        *,
        principal_id: str,
        active: bool,
        actor_name: str | None = None,
        team_id: str | None = None,
        team_name: str | None = None,
        clearance: str | None = None,
        allowed_clients: tuple[str, ...] | None = None,
        capabilities: tuple[str, ...] | None = None,
        issuer: str | None = None,
        subject: str | None = None,
        projection_sha256: str | None = None,
    ) -> None:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT gateway_directory_principal_sync("
            "%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)",
            (
                principal_id,
                active,
                actor_name,
                team_id,
                team_name,
                clearance,
                _json_array(allowed_clients) if allowed_clients is not None else None,
                _json_array(capabilities) if capabilities is not None else None,
                issuer,
                subject,
                projection_sha256,
            ),
        )
        row = cursor.fetchone()  # type: ignore[attr-defined]
        if not isinstance(row, (tuple, list)) or len(row) != 1 or not isinstance(row[0], bool):
            raise DirectoryError("directory_record_corrupt")

    def _disable_dynamic_principal(
        self,
        cursor: object,
        *,
        actor_id: str,
    ) -> None:
        self._sync_projection(cursor, principal_id=actor_id, active=False)

    def _sync_human_principal(
        self,
        cursor: object,
        *,
        organization_id: str,
        user_id: str,
        now: datetime,
        authorization: ResolvedSCIMGroupAuthorization | None = None,
    ) -> ResolvedSCIMGroupAuthorization | None:
        issuer, subject, display_name, user_active = self._user_subject(
            cursor, organization_id, user_id
        )
        if not user_active:
            self._disable_dynamic_principal(
                cursor, actor_id=user_id
            )
            return None
        try:
            if authorization is None:
                authorization = self._human_authorization(
                    cursor,
                    organization_id=organization_id,
                    user_id=user_id,
                )
        except DirectoryError:
            self._disable_dynamic_principal(
                cursor, actor_id=user_id
            )
            return None
        digest = _projection_sha256(
            actor_name=display_name,
            team_id=authorization.team_id,
            team_name=authorization.team_name,
            clearance=authorization.clearance,
            allowed_clients=authorization.allowed_clients,
            capabilities=authorization.capabilities,
            identity_type="human",
            issuer=issuer,
            subject=subject,
            authorization_profile_id=authorization.policy_id,
        )
        self._sync_projection(
            cursor,
            principal_id=user_id,
            active=True,
            actor_name=display_name,
            team_id=authorization.team_id,
            team_name=authorization.team_name,
            clearance=authorization.clearance,
            allowed_clients=authorization.allowed_clients,
            capabilities=authorization.capabilities,
            issuer=issuer,
            subject=subject,
            projection_sha256=digest,
        )
        return authorization

    def _sync_workload_principal(
        self,
        cursor: object,
        *,
        organization_id: str,
        workload_id: str,
        now: datetime,
    ) -> None:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT resource.active, workload.issuer, workload.subject, workload.display_name, "
            "workload.identity_type, workload.team_id, workload.team_name, workload.clearance, "
            "workload.allowed_clients_json, workload.capabilities_json "
            "FROM gateway_directory_resources AS resource JOIN gateway_directory_workloads AS workload "
            "ON workload.tenant_id = resource.tenant_id AND workload.resource_id = resource.resource_id "
            "WHERE resource.tenant_id = %s AND resource.resource_type = 'Workload' "
            "AND resource.resource_id = %s",
            (organization_id, workload_id),
        )
        row = cursor.fetchone()  # type: ignore[attr-defined]
        if not isinstance(row, (tuple, list)) or len(row) != 10:
            raise DirectoryError("directory_record_corrupt")
        if not bool(row[0]):
            self._disable_dynamic_principal(
                cursor, actor_id=workload_id
            )
            return
        issuer, subject, display_name, identity_type = map(str, row[1:5])
        team_id, team_name, clearance = map(str, row[5:8])
        allowed_clients = _tuple_json(row[8])
        capabilities = _tuple_json(row[9])
        digest = _projection_sha256(
            actor_name=display_name,
            team_id=team_id,
            team_name=team_name,
            clearance=clearance,
            allowed_clients=allowed_clients,
            capabilities=capabilities,
            identity_type=identity_type,
            issuer=issuer,
            subject=subject,
        )
        self._sync_projection(
            cursor,
            principal_id=workload_id,
            active=True,
            actor_name=display_name,
            team_id=team_id,
            team_name=team_name,
            clearance=clearance,
            allowed_clients=allowed_clients,
            capabilities=capabilities,
            issuer=issuer,
            subject=subject,
            projection_sha256=digest,
        )

    def get(
        self, *, organization_id: str, resource_type: str, resource_id: str
    ) -> dict[str, object]:
        if resource_type not in _RESOURCE_TYPES:
            raise DirectoryError("scim_resource_not_found")
        with self._tenant_transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                return self._resource(cursor, organization_id, resource_type, resource_id)

    def list(
        self,
        *,
        organization_id: str,
        resource_type: str,
        start_index: int = 1,
        count: int = 100,
    ) -> dict[str, object]:
        if (
            resource_type not in _RESOURCE_TYPES
            or not 1 <= start_index <= 1_000_000
            or not 1 <= count <= 100
        ):
            raise DirectoryError("scim_invalid_request")
        with self._tenant_transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT COUNT(*) FROM gateway_directory_resources "
                    "WHERE tenant_id = %s AND resource_type = %s",
                    (organization_id, resource_type),
                )
                total = cursor.fetchone()
                cursor.execute(
                    "SELECT resource_id FROM gateway_directory_resources "
                    "WHERE tenant_id = %s AND resource_type = %s ORDER BY resource_id "
                    "LIMIT %s OFFSET %s",
                    (organization_id, resource_type, count, start_index - 1),
                )
                resources = [
                    self._resource(cursor, organization_id, resource_type, str(row[0]))
                    for row in cursor.fetchall()
                ]
        return {
            "schemas": [SCIM_LIST_SCHEMA],
            "totalResults": int(total[0]) if total is not None else 0,
            "startIndex": start_index,
            "itemsPerPage": len(resources),
            "Resources": resources,
        }

    def create_user(self, *, administrator: Identity, value: object) -> DirectoryMutation:
        self._require_admin(administrator)
        candidate = parse_user(value)
        self._check_issuer(str(candidate["issuer"]))
        organization_id = administrator.organization_id
        resource_id = _resource_id("usr", organization_id, str(candidate["external_id"]))
        now = _now()
        with self._tenant_transaction(organization_id, principal_id=administrator.actor_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT resource_id FROM gateway_directory_resources WHERE tenant_id = %s "
                    "AND resource_type = 'User' AND external_id = %s",
                    (organization_id, candidate["external_id"]),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    resource = self._user_resource(cursor, organization_id, str(existing[0]))
                    if not self._same_user(resource, candidate):
                        raise DirectoryError("scim_external_id_conflict")
                    return DirectoryMutation(resource=resource, changed=False)
                cursor.execute(
                    "SELECT 1 FROM principals WHERE tenant_id = %s AND principal_id = %s",
                    (organization_id, resource_id),
                )
                if cursor.fetchone() is not None:
                    raise DirectoryError("directory_principal_conflict")
                cursor.execute(
                    "INSERT INTO gateway_directory_resources (tenant_id, resource_type, resource_id, "
                    "external_id, active, revision, created_at, updated_at) "
                    "VALUES (%s, 'User', %s, %s, %s, 1, %s, %s)",
                    (
                        organization_id,
                        resource_id,
                        candidate["external_id"],
                        bool(candidate["active"]),
                        now,
                        now,
                    ),
                )
                cursor.execute(
                    "INSERT INTO gateway_directory_users (tenant_id, resource_id, issuer, subject, user_name, display_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        organization_id,
                        resource_id,
                        candidate["issuer"],
                        candidate["subject"],
                        candidate["user_name"],
                        candidate["display_name"],
                    ),
                )
                self._route_upsert(
                    cursor,
                    issuer=str(candidate["issuer"]),
                    subject=str(candidate["subject"]),
                    resource_type="User",
                    resource_id=resource_id,
                )
                self._sync_human_principal(
                    cursor, organization_id=organization_id, user_id=resource_id, now=now
                )
                self._event(
                    cursor,
                    organization_id=organization_id,
                    decision=administrator,
                    action="created",
                    resource_type="User",
                    resource_id=resource_id,
                    target_actor_id=resource_id,
                    prior_revision=None,
                    revision=1,
                    occurred_at=now,
                )
                return DirectoryMutation(
                    resource=self._user_resource(cursor, organization_id, resource_id),
                    changed=True,
                    affected_actor_ids=(resource_id,),
                )

    def replace_user(
        self,
        *,
        administrator: Identity,
        resource_id: str,
        value: object,
        if_match: str | None = None,
    ) -> DirectoryMutation:
        self._require_admin(administrator)
        candidate = parse_user(value)
        self._check_issuer(str(candidate["issuer"]))
        organization_id = administrator.organization_id
        now = _now()
        with self._tenant_transaction(organization_id, principal_id=administrator.actor_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                row = self._resource_row(cursor, organization_id, "User", resource_id, lock=True)
                _parse_if_match(if_match, int(row[2]))
                resource = self._user_resource(cursor, organization_id, resource_id)
                if resource.get("externalId") != candidate["external_id"]:
                    raise DirectoryError("scim_external_id_immutable")
                if self._same_user(resource, candidate):
                    return DirectoryMutation(resource=resource, changed=False)
                extension = resource[HORMUZ_USER_EXTENSION]
                assert isinstance(extension, dict)
                old_issuer, old_subject = str(extension["issuer"]), str(extension["subject"])
                revision = int(row[2]) + 1
                cursor.execute(
                    "UPDATE gateway_directory_resources SET active = %s, revision = %s, updated_at = %s "
                    "WHERE tenant_id = %s AND resource_type = 'User' AND resource_id = %s",
                    (bool(candidate["active"]), revision, now, organization_id, resource_id),
                )
                cursor.execute(
                    "UPDATE gateway_directory_users SET issuer = %s, subject = %s, user_name = %s, display_name = %s "
                    "WHERE tenant_id = %s AND resource_id = %s",
                    (
                        candidate["issuer"],
                        candidate["subject"],
                        candidate["user_name"],
                        candidate["display_name"],
                        organization_id,
                        resource_id,
                    ),
                )
                if (old_issuer, old_subject) != (candidate["issuer"], candidate["subject"]):
                    self._route_delete(
                        cursor,
                        issuer=old_issuer,
                        subject=old_subject,
                        resource_type="User",
                        resource_id=resource_id,
                    )
                    self._route_upsert(
                        cursor,
                        issuer=str(candidate["issuer"]),
                        subject=str(candidate["subject"]),
                        resource_type="User",
                        resource_id=resource_id,
                    )
                self._sync_human_principal(
                    cursor, organization_id=organization_id, user_id=resource_id, now=now
                )
                self._event(
                    cursor,
                    organization_id=organization_id,
                    decision=administrator,
                    action="deactivated" if not candidate["active"] else "updated",
                    resource_type="User",
                    resource_id=resource_id,
                    target_actor_id=resource_id,
                    prior_revision=int(row[2]),
                    revision=revision,
                    occurred_at=now,
                )
                return DirectoryMutation(
                    resource=self._user_resource(cursor, organization_id, resource_id),
                    changed=True,
                    affected_actor_ids=(resource_id,),
                )

    def create_group(self, *, administrator: Identity, value: object) -> DirectoryMutation:
        self._require_admin(administrator)
        candidate = parse_group(value)
        organization_id = administrator.organization_id
        resource_id = _resource_id("grp", organization_id, str(candidate["external_id"]))
        now = _now()
        members = tuple(candidate["members"])
        with self._tenant_transaction(organization_id, principal_id=administrator.actor_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                self._assert_members_exist(cursor, organization_id, members)
                cursor.execute(
                    "SELECT resource_id FROM gateway_directory_resources WHERE tenant_id = %s "
                    "AND resource_type = 'Group' AND external_id = %s",
                    (organization_id, candidate["external_id"]),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    resource = self._group_resource(cursor, organization_id, str(existing[0]))
                    if not self._same_group(resource, candidate):
                        raise DirectoryError("scim_external_id_conflict")
                    return DirectoryMutation(resource=resource, changed=False)
                cursor.execute(
                    "INSERT INTO gateway_directory_resources (tenant_id, resource_type, resource_id, "
                    "external_id, active, revision, created_at, updated_at) "
                    "VALUES (%s, 'Group', %s, %s, %s, 1, %s, %s)",
                    (organization_id, resource_id, candidate["external_id"], bool(candidate["active"]), now, now),
                )
                cursor.execute(
                    "INSERT INTO gateway_directory_groups (tenant_id, resource_id, display_name, team_id, "
                    "team_name, clearance, allowed_clients_json, capabilities_json) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                    (
                        organization_id,
                        resource_id,
                        candidate["display_name"],
                        _GROUP_STORAGE_SENTINEL[0],
                        _GROUP_STORAGE_SENTINEL[1],
                        _GROUP_STORAGE_SENTINEL[2],
                        _json_array(_GROUP_STORAGE_SENTINEL[3]),
                        _json_array(_GROUP_STORAGE_SENTINEL[4]),
                    ),
                )
                for member in members:
                    cursor.execute(
                        "INSERT INTO gateway_directory_group_memberships "
                        "(tenant_id, group_id, user_id, created_at) VALUES (%s, %s, %s, %s)",
                        (organization_id, resource_id, member, now),
                    )
                for member in members:
                    self._sync_human_principal(
                        cursor, organization_id=organization_id, user_id=member, now=now
                    )
                self._event(
                    cursor,
                    organization_id=organization_id,
                    decision=administrator,
                    action="created",
                    resource_type="Group",
                    resource_id=resource_id,
                    target_actor_id="-",
                    prior_revision=None,
                    revision=1,
                    occurred_at=now,
                )
                return DirectoryMutation(
                    resource=self._group_resource(cursor, organization_id, resource_id),
                    changed=True,
                    affected_actor_ids=tuple(sorted(set(members))),
                )

    def replace_group(
        self,
        *,
        administrator: Identity,
        resource_id: str,
        value: object,
        if_match: str | None = None,
    ) -> DirectoryMutation:
        self._require_admin(administrator)
        candidate = parse_group(value)
        organization_id = administrator.organization_id
        now = _now()
        new_members = tuple(candidate["members"])
        with self._tenant_transaction(organization_id, principal_id=administrator.actor_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                row = self._resource_row(cursor, organization_id, "Group", resource_id, lock=True)
                _parse_if_match(if_match, int(row[2]))
                self._assert_members_exist(cursor, organization_id, new_members)
                resource = self._group_resource(cursor, organization_id, resource_id)
                if resource.get("externalId") != candidate["external_id"]:
                    raise DirectoryError("scim_external_id_immutable")
                if self._same_group(resource, candidate):
                    return DirectoryMutation(resource=resource, changed=False)
                old_members = self._member_ids(cursor, organization_id, resource_id)
                revision = int(row[2]) + 1
                cursor.execute(
                    "UPDATE gateway_directory_resources SET active = %s, revision = %s, updated_at = %s "
                    "WHERE tenant_id = %s AND resource_type = 'Group' AND resource_id = %s",
                    (bool(candidate["active"]), revision, now, organization_id, resource_id),
                )
                cursor.execute(
                    "UPDATE gateway_directory_groups SET display_name = %s, team_id = %s, team_name = %s, "
                    "clearance = %s, allowed_clients_json = %s::jsonb, capabilities_json = %s::jsonb "
                    "WHERE tenant_id = %s AND resource_id = %s",
                    (
                        candidate["display_name"],
                        _GROUP_STORAGE_SENTINEL[0],
                        _GROUP_STORAGE_SENTINEL[1],
                        _GROUP_STORAGE_SENTINEL[2],
                        _json_array(_GROUP_STORAGE_SENTINEL[3]),
                        _json_array(_GROUP_STORAGE_SENTINEL[4]),
                        organization_id,
                        resource_id,
                    ),
                )
                cursor.execute(
                    "DELETE FROM gateway_directory_group_memberships "
                    "WHERE tenant_id = %s AND group_id = %s",
                    (organization_id, resource_id),
                )
                for member in new_members:
                    cursor.execute(
                        "INSERT INTO gateway_directory_group_memberships "
                        "(tenant_id, group_id, user_id, created_at) VALUES (%s, %s, %s, %s)",
                        (organization_id, resource_id, member, now),
                    )
                affected = tuple(sorted(set(old_members + new_members)))
                for member in affected:
                    self._sync_human_principal(
                        cursor, organization_id=organization_id, user_id=member, now=now
                    )
                self._event(
                    cursor,
                    organization_id=organization_id,
                    decision=administrator,
                    action="deactivated" if not candidate["active"] else "updated",
                    resource_type="Group",
                    resource_id=resource_id,
                    target_actor_id="-",
                    prior_revision=int(row[2]),
                    revision=revision,
                    occurred_at=now,
                )
                return DirectoryMutation(
                    resource=self._group_resource(cursor, organization_id, resource_id),
                    changed=True,
                    affected_actor_ids=affected,
                )

    def create_workload(self, *, administrator: Identity, value: object) -> DirectoryMutation:
        self._require_policy_admin(administrator)
        candidate = parse_workload(value)
        self._check_issuer(str(candidate["issuer"]))
        organization_id = administrator.organization_id
        resource_id = _resource_id("wrk", organization_id, str(candidate["external_id"]))
        now = _now()
        with self._tenant_transaction(organization_id, principal_id=administrator.actor_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT resource_id FROM gateway_directory_resources WHERE tenant_id = %s "
                    "AND resource_type = 'Workload' AND external_id = %s",
                    (organization_id, candidate["external_id"]),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    resource = self._workload_resource(cursor, organization_id, str(existing[0]))
                    if not self._same_workload(resource, candidate):
                        raise DirectoryError("scim_external_id_conflict")
                    return DirectoryMutation(resource=resource, changed=False)
                cursor.execute(
                    "SELECT 1 FROM principals WHERE tenant_id = %s AND principal_id = %s",
                    (organization_id, resource_id),
                )
                if cursor.fetchone() is not None:
                    raise DirectoryError("directory_principal_conflict")
                cursor.execute(
                    "INSERT INTO gateway_directory_resources (tenant_id, resource_type, resource_id, "
                    "external_id, active, revision, created_at, updated_at) "
                    "VALUES (%s, 'Workload', %s, %s, %s, 1, %s, %s)",
                    (organization_id, resource_id, candidate["external_id"], bool(candidate["active"]), now, now),
                )
                cursor.execute(
                    "INSERT INTO gateway_directory_workloads (tenant_id, resource_id, issuer, subject, "
                    "display_name, identity_type, team_id, team_name, clearance, allowed_clients_json, "
                    "capabilities_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                    (
                        organization_id,
                        resource_id,
                        candidate["issuer"],
                        candidate["subject"],
                        candidate["display_name"],
                        candidate["identity_type"],
                        candidate["team_id"],
                        candidate["team_name"],
                        candidate["clearance"],
                        _json_array(tuple(candidate["allowed_clients"])),
                        _json_array(tuple(candidate["capabilities"])),
                    ),
                )
                self._route_upsert(
                    cursor,
                    issuer=str(candidate["issuer"]),
                    subject=str(candidate["subject"]),
                    resource_type="Workload",
                    resource_id=resource_id,
                )
                self._sync_workload_principal(
                    cursor, organization_id=organization_id, workload_id=resource_id, now=now
                )
                self._event(
                    cursor,
                    organization_id=organization_id,
                    decision=administrator,
                    action="created",
                    resource_type="Workload",
                    resource_id=resource_id,
                    target_actor_id=resource_id,
                    prior_revision=None,
                    revision=1,
                    occurred_at=now,
                )
                return DirectoryMutation(
                    resource=self._workload_resource(cursor, organization_id, resource_id),
                    changed=True,
                    affected_actor_ids=(resource_id,),
                )

    def replace_workload(
        self,
        *,
        administrator: Identity,
        resource_id: str,
        value: object,
        if_match: str | None = None,
    ) -> DirectoryMutation:
        self._require_policy_admin(administrator)
        candidate = parse_workload(value)
        self._check_issuer(str(candidate["issuer"]))
        organization_id = administrator.organization_id
        now = _now()
        with self._tenant_transaction(organization_id, principal_id=administrator.actor_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                row = self._resource_row(cursor, organization_id, "Workload", resource_id, lock=True)
                _parse_if_match(if_match, int(row[2]))
                resource = self._workload_resource(cursor, organization_id, resource_id)
                if resource.get("externalId") != candidate["external_id"]:
                    raise DirectoryError("scim_external_id_immutable")
                if self._same_workload(resource, candidate):
                    return DirectoryMutation(resource=resource, changed=False)
                old_issuer, old_subject = str(resource["issuer"]), str(resource["subject"])
                revision = int(row[2]) + 1
                cursor.execute(
                    "UPDATE gateway_directory_resources SET active = %s, revision = %s, updated_at = %s "
                    "WHERE tenant_id = %s AND resource_type = 'Workload' AND resource_id = %s",
                    (bool(candidate["active"]), revision, now, organization_id, resource_id),
                )
                cursor.execute(
                    "UPDATE gateway_directory_workloads SET issuer = %s, subject = %s, display_name = %s, "
                    "identity_type = %s, team_id = %s, team_name = %s, clearance = %s, "
                    "allowed_clients_json = %s::jsonb, capabilities_json = %s::jsonb "
                    "WHERE tenant_id = %s AND resource_id = %s",
                    (
                        candidate["issuer"],
                        candidate["subject"],
                        candidate["display_name"],
                        candidate["identity_type"],
                        candidate["team_id"],
                        candidate["team_name"],
                        candidate["clearance"],
                        _json_array(tuple(candidate["allowed_clients"])),
                        _json_array(tuple(candidate["capabilities"])),
                        organization_id,
                        resource_id,
                    ),
                )
                if (old_issuer, old_subject) != (candidate["issuer"], candidate["subject"]):
                    self._route_delete(
                        cursor,
                        issuer=old_issuer,
                        subject=old_subject,
                        resource_type="Workload",
                        resource_id=resource_id,
                    )
                    self._route_upsert(
                        cursor,
                        issuer=str(candidate["issuer"]),
                        subject=str(candidate["subject"]),
                        resource_type="Workload",
                        resource_id=resource_id,
                    )
                self._sync_workload_principal(
                    cursor, organization_id=organization_id, workload_id=resource_id, now=now
                )
                self._event(
                    cursor,
                    organization_id=organization_id,
                    decision=administrator,
                    action="deactivated" if not candidate["active"] else "updated",
                    resource_type="Workload",
                    resource_id=resource_id,
                    target_actor_id=resource_id,
                    prior_revision=int(row[2]),
                    revision=revision,
                    occurred_at=now,
                )
                return DirectoryMutation(
                    resource=self._workload_resource(cursor, organization_id, resource_id),
                    changed=True,
                    affected_actor_ids=(resource_id,),
                )

    def deactivate(
        self,
        *,
        administrator: Identity,
        resource_type: str,
        resource_id: str,
        if_match: str | None = None,
    ) -> DirectoryMutation:
        if resource_type not in _RESOURCE_TYPES:
            raise DirectoryError("scim_resource_not_found")
        if resource_type == "Workload":
            self._require_policy_admin(administrator)
        else:
            self._require_admin(administrator)
        organization_id = administrator.organization_id
        now = _now()
        with self._tenant_transaction(organization_id, principal_id=administrator.actor_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                row = self._resource_row(cursor, organization_id, resource_type, resource_id, lock=True)
                _parse_if_match(if_match, int(row[2]))
                resource = self._resource(cursor, organization_id, resource_type, resource_id)
                if not bool(row[1]):
                    return DirectoryMutation(resource=resource, changed=False)
                revision = int(row[2]) + 1
                cursor.execute(
                    "UPDATE gateway_directory_resources SET active = false, revision = %s, updated_at = %s "
                    "WHERE tenant_id = %s AND resource_type = %s AND resource_id = %s",
                    (revision, now, organization_id, resource_type, resource_id),
                )
                if resource_type == "Group":
                    affected = self._member_ids(cursor, organization_id, resource_id)
                    for user_id in affected:
                        self._sync_human_principal(
                            cursor, organization_id=organization_id, user_id=user_id, now=now
                        )
                    target = "-"
                elif resource_type == "User":
                    affected = (resource_id,)
                    self._sync_human_principal(
                        cursor, organization_id=organization_id, user_id=resource_id, now=now
                    )
                    target = resource_id
                else:
                    affected = (resource_id,)
                    self._sync_workload_principal(
                        cursor, organization_id=organization_id, workload_id=resource_id, now=now
                    )
                    target = resource_id
                self._event(
                    cursor,
                    organization_id=organization_id,
                    decision=administrator,
                    action="deactivated",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    target_actor_id=target,
                    prior_revision=int(row[2]),
                    revision=revision,
                    occurred_at=now,
                )
                return DirectoryMutation(
                    resource=self._resource(cursor, organization_id, resource_type, resource_id),
                    changed=True,
                    affected_actor_ids=tuple(sorted(set(affected))),
                )

    def _route_lookup(self, issuer: str, subject: str) -> tuple[str, str, str] | None:
        with self._global_transaction() as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT tenant_id, resource_type, resource_id "
                    "FROM gateway_directory_subject_route_lookup(%s)",
                    (self._subject_tag(issuer, subject),),
                )
                rows = cursor.fetchall()
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], (tuple, list)) or len(rows[0]) != 3:
            raise DirectoryError("directory_subject_ambiguous")
        return str(rows[0][0]), str(rows[0][1]), str(rows[0][2])

    def identity_for_subject(self, issuer: str, subject: str) -> Identity | None:
        route = self._route_lookup(issuer, subject)
        if route is None:
            return None
        organization_id, resource_type, resource_id = route
        with self._tenant_transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                if resource_type == "Workload":
                    resource = self._workload_resource(cursor, organization_id, resource_id)
                    if not bool(resource["active"]):
                        raise DirectoryError("directory_identity_inactive")
                    return Identity(
                        token_env="",
                        token="",
                        actor_id=resource_id,
                        actor_name=str(resource["displayName"]),
                        team_id=str(resource["teamId"]),
                        team_name=str(resource["teamName"]),
                        allowed_clients=tuple(str(item) for item in resource["allowedClients"]),
                        capabilities=tuple(str(item) for item in resource["capabilities"]),
                        organization_id=organization_id,
                        clearance=str(resource["clearance"]),
                        authentication_source=f"directory:{issuer}",
                        identity_type=str(resource["identityType"]),
                    )
                if resource_type != "User":
                    raise DirectoryError("directory_record_corrupt")
                resource = self._user_resource(cursor, organization_id, resource_id)
                if not bool(resource["active"]):
                    raise DirectoryError("directory_identity_inactive")
                extension = resource.get(HORMUZ_USER_EXTENSION)
                if not isinstance(extension, dict) or extension.get("issuer") != issuer or extension.get("subject") != subject:
                    raise DirectoryError("directory_record_corrupt")
                try:
                    authorization = self._human_authorization(
                        cursor,
                        organization_id=organization_id,
                        user_id=resource_id,
                    )
                except DirectoryError:
                    self._disable_dynamic_principal(cursor, actor_id=resource_id)
                    raise
                synchronized = self._sync_human_principal(
                    cursor,
                    organization_id=organization_id,
                    user_id=resource_id,
                    now=_now(),
                    authorization=authorization,
                )
                if synchronized is None:
                    raise DirectoryError("directory_policy_unavailable")
                return Identity(
                    token_env="",
                    token="",
                    actor_id=resource_id,
                    actor_name=str(resource["displayName"]),
                    team_id=authorization.team_id,
                    team_name=authorization.team_name,
                    allowed_clients=authorization.allowed_clients,
                    capabilities=authorization.capabilities,
                    organization_id=organization_id,
                    clearance=authorization.clearance,
                    authentication_source=f"directory:{issuer}",
                    identity_type="human",
                    authorization_profile_id=authorization.policy_id,
                )

    def organizations_for_issuer(self, issuer: str) -> tuple[str, ...]:
        with self._global_transaction() as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT tenant_id FROM gateway_directory_issuer_route_lookup(%s)",
                    (self._issuer_tag(issuer),),
                )
                rows = cursor.fetchall()
        return tuple(sorted({str(row[0]) for row in rows if isinstance(row, (tuple, list)) and row}))
