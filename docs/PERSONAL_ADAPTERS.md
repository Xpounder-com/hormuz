# Personal adapter SDK 1.0

`hormuz.adapters.AgentAdapter` separates agent identity, provider protocol, and
capabilities. The initial contract has four small surfaces:

1. immutable identity and qualified executable version;
2. declared request protocols and optional conversation/execution capability;
3. path-to-protocol classification;
4. a launch plan that receives only a short-lived loopback credential.

Adapters may optionally expose a conversation boundary or explicit typed
execution steps. The core does not assume either exists. Requests, responses,
usage, streaming, cancellation, and errors keep their provider protocol
contracts; adapters do not receive upstream credentials or permission to edit
agent configuration.

The built-in catalog contains Codex, Claude Code, and Aider. Aider is direct
OpenAI-compatible only and currently reports structural compaction as
unsupported because its terminal output is not a typed tool result.

## Provider-free sample

```sh
python -m hormuz.personal_adapter_example
hormuz personal conformance --agent codex --json
hormuz personal conformance --agent claude-code --json
hormuz personal conformance --agent aider --json
```

The sample and conformance kit need no provider account. They verify API
version, declared routes, removal of the relevant direct provider credential,
preservation of client-owned tool environment, and absence of the loopback
credential from process arguments. Synthetic request fixtures and relay tests
then cover streaming, cancellation, errors, and numeric usage extraction.
The packaged `personal-qualification-v1.json` fixture also exercises the
bounded semantic, exact-execution, Jev-fallback, and loop-intervention
contracts with zero provider calls. Those checks are labelled
`experimental_contract`: they prove reference contracts only and do not claim
that a built-in agent yields an execution step or that Jev was contacted.

Contributor sequence:

1. implement the smallest `AgentAdapter` supported by an agent's documented
   endpoint controls;
2. keep all provider secrets out of the launch plan;
3. run the conformance report and add deterministic protocol fixtures;
4. demonstrate install, ordinary work, benefit reporting, and removal;
5. name unsupported capabilities explicitly;
6. add tool mappings only after real typed call/result shapes are captured in
   sanitized product-owned fixtures.

Protocol compatibility alone is not an integration claim. The agent version,
its actual endpoint behavior, streaming, cancellation, credential isolation,
and removal all require qualification.

The current Codex, Claude Code, and Aider adapters all declare
`explicit_execution_steps = false`. That is the cycle-5 H14 result for the
built-in catalog: there is no supported interception point for replacing a
hidden model decision, so the reference execution router is not wired into the
release request path.
