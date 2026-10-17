# Security policy

Koto is experimental and has not received an independent security audit. Keep
the local router on loopback. Do not expose it as an internet service or infer
production certification from routine tests.

Report vulnerabilities through GitHub's [private reporting form](https://github.com/mehrdadxzaker/hormuz/security/advisories/new).
If that channel is unavailable, open a content-free installation issue saying
only that private reporting is unavailable. Never put vulnerabilities, real keys,
tokens, prompts, responses, customer data, private hostnames or raw logs in public
issues, PRs or commits. Include a synthetic reproduction and exact source/package
revision in the private report. Revoke an exposed credential at its authoritative
provider; repository cleanup does not revoke it.

Current source fixes follow the local-router development line. Backports to
older releases are not guaranteed. Support and exact qualification limitations
are documented in [SUPPORT.md](SUPPORT.md).

The approved workflow preserves local caller authentication, provider credential
isolation, policy and secret inspection before serialization, destination
storage permissions, local-only admission including helpers, continuation
identity, cancellation and conservative unknown outcomes. Routine evidence
contains metadata rather than stored request/response bodies. Exact answer reuse
is separately opt-in and bounded in process memory. Do not add prompt, transcript
or credential-bearing logs as part of observability.

Local-only covers intercepted model requests. Other client tools, plugins and
connections need their own controls. Subscription eligibility, OS integration,
real model quality and plugin distribution require separate qualification.
Legacy cloud data and account obligations remain in [the operational track](docs/OPERATIONS_CLEANUP.md).
