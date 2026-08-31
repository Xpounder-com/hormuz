# Local administrator console

This opt-in preview adds a browser console to the managed-team directory. It
provides organization/team usage and member removal. It does not deploy a hosted
service, grant policy/custody authority, or replace the native client session.

## Authorization contract

- Enable `authentication.session_broker.console_enabled` together with the broker
  and `onboarding_enabled`. The console is disabled by default.
- A server-local operator grants `report_viewer` or `member_admin` to an already
  active, verified membership. Grants cannot be created or changed over HTTP.
  `report_viewer` reads organization/team usage; `member_admin` additionally lists
  members and removes access. Neither role can change provider keys or policies.
- Console login uses the existing configured OIDC issuer with its own exact
  `/v1/admin/auth/callback` redirect URI. Register that additional URI at the IdP.
  Authorization code, PKCE, state, nonce, signature, issuer and audience checks
  apply. Grant lookup uses the verified issuer/subject and active membership,
  never email, groups, request headers, or a claimed role.
- Console credentials are opaque HttpOnly cookies, separate from native access
  and refresh credentials. They cannot call inference APIs; inference credentials
  cannot call the console. HTTPS uses a host-only Secure cookie. Explicit local
  HTTP is development-only. No credential enters browser storage or a URL.
- A console session expires after ten idle minutes or one hour absolutely, with
  no refresh-token API. A new console login replaces prior console sessions for
  the same grant. Native client sessions are unaffected by console login/logout.
- Every request rechecks membership, its authorization version, the active grant
  and its version. Changing/revoking a grant revokes its console sessions. Removing
  a member revokes both native and console access and their console grant in one
  transaction. Reinvitation never restores an administrator grant automatically.
- POST requests require the exact same Origin and a session-bound CSRF token.
  Member removal rechecks the acting administrator inside the write transaction,
  requires the displayed target authorization version, and refuses self-removal.
  Removing an already disabled member is idempotent; a stale page cannot remove a
  subsequently reactivated member. The actor is recorded as the verified member,
  not `server_local_operator`.
- Revocation also cancels unidentified pending console logins in that organization
  because the user is not known until callback. Other users can restart login.

## Operator and browser workflow

Join the managed team through the existing invitation flow, then grant console
access from the server's private operator environment:

```sh
hormuz --config /private/operator/hormuz.json team administrators grant \
  --organization acme --member MEMBER_ID --role member_admin
hormuz --config /private/operator/hormuz.json team administrators list \
  --organization acme
hormuz --config /private/operator/hormuz.json team administrators revoke \
  --organization acme --member MEMBER_ID
```

Open `/console` at the configured public gateway origin, enter the organization
ID, and sign in through the IdP. The console shows the verified organization and
role, bounded usage filters, and member controls when authorized. Invitations and
reinvitation remain operator-assisted private-file operations in this slice.

## HTTP contract

All console routes require the configured exact Host and existing ingress checks.
No endpoint accepts bearer credentials, caller-selected organization headers,
redirect targets, credentials in queries, duplicate fields or extra fields.
The only cross-origin POST is the OIDC callback, bound to its one-time flow cookie.

Form pages use `Referrer-Policy: strict-origin`: browser form posts retain their
Origin, while referrers contain no path/query and are suppressed on HTTPS
downgrades. Identity-provider links additionally use `rel=noreferrer`. API and
stylesheet responses retain `no-referrer`. Using `no-referrer` on a form page
causes browsers to send `Origin: null`, which the exact-origin guard must reject;
see the [Fetch Origin algorithm](https://fetch.spec.whatwg.org/#append-a-request-origin-header).
Do not accept a null Origin as a workaround for a broken form.

| Route | Authority | Behavior |
| --- | --- | --- |
| `GET /console` | Optional console cookie | Sign-in form or dashboard |
| `POST /v1/admin/auth/start` | Same Origin | Start an organization-bound OIDC flow |
| `POST /v1/admin/auth/callback` | One-time flow/cookie | Verify identity and issue a console cookie |
| `GET /v1/admin/me` | Active console session | Own role, organization, expiry and CSRF token |
| `GET /v1/admin/usage` | Either console role | Bounded usage totals for own organization or a team in it |
| `GET /v1/admin/teams` | Either console role | Paginated team metadata in own organization |
| `GET /v1/admin/members` | `member_admin` | Paginated, explicitly projected member metadata |
| `POST /v1/admin/members/disable` | `member_admin` + CSRF | Version-checked member removal |
| `POST /v1/admin/logout` | Console session + CSRF | Revoke this console session |

JSON responses have a separate `schema_id` (`hormuz.admin-identity`,
`hormuz.admin-usage`, `hormuz.admin-list`, `hormuz.admin-member-removal`,
`hormuz.admin-logout` or `hormuz.admin-error`) and integer `schema_version: 1`.
Errors use a stable
code and fixed message; they omit database errors, token claims and request data.
Usage accepts `from_date`, `through_date` and optional `team_id`. Dates are UTC
calendar dates, inclusive, with a maximum 31-day range ending no later than today.
Omitting both dates selects month to date. Both dates must be supplied together.
The service converts the inclusive end to an exclusive UTC timestamp and uses the
existing scoped usage repository. Costs are configured-rate-card estimates for
gateway-captured requests, not provider invoices or an availability measurement.
Team/member pages accept `after` and `limit` (1–100, default 20); cursors are scoped
metadata IDs, not authorization. Multi-page reads are not a frozen snapshot.

Authenticated mutations accept either JSON or URL-encoded forms, with a required
`csrf_token` from `/v1/admin/me` or the rendered form. Member removal also requires
`membership_id` and `expected_version` (positive JSON integer; decimal text in a
form). No other fields are accepted. JSON requests return the versioned result;
browser forms render confirmation or redirect to the fixed `/console` route.
Login start and callback accept forms only. Login start requires the exact Origin;
the callback instead requires the one-time state/flow cookie. Role mismatch and
CSRF/origin rejection return 403; stale/self-removal returns 409; missing or revoked
console sessions return 401; invalid windows/fields return 400; unavailable scoped
members/teams return 404; storage/provider outages return a content-free 503.

To verify with disposable local data, run
`python -m tests._console_browser_fixture`, open the printed URL, and enter
`customer-a`. Its explicitly labeled identity simulator uses no Okta account.
After removing the synthetic member, type `verify` in the fixture terminal for
server-side revocation checks. Exit the fixture to remove its private databases.
This is not the real identity-provider or independent onboarding gate.

## Storage and verification limits

Session schema 4 adds closed console grant, login-flow, session and event tables
to the same SQLite database as memberships. This keeps removal atomic. Valid v2
and v3 databases upgrade transactionally without changing the existing v2 key
derivation. Older binaries refuse v4. Take a private consistent backup before
upgrading; rollback and restore must reconcile revoked access before serving.

No raw session cookie, CSRF token, IdP token, email address, or AI content enters
the console tables. PKCE/nonce flow state is encrypted and removed on consumption;
credentials are keyed hashes. Member names and IDs remain personal metadata.
Events are transactional local records, not immutable externally anchored audit.
Retention, master-key/public-origin migration, distributed enforcement and managed
directory recovery remain production work.

Chrome browser checks now cover console sign-in, organization/team reporting,
foreign-team refusal, member removal and logout against the local simulator.
The removed member's existing client credential was rejected, and the verified
administrator was recorded as the actor. The authenticated layout was inspected
at 1280- and 390-pixel widths, including keyboard access to the scrolling member
table. The separate invitation browser flow also reached Connected and passed
redemption, identity/usage and removal/refresh-rejection checks.

The earlier `Origin: null` rejection was reproduced in Chrome and traced to
`no-referrer` on native form pages. The form-page policy above corrects the
browser-generated header; exact Origin and CSRF checks remain in force. No
browser protection, request header or cookie was overridden to obtain the proof.

These browser checks use disposable local identities and provider simulators;
they do not qualify real Okta, the invited Mac/Keychain workflow, HTTPS cookie
behavior on the intended public origin, or independent customer onboarding.
Render deployment/recovery, signed distribution, provider custody/policies,
policy/budget administration, billing and a real customer pilot remain separate
gates.
