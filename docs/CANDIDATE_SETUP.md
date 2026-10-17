# First local coding candidate

Decision for [#541](https://github.com/Xpounder-com/hormuz/issues/541): select
`codex0160-macos262-m5-32g-llamab11617-qwen3-4b-v1` as an **experimental,
unqualified candidate**. This pins the next experiment, rather than asserting
supported coding capability. The machine-readable
[candidate](campaigns/local-repair-20261016-v3/candidate.json) is authoritative
for exact asset hashes and bounds.

| Component | Candidate |
| --- | --- |
| Client surface | Codex terminal CLI 0.160.0, one agent, Responses HTTP |
| Observed host | macOS 26.2 (25C56), Darwin arm64, Mac17,2, Apple M5, 32 GiB RAM |
| Client executable | Native aarch64-apple-darwin binary, independently hashed; the npm launcher hash is recorded separately |
| Hormuz runtime | Main `c7f8e1d7b7992c308d679db1e886f10e68bc4fb0`, CI wheel 2.0.0a2 |
| Local engine | llama.cpp b11617, existing verified macOS arm64 archive and executable |
| Local model | Existing Qwen3-4B-Q4_K_M GGUF, SHA256 `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5` |
| Envelope | Inline function tools and streaming; 65,536 request bytes, 512 output tokens per generation, 90-second upstream deadline |
| Engine controls | One slot, 32,768 context tokens, four inference/batch threads, two HTTP threads; existing managed sampling, no explicit seed |
| Admission | Hard local-only; coding remains unknown and blocked without a matching passing receipt; code review unsupported |
| Routing advice | Optional shadow observations only; `routing_effect: none`, no promotion |

These host facts identify one observed machine. They establish neither minimum
hardware requirements nor support for other Macs, Linux, Windows, or a desktop
app's separately bundled Codex engine. This selection keeps the supported wire
version already installed, reuses verified assets, and holds the current engine
and output bounds steady while investigating coding completion.

The retained [native diagnosis](native-coding-diagnosis-20261015-v2.md) includes
one edited implementation that passed its checks but timed out, zero passing
repairs in the original three-case campaign, and zero in the later four-case
pilot. Those results remain unchanged. They make this model a reproducible
candidate with negative evidence, not a promising or qualified coding model.
A different model or wider envelope requires a new candidate and campaign.

The latest [bounded development treatment](campaigns/local-repair-20261016-v6/README.md)
preserved these client/host/model/engine limits and v5's exact Hormuz runtime,
while adding explicit shell-patch usage guidance. It completed one local episode
but made no edit and failed independent checks. Its fresh held-out inventory
remains unrun, and the candidate remains experimental and unqualified.

## Product boundary

CLI and skills-only plugin callers continue to use the same connected-client
owner. The initial native experiment uses the terminal CLI. Actual Codex
plugin-host activation, follow-up/resume/fork and restoration remain release
gates; CLI evidence does not qualify those surfaces. Installing a plugin alone
does not activate routing. This decision adds no coding UI, second agent loop,
mandatory MCP server, startup registration or user settings change.

The selected cloud target is the native Codex gateway path: client-owned native
ChatGPT credentials and a separate Hormuz gateway key, to the fixed native
backend. At the pinned source baseline, [catalog discovery](NATIVE_CODEX_CATALOG.md)
is implemented, while inference, managed-provider activation and actual account
permission remain unqualified. A catalog response does not establish any of
them. The first release must prove this path for an eligible configuration;
personal-account support is not assumed. There is no API-key billing fallback.
Local operation continues to require no cloud account, and local-only forbids
both cloud model and helper calls.

The full [router scope](ROUTER_SCOPE.md) remains in force. This is not a decision
to ship a local-only subset and close the epic. Verified acquisition and
distribution, actual plugin/cloud qualification, coding outcomes, and the final
closure contract remain outstanding under #542, #543, #537 and #523.

## Prepared qualification

The [version-three campaign](campaigns/local-repair-20261016-v3/README.md) has
four development and twelve held-out cases, with one development and three
held-out cases per required family. It follows
[local-repair.v1](LOCAL_CODING_QUALIFICATION.md). Case text, broken files and
independent checker bytes are hashed before any new model inference.

The candidate's [setup profile](campaigns/local-repair-20261016-v3/setup-profile.json)
uses the existing version-two public profile contract, permits only
`code_change`, and carries `coding_evidence: null`. It preserves unknown coding
readiness; it is not a passing receipt. A separately captured exact native
instruction/tool/generation profile and a reviewed way to collect outcomes
remain necessary before the native campaign can start.

Managed setup intentionally cannot run unqualified coding or switch to the
legacy `configured` admission mode. Keep that behavior. Preparing this campaign
does not authorize replacing the gate, using synthetic receipts, relabeling
coding as another task, or importing historical failures as successes. If an
explicit outcome-collection interface is needed, specify and test it at the
approved workflow before running native episodes.

## Validation and change rule

Preparation verified the actual native Codex version and binary hash, runtime
archive/executable, model and pinned wheel. An isolated public setup dry-run
using that wheel and the candidate profile succeeded, created no gateway
configuration, and preserved the private test client's settings. It performed
no model inference and started no router or engine.

All sixteen broken baselines fail their independent checkers with assertion
exit 1; independently authored reference implementations pass all sixteen.
Reference implementations stay in private preparation evidence outside the
campaign corpus and model workspaces. This validates checker behavior, not
model quality. The content-free [preparation evidence](campaigns/local-repair-20261016-v3/preparation-evidence.json)
records the distinction.

These are versioned development records, not additional runtime API contracts.
The existing public setup profile remains version 2 and the outcome receipt
remains version 1. Review a new record version before changing pins or controls.
After any inference, changing a subject, prompt, control or checker requires a
new campaign and fresh held-out cases. Rollback removes only these preparation
artifacts; no installed connection, user data, engine or policy migration is
introduced.
