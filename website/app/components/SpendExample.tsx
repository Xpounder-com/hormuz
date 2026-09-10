'use client';

import { useState } from 'react';
import fixture from '../../public/demo/customer-example.json';
import { budgetBalance, monthPace, summarizeUsage } from '../../lib/customer-example.mjs';
import { CodeBlock } from './CodeBlock';
import { sitePath, sourcePath } from '../../lib/site.mjs';

export const usd = (value: number, cents = false) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: cents ? 2 : 0 }).format(value);
export const tokens = (value: number) => new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(value);

export function SpendExample({ onPolicy }: { onPolicy: () => void }) {
  const [team, setTeam] = useState('All teams');
  const [model, setModel] = useState('All models');
  const summary = summarizeUsage(fixture, team, model);
  const teamSummary = summarizeUsage(fixture, team);
  const selected = fixture.teams.find(row => row.name === team);
  const budget = Number(selected?.monthly_budget ?? fixture.monthly_budget);
  const pending = Number(selected?.pending ?? fixture.expected.pending);
  const uncertain = Number(selected?.uncertain ?? fixture.expected.uncertain);
  const available = budgetBalance(budget, teamSummary.cost, pending, uncertain);
  const pace = monthPace(teamSummary.cost, 15, 30);
  function chooseTeam(name: string) { setTeam(name); setModel('All models'); }
  return <div className="spend-example">
    <div className="experience-view-heading"><div><span className="experience-kicker">YOUR MONDAY MORNING CHECK-IN</span><h3>{selected ? `${team}: where is the money going?` : 'A $1M AI budget. One clear view.'}</h3></div><span className="period-pill">Day 15 of 30 · example month</span></div>
    <div className="spend-metrics" aria-label="Team budget summary">
      <div><span>Estimated cost so far</span><strong>{usd(teamSummary.cost)}</strong><small>Configured rates · captured requests</small></div>
      <div><span>Monthly budget</span><strong>{usd(budget, true)}</strong><small>{selected ? 'Team ceiling' : '$1,000,000 annual plan ÷ 12'}</small></div>
      <div className="metric-pace"><span>At this pace</span><strong>{usd(pace)}</strong><small>{pace > budget ? `${usd(pace - budget, true)} above the monthly plan` : `${usd(budget - pace, true)} below the monthly plan`}</small></div>
    </div>
    <div className="spend-workspace">
      <aside className="team-list" aria-label="Choose a team"><div className="table-overline"><span>TEAM</span><span>EST. COST</span></div><button type="button" onClick={() => chooseTeam('All teams')} aria-pressed={team === 'All teams'}><span>All teams <small>8.99B tokens</small></span><strong>$50,000</strong></button>{fixture.teams.map(row => <button key={row.name} type="button" onClick={() => chooseTeam(row.name)} aria-pressed={team === row.name}><span>{row.name}<small>{tokens(row.expected_tokens)} tokens</small></span><strong>{usd(Number(row.expected_cost))}<span aria-hidden="true"> ↗</span></strong></button>)}<p>Try Engineering to see why output tokens deserve a closer look.</p></aside>
      <div className="token-detail">
        <div className="detail-toolbar"><h4>What drives the cost?</h4><label><span className="sr-only">Filter model</span><select value={model} onChange={event => setModel(event.target.value)}><option>All models</option><option>Advanced</option><option>Standard</option></select></label></div>
        <p className="model-note">{tokens(summary.tokens)} tokens · {usd(summary.cost)} estimated cost {model !== 'All models' ? `for ${model}` : ''}</p>
        <div className="token-stacked-bar" aria-label="Share of estimated cost by token category">{summary.categories.map((row, index) => <span key={row.kind} className={`token-color-${index}`} style={{ width: `${summary.cost ? row.cost / summary.cost * 100 : 0}%` }} />)}</div>
        <table className="token-table"><caption className="sr-only">Token categories for {team}, {model}. Cached input is included once.</caption><thead><tr><th scope="col">Token type</th><th scope="col">Tokens</th><th scope="col">Est. cost</th></tr></thead><tbody>{summary.categories.map((row, index) => <tr key={row.kind}><th scope="row"><i className={`token-color-${index}`} />{row.label}</th><td>{tokens(row.tokens)}</td><td>{usd(row.cost)}</td></tr>)}</tbody></table>
        <div className="insight-note"><span aria-hidden="true">↗</span><p>{team === 'Engineering' && model === 'All models' ? <><strong>12.82% of tokens. 55% of cost.</strong> Engineering’s 500M output tokens account for $16,500. Advanced accounts for $28,800 of the team’s $30,000.</> : <><strong>Volume and cost tell different stories.</strong> Compare cached input, uncached input, and output. Different model rates change what the same number of tokens costs.</>}</p></div>
        <button className="experience-action" type="button" onClick={onPolicy}>Try an output cap for Engineering <span aria-hidden="true">→</span></button>
      </div>
    </div>
    <details className="experience-details"><summary>What is actually available to spend? <span>Work-budget report preview</span></summary><div className="hold-breakdown"><div><span>Active plan</span><strong>{usd(budget, true)}</strong></div><div><span>Committed estimates</span><strong>− {usd(teamSummary.cost)}</strong></div><div><span>Pending requests</span><strong>− {usd(pending)}</strong></div><div><span>Uncertain outcomes</span><strong>− {usd(uncertain)}</strong></div><div><span>Available after holds</span><strong>{usd(available, true)}</strong></div></div><p>Pending reserves room for work in flight. Uncertain holds keep room for requests whose provider outcome is unknown. The current internal work-budget report also identifies the active plan, work scope, and plan changes. It has no public CLI or HTTP endpoint yet. This is a preview of that report. <a href={sourcePath('docs/WORK_BUDGETS.md')}>Read its availability and accounting contract ↗</a></p></details>
    <details className="experience-details"><summary>Where do I see this after installing?</summary><p>Administrators can run current UTC-month reports by organization, team, person, model, client, or provider. The Mac companion shows personal counters. The opt-in browser console preview adds organization/team usage and limited member administration. This website combines those concepts into an interactive illustration; it is not a screenshot of a released manager dashboard.</p><CodeBlock label="Engineering usage by model · real CLI" code={'hormuz --config hormuz.json status --group-by model --team engineering\nhormuz --config hormuz.json status --group-by team --json'} /><p>Reports also expose requests, successes, failures, policy denials, provider 429s, active identities, and redactions. Cache-write and reasoning categories appear when the provider reports them. <a href={sourcePath('docs/USAGE.md')}>Report fields ↗</a> · <a href={sourcePath('docs/ADMIN_CONSOLE_LOCAL.md')}>Console preview ↗</a></p></details>
    <p className="experience-footnote">Fictional teams, model aliases, and rates. Straight-line pace = cost ÷ 15 × 30; it is a scenario estimate. Only traffic routed through Hormuz is visible. Token volume measures consumption, not productivity. <a href={sitePath('/demo/customer-example.json')} download>Inspect all example data ↓</a></p>
  </div>;
}
