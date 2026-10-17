# Local coding qualification

This resolves the admission decision in [#538](https://github.com/Xpounder-com/hormuz/issues/538).
The approved workflow remains connect → submit through the real router → inspect
result/status → disconnect. Qualification gates fresh automatic coding admission;
it does not grant a routing model execution authority.

## First criteria: `local-repair.v1`

The initial coding envelope is autonomous `code_change` through Codex Responses,
using a verified managed llama.cpp runtime. It covers bounded text and inline
function tools only. `code_review` has no qualification criteria in this version
and cannot gain automatic admission. Existing transport capabilities and request,
output and elapsed-time bounds remain necessary. They are not coding evidence.

Predeclare a versioned campaign before inference, including its exact subject,
case inventory, task hashes and independent check hashes. Pin client version,
OS family/architecture, runtime archive, model revision/alias, protocol, capabilities, request
and output bounds, timeout and the instruction/tool/generation-control profile.
Changing any of these requires new evidence. User text, conversation IDs and
provider-owned continuation state are not part of the instruction profile hash.

Coding admission and final dispatch also require one printable `User-Agent`
header, at most 512 characters, whose first product is
`codex_exec/<evidence client_version>`. This is the product/version observed in
the pinned native Codex 0.147.0 coding experiment; its platform suffix may vary.
Missing, duplicate, malformed, unrecognized or changed version declarations
cannot inherit coding evidence. Other client origins require separate wire
qualification. The router bearer authenticates access; the header remains a
caller assertion. This catches ordinary client upgrade drift, not a token
holder forging a version or cryptographic attestation of an executable.
Evidence must name an explicitly supported client wire version. Changing the
adapter's default does not invalidate an unchanged older version that remains
supported; each version still needs its own matching receipt and request header.
The evidence subject can declare the supported `codex_http_0160` wire capability,
including its bounded explicit no-reasoning envelope. It must exactly match the
configured route and request controls. Transport support alone grants no coding
readiness; missing evidence remains unknown, and version/control drift blocks.

The minimum battery is four development cases (at least one per family) and
12 distinct held-out cases (at least three per family): arithmetic/boundaries,
text normalization, collection behavior, and validation/state. These are a
conservative engineering acceptance floor for the declared byte/token/time/tool
bounded `code_change` policy, not a statistical accuracy claim. The deterministic
task hint does not establish that a new request shares the cases' complexity;
this version does not enforce a semantic "small repair" promise or guarantee
repository-wide success. A narrower task promise requires a separately defined
trusted caller envelope, not a classifier's confidence. Every predeclared case must pass;
adding failures or missing observations cannot improve readiness. Keep at most
64 cases. Development and held-out tasks must have distinct task hashes.

A passing episode must have an independently observed failing baseline, an
actual implementation edit with a retained patch digest, unchanged independent
checks matching the frozen hash, passing post-edit checks, and normal client
completion with exit zero within the declared budget. The independent baseline
runner must report exit 1 for a failed assertion; other baseline exits fail the gate. Missing outcomes, timeouts,
cancellation, unchanged files, changed checks and nonzero exits do not pass.
No quality retries, post-result oracle edits, dropping cases or held-out tuning
are permitted. A changed prompt/model needs a separately versioned campaign and
new held-out tasks. Preserve the original failures and invalid treatments.

Receipts are bounded, content-free **operator-attested outcomes**. A digest
binds the predeclared plan and makes evidence changes visible; it does not prove
that an operator ran an honest experiment. The operator must review the frozen
runner, patches and independent results before installing a receipt. Koto
validates the receipt and its applicability, rather than running supplied code,
fetching evidence URLs, or treating an assertion of confidence as certification.
No second episode ledger or transcript store is introduced.

## Readiness and admission

`GET /v1/router/state` version 2 separates prompt-free engine health (`readiness`)
from `coding_readiness`, evidence identity/digest and `automatic_task_types`.
CLI/plugin callers use `koto client status codex --client-home /path/to/codex-home --routing`
to read that same live, policy-filtered status through the owned local control
socket. It makes no model call and needs no exported credential. Ordinary
connection status retains its earlier contract. A stopped/unavailable owner
returns an explicit error rather than inferring live readiness from a file.
No receipt means `unknown`; insufficient or missing evidence is `incomplete`;
observed unsuccessful episodes are `failed`; a different execution subject is
`subject_mismatch`. Only a complete passing campaign is `qualified` for
`code_change`. Review remains unsupported. A healthy engine can remain unqualified.
Qualified public summaries require at least 16 planned cases and identical
planned, evaluated and passed counts. Contradictory summaries fail validation.
Every nonnull evidence summary has at least one planned case; failed readiness
requires an evaluated case that did not pass. Incomplete campaigns can have
every planned case pass while lacking the required coverage. Subject mismatch
takes precedence over outcome readiness and may have zero evaluated cases.
Summary identifiers retain the receipt's bounded ASCII identifier rule, and
automatic task lists contain unique supported tasks only.
Summary plan/receipt digests require 64 lowercase hexadecimal characters.
Malformed readiness or task-list types raise `ContractValidationError`, like
other invalid summary fields.
Storage faults return unavailable diagnostic results without stopping the owned
router or engine; restoring storage permits the same generation to recover.
Routing control replies use a finite limit derived from the existing 1 MiB
configuration bound, including JSON escaping and per-route summary overhead,
so supported long aliases remain observable through CLI/plugin status. Other
control replies retain their smaller bound.
Nonfinite numeric input, including exponent overflow, returns `invalid_json`
before admission or provider traffic. Escaped lone Unicode surrogates in values
or object keys return the same bounded error. Valid Unicode, including astral
characters, retains its existing UTF-8 profile digest.

Old operator declarations retain their noncoding task envelopes. Naming
`code_change` or `code_review` alone no longer permits fresh automatic coding
execution in `auto` or `local_only`. Unknown or failed coding uses only an
explicit policy-permitted cloud baseline in `auto`; a local baseline or
`local_only` blocks with zero cloud requests. Shadow advice cannot add a
candidate, qualify a model, relax privacy, or move a conversation.

A passing coding record pins the instruction/tool profile for `code_change`
requests and their coding continuations. On the same route, declared noncoding
tasks retain their existing bounded task/capability envelope without matching the
coding receipt. Standard-tier and `store:false` defaults are normalized
before hashing, consistently at selection and final dispatch. Any change to the
managed inference flags invalidates `managed-llama.v1` evidence and requires a new
runtime-profile identifier and campaign.

Successful continuations retain the admitted model/destination generation and
the work's observed task context; they do not infer a new task from tool output.
Only an observed noncoding context exempts a continuation from coding checks.
Declared coding work and unknown contexts remain gated. Changed or unqualified
coding instruction/tool controls block rather than migrate. Existing
policy, credential, cancellation, helper and no-replay rules remain authoritative.
An explicit task in a history-bearing request must still be permitted for the
route; `code_review` stays unsupported. Moving a noncoding work to coding requires
a self-contained matching request so its new context can be recorded before any
coding continuation. Inherited provider state cannot establish that transition.
For inferred work, a later admitted self-contained noncoding request replaces
the observed context used by its continuations; the original inferred task label
does not turn into declared coding intent.

The bundled Qwen3-4B experiment remains **unknown for automatic coding admission**.
Its recorded 0/3 original and 0/4 later repairs, including an unevaluable timeout,
are preserved in the [native diagnosis](native-coding-diagnosis-20261015-v2.md).
Those historical campaigns were not preregistered under `local-repair.v1` and
must not be relabelled as a qualifying run. There is no newly qualified native
model in this change.

## Prepared candidate campaign

The [first candidate](CANDIDATE_SETUP.md) pins an experimental Codex 0.160.0 /
macOS arm64 / llama.cpp b11617 / Qwen3-4B setup. Its
[version-three campaign](campaigns/local-repair-20261016-v3/README.md) prepares
the sixteen-case inventory and independent checkers, with no native outcomes or
admission receipt. Exact native request-profile binding and a reviewed outcome
collection path precede inference. The existing unqualified-coding denial is
intentional and remains in force.

An [explicit bounded probe](BOUNDED_CODING_PROBES.md) provides the operator-invoked
collection path without changing ordinary coding admission. It binds one frozen
case and native profile to a local conversation with finite request/time budgets.
Probe permission is not an outcome or passing receipt.

The [version-five development observation](campaigns/local-repair-20261016-v5/README.md)
ran that path with frozen native pins/profile. One local episode completed, but
made no edit and failed independent checks after malformed patch commands.
The v4 denial before inference is retained separately. No held-out case ran,
no passing receipt was installed, and this candidate remains unqualified.

The separately frozen [version-six treatment](campaigns/local-repair-20261016-v6/README.md)
added explicit developer guidance for the existing shell patch helper, while
retaining v5's runtime, client, model and generation bounds. Its one development
episode also made no edit and failed unchanged independent checks. Five local
submissions retained one work and continuation affinity; normal coding was
denied before the explicit probe. No held-out case or quality retry ran, no
receipt was installed, and coding readiness remains unknown. The
[native diagnosis](diagnostics/native-patch-20261016/README.md) found no demonstrated
router argument-loss defect; supplied-patch controls are not model quality.

## Contracts, migration and rollback

The version-1 `hormuz.local-coding-evidence` receipt contains a frozen `plan`, its
canonical SHA-256, and content-free per-case outcomes. The plan includes
`criteria_version`, execution `subject`, and the full case inventory. Canonical
JSON uses sorted keys, compact separators, UTF-8 and no non-finite numbers.
The [synthetic receipt](../tests/fixtures/local-coding-evidence-v1.json) specifies
the exact shape without identifying a qualified model. `plan_sha256` hashes only
`plan`; public `receipt_sha256` hashes the entire receipt, including outcomes.
Each outcome names a planned case and records baseline/check/client exit codes,
normal/timeout/canceled/unknown termination, elapsed milliseconds, an observed
implementation edit, its patch digest and the independent checks digest. Missing
outcomes remain in the planned denominator. The runtime never executes a receipt.

`request_profile_sha256` hashes the canonical mapping of present `instructions`,
`tools`, `tool_choice`, `temperature`, `top_p`, `reasoning`, `include`, `text`,
`parallel_tool_calls`, `store`, `service_tier` and `stream`, after privacy
normalization. Input-carried `system`/`developer` messages are additionally bound
as `input_instructions`: an ordered list of `{position, message}` entries, using
the zero-based input position and entire message in version 1. Version-2
`hormuz.local-coding-evidence` retains the same acceptance criteria and shape,
but normalizes recognized native client message IDs as described in
[bounded probes](BOUNDED_CODING_PROBES.md). Ordinary admission selects the digest
algorithm from the installed receipt version; v1 remains strict. Fresh Codex
launches assign new IDs to otherwise identical developer instructions, so v2
binds their semantic profile rather than those launch-specific labels.
Adding, removing, moving or
changing an instruction message changes the profile; ordinary user and tool
content remains variable. Missing `store` becomes false and absent/null `service_tier` becomes
`default`; other present fields retain their exact JSON values. This captures the
effective agent instructions and tool schema rather than a possibly ignored
command-line model-catalog flag. Client/plugin activation, hardware support and
statistical task success remain separate release qualification decisions.

The [v2 compatibility fixture](../tests/fixtures/local-coding-evidence-v2.json)
does not identify a qualified model. Older binaries reject version-2 evidence;
disconnect and remove that record before downgrade. Existing v1 records are not
rewritten or reinterpreted. Freeze a separately versioned v2 campaign before
collecting new evidence; retained failures keep their original treatment.

Local route declarations may embed `coding_evidence`. Version-1 declarations
without it remain readable and explicitly unqualified for coding. Setup profile
version 2 adds nullable `coding_evidence`, shared by CLI and plugin; version 1
remains readable with unknown coding readiness. The default profile selection
is unchanged; select version 2 explicitly to install reviewed outcomes. Existing
generated configurations are never silently rewritten. Disconnect, inspect any intent mismatch, and use a
new configuration path or explicitly migrate the reviewed configuration.

Router status version 1 and setup profile version 1 fixtures remain valid for
historical readers. Current status uses version 2. No work/storage or connection
journal migration is required. An older binary rejects new qualification fields
and version-2 profiles. Disconnect before downgrade and remove the new fields or
use the retained version-1 profile. **Downgrading also removes the coding evidence
gate**; it is not an equivalent admission policy. Keep hard local-only policy and
credentials intact. The legacy `configured` mode remains an explicit operator
choice with no automatic qualification claim; it is not enabled by setup.

## Acceptance at the approved seam

A healthy declared-only coding route blocks automatic local admission; public
status explains why and local-only sends no cloud request. A synthetic passing
receipt admits only the exact matching subject and request profile. Failed,
missing, leaked/duplicate or mismatched evidence cannot admit coding. Compatible
noncoding work and successful conversation affinity continue to work. Local
shadow advice remains observational even when a coding route is unqualified.
CLI/plugin setup generates the same evidence policy using verified existing
files, and disconnect restores owned settings and stops only owned engines.
Synthetic fixtures establish enforcement, not real model quality.
