"""Tenant-scoped AI work accounting, observational routing, and exact reuse.

This additive single-node runtime owns its own metadata ledger. It does not
replace the gateway usage/finance evidence owners or certify provider invoices.
The transport authenticates identities and authorizes plan administration.
Every operation on a work item additionally checks its tenant and actor owner.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import threading
import time
from typing import Callable, Mapping
from uuid import uuid4

from .config import Identity, ModelRoute


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}\Z")
_OBJECTIVES = frozenset({"cost", "speed", "quality"})
_STATES = frozenset({"active", "paused", "completed", "canceled", "unknown"})
_OBSERVATIONS = frozenset({"completed", "reopened", "corrected", "unknown", "canceled"})
_TERMINAL = frozenset({"succeeded", "failed", "cache_hit", "denied"})
_MAX_MONEY = 9_000_000_000_000_000
_MAX_REQUEST_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 1_048_576
_LIST_WORK_LIMIT = 10_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_work_schema (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO ai_work_schema(version) VALUES (1);
CREATE TABLE IF NOT EXISTS ai_work_plans (
 organization_id TEXT NOT NULL, scope_type TEXT NOT NULL, scope_id TEXT NOT NULL,
 version INTEGER NOT NULL, budget_microusd INTEGER, objective TEXT NOT NULL,
 updated_at REAL NOT NULL,
 PRIMARY KEY(organization_id, scope_type, scope_id)
);
CREATE TABLE IF NOT EXISTS ai_work_jobs (
 work_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 repository TEXT NOT NULL, title TEXT, task_type TEXT NOT NULL,
 context_revision TEXT, state TEXT NOT NULL, pause_reason TEXT,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL,
 cache_generation INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
 pinned_model TEXT
);
CREATE INDEX IF NOT EXISTS ai_work_jobs_owner
 ON ai_work_jobs(organization_id, actor_id, created_at);
CREATE INDEX IF NOT EXISTS ai_work_jobs_profile
 ON ai_work_jobs(organization_id, repository, task_type, created_at);
CREATE TABLE IF NOT EXISTS ai_work_attempts (
 organization_id TEXT NOT NULL, actor_id TEXT NOT NULL, request_id TEXT NOT NULL,
 work_id TEXT NOT NULL REFERENCES ai_work_jobs(work_id), logical_request_id TEXT,
 retry_of TEXT, cache_source TEXT, model TEXT NOT NULL, protocol TEXT NOT NULL,
 route_fingerprint TEXT, request_pattern TEXT, request_shape TEXT NOT NULL DEFAULT 'plain', repeat_count INTEGER NOT NULL DEFAULT 0,
 pattern_excluded INTEGER NOT NULL DEFAULT 0,
 reason TEXT NOT NULL, state TEXT NOT NULL, reserved_microusd INTEGER NOT NULL,
 cost_microusd INTEGER, latency_ms REAL, created_at REAL NOT NULL,
 settled_at REAL, reservation_exceeded INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(organization_id, actor_id, request_id)
);
CREATE INDEX IF NOT EXISTS ai_work_attempts_work ON ai_work_attempts(work_id, created_at);
CREATE TABLE IF NOT EXISTS ai_work_observations (
 observation_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
 actor_id TEXT NOT NULL, work_id TEXT NOT NULL REFERENCES ai_work_jobs(work_id),
 status TEXT NOT NULL, source TEXT NOT NULL, reference TEXT, observed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_work_observations_work
 ON ai_work_observations(work_id, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS ai_work_observation_replay
 ON ai_work_observations(organization_id, actor_id, work_id, status, source, reference)
 WHERE reference IS NOT NULL;
"""


class WorkRuntimeError(ValueError):
    """Fixed error code; never reflects request content or secret values."""

    def __init__(self, reason: str, status: int = 400):
        self.reason = reason
        self.status = status
        super().__init__("ai_work_" + reason)


def _identifier(value: object, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorkRuntimeError("invalid_" + field)
    return value


def _amount(value: object, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if type(value) is not int or not 0 <= value <= _MAX_MONEY:
        raise WorkRuntimeError("invalid_amount")
    return value


def _objective(value: object) -> str:
    aliases = {"cost-first": "cost", "speed-first": "speed", "outcome-first": "quality"}
    value = aliases.get(value, value) if isinstance(value, str) else value
    if not isinstance(value, str) or value not in _OBJECTIVES:
        raise WorkRuntimeError("invalid_objective")
    return value


def _identity(identity: Identity) -> tuple[str, str]:
    if not isinstance(identity, Identity):
        raise WorkRuntimeError("invalid_identity", 403)
    return (_identifier(identity.organization_id, "organization"),
            _identifier(identity.actor_id, "actor"))


def _timestamp(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def _month(now: float) -> tuple[float, float]:
    value = datetime.fromtimestamp(now, timezone.utc)
    start = value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start.timestamp(), end.timestamp()


class WorkRuntime:
    """Durable covered-spend ledger and bounded customer-local route selector.

    Budget administration is an operator API: the HTTP boundary must authorize
    ``set_plan``. Workspace/repository limits cover the current UTC month, job
    limits cover the job lifetime. All limits are checked in one write lock.
    Cache bodies live only in bounded process memory and require explicit opt-in.
    """

    def __init__(self, database_path: str | Path, config=None, *,
                 clock: Callable[[], float] | None = None, cache_enabled: bool = False,
                 minimum_samples: int = 5, cache_max_entries: int = 128,
                 cache_max_bytes: int = 8_388_608):
        if type(minimum_samples) is not int or not 3 <= minimum_samples <= 1000:
            raise WorkRuntimeError("invalid_minimum_samples")
        if type(cache_enabled) is not bool:
            raise WorkRuntimeError("invalid_cache_setting")
        if type(cache_max_entries) is not int or not 1 <= cache_max_entries <= 1000:
            raise WorkRuntimeError("invalid_cache_bound")
        if type(cache_max_bytes) is not int or not _MAX_RESPONSE_BYTES <= cache_max_bytes <= 64 * _MAX_RESPONSE_BYTES:
            raise WorkRuntimeError("invalid_cache_bound")
        self.path = Path(database_path)
        self.config = config
        self.clock = clock or time.time
        self.cache_enabled = cache_enabled
        self.minimum_samples = minimum_samples
        self._cache_entries = cache_max_entries
        self._cache_bound = cache_max_bytes
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._cache_size = 0
        self._cache_lock = threading.RLock()
        self._cache_key = secrets.token_bytes(32)
        self._profile_lock = threading.RLock()
        self._profile_cache: OrderedDict[tuple, dict] = OrderedDict()
        self._profile_pending: OrderedDict[tuple, tuple] = OrderedDict()
        self._profile_versions: OrderedDict[tuple, int] = OrderedDict()
        self._profile_version_counter = 0
        self._profile_worker = None
        self._closed = False
        self._initialize()

    def _now(self) -> float:
        value = self.clock()
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise WorkRuntimeError("clock_unavailable", 503)
        return float(value)

    def _initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise WorkRuntimeError("unsafe_database_path", 503)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise WorkRuntimeError("unsafe_database_path", 503)
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(_SCHEMA)
                # Additive metadata fields also support a ledger initialized by
                # the earliest opt-in development build of this same contract.
                columns = {row[1] for row in connection.execute("PRAGMA table_info(ai_work_attempts)")}
                for name, declaration in (
                    ("route_fingerprint", "TEXT"), ("request_pattern", "TEXT"),
                    ("request_shape", "TEXT NOT NULL DEFAULT 'plain'"),
                    ("repeat_count", "INTEGER NOT NULL DEFAULT 0"),
                    ("pattern_excluded", "INTEGER NOT NULL DEFAULT 0"),
                ):
                    if name not in columns:
                        connection.execute("ALTER TABLE ai_work_attempts ADD COLUMN " + name + " " + declaration)
                # A new runtime cannot prove that a previously reserved POST is
                # still progressing. Keep its full hold and label it uncertain,
                # including when an older instance eventually settles it.
                connection.execute("UPDATE ai_work_attempts SET state='unknown' WHERE state='pending'")
                if [row[0] for row in connection.execute("SELECT version FROM ai_work_schema")] != [1]:
                    raise WorkRuntimeError("schema_unavailable", 503)
        except (OSError, sqlite3.Error):
            raise WorkRuntimeError("storage_unavailable", 503) from None

    def _connect(self):
        connection = sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def verify_ready(self):
        """Probe the existing ledger without creating or initializing a file."""
        expected = {
            "ai_work_schema": ("version",),
            "ai_work_plans": ("organization_id", "scope_type", "scope_id", "version", "budget_microusd", "objective"),
            "ai_work_jobs": ("work_id", "organization_id", "actor_id", "repository", "task_type", "context_revision", "state", "cache_generation", "pinned_model"),
            "ai_work_attempts": ("organization_id", "actor_id", "request_id", "work_id", "model", "protocol", "route_fingerprint", "request_pattern", "request_shape", "state", "reserved_microusd", "cost_microusd"),
            "ai_work_observations": ("observation_id", "organization_id", "actor_id", "work_id", "status", "source"),
        }
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
                raise WorkRuntimeError("unsafe_database_path", 503)
            with closing(sqlite3.connect(self.path.absolute().as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
                for table, fields in expected.items():
                    connection.execute("SELECT " + ",".join(fields) + " FROM " + table + " LIMIT 0")
                if [row[0] for row in connection.execute("SELECT version FROM ai_work_schema")] != [1]:
                    raise WorkRuntimeError("schema_unavailable", 503)
            after = self.path.lstat()
            if (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
                raise WorkRuntimeError("storage_unavailable", 503)
            return {"status": "available", "schema_version": 1}
        except (OSError, sqlite3.Error):
            raise WorkRuntimeError("storage_unavailable", 503) from None

    @contextmanager
    def _transaction(self, *, write=False):
        connection = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise WorkRuntimeError("storage_unavailable", 503) from None
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _work(connection, owner, work_id):
        _identifier(work_id, "work")
        row = connection.execute(
            "SELECT * FROM ai_work_jobs WHERE work_id=? AND organization_id=? AND actor_id=?",
            (work_id, *owner),
        ).fetchone()
        if row is None:
            raise WorkRuntimeError("work_not_found", 404)
        return dict(row)

    def set_plan(self, organization_id, scope_type, scope_id, budget_microusd,
                 objective, expected_version=None):
        org = _identifier(organization_id, "organization")
        scope = _identifier(scope_id, "scope")
        if not isinstance(scope_type, str) or scope_type not in {"workspace", "repository", "job"}:
            raise WorkRuntimeError("invalid_scope_type")
        if scope_type == "workspace" and scope != org:
            raise WorkRuntimeError("invalid_workspace_scope")
        budget = _amount(budget_microusd, optional=True)
        objective = _objective(objective)
        if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
            raise WorkRuntimeError("invalid_version")
        now = self._now()
        with self._transaction(write=True) as connection:
            if scope_type == "job" and connection.execute(
                "SELECT 1 FROM ai_work_jobs WHERE organization_id=? AND work_id=?", (org, scope)
            ).fetchone() is None:
                raise WorkRuntimeError("work_not_found", 404)
            row = connection.execute(
                "SELECT version FROM ai_work_plans WHERE organization_id=? AND scope_type=? AND scope_id=?",
                (org, scope_type, scope),
            ).fetchone()
            current = row[0] if row else 0
            if expected_version is not None and expected_version != current:
                raise WorkRuntimeError("plan_conflict", 409)
            connection.execute(
                "INSERT INTO ai_work_plans VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(organization_id,scope_type,scope_id) DO UPDATE SET "
                "version=excluded.version,budget_microusd=excluded.budget_microusd,"
                "objective=excluded.objective,updated_at=excluded.updated_at",
                (org, scope_type, scope, current + 1, budget, objective, now),
            )
            return self._plan_view(connection, org, scope_type, scope, now)

    def create_work(self, identity, repository, title=None, task_type="general", context_revision=None):
        owner = _identity(identity)
        repository = _identifier(repository, "repository")
        task_type = _identifier(task_type, "task_type")
        revision = _identifier(context_revision, "context_revision", optional=True)
        if title is not None and (not isinstance(title, str) or not title.strip() or len(title) > 160
                                  or any(ord(character) < 32 for character in title)):
            raise WorkRuntimeError("invalid_title")
        now, work_id = self._now(), "work-" + uuid4().hex
        with self._transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO ai_work_jobs(work_id,organization_id,actor_id,repository,title,task_type,"
                "context_revision,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'active',?,?)",
                (work_id, *owner, repository, title, task_type, revision, now, now),
            )
            return self._work_view(connection, self._work(connection, owner, work_id), now)

    def get_work(self, identity, work_id):
        owner, now = _identity(identity), self._now()
        with self._transaction() as connection:
            return self._work_view(connection, self._work(connection, owner, work_id), now)

    def list_work(self, identity):
        owner, now = _identity(identity), self._now()
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_work_jobs WHERE organization_id=? AND actor_id=? "
                "ORDER BY created_at DESC,work_id LIMIT ?", (*owner, _LIST_WORK_LIMIT),
            ).fetchall()
            return [self._work_view(connection, dict(row), now) for row in rows]

    @staticmethod
    def _costs(connection, where, parameters):
        row = connection.execute(
            "SELECT COUNT(*) AS attempts,COALESCE(SUM(CASE WHEN state IN ('succeeded','failed','cache_hit') "
            "THEN cost_microusd ELSE 0 END),0) AS committed_microusd,"
            "COALESCE(SUM(CASE WHEN state='pending' THEN reserved_microusd ELSE 0 END),0) AS pending_microusd,"
            "COALESCE(SUM(CASE WHEN state='unknown' THEN reserved_microusd ELSE 0 END),0) AS uncertain_microusd,"
            "COALESCE(SUM(CASE WHEN state='cache_hit' THEN 1 ELSE 0 END),0) AS cache_hits,"
            "COALESCE(SUM(CASE WHEN state='denied' THEN 1 ELSE 0 END),0) AS denied,"
            "COALESCE(SUM(reservation_exceeded),0) AS reservation_exceeded,"
            "COALESCE(SUM(CASE WHEN repeat_count>0 AND pattern_excluded=0 THEN 1 ELSE 0 END),0) AS repeated_requests,"
            "SUM(latency_ms) AS provider_latency_ms,COUNT(latency_ms) AS timed_attempts "
            "FROM ai_work_attempts WHERE " + where, parameters,
        ).fetchone()
        result = dict(row)
        result["consumed_microusd"] = result["committed_microusd"] + result["pending_microusd"] + result["uncertain_microusd"]
        result["cost_basis"] = "gateway_reported_or_configured_estimate"
        result["invoice_finality"] = False
        result["latency_basis"] = "sum_observed_attempt_latency"
        return result

    def _plan_view(self, connection, org, scope_type, scope_id, now, *, plan_cache=None):
        cache_key = org, scope_type, scope_id
        if plan_cache is not None and cache_key in plan_cache:
            return plan_cache[cache_key]
        row = connection.execute(
            "SELECT * FROM ai_work_plans WHERE organization_id=? AND scope_type=? AND scope_id=?",
            (org, scope_type, scope_id),
        ).fetchone()
        if row is None:
            if plan_cache is not None:
                plan_cache[cache_key] = None
            return None
        plan = dict(row)
        where, values = "organization_id=?", [org]
        if scope_type == "job":
            where += " AND work_id=?"
            values.append(scope_id)
            start, end = None, None
        else:
            start, end = _month(now)
            where += " AND created_at>=? AND created_at<?"
            values.extend((start, end))
            if scope_type == "repository":
                where += " AND work_id IN (SELECT work_id FROM ai_work_jobs WHERE organization_id=? AND repository=?)"
                values.extend((org, scope_id))
        costs = self._costs(connection, where, tuple(values))
        plan.update(costs)
        plan["remaining_microusd"] = None if plan["budget_microusd"] is None else plan["budget_microusd"] - costs["consumed_microusd"]
        plan["period"] = "job_lifetime" if scope_type == "job" else "utc_month"
        plan["window_start"] = _timestamp(start)
        plan["window_end"] = _timestamp(end)
        plan["updated_at"] = _timestamp(plan["updated_at"])
        plan["forecast_microusd"] = None
        plan["forecast_basis"] = "unavailable"
        if start is not None and now > start and costs["attempts"] and costs["uncertain_microusd"] == 0:
            duration, duration_denominator = (end - start).as_integer_ratio()
            elapsed, elapsed_denominator = (now - start).as_integer_ratio()
            numerator = costs["committed_microusd"] * duration * elapsed_denominator
            denominator = duration_denominator * elapsed
            plan["forecast_microusd"] = (numerator + denominator - 1) // denominator
            plan["forecast_basis"] = "linear_committed_estimate_excludes_holds"
        if plan_cache is not None:
            plan_cache[cache_key] = plan
        return plan

    def _plans_for_work(self, connection, work, now, *, plan_cache=None):
        return [plan for scope_type, scope_id in (
            ("workspace", work["organization_id"]), ("repository", work["repository"]), ("job", work["work_id"])
        ) if (plan := self._plan_view(connection, work["organization_id"], scope_type, scope_id, now, plan_cache=plan_cache)) is not None]

    def _work_view(self, connection, work, now, *, attempt_limit=100, observation_limit=1000, plan_cache=None):
        costs = self._costs(connection, "organization_id=? AND actor_id=? AND work_id=?",
                            (work["organization_id"], work["actor_id"], work["work_id"]))
        observations = [dict(row) for row in connection.execute(
            "SELECT status,source,reference,observed_at FROM ai_work_observations "
            "WHERE work_id=? ORDER BY observed_at DESC,observation_id DESC LIMIT ?", (work["work_id"], observation_limit + 1),
        )]
        observations_truncated = len(observations) > observation_limit
        observations = list(reversed(observations[:observation_limit]))
        for observation in observations:
            observation["observed_at"] = _timestamp(observation["observed_at"])
        attempt_rows = connection.execute(
            "SELECT * FROM ai_work_attempts WHERE work_id=? ORDER BY created_at DESC,request_id LIMIT ?",
            (work["work_id"], attempt_limit + 1),
        ).fetchall()
        attempts = [self._attempt_view(dict(row)) for row in attempt_rows[:attempt_limit]]
        plans = self._plans_for_work(connection, work, now, plan_cache=plan_cache)
        result = {key: value for key, value in work.items() if key != "cache_generation"}
        for key in ("created_at", "updated_at", "completed_at"):
            result[key] = _timestamp(result[key])
        result.update({"costs": costs, "observations": observations, "attempts": attempts,
                       "attempts_truncated": len(attempt_rows) > attempt_limit,
                       "attempts_limit": attempt_limit, "observations_truncated": observations_truncated,
                       "observations_limit": observation_limit, "plans": plans,
                       "objective": plans[-1]["objective"] if plans else "cost",
                       "budget_microusd": plans[-1]["budget_microusd"] if plans else None,
                       "cost_microusd": costs["committed_microusd"],
                       "outcome_evidence": "observed" if observations else "unknown",
                       "completion_elapsed_ms": None if work["completed_at"] is None else max(0, (work["completed_at"] - work["created_at"]) * 1000)})
        return result

    def observe(self, identity, work_id, status, source="workflow", reference=None):
        owner = _identity(identity)
        if not isinstance(status, str) or status not in _OBSERVATIONS:
            raise WorkRuntimeError("invalid_observation")
        source = _identifier(source, "source")
        reference = _identifier(reference, "reference", optional=True)
        now = self._now()
        with self._transaction(write=True) as connection:
            work = self._work(connection, owner, work_id)
            if reference is not None and connection.execute(
                "SELECT 1 FROM ai_work_observations WHERE organization_id=? AND actor_id=? AND work_id=? "
                "AND status=? AND source=? AND reference=?", (*owner, work_id, status, source, reference),
            ).fetchone():
                return self._work_view(connection, work, now)
            connection.execute("INSERT INTO ai_work_observations VALUES(?,?,?,?,?,?,?,?)",
                               ("observation-" + uuid4().hex, *owner, work_id, status, source, reference, now))
            state = {"completed": "completed", "canceled": "canceled", "unknown": "unknown"}.get(status, "active")
            if status in {"corrected", "reopened", "unknown"} and work["state"] in {"paused", "canceled"}:
                state = work["state"]
            invalidate = status in {"corrected", "reopened", "canceled"}
            connection.execute(
                "UPDATE ai_work_jobs SET state=?,pause_reason=?,updated_at=?,completed_at=?,version=version+1,"
                "cache_generation=cache_generation+?,pinned_model=CASE WHEN ? THEN NULL ELSE pinned_model END "
                "WHERE work_id=?", (state, work["pause_reason"] if state == "paused" else None, now,
                now if status == "completed" else None, int(invalidate), int(invalidate), work_id),
            )
            result = self._work_view(connection, self._work(connection, owner, work_id), now)
            benchmark_keys = self._work_profile_keys(connection, work)
        if invalidate:
            self._evict_work(work_id)
        self._invalidate_profiles(work, benchmark_keys)
        return result

    def act(self, identity, work_id, action, budget_microusd=None, expected_version=None):
        owner, now = _identity(identity), self._now()
        if not isinstance(action, str) or action not in {"pause", "resume", "stop", "approve_budget"}:
            raise WorkRuntimeError("invalid_action")
        with self._transaction(write=True) as connection:
            work = self._work(connection, owner, work_id)
            if action in {"resume", "approve_budget"} and work["state"] == "completed":
                raise WorkRuntimeError("work_completed", 409)
            if action == "approve_budget":
                budget = _amount(budget_microusd)
                plans = self._plans_for_work(connection, work, now)
                objective = plans[-1]["objective"] if plans else "cost"
                row = connection.execute(
                    "SELECT version FROM ai_work_plans WHERE organization_id=? AND scope_type='job' AND scope_id=?",
                    (owner[0], work_id),
                ).fetchone()
                current = row[0] if row else 0
                if expected_version is not None and (type(expected_version) is not int or expected_version != current):
                    raise WorkRuntimeError("plan_conflict", 409)
                connection.execute(
                    "INSERT INTO ai_work_plans VALUES(?,'job',?,?,?,?,?) "
                    "ON CONFLICT(organization_id,scope_type,scope_id) DO UPDATE SET "
                    "version=excluded.version,budget_microusd=excluded.budget_microusd,"
                    "objective=excluded.objective,updated_at=excluded.updated_at",
                    (owner[0], work_id, current + 1, budget, objective, now),
                )
            state = "paused" if action == "pause" else "canceled" if action == "stop" else "active"
            connection.execute(
                "UPDATE ai_work_jobs SET state=?,pause_reason=?,updated_at=?,version=version+1,"
                "cache_generation=cache_generation+? WHERE work_id=?",
                (state, "customer_paused" if state == "paused" else None, now, int(action == "stop"), work_id),
            )
            result = self._work_view(connection, self._work(connection, owner, work_id), now)
        if action == "stop":
            self._evict_work(work_id)
        return result

    def reserve(self, identity, work_id, request_id, model, protocol, max_cost_microusd,
                reason="baseline", *, logical_request_id=None, retry_of=None, cache_source=None,
                request_pattern=None, automatic_retry=False, legitimate_iteration=False, request_shape="plain",
                expected_cache_generation=None):
        owner, now = _identity(identity), self._now()
        request_id = _identifier(request_id, "request")
        fingerprint = self._route_fingerprint(model)
        if isinstance(model, ModelRoute) and model.protocol != protocol:
            raise WorkRuntimeError("model_protocol_mismatch")
        model = _identifier(model.alias if isinstance(model, ModelRoute) else model, "model")
        protocol = _identifier(protocol, "protocol")
        amount = _amount(max_cost_microusd)
        reason = _identifier(reason, "reason")
        logical_request_id = _identifier(logical_request_id, "logical_request", optional=True)
        retry_of = _identifier(retry_of, "retry_of", optional=True)
        cache_source = _identifier(cache_source, "cache_source", optional=True)
        request_shape = _identifier(request_shape, "request_shape")
        if request_pattern is not None and (not isinstance(request_pattern, str) or not re.fullmatch(r"[0-9a-f]{64}", request_pattern)):
            raise WorkRuntimeError("invalid_request_pattern")
        if type(automatic_retry) is not bool or type(legitimate_iteration) is not bool:
            raise WorkRuntimeError("invalid_pattern_classification")
        if expected_cache_generation is not None and (type(expected_cache_generation) is not int or not 0 <= expected_cache_generation < 2**63):
            raise WorkRuntimeError("invalid_cache_generation")
        pattern_excluded = automatic_retry or legitimate_iteration or retry_of is not None
        denied = None
        with self._transaction(write=True) as connection:
            work = self._work(connection, owner, work_id)
            if expected_cache_generation is not None and work["cache_generation"] != expected_cache_generation:
                raise WorkRuntimeError("cache_generation_changed", 409)
            existing = connection.execute(
                "SELECT * FROM ai_work_attempts WHERE organization_id=? AND actor_id=? AND request_id=?", (*owner, request_id)
            ).fetchone()
            if existing:
                previous = dict(existing)
                if any(previous[key] != value for key, value in {
                    "work_id": work_id, "model": model, "protocol": protocol,
                    "reserved_microusd": amount, "logical_request_id": logical_request_id,
                    "retry_of": retry_of, "cache_source": cache_source,
                    "route_fingerprint": fingerprint, "request_pattern": request_pattern,
                    "pattern_excluded": int(pattern_excluded),
                    "request_shape": request_shape,
                }.items()):
                    raise WorkRuntimeError("request_conflict", 409)
                return {**self._attempt_view(previous), "idempotent_replay": True}
            if work["state"] != "active":
                raise WorkRuntimeError("work_not_active", 409)
            if retry_of is not None and connection.execute(
                "SELECT 1 FROM ai_work_attempts WHERE organization_id=? AND actor_id=? AND request_id=? AND work_id=?",
                (*owner, retry_of, work_id),
            ).fetchone() is None:
                raise WorkRuntimeError("retry_not_found", 404)
            for plan in self._plans_for_work(connection, work, now):
                if plan["remaining_microusd"] is not None and amount > plan["remaining_microusd"]:
                    denied = WorkRuntimeError("budget_exhausted", 402)
                    break
            state = "denied" if denied else "pending"
            repeats = 0
            if request_pattern and not pattern_excluded:
                repeats = connection.execute(
                    "SELECT COUNT(*) FROM ai_work_attempts WHERE organization_id=? AND actor_id=? AND work_id=? "
                    "AND request_pattern=? AND pattern_excluded=0 AND state<>'denied' AND created_at>=?",
                    (*owner, work_id, request_pattern, now - 900),
                ).fetchone()[0]
            connection.execute(
                "INSERT INTO ai_work_attempts(organization_id,actor_id,request_id,work_id,logical_request_id,retry_of,cache_source,"
                "model,protocol,route_fingerprint,request_pattern,request_shape,repeat_count,pattern_excluded,reason,state,reserved_microusd,created_at,settled_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*owner, request_id, work_id, logical_request_id, retry_of, cache_source, model, protocol,
                 fingerprint, request_pattern, request_shape, repeats, int(pattern_excluded), reason, state, amount, now, now if denied else None),
            )
            if denied:
                connection.execute("UPDATE ai_work_jobs SET state='paused',pause_reason='budget_exhausted',updated_at=?,version=version+1 WHERE work_id=?", (now, work_id))
            else:
                connection.execute("UPDATE ai_work_jobs SET pinned_model=COALESCE(pinned_model,?),updated_at=? WHERE work_id=?", (model, now, work_id))
            row = connection.execute("SELECT * FROM ai_work_attempts WHERE organization_id=? AND actor_id=? AND request_id=?", (*owner, request_id)).fetchone()
            result = {**self._attempt_view(dict(row)), "idempotent_replay": False}
        if denied:
            raise denied
        return result

    @staticmethod
    def _attempt_view(attempt):
        result = dict(attempt)
        result.pop("request_pattern", None)
        result["repeat_signal"] = "possible_exact_recurrence_not_quality" if result["repeat_count"] and not result["pattern_excluded"] else "none"
        result["created_at"] = _timestamp(result["created_at"])
        result["settled_at"] = _timestamp(result["settled_at"])
        result["reservation_exceeded"] = bool(result["reservation_exceeded"])
        return result

    def settle(self, identity, request_id, cost_microusd=None, status="succeeded", latency_ms=None):
        owner, now = _identity(identity), self._now()
        _identifier(request_id, "request")
        status = "unknown" if status == "outcome_unknown" else status
        if not isinstance(status, str) or status not in {"succeeded", "failed", "unknown", "cache_hit"}:
            raise WorkRuntimeError("invalid_attempt_status")
        cost = _amount(cost_microusd, optional=True)
        if latency_ms is not None and (isinstance(latency_ms, bool) or not isinstance(latency_ms, (int, float))
                                       or not math.isfinite(latency_ms) or not 0 <= latency_ms <= 86_400_000):
            raise WorkRuntimeError("invalid_latency")
        if status == "cache_hit" and cost not in {0, None}:
            raise WorkRuntimeError("invalid_cache_cost")
        if status == "cache_hit":
            cost = 0
        if cost is None:
            status = "unknown"
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT * FROM ai_work_attempts WHERE organization_id=? AND actor_id=? AND request_id=?", (*owner, request_id)).fetchone()
            if row is None:
                raise WorkRuntimeError("attempt_not_found", 404)
            attempt = dict(row)
            work = self._work(connection, owner, attempt["work_id"])
            if attempt["state"] in _TERMINAL:
                if attempt["state"] != status or attempt["cost_microusd"] != cost or (latency_ms is not None and attempt["latency_ms"] != latency_ms):
                    raise WorkRuntimeError("settlement_conflict", 409)
                return self._attempt_view(attempt)
            connection.execute(
                "UPDATE ai_work_attempts SET state=?,cost_microusd=?,latency_ms=?,settled_at=?,reservation_exceeded=? "
                "WHERE organization_id=? AND actor_id=? AND request_id=?",
                (status, cost, latency_ms, now, int(cost is not None and cost > attempt["reserved_microusd"]), *owner, request_id),
            )
            row = connection.execute("SELECT * FROM ai_work_attempts WHERE organization_id=? AND actor_id=? AND request_id=?", (*owner, request_id)).fetchone()
            result = self._attempt_view(dict(row))
        self._invalidate_profiles(work, [(attempt["protocol"], attempt["request_shape"])])
        return result

    def report(self, identity, *, administrator=False):
        """Return owned work, or tenant work after transport-authorized admin access."""
        if type(administrator) is not bool:
            raise WorkRuntimeError("invalid_report_scope")
        owner, now = _identity(identity), self._now()
        with self._transaction() as connection:
            where = "organization_id=?" if administrator else "organization_id=? AND actor_id=?"
            parameters = (owner[0],) if administrator else owner
            rows = connection.execute("SELECT * FROM ai_work_jobs WHERE " + where + " ORDER BY created_at DESC,work_id LIMIT 100", parameters).fetchall()
            plan_cache = {}
            works = [self._work_view(connection, dict(row), now, attempt_limit=5, observation_limit=5, plan_cache=plan_cache) for row in rows]
            plan_where, plan_parameters = "organization_id=?", (owner[0],)
            if not administrator:
                plan_where += " AND (scope_type='workspace' OR (scope_type='repository' AND scope_id IN " \
                    "(SELECT repository FROM ai_work_jobs WHERE organization_id=? AND actor_id=?)) OR " \
                    "(scope_type='job' AND scope_id IN (SELECT work_id FROM ai_work_jobs WHERE organization_id=? AND actor_id=?)))"
                plan_parameters += (*owner, *owner)
            plan_rows = connection.execute("SELECT scope_type,scope_id FROM ai_work_plans WHERE " + plan_where +
                " ORDER BY CASE scope_type WHEN 'workspace' THEN 0 WHEN 'repository' THEN 1 ELSE 2 END,scope_id LIMIT 100", plan_parameters).fetchall()
            plans = [self._plan_view(connection, owner[0], row["scope_type"], row["scope_id"], now, plan_cache=plan_cache)
                     for row in plan_rows]
            totals = self._costs(connection, where, parameters)
            work_totals = connection.execute("SELECT COUNT(*),COALESCE(SUM(state='completed'),0) FROM ai_work_jobs WHERE " + where, parameters).fetchone()
            total_plans = connection.execute("SELECT COUNT(*) FROM ai_work_plans WHERE " + plan_where, plan_parameters).fetchone()[0]
            totals["works"], totals["completed"] = work_totals
            return {"schema_id": "hormuz.ai-work-state", "schema_version": 1,
                    "organization_id": owner[0], "actor_id": owner[1], "as_of": _timestamp(now),
                    "works": works, "plans": plans, "totals": totals,
                    "scope": "organization" if administrator else "actor",
                    "display": {"job_limit": 100, "shown_jobs": len(works), "total_jobs": totals["works"],
                                "jobs_truncated": totals["works"] > len(works), "attempt_limit_per_job": 5,
                                "observation_limit_per_job": 5, "plan_limit": 100, "shown_plans": len(plans),
                                "total_plans": total_plans, "plans_truncated": total_plans > len(plans)},
                    "coverage": "gateway_captured_work_requests_only",
                    "cache": {"enabled": self.cache_enabled, "retention": "bounded_process_memory", "exact_only": True}}

    def _profiles(self, connection, work, protocol):
        rows = connection.execute(
            "WITH candidates AS (SELECT j.work_id,j.created_at,"
            "(SELECT COUNT(*) FROM ai_work_attempts counted WHERE counted.work_id=j.work_id) AS attempts "
            "FROM ai_work_jobs j WHERE j.organization_id=? AND j.repository=? AND j.task_type=? "
            "AND EXISTS (SELECT 1 FROM ai_work_observations o WHERE o.work_id=j.work_id AND o.status IN ('completed','corrected','reopened')) "
            "AND EXISTS (SELECT 1 FROM ai_work_attempts recent WHERE recent.work_id=j.work_id AND recent.protocol=? AND recent.created_at>=?) "
            "ORDER BY j.created_at DESC,j.work_id LIMIT 1000), "
            "bounded AS (SELECT work_id,SUM(attempts) OVER (ORDER BY created_at DESC,work_id ROWS UNBOUNDED PRECEDING) AS total "
            "FROM candidates WHERE attempts<=1000) "
            "SELECT w.work_id,w.state,a.model,a.protocol,a.route_fingerprint,a.request_shape,a.state AS attempt_state,a.cost_microusd,a.latency_ms "
            "FROM ai_work_jobs w JOIN ai_work_attempts a ON a.work_id=w.work_id "
            "WHERE w.work_id IN (SELECT work_id FROM bounded WHERE total<=10000) "
            "ORDER BY w.created_at DESC,w.work_id,a.created_at LIMIT 10001",
            (work["organization_id"], work["repository"], work["task_type"], protocol, self._now() - 90 * 86400),
        ).fetchall()
        if len(rows) > 10_000:
            return {}
        episodes = {}
        for row in rows:
            episode = episodes.setdefault(row["work_id"], {"models": set(), "cost": 0, "latency": 0,
                "state": row["state"], "usable": True, "timed": True, "executed": False, "fingerprints": set(), "shapes": set(), "protocols": set()})
            episode["models"].add(row["model"])
            episode["fingerprints"].add(row["route_fingerprint"])
            episode["shapes"].add(row["request_shape"])
            episode["protocols"].add(row["protocol"])
            if row["attempt_state"] not in {"succeeded", "failed", "cache_hit"} or row["cost_microusd"] is None:
                episode["usable"] = False
            else:
                episode["cost"] += row["cost_microusd"]
            if row["attempt_state"] in {"succeeded", "failed"}:
                episode["executed"] = True
            if row["latency_ms"] is None:
                episode["timed"] = False
            else:
                episode["latency"] += row["latency_ms"]
        profiles = {}
        for work_id, episode in episodes.items():
            observations = connection.execute("SELECT status FROM ai_work_observations WHERE work_id=?", (work_id,)).fetchall()
            statuses = {row[0] for row in observations}
            if not episode["usable"] or not episode["executed"] or len(episode["models"]) != 1 or len(episode["fingerprints"]) != 1 or not statuses.intersection({"completed", "corrected", "reopened"}):
                continue
            if episode["shapes"] != {work.get("_request_shape", "plain")} or episode["protocols"] != {protocol}:
                continue
            alias = next(iter(episode["models"]))
            fingerprint = next(iter(episode["fingerprints"]))
            profile = profiles.setdefault((alias, fingerprint), {"samples": 0, "completed": 0, "rework": 0,
                                                   "cost": 0, "latency": 0, "timed": 0})
            profile["samples"] += 1
            profile["completed"] += int(episode["state"] == "completed")
            profile["rework"] += int(bool(statuses.intersection({"corrected", "reopened"})))
            profile["cost"] += episode["cost"]
            if episode["timed"]:
                profile["timed"] += 1
                profile["latency"] += episode["latency"]
        return profiles

    @staticmethod
    def _profile_key(work, protocol):
        return work["organization_id"], work["repository"], work["task_type"], protocol, work.get("_request_shape", "plain")

    @staticmethod
    def _work_profile_keys(connection, work):
        return [(row[0], row[1]) for row in connection.execute(
            "SELECT DISTINCT protocol,request_shape FROM ai_work_attempts WHERE work_id=? LIMIT 128",
            (work["work_id"],),
        )]

    def _profile_version(self, key):
        with self._profile_lock:
            if key not in self._profile_versions:
                self._profile_version_counter += 1
                self._profile_versions[key] = self._profile_version_counter
            self._profile_versions.move_to_end(key)
            while len(self._profile_versions) > 512:
                self._profile_versions.popitem(last=False)
            return self._profile_versions[key]

    def _invalidate_profiles(self, work, evidence_keys):
        """Retire comparable snapshots before queuing committed new evidence."""
        prefix = work["organization_id"], work["repository"], work["task_type"]
        with self._profile_lock:
            keys = {key for key in self._profile_versions if key[:3] == prefix}
            keys.update((*prefix, protocol, shape) for protocol, shape in evidence_keys)
            for key in keys:
                self._profile_cache.pop(key, None)
                self._profile_version_counter += 1
                self._profile_versions[key] = self._profile_version_counter
                self._profile_versions.move_to_end(key)
            while len(self._profile_versions) > 512:
                self._profile_versions.popitem(last=False)
        for key in keys:
            self._queue_profile_refresh({**work, "_request_shape": key[4]}, key[3])

    def _save_profiles(self, key, profiles, version):
        with self._profile_lock:
            if self._closed or self._profile_versions.get(key) != version:
                return False
            self._profile_cache[key] = {"built_at": self._now(), "profiles": profiles}
            self._profile_cache.move_to_end(key)
            while len(self._profile_cache) > 128:
                self._profile_cache.popitem(last=False)
            return True

    def refresh_benchmark(self, identity, work_id, protocol, request_value=None):
        """Build a bounded snapshot outside request routing, for jobs/operators.

        The normal selector only reads snapshots and queues this work on cache
        miss. This explicit method is useful for deterministic offline replay.
        It does not call any provider or turn unknown work into success.
        """
        owner = _identity(identity)
        protocol = _identifier(protocol, "protocol")
        with self._transaction() as connection:
            work = self._work(connection, owner, work_id)
            work["_request_shape"] = self.shape_for_request(request_value or {})
            version = self._profile_version(self._profile_key(work, protocol))
            profiles = self._profiles(connection, work, protocol)
        if not self._save_profiles(self._profile_key(work, protocol), profiles, version):
            self._queue_profile_refresh(work, protocol)
        return {"sample_count": sum(profile["samples"] for profile in profiles.values()),
                "evidence_basis": "local_observational_work_episodes"}

    def _queue_profile_refresh(self, work, protocol):
        key = self._profile_key(work, protocol)
        with self._profile_lock:
            if self._closed or key in self._profile_pending or len(self._profile_pending) >= 128:
                return
            self._profile_version(key)
            self._profile_pending[key] = dict(work), protocol
            if self._profile_worker is not None:
                return
            worker = threading.Thread(target=self._drain_profiles, name="hormuz-work-benchmark", daemon=True)
            self._profile_worker = worker
            worker.start()

    def _drain_profiles(self):
        while True:
            with self._profile_lock:
                if self._closed or not self._profile_pending:
                    self._profile_worker = None
                    return
                key, (work, protocol) = self._profile_pending.popitem(last=False)
                version = self._profile_version(key)
            try:
                with self._transaction() as connection:
                    profiles = self._profiles(connection, work, protocol)
                if not self._save_profiles(key, profiles, version):
                    self._queue_profile_refresh(work, protocol)
            except (WorkRuntimeError, OSError):
                # Measurement unavailable means approved baseline; never a
                # provider retry, hidden zero, or failure of ordinary routing.
                continue

    def close(self):
        """Stop background measurement and release process-only content."""
        with self._profile_lock:
            self._closed = True
            self._profile_pending.clear()
            worker = self._profile_worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=5)
        with self._cache_lock:
            self._cache.clear()
            self._cache_size = 0

    def _snapshot_profiles(self, work, protocol):
        key = self._profile_key(work, protocol)
        with self._profile_lock:
            snapshot = self._profile_cache.get(key)
            fresh = snapshot is not None and self._now() - snapshot["built_at"] <= 30
            profiles = snapshot["profiles"] if fresh else None
        if profiles is None:
            self._queue_profile_refresh(work, protocol)
        return profiles

    def choose_route(self, identity, work_id, candidates, baseline, request_value):
        owner, now = _identity(identity), self._now()
        candidate_values = list(candidates.values()) if isinstance(candidates, Mapping) else list(candidates)
        if not 1 <= len(candidate_values) <= 100 or any(not isinstance(route, ModelRoute) for route in candidate_values):
            raise WorkRuntimeError("invalid_candidates")
        routes = {route.alias: route for route in candidate_values}
        baseline_alias = baseline.alias if isinstance(baseline, ModelRoute) else baseline
        if baseline_alias not in routes:
            raise WorkRuntimeError("baseline_not_eligible", 409)
        default = routes[baseline_alias]
        eligible = {alias: route for alias, route in routes.items() if route.protocol == default.protocol}
        if not isinstance(request_value, dict):
            raise WorkRuntimeError("invalid_request")
        result = {"alias": baseline_alias, "reason": "approved_baseline", "sample_confidence": "insufficient",
                  "sample_count": 0, "objective": "cost", "evidence_basis": "local_observational_work_episodes"}
        with self._transaction() as connection:
            work = self._work(connection, owner, work_id)
            work["_request_shape"] = self.shape_for_request(request_value)
            plans = connection.execute(
                "SELECT objective FROM ai_work_plans WHERE organization_id=? AND "
                "((scope_type='workspace' AND scope_id=?) OR (scope_type='repository' AND scope_id=?) OR "
                "(scope_type='job' AND scope_id=?)) ORDER BY CASE scope_type WHEN 'workspace' THEN 0 WHEN 'repository' THEN 1 ELSE 2 END",
                (work["organization_id"], work["organization_id"], work["repository"], work_id),
            ).fetchall()
            objective = plans[-1]["objective"] if plans else "cost"
            result["objective"] = objective
            if work["repository"] == "unattributed" or work["task_type"] == "unattributed":
                result.update(reason="unattributed_baseline")
                return result
            # Never translate opaque provider state or change model inside a
            # continuing tool interaction. The caller filters other capability
            # constraints before invoking this selector.
            continuation = self._contains_provider_state(request_value)
            if continuation:
                pinned = work["pinned_model"]
                if pinned and pinned not in eligible:
                    raise WorkRuntimeError("session_route_not_eligible", 409)
                result.update(alias=pinned or baseline_alias, reason="session_affinity", sample_confidence="not_applicable")
                return result
            if request_value.get("tools"):
                declared = getattr(getattr(self.config, "ai_work", None), "tool_capable_aliases", ())
                tool_routes = {alias: route for alias, route in eligible.items() if alias in declared}
                if baseline_alias not in tool_routes:
                    result.update(reason="tool_compatibility_baseline", sample_confidence="not_applicable")
                    return result
                eligible = tool_routes
            profiles = self._snapshot_profiles(work, default.protocol)
            if profiles is None:
                result.update(reason="benchmark_refresh_pending")
                return result
            baseline_profile = profiles.get((baseline_alias, self._route_fingerprint(default)))
            if baseline_profile is None or baseline_profile["samples"] < self.minimum_samples:
                return result
            baseline_success = baseline_profile["completed"] / baseline_profile["samples"]
            baseline_rework = baseline_profile["rework"] / baseline_profile["samples"]
            qualified = {}
            for alias in eligible:
                profile = profiles.get((alias, self._route_fingerprint(eligible[alias])))
                if profile is None or profile["samples"] < self.minimum_samples:
                    continue
                if objective != "quality" and (profile["completed"] / profile["samples"] < baseline_success or
                                                profile["rework"] / profile["samples"] > baseline_rework):
                    continue
                if objective == "speed" and profile["timed"] != profile["samples"]:
                    continue
                qualified[alias] = profile
            if not qualified:
                return result
            def score(alias):
                profile = qualified[alias]
                count = profile["samples"]
                cost = profile["cost"] / count
                latency = profile["latency"] / count if profile["timed"] == count else math.inf
                completed, rework = profile["completed"] / count, profile["rework"] / count
                tie = alias != baseline_alias
                if objective == "speed":
                    return latency, cost, tie, alias
                if objective == "quality":
                    return -completed, rework, cost, latency, tie, alias
                return cost, latency, tie, alias
            selected = min(qualified, key=score)
            result.update(alias=selected, reason="local_" + objective + "_evidence",
                          sample_confidence="observational", sample_count=qualified[selected]["samples"])
            return result

    @staticmethod
    def _contains_provider_state(value, depth=0):
        if depth > 40:
            return True
        if isinstance(value, dict):
            if any(value.get(key) for key in ("previous_response_id", "conversation", "session_id")):
                return True
            if value.get("role") in {"assistant", "tool"} or value.get("type") in {
                "function_call", "function_call_output", "tool_use", "tool_result", "computer_call", "computer_call_output"
            }:
                return True
            return any(WorkRuntime._contains_provider_state(item, depth + 1) for item in value.values())
        return isinstance(value, list) and any(WorkRuntime._contains_provider_state(item, depth + 1) for item in value)

    @staticmethod
    def _cache_safe(request_value):
        if not isinstance(request_value, dict) or request_value.get("stream") or request_value.get("tools") or request_value.get("temperature") != 0:
            return False
        forbidden = {"previous_response_id", "conversation", "session_id", "file_id", "image_url", "audio", "tool_calls", "tool_call_id",
                     "url", "file_data", "input_audio", "input_image", "prompt", "prompt_id"}
        def safe(value, depth=0):
            if depth > 40:
                return False
            if isinstance(value, dict):
                if any(key in forbidden and item not in (None, False, [], {}) for key, item in value.items()):
                    return False
                if value.get("role") in {"assistant", "tool"} or value.get("type") in {
                    "function_call", "function_call_output", "tool_use", "tool_result", "image", "input_image", "input_file", "document", "audio", "file"
                }:
                    return False
                return all(isinstance(key, str) and safe(item, depth + 1) for key, item in value.items())
            if isinstance(value, list):
                return len(value) <= 1000 and all(safe(item, depth + 1) for item in value)
            return value is None or isinstance(value, (str, bool, int)) or (isinstance(value, float) and math.isfinite(value))
        return safe(request_value) and not request_value.get("background") and not request_value.get("store")

    def _route_fingerprint(self, model):
        if isinstance(model, str) and self.config is not None:
            model = getattr(self.config, "model_routes", {}).get(model, model)
        if not isinstance(model, ModelRoute):
            return None
        fields = [model.alias, model.protocol, model.upstream_model, model.input_cost_per_million,
                  model.cache_read_cost_per_million, model.cache_write_cost_per_million, model.output_cost_per_million]
        upstream = getattr(self.config, "upstreams", {}).get(model.protocol) if self.config is not None else None
        fields.append(getattr(upstream, "base_url", None))
        try:
            return hashlib.sha256(json.dumps(fields, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        except (TypeError, ValueError):
            raise WorkRuntimeError("invalid_model_evidence") from None

    def pattern_for_request(self, request_value):
        """HMAC exact redacted request metadata; not a semantic similarity score.

        The per-process key intentionally prevents persisted fingerprints from
        linking requests across a process restart. Automatic retries, history
        retransmission and legitimate iterations must be excluded by callers.
        """
        if not isinstance(request_value, dict):
            raise WorkRuntimeError("invalid_request")
        try:
            canonical = json.dumps({key: value for key, value in request_value.items() if key != "model"},
                                   sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        except (TypeError, ValueError, RecursionError):
            raise WorkRuntimeError("invalid_request") from None
        if len(canonical) > _MAX_REQUEST_BYTES:
            return None
        return hmac.new(self._cache_key, b"pattern\0" + canonical, hashlib.sha256).hexdigest()

    @staticmethod
    def shape_for_request(request_value):
        """Version request capability metadata without keeping input content."""
        if not isinstance(request_value, dict):
            raise WorkRuntimeError("invalid_request")
        fields = {key: request_value[key] for key in ("tools", "tool_choice", "response_format", "text", "modalities", "_hormuz_endpoint")
                  if request_value.get(key)}
        if not fields:
            return "plain"
        try:
            encoded = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, TypeError, RecursionError):
            raise WorkRuntimeError("invalid_request_shape") from None
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise WorkRuntimeError("request_shape_too_large")
        return "shape-v1:" + hashlib.sha256(encoded).hexdigest()

    def cache_generation(self, identity, work_id):
        """Capture an owned dispatch epoch before sending provider work."""
        owner = _identity(identity)
        with self._transaction() as connection:
            return self._work(connection, owner, work_id)["cache_generation"]

    def _cache_identity(self, identity, work_id, request_value, model, policy_version, *, expected_generation=None):
        owner = _identity(identity)
        fingerprint = self._route_fingerprint(model)
        model = _identifier(model.alias if isinstance(model, ModelRoute) else model, "model")
        if isinstance(policy_version, int) and not isinstance(policy_version, bool):
            policy_version = str(policy_version)
        policy_version = _identifier(policy_version, "policy_version")
        with self._transaction() as connection:
            work = self._work(connection, owner, work_id)
        if expected_generation is not None:
            if type(expected_generation) is not int or not 0 <= expected_generation < 2**63:
                raise WorkRuntimeError("invalid_cache_generation")
            if work["cache_generation"] != expected_generation:
                return None
        if not self.cache_enabled or work["state"] != "active" or not work["context_revision"] or not self._cache_safe(request_value):
            return None
        try:
            canonical = json.dumps(request_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError):
            raise WorkRuntimeError("invalid_cache_request") from None
        if len(canonical) > _MAX_REQUEST_BYTES:
            return None
        dimensions = json.dumps([*owner, work_id, work["context_revision"], work["cache_generation"], model, fingerprint, policy_version], separators=(",", ":")).encode()
        return hmac.new(self._cache_key, dimensions + b"\0" + canonical, hashlib.sha256).hexdigest(), work["cache_generation"]

    def cache_get(self, identity, work_id, request_value, model, policy_version):
        identity_key = self._cache_identity(identity, work_id, request_value, model, policy_version)
        if identity_key is None:
            return None
        key, generation = identity_key
        now = self._now()
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry["expires_at"] <= now:
                self._cache_size -= len(entry["body"])
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return {"body": entry["body"], "status_code": entry["status_code"],
                    "headers": dict(entry["headers"]), "source": "exact_answer_cache", "cache_id": key,
                    "cache_generation": generation, "expires_at": _timestamp(entry["expires_at"])}

    def cache_put(self, identity, work_id, request_value, model, policy_version,
                  response, ttl_seconds=60, *, status_code=200, headers=None, expected_generation=None):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise WorkRuntimeError("invalid_cache_ttl")
        if type(status_code) is not int or status_code != 200:
            return False
        if expected_generation is None:
            self.cache_generation(identity, work_id)
            return False
        identity_key = self._cache_identity(identity, work_id, request_value, model, policy_version,
                                            expected_generation=expected_generation)
        if identity_key is None:
            return False
        key, generation = identity_key
        if isinstance(response, dict):
            try:
                body = json.dumps(response, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError, RecursionError):
                raise WorkRuntimeError("invalid_cache_response") from None
        elif isinstance(response, bytes):
            body = response
        else:
            raise WorkRuntimeError("invalid_cache_response")
        if not body or len(body) > _MAX_RESPONSE_BYTES:
            return False
        safe_headers = {}
        for name, value in (headers or {}).items():
            if name.lower() in {"content-type"} and isinstance(value, str) and len(value) <= 128 and "\r" not in value and "\n" not in value:
                safe_headers[name.lower()] = value
        with self._transaction(write=True) as connection:
            work = self._work(connection, _identity(identity), work_id)
            if work["cache_generation"] != generation or work["state"] != "active":
                return False
            with self._cache_lock:
                if self._closed:
                    return False
                previous = self._cache.pop(key, None)
                if previous:
                    self._cache_size -= len(previous["body"])
                now = self._now()
                for expired in [item for item, entry in self._cache.items() if entry["expires_at"] <= now]:
                    self._cache_size -= len(self._cache.pop(expired)["body"])
                while self._cache and (len(self._cache) >= self._cache_entries or self._cache_size + len(body) > self._cache_bound):
                    _, removed = self._cache.popitem(last=False)
                    self._cache_size -= len(removed["body"])
                self._cache[key] = {"work_id": work_id, "body": body, "status_code": status_code,
                                    "headers": safe_headers, "expires_at": now + ttl_seconds}
                self._cache_size += len(body)
        return True

    def _evict_work(self, work_id):
        with self._cache_lock:
            for key in [key for key, value in self._cache.items() if value["work_id"] == work_id]:
                self._cache_size -= len(self._cache.pop(key)["body"])

    def cache_invalidate(self, identity, work_id):
        owner = _identity(identity)
        with self._transaction(write=True) as connection:
            self._work(connection, owner, work_id)
            connection.execute("UPDATE ai_work_jobs SET cache_generation=cache_generation+1 WHERE work_id=?", (work_id,))
        self._evict_work(work_id)
        return {"invalidated": True, "work_id": work_id}
