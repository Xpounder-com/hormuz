import { sitePath } from '../../lib/site.mjs';
import validation from '../../public/demo/ai-work-browser-qa.json';

export function AiWorkDemo() {
  const seededCost = (validation.seeded_cost_microusd / 1_000_000).toFixed(2);
  return <section className="section prose-section work-demo" id="work-demo"><h2>See the actual AI Work dashboard.</h2><p>Create a work record, save a priority, pause and resume, and inspect sourced completion from executed CLI checks.</p><figure><video controls preload="metadata" poster={sitePath('/demo/ai-work-desktop.png')} aria-label="Recorded functional validation of the Hormuz AI Work dashboard"><source src={sitePath('/demo/ai-work-demo.webm')} type="video/webm" />Your browser can <a href={sitePath('/demo/ai-work-demo.webm')}>download the recording</a>.</video><figcaption>Actual authenticated gateway UI against a local synthetic identity and provider fixture. The ${seededCost} cost is seeded synthetic data; the run uses {validation.provider_calls}. Production, payment, and native-client qualification remains separate.</figcaption></figure><div className="resource-actions"><a className="text-link" href={sitePath('/demo/ai-work-demo.webm')} download>Download the UI recording ↓</a><a className="text-link" href={sitePath('/demo/ai-work-browser-qa.json')} download>Browser validation and conditions ↓</a></div></section>;
}
