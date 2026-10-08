"""Escaped, script-free customer workspace pages."""

from html import escape


def page(title, body):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · Hormuz</title><link rel="stylesheet" href="/workspace/styles.css"></head><body><a class="skip-link" href="#content">Skip to content</a><header class="site-header"><a class="brand" href="/workspace">Hormuz</a><span>Your workspace</span></header><main id="content">{body}</main><footer><span>Hormuz</span><span>Your default address stays available when you connect a custom domain.</span></footer></body></html>'''


def login(*, message="", action="/v1/workspaces/auth/start"):
    notice = f'<p class="notice" role="status">{escape(message)}</p>' if message else ""
    return page("Sign in", f'''<section class="login-panel">{notice}<p class="eyebrow">Welcome to Hormuz</p><h1>Your workspace,<br>ready after sign-in.</h1><p class="lead">Sign in to open your workspace. Your Hormuz address is included. You can connect your own domain later.</p><form class="login-form" method="post" action="{escape(action)}"><input type="hidden" name="intent" value="signin"><button type="submit">Sign in to Hormuz <span aria-hidden="true">→</span></button></form></section>''')


def continue_login(url):
    return page("Continue sign-in", f'''<section class="login-panel"><h1>Continue securely</h1><p class="lead">Use your account to create or reopen your workspace.</p><a class="button" href="{escape(url)}" rel="noreferrer">Continue to sign-in →</a></section>''')


def handoff(origin, token):
    return page("Open your workspace", f'''<section class="login-panel"><h1>You’re signed in.</h1><p class="lead">Continue to your workspace at <strong>{escape(origin)}</strong>.</p><form method="post" action="{escape(origin)}/v1/workspaces/auth/handoff"><input type="hidden" name="token" value="{escape(token)}"><button type="submit">Open workspace →</button></form></section>''')


def failure(message):
    return page("Try again", f'''<section class="login-panel"><h1>We couldn’t complete that.</h1><p class="lead">{escape(message)}</p><a class="button" href="/workspace">Return to your workspace</a></section>''')


def dashboard(current, canonical_origin, domains, csrf, *, domains_enabled, message=""):
    slug = current["slug"]
    address = canonical_origin + "/w/" + slug
    hidden = f'<input type="hidden" name="csrf_token" value="{escape(csrf)}">'
    notice = f'<p class="notice" role="status">{escape(message)}</p>' if message else ""
    records = ""
    reasons = {"ownership_pending": "Waiting for the ownership TXT record.", "routing_pending": "Waiting for the CNAME record.", "https_pending": "Waiting for HTTPS and dashboard routing.", "provider_unavailable": "Verification is temporarily unavailable. We’ll retry automatically."}
    for domain in domains:
        dns = "".join(f'<div><dt>{escape(record["type"])}</dt><dd><span>Name</span><code>{escape(record["name"])}</code></dd><dd><span>Value</span><code>{escape(record["value"])}</code></dd></div>' for record in domain["dns_records"])
        domain_input = f'<input type="hidden" name="domain_id" value="{escape(domain["id"])}">'
        link = f'<p><a href="{escape(domain["url"])}">Open dashboard on this domain →</a></p>' if domain["url"] else ""
        records += f'''<article class="panel workspace-domain"><div class="section-heading"><h3>{escape(domain["hostname"])}</h3><span class="status">{escape(domain["status"])}</span></div><p>{escape(reasons.get(domain["last_error"], "The dashboard is available on this domain." if domain["status"] == "active" else "Add these records at your DNS provider."))}</p>{link}<h4>Required DNS records</h4><dl class="domain-records">{dns}</dl><p class="muted">Keep both records in place. DNS updates may take time.</p><form method="post" action="/v1/workspaces/domains/check">{hidden}{domain_input}<button type="submit">Check connection</button></form><details class="remove-control"><summary>Remove domain</summary><p>Your default Hormuz address will keep working.</p><form method="post" action="/v1/workspaces/domains/remove">{hidden}{domain_input}<button class="danger" type="submit">Remove this domain</button></form></details></article>'''
    can_manage = current["membership_id"] == current["owner_membership_id"] and current["role"] == "member_admin"
    connect = (f'''<form class="login-form" method="post" action="/v1/workspaces/domains"><label for="hostname">Your subdomain</label><input id="hostname" name="hostname" placeholder="ai.yourcompany.com" autocomplete="off" aria-describedby="domain-help" required maxlength="253"><p class="muted" id="domain-help">Use a subdomain that you control. Your dashboard will be served at this address after verification.</p>{hidden}<button type="submit">Connect domain</button></form>''' if domains_enabled and can_manage else '<p class="muted">Custom domain connections are not enabled yet.</p>' if not domains_enabled else '<p class="muted">Your workspace owner can manage domains.</p>')
    return page("Your workspace", f'''<div class="workspace-bar"><div><p class="eyebrow">Workspace</p><h1>{escape(current["workspace_name"])}</h1></div><form method="post" action="/v1/workspaces/logout">{hidden}<button class="secondary" type="submit">Sign out</button></form></div>{notice}<section class="panel"><p class="eyebrow">Your address</p><h2>Your dashboard is ready.</h2><p><a href="{escape(address)}"><code>{escape(address)}</code></a></p><p class="muted">Bookmark this address. A custom domain is optional.</p></section><section class="overview"><h2>Workspace setup</h2><p>Your account and workspace are ready. AI traffic will be available after provider connections and application access are enabled.</p><div class="two-column"><div class="panel"><h3>Provider connection</h3><span class="status">Not connected</span><p>Your workspace has no provider credentials.</p></div><div class="panel"><h3>Application access</h3><span class="status">Not enabled</span><p>Application credentials and the inference endpoint will appear here when available.</p></div></div></section><section aria-labelledby="domains"><h2 id="domains">Custom domains <span class="status">Optional</span></h2><p>Open the same workspace on an address your customers recognize.</p><div class="panel">{connect}</div>{records}</section>''')
