"""Content-free fixtures, with explicitly synthetic source verification."""

from __future__ import annotations

from dataclasses import asdict, replace
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import json
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from uuid import uuid4

from hormuz._outcome_schema import TABLE_DDL
from hormuz._portfolio_sql import PortfolioSQL
from hormuz.outcome_ingest import AuthenticatedDelivery, OutcomeIngestor
from hormuz.outcome_wire import OutcomeKeys, validate_context, validate_coverage
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import BINDINGS, OUTCOMES, SCOPES, PortfolioError, canonical, validate

if __package__:
    from ._portfolio_fixture import ADMIN, OTHER, VIEWER, binding_request, create_request, version_request
    from .test_outcome_contract import observation
else:
    from _portfolio_fixture import ADMIN, OTHER, VIEWER, binding_request, create_request, version_request
    from test_outcome_contract import observation


class SyntheticOutcomeAdapter:
    def verify(self, *, binding, headers, raw):
        if headers.get("synthetic-signature") != "verified-test-only":
            raise PortfolioError("unauthenticated")
        return AuthenticatedDelivery(binding.organization_id, binding.connector_id, binding.provider,
                                     binding.installation_id, binding.workspace_id, headers["delivery"], "test-auth-v1")

    def normalize(self, *, binding, verified, body):
        return body["observations"]


def seed_outcome_metadata(config, *, environ=None):
    """Populate every outcome table on an already migrated synthetic database.

    Source authorities/signatures are synthetic. Repository/container 789 is
    separate from the actual predecessor's existing 456 binding. Return exact
    content-free inputs/results for isolated restore and replay verification.
    """
    group = create_portfolio_repository(config, environ=environ)
    service = PortfolioService(config, group)
    principal = service.authenticate(ADMIN)
    scope = service.dispatch(ADMIN, "POST", SCOPES, body=canonical(create_request()).encode(), idempotency_key="outcome-recovery-scope")[1]
    service.dispatch(ADMIN, "POST", BINDINGS, body=canonical(binding_request(scope, external_object_id="789")).encode(),
                     idempotency_key="outcome-recovery-binding")
    keys = OutcomeKeys("recovery-key-v1", {"recovery-key-v1": b"synthetic-outcome-recovery-key-123"})
    ingestor = OutcomeIngestor(config, group.outcomes, "acme", "github-one", SyntheticOutcomeAdapter(), keys)
    deliveries, sources = [], []
    for ordinal in (1, 2, 3):
        source = observation(source_event_id=str(uuid4()), external_object_id=str(1000 + ordinal), container_id="789",
                             source_revision=str(ordinal), revision_order=str(ordinal),
                             event_at=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))
        sources.append(source["source_event_id"])
        delivery, raw = str(uuid4()), canonical({"observations": [source]})
        receipt = ingestor.ingest({"synthetic-signature": "verified-test-only", "delivery": delivery}, raw.encode())
        deliveries.append({"delivery": delivery, "raw": raw, "receipt": receipt})
    retained = group.outcomes.tombstone(principal, "github-one", sources[0], idempotency_key="outcome-recovery-retention", keys=keys)
    failed_delivery, failed_raw = str(uuid4()), '{"observations":[{}]}'
    try:
        ingestor.ingest({"synthetic-signature": "verified-test-only", "delivery": failed_delivery}, failed_raw.encode())
    except PortfolioError as error:
        if error.code != "invalid_request":
            raise
    else:
        raise AssertionError("synthetic-invalid-outcome-not-refused")
    page = service.dispatch(ADMIN, "GET", OUTCOMES, query="limit=1")[1]
    if not page["has_more"]:
        raise AssertionError("synthetic-outcome-cursor-not-created")
    return {"deliveries": deliveries, "retention": retained, "retained_source": sources[0],
            "failed_delivery": failed_delivery, "failed_raw": failed_raw, "page": page}


def replay_outcome_metadata(config, seeded, *, environ=None):
    group = create_portfolio_repository(config, environ=environ)
    service = PortfolioService(config, group)
    keys = OutcomeKeys("recovery-key-v1", {"recovery-key-v1": b"synthetic-outcome-recovery-key-123"})
    ingestor = OutcomeIngestor(config, group.outcomes, "acme", "github-one", SyntheticOutcomeAdapter(), keys)
    receipts = [ingestor.ingest({"synthetic-signature": "verified-test-only", "delivery": item["delivery"]}, item["raw"].encode())
                for item in seeded["deliveries"]]
    retained = group.outcomes.tombstone(service.authenticate(ADMIN), "github-one", seeded["retained_source"],
                                       idempotency_key="outcome-recovery-retention", keys=keys)
    try:
        ingestor.ingest({"synthetic-signature": "verified-test-only", "delivery": seeded["failed_delivery"]}, seeded["failed_raw"].encode())
    except PortfolioError as error:
        if error.code != "invalid_request":
            raise
    else:
        raise AssertionError("synthetic-invalid-outcome-not-refused")
    return receipts, retained


class OutcomeAssertions:
    def setup_outcomes(self):
        self.clock_instant = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        self.clock_lock = threading.Lock()
        for target in ("hormuz._portfolio_sql.PortfolioSQL.now", "hormuz.outcome_ingest.observed_time"):
            clock_patch = mock.patch(target, side_effect=self.clock)
            clock_patch.start()
            self.addCleanup(clock_patch.stop)
        self.repositories = create_portfolio_repository(self.config, environ=self.environment)
        self.repository = self.repositories.outcomes
        self.service = PortfolioService(self.config, self.repositories)
        self.principal = self.service.authenticate(ADMIN)
        self.keys = OutcomeKeys("key-v1", {"key-v1": b"a" * 32})
        self.ingestor = OutcomeIngestor(self.config, self.repository, "acme", "github-one", SyntheticOutcomeAdapter(), self.keys)
        self.scope = self.service.dispatch(ADMIN, "POST", SCOPES, body=canonical(create_request()).encode(), idempotency_key="outcome-scope")[1]
        self.binding = self.service.dispatch(ADMIN, "POST", BINDINGS, body=canonical(binding_request(self.scope)).encode(), idempotency_key="outcome-binding")[1]

    def source(self, **changes):
        # Event time follows enrollment, but precedes observation/commit.
        return observation(**{"source_event_id": str(uuid4()), "event_at": self.clock(), **changes})

    def clock(self):
        with self.clock_lock:
            self.clock_instant += timedelta(milliseconds=1)
            return self.clock_instant.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def ingest(self, observations=None, *, delivery=None, raw=None, ingestor=None):
        raw = canonical({"observations": observations if observations is not None else [self.source()]}).encode() if raw is None else raw
        delivery = str(uuid4()) if delivery is None else delivery
        return (ingestor or self.ingestor).ingest({"synthetic-signature": "verified-test-only", "delivery": delivery}, raw)

    def page(self, query="", *, token=ADMIN):
        status, page = self.service.dispatch(token, "GET", OUTCOMES, query=query)
        self.assertEqual(status, 200)
        validate(page, "hormuz.work-outcome-page")
        return page

    def error(self, code, operation):
        with self.assertRaises(PortfolioError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def check_atomic_metadata_receipt_replay_and_rotation(self):
        source, delivery = self.source(), str(uuid4())
        raw = canonical({"observations": [source], "ignored_work_content": "SYNTHETIC_EXCLUDED"}).encode()
        prior_ledgers = self.legacy_rows()
        receipt = self.ingest(delivery=delivery, raw=raw)
        validate(receipt, "hormuz.connector-ingest-receipt")
        before = self.outcome_rows()
        self.assertEqual(self.ingest(delivery=delivery, raw=raw), receipt)
        self.assertEqual(self.outcome_rows(), before)
        rotated = OutcomeIngestor(self.config, self.repository, "acme", "github-one", SyntheticOutcomeAdapter(),
                                  OutcomeKeys("key-v2", {"key-v1": b"a" * 32, "key-v2": b"b" * 32}))
        self.assertEqual(self.ingest(delivery=delivery, raw=raw, ingestor=rotated), receipt)
        missing = OutcomeIngestor(self.config, self.repository, "acme", "github-one", SyntheticOutcomeAdapter(),
                                  OutcomeKeys("key-v2", {"key-v2": b"b" * 32}))
        self.error("unavailable", lambda: self.ingest(delivery=delivery, raw=raw, ingestor=missing))
        self.error("idempotency_conflict", lambda: self.ingest(delivery=delivery, raw=raw + b" "))
        self.assertEqual(self.outcome_rows(), before)
        self.assertEqual(self.legacy_rows(), prior_ledgers)
        event = self.page()["items"][0]
        self.assertEqual(event["event_at"], source["event_at"])
        self.assertEqual(event["evidence_level"], "descriptive")
        self.assertNotIn("work_scope", event)
        context = self.repository.context(self.principal, "github-one", source["source_event_id"])
        validate_context(context)
        self.assertEqual((context["work_scope_id"], context["work_scope_version"], context["binding_event_id"]),
                         (self.scope["work_scope_id"], 1, self.binding["binding_event_id"]))
        self.assertEqual((context["scope_state"], context["key_version"], context["credential_version"]), ("matched", "key-v1", "test-auth-v1"))
        self.assertNotIn("SYNTHETIC_EXCLUDED", canonical(self.outcome_rows()))
        for item in self.repository.coverage(self.principal):
            validate_coverage(item)
            self.assertEqual(item["eligibility_state"], "inconclusive")

    def check_ordering_uncertainty_and_corrections_never_rewrite_facts(self):
        latest = self.source(source_revision="10", revision_order="10")
        self.ingest([latest])
        for revision in ("3", "10"):
            self.ingest([self.source(source_revision=revision, revision_order=revision, event_type="reopened")])
        self.ingest([self.source(source_revision=None, ordering_domain=None, revision_order=None)])
        self.ingest([self.source(source_revision="2026-08-30T12:00:00Z", ordering_domain="source_updated_at_v1",
                                revision_order=str(int(datetime(2026, 8, 30, 12, tzinfo=timezone.utc).timestamp()) * 1000000))])
        current = self.repository.current(self.principal, "github-one", "101")
        self.assertEqual(current["source_event_id"], latest["source_event_id"])
        corrected = self.source(source_revision="11", revision_order="11", event_type="reopened",
                                supersedes_source_event_id=latest["source_event_id"], reason_code="corrected")
        original_row = self.outcome_rows()["portfolio_outcome_events"][0]
        self.ingest([corrected])
        self.assertIn(original_row, self.outcome_rows()["portfolio_outcome_events"])
        self.assertEqual(self.repository.current(self.principal, "github-one", "101")["source_event_id"], corrected["source_event_id"])
        stale = self.source(source_revision="12", revision_order="12", supersedes_source_event_id=latest["source_event_id"], reason_code="corrected")
        self.error("version_conflict", lambda: self.ingest([stale]))
        tombstone = self.source(source_revision="12", revision_order="12", event_type="deleted", state="tombstoned",
                                supersedes_source_event_id=corrected["source_event_id"], reason_code="tombstoned")
        self.ingest([tombstone])
        self.assertEqual(self.repository.current(self.principal, "github-one", "101")["state"], "tombstoned")
        states = {item["state"] for item in self.repository.coverage(self.principal)}
        self.assertTrue({"observed", "late", "ambiguous", "excluded"}.issubset(states))

    def check_batch_ordering_and_unsupported_do_not_replace_authoritative_state(self):
        first = self.source(source_event_id="ffffffff-ffff-4fff-8fff-ffffffffffff", source_revision="1", revision_order="1")
        latest = self.source(source_event_id="11111111-1111-4111-8111-111111111111", source_revision="10", revision_order="10")
        late = self.source(source_event_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", source_revision="5", revision_order="5")
        self.ingest([first, latest, late])
        self.assertEqual(self.repository.current(self.principal, "github-one", "101")["source_event_id"], latest["source_event_id"])
        self.assertEqual(self.repository.context(self.principal, "github-one", late["source_event_id"])["ordering_state"], "late")
        self.ingest([self.source(source_revision="99", revision_order="99", event_type="unsupported", reason_code="unsupported")])
        self.assertEqual(self.repository.current(self.principal, "github-one", "101")["source_event_id"], latest["source_event_id"])
        self.ingest([self.source(source_revision="100", revision_order="100", object_type="pull_request")])
        self.assertEqual(self.repository.current(self.principal, "github-one", "101")["source_event_id"], latest["source_event_id"])

    def check_historical_binding_and_missing_source_time(self):
        before_version = self.source()
        changed = self.service.dispatch(ADMIN, "POST", SCOPES + "/" + self.scope["work_scope_id"] + "/versions",
                                       body=canonical(version_request(self.scope)).encode(), idempotency_key="scope-advance")[1]
        self.ingest([before_version])
        context = self.repository.context(self.principal, "github-one", before_version["source_event_id"])
        self.assertEqual((context["work_scope_version"], context["scope_state"]), (1, "matched"))
        after_version = self.source(source_revision="5", revision_order="5")
        self.ingest([after_version])
        self.assertEqual(self.repository.context(self.principal, "github-one", after_version["source_event_id"])["scope_state"], "excluded")
        for source in (
            self.source(container_id="789"),
            self.source(event_at=None),
            self.source(event_at="2020-01-01T00:00:00Z"),
        ):
            self.ingest([source])
            self.assertEqual(self.repository.context(self.principal, "github-one", source["source_event_id"])["scope_state"], "unmatched")
        self.assertEqual(self.repository.context(self.principal, "github-one", before_version["source_event_id"]), context)
        self.assertEqual(changed["version"], 2)

    def check_atomic_failure_and_no_read_before_audit(self):
        source, delivery = self.source(), str(uuid4())
        before = self.outcome_rows()
        insert = PortfolioSQL.insert
        def fail(sql, table, row):
            if table == "portfolio_outcome_contexts":
                raise RuntimeError("synthetic-transaction-failure")
            return insert(sql, table, row)
        with mock.patch.object(PortfolioSQL, "insert", fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic-transaction-failure"):
                self.ingest([source], delivery=delivery)
        self.assertEqual(self.outcome_rows(), before)
        self.ingest([source], delivery=delivery)
        before = self.outcome_rows()
        with mock.patch.object(self.repository, "_audit", side_effect=RuntimeError("synthetic-audit-failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic-audit-failure"):
                self.page()
        self.assertEqual(self.outcome_rows(), before)
        with mock.patch.object(self.repository, "_event", return_value={"schema_id": "hormuz.work-outcome-event", "title": "SYNTHETIC_EXCLUDED"}):
            self.error("unavailable", self.page)
        self.assertEqual(self.outcome_rows(), before)

    def check_failed_delivery_is_bounded_content_free_and_not_success(self):
        delivery, raw = str(uuid4()), b'{"title":"SYNTHETIC_EXCLUDED","observations":[{}]}'
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), self.assertNoLogs():
            self.error("invalid_request", lambda: self.ingest(delivery=delivery, raw=raw))
        self.assertEqual(output.getvalue(), "")
        before = self.outcome_rows()
        self.assertEqual(len(before["portfolio_outcome_dead_letters"]), 1)
        self.assertEqual(before["portfolio_outcome_receipts"], [])
        self.assertEqual(before["portfolio_outcome_events"], [])
        self.assertNotIn("SYNTHETIC_EXCLUDED", canonical(before))
        self.error("invalid_request", lambda: self.ingest(delivery=delivery, raw=raw))
        self.assertEqual(self.outcome_rows(), before)
        self.error("idempotency_conflict", lambda: self.ingest(delivery=delivery, raw=raw + b" "))
        self.assertEqual(self.outcome_rows(), before)
        coverage = self.repository.coverage(self.principal)[0]
        self.assertEqual((coverage["state"], coverage["member_unit"], coverage["member_count"]), ("failed", "delivery", 1))

    def check_repository_rejections_have_durable_failure_coverage(self):
        original = self.source()
        self.ingest([original])
        accepted = self.outcome_rows()["portfolio_outcome_events"]
        for source, code in (
            (original, "idempotency_conflict"),
            (self.source(supersedes_source_event_id=str(uuid4()), reason_code="corrected"), "not_found"),
        ):
            delivery, raw = str(uuid4()), canonical({"observations": [source]}).encode()
            self.error(code, lambda: self.ingest(delivery=delivery, raw=raw))
            before = self.outcome_rows()
            self.error(code, lambda: self.ingest(delivery=delivery, raw=raw))
            self.assertEqual(self.outcome_rows(), before)
        rows = self.outcome_rows()
        self.assertEqual(rows["portfolio_outcome_events"], accepted)
        self.assertEqual(len(rows["portfolio_outcome_receipts"]), 1)
        self.assertEqual(len(rows["portfolio_outcome_dead_letters"]), 2)
        self.assertEqual(len([row for row in self.repository.coverage(self.principal) if row["state"] == "failed"]), 2)

    def check_corrupt_cursors_fail_without_audit_or_partial_pages(self):
        self.ingest([self.source(source_revision=str(i), revision_order=str(i)) for i in (1, 2, 3)])
        cursor = self.page("limit=1")["next_cursor"]
        before, one = self.outcome_rows(), PortfolioSQL.one
        for change in (
            {"filters_json": '{"work_scope_id":123}'},
            {"filters_json": '{"connector_id":"other"}'},
            {"snapshot_sequence": 0},
            {"after_id": str(uuid4())},
            {"after_at": "2020-01-01T00:00:00.000000Z"},
        ):
            def corrupt(sql, statement, values=()):
                row = one(sql, statement, values)
                return {**row, **change} if "SELECT * FROM portfolio_outcome_cursors" in statement else row
            with self.subTest(change=change), mock.patch.object(PortfolioSQL, "one", corrupt):
                self.error("unavailable", lambda: self.page("cursor=" + cursor))
            self.assertEqual(self.outcome_rows(), before)

    def check_storage_outage_never_accepts_or_retries(self, unavailable):
        before, adapter = self.outcome_rows(), mock.Mock(wraps=SyntheticOutcomeAdapter())
        ingestor = OutcomeIngestor(self.config, unavailable, "acme", "github-one", adapter, self.keys)
        self.error("unavailable", lambda: self.ingest(ingestor=ingestor))
        self.assertEqual(adapter.verify.call_count, 1)
        adapter.normalize.assert_not_called()
        self.error("unavailable", lambda: unavailable.execute(self.principal, "list_outcomes", path=OUTCOMES,
                                                              scope_id=None, query={}, body=None, idempotency_key=None))
        self.assertEqual(self.outcome_rows(), before)

    def check_connector_ties_and_config_or_role_changes(self):
        second = replace(self.config.portfolio_control.connectors[0], connector_id="github-two")
        config = replace(self.config, portfolio_control=replace(self.config.portfolio_control,
                         connectors=(*self.config.portfolio_control.connectors, second)))
        group = create_portfolio_repository(config, environ=self.environment)
        service = PortfolioService(config, group)
        other = OutcomeIngestor(config, group.outcomes, "acme", "github-two", SyntheticOutcomeAdapter(), self.keys)
        source = self.source()
        self.ingest([source])
        self.ingest([source], ingestor=other)
        first = service.dispatch(ADMIN, "GET", OUTCOMES, query="limit=1")[1]
        second_page = service.dispatch(ADMIN, "GET", OUTCOMES, query="cursor=" + first["next_cursor"])[1]
        self.assertEqual([first["items"][0]["connector_id"], second_page["items"][0]["connector_id"]], ["github-two", "github-one"])
        self.assertIsNone(second_page["next_cursor"])
        # Same actor and tenant do not authorize a cursor under changed source
        # registration, role membership, or after its absolute expiry.
        self.error("cursor_invalid", lambda: self.page("cursor=" + first["next_cursor"]))
        principal = replace(self.principal, roles=("portfolio_admin", "finance_viewer"))
        roles = tuple(replace(item, roles=principal.roles) if item.actor_id == "alice" else item
                      for item in config.portfolio_control.role_bindings)
        changed = replace(config, portfolio_control=replace(config.portfolio_control, role_bindings=roles))
        changed_service = PortfolioService(changed, create_portfolio_repository(changed, environ=self.environment))
        self.error("cursor_invalid", lambda: changed_service.dispatch(ADMIN, "GET", OUTCOMES, query="cursor=" + first["next_cursor"]))
        self.clock_instant += timedelta(hours=2)
        self.error("cursor_invalid", lambda: service.dispatch(ADMIN, "GET", OUTCOMES, query="cursor=" + first["next_cursor"]))

    def check_authorized_retention_is_separate_append_only_and_invalidates_cursors(self):
        sources = [self.source(source_revision=str(i), revision_order=str(i)) for i in (1, 2, 3)]
        self.ingest(sources)
        cursor = self.page("limit=1")["next_cursor"]
        before_legacy = self.legacy_rows()
        source_rows = self.outcome_rows()["portfolio_outcome_events"]
        target = sources[-1]["source_event_id"]
        marker = self.repository.tombstone(self.principal, "github-one", target, idempotency_key="retention-test", keys=self.keys)
        self.assertEqual((marker["schema_id"], marker["source_event_id"], marker["actor_id"], marker["reason_code"]),
                         ("hormuz.outcome-retention-event", target, "alice", "tombstoned"))
        before = self.outcome_rows()
        self.assertEqual(self.repository.tombstone(self.principal, "github-one", target, idempotency_key="retention-test", keys=self.keys), marker)
        self.assertEqual(self.outcome_rows(), before)
        self.error("idempotency_conflict", lambda: self.repository.tombstone(
            self.principal, "github-one", sources[0]["source_event_id"], idempotency_key="retention-test", keys=self.keys))
        self.assertEqual(self.outcome_rows()["portfolio_outcome_events"], source_rows)
        self.assertEqual(self.legacy_rows(), before_legacy)
        self.assertIsNone(self.repository.current(self.principal, "github-one", "101"))
        self.assertNotIn(target, {item["source_event_id"] for item in self.page()["items"]})
        self.error("cursor_invalid", lambda: self.page("cursor=" + cursor))
        self.error("not_found", lambda: self.repository.tombstone(
            self.service.authenticate(OTHER), "github-one", target, idempotency_key="foreign", keys=self.keys))
        # Cleanup remains possible after connector disablement; it is scoped by
        # the administrator and original tenant-owned fact, not new ingestion.
        disabled = replace(self.config, portfolio_control=replace(self.config.portfolio_control, connectors=()))
        repository = create_portfolio_repository(disabled, environ=self.environment).outcomes
        self.assertEqual(repository.tombstone(self.principal, "github-one", sources[0]["source_event_id"],
                                             idempotency_key="disabled-cleanup", keys=self.keys)["reason_code"], "tombstoned")

    def check_retention_replay_rejects_corrupt_mac_and_supports_rotation(self):
        source = self.source()
        self.ingest([source])
        def retain(keys=self.keys):
            return self.repository.tombstone(self.principal, "github-one", source["source_event_id"],
                                             idempotency_key="retention-integrity", keys=keys)
        marker = retain()
        before, one = self.outcome_rows(), PortfolioSQL.one
        def corrupt(sql, statement, values=()):
            row = one(sql, statement, values)
            return {**row, "request_mac": "\u00e9" * 64} if "SELECT * FROM portfolio_outcome_retention_events" in statement else row
        with mock.patch.object(PortfolioSQL, "one", corrupt):
            self.error("unavailable", retain)
        self.assertEqual(self.outcome_rows(), before)
        self.assertEqual(retain(OutcomeKeys("key-v2", {"key-v1": b"a" * 32, "key-v2": b"b" * 32})), marker)
        self.error("unavailable", lambda: retain(OutcomeKeys("key-v2", {"key-v2": b"b" * 32})))
        self.assertEqual(self.outcome_rows(), before)

    def check_authorization_and_tenant_isolation_before_lookup(self):
        source = self.source()
        self.ingest([source])
        with mock.patch.object(self.repository, "_transaction", side_effect=AssertionError("denied-storage")):
            for token, code in (("bad", "unauthenticated"), (VIEWER, "forbidden")):
                self.error(code, lambda token=token: self.page(token=token))
        self.assertEqual(self.page(token=OTHER)["items"], [])
        self.error("not_found", lambda: self.repository.context(self.service.authenticate(OTHER), "github-one", source["source_event_id"]))
        self.error("forbidden", lambda: self.page("connector_id=github-other"))
        self.error("invalid_request", lambda: self.page("organization_id=beta"))

    def check_concurrent_replicas_and_frozen_pagination(self):
        sources = [self.source(source_revision=str(i), revision_order=str(i)) for i in range(1, 4)]
        barrier, delivery = threading.Barrier(4), str(uuid4())
        raw = canonical({"observations": sources}).encode()
        def receive(_):
            # Independent repository instances share only the database lock.
            repository = create_portfolio_repository(self.config, environ=self.environment).outcomes
            ingestor = OutcomeIngestor(self.config, repository, "acme", "github-one", SyntheticOutcomeAdapter(), self.keys)
            barrier.wait(timeout=10)
            return self.ingest(delivery=delivery, raw=raw, ingestor=ingestor)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(receive, range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.outcome_rows()["portfolio_outcome_receipts"]), 1)
        first = self.page("limit=1")
        self.ingest([self.source(source_revision="5", revision_order="5")])
        seen, cursor = first["items"][:], first["next_cursor"]
        while cursor:
            page = self.page("limit=1&cursor=" + cursor)
            self.assertEqual(page["as_of"], first["as_of"])
            seen.extend(page["items"])
            cursor = page["next_cursor"]
        self.assertEqual({event["source_event_id"] for event in seen}, {source["source_event_id"] for source in sources})
        self.error("cursor_invalid", lambda: self.page("cursor=" + first["next_cursor"], token=OTHER))
        self.error("cursor_invalid", lambda: self.page("cursor=" + first["next_cursor"] + "&connector_id=github-one"))
        self.error("cursor_invalid", lambda: self.page("cursor=forged"))
