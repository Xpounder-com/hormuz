import fixture from '../../public/demo/customer-example.json';
import { summarizeUsage } from '../../lib/customer-example.mjs';
import { BrandMark } from './Brand';

export function SpendHookPreview() {
  const engineering = summarizeUsage(fixture, 'Engineering');
  const output = engineering.categories.find(category => category.kind === 'output')!;
  const tokenShare = output.tokens / engineering.tokens * 100;
  const costShare = output.cost / engineering.cost * 100;
  const usd = (value: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
  const tokens = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(output.tokens);

  return <article className="spend-hook" aria-labelledby="spend-hook-title">
    <header className="spend-hook-header"><span><BrandMark /> Acme AI workspace</span><span className="spend-hook-badge">Fictional example</span></header>
    <div className="spend-hook-body">
      <p className="spend-hook-scope">ENGINEERING · THIS MONTH SO FAR</p>
      <h2 id="spend-hook-title">Output tokens drive the cost.</h2>
      <dl className="spend-hook-shares">
        <div><dt>Share of team tokens</dt><dd><strong>≈{Math.round(tokenShare)}<span>%</span></strong><span className="spend-hook-track" aria-hidden="true"><span style={{ width: `${tokenShare}%` }} /></span></dd></div>
        <div className="spend-hook-cost"><dt>Share of team cost</dt><dd><strong>{Math.round(costShare)}<span>%</span></strong><span className="spend-hook-track" aria-hidden="true"><span style={{ width: `${costShare}%` }} /></span></dd></div>
      </dl>
      <p className="spend-hook-finding"><strong>{tokens} output tokens → {usd(output.cost)}</strong> of Engineering’s {usd(engineering.cost)} estimated spend.</p>
      <a className="spend-hook-action" href="#policy">Try an output cap <span aria-hidden="true">→</span></a>
      <p className="spend-hook-note">Illustrative report · Invented usage and rates.</p>
    </div>
  </article>;
}
