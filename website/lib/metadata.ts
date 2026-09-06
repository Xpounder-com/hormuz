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
    icons: {
      icon: [
        { url: sitePath('/favicon.ico?v=passage-h-1'), sizes: '16x16 32x32 48x48' },
        { url: sitePath('/brand/icons/forest/hormuz-forest-16.png'), type: 'image/png', sizes: '16x16' },
        { url: sitePath('/brand/icons/forest/hormuz-forest-32.png'), type: 'image/png', sizes: '32x32' },
        { url: sitePath('/icon.svg?v=passage-h-1'), type: 'image/svg+xml', sizes: 'any' },
      ],
      apple: [{ url: sitePath('/apple-touch-icon.png?v=passage-h-1'), sizes: '180x180' }],
    },
    robots: { index: true, follow: true },
  };
}
