from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from uuid import uuid4

from hormuz.attribution_admission import select_admission
from hormuz.outcome_ingest import OutcomeIngestor
from hormuz.outcome_wire import OutcomeKeys
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import ASSOCIATIONS, BINDINGS, RUN_WORK_LINKS, SCOPES, PortfolioError, canonical, validate
from hormuz.store import ReservationScope, UsageStore

from ._attribution_fixture import attributed_config
from ._outcome_fixture import SyntheticOutcomeAdapter
from ._portfolio_fixture import ADMIN, OTHER, VIEWER, binding_request, create_request, registry_config
from ._sqlite import managed_sqlite_connection
from .test_outcome_contract import observation


class SQLiteAssociationRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = registry_config(self.root)
        self.store = UsageStore(self.config.database_path)
        self.clock_instant = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        self.clock_lock = threading.Lock()
        for target in ("hormuz._portfolio_sql.PortfolioSQL.now", "hormuz.outcome_ingest.observed_time"):
            patcher = mock.patch(target, side_effect=self.clock)
            patcher.start()
            self.addCleanup(patcher.stop)
        initial = create_portfolio_repository(self.config)
        service = PortfolioService(self.config, initial)
        self.scope = service.dispatch(
            ADMIN,
            "POST",
            SCOPES,
            body=canonical(create_request()).encode(),
            idempotency_key="association-scope",
        )[1]
        self.binding = service.dispatch(
            ADMIN,
            "POST",
            BINDINGS,
            body=canonical(binding_request(self.scope)).encode(),
            idempotency_key="association-binding",
        )[1]
        self.config = attributed_config(self.config, self.scope)
        self.repositories = create_portfolio_repository(self.config)
        self.service = PortfolioService(self.config, self.repositories)
        self.principal = self.service.authenticate(ADMIN)
        self.identity = self.config.identities_by_token[ADMIN]
        self.keys = OutcomeKeys("association-key-v1", {"association-key-v1": b"a" * 32})
        self.ingestor = OutcomeIngestor(
            self.config,
            self.repositories.outcomes,
            "acme",
            "github-one",
            SyntheticOutcomeAdapter(),
            self.keys,
        )

    def clock(self):
        with self.clock_lock:
            self.clock_instant += timedelta(milliseconds=1)
            return self.clock_instant.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def attempt(self):
        attempt = self.store.begin_request_attempt(
            identity=self.identity,
            client="codex",
            protocol="openai",
            requested_model="requested-alias",
            resolved_alias="resolved-alias",
            upstream_model="routed-model",
            policy_version="association-policy-v1",
            policy_action="allowed",
            redaction_count=0,
            redaction_rules=(),
            scopes=(ReservationScope(name="organization"),),
            reserved_tokens=20,
            reserved_cost_microusd=40,
            ttl_seconds=60,
        )
        admission = select_admission(
            self.config,
            self.identity,
            "codex",
            [f'v1;work_scope_id={self.scope["work_scope_id"]};version=1'],
            account_usage=True,
        )
        attribution = self.repositories.attributions.admit(
            self.identity,
            "codex",
            "openai",
            admission,
            attempt.attempt_id,
        )
        self.store.finalize_request_attempt(
            attempt=attempt,
            organization_id="acme",
            status="succeeded",
            provider_reported_model="actual-model",
            input_tokens=7,
            output_tokens=3,
            cost_microusd=11,
        )
        return attempt, attribution

    def outcome(self, **changes):
        value = observation(**{
            "source_event_id": str(uuid4()),
            "source_revision": "1",
            "revision_order": "1",
            "event_at": self.clock(),
            **changes,
        })
        raw = canonical({"observations": [value]}).encode()
        self.ingestor.ingest(
            {"synthetic-signature": "verified-test-only", "delivery": str(uuid4())},
            raw,
        )
        return value

    @staticmethod
    def link_request(attempt, source, *, prior=None, reason="explicit_link"):
        return {
            "schema_id": "hormuz.run-work-link-request",
            "schema_version": 1,
            "request_attempt_id": attempt.attempt_id,
            "outcome": {
                "connector_id": "github-one",
                "source_event_id": source["source_event_id"],
            },
            "expected_prior_link_event_id": prior,
            "reason_code": reason,
        }

    @staticmethod
    def evaluation_request(source, *, prior=None):
        instant = datetime.fromisoformat(source["event_at"])
        return {
            "schema_id": "hormuz.run-outcome-association-evaluation-request",
            "schema_version": 1,
            "outcome": {
                "connector_id": "github-one",
                "source_event_id": source["source_event_id"],
            },
            "window": {
                "start_at": (instant - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                "end_at": (instant + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            },
            "expected_prior_association_event_id": prior,
        }

    def post(self, path, value, *, key=None):
        return self.service.dispatch(
            ADMIN,
            "POST",
            path,
            body=canonical(value).encode(),
            idempotency_key=key,
        )[1]

    def error(self, code, operation):
        with self.assertRaises(PortfolioError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def test_exact_link_association_replay_and_commit_chain(self):
        attempt, attribution = self.attempt()
        source = self.outcome()
        request = self.link_request(attempt, source)
        link = self.post(RUN_WORK_LINKS, request, key="link-one")
        validate(link, "hormuz.run-work-link-event")
        self.assertEqual(link["attribution_event_id"], attribution["attribution_event_id"])
        self.assertEqual(link["work_scope"], {"work_scope_id": self.scope["work_scope_id"], "version": 1})
        self.assertEqual(self.post(RUN_WORK_LINKS, request, key="link-one"), link)
        self.error(
            "idempotency_conflict",
            lambda: self.post(
                RUN_WORK_LINKS,
                {**request, "reason_code": "corrected", "expected_prior_link_event_id": link["link_event_id"]},
                key="link-one",
            ),
        )
        evaluation = self.evaluation_request(source)
        associated = self.post(ASSOCIATIONS, evaluation)
        validate(associated, "hormuz.run-outcome-association-event")
        self.assertEqual(
            (associated["state"], associated["candidate_count"], associated["reason_code"]),
            ("associated", 1, "eligible"),
        )
        self.assertEqual(associated["request_attempt_id"], attempt.attempt_id)
        self.assertEqual(self.post(ASSOCIATIONS, evaluation), associated)
        self.error(
            "invalid_request",
            lambda: self.post(ASSOCIATIONS, evaluation, key="unused-evaluation-key"),
        )
        links = self.service.dispatch(ADMIN, "GET", RUN_WORK_LINKS)[1]
        associations = self.service.dispatch(ADMIN, "GET", ASSOCIATIONS)[1]
        validate(links, "hormuz.run-work-link-page")
        validate(associations, "hormuz.run-outcome-association-page")
        self.assertEqual(links["items"], [link])
        self.assertEqual(associations["items"], [associated])
        self.assert_audit_and_immutability(link, associated)

    def assert_audit_and_immutability(self, link, associated):
        with managed_sqlite_connection(self.config.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT source_schema_id,source_event_id,event_json FROM gateway_audit_chain_entries "
                "WHERE source_schema_id IN (?,?) ORDER BY sequence",
                ("hormuz.run-work-link-event", "hormuz.run-outcome-association-event"),
            ).fetchall()
            self.assertEqual([row["source_schema_id"] for row in rows], [
                "hormuz.run-work-link-event",
                "hormuz.run-outcome-association-event",
            ])
            self.assertEqual(rows[0]["event_json"], canonical(link))
            self.assertEqual(rows[1]["event_json"], canonical(associated))
            for statement in (
                "UPDATE portfolio_run_work_link_events SET actor_id='mallory'",
                "DELETE FROM portfolio_run_outcome_association_events",
            ):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "association_metadata_append_only"):
                    connection.execute(statement)

    def test_ambiguity_tombstone_re_evaluation_and_bound_cursors(self):
        source = self.outcome()
        first_attempt, _ = self.attempt()
        second_attempt, _ = self.attempt()
        first = self.post(RUN_WORK_LINKS, self.link_request(first_attempt, source), key="link-first")
        second = self.post(RUN_WORK_LINKS, self.link_request(second_attempt, source), key="link-second")
        ambiguous = self.post(ASSOCIATIONS, self.evaluation_request(source))
        self.assertEqual(
            (ambiguous["state"], ambiguous["candidate_count"], ambiguous["reason_code"]),
            ("ambiguous", 2, "ambiguous"),
        )
        tombstone = self.post(
            RUN_WORK_LINKS,
            self.link_request(
                second_attempt,
                source,
                prior=second["link_event_id"],
                reason="tombstoned",
            ),
            key="link-second-tombstone",
        )
        self.assertEqual(tombstone["state"], "tombstoned")
        associated = self.post(
            ASSOCIATIONS,
            self.evaluation_request(source, prior=ambiguous["association_event_id"]),
        )
        self.assertEqual(
            (associated["state"], associated["request_attempt_id"]),
            ("associated", first_attempt.attempt_id),
        )
        first_page = self.service.dispatch(
            ADMIN,
            "GET",
            RUN_WORK_LINKS,
            query="limit=1&connector_id=github-one",
        )[1]
        self.assertTrue(first_page["has_more"])
        continuation = self.service.dispatch(
            ADMIN,
            "GET",
            RUN_WORK_LINKS,
            query="limit=1&connector_id=github-one&cursor=" + first_page["next_cursor"],
        )[1]
        self.assertEqual(continuation["as_of"], first_page["as_of"])
        self.error(
            "cursor_invalid",
            lambda: self.service.dispatch(
                ADMIN,
                "GET",
                RUN_WORK_LINKS,
                query="limit=1&cursor=" + first_page["next_cursor"],
            ),
        )
        self.error(
            "forbidden",
            lambda: self.service.dispatch(VIEWER, "GET", ASSOCIATIONS),
        )
        self.assertEqual(self.service.dispatch(OTHER, "GET", ASSOCIATIONS)[1]["items"], [])

    def test_unmatched_then_retention_exclusion_is_append_only(self):
        source = self.outcome()
        unmatched = self.post(ASSOCIATIONS, self.evaluation_request(source))
        self.assertEqual(
            (unmatched["state"], unmatched["candidate_count"], unmatched["reason_code"]),
            ("unmatched", 0, "missing_evidence"),
        )
        retained = self.repositories.outcomes.tombstone(
            self.principal,
            "github-one",
            source["source_event_id"],
            idempotency_key="association-retention",
            keys=self.keys,
        )
        self.assertEqual(retained["reason_code"], "tombstoned")
        excluded = self.post(
            ASSOCIATIONS,
            self.evaluation_request(source, prior=unmatched["association_event_id"]),
        )
        self.assertEqual(
            (excluded["state"], excluded["candidate_count"], excluded["reason_code"]),
            ("excluded", 0, "excluded"),
        )
        page = self.service.dispatch(ADMIN, "GET", ASSOCIATIONS)[1]
        self.assertEqual(
            {item["association_event_id"] for item in page["items"]},
            {unmatched["association_event_id"], excluded["association_event_id"]},
        )

    def test_late_revision_reopen_and_equal_revision_conflict_do_not_reuse_stale_authority(self):
        first_attempt, _ = self.attempt()
        first = self.outcome(
            source_revision="10",
            revision_order="10",
            event_type="accepted",
            quality_state="accepted",
        )
        self.post(RUN_WORK_LINKS, self.link_request(first_attempt, first), key="authority-first-link")
        accepted = self.post(ASSOCIATIONS, self.evaluation_request(first))
        self.assertEqual(accepted["state"], "associated")

        conflict = self.outcome(
            source_revision="10",
            revision_order="10",
            event_type="reopened",
            quality_state="unknown",
            supersedes_source_event_id=first["source_event_id"],
            reason_code="corrected",
        )
        ambiguous = self.post(
            ASSOCIATIONS,
            self.evaluation_request(first, prior=accepted["association_event_id"]),
        )
        self.assertEqual(
            (ambiguous["state"], ambiguous["candidate_count"], ambiguous["reason_code"]),
            ("ambiguous", 2, "ambiguous"),
        )
        self.error(
            "version_conflict",
            lambda: self.post(
                RUN_WORK_LINKS,
                self.link_request(first_attempt, conflict),
                key="authority-conflict-link",
            ),
        )

        reopened = self.outcome(
            source_revision="11",
            revision_order="11",
            event_type="reopened",
            quality_state="unknown",
            supersedes_source_event_id=conflict["source_event_id"],
            reason_code="corrected",
        )
        excluded = self.post(
            ASSOCIATIONS,
            self.evaluation_request(first, prior=ambiguous["association_event_id"]),
        )
        self.assertEqual(
            (excluded["state"], excluded["candidate_count"], excluded["reason_code"]),
            ("excluded", 0, "superseded"),
        )
        second_attempt, _ = self.attempt()
        self.post(RUN_WORK_LINKS, self.link_request(second_attempt, reopened), key="authority-reopen-link")
        current = self.post(ASSOCIATIONS, self.evaluation_request(reopened))
        self.assertEqual(
            (current["state"], current["request_attempt_id"], current["reason_code"]),
            ("associated", second_attempt.attempt_id, "eligible"),
        )

    def test_authorized_metric_reference_joins_once_and_keeps_completeness_inconclusive(self):
        attempt, _ = self.attempt()
        source = self.outcome(
            event_type="accepted",
            quality_state="accepted",
            duration_ms="60000",
        )
        self.post(
            RUN_WORK_LINKS,
            self.link_request(attempt, source),
            key="metric-reference-link",
        )
        source_at = datetime.fromisoformat(source["event_at"])
        real_now = datetime.now(timezone.utc)
        start = source_at - timedelta(hours=1)
        end = real_now + timedelta(hours=1)
        evaluated = real_now + timedelta(hours=4)
        with self.clock_lock:
            self.clock_instant = real_now + timedelta(hours=3)
        evaluation = self.evaluation_request(source)
        evaluation["window"] = {
            "start_at": start.isoformat().replace("+00:00", "Z"),
            "end_at": end.isoformat().replace("+00:00", "Z"),
        }
        associated = self.post(ASSOCIATIONS, evaluation)
        self.assertEqual(associated["state"], "associated")
        with self.clock_lock:
            self.clock_instant = real_now + timedelta(hours=5)
        vector = self.repositories.associations.metric_reference(
            self.principal,
            work_scope_id=self.scope["work_scope_id"],
            work_scope_version=1,
            start_at=evaluation["window"]["start_at"],
            end_at=evaluation["window"]["end_at"],
            evaluated_at=evaluated.isoformat().replace("+00:00", "Z"),
        )
        self.assertEqual(
            {
                name: vector["denominators"][name]
                for name in (
                    "eligible_attempts", "priced_attempts", "unique_work_objects",
                    "associated", "unmatched", "ambiguous", "excluded",
                )
            },
            {
                "eligible_attempts": 1,
                "priced_attempts": 0,
                "unique_work_objects": 1,
                "associated": 1,
                "unmatched": 0,
                "ambiguous": 0,
                "excluded": 0,
            },
        )
        self.assertEqual(vector["coverage"]["priced_runs"]["ratio"], "0")
        self.assertEqual(vector["measures"]["cycle_time"]["status"], "inconclusive")
        self.assertFalse(vector["causal_claim"])
        self.error(
            "not_found",
            lambda: self.repositories.associations.metric_reference(
                self.principal,
                work_scope_id="missing-use-case",
                work_scope_version=1,
                start_at=evaluation["window"]["start_at"],
                end_at=evaluation["window"]["end_at"],
                evaluated_at=evaluated.isoformat().replace("+00:00", "Z"),
            ),
        )
        self.error(
            "forbidden",
            lambda: self.repositories.associations.metric_reference(
                self.service.authenticate(VIEWER),
                work_scope_id=self.scope["work_scope_id"],
                work_scope_version=1,
                start_at=evaluation["window"]["start_at"],
                end_at=evaluation["window"]["end_at"],
                evaluated_at=evaluated.isoformat().replace("+00:00", "Z"),
            ),
        )

    def test_metric_delivery_health_prefers_recovered_receipt_over_dead_letter(self):
        anchor = self.outcome(external_object_id="901")
        source = observation(
            source_event_id=str(uuid4()),
            external_object_id="902",
            source_revision="1",
            revision_order="1",
            event_at=self.clock(),
        )
        raw = canonical({"observations": [source]}).encode()
        delivery = str(uuid4())
        failing_adapter = SyntheticOutcomeAdapter()
        failing_adapter.normalize = mock.Mock(side_effect=PortfolioError("invalid_request"))
        failing = OutcomeIngestor(
            self.config,
            self.repositories.outcomes,
            "acme",
            "github-one",
            failing_adapter,
            self.keys,
        )
        headers = {
            "synthetic-signature": "verified-test-only",
            "delivery": delivery,
        }
        self.error("invalid_request", lambda: failing.ingest(headers, raw))
        source_at = datetime.fromisoformat(source["event_at"])
        with self.clock_lock:
            self.clock_instant = source_at + timedelta(hours=2)
        receipt = self.ingestor.ingest(headers, raw)
        self.assertEqual(receipt["disposition"], "accepted")

        with self.clock_lock:
            self.clock_instant = source_at + timedelta(hours=5)
        arguments = dict(
            principal=self.principal,
            work_scope_id=self.scope["work_scope_id"],
            work_scope_version=1,
            start_at=(source_at - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            end_at=(source_at + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        )
        before_recovery = self.repositories.associations.metric_reference(
            evaluated_at=(source_at + timedelta(hours=1, minutes=30)).isoformat().replace("+00:00", "Z"),
            **arguments,
        )
        after_recovery = self.repositories.associations.metric_reference(
            evaluated_at=(source_at + timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
            **arguments,
        )
        self.assertEqual(anchor["external_object_id"], "901")
        self.assertEqual(
            (
                before_recovery["denominators"]["connector_deliveries"],
                before_recovery["denominators"]["connector_successful_deliveries"],
            ),
            (2, 1),
        )
        self.assertEqual(
            (
                after_recovery["denominators"]["connector_deliveries"],
                after_recovery["denominators"]["connector_successful_deliveries"],
            ),
            (2, 2),
        )

    def test_authorization_and_connector_checks_precede_storage(self):
        source = observation(
            source_event_id=str(uuid4()),
            source_revision="1",
            revision_order="1",
            event_at=self.clock(),
        )
        evaluation = self.evaluation_request(source)
        evaluation["outcome"]["connector_id"] = "unconfigured-connector"
        with mock.patch.object(
            self.repositories.associations,
            "_transaction",
            side_effect=AssertionError("unauthorized storage access"),
        ):
            self.error(
                "forbidden",
                lambda: self.service.dispatch(VIEWER, "GET", ASSOCIATIONS),
            )
            self.error(
                "forbidden",
                lambda: self.post(ASSOCIATIONS, evaluation),
            )
