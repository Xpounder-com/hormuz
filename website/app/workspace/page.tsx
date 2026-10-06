import { pageMetadata } from '../../lib/metadata';
import { PageFrame, PageHero } from '../components/PageFrame';
import { sitePath } from '../../lib/site.mjs';
import { workspaceConfig } from '../../lib/workspace.mjs';

export const metadata = pageMetadata('Your workspace — Hormuz', 'Open your Hormuz workspace. Start with an included dashboard address and connect your own domain later.', '/workspace/');

export default function WorkspacePage() {
  const { dashboardUrl } = workspaceConfig();
  return <PageFrame active="workspace"><PageHero eyebrow="Hormuz workspace" title={<>Your workspace.<br /><em>Your address.</em></>}>
    <p>Sign in to open your dashboard. Your workspace comes with an address you can use immediately. Connecting your own domain is optional.</p>
    {dashboardUrl ? <a className="button landing-primary" href={dashboardUrl}>Open your workspace <span aria-hidden="true">→</span></a>
      : <><p>Workspace sign-in is being prepared. You can use Hormuz locally today.</p><a className="button landing-primary" href={sitePath('/docs/')}>Install Hormuz free →</a></>}
  </PageHero><section className="wide" style={{ paddingBlock: '3rem' }}><h2>A familiar address, when you want one.</h2><p>Keep the included Hormuz address, or connect a subdomain such as <code>ai.yourcompany.com</code> from your dashboard. Your workspace stays the same under either address.</p></section></PageFrame>;
}
