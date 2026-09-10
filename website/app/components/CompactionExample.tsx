'use client';

import { useState } from 'react';
import fixture from '../../public/demo/compaction-example.json';
import { restorePathExample } from '../../lib/customer-example.mjs';
import { sitePath, sourcePath } from '../../lib/site.mjs';

const restored = restorePathExample(fixture.compact);
const smallResult = 'src/main.py\nsrc/config.py\nsrc/server.py\n';

export function CompactionExample() {
  const [enabled, setEnabled] = useState(false);
  const [sample, setSample] = useState('paths');
  const [view, setView] = useState('sent');
  const isCompact = enabled && sample === 'paths';
  const original = sample === 'paths' ? fixture.original : smallResult;
  const sent = isCompact ? fixture.compact : original;
  const after = isCompact ? restored : original;
  const displayed = view === 'original' ? original : view === 'restored' ? after : sent;
  const beforeBytes = new TextEncoder().encode(original).length;
  const afterBytes = new TextEncoder().encode(sent).length;
  return <div>
    <div className="experience-view-heading"><div><span className="experience-kicker">AVAILABLE IN HORMUZ 1.2 · OFF BY DEFAULT</span><h3>127 paths. The same information, less repetition.</h3></div><span className="period-pill">A real structural transform</span></div>
    <p className="chapter-intro">A coding tool returns a long list of file paths. Hormuz can store the repeated directory prefix once and preserve every path, duplicate, and line order. Try the switch and inspect the exact result.</p>
    <div className="compaction-workspace">
      <aside className="compaction-setting"><span className="experience-kicker">CLIENT → CONTEXT OPTIMIZATION</span><h4>Engineering connection</h4><p>This device · this saved profile</p><div className="optimization-toggle"><label htmlFor="optimize-example">Context optimization<strong>{enabled ? 'On' : 'Off'}</strong></label><button id="optimize-example" type="button" role="switch" aria-checked={enabled} aria-label="Context optimization in the example" onClick={() => setEnabled(!enabled)}><span /></button></div><p className="setting-status" role="status">{!enabled ? 'Off · ordinary request passes through' : isCompact ? 'On · eligible path list encoded' : 'On · small result passes through unchanged'}</p><div className="example-field"><label htmlFor="compaction-sample">Try a tool result</label><select id="compaction-sample" value={sample} onChange={event => setSample(event.target.value)}><option value="paths">127 paths with a shared prefix</option><option value="small">3 paths · too small to benefit</option></select></div><p className="field-explainer">The switch mirrors the product setting. The sample and inspection buttons are teaching controls, not extra product knobs.</p><a href={sitePath('/docs/#mac')}>Install the Mac app →</a></aside>
      <div className="compaction-inspector"><div className="compaction-numbers"><div><span>Original tool block</span><strong>{beforeBytes.toLocaleString('en-US')} <small>bytes</small></strong></div><span aria-hidden="true">→</span><div><span>Selected tool block</span><strong>{afterBytes.toLocaleString('en-US')} <small>bytes</small></strong></div><span className="exact-badge">{after === original ? '✓ Exact restoration' : 'Check required'}</span></div><div className="inspect-controls" aria-label="Inspect a representation">{[['original', 'Original'], ['sent', 'Selected representation'], ['restored', 'Restored']].map(([key, label]) => <button key={key} type="button" aria-pressed={view === key} onClick={() => setView(key)}>{label}</button>)}</div><pre className="compaction-code" tabIndex={0} aria-label={`${view} tool result`}><code>{displayed}</code></pre><p className="reconstruction-note">{sample === 'paths' ? <><strong>Try finding item_7.py:</strong> its full path is <code>src/generated/item_7.py</code>, and all 6 occurrences survive. No lines are summarized or discarded.</> : <><strong>No useful reduction?</strong> The ordinary request remains valid. Turning optimization on does not mean every request changes.</>}</p></div>
    </div>
    <div className="data-path" aria-label="Context optimization data path"><span><strong>Your coding tool</strong>Produces the result</span><i aria-hidden="true">→</i><span><strong>Local helper</strong>Selects and verifies on your device</span><i aria-hidden="true">→</i><span><strong>Your gateway</strong>Decodes for secret checks; applies policy</span><i aria-hidden="true">→</i><span><strong>Model provider</strong>Receives the governed request</span></div>
    <p className="experience-footnote">Measured tool-block bytes, not whole-request tokens or billed savings. The synthetic fixture was transformed and restored with Hormuz’s implementation. Formatting shown here is complete. <a href={sitePath('/demo/compaction-example.json')} download>Download the source and compact forms ↓</a></p>
    <details className="experience-details"><summary>What gets compacted, and what stays unchanged?</summary><p>Automatic selection covers narrowly mapped tool results: simple rg --files and rg -n commands in Codex exec_command or Claude Code Bash, plus Claude Code Glob path lists. Structural formats can represent shared-prefix paths, repeated lines, search results, and identical-key JSON tables. A format being available does not make every tool result eligible.</p><p>Unknown or composed commands, ambiguous histories, code, diffs, images, system/user text, oversized or malformed data, and non-beneficial transforms pass through unchanged. There is no aggressiveness slider. Internal safety limits and dual-tokenizer checks select useful candidates. The 1.2 release’s live provider qualification is OpenAI-only.</p></details>
    <details className="experience-details"><summary>Does it change my privacy, caching, or current chat?</summary><p>Selection, reconstruction checks, and token estimates happen locally. Only one selected request representation travels to your gateway. The gateway can read and decode it for secret inspection; this is not encryption. Routine evidence stores metadata, not the conversation.</p><p>The toggle affects the next request; work already in flight keeps its previous setting. Start a new chat after toggling for the most stable provider prefix-cache behavior. Existing connectors may need to be reviewed and saved once in the updated app. The Mac archive includes the matching helper and tokenizer files. <a href={sourcePath('docs/CONTEXT_OPTIMIZATION.md')}>Setup, readiness states, limits, and evidence ↗</a></p></details>
  </div>;
}
