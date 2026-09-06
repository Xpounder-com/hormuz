import { pageMetadata } from '../../lib/metadata';
import { sitePath, OWNER_NAME, OWNER_URL } from '../../lib/site.mjs';
import { PageFrame, PageHero } from '../components/PageFrame';
import { BrandMark } from '../components/Brand';

export const metadata = pageMetadata('Brand & assets — Hormuz', 'Controlled passage: the Hormuz identity, visual language, and downloadable brand assets for contributors and partners.', '/brand/');

const colors = [
  ['Chalk', '#F4F3EE', 'The canvas'], ['Forest', '#24392D', 'Primary action'],
  ['Sage', '#DCE6D5', 'Supporting surfaces'], ['Signal', '#DCEDA6', 'Focused accents'],
  ['Ink', '#202723', 'Text and detail'],
];
const assets = [
  ['Complete brand kit', 'SVG marks and lockups, artwork, social card, tokens, and usage guide · ZIP', '/brand/hormuz-brand-kit.zip'],
  ['Primary lockup', 'Forest identity for light backgrounds · SVG', '/brand/hormuz-lockup.svg'],
  ['Reverse lockup', 'Chalk identity for dark backgrounds · SVG', '/brand/hormuz-lockup-reverse.svg'],
  ['Standalone mark', 'Two gate forms, one open passage · SVG', '/brand/hormuz-mark.svg'],
  ['Icons in every size', 'Forest, signal, and transparent · 16–1024 px PNGs, SVGs, and ICOs · ZIP', '/brand/hormuz-icons.zip'],
  ['Social preview', '1200 × 630, ready for links and organic posts · PNG', '/og.png'],
  ['Brand guide', 'Positioning, voice, color, typography, and asset usage · Markdown', '/brand/README.md'],
];

export default function BrandPage() {
  return <PageFrame active="brand">
    <PageHero eyebrow="The Hormuz identity" title={<>Room to move.<br /><span>Clarity at the boundary.</span></>}>
      <p>A practical identity for an open-source AI gateway. Built around one idea: controlled passage. A product of <a href={OWNER_URL}>{OWNER_NAME} ↗</a>, led by Mehrdad Zaker.</p>
      <div className="hero-actions"><a className="button landing-primary" href="#assets">Get the brand assets <span aria-hidden="true">↓</span></a></div>
    </PageHero>
    <section className="brand-section brand-concept" id="concept">
      <div><p className="section-label">01 / The idea</p><h2>Controlled<br /><em>passage.</em></h2><p>Teams need room to use AI. Organizations need a clear place to apply their rules. Hormuz brings those needs together at the model-request boundary.</p><p>Our visual language follows the same idea: open channels, deliberate checkpoints, and a record you can inspect. Calm enough to read. Precise enough to trust.</p><div className="brand-positioning"><strong>WHAT WE DO</strong><p>Hormuz is an open-source AI policy gateway for supported requests routed through it, with budgets, secret controls, and content-free routine evidence.</p></div></div>
      <figure><img src={sitePath('/brand/controlled-passage.webp')} width="1536" height="1024" alt="Layered cream and sage contours framing an open curved passage" /><figcaption>Original abstract brand artwork. The passage is a visual metaphor, separate from product architecture.</figcaption></figure>
    </section>
    <section className="brand-section" id="identity">
      <p className="section-label">02 / The identity</p><h2>One mark.<br /><em>A consistent family.</em></h2><p>Two solid gate forms create an open H. Unequal heights give the mark its character; the clear passage connects it to the product. The same geometry carries from browser tabs to the full wordmark.</p>
      <div className="brand-lockups"><div className="brand-lockup-card"><img src={sitePath('/brand/hormuz-lockup.svg')} alt="Hormuz primary forest lockup" width="360" height="80" /><span>PRIMARY / LIGHT SURFACES</span></div><div className="brand-lockup-card brand-lockup-dark"><img src={sitePath('/brand/hormuz-lockup-reverse.svg')} alt="Hormuz reverse chalk lockup" width="360" height="80" /><span>REVERSE / DARK SURFACES</span></div></div>
      <div className="brand-small-system"><BrandMark /><p>Keep clear space of at least half the mark’s width around the lockup. Use the standalone mark at 24 px or larger. The dedicated 16 px icon has a wider central gap to stay clear in a browser tab.</p></div>
      <div className="brand-icon-system" id="icons">
        {(['forest', 'signal'] as const).map(theme => <div className={`brand-icon-row brand-icon-${theme}`} key={theme}><h3>{theme === 'forest' ? 'Forest / primary icon' : 'Signal / alternate icon'}</h3><div>{[16, 24, 32, 48, 64, 128].map(size => <figure key={size}><img src={sitePath(`/brand/icons/${theme}/hormuz-${theme}-${size}.png`)} width={size} height={size} alt={`${theme === 'forest' ? 'Forest' : 'Signal'} Hormuz icon at ${size} pixels`} /><figcaption>{size} px</figcaption></figure>)}</div></div>)}
        <p>Download the full set from 16 to 1024 px, including scalable SVGs and multi-resolution ICO files.</p><a href={sitePath('/brand/hormuz-icons.zip')} download>Download all icon sizes ↓</a>
      </div>
    </section>
    <section className="brand-section" id="system">
      <p className="section-label">03 / Color & typography</p><h2>A calm canvas.<br /><em>A clear signal.</em></h2><p>Chalk gives the content room. Forest anchors actions. Sage supports the interface, and a little signal green draws attention. Status always includes a label or symbol.</p>
      <div className="brand-swatches">{colors.map(([name, hex, role]) => <div className="brand-swatch" key={name}><div style={{ background: hex }} /><p><strong>{name}</strong><code>{hex}</code>{role}</p></div>)}</div>
      <div className="brand-type-grid"><div className="brand-type-card"><span>GEIST + GEORGIA ITALIC</span><h3>Give your team AI.<br /><em>Keep control.</em></h3><p>Geist carries the product story. A short phrase in Georgia italic brings a human voice to editorial headlines.</p></div><div className="brand-type-card"><span>GEIST MONO / TECHNICAL DETAIL</span><code>policy / budget / evidence<br />decision: allowed<br />routine_content: excluded</code><p>Use monospace for code, labels, and inspectable detail. This is a typographic specimen, not an exported product record.</p></div></div>
    </section>
    <section className="brand-section" id="voice"><p className="section-label">04 / How we speak</p><h2>Clear language.<br /><em>Claims you can inspect.</em></h2><div className="brand-voice"><article><h3>Start with the workflow.</h3><p>Talk about the tools people use and the control they need. Make the next step concrete.</p><blockquote>“Keep Codex and Claude Code. Put policy in the request path.”</blockquote></article><article><h3>Show the boundary.</h3><p>Explain what a control covers. Pair capability with evidence instead of a sweeping assurance.</p><blockquote>“Company provider keys stay on the gateway.”</blockquote></article><article><h3>Keep the human voice.</h3><p>Be direct, helpful, and specific. A review is a conversation. A pilot starts with agreed scope.</p><blockquote>“Bring one workflow. We’ll work through it together.”</blockquote></article></div></section>
    <section className="brand-section" id="assets"><p className="section-label">05 / Use the identity</p><h2>Ready for the next<br /><em>page, brief, or post.</em></h2><p>Assets for referring to Hormuz and creating consistent project materials. Keep the identity legible and avoid implying a partnership, certification, or endorsement.</p><figure className="brand-social"><img src={sitePath('/og.png')} width="1200" height="630" alt="Hormuz social preview: Give your team AI. Keep control. Open-source AI policy gateway." loading="lazy" /></figure><div className="brand-asset-grid">{assets.map(([title, detail, href]) => <a key={href} href={sitePath(href)} download><strong>{title} <span aria-hidden="true">↓</span></strong><span>{detail}</span></a>)}</div><p className="brand-asset-note">The software remains Apache-2.0. These assets identify the Hormuz project; software licensing does not imply a trademark license or endorsement.</p></section>
  </PageFrame>;
}
