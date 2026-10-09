"""Escaped, script-free AI work experience; every number comes from runtime state."""

from html import escape
from decimal import Decimal


def page(title, body, *, workspace_enabled=True, console_enabled=True):
    navigation = ('<a href="/workspace">Workspace</a>' if workspace_enabled else '') + ('<a href="/console">Administration</a>' if console_enabled else '')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · Hormuz</title><link rel="stylesheet" href="/work.css"></head><body><a class="skip-link" href="#content">Skip to content</a><header class="site-header"><a class="brand" href="/work"><span class="brand-mark" aria-hidden="true">H</span>HORMUZ</a><nav aria-label="Workspace"><a href="/work" aria-current="page">AI work</a>{navigation}</nav></header><main id="content">{body}</main><footer><span>Hormuz · AI work</span><span>Covered requests only. Provider fees are separate.</span></footer></body></html>'''


def login(*, workspace_enabled=True, console_enabled=True, message=""):
    target = "/workspace" if workspace_enabled else "/console" if console_enabled else None
    action = f'<a class="button" href="{target}">Sign in to your workspace →</a>' if target else '<p>Browser sign-in is not configured. Ask your gateway administrator for authorized application access.</p>'
    notice = f'<p class="notice" role="status">{escape(message)}</p>' if message else ''
    return page("AI work", f'''<section class="login-panel"><p class="eyebrow">Your AI work, under control</p><h1>A budget.<br>A priority.<br>A clear record.</h1><p class="lead">Connect supported API agents, attach their requests to a job, and account for every captured attempt.</p>{notice}{action}<p class="muted">A configured gateway identity can also use the authenticated work API. No provider keys are entered on this page.</p></section>''', workspace_enabled=workspace_enabled, console_enabled=console_enabled)


def error_message(reason):
    specific = {"work_read_only": "Your grant is read only. Use an authorized application or ask your administrator to change access.", "plan_conflict": "This budget changed since the page loaded. Reload it, review the current allowance, then save again.", "budget_exhausted": "A job or parent budget has reached its limit. Review the reserved amounts and approve capacity explicitly before continuing.", "activation_qualification_required": "The connection must be qualified before paid checkout can begin.", "activation_operator_required": "Only the configured gateway operator can record this qualification or recovery decision.", "activation_review_conflict": "Qualification changed while this page was open. Reload the recorded state before reviewing again.", "activation_configuration_changed": "The route or enrollment configuration changed. Ask the operator to qualify the current configuration.", "billing_existing_subscription": "This workspace already has a subscription. Use its management portal or operator recovery flow.", "billing_api_unavailable": "The billing service is unavailable. Your work records are preserved; ask the operator to restore the configured service.", "billing_reverification_retry": "Payment could not be reverified. Preserve the receipt and retry after the operator checks the billing service.", "binding_connector_not_qualified": "This workflow is not associated with an allowed configured signed connector. Review the available choices with the operator.", "binding_association_conflict": "This external object is already associated with another work identity. Review that association before continuing.", "binding_completion_condition_conflict": "The workflow condition must match the condition declared for this job.", "binding_invalid_object": "Enter the exact pull request or check numeric identifier, or the Linear issue UUID.", "acquisition_consent_required": "Campaign attribution requires explicit consent. You can continue to AI work without it.", "invalid_campaign_labels": "Use only bounded source, medium, campaign, and content labels. Private work identifiers are not accepted."}
    if reason in specific:
        return specific[reason]
    return {"work_session_required": "Sign in to open your AI work.", "work_administrator_required": "Your verified identity needs administrator access to change shared budgets.", "work_origin_rejected": "Submit this form from the same Hormuz address.", "workspace_csrf_rejected": "The form expired or could not be verified. Reload your work page and try again.", "admin_csrf_rejected": "The form expired or could not be verified. Reload your work page and try again.", "work_not_enabled": "AI work is not enabled on this gateway.", "work_not_found": "This job is not available to your account.", "work_budget_exceeded": "The approved budget cannot cover this request. Review the job and its parent budgets.", "work_invalid_budget": "Enter a non-negative USD amount with at most six decimal places.", "work_storage_unavailable": "Work storage is temporarily unavailable.", "work_invalid_observation_source": "Use workflow, agent, or operator as the observation source."}.get(reason, "The request could not be completed. Reload the work page and check the submitted values.")


def failure(message, **navigation):
    return page("Review your request", f'''<section class="login-panel"><h1>Review your request</h1><p class="lead">{escape(message)}</p><a class="button" href="/work">Return to AI work →</a></section>''', **navigation)


def _money(value):
    if value is None:
        return "No limit set"
    amount = Decimal(int(value)) / Decimal(1_000_000)
    return "$" + (f"{amount:.6f}".rstrip("0") if 0 < amount < Decimal("0.01") else f"{amount:,.2f}")


def _confirmed(costs):
    value = costs.get("provider_confirmed_cost_microusd")
    return "Unknown" if value is None or costs.get("provider_confirmed_attempts") == 0 else _money(value)


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


def _completion_options(value="workflow.completed.v1"):
    conditions = (("workflow.completed.v1", "Workflow runner records completion"), ("github.pull_request.merged.v1", "GitHub pull request is merged"), ("github.check.passed.v1", "GitHub check passes"), ("linear.issue.completed.v1", "Linear issue is completed"))
    return ''.join(f'<option value="{key}"{" selected" if key == value else ""}>{label}</option>' for key, label in conditions)


def acquisition(principal, labels, **navigation):
    allowed = ("utm_source", "utm_medium", "utm_campaign", "utm_content")
    selected = {key: str(labels[key]) for key in allowed if labels.get(key)}
    rows = ''.join(f'<div><dt>{escape(key.removeprefix("utm_").replace("_", " "))}</dt><dd>{escape(value)}</dd></div>' for key, value in selected.items())
    fields = ''.join(f'<input type="hidden" name="{key}" value="{escape(value)}">' for key, value in selected.items())
    csrf = principal.csrf if hasattr(principal, "csrf") else principal.get("csrf_token", "")
    body = f'''<section class="login-panel acquisition-panel"><p class="eyebrow">Optional source attribution</p><h1>Connect the source.<br>Keep work private.</h1><p class="lead">With your consent, connect these bounded campaign labels to this workspace’s qualification and activation record.</p><dl class="detail-list">{rows or '<div><dt>Source</dt><dd>No campaign labels supplied</dd></div>'}</dl><p class="muted">Only these labels and an opaque reference are recorded. Credentials, repositories, task descriptions, and private work identifiers are excluded from public analytics. This consent does not enable website analytics or marketing emails.</p><form class="work-form" method="post" action="/v1/work/acquisition">{_hidden(csrf)}{fields}<label class="consent-line"><input type="checkbox" name="analytics_consent" value="true" required> I agree to connect these campaign labels to my private workspace progress.</label><button type="submit">Confirm source and open AI work →</button></form><a class="page-link" href="/work">Continue without source attribution →</a></section>'''
    return page("Confirm campaign source", body, **navigation)


def _plan_form(csrf, scope_type, scope_id, *, plan=None, label="Save plan", form_id="plan", routing=None):
    plan = plan or {}
    budget = plan.get("budget_microusd")
    version = f'<input type="hidden" name="expected_version" value="{int(plan.get("version", 0))}">'
    amount = "" if budget is None else f"{Decimal(int(budget)) / Decimal(1_000_000):.6f}".rstrip("0").rstrip(".")
    exploration = _exploration_control(plan, routing or {}, form_id)
    return f'''<form class="work-form" method="post" action="/v1/work/policies">{_hidden(csrf)}<input type="hidden" name="scope_type" value="{escape(scope_type)}"><input type="hidden" name="scope_id" value="{escape(scope_id)}">{version}<div><label for="{form_id}-budget">Approved budget (USD)</label><input id="{form_id}-budget" name="budget_usd" type="number" min="0" step="0.000001" value="{amount}" placeholder="No limit set" aria-describedby="{form_id}-help"><p id="{form_id}-help" class="muted">Blank removes this scope’s limit. Zero blocks paid calls. Parent limits still apply.</p></div>{_objective(plan.get('objective', 'cost'), input_id=form_id+'-objective')}{exploration}<button type="submit">{escape(label)}</button></form>'''


def _duration(value):
    return "Unknown" if value is None else f"{float(value) / 1000:,.3f}s"


def _explanation(value):
    return escape(str(value or "Unknown").replace("_", " "))


def _exploration_control(plan, routing, form_id):
    if "exploration_enabled" not in plan and not routing:
        return ""
    enabled = plan.get("exploration_enabled")
    aliases = ", ".join(str(alias) for alias in routing.get("exploration_aliases", [])) or "Only operator-approved aliases"
    allowance = routing.get("exploration_max_cost_microusd")
    risk = f"Configured monthly exploration allowance: {_money(allowance)}. " if allowance is not None else ""
    inherited = "On" if routing.get("exploration_enabled") else "Off"
    return f'''<div class="exploration-control"><label for="{form_id}-exploration">Explore approved alternatives</label><select id="{form_id}-exploration" name="exploration_enabled" aria-describedby="{form_id}-exploration-help"><option value="inherit"{" selected" if enabled is None else ""}>Inherit the gateway default · {inherited}</option><option value="false"{" selected" if enabled is False else ""}>Off · use existing evidence</option><option value="true"{" selected" if enabled is True else ""}>On · permit bounded exploration</option></select><p id="{form_id}-exploration-help" class="muted">Exploration may incur provider charges and a different outcome. {escape(risk)}All approved scope limits still apply. Candidates: {escape(aliases)}.</p></div>'''


def _onboarding(state):
    onboarding = state.get("onboarding", {})
    connection = state.get("connection", {})
    principal = state["principal"]
    csrf = principal["csrf_token"]
    activation = onboarding.get("activation", {})
    if isinstance(activation, str):
        activation = {"state": activation}
    phase = activation.get("state", onboarding.get("state", onboarding.get("activation_state", "not_requested")))
    verified = onboarding.get("identity_verified", bool(principal.get("actor_id")))
    access = onboarding.get("application_access_enabled", connection.get("application_access_enabled", False))
    qualified = phase in {"qualified", "checkout_pending", "payment_unverified", "active"} or (phase == "not_configured" and access and any(route.get("eligible") for route in connection.get("routes", [])))
    plans = state.get("plans", [])
    planned = any(plan.get("scope_type") == "workspace" for plan in plans)
    captured = int(state.get("totals", {}).get("attempts", 0)) > 0
    steps = [("Identity", verified), ("Supported connection", qualified), ("Budget and priority", planned), ("First captured work", captured)]
    items = "".join(f'<li class="{"is-complete" if done else "is-pending"}"><span aria-hidden="true">{"✓" if done else str(index)}</span><strong>{label}</strong><small>{"Recorded" if done else "Next step required"}</small></li>' for index, (label, done) in enumerate(steps, 1))
    headings = {"not_requested": "Confirm the connection before your first task.", "qualification_required": "Confirm the connection before your first task.", "not_configured": "Connect your existing tools to a named job.", "requested": "Your connection is awaiting qualification.", "qualified": "Your connection is qualified. Review activation.", "rejected": "Review the connection requirements.", "checkout_pending": "Complete the verified checkout.", "payment_unverified": "Payment verification is pending.", "active": "Your qualified connection is active.", "recovery_required": "Restore the qualified connection before continuing."}
    action = ''
    routes = onboarding.get("choices", onboarding.get("routes", connection.get("routes", [])))
    if onboarding.get("can_request_qualification") and principal.get("can_mutate_work", True):
        choices = []
        for route in routes:
            if not route.get("eligible", route.get("credential_configured", False)):
                continue
            client = route.get("client", "claude-code" if route.get("protocol") == "anthropic" else "codex")
            label = "Anthropic API agent" if client == "claude-code" else "OpenAI API agent"
            choices.append(f'''<form class="work-form qualification-choice" method="post" action="/v1/work/activation/request">{_hidden(csrf)}<input type="hidden" name="model" value="{escape(str(route['model']))}"><input type="hidden" name="client" value="{escape(str(client))}"><strong>{escape(str(route['model']))}</strong><p class="muted">{label} · provider credential configured</p><button type="submit">Request qualification for {escape(str(route['model']))} →</button></form>''')
        if choices:
            action = '<div class="qualification-choices">' + ''.join(choices) + '</div>'
        else:
            action = '<p class="muted">Your operator must configure an allowed route before qualification can be requested.</p>'
    elif phase == "requested":
        action = '<p class="muted">Your operator reviews application enrollment, provider access, compatibility, billing, and recovery readiness. Refresh to see the recorded decision.</p>'
    elif phase == "rejected":
        action = f'<p class="notice">{_explanation(activation.get("reason", "Qualification has not been approved"))}</p>'
    elif phase in {"checkout_pending", "payment_unverified"}:
        action = '<p class="muted">A checkout redirect does not activate paid work. Signed payment evidence and the recorded activation conditions must pass.</p>'
    elif phase == "recovery_required":
        action = '<p class="muted">Preserve your work records. Download a support receipt and ask the operator to requalify the restored deployment. Unresolved reservations remain held.</p>'
    else:
        action = '<p class="muted">Confirm your configured route and application enrollment, set your boundaries, then attach an agent to a named job.</p>'
    if onboarding.get("can_checkout"):
        action += f'<form method="post" action="/v1/work/billing/checkout">{_hidden(csrf)}<button type="submit">Continue to Stripe checkout →</button><p class="muted">Review the subscription and cancellation terms. Billing starts at completed checkout. Provider fees are separate.</p></form>'
    if (qualified and phase == "active") or state.get("billing", {}).get("status") == "self_hosted":
        label = 'Start another job' if state.get('works') else 'Create your first job'
        action += f'<a class="button" href="#create-work">{label} →</a>'
    if onboarding.get("can_review_qualification"):
        action += f'''<details class="qualification-review"><summary>Operator qualification review</summary><p class="muted">Record a decision only against verified client, provider, payment, and recovery evidence for this organization.</p><form class="work-form" method="post" action="/v1/work/activation/review">{_hidden(csrf)}<div><label for="qualification-reference">Qualification evidence reference</label><input id="qualification-reference" name="reference" maxlength="255" required placeholder="qualification/run-42"></div><div><label for="qualification-action">Decision</label><select id="qualification-action" name="action"><option value="approve">Approve the verified connection</option><option value="reject">Reject qualification</option></select></div><button class="secondary" type="submit">Record qualification decision</button></form></details>'''
    if onboarding.get("can_reverify"):
        action += f'''<form method="post" action="/v1/work/activation/reverify">{_hidden(csrf)}<button class="secondary" type="submit">Reverify bound subscription evidence</button><p class="muted">Operator action reads current configured Stripe evidence. No browser-entered payment identifier or amount is accepted.</p></form>'''
    next_step = onboarding.get('next_step')
    if captured and next_step == 'create_first_job':
        next_step = 'inspect_captured_work'
        action = '<p class="muted">Your connection and selected boundaries are recorded. Inspect captured work, completion evidence, and receipts, or start another job.</p><a class="button" href="#your-jobs">Inspect captured jobs →</a>' + action
    reason = f'<p class="muted">Next requirement: {_explanation(next_step)}</p>' if next_step else ''
    return f'''<section class="panel onboarding" aria-labelledby="onboarding-title"><div class="section-heading"><div><p class="eyebrow">Connection → boundaries → work → receipt</p><h2 id="onboarding-title">{escape(headings.get(phase, "Complete your connection requirements."))}</h2></div><span class="status">{_explanation(phase)}</span></div><ol class="setup-progress">{items}</ol><div class="onboarding-action">{reason}<p class="muted">Identity verified: {"Yes" if verified else "Pending"} · Application access: {"Enabled" if access else "Requires enrollment"}</p>{action}</div></section>'''


def _forecast(plan):
    forecast = plan.get("forecast", {})
    point = forecast.get("point_microusd", plan.get("forecast_microusd"))
    basis = forecast.get("basis", plan.get("forecast_basis", "unavailable"))
    lower, upper = forecast.get("lower_microusd"), forecast.get("upper_microusd")
    interval = f'{_money(lower)}–{_money(upper)}' if lower is not None and upper is not None else "Interval unavailable"
    uncertainty = forecast.get("uncertainty", [])
    reasons = ", ".join(str(reason).replace("_", " ") for reason in uncertainty) or ("No uncertainty detail supplied" if not forecast else "Declared evidence conditions apply")
    return f'''<div class="forecast"><strong>{"Unavailable" if point is None else _money(point)}</strong><span>{escape(interval)}</span><small>{_explanation(basis)} · {escape(str(forecast.get('observed_days', 'Unknown')))} observed days</small><small>Coverage: {_explanation(forecast.get('coverage', 'gateway_captured_work_requests_only'))}</small><small>{escape(reasons)}. {"Excludes unsettled holds." if forecast.get('excludes_holds', False) else "Review pending and uncertain amounts separately."}</small></div>'''


def _plan_evidence(plans):
    cards = []
    for plan in plans:
        scope = str(plan.get("scope_type", ""))
        cards.append(f'''<article class="panel plan-evidence"><p class="eyebrow">{escape(scope)} · {"Job lifetime" if scope == "job" else "Current UTC month"}</p><h3>{escape(str(plan.get('scope_id', '')))}</h3><dl class="detail-list"><div><dt>Approved limit</dt><dd>{_money(plan.get('budget_microusd'))}</dd></div><div><dt>Available after holds</dt><dd>{_money(plan.get('remaining_microusd'))}</dd></div><div><dt>Settled estimate</dt><dd>{_money(plan.get('estimated_cost_microusd', plan.get('committed_microusd')))}</dd></div><div><dt>Provider-confirmed cost</dt><dd>{_confirmed(plan)}</dd></div></dl><p class="muted">Unconfirmed attempts: {escape(str(plan.get('unconfirmed_attempts', 'Unknown')))}. Confirmed amounts cover their imported subset; invoice finality: {'Recorded by source' if plan.get('invoice_finality') else 'Not established'}.</p><h4>{"Estimated job cost · lifetime" if scope == "job" else "Estimated month-end cost"}</h4>{_forecast(plan)}</article>''')
    return '<section class="forecast-section" aria-labelledby="forecast-title"><div class="section-heading"><h2 id="forecast-title">Forecasts and available capacity</h2><p class="muted">Estimates, coverage, and uncertainty stay visible</p></div><div class="forecast-grid">' + ''.join(cards) + '</div></section>' if cards else '<section class="panel forecast-section"><h2>Forecasts need captured work.</h2><p class="muted">Set a scope plan and run attributed work. Unknown completion and charge evidence is preserved; no forecast is assumed before observations exist.</p></section>'


def _routing_summary(state):
    routing = state.get("routing", {})
    if not routing:
        return ''
    aliases = ', '.join(str(alias) for alias in routing.get('exploration_aliases', [])) or 'None configured'
    return f'''<details class="panel routing-summary"><summary>Routing evidence and exploration boundaries</summary><dl class="detail-list"><div><dt>Minimum comparable observations</dt><dd>{escape(str(routing.get('minimum_samples', 'Unknown')))}</dd></div><div><dt>Configured exploration</dt><dd>{"Configured default On · scope can override" if routing.get('exploration_enabled') else "Off"}</dd></div><div><dt>Approved candidates</dt><dd>{escape(aliases)}</dd></div><div><dt>Maximum exploration rate</dt><dd>{escape(str(routing.get('exploration_rate_percent', 'Unknown')))}%</dd></div><div><dt>Monthly exploration allowance</dt><dd>{"Unknown" if routing.get('exploration_max_cost_microusd') is None else _money(routing['exploration_max_cost_microusd'])}</dd></div></dl><p class="muted">Evidence basis: {_explanation(routing.get('evidence_basis'))}. Observations are local to their declared repository, work characteristics, request shape, protocol, context, and route configuration. A small sample or missing result keeps the approved baseline; it does not establish model superiority.</p></details>'''


def _provider_accounts(state):
    evidence = state.get("provider_account_costs")
    if not evidence:
        return ''
    rows = []
    for account in evidence.get("accounts", []):
        amount = account.get("provider_reported_amount")
        provenance = account.get("selected_snapshot_provenance", [])
        sources = ', '.join(str(item.get('evidence_origin', 'Unknown')) for item in provenance) or 'No selected snapshot'
        rows.append(f'''<tr><th scope="row">{escape(str(account.get('provider', 'Unknown')))}<code>{escape(str(account.get('account_binding_id', 'Unknown')))}</code></th><td>{"Unknown" if amount is None else escape(str(amount)) + " USD"}<small>{_explanation(account.get('cost_basis'))}</small></td><td>{_explanation(account.get('status'))}<small>{escape(str(account.get('observed_bucket_count', 'Unknown')))} observed · {escape(str(account.get('missing_bucket_count', 'Unknown')))} missing buckets</small></td><td>{escape(sources)}<small>Audit: {escape(str(account.get('query_audit_event_id') or 'Unavailable'))}</small><small>Invoice finality: {"Recorded by source" if account.get('invoice_finality') else "Not established"}</small></td></tr>''')
    table = '<div class="table-scroll"><table><thead><tr><th scope="col">Provider account</th><th scope="col">Provider-reported amount</th><th scope="col">Coverage</th><th scope="col">Provenance</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>' if rows else f'<p class="muted">{_explanation(evidence.get("reason", "unavailable"))}. No amount is assumed.</p>'
    return f'''<details class="panel provider-account-evidence"><summary>Provider account evidence · {_explanation(evidence.get('status'))}</summary><p class="muted">{escape(str(evidence.get('period_start_at') or 'Period unavailable'))} → {escape(str(evidence.get('period_end_at') or 'Period unavailable'))}. Account-level provider observations are separate from estimated captured work. They are not allocated to individual jobs or requests, and a missing amount stays unknown.</p>{table}<p class="muted">This view reads existing authorized account-bound evidence. It does not collect provider credentials, import a browser-entered charge, or establish a reconciled invoice.</p></details>'''


def _funnel(state):
    funnel = state.get('funnel', {})
    if not funnel.get('consent'):
        return ''
    labels = {'qualified_connection': 'Qualified connection recorded', 'operator_qualified': 'Operator qualification recorded', 'first_attributed_work': 'First attributed work captured', 'receipt_opened': 'Job receipt opened', 'checkout_payment_received': 'Checkout payment event received', 'payment_verified': 'Current payment verified', 'paid_activation': 'Paid work currently active', 'repeat_work': 'Another attributed job captured'}
    rows = ''.join(f'<div><dt>{label}</dt><dd>{int(funnel.get("events", {}).get(key, 0))}</dd></div>' for key, label in labels.items() if key in funnel.get('events', {}))
    return f'''<details class="panel private-progress"><summary>Your consented source and private progress</summary><p class="muted">Recorded from authenticated work and durable qualification or payment evidence. Opening a public page does not claim a paid conversion.</p><dl class="detail-list">{rows or '<div><dt>Progress</dt><dd>No qualifying event recorded yet</dd></div>'}</dl><p class="muted">These records stay private to this verified account. Task content and private job identifiers are excluded from public website analytics.</p></details>'''


def _support(state):
    onboarding = state.get("onboarding", {})
    support = state.get("support", {"receipt_available": True, "recovery_state": onboarding.get("state", "not_configured")})
    receipt = '<a class="button secondary" href="/v1/work/support/receipt">Download support receipt →</a>' if support.get("receipt_available") else ''
    recovery = ''
    if onboarding.get("can_reset"):
        recovery = f'''<details class="recovery-control"><summary>Operator recovery checkpoint</summary><p class="muted">This blocks new paid admissions, invalidates pending checkout and payment qualification, and preserves work and subscription history. It does not cancel the Stripe subscription. Requalify the restored deployment and current payment evidence before resuming.</p><form class="work-form" method="post" action="/v1/work/activation/reset">{_hidden(state['principal']['csrf_token'])}<div><label for="recovery-reference">Recovery evidence reference</label><input id="recovery-reference" name="reference" maxlength="255" required placeholder="recovery/backup-42"></div><button class="danger" type="submit">Record recovery checkpoint</button></form></details>'''
    return f'''<section class="panel support-panel"><p class="eyebrow">Keep work recoverable</p><h2>Support evidence without conversation content.</h2><p class="muted">Recovery state: {_explanation(support.get('recovery_state', 'Unknown'))}. Preserve work IDs, unsettled holds, policy versions, and payment history when investigating an incident.</p>{receipt}<p class="muted">The authenticated receipt includes bounded metadata and evidence sources. Credentials, prompts, response bodies, and repository contents are excluded.</p>{recovery}</section>'''


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
    header = f'''<div class="workspace-bar"><div><p class="eyebrow">AI work</p><h1>Set the boundaries.<br>Keep work moving.</h1><p class="lead">Your captured jobs, their cost and time evidence, and the priorities you choose.</p></div><div class="account"><div><strong>{escape(principal['organization_id'])}</strong><small>Signed-in account · {escape(principal['actor_id'])}</small></div><a class="button secondary" href="/work">Refresh</a></div></div>{notice}'''
    confirmed = _confirmed(totals)
    metrics = f'''<section class="metrics work-metrics" aria-label="Captured work totals"><article class="metric"><h2>Settled estimate</h2><p class="metric-value">{_money(cost)}</p><p>Configured provider rates · captured attempts</p></article><article class="metric"><h2>Provider-confirmed attempt subset</h2><p class="metric-value">{confirmed}</p><p>{escape(str(totals.get('unconfirmed_attempts', 'Unknown')))} captured attempts still unconfirmed · invoice finality {'recorded by source' if totals.get('invoice_finality') else 'not established'}</p></article><article class="metric"><h2>Reserved for in-flight work</h2><p class="metric-value">{_money(pending)}</p><p>Reservations remain until settlement</p></article><article class="metric"><h2>Provider attempts</h2><p class="metric-value">{int(attempts):,}</p><p>Retries and fallbacks are attempts</p></article><article class="metric"><h2>Observed gateway overhead</h2><p class="metric-value">{_duration(totals.get('gateway_overhead_ms'))}</p><p>Captured request handling · measured coverage</p></article><article class="metric"><h2>Jobs observed</h2><p class="metric-value">{int(jobs_total):,}</p><p>Missing completion evidence stays unknown</p></article></section>'''
    workspace_plan = next((p for p in plans if p.get("scope_type") == "workspace"), None)
    if principal["can_manage_plans"]:
        shared = f'''<section class="two-column" id="scope-plans"><article class="panel"><p class="eyebrow">Workspace plan</p><h2>One shared budget and default priority</h2><p class="muted">Workspace and repository budgets apply to the UTC month. Job budgets apply over the job’s lifetime.</p>{_plan_form(csrf, 'workspace', principal['organization_id'], plan=workspace_plan, form_id='workspace-plan', routing=state.get("routing"))}</article><article class="panel"><p class="eyebrow">Repository plan</p><h2>Set a repository boundary</h2><form class="work-form" method="post" action="/v1/work/policies">{_hidden(csrf)}<input type="hidden" name="scope_type" value="repository"><input type="hidden" name="expected_version" value="0"><div><label for="repo-plan-id">Repository</label><input id="repo-plan-id" name="scope_id" placeholder="organization/repository" maxlength="255" required></div><div><label for="repo-plan-budget">Monthly approved budget (USD)</label><input id="repo-plan-budget" name="budget_usd" type="number" min="0" step="0.000001" placeholder="No limit set"></div>{_objective(input_id='repo-plan-objective')}{_exploration_control({}, state.get('routing', {}), 'repo-plan')}<button type="submit">Save repository plan</button></form></article></section>'''
    else:
        shared = '<p class="scope-note">Shared plans are managed by your administrator. You can manage your own job plan within those limits.</p>'
    completion_field = f'<div class="full-row"><label for="completion-condition">Workflow completion condition</label><select id="completion-condition" name="completion_condition">{_completion_options()}</select><p class="muted">Bind an existing workflow condition. Its sourced runner or signed connector records the result; a response or cache hit does not complete the job.</p></div>'
    create = f'''<section class="panel create-job" id="create-work" aria-labelledby="create-job"><div><p class="eyebrow">Start a job</p><h2 id="create-job">Give the work a durable identity.</h2><p class="muted">Attach the same work ID to every model request in this job. A response, retry, or cache hit is activity; completion is recorded separately.</p></div><form class="work-form" method="post" action="/v1/work/jobs">{_hidden(csrf)}<div><label for="repository">Repository</label><input id="repository" name="repository" placeholder="organization/repository" maxlength="255" required></div><div><label for="title">Job name</label><input id="title" name="title" placeholder="Repair the failing integration check" maxlength="160"></div><div><label for="task-type">Work type</label><input id="task-type" name="task_type" value="general" placeholder="ci-repair" maxlength="100"></div><div><label for="context-revision">Context revision</label><input id="context-revision" name="context_revision" placeholder="Commit SHA or immutable revision" maxlength="255"></div>{completion_field}<button type="submit">Create job →</button></form></section>'''
    if not principal.get("can_mutate_work", True):
        create = '<p class="scope-note">Your browser grant is read only. Use an authorized application identity to create and manage your own jobs.</p>'
        shared = '<p class="muted">Your administrator manages shared plans.</p>'
    jobs = "".join(_job(work, plans, csrf, can_manage=work.get("actor_id") == principal["actor_id"] and principal.get("can_mutate_work", True), routing=state.get("routing")) for work in works)
    if not jobs:
        jobs = '<div class="empty-state"><strong>No captured jobs yet.</strong><p>Create a job, connect a supported agent, and send its first authenticated request. No savings or outcome improvement is assumed before observations exist.</p></div>'
    plan_rows = "".join(f'<tr><th scope="row">{escape(str(p.get("scope_type", "")))}<code>{escape(str(p.get("scope_id", "")))}</code></th><td>{_money(p.get("budget_microusd"))}</td><td>{escape({"cost":"Cost first","speed":"Speed first","quality":"Outcome first"}.get(p.get("objective"), str(p.get("objective", ""))))}</td><td>{escape(str(p.get("version", "")))}</td></tr>' for p in plans)
    repository_editors = "".join(f'<article class="panel"><h3>{escape(p["scope_id"])}</h3>{_plan_form(csrf, "repository", p["scope_id"], plan=p, form_id="repository-"+str(i), routing=state.get("routing"))}</article>' for i, p in enumerate(plans) if principal["can_manage_plans"] and p.get("scope_type") == "repository")
    plan_list = f'<details class="panel plan-list"><summary>Current plans ({len(plans)})</summary><div class="table-scroll"><table><thead><tr><th scope="col">Scope</th><th scope="col">Approved budget</th><th scope="col">Priority</th><th scope="col">Version</th></tr></thead><tbody>{plan_rows}</tbody></table></div></details>' if plans else ''
    job_label = "Workspace jobs" if state.get("scope") == "organization" else "Your jobs"
    return page("AI work", header + _onboarding(state) + '<section class="overview">' + metrics + '</section>' + _plan_evidence(plans) + _routing_summary(state) + _provider_accounts(state) + _funnel(state) + _connect(state) + shared + create + f'<section aria-labelledby="your-jobs"><div class="section-heading"><h2 id="your-jobs">{job_label}</h2><p class="muted">Full episodes · costs and time remain separate</p></div>{summary_note}<div class="job-list">{jobs}</div></section>' + plans_note + plan_list + ('<details class="panel"><summary>Edit repository plans</summary><div class="two-column">'+repository_editors+'</div></details>' if repository_editors else '') + _support(state) + '''<aside class="scope-note"><strong>What these measurements cover</strong><p>Only authenticated, work-tagged requests through this gateway. Token prices are configured estimates, not reconciled provider invoices. Outside subscriptions, untagged calls, and external agent actions are outside this ledger. Missing usage remains reserved and missing outcomes remain unknown. Self-reported workflow signals are labeled by source.</p><p>Routing learns from observed local evidence. Cost first and speed first express your priorities; they do not guarantee a task will be correct or completed.</p></aside>''', **navigation)


def _connect(state):
    connection, billing = state["connection"], state["billing"]
    routes = connection.get("routes", [])
    rows = "".join(f'<li><div><strong>{escape(route["model"])}</strong><small>{escape(route["protocol"])}</small></div><span class="status">{"Credential configured" if route["credential_configured"] else "Credential required"}</span></li>' for route in routes)
    if not rows:
        rows = '<li>No model routes are allowed for this identity. Ask your gateway administrator to enable a supported route.</li>'
    access = "Application access enabled" if connection.get("application_access_enabled") else "Application access requires administrator enrollment"
    portal = f'<form method="post" action="/v1/work/billing/portal">{_hidden(state["principal"]["csrf_token"])}<button class="secondary" type="submit">Manage subscription and payment</button></form>' if billing.get("portal_available") and state["principal"]["can_manage_plans"] else ""
    return f'''<section class="two-column connection"><article class="panel"><p class="eyebrow">Connect your existing tools</p><h2>Supported API requests, one work ID.</h2><p class="status">{access}</p><p>Use your authorized Hormuz credential with the gateway address below. Provider keys stay on the server.</p><p><code>{escape(connection['endpoint'])}</code></p><ol><li>Create a job and copy its work ID.</li><li>Point an API-compatible agent at this gateway.</li><li>Attach <code>X-Hormuz-Work-Id</code> to its requests.</li></ol><p class="muted">Budgeted input coverage: {escape(", ".join(connection.get("supported_inputs", ["text"])))}. Image, audio, document, and provider-resolved inputs are rejected when a validated upper cost bound is unavailable.</p><p class="muted">OpenAI Responses / Chat Completions and Anthropic Messages. Native subscriptions and tools without configurable API access cannot be controlled through this endpoint.</p><details><summary>Python client walkthrough</summary><pre><code>from hormuz.work_client import WorkClient

client = WorkClient.from_environment()
job = client.create_job("organization/repository", title="Fix CI")
# Add the returned work_id as X-Hormuz-Work-Id
# to your supported agent's gateway requests.
print(job["work_id"])</code></pre><p class="muted">Set HORMUZ_GATEWAY_URL and HORMUZ_TOKEN locally. Obtain your authorized credential through the existing enrollment flow or server administrator.</p></details></article><article class="panel"><p class="eyebrow">Configured coverage</p><h2>Routes available to this identity</h2><ul class="route-list">{rows}</ul><p class="muted">Configured credentials do not establish provider availability. Requests still pass normal policy checks.</p><dl class="detail-list"><div><dt>Billing state</dt><dd>{escape(str(billing.get('status', 'unknown')).replace('_', ' '))}</dd></div><div><dt>Provider fees</dt><dd>{escape(str(billing.get('provider_fees', 'separate')).replace('_', ' '))}</dd></div><div><dt>Paid activation</dt><dd>{escape(str(billing.get('paid_activation', 'not_configured')).replace('_', ' '))}</dd></div></dl>{portal}</article></section>'''


def _attempts(attempts):
    rows = []
    for attempt in attempts:
        confirmed = attempt.get("confirmed_cost_microusd")
        charge = "Unknown" if attempt.get("cost_microusd") is None else _money(attempt["cost_microusd"])
        confirmation = "Unknown" if confirmed is None else _money(confirmed)
        source = _explanation(attempt.get("cost_confirmation_source", "No confirmation"))
        reuse = "Exact cache hit" if attempt.get("state") == "cache_hit" else _explanation(attempt.get("cache_bypass_reason", "No bypass recorded"))
        rows.append(f'''<tr><th scope="row">{escape(str(attempt.get('model', 'unknown')))}<code>{escape(str(attempt.get('request_id', '')))}</code></th><td>{_explanation(attempt.get('state'))}</td><td>{charge}<small>Provider-confirmed: {confirmation}</small><small>{source}</small></td><td>{_duration(attempt.get('latency_ms'))}<small>Gateway wall: {_duration(attempt.get('gateway_wall_ms'))}</small><small>Overhead: {_duration(attempt.get('gateway_overhead_ms'))}</small></td><td>{_explanation(attempt.get('reason'))}<small>Recurrence: {_explanation(attempt.get('repeat_signal', 'none'))}</small><small>Cache: {reuse}</small></td></tr>''')
    return '<div class="table-scroll"><table><thead><tr><th scope="col">Model / request</th><th scope="col">State</th><th scope="col">Cost evidence</th><th scope="col">Observed provider time</th><th scope="col">Decision and reuse reason</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>' if rows else '<p class="muted">No provider attempts captured.</p>'


def _local_evidence(work):
    evidence = work.get("routing") or work.get("routing_evidence") or {}
    scope = evidence.get("evidence_scope", evidence.get("scope", {}))
    candidates = evidence.get("candidates", [])
    rows = ''.join(f'<tr><th scope="row">{escape(str(candidate.get("model", "Unknown")))}</th><td>{escape(str(candidate.get("samples", "Unknown")))}<small>{escape(str(candidate.get("completed", "Unknown")))} completed · {escape(str(candidate.get("rework", "Unknown")))} rework</small></td><td>{"Unknown" if candidate.get("mean_cost_microusd") is None else _money(candidate["mean_cost_microusd"])}</td><td>{_duration(candidate.get("mean_provider_ms"))}<small>Gateway: {_duration(candidate.get("mean_gateway_ms"))}</small><small>Completion: {_duration(candidate.get("mean_completion_ms"))}</small></td><td>{_explanation(candidate.get("exclusion_reason", "Observed" if candidate.get("eligible") else "Eligibility unavailable"))}</td></tr>' for candidate in candidates)
    table = f'<div class="table-scroll"><table><thead><tr><th scope="col">Model</th><th scope="col">Comparable job signals</th><th scope="col">Mean estimated work cost</th><th scope="col">Mean observed provider time</th><th scope="col">Eligibility</th></tr></thead><tbody>{rows}</tbody></table></div>' if rows else '<p class="muted">No comparable candidate evidence is available in this record. Routing retains the approved baseline until the required evidence exists.</p>'

    confidence = evidence.get('sample_confidence', evidence.get('confidence', 'Unavailable'))
    return f'''<details class="local-evidence"><summary>Evidence confidence and comparison scope</summary><p class="status">{_explanation(confidence)}</p><p class="muted">Comparable samples: {escape(str(evidence.get('sample_count', 'Unknown')))}. Repository: {escape(str(scope.get('repository', work.get('repository', 'Unknown'))))}. Work type: {escape(str(scope.get('task_type', work.get('task_type', 'Unknown'))))}. Task origin: {_explanation(work.get('task_origin', 'unknown'))}. Context: {escape(str(scope.get('context_signature') or work.get('context_signature') or work.get('context_revision') or 'Unknown'))}.</p><p class="muted">Request kind: {_explanation(scope.get('request_kind'))}. Observation window: {escape(str(scope.get('window_days', 'Unknown')))} days. Decision: {_explanation(evidence.get('reason'))}.</p>{table}<p class="muted">Completion and rework signals establish their recorded workflow conditions. Unknown outcomes, incompatible request shapes, context changes, and excluded repetitions do not become positive quality evidence.</p></details>'''


def _job(work, plans, csrf, *, can_manage=True, routing=None):
    work_id = str(work["work_id"])
    costs = work.get("costs", {})
    settled = _number(costs, "committed_microusd")
    pending = _number(costs, "pending_microusd") + _number(costs, "uncertain_microusd")
    plan = next((p for p in work.get("plans", plans) if p.get("scope_type") == "job" and p.get("scope_id") == work_id), None)
    hidden = _hidden(csrf)
    elapsed = costs.get("summed_provider_ms", costs.get("provider_latency_ms")) if costs.get("timed_attempts") else None
    elapsed_text = "Unknown" if elapsed is None else f'{float(elapsed) / 1000:,.2f}s'
    completion = work.get("completion_elapsed_ms")
    completion_text = "Unknown" if completion is None else f'{float(completion) / 1000:,.2f}s'
    observations = work.get("observations", [])
    events = "".join(f'<li><strong>{escape(str(o.get("status", "unknown")))}</strong> · {escape(str(o.get("source", "unknown")))}<small>{escape(str(o.get("reference") or "No reference supplied"))}</small></li>' for o in observations[-5:]) or '<li>No completion observation. Outcome remains unknown.</li>'
    attempts = work.get("attempts", [])
    attempt_table = _attempts(attempts)
    truncation = f'<p class="muted">Showing the most recent {int(work.get("attempts_limit", len(attempts)))} attempts; totals include all captured attempts.</p>' if work.get("attempts_truncated") else ''
    if can_manage:
        truncation += f'<p><a href="/work/jobs/{escape(work_id)}">Open job details →</a></p>'
    if work.get("observations_truncated"):
        events += '<li class="muted">Additional observations exist outside this summary.</li>'
    controls = ''
    if can_manage:
        actions = "".join(f'<form method="post" action="/v1/work/jobs/{escape(work_id)}/actions">{hidden}<input type="hidden" name="action" value="{action}"><button class="secondary" type="submit">{label}</button></form>' for action, label in (("pause", "Pause"), ("resume", "Resume")))
        version = int(plan.get("version", 0)) if plan else 0
        controls = f'''<details><summary>Job budget and continuation</summary>{_plan_form(csrf, 'job', work_id, plan=plan, label='Save job plan', form_id=work_id, routing=routing)}<div class="job-actions">{actions}<details class="stop-job"><summary>Stop this job</summary><p class="muted">Stop future admitted requests for this job. Already dispatched provider work cannot be canceled by this control.</p><form method="post" action="/v1/work/jobs/{escape(work_id)}/actions">{hidden}<input type="hidden" name="action" value="stop"><button class="danger" type="submit">Stop job</button></form></details></div><form class="work-form continuation" method="post" action="/v1/work/jobs/{escape(work_id)}/actions">{hidden}<input type="hidden" name="action" value="approve_budget"><input type="hidden" name="expected_version" value="{version}"><div><label for="{work_id}-continuation">New total job allowance (USD)</label><input id="{work_id}-continuation" name="budget_usd" type="number" min="0" step="0.000001" required><p class="muted">Approve an explicit total. This does not increase workspace or repository limits.</p></div><button type="submit">Approve allowance</button></form></details>'''
        options = "".join(f'<option value="{s}">{label}</option>' for s, label in (("unknown", "Unknown"), ("completed", "Specified work completed"), ("corrected", "Corrective request"), ("reopened", "Work reopened"), ("canceled", "Work canceled")))
        controls += f'''<details><summary>Add an optional operator observation</summary><form class="work-form" method="post" action="/v1/work/jobs/{escape(work_id)}/observations">{hidden}<input type="hidden" name="source" value="operator"><div><label for="{work_id}-status">Observation</label><select id="{work_id}-status" name="status">{options}</select></div><div><label for="{work_id}-reference">Reference identifier (optional)</label><input id="{work_id}-reference" name="reference" maxlength="255" placeholder="ci/run-12"></div><button class="secondary" type="submit">Record observation</button></form><p class="muted">Automated agents can submit sourced workflow signals through the API. These observations do not establish independent correctness.</p></details>'''
        bindings = work.get('bindings', [])
        if bindings:
            controls += '<details><summary>Recorded workflow associations</summary><ul class="observations">' + ''.join(f'<li><strong>{escape(str(binding.get("provider", "Unknown")))}</strong><span>{escape(str(binding.get("connector_id", "Unknown")))}</span><code>{escape(str(binding.get("completion_condition", "Unknown")))}</code></li>' for binding in bindings) + '</ul></details>'
        if work.get('binding_available'):
            forms = []
            for index, connector in enumerate(work.get('connector_choices', [])):
                provider = str(connector['provider'])
                connector_id = str(connector['connector_id'])
                containers = ''.join(f'<option value="{escape(str(container))}">{escape(str(container))}</option>' for container in connector.get('container_ids', []))
                form_id = work_id + '-binding-' + str(index)
                forms.append(f'''<form class="work-form workflow-binding" method="post" action="/v1/work/jobs/{escape(work_id)}/bindings">{hidden}<input type="hidden" name="provider" value="{escape(provider)}"><input type="hidden" name="connector_id" value="{escape(connector_id)}"><input type="hidden" name="completion_condition" value="{escape(str(work['completion_condition']))}"><strong class="full-row">{escape(provider)} · {escape(connector_id)}</strong><div><label for="{form_id}-container">Configured repository or workspace</label><select id="{form_id}-container" name="container_id" required>{containers}</select></div><div><label for="{form_id}-object">{'Pull request or check numeric identifier' if provider == 'github' else 'Linear issue UUID'}</label><input id="{form_id}-object" name="object_id" maxlength="255" required placeholder="{'42' if provider == 'github' else 'Issue UUID'}"></div><button class="secondary" type="submit">Bind this workflow →</button></form>''')
            controls += '<details><summary>Bind an existing workflow for automatic outcomes</summary><p class="muted">Choose a configured signed channel for this job’s declared completion condition. Signed matching events supply outcome evidence after this association is recorded. Live connector delivery still requires qualification.</p>' + ''.join(forms) + '<p class="muted">These identifiers remain private. This form configures no provider account or webhook secret.</p></details>'

    else:
        controls = '<p class="muted">Your current grant does not allow continuation or observations for this job.</p>'
    pause = f'<p class="notice">Paused: {escape(str(work["pause_reason"]).replace("_", " "))}</p>' if work.get("pause_reason") else ''
    priority = {'cost': 'Cost first', 'speed': 'Speed first', 'quality': 'Outcome first'}.get(work.get('objective'), 'Default')
    confirmation = _confirmed(costs)
    condition = escape(str(work.get('completion_condition') or 'No completion condition supplied'))
    gateway_wall = _duration(costs.get('gateway_wall_ms'))
    overhead = _duration(costs.get('gateway_overhead_ms'))
    timing = _explanation(costs.get('timing_coverage', 'Provider attempt timing only'))
    receipt = f'<a class="text-link" href="/v1/work/jobs/{escape(work_id)}">Open job receipt (JSON) →</a>' if can_manage else ''
    agent = _agent_integration(work) if can_manage else ''

    return f'''<article class="panel job-card"><div class="section-heading"><div><p class="eyebrow">{escape(str(work['repository']))}</p><h3>{escape(str(work.get('title') or work_id))}</h3></div><span class="status">{escape(str(work.get('state', 'unknown')).replace('_', ' '))}</span></div>{pause}<p class="work-id"><span>Work ID</span><code>{escape(work_id)}</code></p><p class="completion-condition"><strong>Completion condition</strong>{condition}</p><dl class="job-facts"><div><dt>Settled estimate</dt><dd>{_money(settled)}</dd></div><div><dt>Provider-confirmed cost</dt><dd>{confirmation}</dd></div><div><dt>Reserved / unsettled</dt><dd>{_money(pending)}</dd></div><div><dt>Sum of observed attempt time</dt><dd>{elapsed_text}</dd></div><div><dt>Gateway wall time</dt><dd>{gateway_wall}</dd></div><div><dt>Gateway overhead</dt><dd>{overhead}</dd></div><div><dt>Observed completion elapsed</dt><dd>{completion_text}</dd></div><div><dt>Priority</dt><dd>{escape(priority)}</dd></div><div><dt>Cache hits / recurrences</dt><dd>{int(costs.get('cache_hits', 0))} / {int(costs.get('repeated_requests', 0))}</dd></div></dl><p class="muted">Cost basis: {_explanation(costs.get('cost_basis'))}. Unconfirmed attempts: {escape(str(costs.get('unconfirmed_attempts', 'Unknown')))}. Confirmed amounts cover only their imported attempt subset; invoice finality: {'Recorded by source' if costs.get('invoice_finality') else 'Not established'}.</p><p class="muted">Timing coverage: {timing}. Provider, gateway, and whole-job completion time are different measurements.</p>{receipt}{agent}<details><summary>Attempts and route decisions ({int(costs.get('attempts', 0))})</summary>{attempt_table}{truncation}<p class="muted">Recurrence is a possible repeat signal, not a quality judgment. Cache hits never establish completion. Repeated corrective work can bypass an earlier answer.</p></details>{_local_evidence(work)}<details><summary>Outcome evidence</summary><ul class="observations">{events}</ul></details>{controls}</article>'''


def _agent_integration(work):
    work_id = str(work["work_id"])
    configuration = 'hormuz client config codex --auth-mode session --url "$HORMUZ_GATEWAY_URL" --model YOUR_APPROVED_ALIAS --work-id ' + work_id
    launch = 'hormuz context run --profile YOUR_MANAGED_PROFILE --work-id ' + work_id
    if work.get('completion_condition') == 'workflow.completed.v1':
        check = 'hormuz work check --work-id ' + work_id + ' --reference ci/run-42 --completes-work -- python -m unittest YOUR_CHECK'
        outcome = '<p class="muted">Run the ordinary task in that agent. The declared workflow runner can record its actual check result:</p><pre><code>' + escape(check) + '</code></pre>'
    else:
        outcome = '<p class="muted">Run the ordinary task in that agent. Completion requires the declared signed workflow condition. Bind its configured connector below; a local check or model response does not establish that condition.</p>'
    return '<details><summary>Connect an ordinary agent to this job</summary><p class="muted">Use your enrolled session and an approved model alias. This binds requests and retries to the work ID; it does not run a task by itself.</p><pre><code>' + escape(configuration) + '</code></pre><p class="muted">For an existing managed profile, launch its agent with the same job identity:</p><pre><code>' + escape(launch) + '</code></pre>' + outcome + '</details>'


def job_detail(work, *, csrf, can_manage, **navigation):
    heading = '<div class="section-heading"><h1>Job details</h1><a class="button secondary" href="/work">All captured work →</a></div>'
    return page("Job details", heading + _job(work, work.get("plans", []), csrf, can_manage=can_manage), **navigation)
