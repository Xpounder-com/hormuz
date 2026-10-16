# Local repair version 4: frozen development probe

This separately versioned campaign follows the explicit bounded qualification
workflow in #537. The source/wheel, existing Codex 0.160.0, macOS arm64,
llama.cpp b11617, Qwen3-4B and actual native instruction/tool profile are pinned
before inference. Original v1/v2 failures and v3 preparation remain unchanged.

The [complete coding plan](coding-plan.json) freezes four development and twelve
held-out cases from the previously unused v3 corpus. The [v4 corpus](corpus.json)
adds an explicit coding prompt to every goal and computes new task hashes before
inference; checker sources and hashes are unchanged. Only `dev-clamp` is
currently authorized: one native episode, eight submissions, ninety seconds,
no transport or quality retries. No held-out task runs in this slice.

The [manifest](frozen-manifest.json) binds 152 artifacts, including all 136
installed runtime files, the wheel, assets, [runner](development-runner.py),
[actual profile](request-profile.json), [first input](first-input.json),
[setup profile](setup-profile.json), [pins](pins.json) and [probe plan](probe-plan.json).
It was reviewed before inference. Source commit is
`7898aa8960c32ba6bc61166cdb61e7be4c869ef7`; wheel SHA-256 is
`33c3cd175a938cdfd86807fedfcfe71b50fe2e551fd848336053f60919d71c83`.
The freeze's SHA-256 is
`34d79b895d87cd6e315cd353e9ec2ff00710a40bfbf08194a63890c6958b9519`.
Absolute paths describe this one observed host, not a portable installer.

[Preflight](preflight.json) captured one actual native Responses request without
forwarding to inference. Its intentional HTTP 503 produced a failed native turn,
not a coding episode. Public router attempts remained zero; disconnect restored
settings and closed owned listeners. The independent development baseline
subsequently returned assertion exit 1 under a no-network/no-write macOS sandbox.

The reviewed runner has separate initialize, zero-inference preflight and run
phases. Its run verifies frozen artifacts before activation, uses the shared
setup and private credential helper, arms one explicit probe, launches the
existing coding client, inspects public status, checks the implementation
independently, revokes and disconnects. A consumed episode marker forbids rerun.
It does not contain a second agent loop or generate a passing coding receipt.

At this preregistration commit, native outcomes remain pending. The frozen
manifest will not be rewritten to incorporate results. Later observations must
be separate files, including failures, timeouts, missing edits and unknowns.
Ordinary unqualified coding stays blocked, local-only and conversation affinity
remain enforced, and advice has no execution authority. One development result
cannot qualify this candidate; all sixteen planned outcomes are required.
