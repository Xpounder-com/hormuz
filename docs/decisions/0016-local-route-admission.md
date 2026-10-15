# ADR 0016: Admit local execution before optional optimization

Status: Accepted

Local-only privacy cannot depend on optional AI Work accounting or an optimizer's
fallback behavior. A coding-client request may be unaccounted, a helper, unknown,
continuing, or outside a model's tested envelope.

One deep `RouteAdmission` module owns an immutable operator qualification envelope,
bounded prompt-free readiness observations, eligible decisions, final dispatch
checks, local capacity leases and their public receipt/status. Its interface is
used by the common provider workflow; the existing work selector only sees the
admitted set. Configuration defaults preserve legacy behavior.

Fresh recognized work prefers eligible local execution. Unknown work retains an
explicit permitted cloud baseline or blocks. Local-only removes cloud execution
before optional selection and is checked again before delivery/open. Stateful
continuations require retained successful affinity and matching destination
generation. Unqualified helpers, alternates and opaque state cannot bypass it.

Readiness and task hints remain observations, while qualification remains an
operator declaration. A literal loopback origin alone cannot establish that its
listener performs local inference. Model/runtime acquisition and client-specific
qualification remain separate. No classifier inference, transcript ledger,
subscription credential reuse or new coding app is introduced.

The adapter seam approved by the user remains connect, submit through the router,
inspect result/status, disconnect. Synthetic loopback providers test actual
dispatch, hard privacy, accounting independence, helper gating, affinity,
streaming and cleanup. They do not establish real model quality, subscription
eligibility, operating-system support or marketplace approval.
