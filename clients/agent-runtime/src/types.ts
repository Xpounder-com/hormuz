export const SCHEMA_VERSION = 1;
export type Engine = 'codex' | 'claude';
export type Mode = 'plan' | 'execute';
export type Phase = 'idle' | 'planning' | 'ready' | 'executing' | 'completed' | 'interrupted' | 'failed' | 'unknown';
export type RuntimeEvent = {
  type: 'message' | 'plan' | 'tool' | 'usage' | 'diff' | 'question' | 'terminal';
  turnId?: string;
  data: unknown;
};
export type ActionRequest = {
  id: string;
  turnId: string;
  kind: 'command' | 'file' | 'network' | 'question';
  operation: Record<string, unknown>;
};
export type ApprovalDecision = {
  decision: 'accept' | 'decline' | 'cancel';
  answers?: Record<string, string[]>;
};
export type RuntimeStart = {
  workspace: string;
  model: string;
  configDir: string;
  endpoint: string;
  credential: string;
  maxOutputTokens?: number;
  threadId?: string;
};
export type RuntimeRun = {
  sessionId: string;
  prompt: string;
  mode: Mode;
  signal: AbortSignal;
  onEvent: (event: RuntimeEvent) => void;
  onApproval: (request: ActionRequest) => Promise<ApprovalDecision>;
};
export type RunResult = { status: 'completed' | 'interrupted' | 'failed' | 'unknown'; plan?: string; threadId?: string };
/** Trusted host metadata; never persisted as runtime authority or supplied by a renderer. */
export type RuntimeHistoryPolicy = { engine: 'codex'; runtimeVersion: '0.160.0'; executablePath: string };
export interface AgentSessionAdapter {
  readonly engine: Engine;
  readonly version: string;
  historyPolicy?(): Promise<RuntimeHistoryPolicy>;
  start(options: RuntimeStart): Promise<{ threadId: string }>;
  run(options: RuntimeRun): Promise<RunResult>;
  interrupt(): Promise<void>;
  dispose(): Promise<void>;
}
export class AgentError extends Error {
  code: string;
  constructor(code: string, message: string = code) { super(message); this.code = code; this.name = 'AgentError'; }
}
