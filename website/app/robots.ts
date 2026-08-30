import type { MetadataRoute } from 'next';
import { siteUrl } from '../lib/site.mjs';

export const dynamic = 'force-static';
// Project Pages cannot control the origin-root robots.txt. Per-page robot
// metadata and the project sitemap are also provided; see website/README.md.
export default function robots(): MetadataRoute.Robots {
  return { rules: { userAgent: '*', allow: '/hormuz/' }, sitemap: siteUrl('/sitemap.xml') };
}
