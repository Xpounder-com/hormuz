from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from hormuz.context import (
    CONTEXT_PACK_SCHEMA,
    ContextError,
    ContextPackRequest,
    ContextPrincipal,
    ContextRecord,
    build_context_pack,
    estimate_record_tokens,
)


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def record(
    record_id: str,
    *,
    title: str = "Retry policy",
    content: str = "Use bounded exponential retry policy for transient failures.",
    organization_id: str = "acme",
    visibility: str = "organization",
    scope_id: str | None = None,
    classification: str = "internal",
    repository_id: str | None = None,
    branch: str | None = None,
    verification: str = "verified",
    verified_at: datetime | None = NOW - timedelta(days=1),
    expires_at: datetime | None = None,
    supersedes_id: str | None = None,
    tags: tuple[str, ...] = ("reliability",),
) -> ContextRecord:
    return ContextRecord(
        record_id=record_id,
        title=title,
        content=content,
        organization_id=organization_id,
        visibility=visibility,
        scope_id=scope_id or (organization_id if visibility == "organization" else "engineering"),
        classification=classification,
        source_uri=f"https://example.test/{record_id}",
        source_revision="sha256:test",
        repository_id=repository_id,
        branch=branch,
        verification=verification,
        verified_at=verified_at if verification == "verified" else None,
        expires_at=expires_at,
        supersedes_id=supersedes_id,
        tags=tags,
    )


def request(**changes: object) -> ContextPackRequest:
    values: dict[str, object] = {
        "query": "retry policy",
        "principal": ContextPrincipal(
            organization_id="acme",
            team_id="engineering",
            actor_id="alice",
            clearance="internal",
            repository_id="acme/api",
            branch="main",
        ),
        "token_budget": 10_000,
        "policy_version": "policy-17",
        "as_of": NOW,
    }
    values.update(changes)
    return ContextPackRequest(**values)  # type: ignore[arg-type]


class ContextPackTests(unittest.TestCase):
    def test_authorization_verification_and_freshness_filter_before_ranking(self) -> None:
        records = [
            record("org"),
            record("team", visibility="team", scope_id="engineering"),
            record("actor", visibility="actor", scope_id="alice"),
            record("other-org", organization_id="other", scope_id="other"),
            record("other-team", visibility="team", scope_id="marketing"),
            record("other-actor", visibility="actor", scope_id="bob"),
            record("other-repo", repository_id="acme/web"),
            record("other-branch", repository_id="acme/api", branch="feature"),
            record("too-sensitive", classification="confidential"),
            record("provisional", verification="provisional", verified_at=None),
            record("future", verified_at=NOW + timedelta(minutes=1)),
            record(
                "expired",
                verified_at=NOW - timedelta(days=2),
                expires_at=NOW - timedelta(seconds=1),
            ),
        ]

        pack = build_context_pack(records, request())

        self.assertEqual([item.record.record_id for item in pack.items], ["actor", "org", "team"])
        self.assertEqual(pack.eligible_records, 3)
        self.assertEqual(pack.matched_records, 3)
        self.assertLessEqual(pack.estimated_tokens, pack.request.token_budget)
        self.assertEqual(pack.to_dict()["schema_version"], CONTEXT_PACK_SCHEMA)

    def test_supersession_is_authorized_active_and_order_independent(self) -> None:
        old = record("old", verified_at=NOW - timedelta(days=10))
        current = record("current", verified_at=NOW - timedelta(days=1), supersedes_id="old")

        forward = build_context_pack([old, current], request())
        reverse = build_context_pack([current, old], request())

        self.assertEqual([item.record.record_id for item in forward.items], ["current"])
        self.assertEqual(forward.pack_id, reverse.pack_id)
        self.assertEqual(forward.manifest_sha256, reverse.manifest_sha256)

        expired_current = replace(current, expires_at=NOW - timedelta(seconds=1))
        fallback = build_context_pack([old, expired_current], request())
        self.assertEqual([item.record.record_id for item in fallback.items], ["old"])

    def test_pack_identity_covers_content_scope_policy_and_budget(self) -> None:
        original = record("standard")
        first = build_context_pack([original], request())

        changed_content = build_context_pack(
            [replace(original, content=original.content + " Add jitter.")],
            request(),
        )
        changed_policy = build_context_pack([original], request(policy_version="policy-18"))
        changed_budget = build_context_pack([original], request(token_budget=9_999))
        changed_metadata = build_context_pack(
            [replace(original, classification="public")],
            request(),
        )
        later_same_selection = build_context_pack(
            [original],
            request(as_of=NOW + timedelta(minutes=5)),
        )

        self.assertNotEqual(first.pack_id, changed_content.pack_id)
        self.assertNotEqual(first.pack_id, changed_policy.pack_id)
        self.assertNotEqual(first.pack_id, changed_budget.pack_id)
        self.assertNotEqual(first.pack_id, changed_metadata.pack_id)
        self.assertEqual(first.pack_id, later_same_selection.pack_id)

    def test_budget_skips_oversized_record_and_selects_smaller_match(self) -> None:
        oversized = record(
            "oversized",
            title="Retry policy",
            content="retry policy " + "large " * 2_000,
        )
        compact = record(
            "compact",
            title="Retry guidance",
            content="Retry transient failures according to policy.",
        )
        budget = estimate_record_tokens(compact)

        pack = build_context_pack([oversized, compact], request(token_budget=budget))

        self.assertEqual([item.record.record_id for item in pack.items], ["compact"])
        self.assertEqual(pack.estimated_tokens, budget)

    def test_invalid_records_and_supersession_cycles_fail_closed(self) -> None:
        with self.assertRaises(ContextError):
            ContextRecord.from_dict(
                {
                    "id": "bad",
                    "title": "Bad",
                    "content": "retry policy",
                    "organization_id": "acme",
                    "visibility": "organization",
                    "scope_id": "acme",
                    "source": {"uri": "test", "revision": "1"},
                    "verification": "verified",
                    "verified_at": "2026-08-15T12:00:00",
                }
            )

        one = record("one", supersedes_id="two")
        two = record("two", supersedes_id="one")
        with self.assertRaisesRegex(ContextError, "supersession cycle"):
            build_context_pack([one, two], request())

        with self.assertRaisesRegex(ContextError, "duplicate"):
            build_context_pack([record("same"), record("same")], request())

    def test_provisional_records_require_explicit_opt_in(self) -> None:
        provisional = record("draft", verification="provisional", verified_at=None)

        default_pack = build_context_pack([provisional], request())
        opted_in = build_context_pack(
            [provisional],
            request(include_provisional=True),
        )

        self.assertEqual(default_pack.items, ())
        self.assertEqual([item.record.record_id for item in opted_in.items], ["draft"])


if __name__ == "__main__":
    unittest.main()
