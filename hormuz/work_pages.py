"""Escaped, script-free AI work experience; every number comes from runtime state."""

from html import escape
from decimal import Decimal


def page(title, body, *, workspace_enabled=True, console_enabled=True):
    navigation = ('<a href="/workspace">Workspace</a>' if workspace_enabled else '') + ('<a href="/console">Administration</a>' if console_enabled else '')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · Hormuz</title><link rel="stylesheet" href="/work.css"></head><body><a class="skip-link" href="#content">Skip to content</a><header class="site-header"><a class="brand" href="/work"><span class="brand-mark" aria-hidden="true">H</span>HORMUZ</a><nav aria-label="Workspace"><a href="/work" aria-current="page">AI work</a>{navigation}</nav></header><main id="content">{body}</main><footer><span>Hormuz · AI work</span><span>Covered requests only. Provider fees are separate.</span></footer></body></html>'''


def login(*, workspace_enabled=True, console_enabled=True):
    target = "/workspace" if workspace_enabled else "/console" if console_enabled else None
    action = f'<a class="button" href="{target}">Sign in to your workspace →</a>' if target else '<p>Browser sign-in is not configured. Ask your gateway administrator for authorized application access.</p>'
    return page("AI work", f'''<section class="login-panel"><p class="eyebrow">Your AI work, under control</p><h1>A budget.<br>A priority.<br>A clear record.</h1><p class="lead">Connect supported API agents, attach their requests to a job, and account for every captured attempt.</p>{action}<p class="muted">A configured gateway identity can also use the authenticated work API. No provider keys are entered on this page.</p></section>''', workspace_enabled=workspace_enabled, console_enabled=console_enabled)


def error_message(reason):
    return {"work_session_required": "Sign in to open your AI work.", "work_administrator_required": "Your verified identity needs administrator access to change shared budgets.", "work_origin_rejected": "Submit this form from the same Hormuz address.", "workspace_csrf_rejected": "The form expired or could not be verified. Reload your work page and try again.", "admin_csrf_rejected": "The form expired or could not be verified. Reload your work page and try again.", "work_not_enabled": "AI work is not enabled on this gateway.", "work_not_found": "This job is not available to your account.", "work_budget_exceeded": "The approved budget cannot cover this request. Review the job and its parent budgets.", "work_invalid_budget": "Enter a non-negative USD amount with at most six decimal places.", "work_storage_unavailable": "Work storage is temporarily unavailable.", "work_invalid_observation_source": "Use workflow, agent, or operator as the observation source."}.get(reason, "The request could not be completed. Reload the work page and check the submitted values.")


def failure(message, **navigation):
    return page("Review your request", f'''<section class="login-panel"><h1>Review your request</h1><p class="lead">{escape(message)}</p><a class="button" href="/work">Return to AI work →</a></section>''', **navigation)


def _money(value):
    if value is None:
        return "No limit set"
    amount = Decimal(int(value)) / Decimal(1_000_000)
    return "$" + (f"{amount:.6f}".rstrip("0") if 0 < amount < Decimal("0.01") else f"{amount:,.2f}")


def _number(values, *keys):
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return 0


def _hidden(csrf):
    return f'<input type="hidden" name="csrf_token" value="{escape(csrf)}">'


def _objective(value="cost", *, label="Priority", input_id="objective"):
    choices = (("cost", "Cost first"), ("speed", "Speed first"), ("quality", "Outcome first"))
    options = "".join(f'<option value="{key}"{" selected" if key == value else ""}>{name}</option>' for key, name in choices)
    return f'<div><label for="{escape(input_id)}">{label}</label><select id="{escape(input_id)}" name="objective">{options}</select></div>'


def _plan_form(csrf, scope_type, scope_id, *, plan=None, label="Save plan", form_id="plan"):
    plan = plan or {}
    budget = plan.get("budget_microusd")
    version = f'<input type="hidden" name="expected_version" value="{int(plan.get("version", 0))}">'
    amount = "" if budget is None else f"{Decimal(int(budget)) / Decimal(1_000_000):.6f}".rstrip("0").rstrip(".")
    return f'''<form class="work-form" method="post" action="/v1/work/policies">{_hidden(csrf)}<input type="hidden" name="scope_type" value="{escape(scope_type)}"><input type="hidden" name="scope_id" value="{escape(scope_id)}">{version}<div><label for="{form_id}-budget">Approved budget (USD)</label><input id="{form_id}-budget" name="budget_usd" type="number" min="0" step="0.000001" value="{amount}" placeholder="No limit set" aria-describedby="{form_id}-help"><p id="{form_id}-help" class="muted">Blank removes this scope’s limit. Zero blocks paid calls. Parent limits still apply.</p></div>{_objective(plan.get('objective', 'cost'), input_id=form_id+'-objective')}<button type="submit">{escape(label)}</button></form>'''


def dashboard(state, *, message="", **navigation):
    principal = state["principal"]
    csrf = principal["csrf_token"]
    works, plans = state.get("works", []), state.get("plans", [])
    totals = state.get("totals", {})
    cost = _number(totals, "committed_microusd")
    pending = _number(totals, "pending_microusd") + _number(totals, "uncertain_microusd")
    attempts = max(0, _number(totals, "attempts") - _number(totals, "cache_hits") - _number(totals, "denied"))
    unknown = _number(totals, "unknown", "unknown_outcomes", "unknown_work_count")
    notice = f'<p class="notice" role="status">{escape(message)}</p>' if message else ""
    display = state.get("display", {})
    jobs_total = totals.get("works", len(works))
    summary_note = f'<p class="scope-note">Showing {len(works):,} of {int(jobs_total):,} captured jobs. Totals cover the full authorized scope. Open an owned job for additional attempt details.</p>' if display.get("jobs_truncated") else ""
    plans_note = f'<p class="muted">Showing {len(plans):,} of {int(display.get("total_plans", len(plans))):,} plans.</p>' if display.get("plans_truncated") else ""
    header = f'''<div class="workspace-bar"><div><p class="eyebrow">AI work</p><h1>Set the boundaries.<br>Keep work moving.</h1><p class="lead">Your captured jobs, their full execution cost, and the priorities you choose.</p></div><div class="account"><div><strong>{escape(principal['organization_id'])}</strong><small>Signed-in account · {escape(principal['actor_id'])}</small></div><a class="button secondary" href="/work">Refresh</a></div></div>{notice}'''
    metrics = f'''<section class="metrics work-metrics" aria-label="Captured work totals"><article class="metric"><h2>Settled estimate</h2><p class="metric-value">{_money(cost)}</p><p>Configured provider rates · captured attempts</p></article><article class="metric"><h2>Reserved for in-flight work</h2><p class="metric-value">{_money(pending)}</p><p>Reservations remain until settlement</p></article><article class="metric"><h2>Provider attempts</h2><p class="metric-value">{int(attempts):,}</p><p>Retries and fallbacks are attempts</p></article><article class="metric"><h2>Jobs observed</h2><p class="metric-value">{int(jobs_total):,}</p><p>Missing completion evidence stays unknown</p></article></section>'''
    workspace_plan = next((p for p in plans if p.get("scope_type") == "workspace"), None)
    if principal["can_manage_plans"]:
        shared = f'''<section class="two-column"><article class="panel"><p class="eyebrow">Workspace plan</p><h2>One shared budget and default priority</h2><p class="muted">Workspace and repository budgets apply to the UTC month. Job budgets apply over the job’s lifetime.</p>{_plan_form(csrf, 'workspace', principal['organization_id'], plan=workspace_plan, form_id='workspace-plan')}</article><article class="panel"><p class="eyebrow">Repository plan</p><h2>Set a repository boundary</h2><form class="work-form" method="post" action="/v1/work/policies">{_hidden(csrf)}<input type="hidden" name="scope_type" value="repository"><input type="hidden" name="expected_version" value="0"><div><label for="repo-plan-id">Repository</label><input id="repo-plan-id" name="scope_id" placeholder="organization/repository" maxlength="255" required></div><div><label for="repo-plan-budget">Monthly approved budget (USD)</label><input id="repo-plan-budget" name="budget_usd" type="number" min="0" step="0.000001" placeholder="No limit set"></div>{_objective(input_id='repo-plan-objective')}<button type="submit">Save repository plan</button></form></article></section>'''
    else:
        shared = '<p class="scope-note">Shared plans are managed by your administrator. You can manage your own job plan within those limits.</p>'
    create = f'''<section class="panel create-job" aria-labelledby="create-job"><div><p class="eyebrow">Start a job</p><h2 id="create-job">Give the work a durable identity.</h2><p class="muted">Attach the same work ID to every model request in this job. A response, retry, or cache hit is activity; completion is recorded separately.</p></div><form class="work-form" method="post" action="/v1/work/jobs">{_hidden(csrf)}<div><label for="repository">Repository</label><input id="repository" name="repository" placeholder="organization/repository" maxlength="255" required></div><div><label for="title">Job name</label><input id="title" name="title" placeholder="Repair the failing integration check" maxlength="160"></div><div><label for="task-type">Work type</label><input id="task-type" name="task_type" value="general" placeholder="ci-repair" maxlength="100"></div><div><label for="context-revision">Context revision</label><input id="context-revision" name="context_revision" placeholder="Commit SHA or immutable revision" maxlength="255"></div><button type="submit">Create job →</button></form></section>'''
    if not principal.get("can_mutate_work", True):
        create = '<p class="scope-note">Your browser grant is read only. Use an authorized application identity to create and manage your own jobs.</p>'
        shared = '<p class="muted">Your administrator manages shared plans.</p>'
    jobs = "".join(_job(work, plans, csrf, can_manage=work.get("actor_id") == principal["actor_id"] and principal.get("can_mutate_work", True)) for work in works)
    if not jobs:
        jobs = '<div class="empty-state"><strong>No captured jobs yet.</strong><p>Create a job, connect a supported agent, and send its first authenticated request. No savings or outcome improvement is assumed before observations exist.</p></div>'
    plan_rows = "".join(f'<tr><th scope="row">{escape(str(p.get("scope_type", "")))}<code>{escape(str(p.get("scope_id", "")))}</code></th><td>{_money(p.get("budget_microusd"))}</td><td>{escape({"cost":"Cost first","speed":"Speed first","quality":"Outcome first"}.get(p.get("objective"), str(p.get("objective", ""))))}</td><td>{escape(str(p.get("version", "")))}</td></tr>' for p in plans)
    repository_editors = "".join(f'<article class="panel"><h3>{escape(p["scope_id"])}</h3>{_plan_form(csrf, "repository", p["scope_id"], plan=p, form_id="repository-"+str(i))}</article>' for i, p in enumerate(plans) if principal["can_manage_plans"] and p.get("scope_type") == "repository")
    plan_list = f'<details class="panel plan-list"><summary>Current plans ({len(plans)})</summary><div class="table-scroll"><table><thead><tr><th scope="col">Scope</th><th scope="col">Approved budget</th><th scope="col">Priority</th><th scope="col">Version</th></tr></thead><tbody>{plan_rows}</tbody></table></div></details>' if plans else ''
    job_label = "Workspace jobs" if state.get("scope") == "organization" else "Your jobs"
    return page("AI work", header + '<section class="overview">' + metrics + '</section>' + _connect(state) + shared + create + f'<section aria-labelledby="your-jobs"><div class="section-heading"><h2 id="your-jobs">{job_label}</h2><p class="muted">Full episodes · costs and time remain separate</p></div>{summary_note}<div class="job-list">{jobs}</div></section>' + plans_note + plan_list + ('<details class="panel"><summary>Edit repository plans</summary><div class="two-column">'+repository_editors+'</div></details>' if repository_editors else '') + '''<aside class="scope-note"><strong>What these measurements cover</strong><p>Only authenticated, work-tagged requests through this gateway. Token prices are configured estimates, not reconciled provider invoices. Outside subscriptions, untagged calls, and external agent actions are outside this ledger. Missing usage remains reserved and missing outcomes remain unknown. Self-reported workflow signals are labeled by source.</p><p>Routing learns from observed local evidence. Cost first and speed first express your priorities; they do not guarantee a task will be correct or completed.</p></aside>''', **navigation)


def _connect(state):
    connection, billing = state["connection"], state["billing"]
    routes = connection.get("routes", [])
    rows = "".join(f'<li><div><strong>{escape(route["model"])}</strong><small>{escape(route["protocol"])}</small></div><span class="status">{"Credential configured" if route["credential_configured"] else "Credential required"}</span></li>' for route in routes)
    if not rows:
        rows = '<li>No model routes are allowed for this identity. Ask your gateway administrator to enable a supported route.</li>'
    access = "Application access enabled" if connection.get("application_access_enabled") else "Application access requires administrator enrollment"
    portal = f'<form method="post" action="/v1/work/billing/portal">{_hidden(state["principal"]["csrf_token"])}<button class="secondary" type="submit">Manage subscription and payment</button></form>' if billing.get("portal_available") and state["principal"]["can_manage_plans"] else ""
    return f'''<section class="two-column connection"><article class="panel"><p class="eyebrow">Connect your existing tools</p><h2>Supported API requests, one work ID.</h2><p class="status">{access}</p><p>Use your authorized Hormuz credential with the gateway address below. Provider keys stay on the server.</p><p><code>{escape(connection['endpoint'])}</code></p><ol><li>Create a job and copy its work ID.</li><li>Point an API-compatible agent at this gateway.</li><li>Attach <code>X-Hormuz-Work-Id</code> to its requests.</li></ol><p class="muted">OpenAI Responses / Chat Completions and Anthropic Messages. Native subscriptions and tools without configurable API access cannot be controlled through this endpoint.</p><details><summary>Python client walkthrough</summary><pre><code>from hormuz.work_client import WorkClient

client = WorkClient.from_environment()
job = client.create_job("organization/repository", title="Fix CI")
# Add the returned work_id as X-Hormuz-Work-Id
# to your supported agent's gateway requests.
print(job["work_id"])</code></pre><p class="muted">Set HORMUZ_GATEWAY_URL and HORMUZ_TOKEN locally. Obtain your authorized credential through the existing enrollment flow or server administrator.</p></details></article><article class="panel"><p class="eyebrow">Configured coverage</p><h2>Routes available to this identity</h2><ul class="route-list">{rows}</ul><p class="muted">Configured credentials do not establish provider availability. Requests still pass normal policy checks.</p><dl class="detail-list"><div><dt>Billing state</dt><dd>{escape(str(billing.get('status', 'unknown')).replace('_', ' '))}</dd></div><div><dt>Provider fees</dt><dd>{escape(str(billing.get('provider_fees', 'separate')).replace('_', ' '))}</dd></div><div><dt>Paid activation</dt><dd>{escape(str(billing.get('paid_activation', 'not_configured')).replace('_', ' '))}</dd></div></dl>{portal}</article></section>'''


def _job(work, plans, csrf, *, can_manage=True):
    work_id = str(work["work_id"])
    costs = work.get("costs", {})
    settled = _number(costs, "committed_microusd")
    pending = _number(costs, "pending_microusd") + _number(costs, "uncertain_microusd")
    plan = next((p for p in work.get("plans", plans) if p.get("scope_type") == "job" and p.get("scope_id") == work_id), None)
    hidden = _hidden(csrf)
    elapsed = costs.get("provider_latency_ms") if costs.get("timed_attempts") else None
    elapsed_text = "Unknown" if elapsed is None else f'{float(elapsed) / 1000:,.2f}s'
    completion = work.get("completion_elapsed_ms")
    completion_text = "Unknown" if completion is None else f'{float(completion) / 1000:,.2f}s'
    observations = work.get("observations", [])
    events = "".join(f'<li><strong>{escape(str(o.get("status", "unknown")))}</strong> · {escape(str(o.get("source", "unknown")))}<small>{escape(str(o.get("reference") or "No reference supplied"))}</small></li>' for o in observations[-5:]) or '<li>No completion observation. Outcome remains unknown.</li>'
    attempts = work.get("attempts", [])
    attempt_rows = "".join(f'<tr><th scope="row">{escape(str(a.get("model", "unknown")))}<code>{escape(str(a.get("request_id", "")))}</code></th><td>{escape(str(a.get("state", "unknown")))}</td><td>{"Unknown" if a.get("cost_microusd") is None else _money(a["cost_microusd"])}</td><td>{"Unknown" if a.get("latency_ms") is None else format(float(a["latency_ms"])/1000,".2f")+"s"}</td><td>{escape(str(a.get("reason", "unknown")))}<small>{escape(str(a.get("repeat_signal", "none")))}</small></td></tr>' for a in attempts)
    attempt_table = f'<div class="table-scroll"><table><thead><tr><th scope="col">Model / request</th><th scope="col">State</th><th scope="col">Cost estimate</th><th scope="col">Latency</th><th scope="col">Route reason / recurrence</th></tr></thead><tbody>{attempt_rows}</tbody></table></div>' if attempts else '<p class="muted">No provider attempts captured.</p>'
    truncation = f'<p class="muted">Showing the most recent {int(work.get("attempts_limit", len(attempts)))} attempts; totals include all captured attempts.</p>' if work.get("attempts_truncated") else ''
    if can_manage:
        truncation += f'<p><a href="/work/jobs/{escape(work_id)}">Open job details →</a></p>'
    if work.get("observations_truncated"):
        events += '<li class="muted">Additional observations exist outside this summary.</li>'
    controls = ''
    if can_manage:
        actions = "".join(f'<form method="post" action="/v1/work/jobs/{escape(work_id)}/actions">{hidden}<input type="hidden" name="action" value="{action}"><button class="secondary" type="submit">{label}</button></form>' for action, label in (("pause", "Pause"), ("resume", "Resume")))
        version = int(plan.get("version", 0)) if plan else 0
        controls = f'''<details><summary>Job budget and continuation</summary>{_plan_form(csrf, 'job', work_id, plan=plan, label='Save job plan', form_id=work_id)}<div class="job-actions">{actions}<details class="stop-job"><summary>Stop this job</summary><p class="muted">Stop future admitted requests for this job. Already dispatched provider work cannot be canceled by this control.</p><form method="post" action="/v1/work/jobs/{escape(work_id)}/actions">{hidden}<input type="hidden" name="action" value="stop"><button class="danger" type="submit">Stop job</button></form></details></div><form class="work-form continuation" method="post" action="/v1/work/jobs/{escape(work_id)}/actions">{hidden}<input type="hidden" name="action" value="approve_budget"><input type="hidden" name="expected_version" value="{version}"><div><label for="{work_id}-continuation">New total job allowance (USD)</label><input id="{work_id}-continuation" name="budget_usd" type="number" min="0" step="0.000001" required><p class="muted">Approve an explicit total. This does not increase workspace or repository limits.</p></div><button type="submit">Approve allowance</button></form></details>'''
        options = "".join(f'<option value="{s}">{label}</option>' for s, label in (("unknown", "Unknown"), ("completed", "Specified work completed"), ("corrected", "Corrective request"), ("reopened", "Work reopened"), ("canceled", "Work canceled")))
        controls += f'''<details><summary>Add an optional operator observation</summary><form class="work-form" method="post" action="/v1/work/jobs/{escape(work_id)}/observations">{hidden}<input type="hidden" name="source" value="operator"><div><label for="{work_id}-status">Observation</label><select id="{work_id}-status" name="status">{options}</select></div><div><label for="{work_id}-reference">Reference identifier (optional)</label><input id="{work_id}-reference" name="reference" maxlength="255" placeholder="ci/run-12"></div><button class="secondary" type="submit">Record observation</button></form><p class="muted">Automated agents can submit sourced workflow signals through the API. These observations do not establish independent correctness.</p></details>'''
    else:
        controls = '<p class="muted">Your current grant does not allow continuation or observations for this job.</p>'
    pause = f'<p class="notice">Paused: {escape(str(work["pause_reason"]).replace("_", " "))}</p>' if work.get("pause_reason") else ''
    priority = {'cost': 'Cost first', 'speed': 'Speed first', 'quality': 'Outcome first'}.get(work.get('objective'), 'Default')
    return f'''<article class="panel job-card"><div class="section-heading"><div><p class="eyebrow">{escape(str(work['repository']))}</p><h3>{escape(str(work.get('title') or work_id))}</h3></div><span class="status">{escape(str(work.get('state', 'unknown')).replace('_', ' '))}</span></div>{pause}<p class="work-id"><span>Work ID</span><code>{escape(work_id)}</code></p><dl class="job-facts"><div><dt>Settled estimate</dt><dd>{_money(settled)}</dd></div><div><dt>Reserved / unsettled</dt><dd>{_money(pending)}</dd></div><div><dt>Sum of observed attempt time</dt><dd>{elapsed_text}</dd></div><div><dt>Observed completion elapsed</dt><dd>{completion_text}</dd></div><div><dt>Priority</dt><dd>{escape(priority)}</dd></div><div><dt>Cache hits / recurrences</dt><dd>{int(costs.get('cache_hits', 0))} / {int(costs.get('repeated_requests', 0))}</dd></div></dl><details><summary>Attempts and route decisions ({int(costs.get('attempts', 0))})</summary>{attempt_table}{truncation}<p class="muted">Recurrence is a possible repeat signal, not a quality judgment. Cache hits never establish completion.</p></details><details><summary>Outcome evidence</summary><ul class="observations">{events}</ul></details>{controls}</article>'''


def job_detail(work, *, csrf, can_manage, **navigation):
    heading = '<div class="section-heading"><h1>Job details</h1><a class="button secondary" href="/work">All captured work →</a></div>'
    return page("Job details", heading + _job(work, work.get("plans", []), csrf, can_manage=can_manage), **navigation)
