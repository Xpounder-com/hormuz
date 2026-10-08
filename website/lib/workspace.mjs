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
