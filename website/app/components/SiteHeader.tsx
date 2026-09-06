import { CampaignLink } from './CampaignLink';
import { BrandLockup } from './Brand';
import { sitePath } from '../../lib/site.mjs';

const navigation = [
  { key: 'platform', label: 'Platform', href: '/' },
  { key: 'demo', label: 'Demo', href: '/demo/' },
  { key: 'docs', label: 'Docs', href: '/docs/' },
  { key: 'enterprise', label: 'Pricing', href: '/enterprise/' },
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
        <a className="brand" href={sitePath('/')} aria-label="Hormuz home">
          <BrandLockup />
        </a>

        <div className="nav-links">
          {navigation.map((item) => (
            <a
              key={item.key}
              href={sitePath(item.href)}
              className={active === item.key ? 'nav-active' : undefined}
              aria-current={active === item.key ? 'page' : undefined}
            >
              {item.label}
            </a>
          ))}
        </div>

        <details className="mobile-nav">
          <summary aria-label="Open navigation">Menu</summary>
          <div>
            {navigation.map((item) => (
              <a
                key={item.key}
                href={sitePath(item.href)}
                aria-current={active === item.key ? 'page' : undefined}
              >
                {item.label}
              </a>
            ))}
            <a href={sitePath('/integrations/')} aria-current={active === 'integrations' ? 'page' : undefined}>Integrations</a>
            <CampaignLink className="mobile-pilot-cta" href={sitePath('/contact/?interest=review')}>Get a free review ↗</CampaignLink>
          </div>
        </details>

        <CampaignLink
          className="nav-cta"
          href={sitePath('/contact/?interest=review')}
        >
          Get a free review
          <span aria-hidden="true">↗</span>
        </CampaignLink>
      </nav>
    </header>
  );
}
