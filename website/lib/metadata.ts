import type { Metadata } from 'next';
import { AUTHOR, sitePath, siteUrl } from './site.mjs';

export function pageMetadata(title: string, description: string, path: string): Metadata {
  return {
    title,
    description,
    authors: [{ name: AUTHOR }],
    alternates: { canonical: siteUrl(path) },
    openGraph: {
      title, description, url: siteUrl(path), type: 'website', siteName: 'Hormuz',
      images: [{ url: siteUrl('/og.png'), width: 1200, height: 630, alt: 'Hormuz — Give your team AI. Keep control.' }],
    },
    twitter: { card: 'summary_large_image', title, description, images: [siteUrl('/og.png')] },
    icons: { icon: sitePath('/icon.svg') },
    robots: { index: true, follow: true },
  };
}
