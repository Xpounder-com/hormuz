import { CampaignLink } from './CampaignLink';
import { BrandLockup } from './Brand';
import { sitePath } from '../../lib/site.mjs';

const navigation = [
  { key: 'platform', label: 'Product', href: '/' },
  { key: 'demo', label: 'Try Hormuz', href: '/demo/' },
  { key: 'docs', label: 'Install & docs', href: '/docs/' },
  { key: 'enterprise', label: 'Plans', href: '/enterprise/' },
  { key: 'security', label: 'Security', href: '/security/' },
  { key: 'resources', label: 'Resources', href: '/resources/' },
];

export function SiteHeader({
  active,
  overlay = false,
}: {
  active: string;
  overlay?: boolean;
}) {
  return (
    <header className={`nav-shell${overlay ? ' nav-shell-overlay' : ''}`}>
      <nav className="site-nav" aria-label="Primary navigation">
        <CampaignLink className="brand" href={sitePath('/')} aria-label="Hormuz home">
          <BrandLockup />
        </CampaignLink>

        <div className="nav-links">
          {navigation.map((item) => (
            <CampaignLink
              key={item.key}
              href={sitePath(item.href)}
              className={active === item.key ? 'nav-active' : undefined}
              aria-current={active === item.key ? 'page' : undefined}
            >
              {item.label}
            </CampaignLink>
          ))}
        </div>

        <details className="mobile-nav">
          <summary aria-label="Open navigation">Menu</summary>
          <div>
            {navigation.map((item) => (
              <CampaignLink
                key={item.key}
                href={sitePath(item.href)}
                aria-current={active === item.key ? 'page' : undefined}
              >
                {item.label}
              </CampaignLink>
            ))}
            <CampaignLink href={sitePath('/integrations/')} aria-current={active === 'integrations' ? 'page' : undefined}>Integrations</CampaignLink>
            <CampaignLink className="mobile-pilot-cta" href={sitePath('/docs/')}>Install free ↗</CampaignLink>
          </div>
        </details>

        <CampaignLink
          className="nav-cta"
          href={sitePath('/docs/')}
        >
          Install free
          <span aria-hidden="true">↗</span>
        </CampaignLink>
      </nav>
    </header>
  );
}
