import { CampaignLink } from '../../components/CampaignLink';
import { CodeBlock } from '../../components/CodeBlock';
import { PageFrame, PageHero } from '../../components/PageFrame';
import { pageMetadata } from '../../../lib/metadata';
import { sitePath, sourcePath } from '../../../lib/site.mjs';
import { summarizeUsage } from '../../../lib/customer-example.mjs';
import customerExample from '../../../public/demo/customer-example.json';

export const metadata = pageMetadata(
  'AI Cost Management: Track Spend & Set Team Budgets | Hormuz',
  'Measure captured AI spend, understand input, cache, and output costs, then create team budgets for Codex and Claude Code with the self-hosted Hormuz gateway.',
  '/guides/team-ai-budgets/',
);

const engineeringSpend = summarizeUsage(customerExample, 'Engineering');
const outputSpend = engineeringSpend.categories.find(category => category.kind === 'output')!;
const dollars = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const tokens = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 });

const createPolicy = `hormuz --config config.example.json policy create \\
  --template standard \\
  --monthly-budget-usd 250 \\
  --per-actor-monthly-budget-usd 25 \\
  --output budget-baseline.json`;

const addTeam = `python3 - <<'PY'
import json
from pathlib import Path

policy = json.loads(Path("budget-baseline.json").read_text())
policy["policies"]["teams"]["engineering"] = {
    "monthly_budget_usd": 150,
    "max_output_tokens": 2000,
}
with Path("team-budget.json").open("x") as target:
    target.write(json.dumps(policy, indent=2) + "\\n")
PY

hormuz --config config.example.json policy validate team-budget.json`;

const comparePolicy = `hormuz --config config.example.json policy compare team-budget.json \\
  --baseline budget-baseline.json \\
  --organization xpounder \\
  --json`;

export default function TeamBudgetsGuide() {
  return <PageFrame active="guides">
    <PageHero eyebrow="Practical guide / AI cost management" title={<>Understand AI costs.<br /><span>Set team budgets.</span></>}>
      <p>AI cost management starts with knowing which requests drive the bill. Use Hormuz to measure captured Codex and Claude Code usage, compare token costs, and put organization, team, and person limits around requests routed through your gateway.</p>
      <p><CampaignLink href={sitePath('/resources/#tutorials')}>All practical guides →</CampaignLink></p>
    </PageHero>
    <div className="docs-shell">
      <aside className="docs-sidebar" aria-label="Guide navigation"><div><strong>In this guide</strong><a href="#measure">Measure AI spend</a><a href="#scopes">Choose the limits</a><a href="#create">Create a baseline</a><a href="#team">Add a team budget</a><a href="#compare">Inspect the change</a><a href="#activate">Activate and measure</a></div></aside>
      <article className="docs-content">
        <section id="measure" className="docs-section">
          <p className="section-label">01 / Measure AI spend</p><h2>Find which tokens drive the cost.</h2>
          <p>LLM cost management needs more than a total token count. Input, cached input, and output can have different rates, and the model mix changes what a team pays. Start by grouping captured usage by team, then inspect the models and token categories behind its estimated cost.</p>
          <p>In the site’s fictional company example, Engineering has {tokens.format(engineeringSpend.tokens)} tokens and {dollars.format(engineeringSpend.cost)} in estimated usage charges. The table uses the same <a href={sitePath('/demo/customer-example.json')}>downloadable example data</a> as the interactive demo. Model names and rates are invented; this is not customer spending or current provider pricing.</p>
          <div className="table-scroll"><table className="comparison-table"><caption>Engineering’s fictional usage, accumulated over 15 days</caption><thead><tr><th scope="col">Token category</th><th scope="col">Tokens</th><th scope="col">Estimated cost</th></tr></thead><tbody>
            {engineeringSpend.categories.map(category => <tr key={category.kind}><th scope="row">{category.label}</th><td>{tokens.format(category.tokens)}</td><td>{dollars.format(category.cost)}</td></tr>)}
          </tbody><tfoot><tr><th scope="row">Total</th><td>{tokens.format(engineeringSpend.tokens)}</td><td>{dollars.format(engineeringSpend.cost)}</td></tr></tfoot></table></div>
          <p>For each model and category, multiply tokens by the example rate per million and divide by 1,000,000, then sum the costs. Output contributes {(outputSpend.tokens / engineeringSpend.tokens * 100).toFixed(1)}% of tokens but {(outputSpend.cost / engineeringSpend.cost * 100).toFixed(0)}% of the cost here. That makes response length and model choice worth examining; it does not establish how much a policy change will save.</p>
          <p>For your own data, run these reports in a configured gateway environment with an existing usage store and captured requests. Replace the configuration path and team ID with your deployment’s values:</p>
          <CodeBlock label="Inspect captured AI costs by team, model, and provider" code={'hormuz --config /etc/hormuz/hormuz.json status --group-by team\nhormuz --config /etc/hormuz/hormuz.json status --group-by model --team engineering\nhormuz --config /etc/hormuz/hormuz.json status --group-by provider --team engineering --json'} />
          <p>The report covers the current UTC calendar month. Cache-read and reasoning tokens can be subcategories of reported input or output; do not add them to total tokens a second time. Cost estimates use configured rates and need reconciliation with provider invoices, including discounts, credits, and other charges. Requests that bypass Hormuz are outside its coverage. <a href={sourcePath('docs/USAGE.md')}>Read the usage and cost definitions ↗</a></p>
          <p>New to the gateway? <CampaignLink href={sitePath('/demo/#spend')}>Explore the cost example</CampaignLink>, then follow the local budget walkthrough below. It creates a policy candidate you can inspect before activation.</p>
        </section>
        <section id="scopes" className="docs-section">
          <p className="section-label">02 / Choose the limits</p><h2>Three scopes. One governed request.</h2>
          <p>Hormuz checks requests against organization, team, and person policy. More specific scopes can tighten the applicable controls. A team allowance does not grant permission to exceed the organization’s allowance, and an individual allowance is not extra money on top of the team budget.</p>
          <div className="table-scroll"><table className="comparison-table"><caption>Illustrative monthly limits for this walkthrough, in USD</caption><thead><tr><th scope="col">Scope</th><th scope="col">Example limit</th><th scope="col">What it controls</th></tr></thead><tbody>
            <tr><th scope="row">Organization</th><td>$250</td><td>Combined captured usage across its teams.</td></tr>
            <tr><th scope="row">Engineering</th><td>$150</td><td>Captured usage attributed to this team.</td></tr>
            <tr><th scope="row">Each person</th><td>$25</td><td>The organization’s per-person monthly allowance.</td></tr>
            <tr><th scope="row">Engineering response</th><td>2,000 output tokens</td><td>The response cap for each governed request.</td></tr>
          </tbody></table></div>
          <p>For example, suppose Engineering has used $140 with no outstanding reservations. A request requiring a $12 reservation exceeds the team’s remaining $10, even if that person and the organization have room. These are invented values to explain the check, not customer spending or measured savings.</p>
          <p>Budget accounting uses configured model rates and captured usage in the current UTC month. Provider invoices can differ, and traffic that bypasses Hormuz is outside this budget. <CampaignLink href={sitePath('/demo/#spend')}>Explore the interactive spend example →</CampaignLink></p>
        </section>
        <section id="create" className="docs-section">
          <p className="section-label">03 / Create a baseline</p><h2>Start with a policy you can inspect.</h2>
          <p>First complete the <CampaignLink href={sitePath('/docs/#quickstart')}>source quickstart</CampaignLink>. Run the commands below from the Hormuz checkout with its virtual environment active. They use the repository’s synthetic <code>config.example.json</code>, organization <code>xpounder</code>, and team <code>engineering</code>. Use new output filenames if you have already run this example.</p>
          <CodeBlock code={createPolicy} label="Create an example organization budget" />
          <p>The standard template reads configured clients and models, sets a 16,000-token response cap, and selects secret redaction. The two budget flags add the example organization and person allowances. Creation writes a local candidate; it does not activate it or contact a provider.</p>
          <p>Templates do not automatically copy team or actor overrides. For an existing deployment, review the complete policy and preserve its intended controls before replacing any active version.</p>
        </section>
        <section id="team" className="docs-section">
          <p className="section-label">04 / Add a team budget</p><h2>Give Engineering a tighter boundary.</h2>
          <p>Create a second file with the Engineering allowance and output cap. The script refuses to overwrite an existing <code>team-budget.json</code>. It uses a team already present in the example identity configuration; a policy entry alone does not enroll people into a team.</p>
          <CodeBlock code={addTeam} label="Add and validate the Engineering limits" />
          <p>Successful validation reports <code>policy valid</code> and one team scope. It checks the document against local configuration without resolving provider credentials or changing shared policy. A valid document still needs review for the intended users, models, and limits.</p>
        </section>
        <section id="compare" className="docs-section">
          <p className="section-label">05 / Inspect the change</p><h2>See what the candidate adds.</h2>
          <CodeBlock code={comparePolicy} label="Compare two local policy files" />
          <p>The comparison reports two added paths: <code>policies.teams.engineering.monthly_budget_usd</code> and <code>policies.teams.engineering.max_output_tokens</code>. Exit code <code>1</code> means the policies differ; <code>0</code> means they are identical, and <code>2</code> signals an error.</p>
          <p>This local comparison uses the example SQLite configuration and needs no usage database. Request-level <code>policy preview</code> also reads usage totals and therefore needs an existing usage store. To try a separate complete offline workflow, run <code>hormuz policy demo</code>; it creates and cleans up its own synthetic state.</p>
        </section>
        <section id="activate" className="docs-section">
          <p className="section-label">06 / Activate and measure</p><h2>Connect the policy to real usage.</h2>
          <p>The files above have not changed a running gateway. For managed activation, your policy administrator must use the deployment’s configuration, authorized identity, PostgreSQL policy-control setup, and current active version. Follow the <a href={sourcePath('docs/POLICY_CONTROL.md')}>policy administration reference ↗</a> for compare, request preview, guarded apply, history, and rollback.</p>
          <p>After an approved change, check one allowed request and one agreed denial. Confirm the denied request made no provider call. On the configured gateway environment, inspect the attributed usage:</p>
          <CodeBlock label="Report captured usage on your configured gateway" code={'hormuz --config /etc/hormuz/hormuz.json status --group-by team\nhormuz --config /etc/hormuz/hormuz.json status --group-by model --team engineering'} />
          <p>Use your actual configuration path and team ID. Compare estimated cost, input/output/cache tokens, and policy outcomes. A lower output cap does not guarantee a fixed saving: accepted requests, model rates, and workload quality still matter. The <a href={sourcePath('docs/USAGE.md')}>usage reporting reference ↗</a> explains report coverage and cost estimates.</p>
        </section>
        <section className="docs-next"><div><span>Next guide</span><h2>Route your coding clients through Hormuz.</h2></div><CampaignLink className="button button-primary" href={sitePath('/guides/codex-claude-code-gateway/')}>Connect Codex & Claude Code →</CampaignLink></section>
      </article>
    </div>
  </PageFrame>;
}
