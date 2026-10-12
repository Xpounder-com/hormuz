import { CampaignLink } from './CampaignLink';
import { BrandLockup } from './Brand';
import { REPOSITORY, REPOSITORY_QUICKSTART, sitePath } from '../../lib/site.mjs';

const navigation = [
  { key: 'github', label: 'GitHub ↗', href: REPOSITORY },
  { key: 'docs', label: 'Docs', href: sitePath('/docs/') },
  { key: 'demo', label: 'Demo', href: sitePath('/demo/') },
  { key: 'work', label: 'AI Work', href: sitePath('/work/') },
  { key: 'plans', label: 'Hosting', href: sitePath('/plans/') },
];

export function SiteHeader({
  active,
  overlay = false,
}: {
  active: string;
  overlay?: boolean;
}) {
  const navigationActive = active === 'enterprise' ? 'plans' : active;
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
              href={item.href}
              className={navigationActive === item.key ? 'nav-active' : undefined}
              aria-current={active === item.key ? 'page' : navigationActive === item.key ? 'location' : undefined}
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
                href={item.href}
                aria-current={active === item.key ? 'page' : navigationActive === item.key ? 'location' : undefined}
              >
                {item.label}
              </CampaignLink>
            ))}
            <CampaignLink href={sitePath('/contact/?interest=work')}>Contact</CampaignLink>
          </div>
        </details>

        <a
          className="nav-cta"
          href={REPOSITORY_QUICKSTART}
        >
          Try free on GitHub
          <span aria-hidden="true">→</span>
        </a>
      </nav>
    </header>
  );
}
