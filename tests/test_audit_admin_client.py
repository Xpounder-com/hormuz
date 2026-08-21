from __future__ import annotations

import unittest
from unittest import mock

from hormuz.audit_admin_client import AuditAdminClient, AuditAdminClientError


def _response() -> dict[str, object]:
    return {
        "schema_version": 1,
        "organization_id": "xpounder",
        "kind": "security",
        "window": {
            "start": "2026-08-01T00:00:00+00:00",
            "end": "2026-08-16T12:00:00+00:00",
            "timezone": "UTC",
        },
        "events": [
            {
                "schema_version": 1,
                "event_type": "security.admin.audit_read",
                "id": "audit-event-1",
                "occurred_at": "2026-08-16T12:00:00+00:00",
                "organization_id": "xpounder",
                "action": "audit.events.read",
                "decision_actor_id": "auditor",
                "result_count": 0,
            }
        ],
        "next_cursor": None,
    }


class AuditAdminClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = AuditAdminClient(
            "http://127.0.0.1:8787",
            credential="audit-viewer-token-long",
            allow_insecure_http=True,
        )

    def test_exact_metadata_only_response_shape_is_required(self) -> None:
        response = _response()
        with mock.patch.object(self.client, "_request", return_value=response):
            self.assertEqual(self.client.list_events(kind="security"), response)

        unsafe = _response()
        unsafe["events"][0]["prompt"] = "must-not-be-accepted"  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=unsafe):
            with self.assertRaisesRegex(AuditAdminClientError, "invalid_gateway_response"):
                self.client.list_events(kind="security")

        wrong_tenant = _response()
        wrong_tenant["events"][0]["organization_id"] = "other-company"  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=wrong_tenant):
            with self.assertRaisesRegex(AuditAdminClientError, "invalid_gateway_response"):
                self.client.list_events(kind="security")

    def test_invalid_local_query_fails_before_network_work(self) -> None:
        with mock.patch.object(self.client, "_request") as request:
            with self.assertRaisesRegex(AuditAdminClientError, "invalid_audit_event_request"):
                self.client.list_events(kind="unsupported")
            with self.assertRaisesRegex(AuditAdminClientError, "invalid_audit_event_request"):
                self.client.list_events(limit=101)
            with self.assertRaisesRegex(AuditAdminClientError, "invalid_audit_event_request"):
                self.client.list_events(since="2026-08-01T00:00:00")
            with self.assertRaisesRegex(AuditAdminClientError, "invalid_audit_event_request"):
                self.client.list_events(since=42)  # type: ignore[arg-type]
            with self.assertRaisesRegex(AuditAdminClientError, "invalid_audit_event_request"):
                self.client.list_events(cursor="x" * 4097)
        request.assert_not_called()

    def test_cursor_and_non_finite_metadata_are_checked(self) -> None:
        response = _response()
        response["next_cursor"] = "opaque-cursor"  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=response) as request:
            self.assertEqual(
                self.client.list_events(kind="security", cursor="opaque-cursor"),
                response,
            )
        self.assertIn("cursor=opaque-cursor", request.call_args.args[0])

        non_finite = _response()
        non_finite["events"][0]["result_count"] = float("inf")  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=non_finite):
            with self.assertRaisesRegex(AuditAdminClientError, "invalid_gateway_response"):
                self.client.list_events(kind="security")


if __name__ == "__main__":
    unittest.main()
