# Verified local runtime and model acquisition

Origin: customer-local router [#523](https://github.com/Xpounder-com/hormuz/issues/523),
the acquisition gap in [#542](https://github.com/Xpounder-com/hormuz/issues/542),
and the owner's October 15 selection of "Verified runtime/model acquisition:
preview and explicitly requested downloads using pinned artifacts."

## Scope

Add `koto client acquire codex --directory PATH`. Its default is a read-only,
offline preview; `--download` explicitly authorizes fetching the displayed pinned
artifacts, and `--offline` verifies/reuses a completed installation without
network access. CLI and plugin callers use this same operation. Acquisition
does not connect Codex, start models, grant coding qualification, enable history,
change cloud policy, or replace any existing settings.

Bundle a versioned catalog for the existing experimental Codex 0.147.0 / llama.cpp
b11617 / Qwen3-4B macOS arm64 row. Preserve exact source revisions, SHA-256 and
byte sizes plus upstream license/provenance links. `--catalog PATH` is an explicit
operator-selected reviewed catalog, not model discovery or certification.
The first supported production envelope in #541 remains a separate decision.

## Behavior

- Version catalog, success/preview receipts and fixed content-free errors; add
  independent compatibility fixtures and manifest entries.
- Check platform and storage before downloading. Expose required disk space;
  do not infer task capability or memory suitability from acquisition.
- Download sequentially with bounded streaming, size/digest checks, finite
  redirects restricted to declared HTTPS hosts, explicit timeouts, and closed
  clients. Never import credentials, cookies, proxy auth or bearer headers.
  Reviewed loopback HTTP catalogs support deterministic external test fixtures.
- Keep partial downloads in an exclusively locked, private owned generation.
  Resume only with validated byte ranges; a server ignoring Range safely restarts
  that owned partial file. No automatic retries or inference requests.
- Safely extract a bounded archive, preserving safe internal symlinks and
  rejecting traversal, special files, duplicate paths and resource excess.
  Reuse the existing managed-runtime verifier before atomic publication.
- Repeated requests reverify completed artifacts offline. Unknown files, links,
  changed catalogs and damaged published installations are preserved and block.
  Never replace a published generation or prune assets automatically.
- Return setup arguments for the existing shared `client setup` operation.
  Disconnect retains assets. Updates use a separate digest-bound generation;
  explicit disconnect is required before changing/removing assets in use.

## Approved verification seam

Use the already owner-approved connect -> coding-client request through the real
router -> inspect public result/status -> disconnect workflow. Acquisition is
its prerequisite. Replace only external artifact servers, Codex and llama.cpp
with small deterministic fixtures. Observe receipts, settings and external
traffic; do not mock acquisition/routing decisions or inspect private ledgers.
Work one red/green behavior at a time, then run all workflow tests, scope/import
checks, build and installed-wheel verification. No real weight downloads or
model inference during development. Standards and Spec review use the pinned
baseline `84ef5b2fe3e48ac3d830cf695dc1a6fc47a44d58`.

## Parallel work

#538 owns coding-outcome qualification and admission. PR #545 owns plugin
capability workflows. This branch owns acquisition and its catalog/receipts;
it leaves their active worktrees and implementation surfaces intact wherever
possible. Neither planning decision is closed by this implementation.
