from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Identity


@dataclass(frozen=True)
class MonthlyTotals:
    requests: int = 0
    denied_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_microusd: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        return self.cost_microusd / 1_000_000


class UsageStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS gateway_usage_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT NOT NULL,
                    client TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    requested_model TEXT NOT NULL,
                    resolved_alias TEXT,
                    upstream_model TEXT,
                    policy_action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_microusd INTEGER NOT NULL DEFAULT 0,
                    provider_request_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_usage_occurred_at
                    ON gateway_usage_events(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_gateway_usage_actor_month
                    ON gateway_usage_events(actor_id, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_gateway_usage_team_month
                    ON gateway_usage_events(team_id, occurred_at);
                """
            )

    def record(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        resolved_alias: str | None,
        upstream_model: str | None,
        policy_action: str,
        status: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost_microusd: int = 0,
        provider_request_id: str | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO gateway_usage_events (
                    id, occurred_at, actor_id, actor_name, team_id, team_name, client, protocol,
                    requested_model, resolved_alias, upstream_model, policy_action, status,
                    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                    reasoning_tokens, cost_microusd, provider_request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    datetime.now(timezone.utc).isoformat(),
                    identity.actor_id,
                    identity.actor_name,
                    identity.team_id,
                    identity.team_name,
                    client,
                    protocol,
                    requested_model,
                    resolved_alias,
                    upstream_model,
                    policy_action,
                    status,
                    max(0, input_tokens),
                    max(0, output_tokens),
                    max(0, cache_read_tokens),
                    max(0, cache_write_tokens),
                    max(0, reasoning_tokens),
                    max(0, cost_microusd),
                    provider_request_id,
                ),
            )
        return event_id

    def monthly_totals(self, *, actor_id: str | None = None, team_id: str | None = None) -> MonthlyTotals:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        clauses = ["occurred_at >= ?"]
        parameters: list[object] = [start]
        if actor_id is not None:
            clauses.append("actor_id = ?")
            parameters.append(actor_id)
        if team_id is not None:
            clauses.append("team_id = ?")
            parameters.append(team_id)
        query = f"""
            SELECT
                COUNT(*) AS requests,
                SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END) AS denied_requests,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                COALESCE(SUM(cost_microusd), 0) AS cost_microusd
            FROM gateway_usage_events
            WHERE {' AND '.join(clauses)}
        """
        with self._lock, self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return MonthlyTotals(**dict(row))

    def summary_rows(self) -> list[dict[str, object]]:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT actor_id, actor_name, team_id, team_name, client, protocol,
                       COUNT(*) AS requests,
                       COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                       COALESCE(SUM(cost_microusd), 0) AS cost_microusd,
                       SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END) AS denied
                FROM gateway_usage_events
                WHERE occurred_at >= ?
                GROUP BY actor_id, actor_name, team_id, team_name, client, protocol
                ORDER BY cost_microusd DESC, tokens DESC
                """,
                (start,),
            ).fetchall()
        return [dict(row) for row in rows]
