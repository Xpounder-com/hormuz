# Capability workflows

Use the [shared owner and consent workflow](workflow.md) for prerequisites,
connection, restoration and history. Check each installed command's `--help`:
development package version strings alone do not establish these commands.

## Diagnose and explain

`hormuz --config CONFIG client diagnose codex --client-home HOME` inspects
configuration and connected-client status without probing models or changing
routing. An optional `--executable PATH` performs the existing bounded version
check. Compatibility without that check and model/task readiness are explicitly
unobserved. The public action is the next recovery step, not permission to edit.

Select the project with `--project DIRECTORY` (default current directory). For
known launch settings, supply each argument separately, for example:

```sh
hormuz --config CONFIG client diagnose codex --client-home HOME \
  --project PROJECT --launch-arg=--profile --launch-arg=review \
  --launch-arg=--model --launch-arg=ALIAS
```

Inspection follows the pinned client's profile files (`HOME/NAME.config.toml`),
project-root markers, per-layer trust, nested project settings, generic `-c`
values and dedicated flags. Ignored project provider settings are identified.
`-C`, `--search`, OSS provider selection and remote launch mode are accounted
for; unknown flags or unsafe/malformed files produce a bounded inspection error.
Use routing flags only, without prompts or credentials. Supplied arguments are
never executed or echoed. Source/key names and comparisons explain conflicts;
actual running-session routing remains unobserved. Diagnosis preserves settings
and project trust.

Use `history list` to identify a retained opted-in attempt, then
`hormuz --config CONFIG request explain ENTRY_ID`. New entries expose policy,
fallback and routing evidence, destination/locality, latency and available usage.
Legacy entries report absent evidence as unobserved. Optimization evidence is
unobserved; do not infer token savings. Missing history is not consent to enable it.

## Local model probes and recommendations

For a selected existing local OpenAI Responses route, run:

```sh
hormuz --config CONFIG model qualify --model ALIAS \
  --client-executable CLIENT --runtime-executable RUNTIME --model-file MODEL \
  --output NEW_PRIVATE_REPORT
```

The router must already be running. Qualification preserves existing admission:
a blocked unqualified route remains blocked. Explicit actor selection with
`--actor ACTOR` is required when the profile has multiple actors. Only a direct
local router with static authentication is supported. No cloud route is selected
by this operation; stricter local-only scope is checked before any probe.
Configurations with shadow routing enabled return
`qualification_shadow_router_unsupported` before any model request or report
publication because classification would exceed the fixed request envelope.
Qualification never disables that feature; select an independently reviewed
configuration without shadow routing when qualification is required.
The authenticated running router must also explicitly report shadow routing
disabled. A copied configuration cannot substitute for this live check. Missing
feature metadata requires a router upgrade; unavailable state never establishes
a qualification pass.

There are at most four serial model requests plus two authenticated router-state
reads, zero retries, 128 output tokens per model request and one 12-second probe
deadline. The fixed suite checks a box-count fact,
JSON extraction, an inline function call, and replay of a synthetic tool result.
These checks are a minimal diagnostic envelope, not a coding benchmark. No tool
is executed. The report does not authorize new routes or claim endpoint binary
attestation. User-supplied client/runtime/model files are fingerprinted, not
assumed to be the running server's assets. Hashing is streamed and bounded.
The v2 report records the requested alias, destination, generation, upstream
model and revision. Responses and state before/after the suite must match;
another local destination serving the same model name cannot establish a pass.

The new report is private and never overwrites a file. A failed report exits 2.
Passes require all four checks; transport errors, policy denials and incomplete
provider results fail. Seven-day expiry is a conservative default for this tiny
suite; setup fingerprints must also match.

```sh
hormuz --config CONFIG model recommend --model ALIAS \
  --client-executable CLIENT --runtime-executable RUNTIME --model-file MODEL \
  --qualification PRIVATE_REPORT
```

Recommendation makes no model requests. It reports current versus stale local
user-owned evidence, available system memory and model file size. A model file larger than physical memory is flagged before recommendation. Inference
memory fit otherwise remains unverified. Reports are not signed and are not admission
credentials. Otherwise-current evidence uses at most one authenticated state
read with a three-second deadline. Requalification is required after relevant
setup or router-generation changes, expiry, or unavailable state. Complete v1
reports require requalification; malformed/inconsistent evidence returns a
fixed error rather than a recommendation.

## Policy preview and activation

```sh
hormuz --config CURRENT policy configure --local-only \
  --monthly-budget-usd 2.50 --output NEW_CONFIG --dry-run
```

Either change option may be used alone. The output must be a new file beside
the source, preserving relative-path meanings. Preview validates a private
transient file and removes it; it does not publish the output or alter a client.
Run the matching command without `--dry-run` to stage the new private file.
Existing targets and symlinks are refused. Staging is never reported as active.

For authorized activation, disconnect the old connection with the shared owner,
preview `client connect` against the new configuration, connect and inspect its
receipt. Preserve the old configuration to recover a failed reconnect. The new
profile has separate history consent; do not enable or transfer capture.

The budget uses existing atomic UTC-month admission/reservation accounting,
configured rates and outstanding holds. It is a ceiling on configured estimates,
not provider invoices or infrastructure cost. Unpriced work cannot establish
actual dollar coverage. Zero and negative budgets are refused; use local-only
policy when cloud execution must be prohibited, rather than an estimated budget.
In-flight work retains its existing owner and settlement semantics.

## Usage and maintenance

`hormuz --config CONFIG usage insights` reports one retained-history snapshot:
counts, known estimates, unknown costs, token coverage, observed period and
content-free observations. It never enables capture. No before-and-after savings
claim is generated. Use shared history commands for consent, deletion and local
exports, keeping decrypted content out of agent results.

Maintenance reuses diagnosis, version checks and conditional restoration. It
does not install client/runtime updates. Disconnect before removal, preserving
newer user settings and processes that Hormuz does not own.
