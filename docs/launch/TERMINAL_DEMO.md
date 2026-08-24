<!-- hormuz-launch-asset-v1 {"asset_id":"terminal_demo","publication_status":"draft_do_not_publish","claim_ids":["ALPHA_BOUNDARY","PROVIDER_FREE_DEMO"]} -->

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

The alpha is for evaluation and design-partner hardening. Do not describe the
demo as production deployment, live-provider certification, HA, recovery, or
an independent security review. <!-- claims: ALPHA_BOUNDARY -->

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

### 5. End on the next honest step

Say:

> The demo proves the local governed request path. Connecting a real Codex or
> Claude Code client and a company provider credential is a separate setup and
> release-evidence step.

Do not paste a provider key, employee token, private URL, prompt, response, or
customer identifier into a recording. Do not show shell history, environment
variables, browser sessions, password managers, or unrelated terminal tabs.

