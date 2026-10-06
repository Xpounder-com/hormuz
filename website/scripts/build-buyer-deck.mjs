import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { SOFTWARE_PRICE, CLOUD_PRICE, APPLIANCE_PRICE, ONBOARDING_PRICE, MANAGED_SITE_PRICE, RESERVATION_PRICE, pricing } from '../lib/commercial.mjs';
const artifactModule = process.env.RUNTIME_NODE_MODULES ? pathToFileURL(path.join(process.env.RUNTIME_NODE_MODULES, '@oai/artifact-tool/dist/artifact_tool.mjs')).href : '@oai/artifact-tool';
const { Presentation, PresentationFile } = await import(artifactModule);

// Run from website/ using the bundled artifact runtime. Keep all intermediates
// in .artifacts/; only the reviewed final deck is a public download.
const output = path.resolve(process.env.DECK_OUTPUT_PATH || 'public/downloads/hormuz-buyer-briefing.pptx');
const scratch = path.resolve(process.env.DECK_SCRATCH_DIR || '.artifacts/deck');
await fs.mkdir(scratch, { recursive: true });
await fs.mkdir(path.dirname(output), { recursive: true });
const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
const C = { night: '#24392d', ink: '#202723', paper: '#f4f3ee', white: '#fafbf6', teal: '#31684b', cyan: '#dceda6', muted: '#5c6758', line: '#d4dbcc' };
const site = 'https://usehormuz.github.io/';
const repo = 'https://github.com/Xpounder-com/hormuz/blob/main/';
const sourceNotes = [];
const brandMarks = {
  light: await fs.readFile('public/brand/hormuz-mark.svg', 'utf8'),
  dark: await fs.readFile('public/brand/hormuz-mark-reverse.svg', 'utf8'),
};

function text(slide, name, value, left, top, width, height, size = 28, color = C.ink, bold = false) {
  const shape = slide.shapes.add({ geometry: 'textbox', name, position: { left, top, width, height }, fill: 'none', line: { fill: 'none', width: 0, style: 'solid' } });
  shape.text = value;
  shape.text.style = { fontSize: size, typeface: 'Arial', bold, color, autoFit: 'none', wrap: 'square', insets: { left: 0, right: 0, top: 0, bottom: 0 } };
  return shape;
}
function slide(kicker, title, number, sources, dark = false) {
  const s = presentation.slides.add();
  s.background.fill = dark ? C.night : C.paper;
  s.images.add({ svg: dark ? brandMarks.dark : brandMarks.light, alt: 'Hormuz passage H', fit: 'contain', position: { left: 1176, top: 35, width: 40, height: 40 } });
  text(s, 'section', kicker.toUpperCase(), 64, 44, 1130, 30, 19, dark ? C.cyan : C.teal, true);
  if (title) text(s, 'takeaway', title, 64, 106, 1152, 124, 48, dark ? C.white : C.ink, true);
  text(s, 'footer', 'HORMUZ  /  A product of Neuralint  /  Mehrdad Zaker  /  October 2026', 64, 672, 1000, 24, 17, dark ? '#dce6d5' : C.muted);
  text(s, 'page-number', `${String(number).padStart(2, '0')} / 07`, 1152, 672, 64, 24, 17, dark ? '#dce6d5' : C.muted);
  const urls = sources.map(source => source.startsWith('https:') ? source : repo + source);
  s.speakerNotes.textFrame.setText(`[Sources]\n${urls.join('\n')}\n[/Sources]\nScope: public v1 source contracts and synthetic evidence. No customer outcome, certification, SLA, validated demand, or future software availability is implied.`);
  sourceNotes.push({ slide: number, sources: urls });
  return s;
}

let s = slide('Software + Cloud + appliances', '', 1, ['marketing/OFFER.md', 'LICENSE', 'docs/CLIENTS.md'], true);
text(s, 'cover-title', 'Give your team AI.\nKeep control.', 64, 156, 1140, 222, 76, C.white, true);
text(s, 'cover-summary', 'Self-hosted policy, budgets, secret controls,\nand metadata-only evidence for Codex and Claude Code.', 68, 438, 1110, 106, 31, '#d9e1df');
text(s, 'cover-boundary', 'Apache-2.0 core  •  v1.3.0 source release', 68, 582, 1110, 42, 25, C.cyan, true);

s = slide('The control point', 'Put policy in the model-request path.', 2, ['docs/ARCHITECTURE.md', 'docs/CLIENTS.md', 'docs/AUDIT.md']);
// A single simple native diagram makes the two credential boundaries explicit.
text(s, 'arrow-one', '→', 388, 311, 60, 70, 46, C.teal);
text(s, 'arrow-two', '→', 823, 311, 60, 70, 46, C.teal);
for (const [x, w, label, body] of [
  [64, 300, 'EMPLOYEE CLIENT', 'Codex / Claude Code\nUnique Hormuz identity'],
  [456, 340, 'HORMUZ', 'Identity + policy + secrets\nBudget before egress'],
  [892, 324, 'MODEL PROVIDER', 'Company account\nServer-side provider key'],
]) {
  const box = s.shapes.add({ geometry: 'rect', name: label, position: { left: x, top: 273, width: w, height: 178 }, fill: C.white, line: { fill: C.line, width: 1, style: 'solid' } });
  text(s, `${label}-title`, label, x + 20, 296, w - 40, 40, 22, C.teal, true);
  text(s, `${label}-body`, body, x + 20, 354, w - 40, 80, 24);
}
text(s, 'metadata', 'Retain the control record, not the conversation.', 64, 512, 1152, 45, 32, C.ink, true);
text(s, 'boundary', 'Allowed content still reaches the provider. Bypassed requests and client-side shell, MCP, Git, or browser activity are outside Hormuz’s coverage.', 64, 576, 1152, 70, 25, C.muted);

s = slide('Inspect the proof', 'A real demo. Synthetic inputs.', 3, [site + 'demo/', 'hormuz/demo.py', 'website/public/demo/gateway.json', 'website/public/demo/synthetic-evidence.jsonl'], true);
text(s, 'zero', '0', 64, 268, 220, 162, 140, C.cyan, true);
text(s, 'zero-label', 'external provider calls', 64, 446, 360, 70, 30, C.white, true);
text(s, 'checks', 'Allow an approved request\nReroute and cap an unapproved model\nRedact a detected secret before egress\nDeny without an upstream call', 492, 266, 704, 252, 31, C.white);
text(s, 'proof-boundary', 'Four usage events + one secret-control event. Download the real recording and a schema-checked synthetic export. Not a benchmark, customer case study, or independent-user result.', 64, 553, 1152, 86, 25, '#d9e1df');

s = slide('Software, Cloud, and hardware', 'One clear price catalog.', 4, ['website/lib/pricing.json', 'marketing/OFFER.md', 'marketing/COMMERCIAL_SETUP.md']);
const priceRows = [
  ['Software', SOFTWARE_PRICE, 'Apache-2.0 software'],
  ['Cloud', CLOUD_PRICE, 'Per workspace / month'],
  ['Appliance', APPLIANCE_PRICE, 'Per appliance / one time / SSD included'],
  ['Appliance + scoped onboarding', ONBOARDING_PRICE, 'Per appliance / one time / SSD included'],
  ['Managed site', MANAGED_SITE_PRICE, 'Per site / month / Cloud included'],
];
priceRows.forEach(([label, amount, unit], i) => {
  const y = 260 + i * 60;
  text(s, `offer-${i}`, label, 64, y, 502, 48, 27, C.ink, true);
  text(s, `price-${i}`, amount, 584, y, 230, 48, 32, C.teal, true);
  text(s, `unit-${i}`, unit, 850, y, 366, 54, 23, C.muted);
});
text(s, 'price-scope', `Managed site: ${pricing.managedSite.supportHoursPerMonth} support hour/month; response within ${pricing.managedSite.responseBusinessDays} business days.\nUSD. Provider usage, customer infrastructure, and applicable taxes are separate.`, 64, 575, 1152, 74, 23, C.muted);

s = slide('Maturity and responsibility', 'Stable contracts are not certification.', 5, ['SUPPORT.md', 'docs/OIDC.md', 'docs/SECRET_CONTROLS.md', 'docs/USAGE.md', 'marketing/TRUST.md']);
text(s, 'release', 'v1.3.0 source + Apple Silicon app', 64, 268, 1152, 64, 42, C.teal, true);
text(s, 'oci', 'Personal Optimizer: macOS 14+ on Apple Silicon. Signed OCI: v1.3.0 linux/amd64.', 64, 346, 1152, 52, 28);
text(s, 'operators', 'You still qualify TLS, custody, retention, backups, recovery, availability, access controls, and independent security review in your environment.', 64, 432, 1152, 102, 31, C.ink, true);
text(s, 'nonclaims', 'Not claimed: complete semantic DLP, per-inference human approval, reconciled provider invoices, qualified ARM64 appliance delivery, generally available Cloud, or a 24/7 SLA.', 64, 569, 1152, 81, 25, C.muted);

s = slide('Scoped appliance onboarding', 'One appliance. One supported workflow.', 6, ['marketing/PILOT.md', 'website/lib/pricing.json']);
const phases = [
  ['01', 'Prepare', 'Named operator, ready network, provider account, and acceptance scope.'],
  ['02', 'Connect', 'One provider, one supported application, and initial policies.'],
  ['03', 'Verify and hand over', 'Allowed and denied request checks, operator instructions, and open issues.'],
];
phases.forEach(([n, title, detail], i) => {
  const y = 267 + i * 112;
  text(s, `phase-${n}`, n, 64, y, 104, 70, 54, C.teal, true);
  text(s, `phase-title-${n}`, title, 204, y, 1012, 42, 31, C.ink, true);
  text(s, `phase-body-${n}`, detail, 204, y + 50, 1012, 45, 26);
});
text(s, 'onboarding-boundary', `Up to ${pricing.onboarding.remoteHours} hours of remote setup. Appliance and SSD included.
Custom integrations, on-site work, and ongoing operations are excluded.`, 64, 605, 1152, 58, 22, C.muted);

s = slide('Planned rollout: early 2027', 'Coming soon. Reserve your appliance.', 7, [site + 'enterprise/#reserve', 'marketing/COMMERCIAL_SETUP.md', 'website/lib/pricing.json'], true);
text(s, 'next-step', `Reserve yours — ${RESERVATION_PRICE}\nFully refundable. Credited toward purchase.\nCancel anytime before fulfillment.`, 64, 270, 1140, 178, 38, C.white);
text(s, 'reservation-terms', 'One-time deposit per appliance. All payments use Stripe.\nRollout is a target, not a guaranteed shipping date.', 64, 461, 1152, 70, 24, '#d9e1df');
text(s, 'contact', 'Mehrdad Zaker\nmehrdadz@neuralint.io', 64, 551, 1140, 75, 27, C.cyan, true);
text(s, 'website', 'usehormuz.github.io/enterprise/#reserve', 64, 634, 1140, 28, 20, '#d9e1df');

await fs.writeFile(path.join(scratch, 'source-notes.txt'), JSON.stringify(sourceNotes, null, 2));
for (const [i, item] of presentation.slides.items.entries()) {
  const stem = `slide-${String(i + 1).padStart(2, '0')}`;
  const png = await presentation.export({ slide: item, format: 'png', scale: 1.3 });
  await fs.writeFile(path.join(scratch, `${stem}.png`), new Uint8Array(await png.arrayBuffer()));
  const layout = await item.export({ format: 'layout' });
  await fs.writeFile(path.join(scratch, `${stem}.layout.json`), await layout.text());
}
const pptx = await PresentationFile.exportPptx(presentation);
await pptx.save(output);
await fs.rename(`${output}.inspect.ndjson`, path.join(scratch, 'deck.inspect.ndjson'));
console.log(JSON.stringify({ deck: output, slides: presentation.slides.items.length, previews: scratch }));
