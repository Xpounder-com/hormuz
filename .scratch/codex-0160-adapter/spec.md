# Codex 0.160.0 adapter support

User request: update the adapter for the installed Codex 0.160.0, add regression
tests, and verify routing end to end without downgrading Codex. This extends
the shared CLI/plugin workflow from #523 and PR #545.

Keep the agreed public test seam: CLI preview/connect, native request/result,
public status/diagnosis, disconnect/restoration. Routine regressions use isolated
private client homes and bounded fake loopback providers. The opt-in native
check uses the existing 0.160.0 binary and existing local runtime/model files;
do not download models, use personal authentication, or enable cloud fallback.

Acceptance:

- Exactly qualified Codex 0.147.0 and 0.160.0 versions remain supported;
  unknown versions still fail. Receipts record the actual target version.
- Changing client version while connected requires disconnect/reconnect and a
  fresh generation. Earlier journals remain readable and restorable.
- Setup qualification declarations must match the actual executable version.
  Preserve historical 0.147.0 model evidence and its quality limitations.
- Diagnose 0.160.0 profile/project/launch precedence, including explicit user
  config bypass, without executing supplied flags or changing trust/settings.
- Qualify the real 0.160.0 HTTP request envelope conservatively. Preserve local
  admission, credentials, streaming, tool-result/continuation affinity, usage
  evidence, finite retries and owned cleanup. Unsupported shapes must block.
- Run the full workflow suite and installed-wheel checks. Demonstrate the exact
  installed Codex binary through the real router to a local model, inspect
  public outcome/status, and disconnect with configuration restoration.
- Update the installed CLI and skills from the reviewed implementation. Leave
  the user's existing Codex installation and unrelated provider settings intact.

Review baseline: 87ecb1a09aa615ef90e12cb0e0854da9712e9249.
