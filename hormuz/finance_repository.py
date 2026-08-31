"""Tenant-authorized durable rate cards beside the unchanged v1 usage owner.

This internal owner accepts a principal already authenticated by the caller,
then rechecks its exact configured administrative binding before I/O, under
the tenant lock and before commit. It registers no HTTP/CLI entrypoint, imports
no provider data, and never applies a rate card to a past or current request.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hmac
import os
import re
from typing import Mapping
from uuid import uuid4

from ._finance_schema import TABLE_DDL
from ._portfolio_sql import portfolio_transaction
from .config import GatewayConfig
from .finance_rate_cards import RateCard
from .finance_values import FinanceValueError
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import PortfolioError
from .postgres import POSTGRES_SCHEMA_VERSION, PostgresConnectionPool


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RECEIPT = re.compile(r"[0-9a-f]{32}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z")
_ERRORS = frozenset({"forbidden", "invalid_request", "rate_card_conflict", "not_found", "unavailable"})


class FinanceRepositoryError(RuntimeError):
    """Fixed diagnostics only: no input, SQL, credentials or stored row text."""

    def __init__(self, code: str):
        self.code = code if code in _ERRORS else "unavailable"
        super().__init__(self.code)


@dataclass(frozen=True)
class RateCardRegistration:
    """Internal immutable receipt, not a newly registered public wire schema."""

    card: RateCard
    receipt_id: str
    registered_by: str
    registered_at: str
    sequence: int


class FinanceRateCardRepository:
    def __init__(self, config: GatewayConfig, *, dsn: str, connection_pool: PostgresConnectionPool | None = None,
                 read_only: bool = False):
        self.config, self._dsn, self._pool, self._read_only = config, dsn, connection_pool, read_only

    def _authorize(self, principal: PortfolioPrincipal) -> None:
        control = self.config.portfolio_control
        if (self._read_only or control is None or type(principal) is not PortfolioPrincipal
                or principal.organization_id not in self.config.organization_ids):
            raise FinanceRepositoryError("forbidden")
        if not any((item.organization_id, item.actor_id, item.roles) ==
                   (principal.organization_id, principal.actor_id, principal.roles) and "portfolio_admin" in item.roles
                   for item in control.role_bindings):
            raise FinanceRepositoryError("forbidden")

    @contextmanager
    def _transaction(self, principal: PortfolioPrincipal):
        self._authorize(principal)
        try:
            with portfolio_transaction(self.config, principal.organization_id, dsn=self._dsn, connection_pool=self._pool,
                                       tables=TABLE_DDL, statement_timeout_ms=5000) as sql:
                self._authorize(principal)
                if sql.postgres:
                    # This owner can be constructed independently of the v1
                    # store. Check the exact ledger inside the same transaction,
                    # not only at gateway startup or in a separate connection.
                    rows = sql.execute("SELECT version, state FROM hormuz_schema_migrations ORDER BY version LIMIT ?",
                                       (POSTGRES_SCHEMA_VERSION + 1,)).fetchall()
                    if [(row["version"], row["state"]) for row in rows] != [
                            (version, "applied") for version in range(1, POSTGRES_SCHEMA_VERSION + 1)]:
                        raise FinanceRepositoryError("unavailable")
                yield sql
                self._authorize(principal)
        except PortfolioError:
            raise FinanceRepositoryError("unavailable") from None

    @staticmethod
    def _identity(card_id, version):
        if (type(card_id) is not str or not _ID.fullmatch(card_id)
                or type(version) is not int or not 1 <= version <= 2147483647):
            raise FinanceRepositoryError("invalid_request")

    @staticmethod
    def _row(sql, organization, card_id, version):
        return sql.one("SELECT * FROM portfolio_finance_rate_cards WHERE organization_id=? AND rate_card_id=? AND version=?",
                       (organization, card_id, version))

    @staticmethod
    def _registration(sql, row) -> RateCardRegistration:
        # Independently bind canonical content, indexed identity and original
        # registration audit before returning either a read or retry receipt.
        try:
            card = RateCard(row["card_json"])
            body = card.as_mapping()
            if (any(row[name] != body[name] for name in ("organization_id", "rate_card_id", "version"))
                    or type(row["content_digest"]) is not str
                    or not hmac.compare_digest(card.content_digest, row["content_digest"])
                    or type(row["receipt_id"]) is not str or not _RECEIPT.fullmatch(row["receipt_id"])
                    or type(row["registered_by"]) is not str or not _ID.fullmatch(row["registered_by"])
                    or type(row["sequence"]) is not int or not 1 <= row["sequence"] <= 9223372036854775807
                    or type(row["registered_at"]) is not str or not _TIME.fullmatch(row["registered_at"])):
                raise FinanceRepositoryError("unavailable")
            datetime.fromisoformat(row["registered_at"])
            audit = sql.one("SELECT * FROM portfolio_finance_audit_events WHERE organization_id=? AND sequence=?",
                            (row["organization_id"], row["sequence"]))
            expected = {"organization_id": row["organization_id"], "event_id": row["receipt_id"], "sequence": row["sequence"],
                        "actor_id": row["registered_by"], "operation": "register", "rate_card_id": row["rate_card_id"],
                        "version": row["version"], "content_digest": row["content_digest"], "occurred_at": row["registered_at"]}
            if audit != expected:
                raise FinanceRepositoryError("unavailable")
            return RateCardRegistration(card, row["receipt_id"], row["registered_by"], row["registered_at"], row["sequence"])
        except (FinanceValueError, KeyError, TypeError, ValueError, OverflowError):
            raise FinanceRepositoryError("unavailable") from None

    @staticmethod
    def _audit(sql, principal, operation, card, event_id, now):
        sequence = int(sql.one("SELECT COALESCE(MAX(sequence),0) AS sequence FROM portfolio_finance_audit_events WHERE organization_id=?",
                               (principal.organization_id,))["sequence"]) + 1
        if not 1 <= sequence <= 9223372036854775807:
            raise FinanceRepositoryError("unavailable")
        body = card.as_mapping()
        sql.insert("portfolio_finance_audit_events", {
            "organization_id": principal.organization_id, "event_id": event_id, "sequence": sequence,
            "actor_id": principal.actor_id, "operation": operation, "rate_card_id": body["rate_card_id"],
            "version": body["version"], "content_digest": card.content_digest, "occurred_at": now,
        })
        return sequence

    def register_rate_card(self, principal: PortfolioPrincipal, card: RateCard) -> RateCardRegistration:
        """Bind tenant/card/version to canonical content; exact retry is read-only.

        The natural identity is the idempotency key. It never points to a newer
        version. A different authorized admin retry still gets the original
        receipt/actor, not a new registration or a claim of fresh attribution.
        """
        self._authorize(principal)
        if type(card) is not RateCard:
            raise FinanceRepositoryError("invalid_request")
        try:
            card = RateCard(card._canonical)  # Snapshot and revalidate caller-owned input.
        except (FinanceValueError, AttributeError):
            raise FinanceRepositoryError("invalid_request") from None
        body = card.as_mapping()
        if body["organization_id"] != principal.organization_id:
            raise FinanceRepositoryError("forbidden")
        with self._transaction(principal) as sql:
            self._authorize(principal)
            row = self._row(sql, principal.organization_id, body["rate_card_id"], body["version"])
            if row is not None:
                result = self._registration(sql, row)
                if result.card != card:
                    raise FinanceRepositoryError("rate_card_conflict")
            else:
                now, receipt_id = sql.now(), uuid4().hex
                sequence = self._audit(sql, principal, "register", card, receipt_id, now)
                row = {"organization_id": principal.organization_id, "rate_card_id": body["rate_card_id"],
                       "version": body["version"], "card_json": card._canonical, "content_digest": card.content_digest,
                       "receipt_id": receipt_id, "registered_by": principal.actor_id, "registered_at": now, "sequence": sequence}
                sql.insert("portfolio_finance_rate_cards", row)
                result = self._registration(sql, row)
        return result

    def get_rate_card(self, principal: PortfolioPrincipal, *, card_id: str, version: int) -> RateCardRegistration:
        """Deliver one exact version only after its read audit commits."""
        self._authorize(principal)
        self._identity(card_id, version)
        with self._transaction(principal) as sql:
            self._authorize(principal)
            row = self._row(sql, principal.organization_id, card_id, version)
            if row is None:
                raise FinanceRepositoryError("not_found")
            result = self._registration(sql, row)
            self._audit(sql, principal, "read", result.card, uuid4().hex, sql.now())
        return result


def create_finance_repository(config: GatewayConfig, *, environ: Mapping[str, str] | None = None,
                              connection_pool: PostgresConnectionPool | None = None,
                              read_only: bool = False) -> FinanceRateCardRepository:
    """RepositoryFactory-compatible construction; no I/O, migration or pool ownership."""
    storage = config.usage_storage
    dsn = ""
    if storage.backend == "postgresql":
        environment = os.environ if environ is None else environ
        dsn = environment.get(storage.postgres_dsn_env, "")
        if not dsn:
            raise FinanceRepositoryError("unavailable")
    elif storage.backend != "sqlite":
        raise FinanceRepositoryError("unavailable")
    return FinanceRateCardRepository(config, dsn=dsn, connection_pool=connection_pool, read_only=read_only)
