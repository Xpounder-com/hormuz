"""Content-free, local SCIM lifecycle directory for Hormuz.

The directory is deliberately narrow: it retains only the identity attributes
needed to authenticate a person or federated workload, apply policy, and
produce usage metadata.  It never stores prompts, responses, source code,
provider credentials, or raw SCIM request bodies.

This first implementation is SQLite-backed and single-node.  Its API and data
model are kept separate from the HTTP handler so a shared PostgreSQL directory
can replace the persistence layer without changing the SCIM contract.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from typing import Any, Iterator, Protocol

from .config import (
    ConfigError,
    Identity,
    ResolvedSCIMGroupAuthorization,
    SCIMGroupAuthorizationError,
    _identity_capabilities,
)


SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
HORMUZ_USER_EXTENSION = "urn:hormuz:params:scim:schemas:extension:identity:2.0:User"
# SCIM groups supply only lifecycle membership. Their policy mapping belongs to
# the tenant policy projection, never to an IdP-controlled group payload.
HORMUZ_GROUP_EXTENSION = "urn:hormuz:params:scim:schemas:extension:directory:3.0:Group"
LEGACY_HORMUZ_GROUP_POLICY_EXTENSION = (
    "urn:hormuz:params:scim:schemas:extension:policy:2.0:Group"
)

_IDENTITY_TYPES = {"human", "service_account", "ci", "connector"}
_CLIENTS = {"codex", "claude-code"}
_CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
_RESOURCE_TYPES = {"User", "Group", "Workload"}
_MAX_MEMBERS = 10_000
_GROUP_STORAGE_SENTINEL = ("unbound", "Unbound", "restricted", (), ())


class DirectoryError(ValueError):
    """A stable, content-free directory error suitable for HTTP translation."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DirectoryAuthorizationResolver(Protocol):
    """Policy-owned authorization resolver for active SCIM memberships."""

    def resolve_scim_group_authorization(
        self,
        organization_id: str,
        scim_group_external_ids: tuple[str, ...],
    ) -> ResolvedSCIMGroupAuthorization: ...


@dataclass(frozen=True)
class DirectoryMutation:
    resource: dict[str, object]
    changed: bool
    affected_actor_ids: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _object(value: object, code: str = "scim_invalid_request") -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DirectoryError(code)
    return value


def _string(
    value: object,
    *,
    code: str = "scim_invalid_request",
    minimum: int = 1,
    maximum: int = 512,
) -> str:
    if (
        not isinstance(value, str)
        or not minimum <= len(value.encode("utf-8")) <= maximum
        or not value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise DirectoryError(code)
    return value


def _optional_string(value: object, *, maximum: int = 512) -> str | None:
    if value is None:
        return None
    return _string(value, maximum=maximum)


def _boolean(value: object, *, default: bool = True) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise DirectoryError("scim_invalid_request")
    return value


def _string_list(value: object, *, maximum_items: int = 64) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise DirectoryError("scim_invalid_request")
    result = tuple(_string(item, maximum=128) for item in value)
    if len(result) != len(set(result)):
        raise DirectoryError("scim_invalid_request")
    return result


def _members(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > _MAX_MEMBERS:
        raise DirectoryError("scim_invalid_request")
    result: list[str] = []
    for item in value:
        entry = _object(item)
        result.append(_string(entry.get("value"), maximum=128))
    if len(result) != len(set(result)):
        raise DirectoryError("scim_invalid_request")
    return tuple(sorted(result))


def _schema_set(value: object, required: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DirectoryError("scim_invalid_request")
    if required not in value or len(value) != len(set(value)):
        raise DirectoryError("scim_invalid_request")


def _json_array(value: tuple[str, ...]) -> str:
    return json.dumps(list(value), separators=(",", ":"), ensure_ascii=False)


def _loaded_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise DirectoryError("directory_record_corrupt")
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, RecursionError, ValueError):
        raise DirectoryError("directory_record_corrupt") from None
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise DirectoryError("directory_record_corrupt")
    return tuple(decoded)


def _resource_id(prefix: str, organization_id: str, external_id: str) -> str:
    digest = hashlib.sha256(
        (organization_id + "\x00" + prefix + "\x00" + external_id).encode("utf-8")
    ).hexdigest()[:32]
    return prefix + "_" + digest


def _version(revision: int) -> str:
    return f'W/"{revision}"'


def _parse_if_match(value: str | None, revision: int) -> None:
    if value is None or value == "*":
        return
    if value != _version(revision):
        raise DirectoryError("scim_version_conflict")


def _identity_capability_tuple(value: object) -> tuple[str, ...]:
    try:
        return _identity_capabilities(value, "scim.capabilities")
    except ConfigError as error:
        raise DirectoryError("scim_invalid_request") from error


def _allowed_clients(value: object) -> tuple[str, ...]:
    result = _string_list(value)
    if any(item not in _CLIENTS for item in result):
        raise DirectoryError("scim_invalid_request")
    return result


def parse_user(value: object) -> dict[str, object]:
    item = _object(value)
    _schema_set(item.get("schemas"), SCIM_USER_SCHEMA)
    extension = _object(item.get(HORMUZ_USER_EXTENSION))
    external_id = _string(item.get("externalId"))
    user_name = _string(item.get("userName"))
    display_name = _optional_string(item.get("displayName"), maximum=256) or user_name
    issuer = _string(extension.get("issuer"), maximum=1024)
    subject = _optional_string(extension.get("subject"), maximum=512) or external_id
    return {
        "external_id": external_id,
        "user_name": user_name,
        "display_name": display_name,
        "active": _boolean(item.get("active"), default=True),
        "issuer": issuer,
        "subject": subject,
    }


def parse_group(value: object) -> dict[str, object]:
    item = _object(value)
    _schema_set(item.get("schemas"), SCIM_GROUP_SCHEMA)
    if LEGACY_HORMUZ_GROUP_POLICY_EXTENSION in item:
        raise DirectoryError("scim_group_authorization_fields_forbidden")
    extension = _object(item.get(HORMUZ_GROUP_EXTENSION))
    if set(extension) - {"active"}:
        raise DirectoryError("scim_group_authorization_fields_forbidden")
    if any(
        field in item
        for field in {
            "teamId",
            "teamName",
            "clearance",
            "allowedClients",
            "capabilities",
        }
    ):
        raise DirectoryError("scim_group_authorization_fields_forbidden")
    return {
        "external_id": _string(item.get("externalId")),
        "display_name": _string(item.get("displayName"), maximum=256),
        "members": _members(item.get("members")),
        "active": _boolean(
            extension.get("active", item.get("active")),
            default=True,
        ),
    }


def parse_workload(value: object) -> dict[str, object]:
    item = _object(value)
    identity_type = _string(item.get("identityType"), maximum=32)
    if identity_type not in _IDENTITY_TYPES - {"human"}:
        raise DirectoryError("scim_invalid_request")
    clearance = _string(item.get("clearance", "internal"), maximum=32)
    if clearance not in _CLASSIFICATIONS:
        raise DirectoryError("scim_invalid_request")
    return {
        "external_id": _string(item.get("externalId")),
        "display_name": _string(item.get("displayName"), maximum=256),
        "identity_type": identity_type,
        "active": _boolean(item.get("active"), default=True),
        "issuer": _string(item.get("issuer"), maximum=1024),
        "subject": _string(item.get("subject"), maximum=512),
        "team_id": _string(item.get("teamId"), maximum=128),
        "team_name": _string(item.get("teamName"), maximum=256),
        "clearance": clearance,
        "allowed_clients": _allowed_clients(item.get("allowedClients", [])),
        "capabilities": _identity_capability_tuple(item.get("capabilities", [])),
    }


class SQLiteDirectoryStore:
    """Atomic local directory state plus metadata-only lifecycle audit events."""

    def __init__(
        self,
        path: Path,
        *,
        trusted_issuers: tuple[str, ...],
        authorization_resolver: DirectoryAuthorizationResolver,
    ):
        if not trusted_issuers:
            raise DirectoryError("directory_oidc_issuer_set_empty")
        if authorization_resolver is None:
            raise DirectoryError("directory_authorization_resolver_missing")
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._trusted_issuers = frozenset(trusted_issuers)
        self._authorization_resolver = authorization_resolver
        self._lock = threading.RLock()
        self._initialize()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS directory_resources (
                    organization_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL CHECK (resource_type IN ('User', 'Group', 'Workload')),
                    resource_id TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    active INTEGER NOT NULL CHECK (active IN (0, 1)),
                    revision INTEGER NOT NULL CHECK (revision > 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (organization_id, resource_type, resource_id),
                    UNIQUE (organization_id, resource_type, external_id),
                    UNIQUE (organization_id, resource_id)
                );
                CREATE TABLE IF NOT EXISTS directory_users (
                    organization_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    issuer TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    PRIMARY KEY (organization_id, resource_id),
                    UNIQUE (organization_id, issuer, subject),
                    FOREIGN KEY (organization_id, resource_id)
                        REFERENCES directory_resources (organization_id, resource_id)
                        DEFERRABLE INITIALLY DEFERRED
                );
                CREATE TABLE IF NOT EXISTS directory_groups (
                    organization_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT NOT NULL,
                    clearance TEXT NOT NULL,
                    allowed_clients_json TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    PRIMARY KEY (organization_id, resource_id),
                    FOREIGN KEY (organization_id, resource_id)
                        REFERENCES directory_resources (organization_id, resource_id)
                        DEFERRABLE INITIALLY DEFERRED
                );
                CREATE TABLE IF NOT EXISTS directory_group_memberships (
                    organization_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (organization_id, group_id, user_id),
                    FOREIGN KEY (organization_id, group_id)
                        REFERENCES directory_resources (organization_id, resource_id)
                        DEFERRABLE INITIALLY DEFERRED,
                    FOREIGN KEY (organization_id, user_id)
                        REFERENCES directory_resources (organization_id, resource_id)
                        DEFERRABLE INITIALLY DEFERRED
                );
                CREATE TABLE IF NOT EXISTS directory_workloads (
                    organization_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    issuer TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    identity_type TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT NOT NULL,
                    clearance TEXT NOT NULL,
                    allowed_clients_json TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    PRIMARY KEY (organization_id, resource_id),
                    UNIQUE (organization_id, issuer, subject),
                    FOREIGN KEY (organization_id, resource_id)
                        REFERENCES directory_resources (organization_id, resource_id)
                        DEFERRABLE INITIALLY DEFERRED
                );
                CREATE TABLE IF NOT EXISTS directory_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    decision_actor_id TEXT NOT NULL,
                    decision_actor_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    target_actor_id TEXT NOT NULL,
                    prior_revision INTEGER,
                    revision INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS directory_subject_lookup
                    ON directory_users (issuer, subject, organization_id);
                CREATE INDEX IF NOT EXISTS directory_workload_lookup
                    ON directory_workloads (issuer, subject, organization_id);
                CREATE INDEX IF NOT EXISTS directory_group_memberships_user
                    ON directory_group_memberships (organization_id, user_id, group_id);
                """
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _check_issuer(self, issuer: str) -> None:
        if issuer not in self._trusted_issuers:
            raise DirectoryError("directory_issuer_untrusted")

    @staticmethod
    def _resource_row(
        connection: sqlite3.Connection,
        organization_id: str,
        resource_type: str,
        resource_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM directory_resources WHERE organization_id = ? "
            "AND resource_type = ? AND resource_id = ?",
            (organization_id, resource_type, resource_id),
        ).fetchone()
        if row is None:
            raise DirectoryError("scim_resource_not_found")
        return row

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        organization_id: str,
        decision: Identity,
        action: str,
        resource_type: str,
        resource_id: str,
        target_actor_id: str,
        prior_revision: int | None,
        revision: int,
    ) -> None:
        connection.execute(
            "INSERT INTO directory_events (id, occurred_at, organization_id, decision_actor_id, "
            "decision_actor_name, action, resource_type, resource_id, target_actor_id, "
            "prior_revision, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "ide_" + uuid.uuid4().hex,
                _now(),
                organization_id,
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

    @staticmethod
    def _require_admin(identity: Identity) -> None:
        if "identity_admin" not in identity.capabilities:
            raise DirectoryError("identity_admin_capability_required")

    @staticmethod
    def _require_policy_admin(identity: Identity) -> None:
        if "policy_admin" not in identity.capabilities:
            raise DirectoryError("policy_admin_capability_required")

    @staticmethod
    def _same_user(row: sqlite3.Row, candidate: dict[str, object]) -> bool:
        return (
            str(row["external_id"]) == candidate["external_id"]
            and str(row["user_name"]) == candidate["user_name"]
            and str(row["display_name"]) == candidate["display_name"]
            and bool(row["active"]) is bool(candidate["active"])
            and str(row["issuer"]) == candidate["issuer"]
            and str(row["subject"]) == candidate["subject"]
        )

    @staticmethod
    def _same_group(
        row: sqlite3.Row,
        members: tuple[str, ...],
        candidate: dict[str, object],
    ) -> bool:
        return (
            str(row["external_id"]) == candidate["external_id"]
            and str(row["display_name"]) == candidate["display_name"]
            and tuple(members) == tuple(candidate["members"])
            and bool(row["active"]) is bool(candidate["active"])
        )

    @staticmethod
    def _same_workload(row: sqlite3.Row, candidate: dict[str, object]) -> bool:
        return (
            str(row["external_id"]) == candidate["external_id"]
            and str(row["display_name"]) == candidate["display_name"]
            and str(row["identity_type"]) == candidate["identity_type"]
            and bool(row["active"]) is bool(candidate["active"])
            and str(row["issuer"]) == candidate["issuer"]
            and str(row["subject"]) == candidate["subject"]
            and str(row["team_id"]) == candidate["team_id"]
            and str(row["team_name"]) == candidate["team_name"]
            and str(row["clearance"]) == candidate["clearance"]
            and _loaded_tuple(row["allowed_clients_json"]) == tuple(candidate["allowed_clients"])
            and _loaded_tuple(row["capabilities_json"]) == tuple(candidate["capabilities"])
        )

    @staticmethod
    def _member_ids(connection: sqlite3.Connection, organization_id: str, group_id: str) -> tuple[str, ...]:
        rows = connection.execute(
            "SELECT user_id FROM directory_group_memberships WHERE organization_id = ? "
            "AND group_id = ? ORDER BY user_id",
            (organization_id, group_id),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _assert_members_exist(
        self,
        connection: sqlite3.Connection,
        organization_id: str,
        members: tuple[str, ...],
    ) -> None:
        for user_id in members:
            self._resource_row(connection, organization_id, "User", user_id)

    @staticmethod
    def _actor_ids_for_users(user_ids: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(user_ids)))

    def _user_resource(self, connection: sqlite3.Connection, organization_id: str, resource_id: str) -> dict[str, object]:
        row = self._resource_row(connection, organization_id, "User", resource_id)
        user = connection.execute(
            "SELECT issuer, subject, user_name, display_name FROM directory_users "
            "WHERE organization_id = ? AND resource_id = ?",
            (organization_id, resource_id),
        ).fetchone()
        if user is None:
            raise DirectoryError("directory_record_corrupt")
        groups = connection.execute(
            "SELECT membership.group_id, group_row.display_name FROM directory_group_memberships membership "
            "JOIN directory_groups group_row ON group_row.organization_id = membership.organization_id "
            "AND group_row.resource_id = membership.group_id WHERE membership.organization_id = ? "
            "AND membership.user_id = ? ORDER BY membership.group_id",
            (organization_id, resource_id),
        ).fetchall()
        return {
            "schemas": [SCIM_USER_SCHEMA, HORMUZ_USER_EXTENSION],
            "id": resource_id,
            "externalId": str(row["external_id"]),
            "userName": str(user["user_name"]),
            "displayName": str(user["display_name"]),
            "active": bool(row["active"]),
            "groups": [{"value": str(item[0]), "display": str(item[1])} for item in groups],
            HORMUZ_USER_EXTENSION: {
                "issuer": str(user["issuer"]),
                "subject": str(user["subject"]),
            },
            "meta": {
                "resourceType": "User",
                "created": str(row["created_at"]),
                "lastModified": str(row["updated_at"]),
                "version": _version(int(row["revision"])),
            },
        }

    def _group_resource(self, connection: sqlite3.Connection, organization_id: str, resource_id: str) -> dict[str, object]:
        row = self._resource_row(connection, organization_id, "Group", resource_id)
        group = connection.execute(
            "SELECT display_name "
            "FROM directory_groups WHERE organization_id = ? AND resource_id = ?",
            (organization_id, resource_id),
        ).fetchone()
        if group is None:
            raise DirectoryError("directory_record_corrupt")
        members = self._member_ids(connection, organization_id, resource_id)
        return {
            "schemas": [SCIM_GROUP_SCHEMA, HORMUZ_GROUP_EXTENSION],
            "id": resource_id,
            "externalId": str(row["external_id"]),
            "displayName": str(group["display_name"]),
            "members": [{"value": member} for member in members],
            HORMUZ_GROUP_EXTENSION: {
                "active": bool(row["active"]),
            },
            "meta": {
                "resourceType": "Group",
                "created": str(row["created_at"]),
                "lastModified": str(row["updated_at"]),
                "version": _version(int(row["revision"])),
            },
        }

    def _workload_resource(self, connection: sqlite3.Connection, organization_id: str, resource_id: str) -> dict[str, object]:
        row = self._resource_row(connection, organization_id, "Workload", resource_id)
        workload = connection.execute(
            "SELECT issuer, subject, display_name, identity_type, team_id, team_name, clearance, "
            "allowed_clients_json, capabilities_json FROM directory_workloads "
            "WHERE organization_id = ? AND resource_id = ?",
            (organization_id, resource_id),
        ).fetchone()
        if workload is None:
            raise DirectoryError("directory_record_corrupt")
        return {
            "schema": "hormuz.workload-identity.v1",
            "id": resource_id,
            "externalId": str(row["external_id"]),
            "displayName": str(workload["display_name"]),
            "identityType": str(workload["identity_type"]),
            "active": bool(row["active"]),
            "issuer": str(workload["issuer"]),
            "subject": str(workload["subject"]),
            "teamId": str(workload["team_id"]),
            "teamName": str(workload["team_name"]),
            "clearance": str(workload["clearance"]),
            "allowedClients": list(_loaded_tuple(workload["allowed_clients_json"])),
            "capabilities": list(_loaded_tuple(workload["capabilities_json"])),
            "meta": {
                "resourceType": "Workload",
                "created": str(row["created_at"]),
                "lastModified": str(row["updated_at"]),
                "version": _version(int(row["revision"])),
            },
        }

    def get(self, *, organization_id: str, resource_type: str, resource_id: str) -> dict[str, object]:
        if resource_type not in _RESOURCE_TYPES:
            raise DirectoryError("scim_resource_not_found")
        with self._lock, self._connection() as connection:
            if resource_type == "User":
                return self._user_resource(connection, organization_id, resource_id)
            if resource_type == "Group":
                return self._group_resource(connection, organization_id, resource_id)
            return self._workload_resource(connection, organization_id, resource_id)

    def list(self, *, organization_id: str, resource_type: str, start_index: int = 1, count: int = 100) -> dict[str, object]:
        if resource_type not in _RESOURCE_TYPES or not 1 <= start_index <= 1_000_000 or not 1 <= count <= 100:
            raise DirectoryError("scim_invalid_request")
        with self._lock, self._connection() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM directory_resources WHERE organization_id = ? AND resource_type = ?",
                (organization_id, resource_type),
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT resource_id FROM directory_resources WHERE organization_id = ? AND resource_type = ? "
                "ORDER BY resource_id LIMIT ? OFFSET ?",
                (organization_id, resource_type, count, start_index - 1),
            ).fetchall()
            resources = [
                self._user_resource(connection, organization_id, str(row[0]))
                if resource_type == "User"
                else self._group_resource(connection, organization_id, str(row[0]))
                if resource_type == "Group"
                else self._workload_resource(connection, organization_id, str(row[0]))
                for row in rows
            ]
        return {
            "schemas": [SCIM_LIST_SCHEMA],
            "totalResults": int(total),
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
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT resource.*, user.issuer, user.subject, user.user_name, user.display_name "
                "FROM directory_resources resource JOIN directory_users user "
                "ON user.organization_id = resource.organization_id AND user.resource_id = resource.resource_id "
                "WHERE resource.organization_id = ? AND resource.resource_type = 'User' "
                "AND resource.external_id = ?",
                (organization_id, candidate["external_id"]),
            ).fetchone()
            if existing is not None:
                if not self._same_user(existing, candidate):
                    raise DirectoryError("scim_external_id_conflict")
                return DirectoryMutation(
                    resource=self._user_resource(connection, organization_id, str(existing["resource_id"])),
                    changed=False,
                )
            subject_conflict = connection.execute(
                "SELECT 1 FROM directory_users WHERE organization_id = ? AND issuer = ? AND subject = ?",
                (organization_id, candidate["issuer"], candidate["subject"]),
            ).fetchone()
            if subject_conflict is not None:
                raise DirectoryError("directory_subject_conflict")
            current = _now()
            connection.execute(
                "INSERT INTO directory_resources (organization_id, resource_type, resource_id, external_id, "
                "active, revision, created_at, updated_at) VALUES (?, 'User', ?, ?, ?, 1, ?, ?)",
                (organization_id, resource_id, candidate["external_id"], int(bool(candidate["active"])), current, current),
            )
            connection.execute(
                "INSERT INTO directory_users (organization_id, resource_id, issuer, subject, user_name, display_name) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (organization_id, resource_id, candidate["issuer"], candidate["subject"], candidate["user_name"], candidate["display_name"]),
            )
            self._event(
                connection,
                organization_id=organization_id,
                decision=administrator,
                action="created",
                resource_type="User",
                resource_id=resource_id,
                target_actor_id=resource_id,
                prior_revision=None,
                revision=1,
            )
            return DirectoryMutation(
                resource=self._user_resource(connection, organization_id, resource_id),
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
        with self._transaction() as connection:
            row = self._resource_row(connection, organization_id, "User", resource_id)
            _parse_if_match(if_match, int(row["revision"]))
            current = connection.execute(
                "SELECT resource.*, user.issuer, user.subject, user.user_name, user.display_name "
                "FROM directory_resources resource JOIN directory_users user "
                "ON user.organization_id = resource.organization_id AND user.resource_id = resource.resource_id "
                "WHERE resource.organization_id = ? AND resource.resource_type = 'User' AND resource.resource_id = ?",
                (organization_id, resource_id),
            ).fetchone()
            if current is None:
                raise DirectoryError("directory_record_corrupt")
            if str(current["external_id"]) != candidate["external_id"]:
                raise DirectoryError("scim_external_id_immutable")
            if self._same_user(current, candidate):
                return DirectoryMutation(resource=self._user_resource(connection, organization_id, resource_id), changed=False)
            subject_conflict = connection.execute(
                "SELECT resource_id FROM directory_users WHERE organization_id = ? AND issuer = ? AND subject = ? "
                "AND resource_id <> ?",
                (organization_id, candidate["issuer"], candidate["subject"], resource_id),
            ).fetchone()
            if subject_conflict is not None:
                raise DirectoryError("directory_subject_conflict")
            revision = int(current["revision"]) + 1
            current_time = _now()
            connection.execute(
                "UPDATE directory_resources SET active = ?, revision = ?, updated_at = ? "
                "WHERE organization_id = ? AND resource_type = 'User' AND resource_id = ?",
                (int(bool(candidate["active"])), revision, current_time, organization_id, resource_id),
            )
            connection.execute(
                "UPDATE directory_users SET issuer = ?, subject = ?, user_name = ?, display_name = ? "
                "WHERE organization_id = ? AND resource_id = ?",
                (candidate["issuer"], candidate["subject"], candidate["user_name"], candidate["display_name"], organization_id, resource_id),
            )
            self._event(
                connection,
                organization_id=organization_id,
                decision=administrator,
                action="deactivated" if not candidate["active"] else "updated",
                resource_type="User",
                resource_id=resource_id,
                target_actor_id=resource_id,
                prior_revision=int(current["revision"]),
                revision=revision,
            )
            return DirectoryMutation(
                resource=self._user_resource(connection, organization_id, resource_id),
                changed=True,
                affected_actor_ids=(resource_id,),
            )

    def create_group(self, *, administrator: Identity, value: object) -> DirectoryMutation:
        self._require_admin(administrator)
        candidate = parse_group(value)
        organization_id = administrator.organization_id
        resource_id = _resource_id("grp", organization_id, str(candidate["external_id"]))
        with self._transaction() as connection:
            self._assert_members_exist(connection, organization_id, tuple(candidate["members"]))
            existing = connection.execute(
                "SELECT resource.*, group_row.display_name, group_row.team_id, group_row.team_name, "
                "group_row.clearance, group_row.allowed_clients_json, group_row.capabilities_json "
                "FROM directory_resources resource JOIN directory_groups group_row "
                "ON group_row.organization_id = resource.organization_id AND group_row.resource_id = resource.resource_id "
                "WHERE resource.organization_id = ? AND resource.resource_type = 'Group' AND resource.external_id = ?",
                (organization_id, candidate["external_id"]),
            ).fetchone()
            if existing is not None:
                members = self._member_ids(connection, organization_id, str(existing["resource_id"]))
                if not self._same_group(existing, members, candidate):
                    raise DirectoryError("scim_external_id_conflict")
                return DirectoryMutation(
                    resource=self._group_resource(connection, organization_id, str(existing["resource_id"])),
                    changed=False,
                )
            current = _now()
            connection.execute(
                "INSERT INTO directory_resources (organization_id, resource_type, resource_id, external_id, active, revision, created_at, updated_at) "
                "VALUES (?, 'Group', ?, ?, ?, 1, ?, ?)",
                (
                    organization_id,
                    resource_id,
                    candidate["external_id"],
                    int(bool(candidate["active"])),
                    current,
                    current,
                ),
            )
            connection.execute(
                "INSERT INTO directory_groups (organization_id, resource_id, display_name, team_id, team_name, clearance, allowed_clients_json, capabilities_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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
            for member in tuple(candidate["members"]):
                connection.execute(
                    "INSERT INTO directory_group_memberships (organization_id, group_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                    (organization_id, resource_id, member, current),
                )
            self._event(
                connection,
                organization_id=organization_id,
                decision=administrator,
                action="created",
                resource_type="Group",
                resource_id=resource_id,
                target_actor_id="-",
                prior_revision=None,
                revision=1,
            )
            return DirectoryMutation(
                resource=self._group_resource(connection, organization_id, resource_id),
                changed=True,
                affected_actor_ids=self._actor_ids_for_users(tuple(candidate["members"])),
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
        with self._transaction() as connection:
            row = self._resource_row(connection, organization_id, "Group", resource_id)
            _parse_if_match(if_match, int(row["revision"]))
            self._assert_members_exist(connection, organization_id, tuple(candidate["members"]))
            current = connection.execute(
                "SELECT resource.*, group_row.display_name, group_row.team_id, group_row.team_name, "
                "group_row.clearance, group_row.allowed_clients_json, group_row.capabilities_json "
                "FROM directory_resources resource JOIN directory_groups group_row "
                "ON group_row.organization_id = resource.organization_id AND group_row.resource_id = resource.resource_id "
                "WHERE resource.organization_id = ? AND resource.resource_type = 'Group' AND resource.resource_id = ?",
                (organization_id, resource_id),
            ).fetchone()
            if current is None:
                raise DirectoryError("directory_record_corrupt")
            if str(current["external_id"]) != candidate["external_id"]:
                raise DirectoryError("scim_external_id_immutable")
            old_members = self._member_ids(connection, organization_id, resource_id)
            if self._same_group(current, old_members, candidate):
                return DirectoryMutation(resource=self._group_resource(connection, organization_id, resource_id), changed=False)
            revision = int(current["revision"]) + 1
            current_time = _now()
            connection.execute(
                "UPDATE directory_resources SET active = ?, revision = ?, updated_at = ? WHERE organization_id = ? "
                "AND resource_type = 'Group' AND resource_id = ?",
                (
                    int(bool(candidate["active"])),
                    revision,
                    current_time,
                    organization_id,
                    resource_id,
                ),
            )
            connection.execute(
                "UPDATE directory_groups SET display_name = ?, team_id = ?, team_name = ?, clearance = ?, "
                "allowed_clients_json = ?, capabilities_json = ? WHERE organization_id = ? AND resource_id = ?",
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
            connection.execute(
                "DELETE FROM directory_group_memberships WHERE organization_id = ? AND group_id = ?",
                (organization_id, resource_id),
            )
            for member in tuple(candidate["members"]):
                connection.execute(
                    "INSERT INTO directory_group_memberships (organization_id, group_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                    (organization_id, resource_id, member, current_time),
                )
            self._event(
                connection,
                organization_id=organization_id,
                decision=administrator,
                action="deactivated" if not candidate["active"] else "updated",
                resource_type="Group",
                resource_id=resource_id,
                target_actor_id="-",
                prior_revision=int(current["revision"]),
                revision=revision,
            )
            return DirectoryMutation(
                resource=self._group_resource(connection, organization_id, resource_id),
                changed=True,
                affected_actor_ids=self._actor_ids_for_users(old_members + tuple(candidate["members"])),
            )

    def create_workload(self, *, administrator: Identity, value: object) -> DirectoryMutation:
        self._require_policy_admin(administrator)
        candidate = parse_workload(value)
        self._check_issuer(str(candidate["issuer"]))
        organization_id = administrator.organization_id
        resource_id = _resource_id("wrk", organization_id, str(candidate["external_id"]))
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT resource.*, workload.issuer, workload.subject, workload.display_name, workload.identity_type, "
                "workload.team_id, workload.team_name, workload.clearance, workload.allowed_clients_json, workload.capabilities_json "
                "FROM directory_resources resource JOIN directory_workloads workload "
                "ON workload.organization_id = resource.organization_id AND workload.resource_id = resource.resource_id "
                "WHERE resource.organization_id = ? AND resource.resource_type = 'Workload' AND resource.external_id = ?",
                (organization_id, candidate["external_id"]),
            ).fetchone()
            if existing is not None:
                if not self._same_workload(existing, candidate):
                    raise DirectoryError("scim_external_id_conflict")
                return DirectoryMutation(resource=self._workload_resource(connection, organization_id, str(existing["resource_id"])), changed=False)
            subject_conflict = connection.execute(
                "SELECT 1 FROM directory_workloads WHERE organization_id = ? AND issuer = ? AND subject = ?",
                (organization_id, candidate["issuer"], candidate["subject"]),
            ).fetchone()
            if subject_conflict is not None:
                raise DirectoryError("directory_subject_conflict")
            current = _now()
            connection.execute(
                "INSERT INTO directory_resources (organization_id, resource_type, resource_id, external_id, active, revision, created_at, updated_at) "
                "VALUES (?, 'Workload', ?, ?, ?, 1, ?, ?)",
                (organization_id, resource_id, candidate["external_id"], int(bool(candidate["active"])), current, current),
            )
            connection.execute(
                "INSERT INTO directory_workloads (organization_id, resource_id, issuer, subject, display_name, identity_type, team_id, team_name, clearance, allowed_clients_json, capabilities_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (organization_id, resource_id, candidate["issuer"], candidate["subject"], candidate["display_name"], candidate["identity_type"], candidate["team_id"], candidate["team_name"], candidate["clearance"], _json_array(tuple(candidate["allowed_clients"])), _json_array(tuple(candidate["capabilities"]))),
            )
            self._event(
                connection,
                organization_id=organization_id,
                decision=administrator,
                action="created",
                resource_type="Workload",
                resource_id=resource_id,
                target_actor_id=resource_id,
                prior_revision=None,
                revision=1,
            )
            return DirectoryMutation(resource=self._workload_resource(connection, organization_id, resource_id), changed=True, affected_actor_ids=(resource_id,))

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
        with self._transaction() as connection:
            row = self._resource_row(connection, organization_id, "Workload", resource_id)
            _parse_if_match(if_match, int(row["revision"]))
            current = connection.execute(
                "SELECT resource.*, workload.issuer, workload.subject, workload.display_name, workload.identity_type, "
                "workload.team_id, workload.team_name, workload.clearance, workload.allowed_clients_json, workload.capabilities_json "
                "FROM directory_resources resource JOIN directory_workloads workload "
                "ON workload.organization_id = resource.organization_id AND workload.resource_id = resource.resource_id "
                "WHERE resource.organization_id = ? AND resource.resource_type = 'Workload' AND resource.resource_id = ?",
                (organization_id, resource_id),
            ).fetchone()
            if current is None:
                raise DirectoryError("directory_record_corrupt")
            if str(current["external_id"]) != candidate["external_id"]:
                raise DirectoryError("scim_external_id_immutable")
            if self._same_workload(current, candidate):
                return DirectoryMutation(resource=self._workload_resource(connection, organization_id, resource_id), changed=False)
            subject_conflict = connection.execute(
                "SELECT resource_id FROM directory_workloads WHERE organization_id = ? AND issuer = ? AND subject = ? "
                "AND resource_id <> ?",
                (organization_id, candidate["issuer"], candidate["subject"], resource_id),
            ).fetchone()
            if subject_conflict is not None:
                raise DirectoryError("directory_subject_conflict")
            revision = int(current["revision"]) + 1
            current_time = _now()
            connection.execute(
                "UPDATE directory_resources SET active = ?, revision = ?, updated_at = ? WHERE organization_id = ? "
                "AND resource_type = 'Workload' AND resource_id = ?",
                (int(bool(candidate["active"])), revision, current_time, organization_id, resource_id),
            )
            connection.execute(
                "UPDATE directory_workloads SET issuer = ?, subject = ?, display_name = ?, identity_type = ?, team_id = ?, "
                "team_name = ?, clearance = ?, allowed_clients_json = ?, capabilities_json = ? "
                "WHERE organization_id = ? AND resource_id = ?",
                (candidate["issuer"], candidate["subject"], candidate["display_name"], candidate["identity_type"], candidate["team_id"], candidate["team_name"], candidate["clearance"], _json_array(tuple(candidate["allowed_clients"])), _json_array(tuple(candidate["capabilities"])), organization_id, resource_id),
            )
            self._event(
                connection,
                organization_id=organization_id,
                decision=administrator,
                action="deactivated" if not candidate["active"] else "updated",
                resource_type="Workload",
                resource_id=resource_id,
                target_actor_id=resource_id,
                prior_revision=int(current["revision"]),
                revision=revision,
            )
            return DirectoryMutation(resource=self._workload_resource(connection, organization_id, resource_id), changed=True, affected_actor_ids=(resource_id,))

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
        with self._transaction() as connection:
            row = self._resource_row(connection, organization_id, resource_type, resource_id)
            _parse_if_match(if_match, int(row["revision"]))
            if not bool(row["active"]):
                resource = (
                    self._user_resource(connection, organization_id, resource_id)
                    if resource_type == "User"
                    else self._group_resource(connection, organization_id, resource_id)
                    if resource_type == "Group"
                    else self._workload_resource(connection, organization_id, resource_id)
                )
                return DirectoryMutation(resource=resource, changed=False)
            revision = int(row["revision"]) + 1
            connection.execute(
                "UPDATE directory_resources SET active = 0, revision = ?, updated_at = ? "
                "WHERE organization_id = ? AND resource_type = ? AND resource_id = ?",
                (revision, _now(), organization_id, resource_type, resource_id),
            )
            affected = (
                self._member_ids(connection, organization_id, resource_id)
                if resource_type == "Group"
                else (resource_id,)
            )
            self._event(
                connection,
                organization_id=organization_id,
                decision=administrator,
                action="deactivated",
                resource_type=resource_type,
                resource_id=resource_id,
                target_actor_id="-" if resource_type == "Group" else resource_id,
                prior_revision=int(row["revision"]),
                revision=revision,
            )
            resource = (
                self._user_resource(connection, organization_id, resource_id)
                if resource_type == "User"
                else self._group_resource(connection, organization_id, resource_id)
                if resource_type == "Group"
                else self._workload_resource(connection, organization_id, resource_id)
            )
            return DirectoryMutation(resource=resource, changed=True, affected_actor_ids=self._actor_ids_for_users(tuple(affected)))

    def identity_for_subject(self, issuer: str, subject: str) -> Identity | None:
        """Return a current dynamic identity, or a stable denial for a managed subject."""

        with self._lock, self._connection() as connection:
            workloads = connection.execute(
                "SELECT resource.*, workload.issuer, workload.subject, workload.display_name, workload.identity_type, "
                "workload.team_id, workload.team_name, workload.clearance, workload.allowed_clients_json, workload.capabilities_json "
                "FROM directory_resources resource JOIN directory_workloads workload "
                "ON workload.organization_id = resource.organization_id AND workload.resource_id = resource.resource_id "
                "WHERE workload.issuer = ? AND workload.subject = ?",
                (issuer, subject),
            ).fetchall()
            users = connection.execute(
                "SELECT resource.*, user.issuer, user.subject, user.user_name, user.display_name "
                "FROM directory_resources resource JOIN directory_users user "
                "ON user.organization_id = resource.organization_id AND user.resource_id = resource.resource_id "
                "WHERE user.issuer = ? AND user.subject = ?",
                (issuer, subject),
            ).fetchall()
            matches = [("workload", row) for row in workloads] + [("user", row) for row in users]
            if not matches:
                return None
            if len(matches) != 1:
                raise DirectoryError("directory_subject_ambiguous")
            kind, row = matches[0]
            if not bool(row["active"]):
                raise DirectoryError("directory_identity_inactive")
            organization_id = str(row["organization_id"])
            resource_id = str(row["resource_id"])
            if kind == "workload":
                return Identity(
                    token_env="",
                    token="",
                    actor_id=resource_id,
                    actor_name=str(row["display_name"]),
                    team_id=str(row["team_id"]),
                    team_name=str(row["team_name"]),
                    allowed_clients=_loaded_tuple(row["allowed_clients_json"]),
                    capabilities=_loaded_tuple(row["capabilities_json"]),
                    organization_id=organization_id,
                    clearance=str(row["clearance"]),
                    authentication_source=f"directory:{issuer}",
                    identity_type=str(row["identity_type"]),
                )
            groups = connection.execute(
                "SELECT group_resource.active, group_resource.external_id "
                "FROM directory_group_memberships membership "
                "JOIN directory_resources group_resource ON group_resource.organization_id = membership.organization_id "
                "AND group_resource.resource_type = 'Group' AND group_resource.resource_id = membership.group_id "
                "WHERE membership.organization_id = ? AND membership.user_id = ?",
                (organization_id, resource_id),
            ).fetchall()
            active_group_external_ids = tuple(
                str(group["external_id"])
                for group in groups
                if bool(group["active"])
            )
            try:
                authorization = self._authorization_resolver.resolve_scim_group_authorization(
                    organization_id,
                    active_group_external_ids,
                )
            except SCIMGroupAuthorizationError as error:
                raise DirectoryError(error.code) from None
            except Exception:
                # A corrupt or unavailable policy runtime must never cause the
                # IdP-provided group fields to become an authorization fallback.
                raise DirectoryError("directory_policy_unavailable") from None
            return Identity(
                token_env="",
                token="",
                actor_id=resource_id,
                actor_name=str(row["display_name"]),
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
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT organization_id FROM ("
                "SELECT organization_id FROM directory_users WHERE issuer = ? "
                "UNION SELECT organization_id FROM directory_workloads WHERE issuer = ?"
                ") ORDER BY organization_id",
                (issuer, issuer),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)
