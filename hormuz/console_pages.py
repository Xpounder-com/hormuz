"""Escaped, script-free HTML for the bounded local administrator console."""

from __future__ import annotations

from html import escape
from urllib.parse import urlencode

from .console_store import ConsolePrincipal


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def page(title: str, content: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{_e(title)} · Hormuz</title><link rel="stylesheet" href="/console/styles.css">'
        '</head><body><a class="skip-link" href="#main">Skip to content</a>'
        '<header class="site-header"><a class="brand" href="/console" aria-label="Hormuz console home">'
        '<span class="brand-mark" aria-hidden="true">H</span>HORMUZ</a>'
        '<span class="preview">LOCAL PREVIEW</span></header>'
        f'<main id="main">{content}</main>'
        '<footer>Governed access. Clear accountability. <span>Local console preview · No hosted availability claim.</span></footer>'
        '</body></html>'
    )


def login_page(*, message: str = "") -> str:
    notice = f'<p class="notice" role="status">{_e(message)}</p>' if message else ""
    return page("Administrator sign-in", f'''
        <section class="login-panel"><p class="eyebrow">TEAM ADMINISTRATION</p>
        <h1>Your team's governed<br>AI access, in view.</h1>
        <p class="lead">Review usage and manage member access with your organization's identity provider.</p>
        {notice}<form method="post" action="/v1/admin/auth/start" class="login-form">
        <label for="organization">Organization ID</label>
        <input id="organization" name="organization_id" required maxlength="96" autocomplete="organization"
               pattern="[A-Za-z0-9][A-Za-z0-9_-]*" placeholder="Your organization ID" aria-describedby="login-help">
        <button type="submit">Continue to sign in <span aria-hidden="true">→</span></button></form>
        <p class="muted" id="login-help">Console access must first be granted by your Hormuz operator.
        An employee client session does not grant administrator access.</p></section>''')


def continue_page(url: str) -> str:
    return page("Continue sign-in", f'''
        <section class="login-panel"><p class="eyebrow">SECURE SIGN-IN</p><h1>Continue with your organization.</h1>
        <p class="lead">Continue only if you just requested administrator sign-in on this device.</p>
        <a class="button" href="{_e(url)}" rel="noreferrer">Open identity provider →</a>
        <p class="muted">Your identity provider verifies who you are. Hormuz checks your active console grant.</p></section>''')


def failure_page(message: str) -> str:
    return page("Request could not be completed", f'''
        <section class="login-panel"><p class="eyebrow">REQUEST NOT COMPLETED</p>
        <h1>Let's get you back.</h1><p class="lead" role="alert">{_e(message)}</p>
        <a class="button" href="/console">Return to console</a></section>''')


def _next_page(query: dict[str, str], key: str, cursor: str | None, label: str) -> str:
    if not cursor:
        return ""
    target = "/console?" + urlencode({**query, key: cursor})
    return f'<a class="page-link" href="{_e(target)}">{_e(label)} →</a>'


def dashboard(principal: ConsolePrincipal, report: dict, teams: dict, members: dict | None,
              csrf: str, query: dict[str, str], *, message: str = "") -> str:
    totals, window = report["totals"], report["window"]
    role = "Member administrator" if principal.role == "member_admin" else "Usage viewer"
    notice = f'<p class="notice" role="status">{_e(message)}</p>' if message else ""
    hidden_csrf = f'<input type="hidden" name="csrf_token" value="{_e(csrf)}">'
    team_options = "".join(f'<option value="{_e(team["id"])}">{_e(team["name"])}</option>' for team in teams["items"])
    cards = (
        ("Gateway requests", f'{totals["requests"]:,}', "All recorded request outcomes"),
        ("Input + output tokens", f'{totals["total_tokens"]:,}', "Reported through the gateway"),
        ("Estimated cost", f'${totals["cost_microusd"] / 1_000_000:,.4f}', "USD · configured rate card"),
        ("Policy denials", f'{totals["denied_requests"]:,}', f'{totals["rate_limited_requests"]:,} rate-limited requests separately'),
    )
    metrics = "".join(f'<article class="metric"><h2>{_e(label)}</h2><p class="metric-value">{_e(value)}</p><p>{_e(note)}</p></article>'
                      for label, value, note in cards)
    empty = ('<p class="empty-state">No gateway requests were recorded for this scope and date range.</p>'
             if not totals["requests"] else "")
    team_rows = "".join(f'<li><strong>{_e(item["name"])}</strong><code>{_e(item["id"])}</code></li>' for item in teams["items"])
    team_next = _next_page(query, "teams_after", teams["next_cursor"], "Next teams")
    member_section = '<section class="panel"><h2>Member access</h2><p class="muted">Your usage viewer role does not include member administration.</p></section>'
    if members is not None:
        rows = []
        for member in members["items"]:
            own = member["id"] == principal.membership_id
            control = '<span class="muted">This account</span>' if own else '<span class="muted">Access removed</span>'
            if not own and member["status"] != "disabled":
                control = f'''<details class="remove-control"><summary>Remove access</summary>
                    <p>Revoke this member's client and console access. Rejoining requires a new invitation.</p>
                    <form method="post" action="/v1/admin/members/disable">{hidden_csrf}
                    <input type="hidden" name="membership_id" value="{_e(member['id'])}">
                    <input type="hidden" name="expected_version" value="{_e(member['authorization_version'])}">
                    <button class="danger" type="submit" aria-label="Confirm removal of {_e(member['name'])}">Confirm removal</button></form></details>'''
            rows.append(f'''<tr><th scope="row"><span>{_e(member['name'])}</span><code>{_e(member['id'])}</code></th>
                <td><code>{_e(member['team_id'])}</code></td><td><span class="status">{_e(member['status'])}</span></td>
                <td>{_e(', '.join(member['allowed_clients']))}</td><td>{control}</td></tr>''')
        table = (f'''<div class="table-scroll" role="region" aria-label="Organization members" tabindex="0"><table>
            <thead><tr><th scope="col">Member</th><th scope="col">Team</th><th scope="col">Status</th>
            <th scope="col">Clients</th><th scope="col">Access</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>'''
            if rows else '<p class="empty-state">No members on this page.</p>')
        member_next = _next_page(query, "members_after", members["next_cursor"], "Next members")
        member_section = f'''<section class="panel members"><div class="section-heading"><div><p class="eyebrow">PEOPLE</p>
            <h2>Member access</h2></div><span class="muted">Your organization only · 20 per page</span></div>
            {table}{member_next}<p class="muted panel-note">Invitations and administrator grants are managed by the server operator.
            Removing access also revokes existing sessions.</p></section>'''
    return page("Team overview", f'''
        <div class="workspace-bar"><div><strong>{_e(principal.organization_name)}</strong><code>{_e(principal.organization_id)}</code></div>
        <div class="account"><span>{_e(principal.name)}<small>{_e(role)}</small></span>
        <form method="post" action="/v1/admin/logout">{hidden_csrf}<button class="secondary" type="submit">Sign out</button></form></div></div>
        <section class="overview"><p class="eyebrow">OVERVIEW</p><h1>Your team's AI usage.</h1>
        <p class="lead">See what passes through your gateway. Keep access accountable.</p></section>{notice}
        <form class="filters panel" method="get" action="/console" aria-label="Filter usage">
        <div><label for="from-date">From · UTC</label><input id="from-date" type="date" name="from_date" required value="{_e(window['from_date'])}"></div>
        <div><label for="through-date">Through · UTC</label><input id="through-date" type="date" name="through_date" required value="{_e(window['through_date'])}"></div>
        <div class="team-filter"><label for="team-id">Team ID · optional</label><input id="team-id" name="team_id" maxlength="96"
          list="team-options" value="{_e(report['scope']['team_id'] or '')}" placeholder="All organization teams"><datalist id="team-options">{team_options}</datalist></div>
        <button type="submit">Apply filters</button><p class="filter-note">Up to 31 inclusive UTC days. Leave team blank for your whole organization.</p></form>
        <section class="metrics" aria-label="Usage totals">{metrics}</section>{empty}
        <div class="two-column"><section class="panel"><p class="eyebrow">USAGE DETAIL</p><h2>Tokens &amp; protection</h2>
        <dl class="detail-list"><div><dt>Input tokens</dt><dd>{totals['input_tokens']:,}</dd></div>
        <div><dt>Output tokens</dt><dd>{totals['output_tokens']:,}</dd></div><div><dt>Cache read tokens</dt><dd>{totals['cache_read_tokens']:,}</dd></div>
        <div><dt>Cache write tokens</dt><dd>{totals['cache_write_tokens']:,}</dd></div><div><dt>Reasoning tokens</dt><dd>{totals['reasoning_tokens']:,}</dd></div>
        <div><dt>Secret redactions</dt><dd>{totals['redaction_count']:,}</dd></div></dl>
        <p class="muted">Cache and reasoning counts may overlap provider token totals. Do not add them to input + output.</p></section>
        <section class="panel"><p class="eyebrow">ORGANIZATION</p><h2>Teams</h2><ul class="team-list">{team_rows}</ul>{team_next}
        <p class="muted">20 teams per page. Enter any team ID in your organization to filter usage.</p></section></div>
        {member_section}<aside class="scope-note"><strong>What this view measures</strong>
        <p>Gateway-captured requests only. Cost uses your configured rate card; it is an estimate, not a provider invoice.
        This view does not measure availability, latency, or employee productivity.</p></aside>''')
