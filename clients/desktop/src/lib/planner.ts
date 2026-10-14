import type { Chat, Model, RoutePlan, WorkspaceState } from './types.ts';
import { activeSkills } from './skills.ts';

export function activeChat(state: WorkspaceState): Chat {
  return state.chats.find((c) => c.id === state.activeChatId)!;
}

// Pure fixture routing: no classifiers, network, loading, saving or reservations.
export function preview(state: WorkspaceState, chat: Chat = activeChat(state)): RoutePlan {
  const project = state.projects.find((p) => p.id === chat.projectId);
  const context = chat.selectedFiles.map((id) => project?.files.find((f) => f.id === id));
  const files = context.filter((f) => f !== undefined);
  const instructions = [
    state.appInstructions,
    project?.instructions,
    ...activeSkills(project?.skillIds ?? chat.skillIds, chat.draft).map(
      (s) => `Skill: ${s.name} (${s.id} v${s.version})\n${s.instructions}`,
    ),
    chat.instructions,
  ]
    .filter(Boolean)
    .join('\n\n');
  const complete = chat.messages.filter(
    (m) => m.role === 'assistant' && m.receipt?.status === 'completed',
  );
  const recentIds = new Set(complete.slice(-10).map((m) => m.id));
  const history: string[] = [];
  for (let i = 0; i < chat.messages.length; i++) {
    const m = chat.messages[i];
    if (recentIds.has(m.id)) history.push(chat.messages[i - 1]?.text ?? '', m.text);
  }
  const input = [instructions, ...files.map((f) => f.text), ...history, chat.draft].join('\n');
  const tokens = Math.ceil(input.length / 4); // explicitly synthetic, not a tokenizer
  const cloudAllowed =
    (project?.cloudAllowed ?? chat.cloudAllowed) &&
    !project?.localOnly &&
    !state.profileLocalOnly &&
    !chat.localOnly &&
    !files.some((f) => f.localOnly);
  const objective =
    chat.objective === 'inherit' ? (project?.objective ?? state.appObjective) : chat.objective;
  let contextError = '';
  if (context.some((f) => f === undefined))
    contextError = 'A selected copy is missing. Remove it from context.';
  else if (files.some((f) => f.status !== 'available' || f.ownership === 'unbound-link'))
    contextError = 'Selected context is unsupported, missing or too large. Remove it before Send.';
  else if (
    files.length > 20 ||
    files.some((f) => new TextEncoder().encode(f.text).length > 256 * 1024) ||
    files.reduce((sum, f) => sum + new TextEncoder().encode(f.text).length, 0) > 1024 * 1024
  )
    contextError = 'Selected copies exceed the sample size limits. Reduce context.';
  else if (tokens > 6144)
    contextError =
      'This packet exceeds the sample input window. Reduce context or start a new chat.';
  const key = chat.projectId ?? chat.id;
  const cap = Math.min(project?.cap ?? chat.cap, chat.cap, 10);
  const upper = state.priceUpper;
  const intent =
    chat.requestIntent ??
    (chat.inheritIntent ? (project?.modelIntent ?? state.appIntent) : chat.intent);
  function ineligible(m: Model): string {
    if (contextError) return contextError;
    if (!m.fit || tokens > m.inputLimit)
      return 'Model fit is unavailable for this context. Choose a qualified sample model.';
    if (chat.continuationModel && m.id !== chat.continuationModel)
      return 'This opaque continuation belongs to another model. Start a new chat to change it.';
    if (m.place === 'local') {
      if (m.state !== 'ready' && m.state !== 'downloaded')
        return 'Set up this local model in Models before Send.';
    } else {
      if (!cloudAllowed)
        return 'Cloud is blocked by the effective Local-only setting. Review preferences and selected files.';
      if (state.connection.status !== 'verified')
        return 'Verify a demo connection in Connections before Send.';
      if (
        state.connection.provider &&
        state.connection.provider !== 'demo' &&
        m.id !== state.connection.provider
      )
        return 'This model needs its own demo provider binding. Review Connections.';
      if (
        !Number.isFinite(cap) ||
        cap < 0 ||
        !Number.isFinite(upper) ||
        upper <= 0 ||
        upper > 1 ||
        state.profileUsed + upper > 25 ||
        (state.usage[key] ?? 0) + upper > cap
      )
        return 'The fictional cloud allowance is too low. Review the finite cap.';
    }
    return '';
  }
  const pinned = intent !== 'auto';
  const eligible = state.models.filter((m) => !ineligible(m));
  const writing = /\b(write|rewrite|draft|edit|polish|poem)\b/i.test(chat.draft);
  const research =
    /\b(research|compare|sources|evidence|literature|synthesize|analyse|analyze)\b/i.test(
      chat.draft,
    );
  const ranked = eligible.toSorted((a, b) => {
    const score = (m: Model) =>
      (m.place === 'local' ? 4 : 0) +
      (m.specialty === 'writing' && writing ? 8 : 0) +
      (m.place === 'cloud' && research && objective !== 'speed' ? 10 : 0) +
      (m.place === 'cloud' && objective === 'quality' ? 6 : 0) +
      (m.id === 'qwen' ? 1 : 0);
    return score(b) - score(a);
  });
  const model = pinned ? (state.models.find((m) => m.id === intent) ?? null) : (ranked[0] ?? null);
  const reason = model
    ? ineligible(model) ||
      (pinned
        ? 'Your chat pin stays selected until you choose Auto.'
        : model.place === 'cloud'
          ? 'A research match within your cloud permission and fictional allowance.'
          : writing && model.specialty === 'writing'
            ? 'A local writing match; already downloaded.'
            : 'Local baseline for the available sample evidence.')
    : contextError ||
      (intent === 'unbound'
        ? 'Imported model intent is unbound. Choose Auto or explicitly match this profile’s inventory.'
        : pinned
          ? 'The selected model is unavailable. Keep the intent or explicitly choose Auto / another model.'
          : 'No eligible model. Set up a local model or review cloud preferences.');
  const blocked = !model || Boolean(model && ineligible(model));
  return {
    model,
    blocked,
    reason,
    preparation: model?.place === 'local' && model.state === 'downloaded',
    tokens,
    estimate: model?.place === 'cloud' ? 0.02 : 0,
    upper: model?.place === 'cloud' ? upper : 0,
    context: files,
    instructions,
    objective,
    cloudAllowed,
    historyCount: Math.min(complete.length, 10),
    intent,
    fingerprint: JSON.stringify([
      chat.id,
      chat.projectId,
      chat.draft,
      intent,
      instructions,
      files,
      history,
      objective,
      cloudAllowed,
      cap,
      model?.place === 'cloud' ? upper : 0,
      model?.identity,
      chat.continuationModel,
      model?.id,
      model?.place,
      blocked,
    ]),
  };
}
