import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, realpath, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseCLIArguments, configureCLI } from '../src/agent-command.ts';

const configured = () => configureCLI({ model: 'fixture-model', 'input-rate': '2.50', 'output-rate': '10' }, 'default');
test('CLI preserves literal prompts, spaces and shell metacharacters without executing them', () => {
  const tick = String.fromCharCode(96);
  const prompt = 'Inspect "$(touch should-not-exist)" and ' + tick + 'echo literal' + tick + ' in this folder.';
  assert.equal(parseCLIArguments(['--prompt', prompt]).prompt, prompt);
  assert.equal(parseCLIArguments(['--', '--literal', 'words with spaces']).prompt, '--literal words with spaces');
  assert.equal(parseCLIArguments(['--cwd=/folder with spaces', '--resume', 'last']).flags.cwd, '/folder with spaces');
});
test('first-run wizard EOF or stream error cancels its question without saving partial configuration', async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-cli-wizard-')));
  const workspace = join(root, 'folder'); await mkdir(workspace);
  const state = join(root, 'private-state');
  const module = pathToFileURL(fileURLToPath(new URL('../src/agent-command.ts', import.meta.url))).href;
  const script = 'if(process.argv[4]==="error"){const {PassThrough}=await import("node:stream");Object.defineProperty(process,"stdin",{value:new PassThrough()});} Object.defineProperty(process.stdin,"isTTY",{value:true}); Object.defineProperty(process.stdout,"isTTY",{value:true}); const m=await import(process.argv[1]); const pending=m.main(["init","--cwd",process.argv[2],"--state-root",process.argv[3]]); if(process.argv[4]==="error")setTimeout(()=>process.stdin.emit("error",new Error("fixture disconnect")),50); process.exitCode=await pending;';
  try {
    for (const fixture of [{ input: '', mode: 'eof' }, { input: 'codex\n', mode: 'eof' }, { input: '', mode: 'error' }]) {
      const child = spawnSync(process.execPath, ['--input-type=module', '-e', script, module, workspace, state, fixture.mode], { input: fixture.input, encoding: 'utf8', timeout: 5000, maxBuffer: 64 * 1024 });
      assert.equal(child.status, 130, child.stderr);
      assert.match(child.stderr, /configuration_cancelled/);
      await assert.rejects(access(join(state, 'profiles', 'default', 'config.json')));
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});
test('CLI rejects ambiguous, unknown, duplicate and unbounded arguments', () => {
  for (const argv of [['--yes'], ['--engine'], ['--model=a', '--model=b'], ['--resume', 'last', 'also plan'], ['--prompt', 'one', 'two'], ['doctor', 'prompt'], ['--config', 'ambient.json'], ['--prompt', 'a'.repeat(65537)]]) assert.throws(() => parseCLIArguments(argv));
  assert.throws(() => parseCLIArguments(Array(129).fill('x')));
});
test('CLI help and version are explicit offline actions', () => {
  assert.equal(parseCLIArguments(['--help']).command, 'help');
  assert.equal(parseCLIArguments(['--version']).command, 'version');
  assert.equal(parseCLIArguments(['init', '--model', 'fixture']).command, 'init');
});
test('explicit model rates are exact and default allowances remain ordered', () => {
  const config = configured();
  assert.equal(config.inputMicrousdPerMillion, 2_500_000);
  assert.equal(config.outputMicrousdPerMillion, 10_000_000);
  assert.equal(config.allowanceMicrousd, 1_000_000);
  assert.equal(config.projectAllowanceMicrousd, 5_000_000);
  assert.equal(config.profileAllowanceMicrousd, 10_000_000);
  assert.throws(() => configureCLI({ model: 'fixture' }, 'default'));
});
test('changing provider model requires explicit rates and cannot inherit another engine executable', () => {
  const config = { ...configured(), codexExecutable: '/usr/bin/fixture-codex' };
  assert.throws(() => configureCLI({ model: 'different' }, 'default', config), /new_model_requires_explicit_rates/);
  assert.throws(() => configureCLI({ engine: 'claude' }, 'default', config), /new_model_requires_explicit_rates/);
  const changed = configureCLI({ engine: 'claude', model: 'claude-fixture', 'input-rate': '1', 'output-rate': '2' }, 'default', config);
  assert.equal(changed.engine, 'claude'); assert.equal(changed.codexExecutable, undefined);
});
test('invalid token bounds, allowance ordering and model configuration fail before launch', () => {
  const cases: Array<Record<string, string>> = [{ budget: '6' }, { 'max-input-tokens': '-1' }, { 'max-output-tokens': '100001' }, { model: 'untrusted\nmodel' }, { 'input-rate': '0' }, { 'profile-budget': '4' }];
  for (const overrides of cases) assert.throws(() => configureCLI({ model: 'fixture', 'input-rate': '1', 'output-rate': '2', ...overrides }, 'default'));
});
test('real entry supports private offline setup/doctor/list in a folder without model calls or key disclosure', async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'hormuz-cli-command-')));
  const workspace = join(root, 'folder with spaces'); await mkdir(workspace);
  const state = join(root, 'private-state');
  const entry = fileURLToPath(new URL('../bin/hormuz-agent.mjs', import.meta.url));
  const env = { ...process.env, OPENAI_API_KEY: 'synthetic-doctor-only-secret', ANTHROPIC_API_KEY: '' };
  const call = (argv: string[]) => spawnSync(process.execPath, [entry, ...argv], { cwd: workspace, env, encoding: 'utf8', timeout: 10000, maxBuffer: 256 * 1024 });
  try {
    assert.equal(call(['--help']).status, 0);
    assert.equal(call(['--version']).status, 0);
    const init = call(['init', '--state-root', state, '--model', 'fixture-model', '--input-rate', '1', '--output-rate', '2']);
    assert.equal(init.status, 0, init.stderr);
    const config = await readFile(join(state, 'profiles', 'default', 'config.json'), 'utf8');
    assert.equal(config.includes(env.OPENAI_API_KEY), false);
    const doctor = call(['doctor', '--state-root', state]);
    assert.ok(doctor.status === 0 || doctor.status === 2, doctor.stderr);
    const evidence = JSON.parse(doctor.stdout); assert.equal(evidence.externalProviderCalls, 0);
    assert.equal(evidence.credentialConfigured, true); assert.equal(evidence.runtimeVersionVerified, false);
    assert.equal(doctor.stdout.includes(env.OPENAI_API_KEY), false);
    const sessions = call(['sessions', '--state-root', state]); assert.equal(sessions.status, 0, sessions.stderr); assert.deepEqual(JSON.parse(sessions.stdout), []);
    const piped = call(['--state-root', state, '--prompt', 'Never infer from a pipe.']);
    assert.equal(piped.status, 2); assert.match(piped.stderr, /interactive_terminal_required/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
