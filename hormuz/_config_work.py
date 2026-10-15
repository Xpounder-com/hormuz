"""Non-secret work-runtime configuration; credential resolution stays at startup."""

from pathlib import Path
import re

from .config import AIWorkConfig, ConfigError


def build_ai_work(raw, *, source_path):
    value = raw.get("ai_work", {})
    if any(key.startswith("billing_") or key == "require_paid" for key in value):
        raise ConfigError("router_scope_retired_configuration")
    enabled = value.get("enabled", False)
    cache = value.get("cache_enabled", False)
    if any(type(item) is not bool for item in (enabled, cache)):
        raise ConfigError("ai_work flags must be boolean")
    samples = value.get("minimum_samples", 5)
    if type(samples) is not int or not 3 <= samples <= 1000:
        raise ConfigError("ai_work.minimum_samples must be between 3 and 1000")
    actors = value.get("administrator_actor_ids", [])
    if type(actors) is not list or len(actors) > 1000 or any(
        type(actor) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", actor)
        for actor in actors
    ) or len(set(actors)) != len(actors):
        raise ConfigError("ai_work.administrator_actor_ids is invalid")
    database = value.get("database", "hormuz-work.sqlite3")
    if type(database) is not str or not database.strip() or "\x00" in database:
        raise ConfigError("ai_work.database is invalid")
    path = Path(database).expanduser()
    if not path.is_absolute():
        path = source_path.parent / path
    tool_aliases = value.get("tool_capable_aliases", [])
    if type(tool_aliases) is not list or len(tool_aliases) > 100 or any(type(alias) is not str or alias not in raw.get("model_routes", {}) for alias in tool_aliases) or len(set(tool_aliases)) != len(tool_aliases):
        raise ConfigError("ai_work.tool_capable_aliases must reference unique configured models")
    if not enabled and cache:
        raise ConfigError("ai_work must be enabled for caching")
    exploration = value.get("exploration_enabled", False)
    aliases = value.get("exploration_aliases", [])
    rate = value.get("exploration_rate_percent", 5)
    ceiling = value.get("exploration_max_cost_microusd", 0)
    if type(exploration) is not bool or type(aliases) is not list or len(aliases) > 100 or any(type(alias) is not str or alias not in raw.get("model_routes", {}) for alias in aliases) or len(set(aliases)) != len(aliases):
        raise ConfigError("ai_work.exploration_aliases must reference unique approved models")
    if type(rate) is not int or not 1 <= rate <= 20 or type(ceiling) is not int or not 0 <= ceiling <= 9_000_000_000_000_000:
        raise ConfigError("ai_work exploration sampling or monthly allowance is invalid")
    if exploration and (not enabled or not aliases or ceiling == 0):
        raise ConfigError("ai_work exploration requires enabled work, approved models and a monthly allowance")
    return AIWorkConfig(enabled=enabled, database_path=path.resolve() if enabled or "database" in value else None,
        cache_enabled=cache, minimum_samples=samples, administrator_actor_ids=tuple(actors),
        tool_capable_aliases=tuple(tool_aliases), exploration_enabled=exploration,
        exploration_aliases=tuple(aliases), exploration_rate_percent=rate, exploration_max_cost_microusd=ceiling)
