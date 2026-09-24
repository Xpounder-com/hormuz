"""Authorized persistence for immutable, reproducible model scorecards."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from typing import Mapping
from uuid import uuid4

from ._portfolio_sql import portfolio_transaction
from ._scorecard_schema import AUDIT_TABLE, SNAPSHOT_TABLE, TABLE_DDL
from .config import GatewayConfig
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import PortfolioError, canonical, validate
from .scorecard_kernel import (
    MAX_DOCUMENT_BYTES,
    ScorecardKernelError,
    build_scorecard_evaluation,
)


class ScorecardRepository:
    """Build and store scorecards after tenant and role authorization."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        dsn: str,
        connection_pool=None,
        read_only: bool = False,
    ) -> None:
        self.config = config
        self._dsn = dsn
        self._pool = connection_pool
        self._read_only = read_only

    def _authorize(self, principal: PortfolioPrincipal) -> None:
        control = self.config.portfolio_control
        if self._read_only or control is None or not isinstance(principal, PortfolioPrincipal):
            raise PortfolioError("forbidden")
        if not any(
            (binding.organization_id, binding.actor_id, binding.roles)
            == (principal.organization_id, principal.actor_id, principal.roles)
            and "portfolio_admin" in binding.roles
            for binding in control.role_bindings
        ):
            raise PortfolioError("forbidden")

    @contextmanager
    def _transaction(self, organization_id: str):
        with portfolio_transaction(
            self.config,
            organization_id,
            dsn=self._dsn,
            connection_pool=self._pool,
            tables=TABLE_DDL,
            statement_timeout_ms=10_000,
        ) as sql:
            yield sql

    @staticmethod
    def _sequence(sql, organization_id: str) -> int:
        row = sql.one(
            "SELECT COALESCE(MAX(sequence),0) AS sequence "
            "FROM portfolio_scorecard_audit_events WHERE organization_id=?",
            (organization_id,),
        )
        sequence = int(row["sequence"]) + 1
        if sequence > 9_223_372_036_854_775_807:
            raise PortfolioError("unavailable")
        return sequence

    @staticmethod
    def _timestamp(value: object) -> datetime:
        if type(value) is not str:
            raise PortfolioError("unavailable")
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise PortfolioError("unavailable") from None

    @classmethod
    def _canonical_timestamp(cls, value: object) -> str:
        return cls._timestamp(value).astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")

    @staticmethod
    def _evaluation_digest(value: Mapping[str, object]) -> str:
        return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()

    @classmethod
    def _stored(cls, row: dict[str, object]) -> dict[str, object]:
        try:
            raw_input = json.loads(str(row["input_json"]))
            evaluation = json.loads(str(row["evaluation_json"]))
            if (
                canonical(raw_input) != row["input_json"]
                or canonical(evaluation) != row["evaluation_json"]
                or cls._evaluation_digest(evaluation) != row["evaluation_digest"]
                or build_scorecard_evaluation(raw_input) != evaluation
                or evaluation["lineage"]["input_digest"] != row["input_digest"]
            ):
                raise ValueError
            scorecard = evaluation["scorecard"]
            validate(scorecard, "hormuz.model-scorecard")
            if (
                scorecard["organization_id"] != row["organization_id"]
                or scorecard["scorecard_id"] != row["scorecard_id"]
                or scorecard["version"] != row["version"]
                or scorecard["work_scope"]["work_scope_id"] != row["work_scope_id"]
                or scorecard["work_scope"]["version"] != row["work_scope_version"]
                or scorecard["state"] != row["state"]
                or scorecard["evidence_level"] != row["evidence_level"]
            ):
                raise ValueError
        except (
            KeyError,
            PortfolioError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ScorecardKernelError,
        ):
            raise PortfolioError("unavailable") from None
        return evaluation

    def build(
        self,
        principal: PortfolioPrincipal,
        value: Mapping[str, object],
    ) -> dict[str, object]:
        """Build or replay one exact immutable scorecard snapshot."""

        # Authorization intentionally precedes input parsing and storage.
        self._authorize(principal)
        try:
            evaluation = build_scorecard_evaluation(value)
            input_json = canonical(value)
            evaluation_json = canonical(evaluation)
            if max(len(input_json), len(evaluation_json)) > MAX_DOCUMENT_BYTES:
                raise ScorecardKernelError()
            scorecard = evaluation["scorecard"]
            validate(scorecard, "hormuz.model-scorecard")
        except (ScorecardKernelError, PortfolioError, TypeError, ValueError):
            raise PortfolioError("invalid_request") from None
        if scorecard["organization_id"] != principal.organization_id:
            raise PortfolioError("not_found")

        organization = principal.organization_id
        scorecard_id = scorecard["scorecard_id"]
        version = scorecard["version"]
        scope = scorecard["work_scope"]
        input_digest = evaluation["lineage"]["input_digest"]
        evaluation_digest = self._evaluation_digest(evaluation)
        source_set_digest = hashlib.sha256(
            canonical(evaluation["lineage"]["source_digests"]).encode("ascii")
        ).hexdigest()

        with self._transaction(organization) as sql:
            now = sql.now()
            if self._timestamp(scorecard["generated_at"]) > self._timestamp(now):
                raise PortfolioError("invalid_request")
            exact = sql.one(
                "SELECT * FROM portfolio_model_scorecard_snapshots "
                "WHERE organization_id=? AND scorecard_id=? AND version=?",
                (organization, scorecard_id, version),
            )
            if exact is not None:
                # Replay identity follows the kernel's normalized digest.  Raw
                # list order is retained for provenance but cannot turn the
                # same semantic evidence into a version conflict.
                if exact["input_digest"] != input_digest:
                    raise PortfolioError("version_conflict")
                return self._stored(exact)

            stored_scope = sql.one(
                "SELECT kind,state FROM portfolio_work_scope_versions "
                "WHERE organization_id=? AND work_scope_id=? AND version=?",
                (organization, scope["work_scope_id"], scope["version"]),
            )
            if stored_scope is None or stored_scope["kind"] != "use_case":
                raise PortfolioError("not_found")
            if stored_scope["state"] != "active":
                raise PortfolioError("version_conflict")

            latest = sql.one(
                "SELECT version,work_scope_id,work_scope_version FROM "
                "portfolio_model_scorecard_snapshots WHERE organization_id=? "
                "AND scorecard_id=? ORDER BY version DESC LIMIT 1",
                (organization, scorecard_id),
            )
            supersedes = scorecard["supersedes_version"]
            if latest is None:
                if version != 1 or supersedes is not None:
                    raise PortfolioError("version_conflict")
            elif (
                version != int(latest["version"]) + 1
                or supersedes != latest["version"]
                or latest["work_scope_id"] != scope["work_scope_id"]
                or latest["work_scope_version"] != scope["version"]
            ):
                raise PortfolioError("version_conflict")

            sequence = self._sequence(sql, organization)
            reason = "eligible" if scorecard["state"] == "eligible" else "missing_evidence"
            sql.insert(AUDIT_TABLE, {
                "organization_id": organization,
                "event_id": str(uuid4()),
                "sequence": sequence,
                "actor_id": principal.actor_id,
                "operation": "build",
                "scorecard_id": scorecard_id,
                "scorecard_version": version,
                "reason_code": reason,
                "occurred_at": now,
            })
            sql.insert(SNAPSHOT_TABLE, {
                "organization_id": organization,
                "scorecard_id": scorecard_id,
                "version": version,
                "work_scope_id": scope["work_scope_id"],
                "work_scope_version": scope["version"],
                "window_start_at": self._canonical_timestamp(scorecard["window"]["start_at"]),
                "window_end_at": self._canonical_timestamp(scorecard["window"]["end_at"]),
                "evaluated_at": self._canonical_timestamp(value["evaluated_at"]),
                "generated_at": self._canonical_timestamp(scorecard["generated_at"]),
                "expires_at": self._canonical_timestamp(scorecard["expires_at"]),
                "review_after": self._canonical_timestamp(scorecard["review_after"]),
                "state": scorecard["state"],
                "evidence_level": scorecard["evidence_level"],
                "decision_owner_id": scorecard["decision_owner_id"],
                "supersedes_version": supersedes,
                "input_digest": input_digest,
                "evaluation_digest": evaluation_digest,
                "source_set_digest": source_set_digest,
                "input_json": input_json,
                "evaluation_json": evaluation_json,
                "sequence": sequence,
            })
        return evaluation
