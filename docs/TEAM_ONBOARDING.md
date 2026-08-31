# Local team onboarding

This opt-in preview adds operator-managed organizations, teams, invitations and
member removal to the single-node browser login broker. It does not deploy a
hosted service or give an employee session access to administrative APIs.

## Contract

- A server-local operator holds the gateway configuration, session master key
  and database access. The `hormuz team` commands are the administrative boundary;
  the event actor is `server_local_operator`, not a claimed human administrator.
- An operator creates a new organization bound to one configured login issuer,
  then a team. Existing configured organizations cannot be taken over. Team IDs
  cannot overlap configured identities or another managed team.
- An invitation fixes the organization, team, allowed clients and clearance. Its
  random code is written once to a new private file for manual delivery. Hormuz
  does not send email. Only a keyed code hash and a keyed recipient-email hash
  enter the database; neither value is logged or returned by listing commands.
- The client starts its existing login with the organization ID. The browser
  confirmation page optionally accepts the invitation code in a same-origin
  form POST. The code never belongs in a URL. The form is bound to the browser
  cookie, OAuth state and enrollment; callback PKCE and nonce checks still apply.
- Acceptance requires a signed ID token from the organization's trusted issuer,
  the expected audience and nonce, and an exact recipient email with boolean
  `email_verified: true`. Email-domain case is ignored; local-part case and aliases
  are not normalized. This preview supports bounded ASCII email addresses.
- On first acceptance the membership binds permanently to `(issuer, subject)`.
  Later login uses that stable identity, not the email. A reissued email address
  cannot take over a membership. A disabled member can receive an explicit new
  invitation, but only the original subject can reactivate an established account.
- Invitation acceptance and enrollment authorization commit together. Membership
  removal revokes every session, pending invitation and authorized enrollment in
  the same database transaction. Every access check, refresh and redemption checks
  active membership and its authorization version. After removal commits, a new
  authorization check fails; a request admitted before that commit may finish.
  Unidentified pending logins in that organization are also cancelled, because
  their user is not known yet; other affected users can start a fresh login.
- Managed membership is authoritative for a managed organization. There is no
  fallback to static/configured subjects when access is disabled or onboarding is
  switched off. Configuration collisions fail startup.
- Organization and team policy/model configuration remains gateway-wide as in
  the preceding preview. This is not tenant-specific provider-key custody,
  independent customer policy administration, billing, or a shared production SaaS.

The identity choice follows [OpenID Connect claim stability](https://openid.net/specs/openid-connect-core-1_0.html#ClaimStability).
The browser binding retains the protections described in
[OAuth Security BCP, CSRF prevention](https://www.rfc-editor.org/rfc/rfc9700.html#name-cross-site-request-forgery).

## Operator workflow

Use the server checkout or installed package on Linux/macOS. Enable
`authentication.session_broker.onboarding_enabled: true` alongside the existing
broker configuration. Add `email` to the approved issuer's `login.scopes`; configure
the IdP to include `email` and boolean `email_verified` in its signed ID token. The
broker requests those claims explicitly when an invitation is attached, and rejects
the join if they are absent. This preview does not call the UserInfo endpoint.
An issuer used only for managed login may have an empty `subjects` array.

```sh
hormuz --config /private/operator/hormuz.json team organization create \
  --organization acme --name Acme --issuer https://identity.example.com
hormuz --config /private/operator/hormuz.json team create \
  --organization acme --team acme-engineering --name Engineering
```

Prepare a private, mode-0600 file containing only the recipient's email. Do not put
emails or invitation codes in shell history. Then issue the invitation:

```sh
hormuz --config /private/operator/hormuz.json team invite \
  --organization acme --team acme-engineering --name 'Invited member' \
  --email-file /private/operator/recipient.txt --client codex --client claude-code \
  --output /private/operator/invitation.json
```

The output file must not exist. It contains the gateway origin, organization ID,
member/invitation IDs, expiry and one-time code. Deliver it privately yourself;
the command prints only non-secret status metadata. Default expiry is one hour,
configurable with `--expires-in` from 300 to 86400 seconds. The Mac app's existing
organization field or `hormuz login --organization acme` selects the organization.
The recipient enters the code on the browser confirmation page, then follows the
sign-in link and authenticates with the invited account. Ordinary future logins
need no code. Client-specific sessions remain scoped to their chosen client.

```sh
hormuz --config /private/operator/hormuz.json team members list --organization acme
hormuz --config /private/operator/hormuz.json team members disable \
  --organization acme --member MEMBER_ID
hormuz --config /private/operator/hormuz.json team invitations list --organization acme
hormuz --config /private/operator/hormuz.json team invitations revoke \
  --organization acme --invitation INVITATION_ID
hormuz --config /private/operator/hormuz.json team members reinvite \
  --organization acme --member MEMBER_ID --output /private/operator/new-invitation.json
hormuz --config /private/operator/hormuz.json team events --organization acme
```

Lists use `--limit` (1–100) and `--after` with the previous `next_cursor`. They are
ordered by ID and are not a transactionally frozen multi-page snapshot. Membership
lists omit email hashes and issuer subjects. Setup and disable/revoke commands are
idempotent. Reissuing an invitation is explicit and only permitted for a disabled
member; it preserves the member's permissions and established subject binding.
Revoking an accepted invitation does not remove its member: use `members disable`.
Expired pending invitations must be revoked before a new invitation is issued.

No operation sends email or requires a Render account. Run the implementation tests:

```sh
python -m unittest tests.test_onboarding_store tests.test_onboarding_http \
  tests.test_onboarding_migration tests.test_team_commands tests.test_session_config
```

For a disposable local browser fixture, run
`python -m tests._onboarding_browser_fixture --handoff-file /tmp/NEW-private-invite.json`.
Open the printed login URL, enter the code from that private file, and use the
explicitly labeled simulated identity provider. After Connected appears, enter
`verify` at the fixture's terminal. It checks redemption, personal identity/usage,
and removal, without calling any model. Enter `exit` to stop it and remove its
private temporary state. This is developer evidence, not an independent customer
onboarding session.

## Storage and upgrades

The [administrator console](ADMIN_CONSOLE_LOCAL.md) now upgrades session storage
to schema 4, preserving the v2 key derivation and v3 membership behavior below.
It adds separate operator grants and browser sessions; member removal revokes
those grants and sessions in the same transaction. Reinvitation never restores
an administrator grant. Console enablement is separate and off by default.

Session schema 3 adds a closed set of team/membership/invitation/event tables and
version bindings on existing enrollments and sessions. Stop all broker/operator
processes and make a SQLite-consistent private backup before upgrading. A valid
schema 2 database upgrades atomically on opening; existing configured-subject
sessions retain their hashes, keys and lifetimes. Unexpected fields, tables,
views and triggers are rejected. An older binary refuses schema 3; rolling back requires restoring the
pre-upgrade database with its matching configuration and master key. Restoring
that backup also restores its old access decisions, so reapply removals before
serving traffic.

The master key also protects recipient hashes. Do not rotate it in place for a
managed directory: existing pending invitations and reinvitation email checks
cannot be rekeyed from a one-way hash. Managed-directory key migration and safe
restore/revocation reconciliation remain production gates. The earlier
session-only advice to rotate a key and force fresh logins is not a complete
managed-directory recovery procedure.

This remains SQLite on one node with manual retention and private backups. Raw
identity-provider tokens, invite codes, email addresses and AI request/response
bodies are excluded from these tables. Identity IDs and operator-supplied names
are still personal/organization metadata. Events are transactional local records,
not an externally anchored or tamper-proof audit service.

## Remaining production gates

Real identity-provider claim configuration and browser testing, qualification of
the new local administrator console, tenant-specific provider credentials and policies,
distributed persistence/rate limits, signed client distribution, Render deployment
and recovery evidence, service monitoring, and a real customer pilot remain separate
work. A free hosting instance is not evidence of latency or availability guarantees.
