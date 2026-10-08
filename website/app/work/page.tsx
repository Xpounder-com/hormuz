import { pageMetadata } from '../../lib/metadata';
import { PageFrame, PageHero } from '../components/PageFrame';
import { CampaignLink } from '../components/CampaignLink';
import { sitePath, sourcePath } from '../../lib/site.mjs';
import { workEntryConfig } from '../../lib/workspace.mjs';

export const metadata = pageMetadata('AI Work — Hormuz', 'Open the authenticated AI Work dashboard on your configured Hormuz gateway. Keep task data and credentials off the public website.', '/work/');

export default function WorkPage() {
  const { workUrl } = workEntryConfig();
  return <PageFrame active="work"><PageHero eyebrow="Your configured gateway" title={<>Your AI work.<br /><em>Your priorities.</em></>}>
    <p>Open the authenticated gateway to manage work, budgets, and cost or speed priorities. Your work records and credentials stay on that gateway.</p>
    <div className="hero-actions">{workUrl ? <a className="button landing-primary" href={workUrl} rel="noreferrer">Open AI Work <span aria-hidden="true">→</span></a> : <CampaignLink className="button landing-primary" href={sitePath('/contact/?interest=work')}>Discuss your workflow →</CampaignLink>}<CampaignLink className="button landing-secondary" href={sitePath('/docs/#quickstart')}>Run your own gateway →</CampaignLink></div>
  </PageHero><section className="section prose-section"><h2>Connect once. Keep your agents.</h2><ol className="numbered-list"><li>Your administrator confirms the supported client, protocol, model configuration, and budget scope.</li><li>Open <code>/work</code> on your organization’s gateway and sign in using its configured authentication.</li><li>Create a work record, select a priority and budget, and connect the supported agent requests to it.</li><li>Inspect attempts, usage, and observed outcome evidence as the work runs.</li></ol>
    {!workUrl && <div className="work-entry-note"><h3>Connect to your organization’s gateway.</h3><p>If you already operate Hormuz, use the AI Work address supplied by your administrator. Discuss your workflow to confirm compatibility and setup.</p><details className="disclosure"><summary>For site administrators</summary><p>Configure the public entry with <code>HORMUZ_DASHBOARD_ORIGIN</code> at website build time. The trusted HTTPS origin serves the authenticated <code>/work</code> dashboard.</p></details></div>}
    <p>This static entry page does not collect gateway tokens, provider keys, repository contents, or task descriptions. A website visit creates no workspace or paid subscription.</p><p>Gateway mechanics are demonstrated with provider-free runs. Production compatibility, model performance, and service activation must be qualified for your workflow.</p><div className="resource-actions"><a className="text-link" href={sourcePath('docs/AI_WORK_AGENT_INTEGRATION.md')}>Attach your supported agent to work ↗</a><CampaignLink className="text-link" href={sitePath('/evidence/')}>Inspect the mechanics evidence →</CampaignLink><CampaignLink className="text-link" href={sitePath('/plans/#cloud')}>Cloud activation and billing terms →</CampaignLink></div>
  </section></PageFrame>;
}
