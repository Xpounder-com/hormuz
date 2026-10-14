import type { Chat, Message, Objective, Project, ProjectFile, WorkspaceState } from './types.ts';

export type PortableProject = {
  format: 'hormuz-simulation';
  version: 1;
  project: {
    name: string;
    instructions: string;
    objective: Objective;
    cap: number;
    localOnly: boolean;
  };
  files: ProjectFile[];
  chats: Pick<
    Chat,
    'title' | 'draft' | 'instructions' | 'localOnly' | 'cap' | 'messages' | 'archived' | 'pinned'
  >[];
};
export type ImportPreview = {
  bundle: PortableProject | null;
  error: string;
  warnings: string[];
  conflict: boolean;
  name: string;
};
const bytes = (text: string) => new TextEncoder().encode(text).length;
const objectives = ['balanced', 'quality', 'speed'];
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const fields = (value: Record<string, unknown>, allowed: string[]) =>
  Object.keys(value).every((key) => allowed.includes(key));
function boundedText(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length <= max;
}

// Only portable, bounded renderer fixtures. No keys, live handles or authority.
export function exportProject(state: WorkspaceState, id: string, chatIds?: string[]): string {
  const project = state.projects.find((p) => p.id === id);
  if (!project) return '';
  const chats = state.chats.filter(
    (c) => c.projectId === id && (!chatIds || chatIds.includes(c.id)),
  );
  const bundle: PortableProject = {
    format: 'hormuz-simulation',
    version: 1,
    project: {
      name: project.name,
      instructions: project.instructions,
      objective: project.objective,
      cap: project.cap,
      localOnly: !!project.localOnly,
    },
    files: project.files.map((f) => ({
      id: f.id,
      name: f.name,
      text: f.text,
      revision: f.revision,
      localOnly: f.localOnly,
      status: f.status,
      ownership: f.ownership ?? 'copy',
    })),
    chats: chats.map((c) => ({
      title: c.title,
      draft: c.draft,
      instructions: c.instructions,
      localOnly: c.localOnly,
      cap: c.cap,
      archived: !!c.archived,
      pinned: !!c.pinned,
      messages: c.messages.map((m) => ({
        id: m.id,
        role: m.role,
        text: m.text,
        ...(m.receipt
          ? {
              receipt: {
                model: m.receipt.model,
                place: m.receipt.place,
                status: m.receipt.status,
                context: [...m.receipt.context],
                instructions: m.receipt.instructions,
                origin: m.receipt.origin,
                estimate: m.receipt.estimate,
                held: m.receipt.held,
                ...(m.receipt.actual !== undefined ? { actual: m.receipt.actual } : {}),
              },
            }
          : {}),
      })),
    })),
  };
  return JSON.stringify(bundle, null, 2);
}

export function inspectImport(text: string, state: WorkspaceState): ImportPreview {
  const fail = (error: string): ImportPreview => ({
    bundle: null,
    error,
    warnings: [],
    conflict: false,
    name: '',
  });
  if (bytes(text) > 1024 * 1024)
    return fail('The sample import limit is 1 MiB. Split the export before importing.');
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return fail('This is not valid JSON. Paste a Hormuz simulation export.');
  }
  if (
    !record(value) ||
    !fields(value, ['format', 'version', 'project', 'files', 'chats']) ||
    value.format !== 'hormuz-simulation' ||
    value.version !== 1
  )
    return fail(
      'Unsupported format, version or fields. Credentials, connection bindings and runtime handles cannot be imported.',
    );
  const p = value.project;
  if (
    !record(p) ||
    !fields(p, ['name', 'instructions', 'objective', 'cap', 'localOnly']) ||
    !boundedText(p.name, 100) ||
    !p.name.trim() ||
    !boundedText(p.instructions, 8000) ||
    !objectives.includes(String(p.objective)) ||
    typeof p.cap !== 'number' ||
    !Number.isFinite(p.cap) ||
    p.cap < 0 ||
    p.cap > 10 ||
    typeof p.localOnly !== 'boolean'
  )
    return fail('Project metadata is invalid or outside the sample bounds.');
  if (
    !Array.isArray(value.files) ||
    value.files.length > 20 ||
    !Array.isArray(value.chats) ||
    value.chats.length > 100
  )
    return fail('The sample supports at most 20 copies and 100 chats per import.');
  const files: ProjectFile[] = [];
  const warnings: string[] = [];
  const seen = new Set<string>();
  for (const f of value.files) {
    if (
      !record(f) ||
      !fields(f, ['id', 'name', 'text', 'revision', 'localOnly', 'status', 'ownership']) ||
      !boundedText(f.id, 100) ||
      seen.has(f.id) ||
      !boundedText(f.name, 150) ||
      !boundedText(f.text, 256 * 1024) ||
      typeof f.revision !== 'number' ||
      !Number.isSafeInteger(f.revision) ||
      f.revision < 1 ||
      typeof f.localOnly !== 'boolean'
    )
      return fail(
        'File metadata is invalid or contains duplicate identities. Nothing will be overwritten.',
      );
    seen.add(f.id);
    if (
      !/\.(md|txt)$/i.test(f.name) ||
      f.ownership === 'unbound-link' ||
      bytes(f.text) > 256 * 1024 ||
      f.status !== 'available'
    ) {
      warnings.push(
        `${f.name}: unsupported, unavailable or unbound entry will be skipped. Import a supported text copy separately.`,
      );
      continue;
    }
    files.push({
      id: f.id,
      name: f.name,
      text: f.text,
      revision: f.revision,
      localOnly: f.localOnly,
      status: 'available',
      ownership: 'copy',
    });
  }
  const chats: PortableProject['chats'] = [];
  let count = 0;
  for (const c of value.chats) {
    if (
      !record(c) ||
      !fields(c, [
        'title',
        'draft',
        'instructions',
        'localOnly',
        'cap',
        'messages',
        'archived',
        'pinned',
      ]) ||
      !boundedText(c.title, 100) ||
      !boundedText(c.draft, 30000) ||
      !boundedText(c.instructions, 8000) ||
      typeof c.localOnly !== 'boolean' ||
      (c.cap !== undefined &&
        (typeof c.cap !== 'number' || !Number.isFinite(c.cap) || c.cap < 0 || c.cap > 10)) ||
      (c.archived !== undefined && typeof c.archived !== 'boolean') ||
      (c.pinned !== undefined && typeof c.pinned !== 'boolean') ||
      !Array.isArray(c.messages) ||
      (count += c.messages.length) > 500
    )
      return fail('Chat content is invalid or exceeds the sample message bounds.');
    const messages: Message[] = [];
    for (const m of c.messages) {
      if (
        !record(m) ||
        !fields(m, ['id', 'role', 'text', 'receipt']) ||
        !boundedText(m.id, 100) ||
        !['user', 'assistant'].includes(String(m.role)) ||
        !boundedText(m.text, 30000)
      )
        return fail('An imported message is invalid.');
      const message: Message = { id: m.id, role: m.role as Message['role'], text: m.text };
      if (m.receipt !== undefined) {
        const r = m.receipt;
        if (
          !record(r) ||
          !fields(r, [
            'model',
            'place',
            'status',
            'context',
            'instructions',
            'origin',
            'estimate',
            'held',
            'actual',
          ]) ||
          !boundedText(r.model, 100) ||
          !['local', 'cloud'].includes(String(r.place)) ||
          ![
            'preparing',
            'running',
            'completed',
            'canceled',
            'blocked',
            'failed',
            'unknown',
          ].includes(String(r.status)) ||
          !Array.isArray(r.context) ||
          r.context.length > 20 ||
          !r.context.every((x) => boundedText(x, 200)) ||
          !boundedText(r.instructions, 32256) ||
          !boundedText(r.origin, 100) ||
          typeof r.estimate !== 'number' ||
          !Number.isFinite(r.estimate) ||
          r.estimate < 0 ||
          r.estimate > 1 ||
          typeof r.held !== 'number' ||
          !Number.isFinite(r.held) ||
          r.held < 0 ||
          r.held > 1 ||
          (r.actual !== undefined &&
            (typeof r.actual !== 'number' ||
              !Number.isFinite(r.actual) ||
              r.actual < 0 ||
              r.actual > 1))
        )
          return fail(
            'Historical receipt metadata is invalid. Live continuation or execution handles are not portable.',
          );
        message.receipt = {
          model: r.model,
          place: r.place as 'local' | 'cloud',
          status: ['running', 'preparing'].includes(String(r.status))
            ? 'unknown'
            : (r.status as NonNullable<Message['receipt']>['status']),
          context: r.context as string[],
          instructions: r.instructions,
          origin: r.origin,
          estimate: r.estimate,
          held: r.held,
          ...(typeof r.actual === 'number' ? { actual: r.actual } : {}),
          imported: true,
        };
      }
      messages.push(message);
    }
    chats.push({
      title: c.title,
      draft: c.draft,
      instructions: c.instructions,
      localOnly: c.localOnly,
      cap: c.cap === undefined ? 0 : (c.cap as number),
      messages,
      archived: !!c.archived,
      pinned: !!c.pinned,
    });
  }
  const conflict = state.projects.some((existing) => existing.name === p.name);
  let name = p.name;
  for (let suffix = 1; state.projects.some((existing) => existing.name === name); suffix++)
    name = `${p.name.slice(0, 75)} (imported ${suffix})`;
  return {
    bundle: {
      format: 'hormuz-simulation',
      version: 1,
      project: {
        name: p.name,
        instructions: p.instructions,
        objective: p.objective as Objective,
        cap: p.cap,
        localOnly: p.localOnly,
      },
      files,
      chats,
    },
    error: '',
    warnings,
    conflict,
    name,
  };
}

export function searchHistory(
  state: WorkspaceState,
  query: string,
  scope: string,
  mode: 'title' | 'content',
  archives: 'active' | 'archived' | 'all',
) {
  if (['unavailable', 'corrupt'].includes(state.storage.status)) return [];
  const needle = query.trim().toLocaleLowerCase().slice(0, 200);
  return state.chats
    .filter((c) => {
      if (!c.projectId || (scope !== 'profile' && c.projectId !== scope)) return false;
      const project = state.projects.find((p) => p.id === c.projectId);
      const archived = !!(c.archived || project?.archived);
      if (archives !== 'all' && archived !== (archives === 'archived')) return false;
      const content =
        mode === 'title' ? c.title : [c.title, ...c.messages.map((m) => m.text)].join('\n');
      return content.toLocaleLowerCase().includes(needle);
    })
    .toSorted((a, b) => Number(!!b.pinned) - Number(!!a.pinned))
    .slice(0, 100);
}

export function movePreview(state: WorkspaceState, chatId: string, destination: string) {
  const chat = state.chats.find((c) => c.id === chatId);
  const target = state.projects.find((p) => p.id === destination);
  const source = state.projects.find((p) => p.id === chat?.projectId);
  if (!chat || !source || !target || target.id === source.id) return null;
  return {
    target,
    copies: source.files.filter((f) => chat.selectedFiles.includes(f.id)),
    localOnly: chat.localOnly || !!source.localOnly || !source.cloudAllowed,
    busy: state.attempt?.chatId === chat.id || state.externalActivity?.chatId === chat.id,
  };
}
