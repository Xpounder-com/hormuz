import { parseArgs } from 'node:util';
import { SITE_ORIGIN, SITE_ROUTES } from '../lib/site.mjs';

const { values } = parseArgs({ options: {
  source: { type: 'string', default: 'x' },
  medium: { type: 'string', default: 'paid_social' },
  campaign: { type: 'string' }, content: { type: 'string' },
  path: { type: 'string', default: '/' },
} });
if (!SITE_ROUTES.includes(values.path)) throw new Error('Use a canonical website route, such as /, /demo/, or /contact/.');
const url = new URL(values.path, SITE_ORIGIN);
for (const field of ['source', 'medium', 'campaign', 'content']) {
  if (!/^[a-zA-Z0-9_.-]{1,64}$/.test(values[field] || '')) throw new Error(`Provide --${field} with a campaign label of 1–64 letters, digits, dots, underscores, or hyphens.`);
  url.searchParams.set(`utm_${field}`, values[field]);
}
process.stdout.write(`${url.href}\n`);
