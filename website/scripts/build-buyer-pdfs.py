"""Generate the three source-linked buyer briefs; render and inspect before release."""
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

OUTPUT = Path(__file__).resolve().parents[1] / "public/downloads"
OUTPUT.mkdir(parents=True, exist_ok=True)
INK = colors.HexColor("#101b20")
TEAL = colors.HexColor("#087f78")
PAPER = colors.HexColor("#f4f4ef")
LINE = colors.HexColor("#d9e1df")
URL = "https://xpounder-com.github.io/hormuz/"
REPO = "https://github.com/Xpounder-com/hormuz/blob/main/"
styles = {
    "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=TEAL, spaceAfter=9),
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=27, leading=30, textColor=INK, spaceAfter=13),
    "deck": ParagraphStyle("deck", fontName="Helvetica", fontSize=12, leading=17, textColor=INK, spaceAfter=14),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=INK, spaceBefore=13, spaceAfter=6),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10.2, leading=14.5, textColor=INK, spaceAfter=8, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.8, leading=12, textColor=INK, spaceAfter=6),
    "table": ParagraphStyle("table", fontName="Helvetica", fontSize=9.3, leading=13, textColor=INK),
}


def p(text, kind="body"):
    return Paragraph(text, styles[kind])


def link(label, path):
    return f'<link href="{REPO}{path}" color="#087f78">{label}</link>'


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(44, 46, 568, 46)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(TEAL)
    canvas.drawString(44, 32, "HORMUZ  /  Mehrdad Zaker  /  August 30, 2026")
    canvas.drawRightString(568, 32, f"{doc.page}")
    canvas.linkURL(URL, (44, 24, 440, 43), relative=0)
    canvas.restoreState()


def table(rows, widths):
    items = [[p(value, "table") for value in row] for row in rows]
    result = Table(items, colWidths=widths, hAlign="LEFT", repeatRows=1)
    result.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PAPER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), .6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), .4, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return result


def build(name, title, story):
    document = SimpleDocTemplate(str(OUTPUT / name), pagesize=letter, rightMargin=44, leftMargin=44, topMargin=40, bottomMargin=62, title=title, author="Mehrdad Zaker")
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    print(name)


overview = [
    p("OPEN SOURCE + SUPPORTED EVALUATION", "label"),
    p("Keep the coding clients.<br/>Govern their model requests.", "title"),
    p("Hormuz is a self-hosted, Apache-2.0 policy, usage, and evidence gateway for Codex and Claude Code.", "deck"),
    p("The problem", "h2"),
    p("AI access fragments across clients and provider accounts. Platform teams need an enforceable answer to who may use which model, under what budget and secret-egress policy, with what retained evidence."),
    p("The governed path", "h2"),
    p("Client → Hormuz identity, policy, secret and budget checks → allowed provider request. Company provider keys stay on the gateway. Routine ledgers retain metadata, not prompts or response bodies. Requests that bypass Hormuz are outside its coverage."),
    table([
        ["<b>Useful open core</b>", "<b>Proposed enterprise engagement</b>"],
        ["Gateway, OIDC JWT verification, policy overlays, budgets, deterministic secret controls, usage reports, and evidence exports.", "Scoped workflow mapping, configuration assistance, one non-production integration, acceptance evidence, gap review, and handoff."],
        ["Self-hosted; community support is best effort.", "Same open core. Scope, price, capacity, support hours, response targets, and terms agreed before work."],
    ], [262, 262]),
    p("Try it before a sales conversation", "h2"),
    p(f'The <link href="{URL}demo/" color="#087f78">real provider-free recording</link> demonstrates allow, fallback/cap, redact, deny with no upstream call, and synthetic metadata evidence. The complete quickstart needs no provider account.'),
    p("Know the boundary", "h2"),
    p("v1.0.0 stabilizes source CLI/policy/evidence contracts; the signed OCI reference is separately v0.1.3 linux/amd64. No blanket production certification, managed service, 24/7 SLA, invoice reconciliation, independent security review, or customer endorsement is claimed.", "small"),
    p('<b>Discuss one workflow:</b> Mehrdad Zaker · <link href="mailto:zaker.mehrdad@gmail.com" color="#087f78">zaker.mehrdad@gmail.com</link><br/>' + f'<link href="{URL}" color="#087f78">xpounder-com.github.io/hormuz</link>'),
    p("Sources: " + link("Architecture", "docs/ARCHITECTURE.md") + " · " + link("Clients", "docs/CLIENTS.md") + " · " + link("Support", "SUPPORT.md") + " · " + link("Offer", "marketing/OFFER.md"), "small"),
]

pilot = [
    p("PILOT DISCUSSION BRIEF / NOT AN AGREEMENT", "label"),
    p("Prove one workflow<br/>before widening the route.", "title"),
    p("A proposed 90-day, founder-led evaluation around the same Apache-2.0 core. Begin with fit and scope; a shorter evaluation may come first.", "deck"),
    p("Who this is for", "h2"),
    p("A platform or engineering lead adopting Codex or Claude Code under company provider accounts, with a named operator, policy owner, and security reviewer. This is the initial buyer hypothesis, not validated market demand."),
    p("Prerequisites", "h2"),
    p("One non-production workflow; authorized provider and identity accounts; unique identities; approved test inputs; a safe access method; agreed evidence/retention boundaries; and written acceptance criteria. Timing begins after scope, prerequisites, capacity, and terms are agreed."),
    table([
        ["<b>Phase</b>", "<b>Work</b>", "<b>Deliverable</b>"],
        ["Days 1–15<br/><b>Map</b>", "Identify client, identity, provider, policy, secret, budget and evidence boundaries.", "Control map and acceptance plan; stop or rescope if fit is poor."],
        ["Days 16–45<br/><b>Prove</b>", "Configure and exercise the bounded non-production route.", "Versioned evidence pack, reproducible walkthrough, and issue log."],
        ["Days 46–90<br/><b>Decide</b>", "Review friction, operating effort, agreed operational checks and remaining gates.", "Go/no-go memo, named gap owners, and handoff."],
    ], [95, 216, 213]),
    p("Agree the acceptance test", "h2"),
    p("The pinned client works; a forbidden request makes no upstream call; agreed model/secret/budget policies behave as expected; identity and policy are attributable; exported evidence excludes prompt/response bodies and credentials; the operator can reproduce the checks; remaining gaps have owners."),
    p("Latency, throughput, availability, RPO/RTO, billing accuracy, and business-value targets require separate agreement and measurement. Public demo timings and internal repeatability runs are not evidence for those goals.", "small"),
    PageBreak(),
    p("PILOT / RESPONSIBILITIES AND TERMS", "label"),
    p("Keep ownership explicit.", "title"),
    table([
        ["<b>Customer owns</b>", "<b>Assistance, only as scoped</b>"],
        ["Hosting, TLS/ingress, access, patching, backup, metadata retention.", "Configuration review and bounded integration help."],
        ["Provider charges and authorization; credential custody; JWT issuance/refresh; unique identities.", "Guidance without custody of customer secrets by default."],
        ["Policy decisions, acceptable use, and approved test data.", "Mapping requirements to implemented controls."],
        ["Evidence access, sharing approval, security risk acceptance and production qualification.", "Content-free test results, open-gap review and handoff."],
    ], [262, 262]),
    p("Not included or established", "h2"),
    p("Managed hosting; fleet-wide coverage; client-side shell/MCP governance; 24/7 on-call; certification; legal/compliance determinations; comprehensive semantic DLP; per-inference human approval; native Hormuz login/refresh sessions; invoice reconciliation; guaranteed savings; or future portfolio features."),
    p("Decide before work starts", "h2"),
    p("Price/payment; dates; time budget; meeting cadence; support hours/time zone and response targets; named contact; customer prerequisites; acceptance; change control; confidentiality; approved access/data handling; liability; termination; and post-pilot support. Obtain appropriate contract review. No default price or SLA is implied."),
    p("Protect sensitive information", "h2"),
    p("Do not send tokens, raw prompts, customer content, production databases, or full configurations through the marketing form or public issues. Private access requires an approved method, minimal privilege, and a written handling scope."),
    p("The next decision", "h2"),
    p("Stop and retain the lessons; continue self-service; resolve specific gaps in another bounded engagement; or propose a wider deployment after its gates are satisfied. Expansion is not automatic."),
    p('<b>Mehrdad Zaker</b> · <link href="mailto:zaker.mehrdad@gmail.com" color="#087f78">zaker.mehrdad@gmail.com</link>'),
    p("Sources: " + link("Full pilot scope", "marketing/PILOT.md") + " · " + link("Support", "SUPPORT.md") + " · " + link("Operations", "docs/OPERATIONS.md"), "small"),
]

trust = [
    p("ENGINEERING TRUST BRIEF / NOT CERTIFICATION", "label"),
    p("Keep the control record.<br/>Leave the conversation out.", "title"),
    p("A concise data-flow and responsibility summary for evaluating the v1 source contracts. Provider processing and customer-operated infrastructure remain part of the system.", "deck"),
    table([
        ["<b>Data</b>", "<b>Handling and responsibility</b>"],
        ["Prompts and responses", "Relayed transiently; excluded from routine usage/security ledgers. Allowed content reaches the provider under the customer’s agreement."],
        ["Employee credentials", "Authenticate to Hormuz; are not forwarded as provider credentials. Unique identities are required for useful attribution."],
        ["Provider credentials", "Remain server-side in the configured environment or custody system. Do not distribute company keys to employees."],
        ["Identity and usage metadata", "Bounded actor/team/model/policy/token/cost/status data. Sensitive organizational metadata, even without content."],
        ["Secret-control evidence", "Rule/action/outcome/count metadata; no matched secret value or raw request material."],
        ["Infrastructure logs/backups", "Operator-owned. Disable body logging; restrict access; define retention, deletion and backup protection."],
    ], [142, 382]),
    p("Coverage is the governed model-request path", "h2"),
    p("Client → Hormuz identity/policy/secret/budget checks → allowed provider call. A denial must not make that upstream call. Shell commands, MCP servers, browser/Git traffic and bypassed requests are outside the gateway’s coverage."),
    p("Sources: " + link("Architecture", "docs/ARCHITECTURE.md") + " · " + link("Audit", "docs/AUDIT.md") + " · " + link("Clients", "docs/CLIENTS.md"), "small"),
    PageBreak(),
    p("TRUST / CAPABILITIES, LIMITS, AND REVIEW", "label"),
    p("Review the gaps<br/>in your environment.", "title"),
    p("Implemented, with specific boundaries", "h2"),
    p("<b>Identity:</b> OIDC JWT verification, with issuer/audience/expiry/signature checks and explicit subject mapping. Issuance/refresh remain with identity tooling. No native Hormuz browser login, refresh custody or session-revocation endpoint."),
    p("<b>Secrets:</b> deterministic redact, deny or off modes—not complete semantic DLP. Custody-lifecycle approvals are separate from inference; no per-inference human-approval workflow is claimed."),
    p("<b>Usage:</b> captured gateway traffic in the current UTC month, with configured-rate-card cost estimates—not complete provider-account coverage or reconciled invoices."),
    p("<b>Maturity:</b> v1.0.0 source CLI/policy/evidence contracts; separate v0.1.3 linux/amd64 signed OCI reference. Reference evidence does not certify your deployment."),
    p("Questions to resolve before production traffic", "h2"),
    p("Who owns TLS/ingress and bypass controls? How are tokens issued, refreshed and revoked? Where are provider keys held? Who has administrative access? What are metadata/log/backup retention and deletion rules? Which migration, rollback, HA, capacity and recovery checks pass here? What remains for independent security review and provider/contract review?"),
    p("Inspect synthetic evidence", "h2"),
    p(f'The <link href="{URL}demo/#evidence" color="#087f78">demo evidence pack</link> contains four usage events and one secret-control event from a separate provider-free run. Product schema and forbidden-content checks passed before export. These are synthetic records, not a customer case study or human onboarding result.'),
    p("Report a vulnerability privately", "h2"),
    p("Follow " + link("SECURITY.md", "SECURITY.md") + ". Do not submit sensitive material through public issues or the marketing form. General evaluation contact: <b>Mehrdad Zaker</b>, zaker.mehrdad@gmail.com."),
    p("Sources: " + link("Full trust brief", "marketing/TRUST.md") + " · " + link("OIDC", "docs/OIDC.md") + " · " + link("Secret controls", "docs/SECRET_CONTROLS.md") + " · " + link("Usage", "docs/USAGE.md") + " · " + link("Operations", "docs/OPERATIONS.md"), "small"),
]

if __name__ == "__main__":
    build("hormuz-overview.pdf", "Hormuz — buyer overview", overview)
    build("hormuz-pilot-brief.pdf", "Hormuz — proposed evaluation pilot", pilot)
    build("hormuz-trust-brief.pdf", "Hormuz — trust and data-flow brief", trust)
