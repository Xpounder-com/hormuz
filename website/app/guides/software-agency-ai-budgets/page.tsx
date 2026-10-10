import { CampaignLink } from '../../components/CampaignLink';
import { CodeBlock } from '../../components/CodeBlock';
import { PageFrame, PageHero } from '../../components/PageFrame';
import { TeamServiceNextStep } from '../../components/TeamServiceNextStep';
import { pageMetadata } from '../../../lib/metadata';
import { REPOSITORY, SOURCE_REVISION, sitePath, sourcePath } from '../../../lib/site.mjs';
import examples from '../../../lib/agency-budget-examples.json';

export const metadata = pageMetadata(
  'Team AI Budgets for Software Agencies: A Runnable Walkthrough | Hormuz',
  'Set AI spending limits for your agency, teams, and developers with runnable examples. Start free, ask about Cloud hosting, or get managed-site operating support.',
  '/guides/software-agency-ai-budgets/',
);

const revision = SOURCE_REVISION === 'main' ? '<REVIEWED_40_CHARACTER_COMMIT>' : SOURCE_REVISION;
const install = `git clone ${REPOSITORY}.git hormuz-agency-budget
cd hormuz-agency-budget
git checkout --detach ${revision}
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
hormuz policy demo`;

export default function AgencyBudgetsGuide() {
  return <PageFrame active="guides">
    <PageHero eyebrow="Software agency walkthrough / Team AI budgets" title={<>Give your agency<br /><span>an AI budget.</span></>}>
      <p>Set monthly AI spending limits for your agency, each team, and individual developers. See the estimated cost of requests routed through Hormuz while keeping your supported coding tools. Run the examples with free software, then choose whether to host the gateway yourself or ask about Cloud and managed-site support.</p>
      <div className="hero-actions"><a className="button button-primary" href="#free-setup">Run the free walkthrough ↓</a><a className="button button-ghost" href="#team-next">Choose your next step ↓</a></div>
      <p>For agency owners and technical leads. The local policy examples need no provider account, API key, or payment card.</p>
    </PageHero>
    <div className="docs-shell">
      <aside className="docs-sidebar" aria-label="Guide navigation"><div><strong>In this walkthrough</strong><a href="#scope">How the limits work</a><a href="#free-setup">Install free software</a><a href="#baseline">Set an agency budget</a><a href="#team-budget">Add a team limit</a><a href="#inspect">Inspect the change</a><a href="#evaluate">Connect and view usage</a><a href="#team-next">Free, Cloud, or managed?</a></div><div><strong>Go deeper</strong><CampaignLink href={sitePath('/guides/team-ai-budgets/')}>Cost and budget reference</CampaignLink><CampaignLink href={sitePath('/guides/codex-claude-code-gateway/')}>Coding-client setup</CampaignLink></div></aside>
      <article className="docs-content">
        <section id="scope" className="docs-section">
          <p className="section-label">01 / Agency, team, and developer limits</p><h2>One agency budget. Smaller limits inside it.</h2>
          <p>When several developers use AI coding tools, you need to know how much each team is using and where its allowance ends. Hormuz checks organization, team, and person limits before forwarding a governed request to your provider. Individual identities let you inspect captured usage by developer, team, coding client, and model.</p>
          <p>The runnable example uses the fictional <code>xpounder</code> organization and its <code>engineering</code> team. Use these sample amounts to learn the controls, then replace them with your agency’s approved allowances.</p>
          <div className="table-scroll"><table className="comparison-table"><caption>Illustrative limits for the current UTC calendar month</caption><thead><tr><th scope="col">Boundary</th><th scope="col">Example</th><th scope="col">What it means</th></tr></thead><tbody>
            <tr><th scope="row">Agency organization</th><td>$500 / month</td><td>Combined captured usage across its teams.</td></tr>
            <tr><th scope="row">Engineering team</th><td>$300 / month</td><td>Usage attributed to that team.</td></tr>
            <tr><th scope="row">Each person</th><td>$50 / month</td><td>A per-person allowance inside the organization and team limits.</td></tr>
            <tr><th scope="row">Engineering response</th><td>2,000 output tokens</td><td>The output cap for each governed request.</td></tr>
          </tbody></table></div>
          <p>These limits overlap. A person’s remaining allowance cannot override a team or organization limit. If Engineering has consumed $295 with no outstanding reservations, a request needing a $6 reservation exceeds its remaining $5. Requests in flight also reserve capacity.</p>
          <div className="docs-callout"><span aria-hidden="true">i</span><p><strong>Reading your costs:</strong> Hormuz estimates costs from the model rates you configure and the requests it captures. Your provider invoice remains the billing record. The reporting field <code>client</code> means a coding or API tool, such as Codex. For billing your agency’s customers, you still need your own project invoicing; tools that bypass Hormuz are outside these reports.</p></div>
        </section>
        <section id="free-setup" className="docs-section">
          <p className="section-label">02 / Install free software</p><h2>Try the policy workflow locally.</h2>
          <p>You need Git and Python 3.11+ on macOS or Linux. Start in a directory where <code>hormuz-agency-budget</code> does not already exist. The commands install the source version linked from this page. Installation downloads dependencies; the policy demo runs offline and removes its temporary state.</p>
          <CodeBlock label="Install and run the offline policy tour" code={install} />
          {SOURCE_REVISION === 'main' && <p>This local website build has no immutable source pin. Replace <code>&lt;REVIEWED_40_CHARACTER_COMMIT&gt;</code> with the reviewed full commit before running the install.</p>}
          <p>The tour shows policy creation, validation, comparison, and two request scenarios. Its example starts with zero usage. Keep this shell and virtual environment active for the next three steps.</p>
          <p>You will create local policy files before connecting a shared gateway. Your checkout, virtual environment, and files remain so you can inspect them; run <code>deactivate</code> when finished. <a href={sourcePath('SUPPORT.md')}>Supported platforms and troubleshooting ↗</a></p>
        </section>
        <section id="baseline" className="docs-section">
          <p className="section-label">03 / Set the agency budget</p><h2>Set $500 for the agency and $50 per developer.</h2>
          <p>Run this from <code>hormuz-agency-budget</code>. The supplied <a href={sourcePath('config.example.json')}>example configuration ↗</a> provides fictional identities and model settings; its rates are not a current provider price list. Local creation reads those settings without resolving provider credentials.</p>
          <CodeBlock label="Create the agency budget baseline" code={examples.create} />
          <p>Expect <code>policy created</code>. The standard template selects configured clients and models, secret redaction, and a 16,000-token organization output cap. The flags add the $500 organization and $50 per-person monthly limits.</p>
          <p>The template creates a new policy without copying existing team or person overrides. Check the complete file before using it on your gateway. To protect your files, this command refuses to overwrite <code>agency-baseline.json</code>; use new filenames when repeating the example.</p>
        </section>
        <section id="team-budget" className="docs-section">
          <p className="section-label">04 / Set the team budget</p><h2>Give Engineering a $300 limit.</h2>
          <p>Make a second policy with a $300 monthly Engineering budget and a 2,000-token response cap. The script uses the team already present in the fictional identity configuration; adding a policy entry does not enroll people.</p>
          <CodeBlock label="Add and validate the team budget" code={examples.team} />
          <p>Expect <code>policy valid</code> with one team scope. The script refuses to replace an existing <code>agency-team.json</code>. This validation is local and changes no active policy.</p>
        </section>
        <section id="inspect" className="docs-section">
          <p className="section-label">05 / Inspect before activation</p><h2>Read exactly what changed.</h2>
          <CodeBlock label="Compare the local policy files" code={examples.compare} />
          <p>Expect <code>identical: false</code> in the JSON, with two added paths: <code>policies.teams.engineering.monthly_budget_usd</code> and <code>policies.teams.engineering.max_output_tokens</code>. The comparison returns exit code <code>1</code> for a difference, <code>0</code> for identical policies, and <code>2</code> for an error. The shell block handles the expected difference explicitly and writes <code>agency-comparison.json</code> for review.</p>
          <p>This comparison reads your two local files without needing a usage database. Applying a policy to a shared gateway is a separate administrator step: request preview needs an existing usage store, and managed activation needs the gateway’s PostgreSQL policy setup. The <a href={sourcePath('docs/POLICY_CONTROL.md')}>policy administration guide ↗</a> covers preview, activation, and rollback.</p>
        </section>
        <section id="evaluate" className="docs-section">
          <p className="section-label">06 / Connect and view usage</p><h2>See what your team’s requests consume.</h2>
          <p>Your local policy files are ready to inspect. To use spending controls with your coding tools, your agency needs a configured gateway, individual developer identities, and a supported client connection. Start with approved non-production data while checking your setup.</p>
          <p>Check the supported client versions before connecting. The available live-provider tests cover OpenAI on v1.2.0; live Claude Code and Anthropic use has not been verified on the newer source version. The offline examples above test policy behavior without testing your provider connection. <CampaignLink href={sitePath('/docs/#downloads')}>Choose a source version →</CampaignLink> · <CampaignLink href={sitePath('/integrations/')}>Check client compatibility →</CampaignLink></p>
          <ol className="numbered-list">
            <li>Configure your gateway with developer identities, team membership, approved models, and current model rates. Keep provider credentials on the gateway.</li>
            <li>As the gateway administrator, compare and preview the full policy, then activate it using the policy administration guide.</li>
            <li>Send a small request approved by your agency through your own provider account. Check its result and developer/team attribution. Live provider calls may be billed.</li>
            <li>Test a request that the policy should deny. Check that Hormuz denies it without forwarding it to the provider.</li>
          </ol>
          <p>Once your gateway has captured requests, use these reports to see Engineering’s usage by team, developer, and model. Replace the configuration path and team ID with your gateway’s values:</p>
          <CodeBlock label="Review captured agency usage" code={examples.report} />
          <p>Reports cover the current UTC calendar month. If a report is empty, check whether the gateway captured any requests, whether the developer belongs to the selected team, and whether the tool is using Hormuz. Compare cost estimates with your provider bill and review the work itself when adjusting limits. <a href={sourcePath('docs/USAGE.md')}>Reporting definitions ↗</a></p>
        </section>
        <TeamServiceNextStep />
        <section className="docs-section">
          <h2>What happens when you ask about hosting or support?</h2>
          <p>Tell Hormuz which tools and providers your agency uses, your team size, and expected traffic. We confirm compatibility, service coverage, capacity, and an activation date with you before you subscribe. Sending an inquiry takes no payment. Monthly billing starts at checkout; your workspace is activated separately after the agreed setup and payment are verified.</p>
          <p>A managed site includes its Cloud workspace and one hour of remote assistance per billing month. It covers the stated operating service for one agreed gateway deployment. Additional development or onboarding needs a separate scope. <CampaignLink href={sitePath('/plans/#managed')}>Managed-site coverage and support terms →</CampaignLink></p>
          <p>If your team can host, update, and operate the gateway, you can continue with free software. You pay your model providers and cover your own infrastructure costs.</p>
        </section>
        <section className="docs-next"><div><span>Next step</span><h2>Get help with your agency’s setup.</h2></div><CampaignLink className="button button-primary" href={sitePath('/contact/?interest=review')}>Ask about your agency’s setup →</CampaignLink></section>
      </article>
    </div>
  </PageFrame>;
}
