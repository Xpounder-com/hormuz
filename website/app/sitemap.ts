import type { MetadataRoute } from 'next';
import { siteUrl } from '../lib/site.mjs';

export const dynamic = 'force-static';
export default function sitemap(): MetadataRoute.Sitemap {
  return ['/', '/docs/', '/demo/', '/integrations/', '/enterprise/', '/security/', '/resources/', '/contact/', '/privacy/'].map(path => ({ url: siteUrl(path) }));
}
