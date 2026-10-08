"""Non-secret work-runtime configuration; credential resolution stays at startup."""

from pathlib import Path
import re

from .config import AIWorkConfig, ConfigError


def build_ai_work(raw, *, source_path):
    value = raw.get("ai_work", {})
    enabled = value.get("enabled", False)
    cache = value.get("cache_enabled", False)
    paid = value.get("require_paid", False)
    if any(type(item) is not bool for item in (enabled, cache, paid)):
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
    price = value.get("billing_price_id")
    if price is not None and (type(price) is not str or not re.fullmatch(r"price_[A-Za-z0-9]{1,128}", price)):
        raise ConfigError("ai_work.billing_price_id is invalid")
    secret_env = value.get("billing_webhook_secret_env", "HORMUZ_WORK_BILLING_WEBHOOK_SECRET")
    if type(secret_env) is not str or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", secret_env):
        raise ConfigError("ai_work.billing_webhook_secret_env is invalid")
    if paid and not price:
        raise ConfigError("ai_work.require_paid requires billing_price_id")
    api_env = value.get("billing_api_key_env", "HORMUZ_WORK_BILLING_API_KEY")
    if type(api_env) is not str or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", api_env):
        raise ConfigError("ai_work.billing_api_key_env is invalid")
    bindings = value.get("billing_bindings", [])
    if type(bindings) is not list or len(bindings) > 1000:
        raise ConfigError("ai_work.billing_bindings is invalid")
    approved = []
    for binding in bindings:
        if type(binding) is not dict or set(binding) != {"organization_id", "customer_id", "subscription_id"}:
            raise ConfigError("ai_work.billing_bindings is invalid")
        organization, customer, subscription = (binding[key] for key in ("organization_id", "customer_id", "subscription_id"))
        if any(type(item) is not str for item in (organization, customer, subscription)) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", organization) or not re.fullmatch(r"cus_[A-Za-z0-9]{1,128}", customer) or not re.fullmatch(r"sub_[A-Za-z0-9]{1,128}", subscription):
            raise ConfigError("ai_work.billing_bindings is invalid")
        if any(organization == row[0] or customer == row[1] or subscription == row[2] for row in approved):
            raise ConfigError("ai_work.billing_bindings must have unique ownership")
        approved.append((organization, customer, subscription))
    tool_aliases = value.get("tool_capable_aliases", [])
    if type(tool_aliases) is not list or len(tool_aliases) > 100 or any(type(alias) is not str or alias not in raw.get("model_routes", {}) for alias in tool_aliases) or len(set(tool_aliases)) != len(tool_aliases):
        raise ConfigError("ai_work.tool_capable_aliases must reference unique configured models")
    if not enabled and (cache or paid):
        raise ConfigError("ai_work must be enabled for caching or paid entitlements")
    return AIWorkConfig(enabled, path.resolve() if enabled or "database" in value else None, cache, samples, tuple(actors), paid, price, secret_env, api_env, tuple(approved), tuple(tool_aliases))
