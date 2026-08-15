# Hormuz architecture

Hormuz sits on the provider request path while employees continue using Codex or Claude Code.

```text
Codex / Claude Code
        |
        | employee Hormuz identity token
        v
Hormuz HTTP transport
        |
        +--> authenticate identity and snapshot team metadata
        +--> resolve organization -> team -> person policy
        +--> allow, deny, reroute, or cap the request
        +--> enforce provider storage policy
        +--> redact or deny detected secret material
        +--> replace the employee token with the company provider key
        v
OpenAI Responses API / Anthropic Messages API
        |
        +--> stream response to the original client
        +--> parse provider usage metadata without retaining content
        v
SQLite usage ledger
```

## Code boundaries

- `hormuz/server.py` owns HTTP compatibility, authentication, upstream forwarding, streaming, and protocol-shaped errors.
- `hormuz/config.py` validates configuration and defines identity, route, rate-card, and policy data.
- `hormuz/policy.py` evaluates access, fallback, caps, and budgets without transport concerns.
- `hormuz/store.py` owns the SQLite schema and monthly aggregations.
- `hormuz/usage.py` parses provider usage metadata without storing response content.
- `hormuz/redaction.py` transforms provider-bound JSON values using configured secret controls.
- `hormuz/cli.py` exposes serving, diagnostics, policy checks, client configuration, and usage reporting.

## Trust boundary

Hormuz is trusted with plaintext requests and responses because it must inspect and relay them. The current store is deliberately metadata-only. Redaction runs after authentication and policy selection but before upstream serialization. Future reusable-context injection must run after authorization and before redaction so newly added context is inspected by the same egress controls.

## Compatibility boundary

Hormuz implements the provider endpoints required by Codex and Claude Code rather than inventing a new employee-facing client. Provider protocol changes are compatibility risks and require executable conformance tests.
