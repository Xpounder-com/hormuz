import assert from 'node:assert/strict';
import { chmod, link, lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { AGENT_CLI_CONFIG_LIMITS, dollarToMicrousd, loadAgentCLIConfig, resolveFolderContext, saveAgentCLIConfig, validateAgentCLIConfig } from '../src/terminal-config.ts';
import type { AgentCLIConfig } from '../src/terminal-config.ts';

async function fixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-terminal-config-')));
  const workspace = join(root, 'project'), stateRoot = join(root, 'private-state');
  await mkdir(workspace);
  return { root, workspace, stateRoot, cleanup: () => rm(root, { recursive: true, force: true }) };
}
function config(overrides: Partial<AgentCLIConfig> = {}): AgentCLIConfig {
  return { schemaVersion: 1, profileId: 'personal', engine: 'codex', model: 'fixture-model', allowanceMicrousd: 1_000_000, projectAllowanceMicrousd: 2_000_000, profileAllowanceMicrousd: 5_000_000, inputMicrousdPerMillion: 1_000_000, outputMicrousdPerMillion: 2_000_000, maxInputTokens: 20_000, maxOutputTokens: 1000, ...overrides };
}

test('private profile configuration survives restart and shares one profile state root across projects', async () => {
  const f = await fixture();
  try {
    const first = await resolveFolderContext(f.workspace, 'personal', f.stateRoot);
    assert.equal(await loadAgentCLIConfig(first), undefined);
    await writeFile(join(f.workspace, 'config.json'), JSON.stringify({ credential: 'ambient-workspace-content-must-not-be-loaded' }));
    assert.equal(await loadAgentCLIConfig(first), undefined);
    await saveAgentCLIConfig(first, config({ codexExecutable: '/usr/bin/true' }));
    const restarted = await resolveFolderContext(f.workspace, 'personal', f.stateRoot);
    assert.deepEqual(await loadAgentCLIConfig(restarted), config({ codexExecutable: '/usr/bin/true' }));
    for (const path of [f.stateRoot, join(f.stateRoot, 'profiles'), first.stateDirectory]) assert.equal((await lstat(path)).mode & 0o777, 0o700);
    assert.equal((await lstat(first.configPath)).mode & 0o777, 0o600);
    assert.deepEqual(await readdir(first.stateDirectory), ['config.json']);
    const otherWorkspace = join(f.root, 'other-project'); await mkdir(otherWorkspace);
    const second = await resolveFolderContext(otherWorkspace, 'personal', f.stateRoot);
    assert.equal(second.stateDirectory, first.stateDirectory);
    assert.equal(second.configPath, first.configPath);
    assert.notEqual(second.projectId, first.projectId);
    assert.deepEqual(await loadAgentCLIConfig(second), await loadAgentCLIConfig(first));
  } finally { await f.cleanup(); }
});

test('canonical folder identity is stable through symlink aliases and distinguishes same-named folders', async () => {
  const f = await fixture();
  try {
    const alias = join(f.root, 'folder-alias'); await symlink(f.workspace, alias);
    const original = await resolveFolderContext(f.workspace, 'personal', f.stateRoot);
    const aliased = await resolveFolderContext(alias, 'personal', f.stateRoot);
    assert.equal(aliased.workspace, original.workspace);
    assert.equal(aliased.projectId, original.projectId);
    assert.match(original.projectId, /^folder-project-[a-f0-9]{32}$/);
    const sameName = join(f.root, 'elsewhere', 'project'); await mkdir(sameName, { recursive: true });
    assert.notEqual((await resolveFolderContext(sameName, 'personal', f.stateRoot)).projectId, original.projectId);
  } finally { await f.cleanup(); }
});

test('profile configuration rejects cross-profile scope and forged paths without replacing the original', async () => {
  const f = await fixture();
  try {
    const personal = await resolveFolderContext(f.workspace, 'personal', f.stateRoot);
    const work = await resolveFolderContext(f.workspace, 'work', f.stateRoot);
    await saveAgentCLIConfig(personal, config());
    assert.equal(await loadAgentCLIConfig(work), undefined);
    await assert.rejects(saveAgentCLIConfig(personal, config({ profileId: 'work' })), { code: 'agent_cli_config_scope_denied' });
    await assert.rejects(loadAgentCLIConfig({ ...work, configPath: personal.configPath }), { code: 'agent_cli_state_path_denied' });
    await assert.rejects(loadAgentCLIConfig({ ...personal, projectId: 'foreign-project' }), { code: 'agent_cli_config_scope_denied' });
    assert.deepEqual(await loadAgentCLIConfig(personal), config());
    await writeFile(personal.configPath, JSON.stringify(config({ profileId: 'work' })), { mode: 0o600 });
    await assert.rejects(loadAgentCLIConfig(personal), { code: 'agent_cli_config_scope_denied' });
    await assert.rejects(saveAgentCLIConfig(personal, config()), { code: 'agent_cli_config_scope_denied' });
  } finally { await f.cleanup(); }
});

test('state inside a selected folder or home is denied before creating directories', async () => {
  const f = await fixture();
  try {
    const inside = join(f.workspace, 'must-not-be-created');
    await assert.rejects(resolveFolderContext(f.workspace, 'personal', inside), { code: 'agent_cli_state_inside_workspace' });
    await assert.rejects(lstat(inside), { code: 'ENOENT' });
    await assert.rejects(resolveFolderContext(f.workspace, 'personal', f.workspace), { code: 'agent_cli_state_inside_workspace' });
    await assert.rejects(resolveFolderContext(homedir(), 'personal'), error => (error as Error).message.includes('outside this folder'));
    const escape = join(f.root, 'state-link'); await symlink(f.workspace, escape);
    await assert.rejects(resolveFolderContext(f.workspace, 'personal', join(escape, 'nested')), { code: 'agent_cli_state_inside_workspace' });
    await assert.rejects(lstat(join(f.workspace, 'nested')), { code: 'ENOENT' });
    for (const profileId of ['../escape', '', '.', 'a'.repeat(65)]) await assert.rejects(resolveFolderContext(f.workspace, profileId, f.stateRoot), { code: 'invalid_agent_cli_profile' });
    await assert.rejects(resolveFolderContext(f.workspace, 'personal', 'relative-state'), { code: 'agent_cli_state_path_denied' });
  } finally { await f.cleanup(); }
});

test('symlink and hardlink config files and private directory replacements fail closed', async () => {
  const f = await fixture();
  try {
    const context = await resolveFolderContext(f.workspace, 'personal', f.stateRoot);
    const outside = join(f.root, 'outside.json'); await writeFile(outside, JSON.stringify(config()), { mode: 0o600 });
    await symlink(outside, context.configPath);
    await assert.rejects(loadAgentCLIConfig(context), { code: 'agent_cli_config_file_invalid' });
    await assert.rejects(saveAgentCLIConfig(context, config()), { code: 'agent_cli_config_file_invalid' });
    await rm(context.configPath); await link(outside, context.configPath);
    await assert.rejects(loadAgentCLIConfig(context), { code: 'agent_cli_config_file_invalid' });
    await assert.rejects(saveAgentCLIConfig(context, config()), { code: 'agent_cli_config_file_invalid' });
    assert.deepEqual(JSON.parse(await readFile(outside, 'utf8')), config());
    await rm(context.configPath); await saveAgentCLIConfig(context, config());
    await chmod(context.configPath, 0o644);
    await assert.rejects(loadAgentCLIConfig(context), { code: 'agent_cli_config_file_invalid' });
    await chmod(context.configPath, 0o600); await chmod(context.stateDirectory, 0o755);
    await assert.rejects(loadAgentCLIConfig(context), { code: 'agent_cli_private_state_required' });
    await rm(context.stateDirectory, { recursive: true }); await symlink(f.workspace, context.stateDirectory);
    await assert.rejects(loadAgentCLIConfig(context), { code: 'agent_cli_state_path_denied' });
    const rootLink = join(f.root, 'root-link'); await symlink(f.stateRoot, rootLink);
    await assert.rejects(resolveFolderContext(f.workspace, 'personal', rootLink), { code: 'agent_cli_state_path_denied' });
  } finally { await f.cleanup(); }
});

test('bounded config reads reject oversized, corrupt, invalid UTF-8, and secret-bearing files', async () => {
  const f = await fixture();
  try {
    const context = await resolveFolderContext(f.workspace, 'personal', f.stateRoot);
    await saveAgentCLIConfig(context, config());
    const file = await open(context.configPath, 'w');
    try { await file.truncate(AGENT_CLI_CONFIG_LIMITS.maxBytes + 1); } finally { await file.close(); }
    await assert.rejects(loadAgentCLIConfig(context), { code: 'agent_cli_config_too_large' });
    for (const text of ['{broken', Buffer.from([0xff]), JSON.stringify({ ...config(), cloudAllowed: true }), JSON.stringify({ ...config(), credential: 'private-token' })]) {
      await writeFile(context.configPath, text);
      await assert.rejects(loadAgentCLIConfig(context), { code: 'invalid_agent_cli_config' });
    }
    await assert.rejects(saveAgentCLIConfig(context, config()), { code: 'invalid_agent_cli_config' });
    assert.deepEqual(await readdir(context.stateDirectory), ['config.json']);
  } finally { await f.cleanup(); }
});

test('configuration requires explicit prices and finite ordered caps and never accepts authority or secret fields', () => {
  validateAgentCLIConfig(config());
  for (const invalid of [
    { ...config(), schemaVersion: 2 }, { ...config(), engine: 'other' }, { ...config(), model: 'line\nbreak' },
    { ...config(), model: 'x'.repeat(129) }, { ...config(), model: 'sk-ant-api03-thisIsASecretThatCannotBeWritten' },
    { ...config(), allowanceMicrousd: 0 }, { ...config(), allowanceMicrousd: NaN },
    { ...config(), allowanceMicrousd: 1.5 }, { ...config(), allowanceMicrousd: '1' },
    { ...config(), allowanceMicrousd: AGENT_CLI_CONFIG_LIMITS.maxAllowanceMicrousd + 1 },
    { ...config(), projectAllowanceMicrousd: 500_000 }, { ...config(), profileAllowanceMicrousd: 1_000_000 },
    { ...config(), inputMicrousdPerMillion: undefined }, { ...config(), outputMicrousdPerMillion: 0 },
    { ...config(), inputMicrousdPerMillion: AGENT_CLI_CONFIG_LIMITS.maxPriceMicrousdPerMillion + 1 },
    { ...config(), maxInputTokens: Infinity }, { ...config(), maxOutputTokens: AGENT_CLI_CONFIG_LIMITS.maxOutputTokens + 1 },
    { ...config(), cloudConsent: true }, { ...config(), grants: [] }, { ...config(), environment: {} },
    { ...config(), engine: 'claude', codexExecutable: '/usr/bin/true' },
  ]) assert.throws(() => validateAgentCLIConfig(invalid), { code: 'invalid_agent_cli_config' });
  for (const executable of ['relative', '/usr/bin/../bin/codex', '/usr/bin/codex\0']) assert.throws(() => validateAgentCLIConfig(config({ codexExecutable: executable })), { code: 'agent_cli_state_path_denied' });
});

test('USD conversion preserves exact microdollars and rejects ambiguous or overflowing amounts', () => {
  assert.equal(dollarToMicrousd('0'), 0);
  assert.equal(dollarToMicrousd('0.000001'), 1);
  assert.equal(dollarToMicrousd('1.01'), 1_010_000);
  assert.equal(dollarToMicrousd('12.345678'), 12_345_678);
  assert.equal(dollarToMicrousd('9007199254.740991'), Number.MAX_SAFE_INTEGER);
  assert.throws(() => dollarToMicrousd('9007199254.740992'), { code: 'dollar_amount_out_of_range' });
  for (const value of ['', ' 1', '1 ', '$1', '-1', '-0', '+1', '.1', '01', '1.', '1e-6', '1.0000001', 'NaN', 'Infinity']) assert.throws(() => dollarToMicrousd(value), { code: 'invalid_dollar_amount' });
});
