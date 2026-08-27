<!-- hormuz-launch-asset-v2 {"asset_id":"terminal_demo","publication_status":"draft_do_not_publish","claim_ids":["ALPHA_BOUNDARY","PROVIDER_FREE_DEMO"]} -->

# DRAFT — DO NOT PUBLISH

## Five-minute terminal demonstration

This is the canonical screen-recording and live-demo script for the first
public alpha. Record it from a clean checkout on the release-gated Linux source
path. Use only synthetic values and show the full terminal so the installation
and result are reproducible.

### 1. Explain the boundary

Say:

> This is Hormuz running its real gateway path against disposable local
> provider simulators. It does not need an OpenAI or Anthropic account, spend
> money, or send a prompt outside this machine.

This is a public alpha, not production-ready. External onboarding validation pending: 0/5 independent testers. Do not describe the demo as validated
onboarding, production deployment, live-provider certification, HA, recovery,
or an independent security review. <!-- claims: ALPHA_BOUNDARY -->

### 2. Install from the clean checkout

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
```

Do not edit the output to hide a dependency download, warning, or failure. If
installation fails, stop the recording and file a sanitized installation
report instead of splicing together separate attempts.

### 3. Run the real provider-free path

```bash
hormuz demo
```

Expected diagnostic output:

```text
Hormuz provider-free governed-policy demo
PASS allowed request reached the loopback provider simulator
PASS unapproved model was rerouted and output-capped
PASS detected secret was redacted before provider egress
PASS denied request made no provider call
PASS content-free evidence validated: 4 usage events, 1 security event
PASS external provider calls: 0 (3 loopback simulator calls)
Completed in <elapsed> seconds; temporary evidence removed
```

The elapsed time varies. Every `PASS` line must appear exactly as documented.
The command exercises Hormuz's real HTTP, policy, redaction, request-attempt,
and SQLite evidence path, then deletes its temporary configuration and
database. <!-- claims: PROVIDER_FREE_DEMO -->

### 4. Narrate what changed

Use this 30-second explanation:

> Four requests entered the same gateway. One was allowed, one was rerouted
> and capped by policy, one had a synthetic secret redacted before the local
> provider saw it, and one was denied without any provider call. Hormuz then
> validated metadata-only usage and security evidence and removed the
> temporary state.

### 5. Run the local administrator path

Run this as a separate command; do not replace or edit the gateway transcript
above:

```bash
hormuz policy demo
```

Expected diagnostic output:

```text
Hormuz zero-network policy administrator demo
PASS created standard baseline and strict local candidate policies
PASS validated both local policy documents
PASS semantic comparison found 3 policy changes
PASS created 2 explicit policy scenarios
PASS disposable SQLite current usage: 0 requests, 0 tokens, USD 0
PASS evaluated 2 scenarios with 2 behavior changes: 8000-token request uncapped -> capped at 4000; demo-deep allowed -> denied
PASS network calls: 0; provider credentials: 0; policy mutations: 0
Temporary artifacts removed; rerun with --output DIRECTORY to inspect owner-only files
Retain the local candidate before applying it:
  hormuz policy demo --output policy-demo
This policy-UX demo is not evidence that the enterprise v1 release gate is complete.
Managed next steps (shown only; never executed by this demo):
  hormuz --config hormuz.json policy apply policy-demo/candidate.json --organization demo-organization
  hormuz --config hormuz.json policy history --organization demo-organization --limit 20
  hormuz --config hormuz.json policy rollback --organization demo-organization --if-active <candidate digest>
```

The digest is deterministic for the generated candidate but is abbreviated in
this recording guide. The command validates real local policy documents,
semantic comparison, explicit scenarios, and read-only evaluation against a
disposable SQLite database containing zero current usage. It demonstrates
model and output-policy changes, does not insert artificial budget usage, and
does not stage or activate anything. Use `--output DIRECTORY` only when the
retained owner-only artifacts are useful; the directory must not already
exist.

### 6. End on the next honest step

Say:

> These demonstrations prove the local governed request path and the local
> policy-administration workflow. Connecting a real Codex or Claude Code client,
> using a company provider credential, and activating policy in managed storage
> are separate steps. These provider-free runs are not a substitute for the
> published live-provider evidence or proof of the enterprise v1 release gate.
> The public alpha is recruiting independent installation and demo testers
> through the repository guide.

Do not paste a provider key, employee token, private URL, prompt, response, or
customer identifier into a recording. Do not show shell history, environment
variables, browser sessions, password managers, or unrelated terminal tabs.
