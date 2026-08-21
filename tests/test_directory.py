from __future__ import annotations

import tempfile
from pathlib import Path
import http.client
import json
import unittest

from hormuz.auth import AuthenticationError
from hormuz.config import (
    AuthorizationProfile,
    DirectoryConfig,
    GatewayConfig,
    Identity,
    ListenConfig,
    ModelRoute,
    OIDCIssuerConfig,
    Policy,
    PolicyTeamBinding,
    ResolvedSCIMGroupAuthorization,
    SCIMGroupAuthorizationError,
    SessionBrokerConfig,
    UpstreamConfig,
)
from hormuz.directory import (
    HORMUZ_GROUP_EXTENSION,
    HORMUZ_USER_EXTENSION,
    SCIM_GROUP_SCHEMA,
    SCIM_USER_SCHEMA,
    DirectoryError,
    SQLiteDirectoryStore,
)
from hormuz.server import _apply_scim_patch
from hormuz.server import GatewayServer, serve_in_thread


ISSUER = "https://identity.example"


class _PolicyResolver:
    """Small policy-owned resolver used to isolate directory behavior tests."""

    def __init__(self) -> None:
        self._profiles = {
            "engineering": ResolvedSCIMGroupAuthorization(
                team_id="engineering",
                team_name="Engineering",
                policy_id="engineering-standard",
                clearance="internal",
                allowed_clients=("claude-code", "codex"),
                capabilities=(),
            ),
            "marketing": ResolvedSCIMGroupAuthorization(
                team_id="marketing",
                team_name="Marketing",
                policy_id="marketing-standard",
                clearance="internal",
                allowed_clients=("claude-code",),
                capabilities=(),
            ),
        }

    def resolve_scim_group_authorization(
        self,
        organization_id: str,
        scim_group_external_ids: tuple[str, ...],
    ) -> ResolvedSCIMGroupAuthorization:
        if organization_id != "acme" or not scim_group_external_ids:
            raise SCIMGroupAuthorizationError("directory_subject_unassigned")
        matched = [
            self._profiles.get(group_id) for group_id in scim_group_external_ids
        ]
        if any(profile is None for profile in matched):
            raise SCIMGroupAuthorizationError("directory_subject_group_unbound")
        resolved = set(matched)
        if len(resolved) != 1:
            raise SCIMGroupAuthorizationError("directory_subject_policy_ambiguous")
        return next(iter(resolved))


class DirectoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.admin = Identity(
            token_env="",
            token="",
            actor_id="directory-admin",
            actor_name="Directory Admin",
            team_id="security",
            team_name="Security",
            organization_id="acme",
            clearance="restricted",
            capabilities=("identity_admin", "policy_admin"),
        )
        self.store = SQLiteDirectoryStore(
            Path(self.root.name) / "directory.sqlite3",
            trusted_issuers=(ISSUER,),
            authorization_resolver=_PolicyResolver(),
        )

    def _user(self, external_id: str = "alice-id", *, active: bool = True) -> dict[str, object]:
        return {
            "schemas": [SCIM_USER_SCHEMA, HORMUZ_USER_EXTENSION],
            "externalId": external_id,
            "userName": "alice@example.test",
            "displayName": "Alice",
            "active": active,
            HORMUZ_USER_EXTENSION: {"issuer": ISSUER, "subject": external_id},
        }

    @staticmethod
    def _group(member_id: str, *, team_id: str = "engineering") -> dict[str, object]:
        return {
            "schemas": [SCIM_GROUP_SCHEMA, HORMUZ_GROUP_EXTENSION],
            "externalId": team_id,
            "displayName": team_id.title(),
            "members": [{"value": member_id}],
            HORMUZ_GROUP_EXTENSION: {"active": True},
        }

    def test_scim_user_group_membership_resolves_a_human_identity(self) -> None:
        user = self.store.create_user(administrator=self.admin, value=self._user())
        self.assertTrue(user.changed)
        repeated_user = self.store.create_user(administrator=self.admin, value=self._user())
        self.assertFalse(repeated_user.changed)
        group = self.store.create_group(
            administrator=self.admin,
            value=self._group(str(user.resource["id"])),
        )
        self.assertTrue(group.changed)
        repeated_group = self.store.create_group(
            administrator=self.admin,
            value=self._group(str(user.resource["id"])),
        )
        self.assertFalse(repeated_group.changed)
        self.assertEqual(repeated_group.resource["id"], group.resource["id"])
        repeated_membership = self.store.replace_group(
            administrator=self.admin,
            resource_id=str(group.resource["id"]),
            value=self._group(str(user.resource["id"])),
            if_match=str(group.resource["meta"]["version"]),  # type: ignore[index]
        )
        self.assertFalse(repeated_membership.changed)

        identity = self.store.identity_for_subject(ISSUER, "alice-id")
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.identity_type, "human")
        self.assertEqual(identity.actor_id, user.resource["id"])
        self.assertEqual(identity.team_id, "engineering")
        self.assertEqual(identity.allowed_clients, ("claude-code", "codex"))

    def test_group_member_removal_immediately_removes_dynamic_authorization(self) -> None:
        user = self.store.create_user(administrator=self.admin, value=self._user())
        group = self.store.create_group(
            administrator=self.admin,
            value=self._group(str(user.resource["id"])),
        )
        group_id = str(group.resource["id"])
        update = self._group(str(user.resource["id"]))
        update["members"] = []
        replaced = self.store.replace_group(
            administrator=self.admin,
            resource_id=group_id,
            value=update,
            if_match=str(group.resource["meta"]["version"]),  # type: ignore[index]
        )
        self.assertTrue(replaced.changed)
        self.assertEqual(replaced.affected_actor_ids, (str(user.resource["id"]),))
        with self.assertRaisesRegex(DirectoryError, "directory_subject_unassigned"):
            self.store.identity_for_subject(ISSUER, "alice-id")

    def test_group_deactivation_and_reactivation_change_authorization(self) -> None:
        user = self.store.create_user(administrator=self.admin, value=self._user())
        group = self.store.create_group(
            administrator=self.admin,
            value=self._group(str(user.resource["id"])),
        )
        deactivated = self.store.deactivate(
            administrator=self.admin,
            resource_type="Group",
            resource_id=str(group.resource["id"]),
            if_match=str(group.resource["meta"]["version"]),  # type: ignore[index]
        )
        self.assertTrue(deactivated.changed)
        with self.assertRaisesRegex(DirectoryError, "directory_subject_unassigned"):
            self.store.identity_for_subject(ISSUER, "alice-id")

        reactivated = self.store.replace_group(
            administrator=self.admin,
            resource_id=str(group.resource["id"]),
            value=self._group(str(user.resource["id"])),
            if_match=str(deactivated.resource["meta"]["version"]),  # type: ignore[index]
        )
        self.assertTrue(reactivated.changed)
        self.assertEqual(
            self.store.identity_for_subject(ISSUER, "alice-id").team_id,  # type: ignore[union-attr]
            "engineering",
        )

    def test_user_deactivation_is_idempotent_and_denies_the_subject(self) -> None:
        user = self.store.create_user(administrator=self.admin, value=self._user())
        self.store.create_group(
            administrator=self.admin,
            value=self._group(str(user.resource["id"])),
        )
        resource_id = str(user.resource["id"])
        first = self.store.deactivate(
            administrator=self.admin,
            resource_type="User",
            resource_id=resource_id,
            if_match=str(user.resource["meta"]["version"]),  # type: ignore[index]
        )
        self.assertTrue(first.changed)
        with self.assertRaisesRegex(DirectoryError, "directory_identity_inactive"):
            self.store.identity_for_subject(ISSUER, "alice-id")
        repeated = self.store.deactivate(
            administrator=self.admin,
            resource_type="User",
            resource_id=resource_id,
            if_match=str(first.resource["meta"]["version"]),  # type: ignore[index]
        )
        self.assertFalse(repeated.changed)

        reactivated = self.store.replace_user(
            administrator=self.admin,
            resource_id=resource_id,
            value=self._user(),
            if_match=str(first.resource["meta"]["version"]),  # type: ignore[index]
        )
        self.assertTrue(reactivated.changed)
        self.assertEqual(
            self.store.identity_for_subject(ISSUER, "alice-id").actor_id,  # type: ignore[union-attr]
            resource_id,
        )

    def test_workload_is_federated_and_distinct_from_a_human(self) -> None:
        workload = self.store.create_workload(
            administrator=self.admin,
            value={
                "externalId": "github-actions-production",
                "displayName": "GitHub Actions",
                "identityType": "ci",
                "active": True,
                "issuer": ISSUER,
                "subject": "repo:acme/hormuz:environment:production",
                "teamId": "engineering",
                "teamName": "Engineering",
                "clearance": "internal",
                "allowedClients": ["codex"],
                "capabilities": [],
            },
        )
        identity = self.store.identity_for_subject(
            ISSUER,
            "repo:acme/hormuz:environment:production",
        )
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.identity_type, "ci")
        self.assertEqual(identity.actor_id, workload.resource["id"])
        self.assertEqual(identity.allowed_clients, ("codex",))

    def test_patch_operations_are_deterministic_and_versioned(self) -> None:
        user = self.store.create_user(administrator=self.admin, value=self._user())
        current = user.resource
        patched = _apply_scim_patch(
            "User",
            current,
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [
                    {"op": "replace", "path": "displayName", "value": "Alice Renamed"}
                ],
            },
        )
        updated = self.store.replace_user(
            administrator=self.admin,
            resource_id=str(current["id"]),
            value=patched,
            if_match=str(current["meta"]["version"]),  # type: ignore[index]
        )
        self.assertTrue(updated.changed)
        self.assertEqual(updated.resource["displayName"], "Alice Renamed")
        repeated_update = self.store.replace_user(
            administrator=self.admin,
            resource_id=str(current["id"]),
            value=patched,
            if_match=str(updated.resource["meta"]["version"]),  # type: ignore[index]
        )
        self.assertFalse(repeated_update.changed)
        with self.assertRaisesRegex(DirectoryError, "scim_version_conflict"):
            self.store.replace_user(
                administrator=self.admin,
                resource_id=str(current["id"]),
                value=patched,
                if_match=str(current["meta"]["version"]),  # type: ignore[index]
            )

    def test_team_transfer_is_deterministic_and_stale_membership_event_fails_closed(self) -> None:
        user = self.store.create_user(administrator=self.admin, value=self._user())
        user_id = str(user.resource["id"])
        engineering = self.store.create_group(
            administrator=self.admin,
            value=self._group(user_id, team_id="engineering"),
        )
        marketing_value = self._group(user_id, team_id="marketing")
        marketing_value["members"] = []
        marketing = self.store.create_group(
            administrator=self.admin,
            value=marketing_value,
        )
        self.assertEqual(
            self.store.identity_for_subject(ISSUER, "alice-id").team_id,  # type: ignore[union-attr]
            "engineering",
        )

        added_destination = self.store.replace_group(
            administrator=self.admin,
            resource_id=str(marketing.resource["id"]),
            value=self._group(user_id, team_id="marketing"),
            if_match=str(marketing.resource["meta"]["version"]),  # type: ignore[index]
        )
        self.assertTrue(added_destination.changed)
        with self.assertRaisesRegex(DirectoryError, "directory_subject_policy_ambiguous"):
            self.store.identity_for_subject(ISSUER, "alice-id")

        source_removed = self._group(user_id, team_id="engineering")
        source_removed["members"] = []
        completed_transfer = self.store.replace_group(
            administrator=self.admin,
            resource_id=str(engineering.resource["id"]),
            value=source_removed,
            if_match=str(engineering.resource["meta"]["version"]),  # type: ignore[index]
        )
        self.assertTrue(completed_transfer.changed)
        self.assertEqual(
            self.store.identity_for_subject(ISSUER, "alice-id").team_id,  # type: ignore[union-attr]
            "marketing",
        )
        with self.assertRaisesRegex(DirectoryError, "scim_version_conflict"):
            self.store.replace_group(
                administrator=self.admin,
                resource_id=str(marketing.resource["id"]),
                value=marketing_value,
                if_match=str(marketing.resource["meta"]["version"]),  # type: ignore[index]
            )

    def test_cross_team_groups_fail_closed_instead_of_guessing_policy_scope(self) -> None:
        user = self.store.create_user(administrator=self.admin, value=self._user())
        self.store.create_group(
            administrator=self.admin,
            value=self._group(str(user.resource["id"]), team_id="engineering"),
        )
        self.store.create_group(
            administrator=self.admin,
            value=self._group(str(user.resource["id"]), team_id="marketing"),
        )
        with self.assertRaisesRegex(DirectoryError, "directory_subject_policy_ambiguous"):
            self.store.identity_for_subject(ISSUER, "alice-id")

    def test_scim_group_cannot_supply_authorization_fields(self) -> None:
        user = self.store.create_user(administrator=self.admin, value=self._user())
        forbidden = self._group(str(user.resource["id"]))
        extension = forbidden[HORMUZ_GROUP_EXTENSION]
        assert isinstance(extension, dict)
        extension["teamId"] = "marketing"
        with self.assertRaisesRegex(
            DirectoryError, "scim_group_authorization_fields_forbidden"
        ):
            self.store.create_group(
                administrator=self.admin,
                value=forbidden,
            )

    def test_unbound_group_fails_closed_even_when_a_bound_group_exists(self) -> None:
        user = self.store.create_user(administrator=self.admin, value=self._user())
        self.store.create_group(
            administrator=self.admin,
            value=self._group(str(user.resource["id"]), team_id="engineering"),
        )
        self.store.create_group(
            administrator=self.admin,
            value=self._group(str(user.resource["id"]), team_id="unknown-group"),
        )
        with self.assertRaisesRegex(DirectoryError, "directory_subject_group_unbound"):
            self.store.identity_for_subject(ISSUER, "alice-id")

    def test_identity_admin_cannot_create_a_directly_authorized_workload(self) -> None:
        identity_only_admin = Identity(
            token_env="",
            token="",
            actor_id="identity-admin",
            actor_name="Identity Admin",
            team_id="security",
            team_name="Security",
            organization_id="acme",
            clearance="restricted",
            capabilities=("identity_admin",),
        )
        with self.assertRaisesRegex(DirectoryError, "policy_admin_capability_required"):
            self.store.create_workload(
                administrator=identity_only_admin,
                value={
                    "externalId": "ci",
                    "displayName": "CI",
                    "identityType": "ci",
                    "issuer": ISSUER,
                    "subject": "repo:acme/hormuz",
                    "teamId": "engineering",
                    "teamName": "Engineering",
                },
            )


class DirectoryHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        root = Path(self.root.name)
        self.token = "directory-admin-token-" + "a" * 32
        admin = Identity(
            token_env="HORMUZ_DIRECTORY_ADMIN",
            token=self.token,
            actor_id="directory-admin",
            actor_name="Directory Admin",
            team_id="security",
            team_name="Security",
            organization_id="acme",
            clearance="restricted",
            capabilities=("identity_admin",),
        )
        self.config = GatewayConfig(
            source_path=root / "hormuz.json",
            listen=ListenConfig(host="127.0.0.1", port=0),
            database_path=root / "usage.sqlite3",
            context_database_path=root / "context.sqlite3",
            upstreams={
                "openai": UpstreamConfig("http://127.0.0.1:1", "OPENAI_API_KEY"),
                "anthropic": UpstreamConfig("http://127.0.0.1:1", "ANTHROPIC_API_KEY"),
            },
            identities_by_token={self.token: admin},
            model_routes={
                "gpt-test": ModelRoute("gpt-test", "openai", "gpt-test"),
            },
            organization_policy=Policy(allowed_models=("gpt-test",)),
            source_sha256="a" * 64,
            session_broker=SessionBrokerConfig(
                enabled=True,
                backend="sqlite",
                database_path=root / "sessions.sqlite3",
                public_base_url="http://127.0.0.1:1",
                master_key=b"m" * 32,
                master_key_source="directory-http-test-session-master-key",
            ),
            directory=DirectoryConfig(enabled=True, database_path=root / "directory.sqlite3"),
            oidc_issuers={ISSUER: OIDCIssuerConfig(issuer=ISSUER, audiences=("hormuz",))},
            authorization_profiles={
                "engineering-standard": AuthorizationProfile(
                    organization_id="acme",
                    policy_id="engineering-standard",
                    team_id="engineering",
                    team_name="Engineering",
                    clearance="internal",
                    allowed_clients=("codex",),
                    capabilities=("dlp_approver", "policy_admin"),
                    policy=Policy(allowed_clients=("codex",)),
                )
            },
            team_bindings=(
                PolicyTeamBinding(
                    organization_id="acme",
                    scim_group_external_id="engineering",
                    team_id="engineering",
                    policy_id="engineering-standard",
                ),
            ),
        )
        self.gateway = GatewayServer(self.config)
        self.thread = serve_in_thread(self.gateway)
        self.addCleanup(self._close_gateway)

    def _close_gateway(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        self.thread.join(timeout=5)

    def _request(
        self,
        method: str,
        path: str,
        value: dict[str, object] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, object]]:
        body = json.dumps(value).encode("utf-8") if value is not None else None
        request_headers = {"Authorization": "Bearer " + self.token}
        if body is not None:
            request_headers.update({"Content-Type": "application/scim+json", "Content-Length": str(len(body))})
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.gateway.server_address[1],
            timeout=5,
        )
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            parsed = json.loads(response.read())
            return response.status, {key.lower(): value for key, value in response.getheaders()}, parsed
        finally:
            connection.close()

    def _issue_directory_session(self, *, actor_id: str) -> str:
        broker = self.gateway.session_broker
        self.assertIsNotNone(broker)
        assert broker is not None
        store = broker.store
        enrollment_secret = "directory-session-enrollment-" + "s" * 32
        enrollment = store.create_enrollment(
            issuer=ISSUER,
            client_name="codex",
            enrollment_secret=enrollment_secret,
            organization_id="acme",
        )
        state = "directory-session-state-" + "t" * 32
        browser_cookie = "directory-session-browser-" + "b" * 32
        store.begin_authorization(  # type: ignore[attr-defined]
            enrollment_id=enrollment.enrollment_id,
            state=state,
            browser_cookie=browser_cookie,
            nonce="directory-session-nonce-" + "n" * 32,
            pkce_verifier="directory-session-pkce-" + "p" * 48,
        )
        store.consume_callback(  # type: ignore[attr-defined]
            state=state,
            browser_cookie=browser_cookie,
        )
        store.authorize_enrollment(  # type: ignore[attr-defined]
            enrollment_id=enrollment.enrollment_id,
            subject="alice-id",
            organization_id="acme",
            actor_id=actor_id,
            team_id="engineering",
            clearance="internal",
        )
        return store.redeem_enrollment(  # type: ignore[attr-defined]
            enrollment_id=enrollment.enrollment_id,
            enrollment_secret=enrollment_secret,
        ).access_token

    def _session_request(
        self,
        method: str,
        path: str,
        access_token: str,
        value: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        body = json.dumps(value).encode("utf-8") if value is not None else None
        headers = {"Authorization": "Bearer " + access_token}
        if body is not None:
            headers.update(
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                }
            )
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.gateway.server_address[1],
            timeout=5,
        )
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            parsed = json.loads(response.read())
            return response.status, parsed
        finally:
            connection.close()

    def _assert_protected_paths_denied(self, access_token: str) -> None:
        denied_requests = (
            ("POST", "/v1/responses", {"model": "gpt-test", "input": "blocked"}),
            ("GET", "/v1/admin/policy-active", None),
            (
                "POST",
                "/v1/admin/policy-activations",
                {"version_id": "hpv_v1_" + "0" * 64, "expected_active_version_id": None},
            ),
            (
                "POST",
                "/v1/dlp/approval-requests/apr_" + "0" * 32 + "/decisions",
                {"decision": "approve"},
            ),
        )
        for method, path, value in denied_requests:
            with self.subTest(method=method, path=path):
                status, response = self._session_request(
                    method,
                    path,
                    access_token,
                    value,
                )
                self.assertEqual(status, 401)
                self.assertEqual(response["error"]["code"], "unauthorized")

    def test_scim_http_provisions_and_deprovisions_the_identity_used_by_authentication(self) -> None:
        user_payload = {
            "schemas": [SCIM_USER_SCHEMA, HORMUZ_USER_EXTENSION],
            "externalId": "alice-id",
            "userName": "alice@example.test",
            "displayName": "Alice",
            "active": True,
            HORMUZ_USER_EXTENSION: {"issuer": ISSUER, "subject": "alice-id"},
        }
        status, headers, user = self._request("POST", "/v1/admin/scim/v2/Users", user_payload)
        self.assertEqual(status, 201)
        self.assertEqual(headers["content-type"], "application/scim+json")
        self.assertIn("etag", headers)
        user_id = str(user["id"])
        status, _headers, retried_user = self._request(
            "POST", "/v1/admin/scim/v2/Users", user_payload
        )
        self.assertEqual(status, 200)
        self.assertEqual(retried_user["id"], user_id)
        status, _headers, missing = self._request(
            "GET", "/v1/admin/scim/v2/Users/usr_" + "x" * 32
        )
        self.assertEqual(status, 404)
        self.assertEqual(missing["scimType"], "scim_resource_not_found")
        group_payload = {
            "schemas": [SCIM_GROUP_SCHEMA, HORMUZ_GROUP_EXTENSION],
            "externalId": "engineering",
            "displayName": "Engineering",
            "members": [{"value": user_id}],
            HORMUZ_GROUP_EXTENSION: {"active": True},
        }
        status, _headers, group = self._request("POST", "/v1/admin/scim/v2/Groups", group_payload)
        self.assertEqual(status, 201)
        identity = self.gateway.authenticator.identity_for_subject(ISSUER, "alice-id")
        self.assertEqual(identity.actor_id, user_id)
        self.assertEqual(identity.identity_type, "human")

        status, _headers, patched = self._request(
            "PATCH",
            "/v1/admin/scim/v2/Groups/" + str(group["id"]),
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "replace", "path": "members", "value": []}],
            },
            headers={"If-Match": str(group["meta"]["version"])},  # type: ignore[index]
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["members"], [])
        with self.assertRaisesRegex(AuthenticationError, "directory_subject_unassigned"):
            self.gateway.authenticator.identity_for_subject(ISSUER, "alice-id")

    def test_removed_member_session_cannot_reach_provider_or_administrative_paths(self) -> None:
        user_payload = {
            "schemas": [SCIM_USER_SCHEMA, HORMUZ_USER_EXTENSION],
            "externalId": "alice-id",
            "userName": "alice@example.test",
            "displayName": "Alice",
            "active": True,
            HORMUZ_USER_EXTENSION: {"issuer": ISSUER, "subject": "alice-id"},
        }
        status, _headers, user = self._request("POST", "/v1/admin/scim/v2/Users", user_payload)
        self.assertEqual(status, 201)
        user_id = str(user["id"])
        group_payload = {
            "schemas": [SCIM_GROUP_SCHEMA, HORMUZ_GROUP_EXTENSION],
            "externalId": "engineering",
            "displayName": "Engineering",
            "members": [{"value": user_id}],
            HORMUZ_GROUP_EXTENSION: {"active": True},
        }
        status, _headers, group = self._request("POST", "/v1/admin/scim/v2/Groups", group_payload)
        self.assertEqual(status, 201)
        access_token = self._issue_directory_session(actor_id=user_id)
        status, identity = self._session_request(
            "GET",
            "/v1/gateway/whoami",
            access_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(identity["actor_id"], user_id)

        status, _headers, _patched = self._request(
            "PATCH",
            "/v1/admin/scim/v2/Groups/" + str(group["id"]),
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "replace", "path": "members", "value": []}],
            },
            headers={"If-Match": str(group["meta"]["version"])},  # type: ignore[index]
        )
        self.assertEqual(status, 200)

        self._assert_protected_paths_denied(access_token)

    def test_deactivated_user_session_cannot_reach_provider_or_administrative_paths(self) -> None:
        user_payload = {
            "schemas": [SCIM_USER_SCHEMA, HORMUZ_USER_EXTENSION],
            "externalId": "alice-id",
            "userName": "alice@example.test",
            "displayName": "Alice",
            "active": True,
            HORMUZ_USER_EXTENSION: {"issuer": ISSUER, "subject": "alice-id"},
        }
        status, _headers, user = self._request("POST", "/v1/admin/scim/v2/Users", user_payload)
        self.assertEqual(status, 201)
        user_id = str(user["id"])
        group_payload = {
            "schemas": [SCIM_GROUP_SCHEMA, HORMUZ_GROUP_EXTENSION],
            "externalId": "engineering",
            "displayName": "Engineering",
            "members": [{"value": user_id}],
            HORMUZ_GROUP_EXTENSION: {"active": True},
        }
        status, _headers, _group = self._request("POST", "/v1/admin/scim/v2/Groups", group_payload)
        self.assertEqual(status, 201)
        access_token = self._issue_directory_session(actor_id=user_id)
        status, identity = self._session_request(
            "GET",
            "/v1/gateway/whoami",
            access_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(identity["actor_id"], user_id)

        status, _headers, deactivated = self._request(
            "DELETE",
            "/v1/admin/scim/v2/Users/" + user_id,
            headers={"If-Match": str(user["meta"]["version"])},  # type: ignore[index]
        )
        self.assertEqual(status, 200)
        self.assertFalse(deactivated["active"])

        self._assert_protected_paths_denied(access_token)


if __name__ == "__main__":
    unittest.main()
