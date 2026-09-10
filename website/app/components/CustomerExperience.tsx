'use client';

import { useEffect, useRef, useState } from 'react';
import { BrandMark } from './Brand';
import { SpendExample } from './SpendExample';
import { PolicyExample } from './PolicyExample';
import { CompactionExample } from './CompactionExample';
import { SetupExample } from './SetupExample';

const chapters = [['spend', 'Spend & tokens'], ['policy', 'Set a policy'], ['compaction', 'Compact context'], ['setup', 'My setup']];

export function CustomerExperience() {
  const [chapter, setChapter] = useState('spend');
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => {
    function readHash() { const key = window.location.hash.slice(1); if (chapters.some(([id]) => id === key)) setChapter(key); }
    readHash(); window.addEventListener('hashchange', readHash);
    return () => window.removeEventListener('hashchange', readHash);
  }, []);
  function select(key: string, focus = false) {
    setChapter(key);
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#${key}`);
    if (focus) tabs.current[chapters.findIndex(([id]) => id === key)]?.focus();
  }
  return <section className="customer-experience" id="experience" aria-labelledby="experience-title">
    <div className="experience-intro"><div><p className="landing-eyebrow">A WORKDAY WITH HORMUZ</p><h2 id="experience-title">Try the controls.<br /><em>See the consequence.</em></h2></div><p>Imagine your company spends $1 million a year on AI. Find the cost driver, adjust a policy, and see how your team would use Hormuz.</p></div>
    <div className="experience-shell"><header className="experience-appbar"><span><BrandMark /><strong>Acme AI workspace</strong></span><span className="example-badge">Interactive illustration · fictional data</span></header>
      <div className="experience-tabs" role="tablist" aria-label="Explore Hormuz">{chapters.map(([id, label], index) => <button key={id} id={id} role="tab" type="button" aria-selected={chapter === id} aria-controls={`panel-${id}`} tabIndex={chapter === id ? 0 : -1} ref={node => { tabs.current[index] = node; }} onClick={() => select(id)} onKeyDown={event => { let target = index; if (event.key === 'ArrowRight') target = (index + 1) % chapters.length; else if (event.key === 'ArrowLeft') target = (index + chapters.length - 1) % chapters.length; else if (event.key === 'Home') target = 0; else if (event.key === 'End') target = chapters.length - 1; else return; event.preventDefault(); select(chapters[target][0], true); }}><span>{String(index + 1).padStart(2, '0')}</span>{label}</button>)}</div>
      <div className="experience-panel" role="tabpanel" id="panel-spend" aria-labelledby="spend" hidden={chapter !== 'spend'} tabIndex={0}><SpendExample onPolicy={() => select('policy', true)} /></div>
      <div className="experience-panel" role="tabpanel" id="panel-policy" aria-labelledby="policy" hidden={chapter !== 'policy'} tabIndex={0}><PolicyExample /></div>
      <div className="experience-panel" role="tabpanel" id="panel-compaction" aria-labelledby="compaction" hidden={chapter !== 'compaction'} tabIndex={0}><CompactionExample /></div>
      <div className="experience-panel" role="tabpanel" id="panel-setup" aria-labelledby="setup" hidden={chapter !== 'setup'} tabIndex={0}><SetupExample /></div>
    </div>
    <noscript><div className="setup-callout"><p>The company report above is available without JavaScript. Enable JavaScript to change example policies and compare representations. The same walkthrough is available as a <a href="/demo/walkthrough.txt">plain-text guide</a>, with <a href="/demo/customer-example.json">complete example data</a> and <a href="/demo/compaction-example.json">both compaction forms</a>.</p></div></noscript>
  </section>;
}
