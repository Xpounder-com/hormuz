// Teaching calculations only. No provider calls, pricing feed, or customer telemetry.
export const tokenKinds = ['uncached_input', 'cache_read_input', 'output'];
export const tokenLabels = ['Uncached input', 'Cached input', 'Output'];
export function summarizeUsage(fixture, team = 'All teams', model = 'All models') {
  const rows = fixture.usage.filter(row => (team === 'All teams' || row.team === team) && (model === 'All models' || row.model === model));
  const categories = tokenKinds.map((kind, index) => ({
    kind, label: tokenLabels[index],
    tokens: rows.reduce((sum, row) => sum + row[kind], 0),
    cost: rows.reduce((sum, row) => sum + row[kind] / 1000000 * Number(fixture.rates_usd_per_million_tokens[row.model][kind]), 0),
  }));
  return { categories, tokens: categories.reduce((sum, row) => sum + row.tokens, 0), cost: categories.reduce((sum, row) => sum + row.cost, 0) };
}
export function budgetBalance(budget, committed, pending, uncertain) {
  return Math.round((budget - committed - pending - uncertain) * 100) / 100;
}
export function monthPace(committed, elapsedDays, monthDays) {
  if (elapsedDays <= 0 || elapsedDays > monthDays) throw new Error('Invalid example period');
  return committed / elapsedDays * monthDays;
}
export function policyTemplate(name) {
  if (!['standard', 'strict', 'lockdown'].includes(name)) throw new Error('Unknown template');
  return { template: name, budget: 45000, outputCap: name === 'strict' ? 4000 : 16000, clientAllowed: name !== 'lockdown', advanced: name !== 'lockdown', standard: name !== 'lockdown', secrets: name === 'standard' ? 'redact' : 'deny' };
}
export function evaluateExamplePolicy(policy, { model = 'Advanced', nearLimit = false, secret = false } = {}) {
  if (!Number.isFinite(policy.budget) || policy.budget < 0 || policy.budget > 83333.33 || !Number.isInteger(policy.outputCap) || policy.outputCap < 1 || policy.outputCap > 16000) return { decision: 'invalid', reason: 'Enter a team budget between $0 and $83,333.33 and an output cap between 1 and 16,000.', calls: 0, reservation: 0, available: 0, after: 0, outputCeiling: 0 };
  const outputCeiling = policy.outputCap / 1000000 * (model === 'Advanced' ? 40 : 5);
  // A fixed synthetic request whose input reservation is $0.06 at Advanced's invented rate.
  const reservation = (policy.outputCap * (model === 'Advanced' ? 40 : 5) + (model === 'Advanced' ? 60000 : 11250)) / 1000000;
  const occupied = nearLimit ? 44999.50 : 31500;
  const available = budgetBalance(policy.budget, occupied, 0, 0);
  const allowed = model === 'Advanced' ? policy.advanced : policy.standard;
  const reason = !policy.clientAllowed ? 'Codex is outside the approved client list.' : !allowed ? `${model} is outside the approved model list.` : secret && policy.secrets === 'deny' ? 'A configured secret was detected. The request stops here.' : reservation > available ? `The next reservation exceeds the available team budget.` : secret ? 'Configured secret redacted; the governed request can proceed.' : 'Approved client, model, output cap, and budget checks pass.';
  const decision = !policy.clientAllowed || !allowed || (secret && policy.secrets === 'deny') || reservation > available ? 'denied' : 'allowed';
  return { decision, reason, calls: decision === 'allowed' ? 1 : 0, reservation, available, after: decision === 'allowed' ? available - reservation : available, outputCeiling };
}
export function restorePathExample(compact) {
  const value = JSON.parse(compact);
  if (value.format !== 'hormuz-path-list-v1' || typeof value.prefix !== 'string' || !Array.isArray(value.suffixes) || !value.suffixes.every(s => typeof s === 'string')) throw new Error('Invalid teaching fixture');
  return value.suffixes.map(suffix => value.prefix + suffix).join('\n') + (value.trailing_newline ? '\n' : '');
}
