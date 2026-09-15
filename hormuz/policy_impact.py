"""Bounded, disposable request metadata for output-limit comparisons.

This repository is separate from the immutable usage ledger. Capture is optional,
asynchronous and lossy; it never authorizes requests or proves causal savings.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import stat
import threading
import time
from typing import Iterator

from .policy_repository import PolicyControlError
from .session_store import SQLiteSessionStore

MAX_OBSERVATIONS = 20_000
MAX_SCOPE_OBSERVATIONS = 4_000
MAX_PREVIEWS = 1_000
MAX_ORGANIZATION_PREVIEWS = 100
MAX_QUEUE = 256
RETENTION = timedelta(days=7)
PREVIEW_TTL = timedelta(minutes=10)
SWEEP_INTERVAL_SECONDS = 60
_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,256}\Z")
_STATES = {"pending", "succeeded", "failed", "rate_limited"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise PolicyControlError("impact_invalid_request")
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Observation:
    request_id: str
    organization_id: str
    team_id: str
    actor_id: str
    model_alias: str
    policy_version: str
    requested_limit: int | None
    effective_limit: int | None
    started_at: str
    routing_fingerprint: str
    status: str = "pending"
    output_tokens: int | None = None
    cost_microusd: int | None = None

    def validate(self) -> None:
        for name in ("request_id", "organization_id", "team_id", "actor_id", "policy_version", "routing_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise PolicyControlError("impact_invalid_observation")
        # Match routing configuration: aliases are nonempty strings, not IDs.
        if not isinstance(self.model_alias, str) or not self.model_alias.strip():
            raise PolicyControlError("impact_invalid_observation")
        for name in ("requested_limit", "effective_limit", "output_tokens", "cost_microusd"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
                raise PolicyControlError("impact_invalid_observation")
        if self.status not in _STATES:
            raise PolicyControlError("impact_invalid_observation")
        try:
            if datetime.fromisoformat(self.started_at).tzinfo is None:
                raise ValueError
        except (ValueError, TypeError):
            raise PolicyControlError("impact_invalid_observation") from None


class ImpactStore:
    """Single-host, owner-only, capped SQLite observations and reviewed proposals."""

    def __init__(self, path: Path, *, trusted_parent_path: Path | None = None):
        self.path = path.absolute()
        self._trusted_parent = trusted_parent_path
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._check_parent()
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise PolicyControlError("impact_storage_unsafe")
        finally:
            os.close(fd)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version == 0 and not tables:
                db.execute("CREATE TABLE observations (request_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, team_id TEXT NOT NULL, model_alias TEXT NOT NULL, policy_version TEXT NOT NULL, started_at TEXT NOT NULL, value TEXT NOT NULL)")
                db.execute("CREATE INDEX observation_scope ON observations (organization_id, team_id, model_alias, policy_version, started_at)")
                db.execute("CREATE TABLE previews (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, membership_id TEXT NOT NULL, expires_at TEXT NOT NULL, value TEXT NOT NULL)")
                db.execute("PRAGMA user_version = 1")
            elif version != 1 or tables != {"observations", "previews"}:
                raise PolicyControlError("impact_schema_unsupported")
            shapes = {
                "observations": ("request_id", "organization_id", "team_id", "model_alias", "policy_version", "started_at", "value"),
                "previews": ("id", "organization_id", "membership_id", "expires_at", "value"),
            }
            for table, columns in shapes.items():
                actual = tuple((r[1], r[2], r[3], r[5]) for r in db.execute("PRAGMA table_info(" + table + ")"))
                expected = tuple((name, "TEXT", int(index != 0), int(index == 0)) for index, name in enumerate(columns))
                if actual != expected:
                    raise PolicyControlError("impact_schema_unsupported")
            if tuple(r[2] for r in db.execute("PRAGMA index_info(observation_scope)")) != (
                "organization_id", "team_id", "model_alias", "policy_version", "started_at",
            ):
                raise PolicyControlError("impact_schema_unsupported")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone():
                raise PolicyControlError("impact_schema_unsupported")
            self._purge_expired(db)

    @staticmethod
    def _purge_expired(db) -> None:
        db.execute("DELETE FROM observations WHERE started_at < ?", (iso(utcnow() - RETENTION),))
        db.execute("DELETE FROM previews WHERE expires_at <= ?", (iso(utcnow()),))

    def purge_expired(self) -> None:
        with self.connection() as db:
            self._purge_expired(db)

    def _check_parent(self) -> None:
        SQLiteSessionStore._validate_parent_chain(self.path.parent, allow_trusted_symlinks=True, trusted_parent_path=self._trusted_parent)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self._check_parent()
        metadata = self.path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise PolicyControlError("impact_storage_unsafe")
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            try:
                metadata = sidecar.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise PolicyControlError("impact_storage_unsafe")
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=.25)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA secure_delete = ON")
            with db:
                yield db
        except sqlite3.Error:
            raise PolicyControlError("impact_storage_unavailable") from None
        finally:
            db.close()

    def record(self, observation: Observation) -> None:
        observation.validate()
        observation = replace(observation, started_at=iso(datetime.fromisoformat(observation.started_at)))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # Terminal metadata supersedes pending metadata, never the reverse.
            old = db.execute("SELECT value FROM observations WHERE request_id=?", (observation.request_id,)).fetchone()
            if old is not None:
                previous = json.loads(old[0])
                if any(previous[k] != asdict(observation)[k] for k in ("organization_id", "team_id", "actor_id", "model_alias", "policy_version", "requested_limit", "effective_limit", "started_at", "routing_fingerprint")):
                    raise PolicyControlError("impact_observation_conflict")
                if previous["status"] != "pending":
                    return
            db.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?,?,?)", (
                observation.request_id, observation.organization_id, observation.team_id, observation.model_alias,
                observation.policy_version, observation.started_at, json.dumps(asdict(observation), sort_keys=True),
            ))
            self._purge_expired(db)
            db.execute("DELETE FROM observations WHERE request_id IN (SELECT request_id FROM observations WHERE organization_id=? ORDER BY started_at DESC, request_id DESC LIMIT -1 OFFSET ?)", (observation.organization_id, MAX_SCOPE_OBSERVATIONS))
            db.execute("DELETE FROM observations WHERE request_id IN (SELECT request_id FROM observations ORDER BY started_at DESC, request_id DESC LIMIT -1 OFFSET ?)", (MAX_OBSERVATIONS,))

    def observations(self, *, organization_id: str, team_id: str, model_alias: str, policy_version: str) -> tuple[Observation, ...]:
        with self.connection() as db:
            self._purge_expired(db)
            rows = db.execute("SELECT value FROM observations WHERE organization_id=? AND team_id=? AND model_alias=? AND policy_version=? AND started_at>=? ORDER BY started_at DESC, request_id DESC LIMIT ?", (
                organization_id, team_id, model_alias, policy_version, iso(utcnow() - RETENTION), MAX_SCOPE_OBSERVATIONS,
            )).fetchall()
        observations = tuple(Observation(**json.loads(row[0])) for row in rows)
        for observation in observations:
            observation.validate()
        return observations

    def save_preview(self, *, preview_id: str, organization_id: str, membership_id: str, expires_at: str, value: dict) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._purge_expired(db)
            if db.execute("SELECT COUNT(*) FROM previews WHERE organization_id=?", (organization_id,)).fetchone()[0] >= MAX_ORGANIZATION_PREVIEWS:
                raise PolicyControlError("impact_capacity_reached")
            if db.execute("SELECT COUNT(*) FROM previews").fetchone()[0] >= MAX_PREVIEWS:
                raise PolicyControlError("impact_capacity_reached")
            db.execute("INSERT INTO previews VALUES (?,?,?,?,?)", (preview_id, organization_id, membership_id, expires_at, json.dumps(value, sort_keys=True)))

    def recent_preview_ids(self, *, organization_id: str, membership_id: str) -> tuple[str, ...]:
        with self.connection() as db:
            self._purge_expired(db)
            return tuple(row[0] for row in db.execute(
                "SELECT id FROM previews WHERE organization_id=? AND membership_id=? AND expires_at>? ORDER BY expires_at DESC LIMIT 20",
                (organization_id, membership_id, iso(utcnow())),
            ))

    def preview(self, *, preview_id: str, organization_id: str, membership_id: str) -> dict:
        with self.connection() as db:
            self._purge_expired(db)
            row = db.execute("SELECT value FROM previews WHERE id=? AND organization_id=? AND membership_id=? AND expires_at>?", (preview_id, organization_id, membership_id, iso(utcnow()))).fetchone()
        if row is None:
            raise PolicyControlError("impact_preview_expired")
        return json.loads(row[0])


def compare(observations: tuple[Observation, ...], proposed_limit: int) -> dict[str, object]:
    if type(proposed_limit) is not int or not 1 <= proposed_limit <= 1_000_000:
        raise PolicyControlError("impact_invalid_request")
    for item in observations:
        item.validate()
    affected = sum(item.effective_limit is None or item.effective_limit > proposed_limit for item in observations)
    completed = [item for item in observations if item.status == "succeeded" and item.output_tokens is not None]
    return {
        "schema_id": "hormuz.policy-impact-comparison", "schema_version": 1,
        "captured_requests": len(observations), "lower_limit_requests": affected,
        "unchanged_limit_requests": len(observations) - affected,
        "known_completions": len(completed),
        "completions_above_limit": sum(item.output_tokens > proposed_limit for item in completed),
        "unknown_completions": len(observations) - len(completed),
        "coverage": "bounded_captured_sample_only", "availability": "available" if observations else "no_observations",
        "savings": None, "quality_effect": None,
    }


class ImpactRecorder:
    """A bounded nonblocking queue; a capture failure never changes admission."""

    def __init__(self, store: ImpactStore):
        self.store = store
        self._queue: queue.Queue[Observation] = queue.Queue(MAX_QUEUE)
        self._stop = threading.Event()
        self.dropped = 0
        self._worker = threading.Thread(target=self._run, name="hormuz-impact-capture", daemon=True)
        self._worker.start()

    def submit(self, observation: Observation) -> None:
        try:
            self._queue.put_nowait(observation)
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        next_sweep = time.monotonic() + SWEEP_INTERVAL_SECONDS
        while not self._stop.is_set() or not self._queue.empty():
            if time.monotonic() >= next_sweep:
                try:
                    self.store.purge_expired()
                except (OSError, ValueError, RuntimeError, sqlite3.Error):
                    pass
                next_sweep = time.monotonic() + SWEEP_INTERVAL_SECONDS
            try:
                observation = self._queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                self.store.record(observation)
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                self.dropped += 1
            finally:
                self._queue.task_done()

    def close(self) -> None:
        self._stop.set()
        self._worker.join(timeout=2)


def impact_path(session_path: Path) -> Path:
    return session_path.with_name(session_path.name + ".policy-impact.sqlite3")


def routing_fingerprint(config) -> str:
    """Pin credential-free route definitions used by the captured comparison."""
    value = {alias: asdict(route) for alias, route in sorted(config.model_routes.items())}
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
