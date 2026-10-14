# Shared desktop worker

`Connection<C,S,T,K,B>` is the existing Windows worker, shared without changing
its session policy. It owns one worker, one pending command and one coalesced
notification. `SessionController` remains the only enrollment, credential,
refresh, usage and revocation authority. A notifier may only enqueue an event;
it must not synchronously acquire the worker mutex or manipulate UI objects.

Shells supply private-file coordination, credential custody, transport, clock,
validated browser dispatch and lifecycle notifications. The worker starts
locked and hidden; shells must establish their actual visibility/session state.
Sign-out cancels prior work, clears usage immediately, and retains failed server
revocation for an explicit retry. Drop joins the owned worker and drains an
already accepted sign-out. No toolkit, polling loop or platform credential store
is selected by this crate.

The moved injected tests exercise the real session controller, including locked
custody, scope rejection, cancellation, late responses, pending refresh/logout,
expiry, offline sample time, scheduler suspension and quit-after-sign-out.
`presentation` formats only authenticated snapshots; missing data stays missing,
and costs remain rate-card estimates rather than provider bills.
