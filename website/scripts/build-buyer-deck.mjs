import fs from 'node:fs/promises';
import path from 'node:path';
import { Presentation, PresentationFile } from '@oai/artifact-tool';

// Run from website/ using the bundled artifact runtime. Keep all intermediates
// in .artifacts/; only the reviewed final deck is a public download.
const output = path.resolve('public/downloads/hormuz-buyer-briefing.pptx');
const scratch = path.resolve('.artifacts/deck');
await fs.mkdir(scratch, { recursive: true });
await fs.mkdir(path.dirname(output), { recursive: true });
const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
const C = { night: '#07171a', ink: '#101b20', paper: '#f4f4ef', white: '#fbfcf8', teal: '#087f78', cyan: '#68e6dd', muted: '#607077', line: '#d9e1df' };
const site = 'https://usehormuz.github.io/';
const repo = 'https://github.com/Xpounder-com/hormuz/blob/main/';
const sourceNotes = [];

function text(slide, name, value, left, top, width, height, size = 28, color = C.ink, bold = false) {
  const shape = slide.shapes.add({ geometry: 'textbox', name, position: { left, top, width, height }, fill: 'none', line: { fill: 'none', width: 0, style: 'solid' } });
  shape.text = value;
  shape.text.style = { fontSize: size, typeface: 'Arial', bold, color, autoFit: 'none', wrap: 'square', insets: { left: 0, right: 0, top: 0, bottom: 0 } };
  return shape;
}
function slide(kicker, title, number, sources, dark = false) {
  const s = presentation.slides.add();
  s.background.fill = dark ? C.night : C.paper;
  text(s, 'section', kicker.toUpperCase(), 64, 44, 1130, 30, 19, dark ? C.cyan : C.teal, true);
  if (title) text(s, 'takeaway', title, 64, 106, 1152, 124, 48, dark ? C.white : C.ink, true);
  text(s, 'footer', `HORMUZ  /  Mehrdad Zaker  /  31 Aug 2026                                      ${String(number).padStart(2, '0')} / 07`, 64, 672, 1152, 24, 17, dark ? '#b9ccc7' : C.muted);
  const urls = sources.map(source => source.startsWith('https:') ? source : repo + source);
  s.speakerNotes.textFrame.setText(`[Sources]\n${urls.join('\n')}\n[/Sources]\nScope: public v1 source contracts and synthetic evidence. No customer outcome, certification, SLA, validated demand, or future software availability is implied.`);
  sourceNotes.push({ slide: number, sources: urls });
  return s;
}

let s = slide('Open source + supported evaluation', '', 1, ['marketing/OFFER.md', 'LICENSE', 'docs/CLIENTS.md'], true);
text(s, 'cover-title', 'Keep the coding clients.\nGovern their model requests.', 64, 156, 1140, 222, 68, C.white, true);
text(s, 'cover-summary', 'Self-hosted policy, budgets, secret controls,\nand metadata-only evidence for Codex and Claude Code.', 68, 438, 1110, 106, 31, '#d9e1df');
text(s, 'cover-boundary', 'Apache-2.0 core  •  v1.0.0 source contracts', 68, 582, 1110, 42, 25, C.cyan, true);

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

s = slide('What is free. What is paid.', 'Keep a useful core open source.', 4, ['LICENSE', 'marketing/OFFER.md', 'marketing/PILOT.md']);
text(s, 'oss-title', 'Apache-2.0 product', 64, 268, 514, 50, 34, C.teal, true);
text(s, 'oss-body', 'Gateway and client paths\nOIDC JWT verification\nPolicy overlays and budgets\nDeterministic secret controls\nUsage reports and evidence', 64, 337, 516, 224, 28);
text(s, 'paid-title', 'Scoped engagement', 684, 268, 532, 50, 34, C.teal, true);
text(s, 'paid-body', 'Workflow and control mapping\nConfiguration and integration help\nAgreed non-production checks\nEvidence pack and gap review\nOperator handoff', 684, 337, 532, 224, 28);
text(s, 'same-core', 'Same open core. Customer-operated infrastructure. Price, capacity, support hours, response targets, and terms agreed before work.', 64, 588, 1152, 68, 24, C.muted);

s = slide('Maturity and responsibility', 'Stable contracts are not certification.', 5, ['SUPPORT.md', 'docs/OIDC.md', 'docs/SECRET_CONTROLS.md', 'docs/USAGE.md', 'marketing/TRUST.md']);
text(s, 'release', 'v1.0.0 source contracts', 64, 268, 1152, 64, 42, C.teal, true);
text(s, 'oci', 'Signed OCI v0.1.3 linux/amd64 is a separate reference stream.', 64, 346, 1152, 52, 28);
text(s, 'operators', 'You still qualify TLS, custody, retention, backups, recovery, availability, access controls, and independent security review in your environment.', 64, 432, 1152, 102, 31, C.ink, true);
text(s, 'nonclaims', 'Not claimed: native Hormuz login/refresh sessions, complete semantic DLP, per-inference human approval, reconciled provider invoices, managed SaaS, or a 24/7 SLA.', 64, 569, 1152, 81, 25, C.muted);

s = slide('Proposed 90-day pilot', 'Prove one workflow before expanding.', 6, ['marketing/PILOT.md']);
const phases = [
  ['01', 'Days 1–15 / Map', 'Control map, prerequisites, and agreed acceptance criteria.'],
  ['02', 'Days 16–45 / Prove', 'One non-production integration, evidence pack, and issue log.'],
  ['03', 'Days 46–90 / Decide', 'Go/no-go decision, named gap owners, and operator handoff.'],
];
phases.forEach(([n, title, detail], i) => {
  const y = 267 + i * 112;
  text(s, `phase-${n}`, n, 64, y, 104, 70, 54, C.teal, true);
  text(s, `phase-title-${n}`, title, 204, y, 1012, 42, 31, C.ink, true);
  text(s, `phase-body-${n}`, detail, 204, y + 50, 1012, 45, 26);
});
text(s, 'pilot-boundary', 'Start with fit and scope. A shorter evaluation may come first. No work is confirmed by an inquiry.', 64, 620, 1152, 40, 22, C.muted);

s = slide('A bounded next step', 'Name one workflow worth governing.', 7, [site, 'marketing/PILOT.md', 'marketing/TRUST.md'], true);
text(s, 'next-step', 'Which client? Which team?\nWhich control is missing?\nWho operates the route?', 64, 280, 1140, 198, 44, C.white);
text(s, 'contact', 'Mehrdad Zaker\nzaker.mehrdad@gmail.com', 64, 510, 1140, 86, 31, C.cyan, true);
text(s, 'website', 'usehormuz.github.io', 64, 613, 1140, 40, 25, '#d9e1df');

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
