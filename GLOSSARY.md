# Domain terms

- **Coding client:** Existing application owning editing, tools, permissions
  and the agent loop. Hormuz connects to it.
- **Router:** Customer-local process that admits and forwards intercepted model
  requests, owns credentials and preserves outcomes and continuation affinity.
- **Execution destination:** Immutable endpoint, protocol, authentication,
  locality and model-qualification facts bound to one generation.
- **Qualified local route:** Destination/model with evidence for its permitted
  request envelope and capabilities. A running process is not qualification.
- **Local-only:** Hard permission forbidding cloud execution for all intercepted
  model requests, including helpers. Unsupported work blocks.
- **Automatic routing:** Selection among policy-eligible qualified destinations
  at a safe fresh request point. Unknown evidence uses an approved baseline or
  blocks; opaque continuation is not migrated.
- **Connected-client lifetime:** Shared owner of setup, status, process control,
  recovery, disconnect and conditional configuration restoration.
- **Plugin:** Skills-only entry point calling the same connection implementation
  as the CLI. Plugin installation does not activate routing.
- **Operational cleanup:** Separate retirement of external resources and bills
  tracked in #494–501; source deletion does not complete it.
- **Local history:** A user's optional retained record of intercepted requests,
  responses and usage, kept on their device for review, export and deletion.
- **Spending estimate:** A calculation from observed usage and configured
  prices; it is distinct from an invoice or provider-confirmed charge.
