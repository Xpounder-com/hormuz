"""Quiescent AI Work owner locking, integrity verification and closed recovery."""
from contextlib import contextmanager, closing
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time

from ._hosted_config import HostedError

WORK_DATABASE = "hormuz-work.sqlite3"
BILLING_DATABASE = "hormuz-work.billing.sqlite3"


def load_profile(path):
    """Explicit offline full configuration; never starts a listener or provider.

    The existing session owner validates parents, key binding and marker for
    every operation. Keep exact filenames rather than following arbitrary
    SQLite paths into a multi-file restore.
    """
    from .config import GatewayConfig, ConfigError
    from ._hosted_provider import _safe_configuration_file
    _safe_configuration_file(path)
    try:
        config = GatewayConfig.load(path)
    except (ConfigError, OSError, ValueError):
        raise HostedError("hosted_work_recovery_configuration_invalid") from None
    settings = config.session_broker
    directory = config.database_path.parent
    if not config.ai_work.enabled or config.usage_storage.backend != "sqlite" or not settings.enabled or config.database_path != directory / "usage.sqlite3" or settings.database_path != directory / "sessions.sqlite3" or config.ai_work.database_path != directory / WORK_DATABASE:
        raise HostedError("hosted_work_recovery_configuration_invalid")
    return config


@contextmanager
def owner_lock(path, *, exclusive=True):
    import fcntl
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".owner.lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise HostedError("hosted_work_owner_lock_unsafe")
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HostedError("hosted_work_owner_active") from None
        yield
    finally:
        os.close(descriptor)


def owned_databases(config):
    if not config.ai_work.enabled:
        return ()
    if config.usage_storage.backend != "sqlite":
        raise HostedError("hosted_work_consistent_recovery_requires_sqlite")
    if config.ai_work.database_path != config.database_path.parent / WORK_DATABASE:
        raise HostedError("hosted_work_state_binding_mismatch")
    return (WORK_DATABASE, BILLING_DATABASE) if config.ai_work.billing_price_id else (WORK_DATABASE,)


def validate_store(path, *, billing=False):
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchone():
            raise HostedError("hosted_work_recovery_integrity_invalid")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {"work_entitlements", "work_billing_events", "work_activation", "work_checkouts", "work_payment_facts"} if billing else {"ai_work_schema", "ai_work_plans", "ai_work_jobs", "ai_work_attempts", "ai_work_observations"}
        if not expected <= tables:
            raise HostedError("hosted_work_recovery_schema_invalid")


def close_restored(directory, names):
    now = int(time.time())
    for name in names:
        path = Path(directory) / name
        validate_store(path, billing=name == BILLING_DATABASE)
        with closing(sqlite3.connect(path)) as connection, connection:
            if name == WORK_DATABASE:
                # Unknown/pending spend remains reserved after recovery.
                connection.execute("UPDATE ai_work_attempts SET state='unknown' WHERE state='pending'")
                connection.execute("UPDATE ai_work_jobs SET state='paused',pause_reason='recovery_required',cache_generation=cache_generation+1,version=version+1 WHERE state NOT IN ('stopped','completed')")
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "ai_work_bridge_events" in tables:
                    connection.execute("UPDATE ai_work_bridge_events SET status='processed',reason='recovery_closed' WHERE status='pending'")
                if "ai_work_bindings" in tables:
                    connection.execute("UPDATE ai_work_bindings SET latest_event_at=MAX(COALESCE(latest_event_at,0),?)", (now,))
            else:
                connection.execute("UPDATE work_entitlements SET subscription_state='recovery_required',subscription_at=?,paid_at=?,recovery_after=?,paid_until=0,subscription_until=0", (now, now, now))
                connection.execute("UPDATE work_activation SET state='recovery_required',generation=?,updated_at=?,version=version+1", (secrets.token_urlsafe(24), now))
                connection.execute("UPDATE work_checkouts SET state='blocked',url=NULL")
                connection.execute("DELETE FROM work_payment_facts")
