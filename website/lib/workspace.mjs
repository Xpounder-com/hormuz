// Public browser destination only. Pages remains the entry point; the hosted
// origin serves authenticated workspaces. Never put credentials in this config.
export function workspaceConfig(env = process.env) {
  const value = env.NEXT_PUBLIC_HORMUZ_DASHBOARD_ORIGIN || '';
  if (!value) return { dashboardUrl: null };
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash || url.hostname.endsWith('.github.io')) {
    throw new Error('Workspace dashboard requires a hosted HTTPS origin');
  }
  return { dashboardUrl: `${url.origin}/workspace` };
}
