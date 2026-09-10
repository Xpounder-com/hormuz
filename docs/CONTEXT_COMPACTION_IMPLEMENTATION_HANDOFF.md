# Coding-agent handoff: governed context optimization in Hormuz

Prepared 2026-09-07 against Hormuz commit
`581508151eff848c05ed6773eaa8ffd4a5d81196`.

Revision 3, 2026-09-07: implement a **client-side feature with a user-facing
On/Off toggle**, shipped through the main Hormuz product version and release.
This supersedes revision 2's gateway-side optimizer, administrator startup
profiles, and mandatory centralized compaction evidence. Integration into the
product does not mean running the optimizer on the gateway. Earlier experiment
measurements remain historical evidence, not implementation instructions.

Revision 4, 2026-09-07: the feature is implemented on
`mehrdad/context-optimization`. This revision records the framed real-client
tool-output shapes, v1.2.0 release assignment, packaged-helper boundary, and
remaining qualification gates found during implementation.

Document status: implementation handoff and acceptance contract. The feature
implementation is present on the branch; deterministic local checks can verify
it, while exact-candidate CI/review, signed Apple Silicon distribution,
clean-machine acceptance, and paid model-quality qualification remain release
gates rather than implementation claims.

Local implementation evidence captured for revision 4, without publishing or a
paid provider call:

- The complete source suite reported `OK` after running 1,852 tests; 279 were
  intentional environment-gated skips.
- The rebuilt wheel and source archive passed the disposable installed-package
  verifier, including 182 tests and the no-content-storage/default-install
  boundary. Those measurements predate final 1.2.0 version promotion and must be
  rerun against the exact release candidate.
- A final 11 MB arm64 standalone helper passed its runtime smoke check. Its
  frozen module archive contains `tiktoken` and `regex` and excludes gateway,
  configuration, PostgreSQL, credential-store, `requests`, and cryptography
  runtime stacks.
- Pinned Codex CLI 0.147.0 passed the provider-free packaged-helper relay test:
  On changed and losslessly reconstructed a real framed `exec_command` result;
  Off preserved the original request body bytes.
- The macOS Swift/Xcode suite ran 35 tests with one opt-in Keychain skip and no
  failures. A fresh local ad-hoc app build then passed strict code-signature and
  embedded-helper checks and contained all five required third-party notices.
- The versioned native 12-case fixture evidence reconstructs every selected
  value exactly and meets the guarded threshold under both pinned tokenizers.

## 1. Assignment and completion boundary

Implement **client-side context optimization as a first-class Hormuz feature**.
Deliver the four transforms, protocol adapters, local request integration,
persistent On/Off preference, desktop toggle, local content-free measurements,
`hormuz context compact`, paired-task evaluation tooling, tests, documentation,
and normal Hormuz feature/release versioning. Preserve gateway governance with
the bounded validation described in section 12. Do not add a context-management
platform.

Sections 1–13 are all part of the assignment. Actual client integration and multi-turn
validation are required implementation work, not a later optional project.
Local implementation/verification, model-quality qualification, and publishing
are distinct statuses within the same product release process. Complete all
available implementation work even if a live evaluation or publication gate is
pending; report the precise remaining gate rather than calling a prototype done.

The user wants the benefit of structural context optimization with little added
complexity. Therefore:

- Ship the feature in the main Hormuz package and release. The old
  `hormuz-context-experiment` package is historical and receives no feature bump.
- Use small native Python transforms. No Headroom runtime dependency, custom Rust
  extension, ML model, hosted optimization service, plugin framework, or new repo.
- Run transforms in a Hormuz-owned local helper on the user's machine. Existing
  desktop launchers currently point third-party clients straight at the gateway;
  they do not intercept model bodies. Section 12 specifies the minimal loopback
  relay needed to put these transforms on that actual request path. It is part
  of the same product, not another deployed service.
- Add no content database, persistent content cache, retrieval tool, network
  service, or background worker beyond the launcher-owned local relay. The first
  release is stateless: no new content cache even on the client. Do not read,
  rewrite, or clear the host application's existing caches.
- Preserve existing model access, secret controls, budget enforcement, and
  provider behavior. The gateway does not perform savings analysis, choose
  content, tokenize for optimization, or build compact representations.
- Do not modify tool definitions, descriptions, arguments, user instructions,
  system instructions, model responses, code, diffs, images, or reasoning blocks.
- Do not implement semantic format/relevance detection. The local adapter
  resolves tool calls/results using exact, verified client tool mappings. Ship
  tested mappings with the product; the offline utility accepts explicit
  selections. Users do not have to design profiles to operate the toggle.
- Do not deploy, publish a package, change credentials, post upstream issues,
  or send customer data to providers as part of this assignment.

### User control

- Label: **Context optimization**. Boolean On/Off; default **Off**, including
  upgrades from a client profile without this preference.
- Help text: "Reduce repetitive tool context on this device before sending it
  through your Hormuz gateway. Your team's policies still apply."
- Scope: this device and its saved Hormuz connection profile. Store the boolean
  in local nonsecret preferences with restrictive file permissions. Do not put
  it in a server policy document, employee credential, or model request body.
- Local storage contract: `context-optimization-<profile.key>.json` inside the
  existing private Hormuz state directory. Use the validated saved profile key,
  never a user-supplied path fragment. Closed JSON shape:
  `{"schema_version":1,"enabled":false}`. Enforce a real boolean, a 4 KiB read
  limit, no duplicate/unknown keys, `0600` files and the existing `0700` directory,
  no symlink following, and the existing cross-process lock/atomic-write pattern.
  Missing file means Off. Invalid/unreadable settings disable transformation and
  report `settings_invalid`; a failed write preserves the previous preference
  and displays an error instead of claiming that the toggle changed. Both Python
  and Swift must use this contract and the same state-directory resolution.
- CLI equivalents: `hormuz context settings --profile <key> --enabled on|off`
  and `hormuz context status --profile <key>`. They use the same local preference
  as the desktop control; unknown profile keys are errors, not arbitrary paths.
- Persist changes atomically. The next request snapshots the setting before
  processing. In-flight requests finish using their existing snapshot. Off
  stops new transformations immediately for subsequent requests, without a
  gateway restart, account reconnection, or changing team authorization.
- Toggling can change historical representations and invalidate provider prefix
  caches. Show a brief status explaining that a new chat gives the most stable
  behavior; do not forbid changing the toggle or claim universal cache stability.
- On is permission to try optimization, not a guarantee of savings. Distinguish
  the saved preference from fixed runtime statuses: `off`, `ready`,
  `unsupported_client`, `unsupported_history`, `resources_unavailable`, and
  `gateway_incompatible`, plus `settings_invalid`. Do not show "active" while
  silently doing nothing.
- Put this control in the existing desktop Client settings/card. No new approval
  page, organization-level limits, aggression slider, savings quota, or separate
  subscription. Section 5's bounds are internal correctness/resource guards,
  not new business policies or user-configured limits.

### Privacy contract

The source request, selection, reconstruction check, and token measurements are
processed locally before network egress. Never upload an original/compact pair,
local transcript, cache, source file, content hash, or diagnostic body for this
feature. The ordinary selected request still goes through the configured Hormuz
gateway; the local helper never calls a model provider directly.

Compaction is an encoding, not encryption. The gateway sees readable selected
content and can reconstruct compact representations in memory for existing
secret enforcement (section 12). This does not give it access to unsent files
or omitted local material. Provider responses continue through the usual relay.
The promise is client-side optimization and no new server content retention,
not a gateway that is unable to read model traffic. A customer-operated gateway
and a vendor-operated gateway have different operator trust boundaries; document
that distinction without claiming an independent security review.

## 2. Read these files, then implement

Read in this order:

1. This handoff.
2. [`FINDINGS.md`](../experiments/context/evaluation/FINDINGS.md).
3. [`evaluate.py`](../experiments/context/evaluation/evaluate.py), especially
   `pack_table`, `unpack_table`, `transform_payload`, and `guarded_payload`.
4. [`test_evaluate.py`](../experiments/context/evaluation/test_evaluate.py).
5. [`pyproject.toml`](../pyproject.toml), [`ROADMAP.md`](ROADMAP.md), and
   [`tests/test_release_identity.py`](../tests/test_release_identity.py).
6. [`hormuz/server.py`](../hormuz/server.py), configuration and policy owners,
   [`hormuz/redaction.py`](../hormuz/redaction.py), and
   [`tests/test_gateway.py`](../tests/test_gateway.py).
7. [`CONTEXT_EXPERIMENT_MIGRATION.md`](CONTEXT_EXPERIMENT_MIGRATION.md) and
   [`tools/verify_core_wheel.py`](../tools/verify_core_wheel.py). Preserve the old
   content-storage exclusions while deliberately adding the new stateless feature.
8. [`ConnectorPlan.swift`](../clients/macos/Sources/HormuzClientCore/ConnectorPlan.swift),
   [`PrivateDirectory.swift`](../clients/macos/Sources/HormuzClientCore/PrivateDirectory.swift),
   [`ConnectionModel.swift`](../clients/macos/Sources/Hormuz/Stores/ConnectionModel.swift),
   the current desktop Client settings view, and
   [`hormuz/session_client.py`](../hormuz/session_client.py). Reuse existing
   profile/auth ownership; never copy gateway-owned provider credentials to the
   client. The context launcher may preserve the user's pre-existing client
   process environment after removing direct credentials for the selected
   provider, as specified in sections 3 and 12.

Baseline evidence before implementation: 15 tests passed; 13 synthetic cases and three protocol-shaped
request envelopes were evaluated. Representative o200k token reductions were
94% for repetitive logs, 42% for repeated search paths, and 50% for uniform JSON.
These are synthetic input-token results, not a workload average or billed
savings. JSON results came from our native baseline, not Headroom SmartCrusher.

Do not use Headroom's generic tool-schema compactor: the evaluation reproduces
meaningful key removal inside `const`, `enum`, `default`, and `$defs`, and
whitespace changes inside literal instructions. Keep the reproductions intact.

Some evaluation files may still be untracked. They are task inputs, not trash.
The checkout also contains unrelated desktop work. Preserve it. If using a
worktree, copy/include the evaluation inputs explicitly; a new worktree does
not inherit untracked files. Use a `mehrdad/` branch if a new branch is needed.

## 3. Exact file plan

Paths below are relative to the Hormuz repository root.

| File | Action |
| --- | --- |
| `hormuz/compaction_formats.py` | Strict bounded envelope validators/decoders; no tokenizer or optimizer dependency |
| `hormuz/compaction.py` | Pure client transforms, limits, and result types |
| `hormuz/compaction_protocols.py` | Result/call matching, traversal, and stable per-result guards |
| `hormuz/compaction_runtime.py` | Local request-pinned preference, bundled tool mappings, resource readiness, numeric status |
| `hormuz/client_relay.py` | Launcher-owned loopback forwarding and local optimizer invocation |
| `hormuz/commands/context.py` and current CLI dispatcher | Offline `compact`, local `settings`/`status`, and helper launch commands |
| `hormuz/compaction_enforcement.py`, `hormuz/server.py` | Bounded decoding for existing secret checks, capability header, final-body accounting; no optimizer invocation |
| `clients/macos/Sources/HormuzClientCore/ContextOptimizationSettings.swift` | Shared local preference contract and helper status |
| Existing desktop `ConnectorPlan`, `ConnectionModel`, and Client settings view | Toggle, helper-backed launcher, readiness/error display |
| Existing Python/client build and packaging owners | Deliver the helper and verified tokenizer resources with the product install path |
| `tools/evaluate_context_compaction.py` | Paired-task manifest, scorer, and bounded evaluation runner through Hormuz |
| `tests/test_compaction*.py`, `tests/test_client_relay.py`, gateway and desktop tests | Algorithms, toggle, local relay/privacy, protocol, governance, and session stability |
| `tests/fixtures/context_compaction/` | Synthetic, deterministic cases and expected answers |
| Root `pyproject.toml`, version source, build locks, distribution checks | Main-product version/dependency/artifact consistency |
| `docs/CONTEXT_OPTIMIZATION.md`, README, client example, roadmap/release notes | Installation, toggle, privacy boundary, supported clients, upgrade/disable instructions |
| `docs/evidence/context-compaction/` | New content-free validation summaries; preserve old experimental results |

No standalone `hormuz-context-compact` package/entry point or experiment version
bump. An optional root extra `hormuz[context]` may carry the pinned tokenizer
dependency; an extra shares the Hormuz version and release. Use tiktoken 0.12.0
as the evaluation baseline, then follow the repository's dependency validation
and lock workflow before shipping it. Import lazily so default-disabled installs
still work. Provide deterministic, verified tokenizer resources during setup;
the local helper must never download vocabularies during request handling.
An enabled local profile with missing resources reports `resources_unavailable`
and uses the ordinary request path; it must not silently download anything.
The gateway requires neither the extra nor tokenizer resources. Deliver a
version-matched helper through the desktop installer or its documented product
setup flow, never through a development virtualenv or arbitrary PATH lookup.
Exercise that actual install flow before calling desktop integration complete.
Offline compaction must dispatch before provider credential/config initialization.

### Main-product versioning and release

- Use a normal `mehrdad/context-optimization` feature branch, or a collision-free
  name with that prefix. Preserve unrelated work and include the local handoff
  and evidence inputs when using an isolated worktree.
- Determine the exact next minor release from current tags, package metadata,
  and the active release plan before changing versions. Verification found the
  latest release tag and package identity at `1.0.0`, while the roadmap reserves
  `1.1.0` for portfolio intelligence. This feature is therefore assigned to the
  normal Hormuz `1.2.0` minor line. The release branch promotes all current
  package, runtime, container, and desktop version sources together.
- If the pending minor can include this feature, update its explicit scope;
  otherwise use the next available minor. Record that decision and rationale in
  the release notes. Bind the feature to the normal Hormuz release milestone.
- Follow existing conventions for a development/prerelease identifier before
  promotion. Update all actual version sources and release-identity checks
  together. Do not edit historical evidence or frozen prior-version fixtures.
- `structural-v1` identifies the transformation contract, not another product.
  Use stable `hormuz-*-v1` representation identifiers,
  including `hormuz-json-table-v1` instead of the evaluator's `hormuz-eval-table-v1`.
- Build and verify the normal Hormuz artifacts; add release notes describing the
  feature, opt-in default, installation requirements, measured scope, and limits.
  Publish/tag/promote through the existing release workflow when authorized.
  Preparing versioned artifacts does not itself authorize production deployment.
- Align the main package, local helper, and desktop feature with that product
  release and its compatibility manifest. Keep platform build numbers separate
  only where the existing packaging requires them. Do not invent a separate
  context feature version such as `0.2.0`.

## 4. API and error decisions

Use frozen dataclasses and these names. These are internal Hormuz APIs,
not new HTTP contracts:

```python
Protocol = Literal["chat", "responses", "anthropic"]
Format = Literal["json_table", "line_runs", "search_lines", "path_list"]

@dataclass(frozen=True)
class Selection:
    result_id: str
    format: Format

@dataclass(frozen=True)
class CompactionResult:
    payload: dict[str, object]  # independently owned; input never mutated
    changed: bool
    reason: str               # fixed enum below
    changed_blocks: int
    before_bytes: int | None
    after_bytes: int | None
    before_tokens: dict[str, int]
    after_tokens: dict[str, int]
    transform_version: str    # always "structural-v1"

def optimize_request(payload, protocol, selections, counters, *, enabled=False) -> CompactionResult:
    ...

def compact_text(text: str, format: Format) -> str:
    ...

def restore_text(compact: str) -> str:
    ...
```

`counters` is a mapping from encoding name to `Callable[[str], int]`. This keeps
the pure optimizer independent of tiktoken. The CLI supplies both `cl100k_base`
and `o200k_base`, using `encode(text, disallowed_special=())`.
`enabled` must be a real boolean; false returns an independently owned unchanged
payload with reason `disabled`, no counter calls, and empty token-count maps.
The CLI passes the validated selection-file flag explicitly.

Fixed result reasons: `compacted`, `disabled`, `unsupported_shape`,
`no_eligible_result`, `unsupported_history`, `limit_exceeded`, `no_savings`, `verification_failed`,
`counter_unavailable`. Invalid configuration/selection specifications raise a
small `CompactionConfigError` carrying a fixed reason code, not rejected content.

Bad or unsupported content passes through. Failed reconstruction passes through.
Counter failures pass through the complete original request. Do not turn an
optimization failure into data loss, request denial, or a silent partial edit.
Do not catch `BaseException`; interrupts must still interrupt. Use targeted
exceptions for parsing and decoding. A callable counter can raise `Exception`:
catch it at the counter boundary and return `counter_unavailable` without logging
its exception text. Missing count values are represented by empty count maps,
not fabricated zero token counts.

Metadata contains counts, fixed actions/reasons, and the transform version only.
It must not contain content, paths, tool names, result IDs, payload hashes, or
exception messages. The transformed payload itself is content-bearing output,
separate from metadata.
Keep optimization measurements local in v1. Do not create a server compaction
telemetry endpoint, profile digest, usage-table migration, or upload of client
estimates. Existing gateway attempt/policy/usage evidence remains authoritative
for enforcement and accounting. Local estimates are not billing records.

## 5. Fixed bounds and deterministic behavior

Use these conservative transform limits; do not add tuning flags yet:

- Offline input JSON file/optimization eligibility: maximum 1 MiB of UTF-8 bytes.
  This does not lower existing gateway admission limits; larger otherwise-valid
  requests bypass the local optimizer and use the ordinary gateway path and
  budget checks. The relay must respect existing transport admission limits.
- Parsed nesting depth: maximum 32; nodes: maximum 50,000.
- Selections: maximum 64; duplicate IDs or unknown format names are config errors.
- Each selection ID must be a nonempty string of at most 128 characters.
- Individual eligible content block: minimum 256 UTF-8 bytes, maximum 64 KiB.
- Rows or lines in one block: maximum 4,096.
- Columns in a JSON table: maximum 64.
- Per eligible result, its independently serialized protocol item must fall by
  at least 32 tokens and 5% under both counters, with no byte increase. Include
  JSON escaping and representation overhead. The same rule is used by the CLI
  and local relay. Decisions may not depend on later messages or other results.
- Measure whole-request deltas for evidence and evaluation; do not make a
  whole-request savings threshold switch earlier results between raw and compact
  forms. The old evaluator's aggregate guard must not become runtime behavior.
  Verify whole-request economics in the multi-turn suite before release.

For percentage checks use integer arithmetic: `saved * 100 >= before * 5`.
Never tokenize before checking byte/node/depth bounds. The file CLI reads at
most 1 MiB + 1 byte before parsing. Detect JSON duplicate keys and nonfinite
numbers. Catch `RecursionError` on decode and then validate depth iteratively.
Oversized/invalid input at the CLI returns a fixed error and writes no output;
the in-memory optimizer treats malformed/unsupported provider shapes as unchanged.
For the live path, unchanged means forward the exact original HTTP body bytes;
do not parse/reserialize Off, unsupported, or failed-optimization requests.

For an oversized input that cannot be serialized safely, return `limit_exceeded`
with empty count maps and `before_bytes=after_bytes=None`. Do not use zero as a
sentinel for unavailable measurements. For an unchanged valid input with known
counts, before and after measurements are equal.

No time-based decision, random sampling, current-query scoring, global cache,
or environment-driven transform changes. Identical inputs/selections/counters
must produce identical outputs. If the text already contains a valid recognized
compaction envelope, leave it alone; do not nest representations. This is a
skip optimization rule, not an authority check.

## 6. Implement only these four representations

All representations are ordinary JSON strings placed inside an existing
tool-result string. No tool injection, hidden reference, external retrieval, or
system-prompt modification. Implement these small native formats rather than
vendoring Headroom. They are **new Hormuz formats**, so remeasure them; the
old Headroom fold percentages are not automatically their results.

### A. `json_table`

Reuse and harden the existing native baseline. Only accept canonical minified
JSON arrays of at least two objects whose ordered keys are identical. Reject
duplicate keys and nonfinite numbers; reject rather than normalize pretty
printing, alternate numeric spellings, heterogeneous/missing keys, or reordered
keys. Keep all rows and nested values. Encode:

```json
{"format":"hormuz-json-table-v1","columns":["id","status"],"rows":[[1,"ok"],[2,"error"]]}
```

Decoder checks unique string column names and exact row widths. Reconstruct
with ordered `dict(zip(columns, row, strict=True))`; serialize with
`ensure_ascii=False`, `separators=(",", ":")`, `allow_nan=False`. Require exact
equality with the original string before accepting the candidate.

### B. `line_runs`

Support exact consecutive identical lines in explicitly selected log/tool text.
Split with `text.split("\n")`, preserving a final empty element. Do not use
`splitlines()` or normalize CRLF/ANSI/whitespace. Encode all lines in order:

```json
{"format":"hormuz-line-runs-v1","runs":[["OK",30],["ERROR permission denied",1],["",1]]}
```

This example represents 30 `OK` lines, one error line, and a final newline.
Counts must be positive integers (`bool` is not an integer for validation).
Decoder expands runs and joins with `\n`. Validate total expanded line/byte
counts before allocation. Unique timestamps naturally prevent run folding;
do not strip timestamps or perform semantic deduplication.

### C. `search_lines`

Only accept LF-delimited `path:line_number:match_text` results where every row
has the same nonempty file path, at least two rows, and no CR characters.
Use `^([^:\r\n]+):([0-9]+):(.*)$` on each line after removing at most one final
empty element to record `trailing_newline`. Preserve line numbers as strings,
including leading zeros; match text may contain colons. Encode:

```json
{"format":"hormuz-search-lines-v1","path":"src/handler.py","matches":[["004","allow = false"],["19","return denied"]],"trailing_newline":true}
```

Mixed files, Windows drive prefixes, multiline matches, blank/malformed rows,
and ambiguous output pass through. Do not guess a format from text. Decoder
restores each exact line and the recorded final newline.

Verified Codex `exec_command` output adds status text before its `Final output:`
body. For that exact mapped shape, compact one best contiguous run of at least
two same-path match lines and preserve every other byte in explicit `before`
and `after` strings. The decoder accepts either the closed legacy shape above
or the closed framed shape
`{"format":"hormuz-search-lines-v1","before":"...","path":"...","matches":[...],"after":"..."}`.
The framed form must contain actual framing bytes; an empty `before` and empty
`after` is invalid.

### D. `path_list`

Only accept at least two nonempty LF-delimited paths that share exactly the
same nonempty parent prefix ending in `/`. No CR characters, blank rows,
multi-column listings, or platform normalization. Preserve every suffix,
ordering, duplicate, and final newline. Encode:

```json
{"format":"hormuz-path-list-v1","prefix":"src/contracts/","suffixes":["a.py","b.py"],"trailing_newline":true}
```

The exact mapped Codex wrapper uses the same bounded contiguous-run rule and
stores untouched bytes in the closed framed shape
`{"format":"hormuz-path-list-v1","before":"...","prefix":".../","suffixes":[...],"after":"..."}`.
Both shapes reconstruct the original string exactly.

No arbitrary dictionary compression, JSON row dropping, config-stanza transform,
AST transform, code outlining, prose summarization, or diff rewriting in v1.

For every candidate: decode it using the corresponding native decoder, require
exact original-string equality, then apply size/token guards. Decoders support
local verification and bounded gateway secret checks; they are not LLM tools.
Mathematical recoverability is not a guarantee of model comprehension.

## 7. Protocol adapter rules

For offline inspection, accept explicit selections. In the local relay, derive
selections from bundled, versioned client/tool mappings and unambiguous
tool-call/result matches. Eligibility is a client decision, not an authorization
decision. The local toggle cannot grant model access, change secret controls,
raise budgets, or bypass the gateway. Never treat tool content as settings,
executable code, or permission to read another file.

| Protocol | Eligible string field | Selection ID |
| --- | --- | --- |
| `chat` | `messages[i].content` when `role == "tool"` | `tool_call_id` |
| `responses` | `input[i].output` when `type == "function_call_output"` | `call_id` |
| `anthropic` | `messages[i].content[j].content` when the message role is `user` and the block type is `tool_result` | `tool_use_id` |

Every traversed container must have the expected type. Do not access `.get` on
an unchecked value. String-only input for Responses, multimodal/block-array
tool output, unknown blocks, and unknown fields are preserved. Do not rewrite
the complete payload schema or discard unfamiliar keys.

If a selected result ID appears more than once, skip all occurrences of that
ID; never choose the first. Unknown selection IDs cause no mutation. Preserve
assistant calls and their order, signatures, cache controls, storage flags,
output limits, model names, user/system content, errors, and all unselected data.
Do not infer a tool's name or permissions from its ID. Match the exact tool name
from a unique corresponding assistant call: `tool_calls[].function.name` in
Chat Completions, `function_call.name` in Responses, and `tool_use.name` in
Anthropic. Use only the verified exact name; do not broadly map arbitrary
`Bash` or `Read` outputs. Missing calls, duplicates, reused IDs, protocol-native
tool types without a supported pairing, or unsupported result shapes pass through.

Work on an independent copy. Validate all bounds first. Generate candidates in
input order, using the fixed per-result decision from section 5. Serialize and
count the complete request for evidence. Use the same JSON settings as Hormuz
`_provider_body`: `json.dumps(payload, separators=(",", ":")).encode("utf-8")` (default
`ensure_ascii=True`). The old evaluator used `ensure_ascii=False`; do not carry
that mismatch into the implementation. Both values being compared use exactly
the same serializer. These counts still are not provider-internal billable
token counts.

If parsing/reconstruction/counting fails before any egress, return the complete
original local request with `changed_blocks=0`; never leave partial edits after a
rollback. A changed representation after a resource/configuration failure must
be surfaced as cache-continuity-unverified in local status. Do not claim
cache preservation for that request. Section 12 requires successful-path prefix
tests and explicit coverage of these fallback cases.

## 8. Hormuz CLI contract

Add this command without changing the old command:

```sh
hormuz context compact \
  --protocol responses \
  --input request.json \
  --selection selection.json \
  --output compacted-request.json \
  --metadata compaction-metadata.json
```

Selection file:

```json
{"enabled":true,"version":"structural-v1","selections":[{"result_id":"call_records_1","format":"json_table"}]}
```

Use closed validation for the selection file. `enabled` must be a real boolean;
missing `enabled` defaults to false. Missing selections defaults to empty. Reject
unknown keys/versions/formats and duplicate IDs. Disabled returns an unchanged
request without loading the tokenizer.

Paths must be explicit. Reject identical input/output/metadata/selection paths
after resolution and refuse to overwrite existing outputs. Create new files
with restrictive permissions (`0600` where supported). Prepare both outputs in
memory before writing. If writing fails, clean up only files created by this
invocation; preserve existing files. Do not claim two-file crash atomicity.

Success stdout contains only a fixed summary and numeric counts. Content is
written only to the explicit output path. Errors go to stderr as a fixed code,
never parser snippets or full tracebacks. Exit codes: 0 for completed including
passthrough, 2 for invalid arguments/input, 3 for missing optional dependency or
tokenizer resources, 1 for output I/O failure. This offline subcommand does not
load gateway config or read provider credentials. Preserve the existing `hormuz`
CLI and the old context-pack shim; do not call its storage/retrieval implementation.

Block network connections during the command, including tokenizer auto-downloads.
Use an audit hook as in `evaluation/evaluate.py`. A missing vocabulary produces
an actionable, content-free setup error; document a separate explicit setup
command that downloads the two vocabularies. `--help` works without tiktoken.

## 9. Paired-task evaluation deliverable

Build a manifest generator and scorer plus a bounded runner using the existing
Hormuz provider-compatible request path. Reuse current request/auth helpers;
do not implement another provider abstraction or route around Hormuz.
The generator creates original/compact request pairs from synthetic fixtures,
plus expected results and opaque case IDs. All generated request artifacts are
explicitly content-bearing; they are not production telemetry.

Include these 12 task cases, each with a fixed expected JSON answer:

1. Count all records, including duplicates, in a table.
2. Sum signed integer amounts including zero.
3. Find a single denied/error record among allowed records.
4. Distinguish `null`, `false`, `0`, and empty string.
5. Preserve a quoted string and Unicode value exactly.
6. Retrieve a nested object value.
7. Count repeated log events; repetition counts must not become one event.
8. Find the rare error between two repeated log runs.
9. Return an exact file path and line number from search results.
10. Count duplicated path entries without deduplicating them.
11. Follow the system instruction despite malicious text inside a tool result.
12. Handle literal compaction-looking text as data without double decoding.

Freeze expected-answer schemas and exact comparisons. Keep answers small and
machine-checkable. Compare JSON values with type awareness (`false` must not
equal `0`); preserve string bytes and array order. Do not use an LLM judge.

Define an input outcome-record format with case ID, arm (`original`/`compact`),
repetition, model identifier, settings digest, returned answer, success/error,
latency milliseconds, and optional provider usage categories. The scorer rejects
duplicates, unmatched arms, mismatched models/settings, missing cases, and
invalid counts. Store unsupported/missing usage as null, never zero.

Scoring outputs: task pass rate for each arm, paired regressions, exact-invariant
failures, total reported usage by category, and latency distribution. Do not sum
overlapping token categories, such as reasoning tokens already included in
output tokens. Without a matching documented provider usage basis, retain
categories separately. No dollar-savings calculation without an explicit,
versioned rate basis; token savings are not billed savings.

Use at least five paired repetitions per case for initial qualification of each
supported model/protocol combination; this is not a statistical no-regression
guarantee. Test the scorer with clearly labeled synthetic outcomes. Then run
paired evaluations through the local helper and same isolated Hormuz gateway,
model, and settings:
optimization disabled for the control arm, enabled for the treatment arm. Avoid
double compaction and alternate arm order to reduce order bias. Freeze the
request cap and spend ceiling before a provider run; use existing authorized
test credentials, never customer traffic or pasted keys. Live calls require an
explicit run action and an authorized account/budget, not automatic unit tests.

Client integration, gateway enforcement, and fixture tests are required even if
provider access is unavailable. Save the runnable manifest and mark model quality
and billed-cost evidence `pending`; do not invent outcomes or declare the feature
release-qualified. Compare retries and total usage/latency, not input tokens
alone. Investigate every paired compaction-only task failure before qualification.

## 10. Implementation order and required tests

Complete these steps sequentially, running focused checks after each step.

Steps 1–10 are represented on the v1.2.0 release branch, including the package
version promotion in step 10. Re-run the assertions below against the exact
candidate rather than treating branch status as release qualification.

1. Add pure formats/decoders, result types, fixed bounds, and strict JSON parsing.
2. Implement the four native transforms and exact reconstruction/token guards.
3. Add protocol matching, verified tool mappings, and stable per-result decisions.
4. Add the shared local preference/settings/status contract and offline CLI.
5. Add gateway bounded enforcement and capability indication. Verify that
   compact representations cannot weaken existing secret denial/redaction.
6. Implement the launcher-owned loopback helper and wire its preference snapshot,
   forwarding, credential handling, failure behavior, and cleanup.
7. Integrate desktop toggle/status and the helper-backed dedicated launchers.
   Package the helper/resources through the real product install flow.
8. Prove Off equivalence, On behavior, multi-turn stability within its stated
   limits, gateway governance, and no content telemetry/storage.
9. Implement/run the paired evaluator and synthetic benchmark using the new
   runtime. Save docs/evidence/context-compaction/native-v1-results.json.
   Preserve original experiment results and explicitly mark pending live checks.
10. Update main-product versions, dependency locks, client compatibility, docs,
    and release notes/checks. Build/install the actual artifacts and verify the
    complete toggle-to-request path. The task does not end with pure transforms.

Required assertions:

- Exact reconstruction preserves bytes, escaped characters, Unicode, supported
  CRLF, final newlines, duplicates, key order, nested values, and JSON types.
- Runs preserve rare errors and occurrence counts. Empty/short/dense/unsupported
  inputs do not inflate. Bad decoders reject duplicate keys, negative/bool/huge
  counts, mismatched row widths, unknown formats, and expansion beyond bounds.
- Literal format markers do not cause nesting. Byte/node/depth limits are checked
  before tokenization; counter failure rolls back every local edit.
- Tool schemas, const/enum/$defs, descriptions, arguments, images, code, diffs,
  user/system content, model settings, output caps, signed blocks, and provider
  cache/storage flags stay unchanged.
- All three adapters preserve input ownership, order, and unknown fields.
  Unknown/reused/duplicate IDs and unsupported containers pass through safely.
- Identical inputs/settings/resources yield identical results. New messages do
  not alter old representations through an aggregate savings threshold.
- Whole-request measurement includes wrappers/escaping. Report total usage and
  latency regressions rather than just favorable local input-token reductions.
- Missing local settings default Off. CLI and UI read/write the same boolean.
  Test On/Off persistence, restart, multiple profiles, in-flight pinning, and the
  next-request effect. Updating preference needs no gateway restart.
- Off and unsupported fallback preserve original HTTP body bytes and do not load
  tokenizers. Missing resources and incompatible gateways have visible statuses.
- CLI help and offline operations work without gateway config/credentials;
  offline compaction blocks network, refuses overwrites/path collisions, and
  writes only explicitly requested outputs with restrictive permissions.
- Relay authentication rejects unrelated callers. Loopback binding, strict
  destination/path handling, credential replacement, redirects, cancellation
  without replay, limits, response streaming, and process cleanup are tested.
- Inspect content-bearing data flow with unique synthetic sentinels. Assert no
  original/compact pair, local file, payload hash, matched secret, or transcript
  is persisted or emitted through telemetry, errors, logs, or status messages.
  Sentinels in the normal selected model body are expected; their presence there
  must not be mislabeled as a privacy failure or hidden from the report.
- The gateway never calls the optimizer/tokenizer. Both normal and compact
  requests enforce identity, model policy, budgets, secret controls, storage
  constraints, failover, and provider-usage settlement.
- Secret matches reconstructed from split representations deny before egress or
  fall back to expanded redacted content. Reserve against the exact final body,
  including any expansion caused by redaction. Malformed/oversized declared
  encodings fail before egress rather than reaching the provider unchecked.
- Existing usage/audit records retain their schema and authoritative meanings;
  local estimates never modify billing or token settlement.
- Scorer tests reject wrong answers, missing/duplicate outcomes, mismatched
  model/settings, and mixed synthetic/observed evidence.
- Existing context-pack shim tests and default installations still work without
  the optional client extra. Gateways can start without tokenizer resources.
- Test the actual installed desktop/helper launcher with a supported native
  client and synthetic tool data. Verify the transmitted change and the Off
  control, not just a screenshot of a working toggle.

From the repository root, start with focused main-package tests:

    /path/to/test-venv/bin/python -m unittest discover -s tests -p 'test_compaction*.py' -v
    /path/to/test-venv/bin/python -m unittest -v tests.test_client_relay tests.test_gateway

Run the applicable local profile/session, desktop Swift, protocol/contract, and
required CI checks. Main tests must not depend on a Headroom clone. Run
git diff --check and verify the normal wheel/sdist and desktop artifacts in
fresh installation environments. Update tools/verify_core_wheel.py to require
the new modules while keeping the old content-storage exclusions. Verify
release identity and the gateway-without-tokenizer installation separately.

## 11. Definition of done and final agent response

Implementation is complete when the normal Hormuz artifact provides a working
local optimizer/relay, the desktop and CLI control the same persistent On/Off
preference, at least one supported actual client/tool path is demonstrated,
gateway enforcement remains effective, and applicable local tests/distribution
checks pass. Supply documentation, local numeric evidence, and a justified
main-product release target. A separate experiment, offline-only utility, or
toggle without a request integration is incomplete.

Track release status separately: exact candidate CI/review, native-client
qualification, paired model quality, performance/cost evidence, upgrades/rollback,
and publication requirements. Use passed, pending, blocked, and not applicable
accurately. Missing external resources do not authorize invented outcomes or
broader compatibility claims. No separate experiment release is required.

Report feature/version/branch, changed files, test commands/counts, artifact
identity, measured scope/benefits, supported clients/modes, and remaining gates.
Explain the actual privacy boundary: local optimization, readable selected
gateway traffic, bounded in-memory secret inspection, and no new content
retention. Do not claim zero gateway visibility, enterprise security certification,
billed savings from tokenizer estimates, or universal cache preservation.

## 12. Required client path, gateway enforcement, and multi-turn behavior

### Concrete integration path

At the inspected checkout, ConnectorPlan.swift generates dedicated Codex/Claude
launchers whose base URLs point directly at the configured gateway. It does not
handle model request bodies. Merely adding a SwiftUI toggle or an offline Python
command will not optimize those clients.

Implement a launcher-owned local helper in the main Hormuz package:

    third-party client
        -> authenticated loopback Hormuz helper on the user's machine
        -> existing configured Hormuz gateway over its normal protected connection
        -> model provider

All selection, transform, reconstruction verification, and token measurement
happen in the local helper before the second hop. The gateway only performs its
normal relay/governance duties plus the bounded representation checks below.

- Provide a local command entry point: hormuz context run --profile <key>.
  It resolves the already saved client/model/gateway profile, starts the helper,
  and launches that supported client. Keep existing dedicated-launcher override
  protections. Do not accept arbitrary provider URLs, shell fragments, or
  forwarded flags that can replace the configured endpoint/auth/model.
- Use the helper for both On and Off launches so a saved preference change can
  affect the next request without restarting the third-party client. Old saved
  launchers stay direct until the user regenerates them using the existing
  review/save flow; report that status explicitly.
- Bind only to an OS-assigned IPv4 loopback port. Require a fresh unguessable
  per-launch credential, checked before reading bodies. Deliver it through the
  existing credential-helper mechanism/private local IPC, never command-line
  arguments, model content, persistent launcher files, or logs. The relay
  replaces that local credential with the normal employee gateway session
  credential using existing session/auth ownership. Provider keys remain on
  the gateway. Do not put the employee token in a URL or a browser.
- Restrict methods/paths to the existing supported client protocol routes.
  Reject CONNECT, absolute-form proxy requests, unexpected Host/Origin values,
  and arbitrary destinations. Never enable permissive CORS or follow upstream
  redirects with credentials. Use the saved gateway's existing TLS validation.
- Pin settings per request. Off performs no tokenizer load, content selection,
  JSON normalization, or compaction. Forward original body bytes unchanged,
  with only required transport/auth-header changes. On falls back to those same
  original bytes when local validation, counters, or supported-shape checks fail.
- Preserve status, streaming chunks, errors, cancellation, and backpressure.
  Use existing gateway-compatible limits plus bounded local buffering for the
  1 MiB eligible input. Oversized ordinary traffic must use bounded streaming
  passthrough within existing gateway admission limits, not unbounded buffering.
  The local relay performs no retries or provider failover. A disconnected
  request is not replayed. Gateway failover remains owned by the gateway.
- Tie helper lifetime to the launched client, close sockets on termination, and
  invalidate the per-launch credential. No login daemon or always-running
  background service. Helper startup failure displays a fixed actionable error;
  it must not silently launch a client with its unrelated default provider.
- Keep the local credential/status interface separate from model routes.
  Expose only enum status and numeric optimization measurements to the desktop.
  Do not buffer model responses for analytics, expose requests in a debug UI,
  or log bodies, tool names/IDs, paths, or exception snippets.

### Compatibility and supported tools

Add the response header X-Hormuz-Context-Formats: structural-v1 on the existing
gateway /health route only when its bounded enforcement implementation is
present. This is an additive capability indication, not an authorization grant
or a change to a frozen JSON health schema.

The helper checks that header from its configured gateway before emitting a
compacted request. Missing/unknown capability means gateway_incompatible and
ordinary forwarding; never send compact formats to an older gateway. Pin that
capability for the local launch. Operators must upgrade every backend behind a
shared endpoint before advertising it. Do not retry a model request after
discovering a compatibility or transport error.

A changed request includes X-Hormuz-Context-Format: structural-v1 to declare the
representation contract. The gateway consumes and strips this header before
provider egress. It carries no original, selection IDs, savings report, or profile
data. It is untrusted input and never authorizes anything. Recognize valid known
envelopes in eligible tool-result fields even when the header is absent, so
omitting a header cannot disable their secret checks. Unknown declared versions
or malformed declared envelopes receive fixed content-free errors before egress.
Undeclared malformed lookalikes remain ordinary text subject to existing checks;
valid literal envelopes are not rewritten merely because they resemble the format.

Check installed supported client versions against tools/client_release_versions.py.
Capture only synthetic tool-call/result fixtures to establish exact tool names
and output shapes. Ship a bounded mapping manifest keyed by client/protocol/tool
name, with at most 64 mappings per supported client. Preserve unverified tools.
A broad shell or file-reader name alone is insufficient to distinguish code from
logs: require a verified invocation/output contract or leave it unmapped.

At least one actual supported client/tool path must demonstrate a changed request
through its packaged launcher and this helper. Protocol-shaped fixtures alone
do not satisfy client integration. Explicitly report native clients/modes with
opaque history or unsupported output blocks as unsupported. Do not show all
Codex or Claude Code sessions as optimized based on synthetic fixtures.

### Preserve secret enforcement without server-side optimization

The existing gateway SecretRedactor scans string values and protects both built-in
patterns and process-local exact secret values. Compaction can split a protected
value between a shared path prefix/suffix, table columns/rows, or repeated runs.
Scanning only the compact JSON string can therefore weaken existing enforcement.
Never solve this by exporting the gateway's protected secret values to clients,
trusting a client claim that it scanned content, or uploading a second raw copy.

Implement a small bounded decoder at the existing secret-inspection boundary:

1. Authenticate, evaluate policy, and apply route/output/storage constraints as
   today. Enforce ordinary incoming byte limits before expensive parsing.
2. Validate recognized envelopes only in the eligible tool-result fields.
   Enforce closed keys/types, canonical forms, no nested encodings, and all
   section 5 bounds. Before allocating, cap aggregate reconstructed request
   bytes at 1 MiB and each expanded result at 64 KiB. A declared compact request
   that cannot be validated safely is rejected before any provider call. This
   is a server validation error, not a local optimization passthrough case.
3. Construct one temporary inspection payload by replacing those envelopes with
   their exact decoded strings. Do not retrieve anything or decode arbitrary
   instructions/other fields. Validate even if secret mode is Off; do not call
   the tokenizer, selector, encoder, or savings evaluator on the gateway.
4. Run existing secret enforcement on the reconstructed inspection payload.
   In deny mode, any match blocks egress. In redact mode, any match causes the
   complete reconstructed-and-redacted payload to be sent for this request,
   sacrificing savings instead of attempting to rewrite compact structures.
   Use that existing RedactionResult for counts/rules; do not count both views.
5. If the reconstructed view has no hits, also apply ordinary inspection to the
   received compact payload. A representation-only hit is denied in deny mode;
   in redact mode send the already checked reconstructed view instead of a
   potentially damaged compact envelope, using the raw-view match evidence.
   When neither view has hits, retain the client's compact representations.
6. Serialize the final selected payload, reserve against that exact body, and
   use it consistently for forwarding, failover, and provider-usage settlement.
   Release temporary content references after handling the request. No content
   caches, request dumps, or persistence are introduced.

This is bounded decoding for existing governance, not gateway optimization.
It makes no new promise that selected data is unreadable by the gateway.
Unchanged requests without recognized envelopes follow existing behavior and
must not acquire a tokenizer dependency or new optimization startup config.

At the inspected checkout, the relevant anchors in hormuz/server.py are
SecretRedactor.inspect, _provider_body, _begin_governed_attempt, and _forward.
The reservation uses len(body) as a conservative byte bound, not tokenizer-exact
billing. Search current symbols rather than patching old line numbers. Preserve
policy attribution, admission, redaction audit semantics, failover reservations,
streaming, cancellation, and uncertain outcomes.

Required adversarial fixtures include exact protected values split across table
columns/rows and path prefix/suffix, multiline private keys, Unicode/escaping,
repeated secret occurrences, expansion bombs, literal envelope lookalikes, and
unknown versions. Prove denial makes zero provider calls and redaction forwards
no protected value after decoding. Run these against the real gateway with
the client preference both Off and On. The preference never disables enforcement.

### Multi-turn implementation contract

Use stateless, query-independent per-result decisions. The same result, mapping,
transform version, and tokenizer resources must yield the same local representation
under the same On setting regardless of new turns. Compare actual serialized
historical items, not just token counts. Do not remove earlier transformations
because a whole-request percentage or a first-N selection threshold changed.

Test append-only histories for all three protocols, mixed tools, multiple results,
incompressible tails, fresh helper processes, malformed/reused IDs, and limit
crossings. On-to-Off and Off-to-On apply to subsequent requests and may alter
prefix bytes; preserve user control and report continuity as unverified. An
in-flight request retains its pinned setting even if the preference changes.

Requests relying on opaque provider history, including Responses previous_response_id,
or missing corresponding tool calls pass through with unsupported_history.
Do not fetch remote history or read local application transcripts to fill gaps.
Signed reasoning blocks, cache controls, model output, and unselected data remain
unchanged. A local resource/version change, gateway redaction, or fallback can
change provider-visible bytes; qualify cache-stability claims accordingly.

### Measurement and release checks

Keep optimization status and numeric estimates local. An explicitly requested
offline metadata artifact may persist those fields with restrictive permissions.
Do not add automatic uploads, a content-derived hash, a central optimization
dashboard, or database migrations in v1. Existing gateway accounting records the
actual transmitted attempt and actual provider usage. Treat client estimates as
untrusted estimates; never use them to reduce reservations or settle invoices.

Test the full path: local client fixture -> helper -> gateway -> loopback provider.
Assert zero optimizer calls in the gateway process, zero tokenizer/network
downloads during request processing, no new content files/telemetry, and exact
Off body equivalence. Check On/Off persistence, account/profile isolation,
restart behavior, concurrent setting changes, helper failure/cleanup, streaming,
cancellation without replay, redaction expansion, budget denial, and failover.

Measure local added latency under concurrent requests. Run paired model tasks
through the same integrated path, with On/Off as the only intended difference.
Set quality/latency acceptance thresholds and the authorized evaluation budget
before running them. Investigate task failures before qualifying a model/protocol.

Build and install the normal Hormuz package/helper and desktop artifact. Verify
the actual toggle changes the supported launched client's request path. Keep the
gateway deployable without tokenizer extras. Update boundary docs to distinguish
this client-side feature from the retired context storage experiment. Publication,
live model quality, and deployment remain separately reported release gates.

## 13. Ready-to-paste agent instruction

> Verify and finish revision 4 of docs/CONTEXT_COMPACTION_IMPLEMENTATION_HANDOFF.md as a
> first-class Hormuz feature in its normal product version and release. Deliver
> the persistent, default-Off Context optimization toggle, shared CLI preference,
> actual launcher-owned client relay, four bounded local transforms, local numeric
> status, and gateway compatibility/decoding needed to preserve secret enforcement.
> Run all selection, optimization, and token measurement on the user's machine.
> Send only the ordinary selected request through the configured gateway; add no
> raw-context upload, content cache, retrieval service, or automatic optimization
> telemetry. The gateway can still read submitted content and may reconstruct it
> temporarily for its existing secret checks. Preserve auth, policy, budgets,
> accounting, streaming, cancellation, and unrelated desktop work. Complete
> protocol, toggle, privacy, adversarial, multi-turn, actual-client, and installed
> artifact checks; an offline demonstration or decorative switch is incomplete.
> Select the normal Hormuz minor version from verified current release state;
> align the helper/client compatibility and build the normal artifacts. Do not
> bump the old experiment to 0.2.0. Report implementation and release gates
> separately; do not publish/deploy or run paid provider evaluations without
> the applicable authorization.
