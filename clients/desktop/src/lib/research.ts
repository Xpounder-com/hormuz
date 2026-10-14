import { preview } from './planner.ts';
import type { Chat, Timer, WorkspaceState } from './types.ts';
import type { MockWorkspace } from './workspace.ts';
type ResearchSetup = Pick<
  ResearchSession,
  | 'mode'
  | 'question'
  | 'authorId'
  | 'paperAuthor'
  | 'paperAuthors'
  | 'scope'
  | 'fileIds'
  | 'chatIds'
  | 'reviewerIds'
  | 'scenario'
>;

export type Heat = 'gentle' | 'spicy' | 'scorched';
export type ResponseAction = 'revise' | 'rebut' | 'limit' | 'evidence';
export type Finding = {
  id: string;
  reviewer: string;
  title: string;
  severity: 'Major' | 'Moderate';
  locator: string;
  critique: string;
  request: string;
  response: string;
  evidence: string;
  action: ResponseAction;
  decision: { revision: number; signature: string } | null;
  carriedFrom?: string;
};
export type ReviewerResult = {
  modelId: string;
  name: string;
  place: 'local' | 'cloud';
  status: 'queued' | 'reviewing' | 'complete' | 'canceled' | 'failed';
  findings: Finding[];
  input?: { paper: string; context: string; peerVerdicts: false };
};
export type ReplyCheck = {
  revision: number;
  paper: string;
  model: string;
  context: string;
  status: 'checking' | 'complete' | 'canceled';
  responses: Record<string, string>;
  outcomes: Record<string, 'supported' | 'needs-work' | 'needs-evidence'>;
};
export type RoastRound = {
  id: string;
  number: number;
  title: string;
  paper: string;
  revision: number;
  heat: Heat;
  criteria: string;
  context: string;
  sources: string[];
  instructions: string;
  reviewers: ReviewerResult[];
  status: 'reviewing' | 'complete' | 'incomplete';
  check: ReplyCheck | null;
  usageKey: string;
  origin: string;
  remaining: number;
  activeHold: number;
  unknownHold: number;
  actual: number;
  profileId: WorkspaceState['profileId'];
  frozen: ResearchSetup;
  callUpper: number;
  question: string;
  author: {
    modelId: string;
    name: string;
    place: 'local' | 'cloud';
    status: 'queued' | 'authoring' | 'complete' | 'canceled' | 'failed';
  } | null;
  optionalClosed: boolean;
  reconciliation: {
    model: string;
    status: 'running' | 'complete' | 'canceled';
    revision: number;
    paper?: string;
  } | null;
};
export type ResearchSession = {
  chatId: string;
  profileId: WorkspaceState['profileId'];
  title: string;
  paper: string;
  revision: number;
  sealed: boolean;
  heat: Heat;
  reviewerIds: [string, string];
  criteria: string;
  rounds: RoastRound[];
  importedComments: string;
  importedResponse: string;
  notice: string;
  mode: 'existing' | 'author';
  question: string;
  authorId: string;
  paperAuthor: string;
  paperAuthors: string[];
  scope: 'selected' | 'custom' | 'project';
  fileIds: string[];
  chatIds: string[];
  scenario: 'normal' | 'reviewer-fails' | 'missing-evidence' | 'disagreement';
  signoff: null | {
    revision: number;
    paper: string;
    context: string;
    roundId: string;
    decision: 'accept' | 'changes';
  };
};
export type ResearchSnapshot = {
  sessions: Record<string, ResearchSession>;
  busy: { chatId: string; roundId: string; kind: 'roast' | 'rebuttal' | 'reconcile' } | null;
  totalRounds: number;
};
export type ResearchPlan = {
  blocked: boolean;
  reason: string;
  roles: {
    id: string;
    name: string;
    place: string;
    preparation: boolean;
    kind: 'author' | 'reviewer';
  }[];
  upper: number;
  tokens: number;
  sources: string[];
  context: string;
  instructions: string;
};

const samplePaper = `# A local AI workspace for researchers

Abstract
Our workspace makes research faster and produces reliable conclusions for every researcher.

Methods
We asked twelve colleagues to try the prompt bar for one afternoon. They all used the same laptop. There was no control group or preregistered evaluation.

Results
Ten participants said the workflow felt faster. We did not measure task accuracy or time-to-completion. We conclude that multi-model agreement guarantees correct research.

Limitations
Local models keep selected prompts on the device, while paid cloud models are optional. Hardware and external validation were not evaluated.

This paper is a fictional UI fixture. Its findings and rebuttal checks are scripted, not AI analysis.`;

export function newResearchSession(
  chatId: string,
  profileId: WorkspaceState['profileId'] = 'personal',
): ResearchSession {
  return {
    chatId,
    profileId,
    title: 'A local AI workspace for researchers',
    paper: samplePaper,
    revision: 1,
    sealed: false,
    heat: 'spicy',
    reviewerIds: ['qwen', 'gemma'],
    criteria: 'Claims, methods, evidence and limitations',
    rounds: [],
    importedComments: '',
    importedResponse: '',
    notice: '',
    mode: 'existing',
    question: 'How should a small pilot compare local and cloud AI for research?',
    authorId: 'qwen',
    paperAuthor: 'unknown',
    paperAuthors: [],
    scope: 'selected',
    fileIds: [],
    chatIds: [],
    scenario: 'normal',
    signoff: null,
  };
}
function packet(ws: WorkspaceState, chat: Chat, session: ResearchSession, intent: string) {
  const ids = session.scope === 'selected' ? chat.selectedFiles : session.fileIds;
  const sources = session.chatIds.map((id) => {
    const c = ws.chats.find(
      (c) => c.id === id && c.projectId === chat.projectId && c.projectId !== null,
    );
    return c
      ? `${c.title}\n${c.messages.map((m) => `${m.role}: ${m.text}`).join('\n')}`
      : '[Missing selected chat]';
  });
  return preview(ws, {
    ...chat,
    draft: `${session.title}\n${session.paper}\nReview criteria: ${session.criteria}\n${sources.join('\n\n')}`,
    selectedFiles: ids,
    messages: [],
    intent,
    inheritIntent: false,
    requestIntent: undefined,
    continuationModel: undefined,
  });
}
function contextSignature(ws: WorkspaceState, chat: Chat, session: ResearchSession): string {
  const p = packet(ws, chat, session, session.reviewerIds[0]);
  // Model residency is intentionally absent: the disclosed sequence swaps it.
  return JSON.stringify([
    chat.id,
    chat.projectId,
    p.instructions,
    p.context,
    chat.selectedFiles,
    p.cloudAllowed,
    session.criteria,
    session.mode,
    session.mode === 'author' ? session.authorId : session.paperAuthor,
    session.question,
    session.mode === 'author' ? [session.authorId] : session.paperAuthors,
    session.reviewerIds,
    session.scope,
    session.fileIds,
    session.chatIds,
    session.chatIds.map(
      (id) => ws.chats.find((c) => c.id === id && c.projectId === chat.projectId)?.messages,
    ),
    p.objective,
    chat.cap,
    ws.projects.find((p) => p.id === chat.projectId)?.cap,
    ws.profileLocalOnly,
    ws.priceUpper,
  ]);
}
const identity = (ws: WorkspaceState, id: string) =>
  ws.models.find((m) => m.id === id)?.identity ?? id;
function humanSignature(ws: WorkspaceState, s: ResearchSession, r: RoastRound) {
  const chat = ws.chats.find((c) => c.id === s.chatId);
  return JSON.stringify([
    chat ? contextSignature(ws, chat, s) : '',
    r.id,
    r.reviewers.flatMap((p) => p.findings).map((f) => [responseSignature(f), f.decision]),
  ]);
}
export function reviewDecisionCurrent(ws: WorkspaceState, s: ResearchSession) {
  const r = s.rounds.at(-1);
  return (
    !!r &&
    !!s.signoff &&
    s.signoff.roundId === r.id &&
    s.signoff.revision === s.revision &&
    s.signoff.paper === s.paper &&
    s.signoff.context === humanSignature(ws, s, r)
  );
}
export function researchPreview(
  ws: WorkspaceState,
  s: ResearchSession,
  totalRounds: number,
): ResearchPlan {
  const chat = ws.chats.find((c) => c.id === s.chatId);
  if (!chat)
    return {
      blocked: true,
      reason: 'This chat no longer exists.',
      roles: [],
      upper: 0,
      tokens: 0,
      sources: [],
      context: '',
      instructions: '',
    };
  const releasedUsage = { ...ws.usage };
  let releasable = 0;
  for (const round of s.rounds) {
    releasable += round.remaining;
    releasedUsage[round.usageKey] = Math.max(
      0,
      (releasedUsage[round.usageKey] ?? 0) - round.remaining,
    );
  }
  const quote = { ...ws, profileUsed: ws.profileUsed - releasable, usage: releasedUsage };
  const excluded = new Set<string>();
  const knownAuthor = s.mode === 'author' ? s.authorId : s.paperAuthor;
  const knownAuthors = s.mode === 'author' ? [s.authorId] : [s.paperAuthor, ...s.paperAuthors];
  for (const author of knownAuthors)
    if (!['unknown', 'human', 'auto'].includes(author)) excluded.add(identity(ws, author));
  if (knownAuthor !== 'unknown' && knownAuthor !== 'human' && knownAuthor !== 'auto')
    excluded.add(identity(ws, knownAuthor));
  const intents = s.mode === 'author' ? [s.authorId, ...s.reviewerIds] : [...s.reviewerIds];
  const plans = intents.map((id, index) => {
    const author = s.mode === 'author' && index === 0;
    const target = author ? { ...s, paper: `Research question: ${s.question}` } : s;
    const available =
      id === 'auto'
        ? { ...quote, models: quote.models.filter((m) => !excluded.has(identity(ws, m.id))) }
        : quote;
    const p = packet(available, chat, target, id);
    if (p.model) excluded.add(identity(ws, p.model.id));
    return p;
  });
  const roles = plans.map((p, index) => ({
    id: p.model?.id ?? '',
    name: p.model?.name ?? 'Unavailable',
    place: p.model?.place ?? 'local',
    preparation: p.preparation,
    kind: s.mode === 'author' && index === 0 ? ('author' as const) : ('reviewer' as const),
  }));
  const reviewers = plans.slice(s.mode === 'author' ? 1 : 0);
  const upper =
    plans.reduce((sum, p) => sum + p.upper, 0) +
    Math.max(reviewers[0].upper, s.mode === 'author' ? plans[0].upper : 0);
  const project = ws.projects.find((p) => p.id === chat.projectId);
  const cap = Math.min(project?.cap ?? chat.cap, chat.cap, 10);
  const used = quote.usage[chat.projectId ?? chat.id] ?? 0;
  let reason = '';
  if (s.mode === 'existing' && !s.paper.trim()) reason = 'Add a paper before starting a roast.';
  else if (s.mode === 'author' && !s.question.trim())
    reason = 'Add a research question before authoring.';
  else if (
    s.chatIds.some(
      (id) =>
        !ws.chats.some(
          (c) => c.id === id && c.projectId === chat.projectId && c.projectId !== null,
        ),
    )
  )
    reason = 'A selected source chat is missing or outside this project. Reduce the manifest.';
  else if (new Set(reviewers.map((p) => identity(ws, p.model?.id ?? ''))).size !== 2)
    reason = 'Choose two distinct model identities. Two sessions of one model do not count.';
  else if (
    reviewers.some((p) =>
      (s.mode === 'author' ? [plans[0].model?.id ?? ''] : [s.paperAuthor, ...s.paperAuthors])
        .filter((id) => !['unknown', 'human', 'auto'].includes(id))
        .some((id) => identity(ws, id) === identity(ws, p.model?.id ?? '')),
    )
  )
    reason =
      'A known author or same-version alias cannot act as an independent reviewer. Choose other identities.';
  else if (totalRounds >= 2)
    reason =
      'The demo allows two explicitly started roast rounds. Your findings and rebuttals remain available.';
  else if (ws.attempt || ws.reviewActivity || ws.models.some((m) => m.state === 'loading'))
    reason = 'Another response or review is active. Return to it or stop it first.';
  else if (plans.some((p) => p.blocked)) reason = plans.find((p) => p.blocked)!.reason;
  else if (upper > 3 || (upper > 0 && (quote.profileUsed + upper > 25 || used + upper > cap)))
    reason = 'The whole review plus optional rebuttal check exceeds the fictional allowance.';
  return {
    blocked: Boolean(reason),
    reason:
      reason ||
      (s.mode === 'author'
        ? 'Author, then two blind reviews and one optional check or reconciliation. Maximum four calls.'
        : 'Two blind reviews, then one optional response check or reconciliation. Maximum three calls.'),
    roles,
    upper,
    tokens: Math.max(...plans.map((p) => p.tokens)),
    sources: [
      ...plans[0].context.map((f) => `${f.name} · v${f.revision}`),
      ...s.chatIds.map((id) => `Chat: ${ws.chats.find((c) => c.id === id)?.title ?? 'missing'}`),
    ],
    context: contextSignature(ws, chat, s),
    instructions: plans[0].instructions,
  };
}
export function responseSignature(f: Finding): string {
  return JSON.stringify([f.action, f.response, f.evidence]);
}
export function findingStatus(
  round: RoastRound,
  f: Finding,
  session: ResearchSession,
  currentContext: string,
): string {
  const check = round.check;
  if (check?.status === 'checking') return 'Checking response';
  const current =
    check?.status === 'complete' &&
    check.revision === session.revision &&
    check.paper === session.paper &&
    check.context === currentContext &&
    check.responses[f.id] === responseSignature(f);
  if (
    f.decision &&
    current &&
    f.decision.revision === session.revision &&
    f.decision.signature === responseSignature(f)
  )
    return f.action === 'limit'
      ? 'Limitation accepted'
      : f.action === 'revise'
        ? 'Addressed by revision'
        : 'Rebuttal accepted';
  if (check && !current) return 'Check stale';
  if (current)
    return check.outcomes[f.id] === 'supported'
      ? 'Ready for your decision'
      : check.outcomes[f.id] === 'needs-evidence'
        ? 'Evidence needed'
        : 'Still disputed';
  return f.response.trim() ? 'Response drafted' : 'Open';
}
export function rebuttalLetter(session: ResearchSession, round?: RoastRound): string {
  if (!round)
    return session.importedComments.trim() && session.importedResponse.trim()
      ? `Response to external reviewer comments\n\nReviewer comments (provenance unknown):\n${session.importedComments}\n\nOur response:\n${session.importedResponse}\n\nDraft for your review. No original reviewer acceptance is implied.`
      : '';
  const findings = round.reviewers.flatMap((r) => r.findings);
  return (
    `Response to reviewers — ${session.title}, v${session.revision}\nOriginal roast: v${round.revision}, round ${round.number}\n\n` +
    findings
      .map(
        (f, i) =>
          `${i + 1}. ${f.reviewer}: ${f.title}\nComment: ${f.critique}\nResponse: ${f.response || '[Response needed]'}\nEvidence / change locator: ${f.evidence || '[Not supplied]'}\n`,
      )
      .join('\n') +
    '\nDraft letter only. It does not establish acceptance, scientific validity or permission to publish.'
  );
}

// All results are finite scripted fixtures. No provider, engine or filesystem access.
export class MockResearch {
  state: ResearchSnapshot = { sessions: {}, busy: null, totalRounds: 0 };
  #workspace: MockWorkspace;
  #timer: Timer;
  #timers = new Set<unknown>();
  #listeners = new Set<(s: ResearchSnapshot) => void>();
  #generation = 0;
  #off: () => void;
  #offCopy: () => void;
  #offJournal: () => void;
  #journals = new Map<WorkspaceState['profileId'], Record<string, ResearchSession>>();
  #costs = new Map<
    string,
    Pick<RoastRound, 'remaining' | 'activeHold' | 'unknownHold' | 'actual'>
  >();
  constructor(
    workspace: MockWorkspace,
    timer: Timer = {
      after: (fn, ms) => setTimeout(fn, ms),
      clear: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
    },
  ) {
    this.#workspace = workspace;
    this.#timer = timer;
    this.#offJournal = workspace.onJournal((profile, kind, ids) => {
      if (kind === 'save')
        this.#journals.set(
          profile,
          structuredClone(
            Object.fromEntries(
              Object.entries(this.state.sessions).filter(
                ([id, s]) => s.profileId === profile && ids.includes(id),
              ),
            ),
          ),
        );
      else {
        for (const [id, s] of Object.entries(this.state.sessions))
          if (s.profileId === profile) delete this.state.sessions[id];
        for (const [id, s] of Object.entries(structuredClone(this.#journals.get(profile) ?? {})))
          if (ids.includes(id)) {
            for (const round of s.rounds) {
              if (round.status === 'reviewing') {
                round.status = 'incomplete';
                round.reviewers
                  .filter((r) => r.status !== 'complete')
                  .forEach((r) => (r.status = 'canceled'));
                if (round.author && round.author.status !== 'complete')
                  round.author.status = 'canceled';
              }
              if (round.check?.status === 'checking') round.check.status = 'canceled';
              if (round.reconciliation?.status === 'running')
                round.reconciliation.status = 'canceled';
              Object.assign(round, this.#costs.get(round.id) ?? { remaining: 0, activeHold: 0 });
            }
            this.state.sessions[id] = s;
          }
        this.#emit();
      }
    });
    this.#offCopy = workspace.onTemporaryCopy((sourceId, copyId) => {
      const source = this.state.sessions[sourceId];
      if (!source) return;
      delete this.state.sessions[sourceId];
      for (const round of source.rounds) this.#release(round);
      const copy = structuredClone(source);
      copy.chatId = copyId;
      copy.reviewerIds = ['qwen', 'gemma'];
      copy.notice =
        'Research copied from Temporary. Original reviews retain their scope; destination context requires a fresh roast.';
      this.state.sessions[copyId] = copy;
      this.#emit();
    });
    this.#off = workspace.subscribe((ws) => {
      for (const [id, s] of Object.entries(this.state.sessions))
        if (s.profileId === ws.profileId && !ws.chats.some((c) => c.id === id)) {
          delete this.state.sessions[id];
          for (const r of s.rounds) this.#release(r);
          this.#emit();
        }
    });
  }
  subscribe(fn: (s: ResearchSnapshot) => void) {
    this.#listeners.add(fn);
    fn(structuredClone(this.state));
    return () => {
      this.#listeners.delete(fn);
    };
  }
  #emit() {
    for (const s of Object.values(this.state.sessions))
      for (const r of s.rounds)
        this.#costs.set(r.id, {
          remaining: r.remaining,
          activeHold: r.activeHold,
          unknownHold: r.unknownHold,
          actual: r.actual,
        });
    for (const fn of this.#listeners) fn(structuredClone(this.state));
  }
  #later(fn: () => void, ms: number) {
    let id: unknown;
    id = this.#timer.after(() => {
      this.#timers.delete(id);
      fn();
    }, ms);
    this.#timers.add(id);
  }
  open(chatId: string) {
    if (!this.state.sessions[chatId])
      this.state.sessions[chatId] = newResearchSession(chatId, this.#workspace.state.profileId);
    this.#emit();
  }
  update(
    chatId: string,
    field:
      | 'title'
      | 'paper'
      | 'heat'
      | 'criteria'
      | 'importedComments'
      | 'importedResponse'
      | 'question',
    value: string,
  ) {
    const s = this.state.sessions[chatId];
    if (!s) return;
    if ((field === 'paper' || field === 'title') && s[field] !== value && s.sealed) {
      s.revision++;
      s.sealed = false;
    }
    if (field === 'heat') {
      if (!['gentle', 'spicy', 'scorched'].includes(value)) return;
      s.heat = value as Heat;
    } else s[field] = value.slice(0, field === 'paper' ? 16000 : 4000);
    this.#emit();
    this.#workspace.researchChanged(chatId);
  }
  configure(chatId: string, patch: Partial<ResearchSetup>) {
    const s = this.state.sessions[chatId];
    if (!s) return;
    if (this.state.busy?.chatId === chatId) {
      this.#notice(
        s,
        'The source and role plan are frozen while this pass runs. Stop it before changing the plan.',
      );
      return;
    }
    Object.assign(s, patch);
    if (patch.paperAuthor !== undefined)
      s.paperAuthors = ['unknown', 'human'].includes(patch.paperAuthor) ? [] : [patch.paperAuthor];
    this.#emit();
    this.#workspace.researchChanged(chatId);
  }
  scope(chatId: string, mode: 'selected' | 'project') {
    const s = this.state.sessions[chatId];
    if (!s) return;
    const ws = this.#workspace.state;
    const chat = ws.chats.find((c) => c.id === chatId);
    const project = ws.projects.find((p) => p.id === chat?.projectId);
    this.configure(chatId, {
      scope: mode,
      fileIds: mode === 'project' ? (project?.files.map((f) => f.id) ?? []) : [],
      chatIds:
        mode === 'project'
          ? ws.chats
              .filter((c) => c.projectId === project?.id && c.projectId !== null)
              .map((c) => c.id)
          : [],
    });
  }
  source(chatId: string, kind: 'files' | 'chats', id: string) {
    const s = this.state.sessions[chatId];
    if (!s) return;
    const chat = this.#workspace.state.chats.find((c) => c.id === chatId)!;
    const files = s.scope === 'selected' ? [...chat.selectedFiles] : [...s.fileIds];
    const chats = [...s.chatIds];
    const list = kind === 'files' ? files : chats;
    const next = list.includes(id) ? list.filter((v) => v !== id) : [...list, id];
    this.configure(chatId, {
      scope: 'custom',
      fileIds: kind === 'files' ? next : files,
      chatIds: kind === 'chats' ? next : chats,
    });
  }
  reviewer(chatId: string, slot: number, id: string) {
    const s = this.state.sessions[chatId];
    if (s && (slot === 0 || slot === 1)) {
      if (this.state.busy?.chatId === chatId) {
        this.#notice(s, 'Reviewer identities stay frozen during the pass.');
        return;
      }
      s.reviewerIds[slot] = id;
      this.#emit();
      this.#workspace.researchChanged(chatId);
    }
  }
  respond(
    chatId: string,
    roundId: string,
    findingId: string,
    patch: Partial<Pick<Finding, 'action' | 'response' | 'evidence'>>,
  ) {
    const s = this.state.sessions[chatId];
    const round = s?.rounds.find((r) => r.id === roundId);
    const f = round?.reviewers.flatMap((r) => r.findings).find((f) => f.id === findingId);
    if (!f) return;
    if (patch.response !== undefined) f.response = patch.response.slice(0, 2500);
    if (patch.evidence !== undefined) f.evidence = patch.evidence.slice(0, 500);
    if (patch.action !== undefined) f.action = patch.action;
    f.decision = null;
    this.#emit();
    this.#workspace.researchChanged(chatId);
  }
  #notice(s: ResearchSession, text: string) {
    s.notice = text;
    this.#emit();
    if (!this.state.busy) this.#workspace.researchChanged(s.chatId);
  }
  #release(r: RoastRound) {
    if (r.remaining) {
      const refund = r.remaining;
      r.remaining = 0;
      this.#workspace.adjustReviewAllowance(r.usageKey, -refund, r.profileId);
    }
  }
  #adjusted(r: RoastRound): WorkspaceState {
    const ws = this.#workspace.state;
    return {
      ...ws,
      profileUsed: ws.profileUsed - r.remaining,
      usage: { ...ws.usage, [r.usageKey]: (ws.usage[r.usageKey] ?? 0) - r.remaining },
    };
  }
  start(chatId: string) {
    const s = this.state.sessions[chatId];
    if (!s) return;
    // Release a previous round's unused optional check before quoting another round.
    if (this.state.busy) {
      this.#notice(s, 'Stop the active review before starting another.');
      return;
    }
    const p = researchPreview(this.#workspace.state, s, this.state.totalRounds);
    if (p.blocked) {
      this.#notice(s, p.reason);
      return;
    }
    if (!this.#workspace.beginReview(chatId)) return;
    const working = { paper: s.paper, title: s.title, revision: s.revision, question: s.question };
    for (const r of s.rounds) this.#release(r);
    const chat = this.#workspace.state.chats.find((c) => c.id === chatId)!;
    const r: RoastRound = {
      id: `round-${++this.#generation}`,
      number: ++this.state.totalRounds,
      title: s.title,
      paper: s.paper,
      revision: s.revision,
      heat: s.heat,
      criteria: s.criteria,
      context: p.context,
      sources: p.sources,
      instructions: p.instructions,
      reviewers: p.roles
        .filter((role) => role.kind === 'reviewer')
        .map((role) => ({
          modelId: role.id,
          name: role.name,
          place: role.place as 'local' | 'cloud',
          status: 'queued',
          findings: [],
        })),
      status: 'reviewing',
      check: null,
      usageKey: chat.projectId ?? chat.id,
      origin:
        this.#workspace.state.projects.find((p) => p.id === chat.projectId)?.name ??
        'Temporary chat',
      remaining: p.upper,
      activeHold: 0,
      unknownHold: 0,
      actual: 0,
      profileId: s.profileId,
      frozen: structuredClone({
        mode: s.mode,
        question: s.question,
        authorId: s.authorId,
        paperAuthor: s.paperAuthor,
        paperAuthors: s.paperAuthors,
        scope: s.scope,
        fileIds: s.fileIds,
        chatIds: s.chatIds,
        reviewerIds: s.reviewerIds,
        scenario: s.scenario,
      }),
      callUpper: this.#workspace.state.priceUpper,
      question: s.question,
      author: p.roles.find((role) => role.kind === 'author')
        ? {
            modelId: p.roles[0].id,
            name: p.roles[0].name,
            place: p.roles[0].place as 'local' | 'cloud',
            status: 'queued',
          }
        : null,
      reconciliation: null,
      optionalClosed: false,
    };
    s.rounds.push(r);
    s.sealed = true;
    s.notice = '';
    this.state.busy = { chatId, roundId: r.id, kind: 'roast' };
    if (p.upper) this.#workspace.adjustReviewAllowance(r.usageKey, p.upper);
    this.#emit();
    const token = this.#generation;
    const role = (index: number) => {
      if (token !== this.#generation) return;
      const reviewer = r.reviewers[index];
      this.#runRole(
        s,
        r,
        reviewer.modelId,
        r.paper,
        `${reviewer.name} · blind review ${index + 1}/2`,
        token,
        () => {
          if (r.frozen.scenario === 'reviewer-fails' && index === 1) {
            reviewer.status = 'failed';
            this.stop();
            reviewer.status = 'failed';
            this.#notice(
              s,
              'Reviewer 2 failed. The completed first review remains visible; this pass is incomplete.',
            );
            return;
          }
          reviewer.status = 'complete';
          reviewer.findings = scriptedFindings(reviewer.name, r.id, index, r.heat);
          reviewer.input = { paper: r.paper, context: r.context, peerVerdicts: false };
          if (r.frozen.scenario === 'disagreement' && index === 1) {
            reviewer.findings[0].title = 'Different assessment of the pilot scope';
            reviewer.findings[0].critique =
              'The descriptive pilot can inform a next experiment, although it cannot validate universal accuracy. This blind fixture takes a more permissive view of the scoped claim; disagreement stays visible.';
          }
          if (r.frozen.scenario === 'missing-evidence')
            reviewer.findings[0].request =
              'A source excerpt is missing. The claim remains unsupported until you supply verifiable evidence; this demo cannot verify it.';
          for (const finding of reviewer.findings) {
            for (const previous of s.rounds.slice(0, -1).toReversed()) {
              const earlier = previous.reviewers
                .find((p) => p.modelId === reviewer.modelId)
                ?.findings.find((f) => f.title === finding.title);
              if (earlier?.response.trim()) {
                finding.response = earlier.response;
                finding.evidence = earlier.evidence;
                finding.action = earlier.action;
                finding.carriedFrom = `round ${previous.number} · v${previous.revision}`;
                break;
              }
            }
          }
          this.#emit();
          if (index === 0) role(1);
          else {
            r.status = 'complete';
            this.state.busy = null;
            this.#workspace.finishReview();
            this.#notice(
              s,
              'Blind fixture reviews complete. Original critiques stay frozen while you revise or rebut.',
            );
          }
        },
      );
    };
    if (r.author) {
      r.author.status = 'authoring';
      this.#runRole(
        s,
        r,
        r.author.modelId,
        `Research question: ${r.question}`,
        `${r.author.name} · authoring from frozen sources`,
        token,
        () => {
          r.author!.status = 'complete';
          r.paper = `# ${r.title}\n\nResearch question\n${r.question}\n\n${samplePaper}\n\nAuthored sample fixture by ${r.author!.name}. No actual inference or source verification.`;
          if (
            s.paper === working.paper &&
            s.title === working.title &&
            s.revision === working.revision &&
            s.question === working.question
          ) {
            s.paper = r.paper;
            s.paperAuthor = r.author!.modelId;
            s.paperAuthors = [r.author!.modelId];
            s.sealed = true;
          }
          this.#emit();
          role(0);
        },
      );
    } else role(0);
  }
  #runRole(
    s: ResearchSession,
    r: RoastRound,
    modelId: string,
    paper: string,
    phase: string,
    token: number,
    done: () => void,
  ) {
    const ws = this.#workspace.state;
    const chat = ws.chats.find((c) => c.id === s.chatId)!;
    const frozen = { ...s, ...r.frozen, title: r.title, paper, criteria: r.criteria };
    const route = packet(this.#adjusted(r), chat, frozen, modelId);
    if (route.blocked || contextSignature(ws, chat, frozen) !== r.context) {
      this.stop();
      this.#notice(
        s,
        'Review became ineligible or its selected sources changed. Partial results remain; no model was substituted.',
      );
      return;
    }
    const model = ws.models.find((m) => m.id === modelId)!;
    const reviewer = r.reviewers.find((p) => p.modelId === modelId);
    if (this.state.busy?.kind === 'roast' && reviewer) reviewer.status = 'reviewing';
    const dispatch = () => {
      if (token !== this.#generation) return;
      const current = this.#workspace.state;
      const currentChat = current.chats.find((c) => c.id === s.chatId)!;
      const next = packet(this.#adjusted(r), currentChat, frozen, modelId);
      const cap =
        current.projects.find((p) => p.id === currentChat.projectId)?.cap ?? currentChat.cap;
      if (
        next.blocked ||
        model.state !== 'ready' ||
        (model.place === 'cloud' && next.upper !== r.callUpper) ||
        contextSignature(current, currentChat, frozen) !== r.context ||
        (model.place === 'cloud' &&
          (current.profileUsed > 25 || (current.usage[r.usageKey] ?? 0) > cap))
      ) {
        this.stop();
        this.#notice(
          s,
          'Eligibility changed before this role dispatched. Review remains incomplete.',
        );
        return;
      }
      this.#workspace.reviewRole(modelId, phase);
      if (model.place === 'cloud') {
        r.remaining = Math.max(0, r.remaining - r.callUpper);
        r.activeHold = r.callUpper;
      }
      this.#emit();
      this.#later(() => {
        if (token !== this.#generation) return;
        if (model.place === 'cloud') {
          r.activeHold = 0;
          r.actual += 0.02;
          this.#workspace.adjustReviewAllowance(r.usageKey, -(r.callUpper - 0.02), r.profileId);
        }
        done();
      }, 1100);
    };
    if (route.preparation) {
      this.#workspace.reviewRole(modelId, `Loading ${model.name} · simulated`, true);
      this.#emit();
      this.#later(() => {
        if (token !== this.#generation) return;
        if (this.#workspace.state.setupScenario === 'load-failed') {
          this.stop();
          if (reviewer) reviewer.status = 'failed';
          if (r.author && r.author.modelId === modelId) r.author.status = 'failed';
          this.#notice(
            s,
            'The planned model load failed. This pass is incomplete; no reviewer or cloud substitute was used.',
          );
          return;
        }
        this.#workspace.readyReviewModel(modelId);
        dispatch();
      }, 650);
    } else dispatch();
  }
  checkResponses(
    chatId: string,
    roundId: string,
    scenario: 'normal' | 'pushback' | 'mixed' = 'normal',
  ) {
    const s = this.state.sessions[chatId];
    const r = s?.rounds.find((r) => r.id === roundId);
    if (!r) return;
    const findings = r.reviewers.flatMap((p) => p.findings);
    if (
      r.status !== 'complete' ||
      r.check ||
      r.optionalClosed ||
      r.reconciliation ||
      this.state.busy ||
      findings.some((f) => !f.response.trim())
    ) {
      this.#notice(
        s,
        'Finish both reviews and draft every response. One optional response check is allowed per round.',
      );
      return;
    }
    const chat = this.#workspace.state.chats.find((c) => c.id === chatId)!;
    if (contextSignature(this.#workspace.state, chat, s) !== r.context) {
      this.#notice(
        s,
        'Sources, instructions, privacy or criteria changed. Start a fresh roast rather than reusing its check.',
      );
      return;
    }
    const checker = r.reviewers[0];
    const responsePacket = s.paper + '\n' + rebuttalLetter(s, r);
    const p = packet(this.#adjusted(r), chat, { ...s, paper: responsePacket }, checker.modelId);
    if (p.blocked) {
      this.#notice(s, p.reason);
      return;
    }
    if (!this.#workspace.beginReview(chatId)) return;
    const token = ++this.#generation;
    r.check = {
      revision: s.revision,
      paper: s.paper,
      model: checker.name,
      context: r.context,
      status: 'checking',
      responses: Object.fromEntries(findings.map((f) => [f.id, responseSignature(f)])),
      outcomes: {},
    };
    s.sealed = true;
    this.state.busy = { chatId, roundId: r.id, kind: 'rebuttal' };
    this.#emit();
    const captured = structuredClone(findings);
    this.#runRole(
      s,
      r,
      checker.modelId,
      responsePacket,
      `${checker.name} · response check`,
      token,
      () => {
        r.check!.status = 'complete';
        r.check!.outcomes = Object.fromEntries(
          captured.map((f, i) => [
            f.id,
            scenario === 'pushback' ||
            (scenario === 'mixed' && i % 2) ||
            (f.action === 'revise' && r.check!.paper === r.paper)
              ? 'needs-work'
              : f.response.trim().length < 20 ||
                  (f.action !== 'limit' && !f.evidence.trim()) ||
                  f.action === 'evidence'
                ? 'needs-evidence'
                : 'supported',
          ]),
        );
        this.#release(r);
        this.state.busy = null;
        this.#workspace.finishReview();
        this.#notice(
          s,
          'Scripted response check complete. Accept each supported resolution deliberately; original critiques remain unchanged.',
        );
      },
    );
  }
  closeFinding(chatId: string, roundId: string, findingId: string) {
    const s = this.state.sessions[chatId];
    const r = s?.rounds.find((r) => r.id === roundId);
    const f = r?.reviewers.flatMap((r) => r.findings).find((f) => f.id === findingId);
    if (!f || !r) return;
    const chat = this.#workspace.state.chats.find((c) => c.id === chatId)!;
    if (
      findingStatus(r, f, s, contextSignature(this.#workspace.state, chat, s)) !==
      'Ready for your decision'
    )
      return;
    f.decision = { revision: s.revision, signature: responseSignature(f) };
    this.#emit();
    this.#workspace.researchChanged(chatId);
  }
  reconcile(chatId: string, roundId: string) {
    const s = this.state.sessions[chatId];
    const r = s?.rounds.find((r) => r.id === roundId);
    if (!r) return;
    const chat = this.#workspace.state.chats.find((c) => c.id === chatId)!;
    if (
      r.status !== 'complete' ||
      r.check ||
      r.optionalClosed ||
      r.reconciliation ||
      this.state.busy ||
      contextSignature(this.#workspace.state, chat, s) !== r.context ||
      s.paper !== r.paper ||
      s.revision !== r.revision
    ) {
      this.#notice(
        s,
        'Reconciliation needs the complete current blind pass and its unused optional-call allowance. Changed versions require new review.',
      );
      return;
    }
    const reconciler = r.author ?? r.reviewers[0];
    const paper =
      r.paper +
      '\nCommitted reviewer findings:\n' +
      r.reviewers
        .flatMap((p) => p.findings)
        .map((f) => `${f.title}: ${f.critique}`)
        .join('\n');
    if (packet(this.#adjusted(r), chat, { ...s, paper }, reconciler.modelId).blocked) {
      this.#notice(
        s,
        'The reconciliation packet does not fit current eligibility. Reduce scope or start another explicit round.',
      );
      return;
    }
    if (!this.#workspace.beginReview(chatId)) return;
    const token = ++this.#generation;
    this.state.busy = { chatId, roundId: r.id, kind: 'reconcile' };
    r.reconciliation = { model: reconciler.name, status: 'running', revision: s.revision };
    this.#emit();
    this.#runRole(
      s,
      r,
      reconciler.modelId,
      paper,
      `${reconciler.name} · optional reconciliation`,
      token,
      () => {
        r.reconciliation!.status = 'complete';
        const proposed = r.paper
          .replace(
            'produces reliable conclusions for every researcher',
            'describes an unevaluated small pilot',
          )
          .replace(
            'multi-model agreement guarantees correct research',
            'model agreement can still be wrong',
          );
        const reconciled =
          proposed +
          '\n\nReconciliation fixture: explicitly limit the claim and retain missing evidence. This v2 has not been reviewed.';
        r.reconciliation!.paper = reconciled;
        if (
          s.paper !== r.paper ||
          s.revision !== r.revision ||
          contextSignature(this.#workspace.state, chat, s) !== r.context
        ) {
          this.#release(r);
          this.state.busy = null;
          this.#workspace.finishReview();
          this.#notice(
            s,
            'Your working draft or scope changed. The reconciliation proposal is retained separately; no edits were overwritten. It still needs fresh review.',
          );
          return;
        }
        s.revision++;
        s.paper = reconciled;
        s.paperAuthor = reconciler.modelId;
        s.paperAuthors = [
          ...new Set([
            ...s.paperAuthors,
            ...(!['unknown', 'human'].includes(r.frozen.paperAuthor) ? [r.frozen.paperAuthor] : []),
            reconciler.modelId,
          ]),
        ];
        s.mode = 'existing';
        s.sealed = true;
        this.#release(r);
        this.state.busy = null;
        this.#workspace.finishReview();
        this.#notice(
          s,
          'Reconciled v2 is unreviewed. v1 findings and decisions stay historical; select distinct non-author reviewers and explicitly request round two.',
        );
      },
    );
  }
  finishRound(chatId: string, roundId: string) {
    const s = this.state.sessions[chatId];
    const r = s?.rounds.find((r) => r.id === roundId);
    if (!r || this.state.busy) return;
    r.optionalClosed = true;
    this.#release(r);
    this.#notice(
      s,
      'Unused optional allowance released. This round’s extra call is closed; reviews and decisions remain. A new pass still needs explicit intent.',
    );
  }
  humanDecision(chatId: string, roundId: string, decision: 'accept' | 'changes') {
    const s = this.state.sessions[chatId];
    const r = s?.rounds.find((r) => r.id === roundId);
    const ws = this.#workspace.state;
    const chat = ws.chats.find((c) => c.id === chatId);
    if (!r || !chat) return;
    if (
      r !== s.rounds.at(-1) ||
      r.status !== 'complete' ||
      this.state.busy ||
      r.paper !== s.paper ||
      r.revision !== s.revision ||
      r.context !== contextSignature(ws, chat, s)
    ) {
      this.#notice(
        s,
        'Human decisions require the complete current draft/version and source manifest. Old or incomplete reviews cannot accept another version.',
      );
      return;
    }
    if (
      decision === 'accept' &&
      r.reviewers
        .flatMap((p) => p.findings)
        .some(
          (f) =>
            !['Rebuttal accepted', 'Addressed by revision', 'Limitation accepted'].includes(
              findingStatus(r, f, s, r.context),
            ),
        )
    ) {
      this.#notice(
        s,
        'Address every finding for this version before accepting. You can record Needs changes without claiming resolution.',
      );
      return;
    }
    s.signoff = {
      revision: s.revision,
      paper: s.paper,
      context: humanSignature(ws, s, r),
      roundId: r.id,
      decision,
    };
    this.#notice(
      s,
      'Your scoped human decision is recorded in this simulation. It does not prove research correctness or authorize execution/publication.',
    );
  }
  stop() {
    const busy = this.state.busy;
    if (!busy) return;
    ++this.#generation;
    for (const id of this.#timers) this.#timer.clear(id);
    this.#timers.clear();
    const s = this.state.sessions[busy.chatId];
    const r = s.rounds.find((r) => r.id === busy.roundId)!;
    r.unknownHold += r.activeHold;
    r.activeHold = 0;
    this.#release(r);
    if (busy.kind === 'roast') {
      r.status = 'incomplete';
      r.reviewers.filter((p) => p.status !== 'complete').forEach((p) => (p.status = 'canceled'));
      if (r.author && r.author.status !== 'complete') r.author.status = 'canceled';
    } else if (busy.kind === 'rebuttal') r.check!.status = 'canceled';
    else if (r.reconciliation) r.reconciliation.status = 'canceled';
    this.state.busy = null;
    this.#workspace.finishReview();
    this.#notice(
      s,
      'Stopped. Partial findings remain; dispatched cloud cost can be unknown. No automatic replay.',
    );
  }
  dispose() {
    this.stop();
    for (const s of Object.values(this.state.sessions)) for (const r of s.rounds) this.#release(r);
    this.#off();
    this.#offCopy();
    this.#offJournal();
    this.#listeners.clear();
  }
}

function scriptedFindings(reviewer: string, roundId: string, role: number, heat: Heat): Finding[] {
  const tone = {
    gentle: 'This would benefit from a clearer evidence boundary.',
    spicy: 'This claim is doing more work than your evidence.',
    scorched: 'The conclusion has sprinted past the data and left the methods behind.',
  }[heat];
  const fixtures =
    role === 0
      ? [
          [
            'Universal claim, tiny sample',
            'Major',
            'Abstract / Methods',
            'Twelve colleagues on one laptop cannot establish a result for every researcher.',
            'Narrow the claim or cite a representative validation study.',
          ],
          [
            'Faster is not measured',
            'Moderate',
            'Results',
            'A reported feeling of speed is different from measured task-completion time.',
            'Separate subjective preference from measured speed; name a timing protocol.',
          ],
        ]
      : [
          [
            'Agreement is not correctness',
            'Major',
            'Results / conclusion',
            'Two models can share an unsupported assumption. Agreement alone cannot validate research.',
            'Remove the guarantee and specify a reference, rubric or independent check.',
          ],
          [
            'Missing accuracy and hardware limits',
            'Moderate',
            'Methods / Limitations',
            'Accuracy, hardware diversity and external validity have not been evaluated.',
            'State these limits and describe the next evaluation.',
          ],
        ];
  return fixtures.map(([title, severity, locator, critique, request], i) => ({
    id: `${roundId}-${role}-${i}`,
    reviewer,
    title,
    severity: severity as Finding['severity'],
    locator,
    critique: `${tone} ${critique} (Sample-paper fixture.)`,
    request,
    response: '',
    evidence: '',
    action: 'rebut',
    decision: null,
  }));
}
