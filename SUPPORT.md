# Support and qualification

Hormuz is experimental source software with best-effort community support. The
current direction is [a local router for existing coding clients](docs/ROUTER_SCOPE.md).
No uptime, support, security certification or broad compatibility SLA is offered.

| Surface | Current evidence boundary |
| --- | --- |
| Python | CI targets CPython 3.11–3.14 on Ubuntu; local results are reported separately |
| Persistent connection | Codex CLI version pin `0.147.0`; routine tests use a disposable executable with its configuration/version contract |
| Destination and admission behavior | Real router against bounded loopback local/cloud provider fixtures |
| Client/platform support | Synthetic success does not qualify an installed Codex desktop engine, every client version or operating system |
| Local engine/model | Real llama.cpp/model/task quality and exact capabilities still require qualification |
| Subscription | No synthetic fixture establishes plan consent, account entitlement or zero API spend |
| Plugin | Skills-only setup is planned over the shared CLI; this cleanup does not ship a plugin |
| Claude | Transport prior art does not establish supported non-Claude main-model substitution or Claude GUI integration |
| Storage | Customer-local SQLite; no hosted/PostgreSQL deployment product in the 2.0 line |

Use [installation](https://github.com/Xpounder-com/hormuz/issues/new?template=installation.yml),
[bug](https://github.com/Xpounder-com/hormuz/issues/new?template=bug.yml),
[documentation](https://github.com/Xpounder-com/hormuz/issues/new?template=documentation.yml)
or [feature](https://github.com/Xpounder-com/hormuz/issues/new?template=feature.yml)
reports. Include the source/package revision, OS, Python/client versions and a
small synthetic reproduction. Never submit keys, tokens, prompts, responses,
private repository names, local user paths or database contents publicly.

Use [private vulnerability reporting](SECURITY.md) for sensitive defects and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for conduct. Older published releases and
external resources are separate from the source cleanup; see [migration](docs/MIGRATION_2.md)
and [operational cleanup](docs/OPERATIONS_CLEANUP.md).
