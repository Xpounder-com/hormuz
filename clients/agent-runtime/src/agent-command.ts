import { createHash } from 'node:crypto';
import { access } from 'node:fs/promises';
import { constants } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline/promises';
import { AgentSessions, ApplicationScheduler } from './sessions.ts';
import { SessionStore } from './store.ts';
import { CodexAdapter, CODEX_VERSION } from './codex.ts';
import { ClaudeAdapter, CLAUDE_SDK_VERSION } from './claude.ts';
import { AgentError } from './types.ts';
import { runTerminal } from './terminal.ts';
import { dollarToMicrousd, loadAgentCLIConfig, saveAgentCLIConfig, resolveFolderContext, validateAgentCLIConfig } from './terminal-config.ts';
import type { AgentCLIConfig, FolderContext } from './terminal-config.ts';

const HELP = [
  'Hormuz Agent — coding assistance in the current folder (macOS preview)',
  '',
  'Usage: hormuz agent [options] [prompt]',
  '       hormuz-agent [options] [prompt]',
  '       hormuz agent init [configuration options]',
  '       hormuz agent doctor | sessions [options]',
  '',
  'Plain prompts investigate and propose a read-only plan. /accept and /execute',
  'are separate steps. Concrete actions require approval. Ctrl+C interrupts work.',
  '',
  '  --cwd PATH              Folder to work in (default: current folder)',
  '  --profile NAME          Private profile (default: default)',
  '  --state-root PATH       Private state outside the project folder',
  '  --resume ID|last        Explicitly reopen a matching folder session',
  '  --prompt TEXT           Initial read-only planning prompt',
  '  --engine codex|claude    Engine; fixed for each session',
  '  --model MODEL           Explicit provider model',
  '  --input-rate USD        Configured dollars per million input tokens',
  '  --output-rate USD       Configured dollars per million output tokens',
  '  --budget USD            Lifetime session allowance (default: 1)',
  '  --project-budget USD    Lifetime project allowance (default: 5)',
  '  --profile-budget USD    Lifetime profile allowance (default: 10)',
  '  --max-input-tokens N    Conservative input bound (default: 100000)',
  '  --max-output-tokens N   Generation output bound (default: 4096)',
  '  --codex-executable PATH Trusted installed Codex executable',
  '  --help                  Show help without inference',
  '  --version               Show CLI version',
  '',
  'Model and rates have no vendor defaults. Set current rates explicitly.',
  'API credentials come from OPENAI_API_KEY or ANTHROPIC_API_KEY in this shell.',
  'Cloud context consent is requested in the terminal before planning/resume.',
  'Allowances use configured rates; they are not provider account-wide bill caps.',
  'Linux/Windows interactive execution awaits a qualified process sandbox.',
].join('\n') + '\n';

type CLIArguments = { command: 'interactive' | 'init' | 'doctor' | 'sessions' | 'help' | 'version'; flags: Record<string, string>; prompt?: string };
const FLAGS = new Set(['cwd', 'profile', 'state-root', 'resume', 'prompt', 'engine', 'model', 'input-rate', 'output-rate', 'budget', 'project-budget', 'profile-budget', 'max-input-tokens', 'max-output-tokens', 'codex-executable']);
const CONFIG_FLAGS = ['engine', 'model', 'input-rate', 'output-rate', 'budget', 'project-budget', 'profile-budget', 'max-input-tokens', 'max-output-tokens', 'codex-executable'];

export function parseCLIArguments(argv: string[]): CLIArguments {
  if (argv.length > 128 || Buffer.byteLength(JSON.stringify(argv)) > 128 * 1024) throw new AgentError('argument_limit');
  if (argv.some(value => typeof value !== 'string' || value.includes('\0'))) throw new AgentError('invalid_arguments');
  const parsed: CLIArguments = { command: 'interactive', flags: {} };
  let i = 0;
  if (['init', 'doctor', 'sessions'].includes(argv[0])) { parsed.command = argv[0] as CLIArguments['command']; i++; }
  const prompt: string[] = [];
  for (; i < argv.length; i++) {
    const arg = argv[i]!;
    if (arg === '--help' || arg === '-h') { parsed.command = 'help'; continue; }
    if (arg === '--version') { parsed.command = 'version'; continue; }
    if (arg === '--') { prompt.push(...argv.slice(i + 1)); break; }
    if (!arg.startsWith('-')) { prompt.push(arg); continue; }
    if (!arg.startsWith('--')) throw new AgentError('unknown_option');
    const equals = arg.indexOf('='), name = arg.slice(2, equals === -1 ? undefined : equals);
    if (!FLAGS.has(name) || Object.hasOwn(parsed.flags, name)) throw new AgentError('unknown_or_duplicate_option');
    const value = equals === -1 ? argv[++i] : arg.slice(equals + 1);
    if (!value || value.startsWith('--')) throw new AgentError('option_value_required');
    parsed.flags[name] = value;
  }
  if (prompt.length && parsed.flags.prompt || parsed.flags.resume && (prompt.length || parsed.flags.prompt)) throw new AgentError('conflicting_options');
  parsed.prompt = parsed.flags.prompt ?? (prompt.length ? prompt.join(' ') : undefined);
  if (parsed.prompt && Buffer.byteLength(parsed.prompt) > 64 * 1024) throw new AgentError('prompt_limit');
  if (parsed.command !== 'interactive' && parsed.command !== 'help' && parsed.command !== 'version' && (parsed.prompt || parsed.flags.resume)) throw new AgentError('command_argument_invalid');
  return parsed;
}

const integer = (value: string | undefined, fallback: number) => {
  if (value === undefined) return fallback;
  if (!/^[1-9][0-9]{0,9}$/.test(value)) throw new AgentError('invalid_token_limit');
  return Number(value);
};

export function configureCLI(flags: Record<string, string>, profileId: string, previous?: AgentCLIConfig): AgentCLIConfig {
  const engine = flags.engine ?? previous?.engine ?? 'codex';
  const model = flags.model ?? previous?.model ?? '';
  if (previous && (engine !== previous.engine || model !== previous.model) && (!flags['input-rate'] || !flags['output-rate'])) throw new AgentError('new_model_requires_explicit_rates');
  const config = {
    schemaVersion: 1 as const, profileId, engine, model,
    allowanceMicrousd: flags.budget ? dollarToMicrousd(flags.budget) : previous?.allowanceMicrousd ?? 1_000_000,
    projectAllowanceMicrousd: flags['project-budget'] ? dollarToMicrousd(flags['project-budget']) : previous?.projectAllowanceMicrousd ?? 5_000_000,
    profileAllowanceMicrousd: flags['profile-budget'] ? dollarToMicrousd(flags['profile-budget']) : previous?.profileAllowanceMicrousd ?? 10_000_000,
    inputMicrousdPerMillion: flags['input-rate'] ? dollarToMicrousd(flags['input-rate']) : previous?.inputMicrousdPerMillion ?? 0,
    outputMicrousdPerMillion: flags['output-rate'] ? dollarToMicrousd(flags['output-rate']) : previous?.outputMicrousdPerMillion ?? 0,
    maxInputTokens: integer(flags['max-input-tokens'], previous?.maxInputTokens ?? 100_000),
    maxOutputTokens: integer(flags['max-output-tokens'], previous?.maxOutputTokens ?? 4096),
    ...(flags['codex-executable'] ? { codexExecutable: resolve(flags['codex-executable']) } : engine === 'codex' && previous?.codexExecutable ? { codexExecutable: previous.codexExecutable } : {}),
  };
  validateAgentCLIConfig(config);
  return config;
}

async function setup(flags: Record<string, string>, context: FolderContext, previous?: AgentCLIConfig): Promise<AgentCLIConfig> {
  const supplied = { ...flags };
  if (process.stdin.isTTY && process.stdout.isTTY) {
    const input = createInterface({ input: process.stdin, output: process.stdout });
    let bytes = 0, overflow = false, cancelled = false;
    const controller = new AbortController();
    const cancel = () => { cancelled = true; controller.abort(); };
    const boundedInput = (chunk: Buffer | string) => {
      bytes += Buffer.byteLength(chunk);
      if (bytes > 16_384) { overflow = true; controller.abort(); input.close(); process.stdin.pause(); }
    };
    process.stdin.on('data', boundedInput);
    process.stdin.on('end', cancel); process.stdin.on('close', cancel); process.stdin.on('error', cancel);
    input.on('close', cancel); input.on('error', cancel); input.on('SIGINT', cancel); process.on('SIGINT', cancel);
    const ask = async (label: string, fallback = '') => {
      let answer: string;
      try { answer = await input.question(label + (fallback ? ' [' + fallback + ']' : '') + ': ', { signal: controller.signal }); }
      catch (error) { if (overflow) throw new AgentError('configuration_input_limit'); if (cancelled) throw new AgentError('configuration_cancelled'); throw error; }
      const value = answer.trim();
      if (overflow) throw new AgentError('configuration_input_limit');
      if (cancelled) throw new AgentError('configuration_cancelled');
      return value || fallback;
    };
    try {
      if (!supplied.engine && !previous) supplied.engine = await ask('Engine (codex or claude)', 'codex');
      if (!supplied.model && !previous) supplied.model = await ask('Provider model');
      const changing = previous && ((supplied.engine && supplied.engine !== previous.engine) || (supplied.model && supplied.model !== previous.model));
      if (!supplied['input-rate'] && (!previous || changing)) supplied['input-rate'] = await ask('Configured input USD per million tokens');
      if (!supplied['output-rate'] && (!previous || changing)) supplied['output-rate'] = await ask('Configured output USD per million tokens');
      if (!supplied.budget && !previous) supplied.budget = await ask('Lifetime session allowance in USD', '1');
      if (!supplied['project-budget'] && !previous) supplied['project-budget'] = await ask('Lifetime project allowance in USD', '5');
      if (!supplied['profile-budget'] && !previous) supplied['profile-budget'] = await ask('Lifetime profile allowance in USD', '10');
      if (cancelled) throw new AgentError('configuration_cancelled');
    } finally {
      process.stdin.removeListener('data', boundedInput);
      process.stdin.removeListener('end', cancel); process.stdin.removeListener('close', cancel); process.stdin.removeListener('error', cancel);
      input.removeListener('close', cancel); input.removeListener('error', cancel); input.removeListener('SIGINT', cancel); process.removeListener('SIGINT', cancel);
      input.close();
    }
  }
  const config = configureCLI(supplied, context.profileId, previous);
  await saveAgentCLIConfig(context, config);
  process.stdout.write('Saved private profile configuration: ' + JSON.stringify(context.configPath) + '\n');
  return config;
}

export function createCLIManager(config: AgentCLIConfig, context: FolderContext) {
  let cloudAllowed = false;
  const store = new SessionStore(context.stateDirectory);
  const manager = new AgentSessions({
    profileId: config.profileId, store, scheduler: new ApplicationScheduler(),
    runtimeFactory: engine => {
      if (engine !== config.engine) throw new AgentError('runtime_engine_mismatch');
      return engine === 'codex' ? new CodexAdapter({ command: config.codexExecutable }) : new ClaudeAdapter();
    },
    authorizeWorkspace: async (project, workspace) => project === context.projectId && workspace === context.workspace,
    authorizeAllowance: async (project, amount) => project === context.projectId && amount <= config.allowanceMicrousd,
    authorizeCloud: async project => project === context.projectId && cloudAllowed,
    parentAllowances: { projectMicrousd: config.projectAllowanceMicrousd, profileMicrousd: config.profileAllowanceMicrousd },
    resolveConnection: async connectionId => {
      if (connectionId !== 'provider') throw new AgentError('connection_unavailable');
      const provider = config.engine === 'codex' ? 'openai' : 'anthropic';
      const credential = process.env[provider === 'openai' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY'];
      if (!credential) throw new AgentError('credential_unavailable');
      const version = createHash('sha256').update(JSON.stringify({ ...config, credential })).digest('hex');
      return {
        provider, credential, version, models: [config.model],
        upstream: provider === 'openai' ? 'https://api.openai.com/v1' : 'https://api.anthropic.com/v1',
        inputMicrousdPerMillion: config.inputMicrousdPerMillion,
        outputMicrousdPerMillion: config.outputMicrousdPerMillion,
        maxInputTokens: config.maxInputTokens, maxOutputTokens: config.maxOutputTokens, requestLimit: 25,
      };
    },
  });
  return { manager, store, onCloudConsent: (allowed: boolean) => { cloudAllowed = allowed; } };
}

async function doctor(config: AgentCLIConfig | undefined, context: FolderContext): Promise<number> {
  let runtime = false;
  if (config?.engine === 'codex') {
    const adapter = new CodexAdapter({ command: config.codexExecutable });
    try { await adapter.historyPolicy(); runtime = true; } catch {} finally { await adapter.dispose(); }
  } else if (config?.engine === 'claude') {
    try { await access(fileURLToPath(import.meta.resolve('@anthropic-ai/claude-agent-sdk')), constants.R_OK); runtime = true; } catch {}
  }
  const configured = Boolean(config);
  const credentialConfigured = Boolean(config && process.env[config.engine === 'codex' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY']);
  process.stdout.write(JSON.stringify({
    cliVersion: '0.1.0', node: process.versions.node, platform: process.platform,
    interactivePlatformQualified: process.platform === 'darwin', workspace: context.workspace,
    profile: context.profileId, configurationPresent: configured, engine: config?.engine,
    model: config?.model, runtimeLocated: runtime, expectedRuntime: config?.engine === 'codex' ? CODEX_VERSION : CLAUDE_SDK_VERSION,
    runtimeVersionVerified: false,
    credentialConfigured, externalProviderCalls: 0,
  }, null, 2) + '\n');
  return configured && runtime && credentialConfigured && process.platform === 'darwin' ? 0 : 2;
}

export async function main(argv: string[]): Promise<number> {
  try {
    const args = parseCLIArguments(argv);
    if (args.command === 'help') { process.stdout.write(HELP); return 0; }
    if (args.command === 'version') { process.stdout.write('Hormuz Agent CLI 0.1.0 (macOS preview)\n'); return 0; }
    const context = await resolveFolderContext(args.flags.cwd ?? process.cwd(), args.flags.profile ?? 'default', args.flags['state-root'] ? resolve(args.flags['state-root']) : undefined);
    const previous = await loadAgentCLIConfig(context);
    if (args.command === 'init') { await setup(args.flags, context, previous); return 0; }
    if (args.command === 'doctor') return await doctor(previous, context);
    if (args.command === 'sessions') {
      const sessions = await new SessionStore(context.stateDirectory).list(context.profileId, context.projectId);
      process.stdout.write(JSON.stringify(sessions.map(session => ({ id: session.id, engine: session.engine, model: session.model, phase: session.phase, updatedAt: session.updatedAt })), null, 2) + '\n');
      return 0;
    }
    if (!process.stdin.isTTY || !process.stdout.isTTY) throw new AgentError('interactive_terminal_required');
    if (process.platform !== 'darwin') throw new AgentError('interactive_platform_not_qualified');
    const config = previous ? CONFIG_FLAGS.some(flag => args.flags[flag] !== undefined) ? configureCLI(args.flags, context.profileId, previous) : previous : await setup(args.flags, context);
    validateAgentCLIConfig(config);
    const credential = process.env[config.engine === 'codex' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY'];
    if (!credential) throw new AgentError('credential_unavailable');
    const host = createCLIManager(config, context);
    const listSessions = async () => (await host.store.list(context.profileId, context.projectId)).map(session => ({ id: session.id, phase: session.phase, engine: session.engine, model: session.model, updatedAt: Number(session.updatedAt) }));
    let resumeSessionId = args.flags.resume;
    if (resumeSessionId === 'last') {
      resumeSessionId = (await listSessions()).filter(session => session.engine === config.engine && session.model === config.model).sort((a, b) => b.updatedAt - a.updatedAt)[0]?.id;
      if (!resumeSessionId) throw new AgentError('no_matching_session');
    }
    return await runTerminal({ sessions: host.manager, ...context, engine: config.engine, model: config.model,
      allowanceMicrousd: config.allowanceMicrousd, listSessions, onCloudConsent: host.onCloudConsent,
      initialPrompt: args.prompt, resumeSessionId });
  } catch (error) {
    const code = error instanceof AgentError ? error.code : error instanceof Error && /^[A-Z][A-Z0-9_]+$/.test(error.message) ? error.message : 'cli_start_failed';
    const hints: Record<string, string> = {
      credential_unavailable: 'Set OPENAI_API_KEY for Codex or ANTHROPIC_API_KEY for Claude in this shell.',
      interactive_terminal_required: 'Run this command in a terminal. Piped input cannot approve actions.',
      interactive_platform_not_qualified: 'Interactive execution currently supports macOS. Help, init and doctor do not perform inference.',
      new_model_requires_explicit_rates: 'Changing model or engine requires --input-rate and --output-rate.',
      configuration_cancelled: 'Setup cancelled; no profile configuration was saved.',
    };
    process.stderr.write('Hormuz Agent: ' + code + '. ' + (hints[code] ?? 'Run hormuz agent --help; configure the private profile with hormuz agent init.') + '\n');
    return code === 'configuration_cancelled' ? 130 : 2;
  }
}
