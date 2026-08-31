"""Synthetic accounting values, not provider pricing or live finance evidence."""


def openai_usage():
    return {
        "object": "organization.usage.completions.result",
        "input_tokens": 1000, "output_tokens": 200, "num_model_requests": 2,
        "input_uncached_tokens": 600, "input_cached_tokens": 300,
        "input_cache_write_tokens": 100,
        "input_text_tokens": 600, "input_image_tokens": 0, "input_audio_tokens": 0,
        "input_cached_text_tokens": 300, "input_cached_image_tokens": 0,
        "input_cached_audio_tokens": 0,
        "output_text_tokens": 200, "output_image_tokens": 0, "output_audio_tokens": 0,
    }


def anthropic_usage():
    return {
        "uncached_input_tokens": 600, "cache_read_input_tokens": 300,
        "cache_creation": {"ephemeral_5m_input_tokens": 80, "ephemeral_1h_input_tokens": 20},
        "output_tokens": 200, "server_tool_use": {"web_search_requests": 0},
    }


def rate_card(provider="openai", *, version=1):
    rates = {"uncached_input": "2", "cache_read": "0.5", "output": "8"}
    rates.update({"cache_write": "2"} if provider == "openai" else {"cache_write_5m": "2.5", "cache_write_1h": "4"})
    return {
        "schema_id": "hormuz.finance-rate-card", "schema_version": 1,
        "organization_id": "acme", "rate_card_id": "synthetic-rate-card", "version": version,
        "provider": provider, "actual_model": "synthetic-model-v1", "currency": "USD",
        "effective_from": "2026-08-01T00:00:00Z", "effective_to": "2026-09-01T00:00:00Z",
        "service_tier": "default" if provider == "openai" else "standard", "batch": False,
        "pricing_profile": "openai_text_tokens_v1" if provider == "openai" else "anthropic_messages_tokens_v1",
        "source_kind": "operator_configured", "unit": "per_million_tokens",
        "rounding": "exact_or_unavailable_v1", "rates": rates,
    }


def estimate_context(provider="openai"):
    return {
        "organization_id": "acme", "actual_model": "synthetic-model-v1",
        "event_at": "2026-08-15T12:00:00Z", "service_tier": "default" if provider == "openai" else "standard",
        "batch": False,
    }
