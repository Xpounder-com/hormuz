import { AgentError } from './types.ts';
import type { AgentSessions } from './sessions.ts';

type SenderPolicy = { projectIds: ReadonlySet<string>; workspaceRoots: ReadonlySet<string> };
type HostOptions = { sessions: AgentSessions; senderPolicy: (senderId: number) => SenderPolicy | undefined };
const text = (input: Record<string, unknown>, key: string) => {
  const value = input[key];
  if (typeof value !== 'string' || !value || value.length > 65_536) throw new AgentError('invalid_request');
  return value;
};

/** Narrow Electron-compatible handler. Trusted host registration is the sender authority. */
export function createAgentBridge(options: HostOptions) {
  return async (senderId: number, input: unknown): Promise<unknown> => {
    const policy = options.senderPolicy(senderId);
    if (!policy) throw new AgentError('unauthorized_sender');
    if (!input || typeof input !== 'object' || Array.isArray(input) || Buffer.byteLength(JSON.stringify(input)) > 128 * 1024) throw new AgentError('invalid_request');
    const request = input as Record<string, unknown>;
    if (request.schemaVersion !== 1) throw new AgentError('unsupported_schema');
    const method = text(request, 'method'); const projectId = text(request, 'projectId');
    if (!policy.projectIds.has(projectId)) throw new AgentError('unauthorized_project');
    const allowed: Record<string, string[]> = {
      create: ['workspace', 'engine', 'model', 'connectionId', 'cloudAllowed', 'allowanceMicrousd'],
      get: ['sessionId'], plan: ['sessionId', 'prompt'], acceptPlan: ['sessionId', 'revision'], revisePlan: ['sessionId', 'revision', 'text'], rejectPlan: ['sessionId', 'revision'], execute: ['sessionId', 'revision'],
      resume: ['sessionId'], interrupt: ['sessionId'], review: ['sessionId'], acceptReview: ['sessionId', 'observedRevision'], recordReview: ['sessionId', 'observedRevision', 'disposition'],
      decide: ['sessionId', 'approvalId', 'fingerprint', 'decision', 'answers'], remove: ['sessionId'],
    };
    if (!allowed[method]) throw new AgentError('unsupported_operation');
    for (const key of Object.keys(request)) if (!['schemaVersion', 'method', 'projectId', ...allowed[method]].includes(key)) throw new AgentError('invalid_request');
    const sessions = options.sessions;
    if (method === 'create') {
      const workspace = text(request, 'workspace');
      if (!policy.workspaceRoots.has(workspace)) throw new AgentError('unauthorized_workspace');
      const engine = text(request, 'engine');
      if (engine !== 'codex' && engine !== 'claude') throw new AgentError('invalid_engine');
      if (typeof request.cloudAllowed !== 'boolean' || typeof request.allowanceMicrousd !== 'number') throw new AgentError('invalid_request');
      return await sessions.create({ projectId, workspace, engine, model: text(request, 'model'), connectionId: text(request, 'connectionId'), cloudAllowed: request.cloudAllowed, allowanceMicrousd: request.allowanceMicrousd });
    }
    const sessionId = text(request, 'sessionId');
    const session = await sessions.get(sessionId, projectId);
    if (!policy.workspaceRoots.has(session.workspace)) throw new AgentError('unauthorized_workspace');
    switch (method) {
      case 'get': return await sessions.get(sessionId, projectId);
      case 'plan': return await sessions.plan(sessionId, projectId, text(request, 'prompt'));
      case 'acceptPlan': return await sessions.acceptPlan(sessionId, projectId, text(request, 'revision'));
      case 'revisePlan': return await sessions.revisePlan(sessionId, projectId, text(request, 'revision'), text(request, 'text'));
      case 'rejectPlan': return await sessions.rejectPlan(sessionId, projectId, text(request, 'revision'));
      case 'execute': return await sessions.execute(sessionId, projectId, text(request, 'revision'));
      case 'resume': return await sessions.resume(sessionId, projectId);
      case 'interrupt': return await sessions.interrupt(sessionId, projectId);
      case 'review': return await sessions.review(sessionId, projectId);
      case 'acceptReview': return await sessions.acceptReview(sessionId, projectId, text(request, 'observedRevision'));
      case 'recordReview': {
        const disposition = text(request, 'disposition');
        if (!['accepted', 'rejected', 'request_changes'].includes(disposition)) throw new AgentError('invalid_review_disposition');
        return await sessions.recordReview(sessionId, projectId, text(request, 'observedRevision'), disposition as 'accepted' | 'rejected' | 'request_changes');
      }
      case 'remove': return await sessions.remove(sessionId, projectId);
      case 'decide': {
        const decision = text(request, 'decision');
        if (!['accept', 'decline', 'cancel'].includes(decision)) throw new AgentError('invalid_decision');
        const answers = request.answers;
        if (answers !== undefined && (!answers || typeof answers !== 'object' || Array.isArray(answers) || Object.values(answers).some(v => !Array.isArray(v) || v.some(x => typeof x !== 'string')))) throw new AgentError('invalid_answers');
        return await sessions.decide(sessionId, projectId, text(request, 'approvalId'), text(request, 'fingerprint'), { decision: decision as 'accept' | 'decline' | 'cancel', answers: answers as Record<string, string[]> | undefined });
      }
    }
  };
}

export type AgentIPCEvent = { sender: { id: number }; senderFrame?: { url: string; parent: unknown } | null };
export function registerAgentIPC(ipc: { handle: (channel: string, handler: (event: AgentIPCEvent, request: unknown) => Promise<unknown>) => void; removeHandler: (channel: string) => void }, options: HostOptions & { authorizeEvent: (event: AgentIPCEvent) => boolean }) {
  const handler = createAgentBridge(options);
  ipc.handle('hormuz:agent:v1', async (event, request) => {
    // A webContents ID alone also identifies untrusted child frames and navigated documents.
    if (!options.authorizeEvent(event)) throw new AgentError('unauthorized_document');
    return await handler(event.sender.id, request);
  });
  return () => ipc.removeHandler('hormuz:agent:v1');
}
