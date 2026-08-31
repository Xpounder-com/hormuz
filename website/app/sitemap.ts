import type { MetadataRoute } from 'next';
import { SITE_ROUTES, siteUrl } from '../lib/site.mjs';

export const dynamic = 'force-static';
export default function sitemap(): MetadataRoute.Sitemap {
  return SITE_ROUTES.map(path => ({ url: siteUrl(path) }));
}
