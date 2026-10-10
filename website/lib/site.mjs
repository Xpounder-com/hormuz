export const BASE_PATH = '';
export const SITE_ORIGIN = 'https://usehormuz.github.io';
export const LEGACY_BASE_PATH = '/hormuz';
export const SITE_ROUTES = Object.freeze(['/', '/plans/', '/docs/', '/demo/', '/integrations/', '/enterprise/', '/security/', '/resources/', '/contact/', '/privacy/', '/brand/', '/workspace/', '/work/', '/evidence/', '/guides/team-ai-budgets/', '/guides/codex-claude-code-gateway/', '/guides/software-agency-ai-budgets/']);
export const REPOSITORY = 'https://github.com/Xpounder-com/hormuz';
export const AUTHOR = 'Mehrdad Zaker';
export const CONTACT_EMAIL = 'mehrdadz@neuralint.io';
export const OWNER_NAME = 'Neuralint';
export const OWNER_URL = 'https://neuralint.io';
export const SOURCE_VERSION = 'v1.8.0';
export const OCI_VERSION = 'v1.8.0';
export const MACOS_VERSION = 'v1.8.0';

/** Publication links follow the same immutable revision as the exported site. */
export function resolveSourceRevision(value) {
  if (value === undefined || value === '') return 'main';
  if (typeof value !== 'string' || value.length !== 40 || !/^[a-f0-9]{40}$/.test(value)) {
    throw new Error('Expected a full lowercase source commit SHA');
  }
  return value;
}
export const SOURCE_REVISION = resolveSourceRevision(process.env.NEXT_PUBLIC_HORMUZ_SOURCE_REVISION);

/** Keep native anchors and metadata aligned with the canonical root site. */
export function sitePath(path = '/') {
  if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Expected a local absolute path');
  return `${BASE_PATH}${path}`;
}

export function siteUrl(path = '/') {
  return `${SITE_ORIGIN}${sitePath(path)}`;
}

export function sourcePath(path) {
  return `${REPOSITORY}/blob/${SOURCE_REVISION}/${path}`;
}
