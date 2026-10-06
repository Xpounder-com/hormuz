"""Generate the three source-linked buyer briefs; render and inspect before release."""
from pathlib import Path
import json
import os
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

WEBSITE = Path(__file__).resolve().parents[1]
PRICING = json.loads((WEBSITE / "lib/pricing.json").read_text())
OUTPUT = Path(os.environ.get("PDF_OUTPUT_DIR", str(WEBSITE / "public/downloads")))

def money(key):
    amount = PRICING[key]["amount"]
    return f"${amount:,.2f}" if amount != int(amount) else f"${amount:,.0f}"
OUTPUT.mkdir(parents=True, exist_ok=True)
INK = colors.HexColor("#202723")
TEAL = colors.HexColor("#31684b")
PAPER = colors.HexColor("#f4f3ee")
LINE = colors.HexColor("#d4dbcc")
URL = "https://usehormuz.github.io/"
REPO = "https://github.com/Xpounder-com/hormuz/blob/main/"
styles = {
    "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=TEAL, spaceAfter=9),
    "title": ParagraphStyle("title", fontName="Times-Italic", fontSize=29, leading=31, textColor=INK, spaceAfter=13),
    "deck": ParagraphStyle("deck", fontName="Helvetica", fontSize=12, leading=17, textColor=INK, spaceAfter=14),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=INK, spaceBefore=10, spaceAfter=6),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14, textColor=INK, spaceAfter=7, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.8, leading=12, textColor=INK, spaceAfter=6),
    "table": ParagraphStyle("table", fontName="Helvetica", fontSize=9.3, leading=13, textColor=INK),
}


def p(text, kind="body"):
    return Paragraph(text, styles[kind])


def link(label, path):
    return f'<link href="{REPO}{path}" color="#31684b">{label}</link>'


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(PAPER)
    canvas.rect(0, 0, 612, 792, fill=1, stroke=0)
    # Use the approved export, preserving the same geometry as the website.
    canvas.setFillColor(TEAL)
    canvas.drawImage(str(WEBSITE / 'public/brand/icons/transparent/hormuz-transparent-512.png'), 40, 742, width=32, height=32, mask='auto')
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(77, 754, "H O R M U Z")
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(568, 755, "POLICY / BUDGETS / EVIDENCE")
    canvas.setStrokeColor(LINE)
    canvas.line(44, 734, 568, 734)
    canvas.setStrokeColor(LINE)
    canvas.line(44, 46, 568, 46)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(TEAL)
    canvas.linkURL("https://neuralint.io", (44, 29, 245, 41), relative=0)
    canvas.drawString(44, 32, "HORMUZ  /  A product of Neuralint  /  October 6, 2026")
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
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return result


def build(name, title, story):
    document = SimpleDocTemplate(str(OUTPUT / name), pagesize=letter, rightMargin=44, leftMargin=44, topMargin=80, bottomMargin=62, title=title, author="Mehrdad Zaker")
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    print(name)


overview = [
    p("SOFTWARE / CLOUD / APPLIANCES", "label"),
    p("Keep the coding clients.<br/>Govern their model requests.", "title"),
    p("Hormuz is an Apache-2.0 policy, usage, and evidence gateway for supported coding-client workflows.", "deck"),
    p("The governed path", "h2"),
    p("Client requests pass through identity, policy, secret and budget checks before allowed provider egress. Company provider keys stay on the gateway. Routine ledgers retain metadata rather than prompts or responses. Bypassed traffic is outside coverage."),
    p("The complete price catalog", "h2"),
    table([
        ["<b>Offer</b>", "<b>Price in USD</b>"],
        ["Software", money("software")],
        ["Cloud", money("cloud") + " / workspace / month"],
        ["Appliance", money("appliance") + " / appliance, one time"],
        ["Appliance with scoped onboarding", money("onboarding") + " / appliance, one time"],
        ["Managed site", money("managedSite") + " / site / month"],
    ], [284, 240]),
    p(f'Onboarding includes up to {PRICING["onboarding"]["remoteHours"]} hours of remote setup for one provider and one supported application. A managed site includes its Cloud workspace and {PRICING["managedSite"]["supportHoursPerMonth"]} hour of remote assistance per billing month, with a response within {PRICING["managedSite"]["responseBusinessDays"]} business days.'),
    p("Availability and responsibilities", "h2"),
    p(f'Appliances: <b>coming soon, planned rollout early 2027</b> (not a guaranteed shipping date). <b>Reserve yours: {money("reservation")} one-time refundable deposit</b>, credited toward purchase. Cancel anytime before fulfillment for a full refund; email the owner with your Stripe receipt. All payments use Stripe. Delivery and qualification are confirmed before the balance. Cloud and managed services accept inquiries. Provider usage, infrastructure, and taxes are separate.', "small"),
    p('<b>Discuss your workflow:</b> Mehrdad Zaker · <link href="mailto:mehrdadz@neuralint.io" color="#31684b">mehrdadz@neuralint.io</link><br/>' + f'<link href="{URL}plans/" color="#31684b">usehormuz.github.io/plans</link>'),
    p("Sources: " + link("Architecture", "docs/ARCHITECTURE.md") + " · " + link("Offer", "marketing/OFFER.md") + " · " + link("Scope and support terms", "marketing/COMMERCIAL_SETUP.md"), "small"),
]

appliance = [
    p("APPLIANCE / SCOPED ONBOARDING", "label"),
    p("Your network. Your appliance.<br/>A bounded setup.", "title"),
    p(f'{money("appliance")} per appliance, or {money("onboarding")} per appliance with scoped onboarding. Both prices are USD and one time.', "deck"),
    p("Hardware included", "h2"),
    p(f'Enclosure, cooling, power supply, installed gateway, and setup guidance. <b>Coming soon: planned rollout early 2027.</b> Reserve with a fully refundable {money("reservation")} deposit, credited toward purchase. Cancel anytime before fulfillment for a full refund; email the owner with your Stripe receipt. All payments use Stripe. The date is a target; delivery requires hardware qualification and confirmed arrangements.'),
    p("The onboarding bundle", "h2"),
    p(f'Includes the appliance and up to {PRICING["onboarding"]["remoteHours"]} hours of remote onboarding for one provider and one supported application. It replaces the standalone appliance purchase for that unit. Calls, configuration, checks, and handoff count against this allowance.'),
    table([
        ["<b>Step</b>", "<b>Included work</b>"],
        ["Prepare", "Name the operator; check ready network, power, authorized provider account, approved test inputs, and acceptance scope."],
        ["Connect", "Connect one provider and one supported application; configure initial identity, model, secret, and budget policies."],
        ["Verify and hand over", "Run an allowed and denied request; inspect attributable metadata; hand over operator instructions and open issues."],
    ], [130, 394]),
    p("Customer responsibilities", "h2"),
    p("Supply the network, power, authorized accounts, unique identities, approved policy/test data, and a named operator. Do not send credentials or customer prompts through public forms or issues. Private access requires an approved method and minimal privileges."),
    p("Scope limits", "h2"),
    p("Custom integrations, network redesign, data migration, on-site visits, and ongoing operations are excluded. Pause for missing prerequisites and record unfinished work when the allowance is exhausted. No extra charge is authorized by this scope."),
    PageBreak(),
    p("MANAGED SITE / SUPPORT ALLOWANCE", "label"),
    p("Defined help for one site.", "title"),
    p(f'{money("managedSite")} USD per site per month, including the associated Cloud workspace, device-health monitoring, qualified updates, recovery guidance, and {PRICING["managedSite"]["supportHoursPerMonth"]} hour of remote assistance per billing month.', "deck"),
    p("Site and allowance", "h2"),
    p("A site is one agreed physical location or isolated customer deployment recorded at activation. Additional appliances at that site do not create another site subscription; operating capacity must still be confirmed. Calls, investigation, troubleshooting, and configuration work count toward the allowance. Unused time does not roll over."),
    p("Business hours and response", "h2"),
    p(f'Support hours are Monday-Friday, 9 am-5 pm America/Chicago, excluding local public holidays. Response target: within {PRICING["managedSite"]["responseBusinessDays"]} business days, meaning acknowledgment and a next step. Resolution time depends on the issue.'),
    p("Included and excluded work", "h2"),
    p("Included: configuration and policy guidance, troubleshooting, recovery guidance, and qualified update assistance. Excluded: custom development, on-site work, round-the-clock response, replacement hardware, and work beyond the allowance. Appliance hardware, model-provider usage, customer infrastructure, and applicable taxes are separate."),
    p("Cloud and renewal", "h2"),
    p(f'Standalone Cloud is {money("cloud")} USD per workspace per month. A managed site has no additional subscription charge for its included workspace. Software is {money("software")}. Cloud and managed sites renew monthly until canceled; email the owner before the next renewal with your subscription reference. The current paid period, appliance ownership, and free software remain available.'),
    p("Activation follows qualification", "h2"),
    p("Confirm the covered site, appliances, supported workflow, delivery capacity, operating coverage, and start date before payment. Cloud activation and managed monitoring require validation. Pricing does not establish a production SLA, compliance certification, guaranteed savings, or customer demand."),
    p('<b>Mehrdad Zaker</b> · <link href="mailto:mehrdadz@neuralint.io" color="#31684b">mehrdadz@neuralint.io</link><br/>' + f'<link href="{URL}enterprise/" color="#31684b">usehormuz.github.io/enterprise</link>'),
    p("Sources: " + link("Onboarding scope", "marketing/PILOT.md") + " · " + link("Commercial terms", "marketing/COMMERCIAL_SETUP.md") + " · " + link("Operations", "docs/OPERATIONS.md"), "small"),
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
    p("Client to Hormuz identity/policy/secret/budget checks to allowed provider call. A denial must not make that upstream call. Shell commands, MCP servers, browser/Git traffic and bypassed requests are outside the gateway’s coverage."),
    p("Sources: " + link("Architecture", "docs/ARCHITECTURE.md") + " · " + link("Audit", "docs/AUDIT.md") + " · " + link("Clients", "docs/CLIENTS.md"), "small"),
    PageBreak(),
    p("TRUST / CAPABILITIES, LIMITS, AND REVIEW", "label"),
    p("Review the gaps<br/>in your environment.", "title"),
    p("Implemented, with specific boundaries", "h2"),
    p("<b>Identity:</b> OIDC JWT verification, with issuer/audience/expiry/signature checks and explicit subject mapping. The opt-in hosted evaluation path adds browser login and revocable Hormuz sessions; real-IdP and operating evidence remain deployment-specific."),
    p("<b>Secrets:</b> deterministic redact, deny or off modes—not complete semantic DLP. Custody-lifecycle approvals are separate from inference; no per-inference human-approval workflow is claimed."),
    p("<b>Usage:</b> captured gateway traffic in the current UTC month, with configured-rate-card cost estimates—not complete provider-account coverage or reconciled invoices."),
    p("<b>Maturity:</b> v1.3.0 source release and signed Apple Silicon companion. Personal Optimizer support is limited to macOS 14+ on Apple Silicon. The matching v1.3.0 linux/amd64 signed OCI reference does not claim optimizer support. Reference evidence does not certify your deployment."),
    p("Questions to resolve before production traffic", "h2"),
    p("Who owns TLS/ingress and bypass controls? How are tokens issued, refreshed and revoked? Where are provider keys held? Who has administrative access? What are metadata/log/backup retention and deletion rules? Which migration, rollback, HA, capacity and recovery checks pass here? What remains for independent security review and provider/contract review?"),
    p("Inspect synthetic evidence", "h2"),
    p(f'The <link href="{URL}demo/#evidence" color="#31684b">demo evidence pack</link> contains four usage events and one secret-control event from a separate provider-free run. Product schema and forbidden-content checks passed before export. These are synthetic records, not a customer case study or human onboarding result.'),
    p("Report a vulnerability privately", "h2"),
    p("Follow " + link("SECURITY.md", "SECURITY.md") + ". Do not submit sensitive material through public issues or the marketing form. General evaluation contact: <b>Mehrdad Zaker</b>, mehrdadz@neuralint.io."),
    p("Sources: " + link("Full trust brief", "marketing/TRUST.md") + " · " + link("OIDC", "docs/OIDC.md") + " · " + link("Secret controls", "docs/SECRET_CONTROLS.md") + " · " + link("Usage", "docs/USAGE.md") + " · " + link("Operations", "docs/OPERATIONS.md"), "small"),
]

if __name__ == "__main__":
    build("hormuz-overview.pdf", "Hormuz — buyer overview", overview)
    build("hormuz-appliance-brief.pdf", "Hormuz — appliance and scoped onboarding", appliance)
    # Keep the existing download URL synchronized with the revised hardware offer.
    (OUTPUT / "hormuz-pilot-brief.pdf").write_bytes(
        (OUTPUT / "hormuz-appliance-brief.pdf").read_bytes()
    )
    build("hormuz-trust-brief.pdf", "Hormuz — trust and data-flow brief", trust)
