import { CampaignLink } from '../../components/CampaignLink';
import { CodeBlock } from '../../components/CodeBlock';
import { PageFrame, PageHero } from '../../components/PageFrame';
import { TeamServiceNextStep } from '../../components/TeamServiceNextStep';
import { pageMetadata } from '../../../lib/metadata';
import { REPOSITORY, SOURCE_REVISION, sitePath, sourcePath } from '../../../lib/site.mjs';
import examples from '../../../lib/agency-budget-examples.json';

export const metadata = pageMetadata(
  'Team AI Budgets for Software Agencies: A Runnable Walkthrough | Hormuz',
  'Create and inspect organization, team, and person AI spending limits locally. Evaluate one agency workflow, then choose free software, Cloud, or a managed site.',
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
      <p>Keep your supported coding tools. Give the agency owner a spending boundary and the technical lead a policy they can inspect. Start with free software, then choose hosting or operating help when your workflow is qualified.</p>
      <div className="hero-actions"><a className="button button-primary" href="#free-setup">Run the free walkthrough ↓</a><a className="button button-ghost" href="#team-next">Choose your next step ↓</a></div>
      <p>For agency owners and technical leads. The local policy examples need no provider account, API key, or payment card.</p>
    </PageHero>
    <div className="docs-shell">
      <aside className="docs-sidebar" aria-label="Guide navigation"><div><strong>In this walkthrough</strong><a href="#scope">Pick one workflow</a><a href="#free-setup">Install free software</a><a href="#baseline">Set an agency budget</a><a href="#team-budget">Add a team limit</a><a href="#inspect">Inspect the change</a><a href="#evaluate">Evaluate with your team</a><a href="#team-next">Free, Cloud, or managed?</a></div><div><strong>Go deeper</strong><CampaignLink href={sitePath('/guides/team-ai-budgets/')}>Cost and budget reference</CampaignLink><CampaignLink href={sitePath('/guides/codex-claude-code-gateway/')}>Coding-client setup</CampaignLink></div></aside>
      <article className="docs-content">
        <section id="scope" className="docs-section">
          <p className="section-label">01 / Agree the boundary</p><h2>Start with one team and one workflow.</h2>
          <p>Choose a non-production coding workflow, name its operator, and agree a monthly allowance with the agency owner. Keep individual identities so the technical lead can check which person, team, coding client, and model generated each captured request.</p>
          <p>This walkthrough uses the repository’s fictional <code>xpounder</code> organization and existing <code>engineering</code> team. The amounts below are teaching values, not a recommended agency allowance or measured savings. Replace them after reviewing your workload and approved budget.</p>
          <div className="table-scroll"><table className="comparison-table"><caption>Illustrative limits for the current UTC calendar month</caption><thead><tr><th scope="col">Boundary</th><th scope="col">Example</th><th scope="col">What it means</th></tr></thead><tbody>
            <tr><th scope="row">Agency organization</th><td>$500 / month</td><td>Combined captured usage across its teams.</td></tr>
            <tr><th scope="row">Engineering team</th><td>$300 / month</td><td>Usage attributed to that team.</td></tr>
            <tr><th scope="row">Each person</th><td>$50 / month</td><td>A per-person allowance inside the organization and team limits.</td></tr>
            <tr><th scope="row">Engineering response</th><td>2,000 output tokens</td><td>The output cap for each governed request.</td></tr>
          </tbody></table></div>
          <p>These limits overlap. A person’s remaining allowance cannot override a team or organization limit. If Engineering has consumed $295 with no outstanding reservations, a request needing a $6 reservation exceeds its remaining $5. Requests in flight also reserve capacity.</p>
          <div className="docs-callout"><span aria-hidden="true">i</span><p><strong>Agency billing boundary:</strong> gateway cost estimates use configured model rates and cover captured requests only. Reconcile them with provider invoices. The reporting field <code>client</code> means a coding or API client, such as Codex; it is not your agency’s business customer. This walkthrough does not produce client-project invoices or track tools that bypass the gateway.</p></div>
        </section>
        <section id="free-setup" className="docs-section">
          <p className="section-label">02 / Install free software</p><h2>Try the policy workflow locally.</h2>
          <p>Use Git and Python 3.11+ on macOS or Linux, with a new checkout directory. These commands use the same reviewed source revision as this page, rather than a package-registry name. Installation downloads dependencies; <code>hormuz policy demo</code> itself makes no network or provider calls and removes its temporary state.</p>
          <CodeBlock label="Install and run the offline policy tour" code={install} />
          {SOURCE_REVISION === 'main' && <p>This local website build has no immutable source pin. Replace <code>&lt;REVIEWED_40_CHARACTER_COMMIT&gt;</code> with the reviewed full commit before running the install.</p>}
          <p>The tour should report successful policy creation, validation, comparison, and two evaluated scenarios. It demonstrates model access and output caps with zero starting usage; it does not simulate an agency’s actual spending. Keep this shell and virtual environment active for the next three steps.</p>
          <p>The examples below create local policy files. They do not start a shared gateway or activate a policy. Your checkout, virtual environment, and these files remain for inspection; run <code>deactivate</code> when finished. <a href={sourcePath('SUPPORT.md')}>Check supported platforms and troubleshooting ↗</a></p>
        </section>
        <section id="baseline" className="docs-section">
          <p className="section-label">03 / Set the agency budget</p><h2>Create a complete baseline.</h2>
          <p>Run this from <code>hormuz-agency-budget</code>. The supplied <a href={sourcePath('config.example.json')}>example configuration ↗</a> provides fictional identities and model settings; its rates are not a current provider price list. Local creation reads those settings without resolving provider credentials.</p>
          <CodeBlock label="Create the agency budget baseline" code={examples.create} />
          <p>Expect <code>policy created</code>. The standard template selects configured clients and models, secret redaction, and a 16,000-token organization output cap. The flags add the $500 organization and $50 per-person monthly limits.</p>
          <p>Templates do not copy existing team or person overrides. Review the complete candidate before using it in any deployment. Creation refuses to overwrite <code>agency-baseline.json</code>; use a fresh checkout or new filenames if repeating this example.</p>
        </section>
        <section id="team-budget" className="docs-section">
          <p className="section-label">04 / Tighten the team boundary</p><h2>Add Engineering’s allowance.</h2>
          <p>Make a second policy with a $300 monthly Engineering budget and a 2,000-token response cap. The script uses the team already present in the fictional identity configuration; adding a policy entry does not enroll people.</p>
          <CodeBlock label="Add and validate the team budget" code={examples.team} />
          <p>Expect <code>policy valid</code> with one team scope. The script refuses to replace an existing <code>agency-team.json</code>. This validation is local and changes no active policy.</p>
        </section>
        <section id="inspect" className="docs-section">
          <p className="section-label">05 / Inspect before activation</p><h2>Read exactly what changed.</h2>
          <CodeBlock label="Compare the local policy files" code={examples.compare} />
          <p>Expect <code>identical: false</code> in the JSON, with two added paths: <code>policies.teams.engineering.monthly_budget_usd</code> and <code>policies.teams.engineering.max_output_tokens</code>. The comparison returns exit code <code>1</code> for a difference, <code>0</code> for identical policies, and <code>2</code> for an error. The shell block handles the expected difference explicitly and writes <code>agency-comparison.json</code> for review.</p>
          <p>Both files and the baseline are local, using the example SQLite configuration. No usage database is needed for this comparison. Request preview needs an existing usage store; managed activation requires authorized policy administration and the deployment’s PostgreSQL control setup. Follow the <a href={sourcePath('docs/POLICY_CONTROL.md')}>compare, preview, guarded apply, and rollback reference ↗</a> before changing shared policy.</p>
        </section>
        <section id="evaluate" className="docs-section">
          <p className="section-label">06 / Evaluate one real workflow</p><h2>Give the team seven days and a checklist.</h2>
          <p>After the local tour, agree a small, self-operated evaluation with your technical lead. Define the workload, operator, allowance, deadline, and acceptable request outcome. Use approved non-production data and check your exact client, protocol, model, streaming, and tool behavior before routing agency work.</p>
          <p>The published core release has provider-free checks. Maintained live-provider evidence remains the v1.2.0 OpenAI-only baseline; same-candidate Claude Code and Anthropic live qualification remains open. The local walkthrough does not qualify your provider or production deployment. <CampaignLink href={sitePath('/docs/#downloads')}>Inspect release boundaries →</CampaignLink> · <CampaignLink href={sitePath('/integrations/')}>Check the supported route →</CampaignLink></p>
          <ol className="numbered-list">
            <li>Configure the actual gateway, unique identities, team mapping, approved models, and current rates. Keep provider credentials on the gateway and follow your agency’s data-handling approval.</li>
            <li>Have the authorized administrator compare and preview the complete policy, then activate the agreed version using the deployment’s guarded workflow.</li>
            <li>Make one small, customer-approved request using your own provider account, then test one agreed denial. Confirm the allowed result, expected attribution, and no provider forwarding for the denial. Live calls may be billed.</li>
            <li>Review captured usage with the operator and decide whether the control addresses your agency’s problem. Record gaps and the time spent operating it before choosing a paid service.</li>
          </ol>
          <p>Run these reports on an already configured gateway with a usage store and captured requests. Replace the path and team ID with your deployment’s values; these are not the offline setup commands:</p>
          <CodeBlock label="Review captured agency usage" code={examples.report} />
          <p>Reports cover the current UTC calendar month. Empty output can mean no captured requests, a different team mapping, or traffic bypassing Hormuz. Reconcile configured-rate estimates with your provider bill; a lower output cap does not establish a saving or acceptable work quality. <a href={sourcePath('docs/USAGE.md')}>Read reporting definitions ↗</a></p>
        </section>
        <TeamServiceNextStep />
        <section className="docs-section">
          <h2>Bring a small operating brief.</h2>
          <p>For a Cloud or managed-site discussion, bring your team size, exact tools and providers, expected traffic, chosen budget, operating owner, and evaluation findings. Agree service scope, prerequisites, capacity, and activation date before checkout. An inquiry is not activation; successful payment and service activation are separate steps.</p>
          <p>A managed site includes its associated Cloud workspace and one hour of remote assistance per billing month under the stated support terms. Choose it for the defined operating service around one agreed gateway deployment. Additional development or onboarding needs a separate agreed scope. <CampaignLink href={sitePath('/plans/#managed')}>Read managed-site coverage and exclusions →</CampaignLink></p>
          <p>You can keep the free software when your agency can host, update, and operate it. Provider usage and your own infrastructure costs remain separate from the $0 software license.</p>
        </section>
        <section className="docs-next"><div><span>Next step</span><h2>Review your agency’s workflow.</h2></div><CampaignLink className="button button-primary" href={sitePath('/contact/?interest=review')}>Discuss agency setup →</CampaignLink></section>
      </article>
    </div>
  </PageFrame>;
}
