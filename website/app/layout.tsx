import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import { pageMetadata } from '../lib/metadata';
import { AdConsent } from './components/AdConsent';
import './globals.css';
import './landing.css';
import './interior.css';
import './brand.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = pageMetadata(
  'Hormuz — Open-source AI policy gateway',
  'Self-hosted policy, budgets, secret controls, and metadata-only evidence for AI coding clients. Apache-2.0. Try the provider-free demo.',
  '/',
);

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        <a className="skip-link" href="#content">Skip to content</a>
        <div className="landing-redesign">{children}<AdConsent /></div>
      </body>
    </html>
  );
}
