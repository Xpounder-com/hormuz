# Hormuz public-alpha support

Hormuz support is community-maintainer, best-effort support for public-alpha
evaluation. There is no response, remediation, uptime, compatibility, or
enterprise-support SLA. Maintainers aim to triage a complete public report
within five business days, but this is a goal rather than a guarantee.

## Choose a support path

| Need | Public path |
| --- | --- |
| Installation or first-run failure | [Installation report](https://github.com/Xpounder-com/hormuz/issues/new?template=installation.yml) |
| Reproducible Hormuz defect | [Bug report](https://github.com/Xpounder-com/hormuz/issues/new?template=bug.yml) |
| Documentation error or confusion | [Documentation report](https://github.com/Xpounder-com/hormuz/issues/new?template=documentation.yml) |
| Proposed capability or workflow | [Feature request](https://github.com/Xpounder-com/hormuz/issues/new?template=feature.yml) |
| Vulnerability or sensitive security behavior | Follow [SECURITY.md](SECURITY.md); never use a public issue |
| Conduct incident | Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |

Public reports must use synthetic inputs. Remove access tokens, provider keys,
prompts, responses, customer identifiers, usernames, email addresses, internal
hostnames, private repository names, filesystem paths, and database contents
from screenshots, logs, commands, and attachments.

## Verified compatibility boundary

The table states what the release gates actually exercise. It is not a promise
that every nearby version or platform works.

| Surface | Public-alpha boundary |
| --- | --- |
| Python source install | CPython 3.11, 3.12, 3.13, and 3.14 on GitHub-hosted `ubuntu-latest` runners |
| Host operating system | Linux is release-gated; macOS and Windows source installs are community best effort and not release-gated |
| Official OCI image | Linux `amd64` only; the signed digest is the artifact contract |
| Docker Compose pilot | One native Linux AMD64 VM, one gateway replica, the exact signed Hormuz digest, and one private digest-pinned PostgreSQL service; evaluation/pilots only, not HA or production certification |
| Kubernetes + Helm reference | One disposable Linux AMD64 Kind `v0.32.0` cluster, Kubernetes `v1.36.1`, Helm `v3.21.4`, Cilium `1.20.1`, two Hormuz replicas, and customer-fixture PostgreSQL; Cilium is the first tested CNI, not a dependency, and the result is not HA/DR or production certification |
| Native ARM64 | Not supported for the first image; tracked separately in [#109](https://github.com/Xpounder-com/hormuz/issues/109) |
| Docker/OCI tooling | A BuildKit/buildx-capable Docker environment is used for local verification; Buildx `v0.36.1` is pinned in CI, but Hormuz does not claim every Docker Engine release |
| Codex | `@openai/codex` `0.147.0`, installed with Node.js 24 and routed through loopback fake providers in blocking CI |
| Claude Code | `@anthropic-ai/claude-code` `2.1.233`, installed with Node.js 24 and routed through loopback fake providers in blocking CI |
| Newer client releases | A non-blocking weekly canary detects drift; a green canary does not silently expand the supported-version contract |
| Provider credentials | Ordinary tests require none. Exact same-revision live Codex/OpenAI and Claude Code/Anthropic BYO-provider evidence is recorded in [#115](https://github.com/Xpounder-com/hormuz/issues/115) and [workflow run 32884601758](https://github.com/Xpounder-com/hormuz/actions/runs/32884601758). It does not establish provider-invoice reconciliation, every client feature, traffic bypassing Hormuz, or enterprise production readiness. |
| Default persistence | SQLite for one-process local evaluation; it is not a shared or HA store |
| PostgreSQL | Optional compatibility, migration, RLS, pooling, and recovery proofs exist. The first exact HA reference is CloudNativePG `1.30.0` with three PostgreSQL `16.15` instances in single-host Kind; it is not a managed production database service or certification, production storage/operations, broad HA/DR, or a customer SLA |

The official image and source tree expose additional self-hosted and cloud
conformance harnesses. Those prove only the exact documented, explicitly run
profiles. They do not expand this alpha's general platform or production
support boundary.

## What makes a report actionable

Include:

1. the Hormuz commit or installed package version;
2. operating system, architecture, Python version, installation method, and
   whether Docker or PostgreSQL is involved;
3. the smallest synthetic reproduction and exact command;
4. expected behavior and sanitized observed behavior;
5. whether the failure occurs before provider egress;
6. the tests or workarounds already tried.

Do not assume an issue is a security report merely because it involves
authentication or policy. Use the private path whenever public reproduction
could reveal a bypass, sensitive tenant fact, credential, or customer data.

## Out of scope for public-alpha support

The public issue tracker does not provide architecture certification,
production deployment approval, incident response, provider billing disputes,
customer data review, private debugging sessions, custom integration delivery,
or guaranteed compatibility support. Those require a separate future
commercial or design-partner agreement and must not be inferred from a GitHub
response.
