"""Synthetic, provider-free durable rate-card assertions for both adapters."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import threading
from unittest import mock

from hormuz._portfolio_sql import PortfolioSQL
from hormuz.finance_rate_cards import RateCard, estimate_usage, rate_card_from_mapping
from hormuz.finance_repository import FinanceRateCardRepository, FinanceRepositoryError, create_finance_repository
from hormuz.finance_usage import normalize_provider_usage
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.portfolio_wire import PortfolioError

if __package__:
    from ._finance_values_fixture import estimate_context, openai_usage, rate_card
else:
    from _finance_values_fixture import estimate_context, openai_usage, rate_card


ADMIN = PortfolioPrincipal("acme", "alice", ("portfolio_admin",))
OTHER = PortfolioPrincipal("beta", "bob", ("portfolio_admin",))
VIEWER = PortfolioPrincipal("acme", "finance", ("finance_viewer",))
CARDS = "portfolio_finance_rate_cards"
AUDIT = "portfolio_finance_audit_events"


def seed_finance(config, *, environ=None):
    """Populate every finance table and retain an exact replay target."""
    repository = create_finance_repository(config, environ=environ)
    card = rate_card_from_mapping(rate_card())
    receipt = repository.register_rate_card(ADMIN, card)
    repository.get_rate_card(ADMIN, card_id="synthetic-rate-card", version=1)
    return receipt


class FinanceAssertions:
    def setup_finance(self):
        self.repository = create_finance_repository(self.config, environ=self.environment)

    def error(self, code, action):
        with self.assertRaises(FinanceRepositoryError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn("SYNTHETIC_EXCLUDED", str(caught.exception))

    def register(self, *, principal=ADMIN, repository=None, **changes):
        card = rate_card_from_mapping({**rate_card(), **changes})
        return (repository or self.repository).register_rate_card(principal, card)

    def get(self, *, principal=ADMIN, repository=None, version=1, card_id="synthetic-rate-card"):
        return (repository or self.repository).get_rate_card(principal, card_id=card_id, version=version)

    def test_rate_card_receipt_replay_restart_and_history_are_immutable(self):
        legacy = self.legacy_rows()
        first = self.register()
        rows = self.finance_rows()
        self.assertEqual(len(rows[CARDS]), 1)
        self.assertEqual(len(rows[AUDIT]), 1)
        self.assertEqual(first.card, rate_card_from_mapping(rate_card()))
        self.assertEqual(first.registered_by, "alice")
        self.assertEqual(first.sequence, 1)
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            first.receipt_id = "changed"
        canonical_retry = dict(reversed(list(rate_card().items())))
        canonical_retry["rates"]["output"] = "8.000"
        restarted = create_finance_repository(self.config, environ=self.environment)
        self.assertEqual(restarted.register_rate_card(ADMIN, rate_card_from_mapping(canonical_retry)), first)
        self.assertEqual(self.finance_rows(), rows)
        usage = normalize_provider_usage("openai", openai_usage())
        original = estimate_usage(first.card, usage, **estimate_context())
        second = self.register(version=2, rates={**rate_card()["rates"], "output": "10"})
        self.assertNotEqual(second.card.content_digest, first.card.content_digest)
        self.assertEqual(self.get(repository=restarted), first)
        self.assertEqual(estimate_usage(self.get().card, usage, **estimate_context()), original)
        self.assertEqual(original.amount, "0.00315")
        self.assertEqual(estimate_usage(second.card, usage, **estimate_context()).amount, "0.00355")
        self.assertEqual(self.legacy_rows(), legacy)
        self.assertEqual([row["operation"] for row in self.finance_rows()[AUDIT]], ["register", "register", "read", "read"])

    def test_rate_card_conflicting_identity_fails_and_explicit_versions_have_no_latest_alias(self):
        self.register()
        before = self.finance_rows()
        for change in ({"currency": "EUR"}, {"actual_model": "different"}, {"rates": {**rate_card()["rates"], "output": "9"}}):
            self.error("rate_card_conflict", lambda change=change: self.register(**change))
        self.assertEqual(self.finance_rows(), before)
        self.register(version=9)
        self.register(version=3)
        self.assertEqual(self.get(version=3).card.version, 3)
        for version in (None, True, 0, -1, 2147483648, "latest", "1", [], {}):
            self.error("invalid_request", lambda version=version: self.get(version=version))
        for identifier in (None, "", "x" * 129, "bad\nSYNTHETIC_EXCLUDED", "';DROP TABLE x", [], {}):
            self.error("invalid_request", lambda identifier=identifier: self.get(card_id=identifier))
        self.error("not_found", lambda: self.get(version=2))

    def test_rate_card_authorization_precedes_storage_and_body_is_not_authority(self):
        invalid = (None, object(), VIEWER, PortfolioPrincipal("acme", "alice", ("portfolio_admin", "finance_viewer")),
                   PortfolioPrincipal("unknown", "alice", ("portfolio_admin",)))
        with mock.patch("hormuz.finance_repository.portfolio_transaction", side_effect=AssertionError("must not connect")):
            for principal in invalid:
                self.error("forbidden", lambda principal=principal: self.register(principal=principal))
                self.error("forbidden", lambda principal=principal: self.get(principal=principal))
            self.error("forbidden", lambda: self.register(organization_id="beta"))
            disabled = create_finance_repository(replace(self.config, portfolio_control=None), environ=self.environment)
            self.error("forbidden", lambda: self.get(repository=disabled))
            readonly = create_finance_repository(self.config, environ=self.environment, read_only=True)
            self.error("forbidden", lambda: self.register(repository=readonly))
            self.error("forbidden", lambda: self.get(repository=readonly))
            for invalid_card in ({}, None, "SYNTHETIC_EXCLUDED", object.__new__(RateCard)):
                self.error("invalid_request", lambda invalid_card=invalid_card: self.repository.register_rate_card(ADMIN, invalid_card))
            forged = rate_card_from_mapping(rate_card())
            object.__setattr__(forged, "_canonical", "SYNTHETIC_EXCLUDED")
            self.error("invalid_request", lambda: self.repository.register_rate_card(ADMIN, forged))
        first = self.register()
        self.error("not_found", lambda: self.get(principal=OTHER))
        second = self.register(principal=OTHER, organization_id="beta")
        self.assertNotEqual(first.receipt_id, second.receipt_id)
        self.assertEqual(self.get(principal=OTHER), second)
        self.assertEqual(second.sequence, 1)

    def test_rate_card_authority_is_rechecked_after_lock_and_before_commit(self):
        original = self.repository._transaction
        config = self.repository.config

        @contextmanager
        def revoked(principal):
            with original(principal) as sql:
                self.repository.config = replace(config, portfolio_control=None)
                yield sql

        try:
            with mock.patch.object(self.repository, "_transaction", revoked):
                self.error("forbidden", self.register)
        finally:
            self.repository.config = config
        self.assertEqual(self.finance_rows(), {AUDIT: [], CARDS: []})
        insert = PortfolioSQL.insert

        def revoke_during_insert(sql, table, row):
            insert(sql, table, row)
            if table == CARDS:
                self.repository.config = replace(config, portfolio_control=None)

        try:
            with mock.patch.object(PortfolioSQL, "insert", revoke_during_insert):
                self.error("forbidden", self.register)
        finally:
            self.repository.config = config
        self.assertEqual(self.finance_rows(), {AUDIT: [], CARDS: []})

    def test_rate_card_atomic_rollback_retry_and_no_read_before_audit(self):
        insert = PortfolioSQL.insert

        def fail_after_audit(sql, table, row):
            if table == CARDS:
                raise PortfolioError("unavailable")
            return insert(sql, table, row)

        with mock.patch.object(PortfolioSQL, "insert", fail_after_audit):
            self.error("unavailable", self.register)
        self.assertEqual(self.finance_rows(), {AUDIT: [], CARDS: []})
        first = self.register()
        before = self.finance_rows()
        with mock.patch.object(PortfolioSQL, "insert", side_effect=PortfolioError("unavailable")):
            self.error("unavailable", self.get)
        self.assertEqual(self.finance_rows(), before)
        self.assertEqual(self.get(), first)

    def test_rate_card_concurrent_replicas_bind_one_identity_to_one_receipt(self):
        barrier = threading.Barrier(4)

        def register(output):
            repository = create_finance_repository(self.config, environ=self.environment)
            barrier.wait(timeout=15)
            try:
                return self.register(repository=repository, rates={**rate_card()["rates"], "output": output})
            except FinanceRepositoryError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(register, ("8", "8", "9", "9")))
        accepted = [item for item in results if not isinstance(item, str)]
        self.assertEqual(len(accepted), 2)
        self.assertEqual(accepted[0], accepted[1])
        self.assertEqual(results.count("rate_card_conflict"), 2)
        rows = self.finance_rows()
        self.assertEqual(len(rows[CARDS]), 1)
        self.assertEqual(len(rows[AUDIT]), 1)
        self.assertEqual(self.get(), accepted[0])

    def test_rate_card_factory_borrows_pool_without_io_or_legacy_facade_expansion(self):
        from hormuz.store_router import create_repository_bundle
        pool = mock.Mock()
        with mock.patch("hormuz.finance_repository.portfolio_transaction", side_effect=AssertionError("factory I/O")):
            repository = create_finance_repository(self.config, environ=self.environment, connection_pool=pool)
        self.assertIs(repository._pool, pool)
        pool.assert_not_called()
        pool.close.assert_not_called()
        self.assertFalse(hasattr(self.store, "register_rate_card"))
        with mock.patch("hormuz.store_router.create_usage_store", return_value=self.store):
            bundle = create_repository_bundle(self.config, portfolio_factory=create_finance_repository,
                                             environ=self.environment, connection_pool=pool)
        self.assertIs(bundle.usage, self.store)
        self.assertIsInstance(bundle.portfolio, FinanceRateCardRepository)
        self.assertIs(bundle.portfolio._pool, pool)
