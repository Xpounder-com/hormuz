// Public browser destination only. Pages remains the entry point; the hosted
// origin serves authenticated workspaces. Never put credentials in this config.
function dashboardOrigin(env) {
  const value = env.HORMUZ_DASHBOARD_ORIGIN || env.NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN || '';
  if (env.HORMUZ_DASHBOARD_ORIGIN && env.NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN && env.HORMUZ_DASHBOARD_ORIGIN !== env.NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN) throw new Error('Conflicting dashboard origins');
  if (!value) return null;
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash || url.hostname.endsWith('.github.io')) {
    throw new Error('Workspace dashboard requires a hosted HTTPS origin');
  }
  return url.origin;
}

export function workspaceConfig(env = process.env) {
  const origin = dashboardOrigin(env);
  return { dashboardUrl: typeof origin === 'string' ? `${origin}/workspace` : null };
}

export function workEntryConfig(env = process.env) {
  const origin = dashboardOrigin(env);
  return { workUrl: typeof origin === 'string' ? `${origin}/work` : null };
}

const ACQUISITION_TAGS = Object.freeze(['utm_source', 'utm_medium', 'utm_campaign', 'utm_content']);

/** Source labels are optional and bounded; private identifiers never enter a URL. */
export function workAcquisitionLabels(search = '') {
  const params = new URLSearchParams(search);
  const labels = {};
  for (const key of ACQUISITION_TAGS) {
    const values = params.getAll(key);
    if (values.length === 1 && /^[a-zA-Z0-9_.-]{1,64}$/.test(values[0])) labels[key] = values[0];
  }
  return labels;
}

export function workAcquisitionLink(workUrl, search = '', consented = false) {
  const url = new URL(workUrl);
  const approved = workEntryConfig({ HORMUZ_DASHBOARD_ORIGIN: url.origin }).workUrl;
  if (url.href !== approved) throw new Error('Expected the configured gateway work entry');
  const labels = workAcquisitionLabels(search);
  if (consented !== true || Object.keys(labels).length === 0) return approved;
  url.pathname = '/work/acquisition';
  for (const [key, value] of Object.entries(labels)) url.searchParams.set(key, value);
  return url.href;
}
