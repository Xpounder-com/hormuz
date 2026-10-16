# Native Responses follow-up

Owner request remains native Codex Responses through TDD, required reviews, CI
and protected merge, preserving parallel work. Baseline is merged #554 at
45bc75e2b5540d539cbbef285d77e49619b51921. A later automatic review identified
four remaining gaps in that implementation.

- Separate fresh assistant text/input_text from provider output messages.
  Output text/refusal requires typed message identity, status and retained
  successful affinity. Stripping provider fields cannot authorize fresh output.
- Validate message phase and preserve issued assistant phase on replay.
  Optional null/omitted phase remains equivalent. Fresh assistant text retains
  valid phase support from the native input schema.
- Preserve the shared policy-action header semantics: append +redacted after
  detected replacement, including alongside an output cap.
- Record content-free native detections/denials in existing shared security
  events and public security totals. A protected-member guard contributes one
  observed guard detection. No native authorization/account/payload or priced
  native usage/cost entry is introduced.

Use the approved configure/connect, real-router request, observable result or
public status, controlled external traffic, disconnect seam. Do not query
private ledgers or mock internal routing collaborators. Keep all transport,
auth, policy, privacy, continuation, resource and default-off boundaries from
the originating spec. No real accounts, settings, providers or model downloads.

Each correction needs a failing public workflow followed by the smallest fix
and a passing workflow. Renew exact-head Standards, Spec, technical-lead and
GitHub reviews, all required CI, then normal protected merge and exact-main
log/artifact verification. Observe the automatic ready-triggered review before
merging this follow-up because it surfaced the preceding post-merge findings.
