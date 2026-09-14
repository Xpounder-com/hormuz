"""Script-free policy impact workflow with escaped, metadata-only values."""
from __future__ import annotations

from .console_pages import _e, page


def number(value):
    return "No additional limit" if value is None else f"{value:,}"


def shell(title, content, *, step=1):
    steps = ''.join(f'<span class="impact-step {"current" if i == step else ""}">{i} · {_e(label)}</span>'
                    for i, label in enumerate(("Preview", "Review", "Results"), 1))
    body = f'''<nav class="impact-nav" aria-label="Console navigation"><a href="/console">Usage</a>
      <a href="/console/policy">Policy changes</a><a href="/console/policy/activity">Activity</a></nav>
      <section class="overview"><p class="eyebrow">POLICY ADMINISTRATION</p><h1>{_e(title)}</h1></section>
      <div class="impact-steps" aria-label="Workflow progress">{steps}</div>{content}'''
    return page(title, body).replace('</head>', '<link rel="stylesheet" href="/console/policy.css"></head>')


def hidden(name, value):
    return f'<input type="hidden" name="{_e(name)}" value="{_e(value)}">'


def form(action, csrf, preview_id, text, *, acknowledgement=False, secondary=False):
    check = ('<label class="impact-check"><input type="checkbox" name="acknowledged" value="true" required>'
             '<span>I reviewed the scope and the effect on new requests.</span></label>') if acknowledgement else ''
    return f'''<form method="post" action="/v1/admin/policy/{_e(action)}">{hidden('csrf_token', csrf)}
      {hidden('preview_id', preview_id)}{check}<button class="{"secondary" if secondary else ""}" type="submit">{_e(text)}</button></form>'''


def setup(scopes, csrf):
    if not scopes["teams"] or not scopes["models"]:
        return shell("Preview a policy change", '<p class="empty-state">No managed teams or model routes are available.</p>')
    teams = ''.join(f'<option value="{_e(t["id"])}">{_e(t["name"])}</option>' for t in scopes["teams"])
    models = ''.join(f'<option value="{_e(m["alias"])}">{_e(m["alias"])}</option>' for m in scopes["models"])
    return shell("Preview a policy change", f'''<div class="impact-columns"><section class="panel">
      <p class="eyebrow">PROPOSED CHANGE</p><h2>Lower an output limit</h2>
      <form class="impact-form" method="post" action="/v1/admin/policy/preview">{hidden('csrf_token', csrf)}
      <label for="impact-team">Team</label><select name="team_id" id="impact-team" required>{teams}</select>
      <label for="impact-model">Model alias</label><select name="model_alias" id="impact-model" required>{models}</select>
      <label for="impact-limit">Proposed maximum output tokens</label><input id="impact-limit" name="proposed_limit" type="number" min="1" max="1000000" step="1" value="4000" required>
      <button type="submit">Preview impact →</button></form></section>
      <section class="panel"><h2>Compare before you apply</h2><p>Preview the new ceiling against captured requests using the current policy.</p>
      <dl class="detail-list"><div><dt>Scope</dt><dd>One team · One model</dd></div><div><dt>Observation window</dt><dd>Up to 7 days</dd></div>
      <div><dt>Active generation</dt><dd>{scopes['generation']}</dd></div><div><dt>Policy activation</dt><dd>Explicit approval</dd></div></dl>
      <p class="muted">The sample is bounded and may be incomplete. Older usage records do not contain the metadata needed for this comparison.</p>
      <p class="muted">The gateway’s existing organization, team, and person restrictions still apply.</p></section></div>''')


def preview(value, csrf, *, reviewing=False):
    c = value['comparison']
    count, affected = c['captured_requests'], c['lower_limit_requests']
    scope = f'{_e(value["team_id"])} · {_e(value["model_alias"])}'
    content = f'''<div class="impact-columns"><section class="panel"><p class="eyebrow">OUTPUT LIMIT</p><h2>{scope}</h2>
      <div class="impact-diff"><div><span>Current ceiling</span><strong>{number(value['current_limit'])}</strong></div><span>→</span>
      <div><span>Proposed ceiling</span><strong>{number(value['proposed_limit'])}</strong></div></div>
      <p class="muted">Tokens per response. A stricter request, person, or organization limit continues to win.</p>
      <dl class="detail-list"><div><dt>Baseline generation</dt><dd>{value['baseline_generation']}</dd></div>
      <div><dt>Approval valid until</dt><dd>{_e(value['expires_at'][11:19])} UTC</dd></div></dl>
      <p class="muted">Changes to the active policy invalidate this preview.</p></section>
      <section class="panel"><h2>What would change?</h2><p class="impact-number">{affected:,}<span> requests would get a lower limit</span></p>
      <p class="muted">Out of {count:,} captured requests under this exact baseline policy.</p>
      <dl class="detail-list"><div><dt>Limits unchanged</dt><dd>{c['unchanged_limit_requests']:,}</dd></div>
      <div><dt>Known successful completions</dt><dd>{c['known_completions']:,}</dd></div>
      <div><dt>Completions above proposed ceiling</dt><dd>{c['completions_above_limit']:,}</dd></div>
      <div><dt>Other or unknown completions</dt><dd>{c['unknown_completions']:,}</dd></div></dl>
      <div class="impact-caution">A lower limit may shorten responses. Cost savings and quality effects are not established by this comparison.</div>
      </section></div>'''
    if not count:
        content += '<p class="empty-state">No observations for this scope under the active policy. Send requests through a gateway with policy-impact capture enabled, then create a new preview.</p>'
    content += '<div class="impact-actions"><a href="/console/policy">Edit proposal</a>'
    if value['can_apply']:
        content += form('apply' if reviewing else 'review', csrf, value['preview_id'], 'Apply reviewed change' if reviewing else 'Review change →', acknowledgement=reviewing)
    content += '</div>'
    return shell('Review before applying' if reviewing else 'Preview impact', content, step=2 if reviewing else 1)


def results(value, csrf, *, rollback_review=False):
    p = value['preview']
    active = value['candidate_active']
    rows = ''.join(f'''<tr><td><code>{_e(r['request_id'])}</code></td><td>{_e(r['status'])}</td>
      <td>{number(r['effective_limit'])}</td><td>{'Unknown' if r['output_tokens'] is None else number(r['output_tokens'])}</td></tr>''' for r in value['receipts'])
    cost = f"${value['estimated_cost_microusd']/1_000_000:,.4f}" if value['cost_known_requests'] else 'Not available'
    if rollback_review:
        return shell('Review rollback', f'''<section class="panel"><h2>{_e(p['team_id'])} · {_e(p['model_alias'])}</h2>
          <p>Restore the full policy version that preceded this change. This is allowed only while this change is still the latest activation.</p>
          <div class="impact-diff"><div><span>Current ceiling</span><strong>{number(p['proposed_limit'])}</strong></div><span>→</span>
          <div><span>Previous ceiling</span><strong>{number(p['current_limit'])}</strong></div></div>
          {form('rollback', csrf, p['preview_id'], 'Confirm rollback', acknowledgement=True)}
          <p class="muted">New requests use the restored policy. The change and rollback remain in policy history.</p></section>''', step=3)
    status = 'The reviewed policy is active.' if active else 'The reviewed policy is not active.'
    content = f'''<p class="notice">{status} Current activation generation: {value['active_generation']}.</p>
      <div class="impact-columns"><section class="panel"><h2>Verify enforcement</h2><p class="muted">Make a request with a connected client, then refresh these receipts.</p>
      <p><a class="button secondary" href="/console/policy/results?preview_id={_e(p['preview_id'])}">Refresh results</a></p>
      <dl class="detail-list"><div><dt>Captured attempts on this candidate</dt><dd>{value['captured_requests']}</dd></div>
      <div><dt>Known errors</dt><dd>{value['known_errors']}</dd></div><div><dt>Unknown outcomes</dt><dd>{value['unknown_outcomes']}</dd></div></dl></section>
      <section class="panel"><h2>Results, with context</h2><dl class="detail-list"><div><dt>Estimated captured cost</dt><dd>{cost}</dd></div>
      <div><dt>Requests with known cost</dt><dd>{value['cost_known_requests']}</dd></div><div><dt>Cost savings</dt><dd>Not established</dd></div>
      <div><dt>Quality impact</dt><dd>Not measured</dd></div></dl><p class="muted">Configured-rate-card estimates for captured attempts since this preview. Missing results are not zero usage or proof of success.</p></section></div>
      <section class="panel impact-receipts"><h2>Latest request receipts</h2>'''
    content += (f'<div class="table-scroll"><table><thead><tr><th>Request</th><th>Outcome</th><th>Enforced maximum</th><th>Output tokens</th></tr></thead><tbody>{rows}</tbody></table></div>' if rows else '<p class="empty-state">No captured requests on this candidate yet.</p>')
    content += f'<p class="muted">Policy version <code>{_e(p["candidate_version"])}</code></p></section><div class="impact-actions"><a href="/console/policy">New proposal</a>'
    if value['rollback_available']:
        content += form('rollback-review', csrf, p['preview_id'], 'Review rollback', secondary=True)
    content += '</div>'
    return shell('Policy results', content, step=3)


def activity(history, proposals):
    recent = ''.join(f'<li><a href="/console/policy/results?preview_id={_e(p["preview_id"])}">'
                     f'{_e(p["team_id"])} · {_e(p["model_alias"])} · {number(p["proposed_limit"])} tokens</a>'
                     f'<span class="muted"> · {_e(p["created_at"][:19])} UTC</span></li>' for p in proposals)
    events = ''.join(f'<tr><td>{_e(e.occurred_at.isoformat())}</td><td>{_e(e.event_type)}</td>'
                     f'<td>{e.generation if e.generation is not None else "—"}</td><td><code>{_e(e.version_id)}</code></td></tr>'
                     for e in history.events)
    return shell("Policy activity", f'''<section class="panel"><h2>Your recent proposals</h2>
        <p class="muted">Up to 20 proposals from the past 7 days. Open a proposal to inspect its receipts and available rollback.</p>
        {"<ul>" + recent + "</ul>" if recent else "<p>No retained proposals yet.</p>"}</section>
        <section class="panel impact-receipts"><h2>Organization policy history</h2>
        <p class="muted">Latest 20 immutable lifecycle events. {_e("Earlier events are available through the policy history CLI." if history.has_more else "")}</p>
        <div class="table-scroll"><table><thead><tr><th>Time</th><th>Action</th><th>Generation</th><th>Policy version</th></tr></thead>
        <tbody>{events}</tbody></table></div></section>''', step=3)
