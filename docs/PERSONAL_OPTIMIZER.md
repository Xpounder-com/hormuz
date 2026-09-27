# Hormuz Personal Optimizer 0.1.0

Hormuz Personal Optimizer is an independently versioned artifact shipped in
the `hormuz` Python distribution. It lets one developer keep using a supported
coding agent while a launcher-owned loopback relay applies qualified local
optimizations and records bounded, content-free measurements.

The machine-readable boundary is packaged as
`hormuz/personal-release-v1.json` and is available with:

```sh
hormuz personal contract --json
```

## Initial release boundary

- Supported release surface: macOS 14 or newer on Apple silicon, terminal use.
- Initial pinned agent: Codex CLI 0.148.0 over the Responses protocol. Its
  executable and loopback transport pass the provider-free real-client test;
  live provider/model interpretation remains a separate authorized gate. The
  existing managed context release keeps its independently pinned 0.147.0
  compatibility baseline.
- Installation: `python -m pip install 'hormuz[client,context]'` followed by
  `hormuz context resources install`.
- Server dependencies: none. Direct mode does not need an organization,
  PostgreSQL, or a Hormuz gateway.
- Release claim: for qualified Codex requests containing supported repetitive
  `rg` output, Hormuz uses a bounded lossless representation only when both
  maintained token counters show a useful reduction. Unsupported traffic uses
  the ordinary request path unchanged.

This does not claim a provider bill reduction unless provider evidence supplies
that basis. It does not close the portfolio roadmap, native roadmap, existing
v1.3 commitments, or physical Windows acceptance. The personal modules are
separate, but the first release changes the shared relay and compaction path, so
the full applicable CI suite remains required.

## Connect directly

Provider credentials are read from an explicitly named environment variable
and moved into the operating-system keyring. They are never accepted as a CLI
argument and never written to the profile or metrics files. The profile keeps
only the non-secret variable name so a later shell value under that name is
also removed from the launched agent environment.

```sh
export OPENAI_API_KEY='your-provider-key'
hormuz personal connect \
  --profile personal-codex \
  --mode direct \
  --agent codex \
  --provider openai \
  --model YOUR_MODEL \
  --credential-env OPENAI_API_KEY
unset OPENAI_API_KEY

hormuz personal run --profile personal-codex
```

The launch uses transient command and environment overrides. It does not edit
Codex configuration. The child receives a fresh loopback credential, while the
provider credential stays in the OS keyring and is attached only to the fixed
HTTPS upstream. Loopback HTTP is available only through an explicit test flag;
non-loopback HTTP is rejected.

Rotate a direct credential from the same named variable without changing the
profile or clearing local measurements:

```sh
export OPENAI_API_KEY='replacement-provider-key'
hormuz personal credential \
  --profile personal-codex \
  --credential-env OPENAI_API_KEY
unset OPENAI_API_KEY
```

## Use an existing managed profile

Managed mode retains the existing Hormuz session and governance path. The
personal runner verifies that gateway, agent, and model still match the saved
managed profile on every launch. It never falls back to direct access.

```sh
hormuz personal connect --profile MANAGED_PROFILE_UUID --mode managed
hormuz personal run --profile MANAGED_PROFILE_UUID
```

Removing the personal integration does not log out or delete the managed
session because those are separate user actions.

## Benefit and control

```sh
hormuz personal benefit --profile personal-codex
hormuz personal benefit --profile personal-codex --json
hormuz personal off --profile personal-codex
hormuz personal on --profile personal-codex
hormuz personal clear --profile personal-codex
hormuz personal remove --profile personal-codex
```

The compact view reports:

- eligible, total, and transformed request counts;
- exact before/after serialized request bytes over all captured and eligible
  traffic;
- local before/after token estimates over all captured traffic and eligible
  traffic;
- provider-reported input, output, cache-read, cache-write, reasoning, and total
  usage when present;
- optimizer overhead, time to first byte, total latency, and exceptions;
- actual cost only when the provider explicitly reports an unambiguous
  `actual_cost_microusd` field.

Missing observations remain missing rather than becoming zero. Token estimates
are labelled as estimates, not bills, and the all-traffic token result remains
unavailable unless token observations cover every captured request. Exact byte
reduction remains separately visible. Cache-adjusted monetary savings remain
unknown without request-level provider billing evidence. The current structural
transform uses no auxiliary model calls and does not retry a failed provider
request, so those counts are normally zero rather than missing.

The persisted file contains only bounded counters, sums, maxima, reason codes,
and up to 30 numeric active-day ordinals. The relay may inspect at most 1 MiB of
a response in memory to extract usage while streaming bytes immediately; it
does not persist messages, responses, semantic content, content hashes, or
tool output. `clear` deletes measurements. `remove` also deletes the direct
credential, personal profile, and preference. Since agent configuration was
never changed, removal restores the ordinary setup by construction.

## Caching and regression guard

Structural encoding is deterministic and pinned to `structural-v1` for the
profile, so a compatible history keeps the same representation. Start a new
conversation after toggling the feature to avoid changing a provider cache
prefix mid-history.

A candidate must already pass exact reconstruction, byte non-growth, at least
32 estimated tokens saved, and at least five percent savings under both pinned
tokenizers. The personal relay additionally rejects a changed candidate when
local optimizer work exceeds the bounded overhead guard. The ordinary request
is sent instead and the reason is reported as `net_regression`. Provider cache
categories, auxiliary calls, fallback calls, and optimizer delay remain visible
so a future priced comparison cannot omit them.

## Qualification and limitations

Product fixtures in `tests/fixtures/context_compaction/cases.json` cover counts,
signed values, rare exceptions, JSON types, Unicode, file/line navigation,
duplicate paths, instruction attacks, and marker collisions. The paired
evaluator in `tools/evaluate_context_compaction.py` runs original and compact
arms with identical settings and records task success, provider usage, retries,
and latency. Live model runs remain separately authorized and spend-capped.

The automated release checks exercise profile/credential separation, exact
reconstruction, provider authentication, streaming usage extraction, failures,
cancellation, the Off byte path, adapter conformance, and a built-wheel smoke
install. A green provider-free check proves the local contract, not a live
provider's availability, invoice, task quality, or release publication.

Optional real-client tests run the installed Codex 0.148.0 and Aider 0.86.2
executables against local scripted providers. They prove endpoint routing,
tool/chat request completion, credential replacement, and measurement capture
without a paid call. They still do not prove live-model interpretation or a
fresh-machine release installation.

Run the packaged, provider-free product fixtures with:

```sh
hormuz personal qualify --json
```

## Aider adoption integration

Aider 0.86.2 is the first additional open-source agent adapter. Its pinned
executable completes the provider-free real-client acceptance path. Hormuz uses
Aider's documented [OpenAI-compatible endpoint and environment
settings](https://aider.chat/docs/llms/openai-compat.html), keeps
the local credential out of process arguments, and restores normal setup when
the process exits. Aider does not expose its local shell transcript as the
typed tool-result messages required by `structural-v1`; the benefit view
therefore reports zero eligible transforms honestly while still showing
captured usage and waiting. Managed Aider routing is rejected because the
current managed gateway does not expose Chat Completions.

Run the provider-free contract check with:

```sh
hormuz personal conformance --agent aider --json
```

See [Personal adapter SDK](PERSONAL_ADAPTERS.md) for the public interface and
[optional optimization experiments](PERSONAL_OPTIMIZATION_EXPERIMENTS.md) for
cycles 3–5.

## Voluntary product feedback

Default telemetry remains local. A developer can inspect or deliberately export
the same content-free benefit document:

```sh
hormuz personal feedback export \
  --profile personal-codex \
  --output personal-benefit.json
```

Hormuz does not transmit the file. Active-day and session counters can support
voluntary retention interviews, but no payment, pricing, recurring value, or
willingness-to-pay claim follows from installing or exporting it. The core,
adapter contract, secret controls, and basic useful path remain Apache-2.0.
