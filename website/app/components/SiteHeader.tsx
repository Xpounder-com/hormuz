import { CampaignLink } from './CampaignLink';
import { BrandLockup } from './Brand';
import { sitePath } from '../../lib/site.mjs';

const navigation = [
  { key: 'docs', label: 'Docs', href: '/docs/' },
  { key: 'plans', label: 'Pricing', href: '/plans/' },
  { key: 'work', label: 'AI Work', href: '/work/' },
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
              href={sitePath(item.href)}
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
                href={sitePath(item.href)}
                aria-current={active === item.key ? 'page' : navigationActive === item.key ? 'location' : undefined}
              >
                {item.label}
              </CampaignLink>
            ))}
            <CampaignLink href={sitePath('/contact/?interest=work')}>Contact</CampaignLink>
          </div>
        </details>

        <CampaignLink
          className="nav-cta"
          href={sitePath('/docs/')}
        >
          Start free
          <span aria-hidden="true">→</span>
        </CampaignLink>
      </nav>
    </header>
  );
}
