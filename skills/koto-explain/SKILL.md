---
name: koto-explain
description: Explain where an observed Koto request ran, its routing policy, fallback, latency, usage or missing evidence.
---

# Explain

1. Read [the capability workflow](../koto/references/capabilities.md).
2. Use `history list` to select an unambiguous retained request; ask which entry only when the user's reference cannot be resolved. Do not enable history as a side effect of explanation.
3. Run `koto --config CONFIG request explain ENTRY_ID`. Report observed destination/locality, policy and fallback, latency and usage. State the retained opt-in coverage and unobserved optimization evidence.
4. If the entry is absent or legacy evidence is missing, report that limit. Keep retained text out of agent tools and chat.
