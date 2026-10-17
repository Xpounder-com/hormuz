# 0027: Separate native Codex catalog credentials from API routing

Status: implemented discovery foundation, October 15, 2026.

The owner requested the next independent cloud-auth implementation after #547,
using TDD and preserving parallel work. The documented native gateway path
needs both a Koto gateway credential and a native ChatGPT credential, while
the existing API/local adapter owns its own bearer credential. Reusing that
adapter's authentication or paid API fallback would conflate credential custody
and billing scopes.

Add a default-off, fixed-backend native catalog endpoint and an explicit native
authentication profile in the existing destination owner. Authenticate the
Koto identity separately, apply its effective policy and local-only ceiling,
then forward only the native headers over verified TLS. Keep native profiles
ineligible for inference and generic/API discovery. Native failures never
select another billing path. The cost is an intentionally incomplete native
path until inference and actual client/account qualification are implemented;
catalog success alone cannot advance cloud-readiness status.

Use an isolated, parent-owned fetch worker because the system resolver cannot
be cancelled by a Python socket timeout. A deadline terminates and reaps that
worker rather than leaving lookup threads alive. Native credentials use only
its private stdin pipe; one catalog request costs one short-lived worker, capped
at eight, in exchange for a deadline that covers DNS and bounded cleanup.

The additive version-1 header contract leaves provider model JSON intact and
existing errors/stores unchanged. Disabling the feature is reversible; config
rollback removes native profiles and references before downgrade. The boundary
does not select a release/deployment envelope or complete #523/#541/#543.

See [the contract and rollback instructions](../NATIVE_CODEX_CATALOG.md) and
[the implementation review](https://github.com/Xpounder-com/hormuz/pull/550).
