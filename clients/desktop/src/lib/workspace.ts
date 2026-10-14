import { emptyChat, initialState, sampleFiles } from './fixtures.ts';
import { activeChat, preview } from './planner.ts';
import { inspectImport, movePreview } from './workflows.ts';
import { skills } from './skills.ts';
import type {
  Chat,
  ConnectionScenario,
  Objective,
  ProjectFile,
  Receipt,
  Timer,
  WorkspaceState,
} from './types.ts';

const defaultTimer: Timer = {
  after: (fn, ms) => setTimeout(fn, ms),
  clear: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
};

// Renderer-only fixture owner. These methods are not a production host/IPC API.
export class MockWorkspace {
  state: WorkspaceState;
  #timer: Timer;
  #timers = new Set<unknown>();
  #listeners = new Set<(state: WorkspaceState) => void>();
  #temporaryCopies = new Set<(sourceId: string, copyId: string) => void>();
  #sequence = 0;
  #disposed = false;
  #modelJobs = new Map<string, number>();
  #connectionGeneration = 0;
  #saveTimer: unknown = null;
  #journals = new Map<string, { projects: WorkspaceState['projects']; chats: Chat[] }>();
  #profiles = new Map<string, WorkspaceState>();
  #journalListeners = new Set<
    (profile: WorkspaceState['profileId'], kind: 'save' | 'restart', chatIds: string[]) => void
  >();

  constructor(state = initialState(), timer = defaultTimer) {
    this.state = state;
    this.#timer = timer;
    this.#commitJournal();
  }
  subscribe(listener: (state: WorkspaceState) => void) {
    this.#listeners.add(listener);
    listener(structuredClone(this.state));
    return () => {
      this.#listeners.delete(listener);
    };
  }
  onTemporaryCopy(listener: (sourceId: string, copyId: string) => void) {
    this.#temporaryCopies.add(listener);
    return () => {
      this.#temporaryCopies.delete(listener);
    };
  }
  onJournal(
    listener: (
      profile: WorkspaceState['profileId'],
      kind: 'save' | 'restart',
      chatIds: string[],
    ) => void,
  ) {
    this.#journalListeners.add(listener);
    return () => {
      this.#journalListeners.delete(listener);
    };
  }
  #emit() {
    this.state.revision++;
    for (const listener of this.#listeners) listener(structuredClone(this.state));
  }
  #id(prefix: string) {
    return `${prefix}-${++this.#sequence}`;
  }
  #later(fn: () => void, ms: number) {
    let handle: unknown;
    handle = this.#timer.after(() => {
      this.#timers.delete(handle);
      if (!this.#disposed) fn();
    }, ms);
    this.#timers.add(handle);
    return handle;
  }
  #commitJournal() {
    this.#journals.set(
      this.state.profileId,
      structuredClone({
        projects: this.state.projects,
        chats: this.state.chats.filter((c) => c.projectId !== null),
      }),
    );
    for (const listener of this.#journalListeners)
      listener(
        this.state.profileId,
        'save',
        this.state.chats.filter((c) => c.projectId !== null).map((c) => c.id),
      );
  }
  #clearSaveTimer() {
    if (this.#saveTimer !== null) {
      this.#timer.clear(this.#saveTimer);
      this.#timers.delete(this.#saveTimer);
      this.#saveTimer = null;
    }
  }
  #saveNow() {
    this.#clearSaveTimer();
    if (this.state.storage.status !== 'available') {
      this.state.storage.save = 'failed';
      this.state.storage.error =
        'Save failed. Work remains in memory; recover or copy an export before leaving.';
      return false;
    }
    this.#commitJournal();
    this.state.storage.save = 'saved';
    this.state.storage.error = '';
    return true;
  }
  #changed() {
    this.#clearSaveTimer();
    if (this.state.storage.status !== 'available') {
      this.state.storage.save = 'failed';
      this.state.storage.error =
        'Save failed. Work remains in memory; retry after recovering storage.';
    } else {
      this.state.storage.save = 'saving';
      const profile = this.state.profileId;
      this.#saveTimer = this.#later(() => {
        this.#saveTimer = null;
        if (this.state.profileId !== profile) return;
        this.#saveNow();
        this.#emit();
      }, 400);
    }
    this.#emit();
  }
  #notice(message: string) {
    this.state.notice = message;
    this.#emit();
  }
  clearNotice() {
    this.state.notice = '';
    this.#emit();
  }
  notify(message: string) {
    this.#notice(message);
  }
  navigate(chatId: string) {
    if (!this.state.chats.some((c) => c.id === chatId)) return;
    this.state.activeChatId = chatId;
    this.#emit();
  }
  selectProject(projectId: string) {
    const existing =
      this.state.chats.find((c) => c.projectId === projectId && !c.archived) ??
      this.state.chats.find((c) => c.projectId === projectId);
    if (existing) this.navigate(existing.id);
    else this.newChat(projectId);
  }
  newChat(projectId: string | null = activeChat(this.state).projectId) {
    if (this.state.chats.length >= 500) {
      this.#notice('The sample profile limit is 500 chats. Export or remove work first.');
      return;
    }
    if (projectId === null) {
      const existing = this.state.chats.find((c) => c.projectId === null);
      if (existing) {
        this.navigate(existing.id);
        return;
      }
    }
    const chat = emptyChat(this.#id('chat'), projectId);
    this.state.chats.push(chat);
    this.state.activeChatId = chat.id;
    if (projectId) this.#changed();
    else this.#emit();
  }
  createProject(name: string) {
    if (!name.trim()) return;
    if (this.state.chats.length >= 500) {
      this.#notice(
        'The profile limit is 500 chats. Export or remove work before creating a project.',
      );
      return;
    }
    if (this.state.projects.length >= 100) {
      this.#notice('The sample profile limit is 100 projects. Export or remove work first.');
      return;
    }
    const id = this.#id('project');
    this.state.projects.push({
      id,
      name: name.trim().slice(0, 100),
      color: '#b9b6ff',
      instructions: '',
      cloudAllowed: false,
      cap: 10,
      objective: this.state.appObjective,
      files: [],
      archived: false,
      modelIntent: this.state.appIntent,
    });
    this.state.firstRun = false;
    this.newChat(id);
  }
  renameProject(id: string, name: string) {
    const project = this.state.projects.find((p) => p.id === id);
    if (project && name.trim()) {
      project.name = name.trim().slice(0, 100);
      this.#changed();
    }
  }
  archiveProject(id: string) {
    const project = this.state.projects.find((p) => p.id === id);
    if (project) {
      project.archived = !project.archived;
      this.#changed();
    }
  }
  deleteProject(id: string) {
    if (this.state.attempt?.projectId === id || this.state.externalActivity?.projectId === id) {
      this.#notice('Stop the active response before deleting its project.');
      return;
    }
    const wasActive = activeChat(this.state).projectId === id;
    this.state.projects = this.state.projects.filter((p) => p.id !== id);
    this.state.chats = this.state.chats.filter((c) => c.projectId !== id);
    if (wasActive) {
      if (this.state.chats.length) this.state.activeChatId = this.state.chats[0].id;
      else this.newChat(null);
    }
    this.#changed();
    this.#notice(
      'Project content removed from this simulation. Models, connections and usage are retained.',
    );
  }
  renameChat(id: string, name: string) {
    const chat = this.state.chats.find((c) => c.id === id);
    if (chat && name.trim()) {
      chat.title = name.trim().slice(0, 100);
      if (chat.projectId) this.#changed();
      else this.#emit();
    }
  }
  deleteChat(id: string) {
    if (this.state.attempt?.chatId === id || this.state.externalActivity?.chatId === id) {
      this.#notice('Stop the active response before deleting its chat.');
      return;
    }
    const chat = this.state.chats.find((c) => c.id === id);
    if (!chat) return;
    this.state.chats = this.state.chats.filter((c) => c.id !== id);
    if (this.state.activeChatId === id) {
      const next =
        this.state.chats.find((c) => c.projectId === chat.projectId) ?? this.state.chats[0];
      if (next) this.state.activeChatId = next.id;
      else this.newChat(chat.projectId);
    }
    if (chat.projectId) this.#changed();
    this.#notice(
      chat.projectId
        ? 'Chat removed. Project copies and usage are retained.'
        : 'Temporary content and cloud permission discarded. Usage holds remain.',
    );
  }
  saveTemporary(projectId: string) {
    const source = activeChat(this.state);
    if (source.projectId !== null) return;
    if (
      this.state.attempt?.chatId === source.id ||
      this.state.externalActivity?.chatId === source.id
    ) {
      this.#notice('Stop the Temporary response before saving a copy.');
      return;
    }
    const project = this.state.projects.find((p) => p.id === projectId);
    if (!project) return;
    if (this.state.storage.status !== 'available') {
      this.state.storage.save = 'failed';
      this.state.storage.error =
        'Temporary copy failed. Its draft, replies and permission remain in memory.';
      this.#notice(this.state.storage.error);
      return;
    }
    const copy = structuredClone(source);
    copy.id = this.#id('chat');
    copy.projectId = projectId;
    copy.cloudAllowed = false;
    copy.intent = 'auto';
    copy.inheritIntent = true;
    delete copy.requestIntent;
    delete copy.continuationModel;
    copy.selectedFiles = [];
    copy.instructions = '';
    copy.objective = 'inherit';
    copy.title = source.title === 'Temporary chat' ? 'Saved from Temporary' : source.title;
    this.state.chats = this.state.chats.filter((c) => c.id !== source.id);
    this.state.chats.push(copy);
    this.state.activeChatId = copy.id;
    for (const listener of this.#temporaryCopies) listener(source.id, copy.id);
    this.#saveNow();
    this.#notice(
      `Saved a simulated copy to ${project.name}. Future requests use its context and permission; earlier receipts keep their origin.`,
    );
  }
  setDraft(text: string, chatId = this.state.activeChatId) {
    const chat = this.state.chats.find((c) => c.id === chatId);
    if (!chat) return;
    chat.draft = text.slice(0, 30000);
    if (chat.projectId) this.#changed();
    else this.#emit();
  }
  pin(intent: string) {
    const chat = activeChat(this.state);
    chat.intent = intent === 'inherit' ? 'auto' : intent;
    chat.inheritIntent = intent === 'inherit';
    delete chat.requestIntent;
    if (chat.projectId) this.#changed();
    else this.#emit();
  }
  setChatInstructions(value: string) {
    activeChat(this.state).instructions = value.slice(0, 8000);
    if (activeChat(this.state).projectId) this.#changed();
    else this.#emit();
  }
  toggleSkill(id: string) {
    if (!skills.some((s) => s.id === id)) return;
    const chat = activeChat(this.state);
    const target = this.state.projects.find((p) => p.id === chat.projectId) ?? chat;
    const ids = target.skillIds ?? [];
    target.skillIds = ids.includes(id) ? ids.filter((s) => s !== id) : [...ids, id];
    if (chat.projectId) this.#changed();
    else this.#emit();
  }
  setChatObjective(value: Objective | 'inherit') {
    activeChat(this.state).objective = value;
    if (activeChat(this.state).projectId) this.#changed();
    else this.#emit();
  }
  setLocalRestriction(value: boolean) {
    activeChat(this.state).localOnly = value;
    if (activeChat(this.state).projectId) this.#changed();
    else this.#emit();
  }
  setCloudAllowed(value: boolean) {
    const chat = activeChat(this.state);
    const project = this.state.projects.find((p) => p.id === chat.projectId);
    if (project) project.cloudAllowed = value;
    else chat.cloudAllowed = value;
    if (chat.projectId) this.#changed();
    else this.#emit();
  }
  setCap(value: number) {
    if (!Number.isFinite(value) || value < 0 || value > 10) return;
    const chat = activeChat(this.state);
    const project = this.state.projects.find((p) => p.id === chat.projectId);
    if (project) project.cap = value;
    else chat.cap = value;
    if (chat.projectId) this.#changed();
    else this.#emit();
  }
  setProjectInstructions(value: string) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    if (project) {
      project.instructions = value.slice(0, 8000);
      this.#changed();
    }
  }
  setProjectObjective(value: Objective) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    if (project) {
      project.objective = value;
      this.#changed();
    }
  }
  toggleFile(id: string) {
    const chat = activeChat(this.state);
    const project = this.state.projects.find((p) => p.id === chat.projectId);
    const file = project?.files.find((f) => f.id === id);
    if (
      !file ||
      (!chat.selectedFiles.includes(id) &&
        (file.status !== 'available' || file.ownership === 'unbound-link'))
    )
      return;
    chat.selectedFiles = chat.selectedFiles.includes(id)
      ? chat.selectedFiles.filter((f) => f !== id)
      : [...chat.selectedFiles, id];
    if (chat.projectId) this.#changed();
    else this.#emit();
  }
  addSampleFile(id: string) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    const file = sampleFiles.find((f) => f.id === id);
    if (!project || !file) return;
    if (!project.files.some((f) => f.id === id)) {
      project.files.push(structuredClone(file));
      this.#changed();
      this.#notice(`${file.name} added as a sample copy. Select it to include in a request.`);
    }
  }
  removeFile(id: string) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    if (!project) return;
    project.files = project.files.filter((f) => f.id !== id);
    this.state.chats
      .filter((c) => c.projectId === project.id)
      .forEach((c) => {
        c.selectedFiles = c.selectedFiles.filter((f) => f !== id);
      });
    this.#changed();
    this.#notice('Sample project copy removed. Submitted response snapshots are unchanged.');
  }
  pinProject(id: string) {
    const project = this.state.projects.find((p) => p.id === id);
    if (project) {
      project.pinned = !project.pinned;
      this.#changed();
    }
  }
  pinChat(id: string) {
    const chat = this.state.chats.find((c) => c.id === id);
    if (chat?.projectId) {
      chat.pinned = !chat.pinned;
      this.#changed();
    }
  }
  archiveChat(id: string) {
    const chat = this.state.chats.find((c) => c.id === id);
    if (!chat?.projectId) return;
    chat.archived = !chat.archived;
    this.#changed();
  }
  clearConversation(id: string) {
    if (this.state.attempt?.chatId === id || this.state.externalActivity?.chatId === id) {
      this.#notice('Stop active work before clearing this conversation.');
      return;
    }
    const chat = this.state.chats.find((c) => c.id === id);
    if (!chat) return;
    chat.messages = [];
    delete chat.continuationModel;
    delete chat.retryOf;
    if (chat.projectId) this.#changed();
    else this.#emit();
    this.#notice('Conversation cleared. Draft, context, project copies and separate usage remain.');
  }
  moveChat(id: string, destination: string, carryCopies: boolean) {
    const p = movePreview(this.state, id, destination);
    if (!p) return false;
    if (p.busy) {
      this.#notice('Stop active work before moving its chat.');
      return false;
    }
    if (this.state.storage.status !== 'available') {
      this.#notice('Move failed. Recover storage first; the source remains intact.');
      return false;
    }
    if (carryCopies && p.target.files.length + p.copies.length > 20) {
      this.#notice('Destination copy limit reached. Move without copies or reduce its files.');
      return false;
    }
    const chat = this.state.chats.find((c) => c.id === id)!;
    if (carryCopies)
      for (const file of p.copies)
        p.target.files.push({ ...structuredClone(file), id: this.#id('copy') });
    chat.projectId = destination;
    chat.selectedFiles = [];
    chat.instructions = '';
    chat.objective = 'inherit';
    chat.intent = 'auto';
    chat.inheritIntent = true;
    chat.localOnly = p.localOnly;
    chat.cloudAllowed = false;
    delete chat.requestIntent;
    delete chat.continuationModel;
    this.#saveNow();
    this.#notice(
      'Chat moved. Replies retain their submitted origin. Re-select copied files and review destination defaults before Send; restrictive privacy survives.',
    );
    return true;
  }
  importProject(text: string, allowPartial: boolean) {
    const p = inspectImport(text, this.state);
    if (!p.bundle || (p.warnings.length && !allowPartial)) {
      this.#notice(p.error || 'Review and explicitly accept the skipped entries first.');
      return false;
    }
    if (this.state.storage.status !== 'available') {
      this.#notice(
        'Import could not be saved. Existing work and the import preview are intact. Recover storage and retry.',
      );
      return false;
    }
    if (
      this.state.projects.length >= 100 ||
      this.state.chats.length + Math.max(1, p.bundle.chats.length) > 500
    ) {
      this.#notice('Profile sample limits reached. Nothing was imported.');
      return false;
    }
    const id = this.#id('project');
    this.state.projects.push({
      id,
      name: p.name,
      color: '#a4d9c3',
      instructions: p.bundle.project.instructions,
      objective: p.bundle.project.objective,
      cap: p.bundle.project.cap,
      localOnly: p.bundle.project.localOnly,
      cloudAllowed: false,
      modelIntent: 'unbound',
      files: p.bundle.files.map((f) => ({ ...f, id: this.#id('copy') })),
      archived: false,
    });
    const chats = p.bundle.chats.map((c) => ({
      ...emptyChat(this.#id('chat'), id),
      ...c,
      messages: c.messages.map((m) => ({ ...m, id: this.#id('message') })),
    }));
    if (!chats.length) chats.push(emptyChat(this.#id('chat'), id));
    this.state.chats.push(...chats);
    this.state.firstRun = false;
    this.state.activeChatId = chats[0].id;
    this.#saveNow();
    this.#notice(
      'Imported into new identities. Cloud is off, files are unselected and model intent is unbound. Choose Auto or a model explicitly. Historical receipts restore no permission or allowance.',
    );
    return true;
  }
  replaceFile(id: string, text: string) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    const file = project?.files.find((f) => f.id === id);
    if (!project || !file || file.ownership === 'unbound-link' || !/\.(md|txt)$/i.test(file.name))
      return false;
    if (new TextEncoder().encode(text).length > 256 * 1024) {
      this.#notice('Copy exceeds the sample 256 KiB limit. Nothing was replaced.');
      return false;
    }
    project.files = project.files.map((f) =>
      f.id === id ? { ...f, text, revision: f.revision + 1, status: 'available' } : f,
    );
    this.#changed();
    this.#notice(
      'Imported copy replaced with a new revision. Originals and submitted snapshots are unchanged.',
    );
    return true;
  }
  setFileStatus(id: string, status: ProjectFile['status']) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    if (!project) return;
    project.files = project.files.map((f) => (f.id === id ? { ...f, status } : f));
    this.#changed();
  }
  setRequestIntent(intent: string) {
    const chat = activeChat(this.state);
    chat.requestIntent = intent || undefined;
    if (chat.projectId) this.#changed();
    else this.#emit();
  }
  setProjectIntent(intent: string) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    if (project) {
      project.modelIntent = intent;
      this.#changed();
    }
  }
  setProjectRestriction(value: boolean) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    if (project) {
      project.localOnly = value;
      this.#changed();
    }
  }
  setChatCap(value: number) {
    if (!Number.isFinite(value) || value < 0 || value > 10) return;
    activeChat(this.state).cap = value;
    if (activeChat(this.state).projectId) this.#changed();
    else this.#emit();
  }
  setProfileRestriction(value: boolean) {
    this.state.profileLocalOnly = value;
    this.#emit();
  }
  setContinuation(value: boolean) {
    const chat = activeChat(this.state);
    chat.continuationModel = value ? (preview(this.state).model?.id ?? 'qwen') : undefined;
    if (chat.projectId) this.#changed();
    else this.#emit();
  }
  newConversation() {
    const source = activeChat(this.state);
    const draft = source.draft;
    if (source.projectId === null) {
      this.#notice('Save or discard this Temporary chat before starting another.');
      return;
    }
    this.newChat(source.projectId);
    this.setDraft(draft);
  }
  retryResponse(id: string) {
    const chat = activeChat(this.state);
    const index = chat.messages.findIndex((m) => m.id === id);
    const previous = chat.messages[index];
    if (!previous?.receipt || index < 1 || this.state.attempt || this.state.externalActivity)
      return;
    if (chat.draft.trim()) {
      this.#notice('Keep or clear your existing draft before preparing a retry.');
      return;
    }
    chat.retryOf = previous.receipt.attemptId ?? previous.id;
    this.setDraft(chat.messages[index - 1].text);
    this.#notice(
      'Retry prepared, not dispatched. Review context and Send explicitly; an unknown earlier attempt may have incurred additional cost.',
    );
  }
  setStorageScenario(status: WorkspaceState['storage']['status']) {
    this.#clearSaveTimer();
    this.state.storage.status = status;
    if (status !== 'available') {
      this.state.storage.save = 'failed';
      this.state.storage.error =
        'Storage is unavailable for saving. Work is recoverable in memory; retry after recovery.';
    }
    this.#emit();
  }
  retrySave() {
    this.#saveNow();
    this.#notice(
      this.state.storage.save === 'saved'
        ? 'Saved in the simulation journal. Reload still resets the application.'
        : this.state.storage.error,
    );
  }
  setAppInstructions(value: string) {
    this.state.appInstructions = value.slice(0, 8000);
    this.#emit();
  }
  setAppIntent(value: string) {
    this.state.appIntent = value;
    this.#emit();
  }
  setAppearance(kind: 'reducedMotion' | 'highContrast', value: boolean) {
    this.state[kind] = value;
    this.#emit();
  }
  setFixture<
    K extends
      'catalog' | 'setupScenario' | 'sendScenario' | 'hostScenario' | 'telemetry' | 'updateChecks',
  >(key: K, value: WorkspaceState[K]) {
    this.state[key] = value;
    this.#emit();
  }
  seedHistory() {
    const project = activeChat(this.state).projectId;
    if (!project || this.state.chats.length > 430) return;
    for (let i = 1; i <= 60; i++)
      this.state.chats.push({
        ...emptyChat(this.#id('chat'), project),
        title: `Sample research note ${i}`,
        archived: i % 10 === 0,
        pinned: i === 1,
      });
    this.#changed();
  }
  restartMock(discardUnsaved = false, discardTemporary = false) {
    if (this.state.externalActivity) {
      this.#notice('Stop the review before simulating restart.');
      return false;
    }
    if (
      !discardTemporary &&
      this.state.chats.some((c) => c.projectId === null && (c.draft || c.messages.length))
    ) {
      this.#notice(
        'Temporary work needs an explicit Save, Discard or Cancel decision before quitting.',
      );
      return false;
    }
    for (const profile of this.#profiles.values()) {
      if (
        profile.profileId !== this.state.profileId &&
        (profile.storage.save === 'failed' ||
          profile.chats.some((c) => c.projectId === null && (c.draft || c.messages.length)))
      ) {
        this.#notice(
          'Another demo profile has Temporary or unsaved work. Switch back and resolve it before simulating app quit.',
        );
        return false;
      }
    }
    this.stop();
    if (!discardUnsaved && !this.#saveNow()) {
      this.#notice('Recover the failed save or explicitly discard unsaved memory before restart.');
      return false;
    }
    this.#clearSaveTimer();
    for (const handle of this.#timers) this.#timer.clear(handle);
    this.#timers.clear();
    this.#modelJobs.clear();
    this.#connectionGeneration++;
    const saved = this.#journals.get(this.state.profileId)!;
    this.state.projects = structuredClone(saved.projects);
    this.state.chats = structuredClone(saved.chats);
    for (const chat of this.state.chats)
      for (const message of chat.messages)
        if (message.receipt && ['running', 'preparing'].includes(message.receipt.status)) {
          message.receipt.status = 'unknown';
          message.receipt.restored = true;
          const cost = message.receipt.attemptId
            ? this.state.accounting[message.receipt.attemptId]
            : undefined;
          if (cost) {
            message.receipt.held = cost.held;
            if (cost.actual !== null) message.receipt.actual = cost.actual;
          }
        }
    for (const profile of this.#profiles.values())
      if (profile.profileId !== this.state.profileId) {
        profile.chats = profile.chats.filter((c) => c.projectId !== null);
        const namedIds = profile.chats.map((c) => c.id);
        for (const listener of this.#journalListeners)
          listener(profile.profileId, 'restart', namedIds);
        if (!profile.chats.length) profile.chats.push(emptyChat(this.#id('chat'), null));
        profile.activeChatId = profile.chats[0].id;
      }
    this.state.activeChatId = this.state.chats[0]?.id ?? '';
    for (const model of this.state.models) {
      if (['downloading', 'verifying'].includes(model.state)) {
        model.state = 'offered';
        model.progress = 0;
      }
      if (model.state === 'loading') model.state = 'downloaded';
    }
    if (this.state.connection.status === 'testing') this.state.connection.status = 'configured';
    this.state.sendConfirmation = null;
    if (!this.state.chats.length) this.newChat(null);
    for (const listener of this.#journalListeners)
      listener(
        this.state.profileId,
        'restart',
        saved.chats.map((c) => c.id),
      );
    this.state.storage.save = this.state.storage.status === 'available' ? 'saved' : 'failed';
    this.state.storage.error =
      this.state.storage.status === 'available'
        ? ''
        : 'Named work was restored from the mock journal, but saving remains unavailable.';
    this.#notice(
      'Simulated restart restored the named-work journal. Temporary bodies and consent are gone; usage holds remain. No request resumed. Browser reload still resets everything.',
    );
    return true;
  }
  switchProfile(profile: WorkspaceState['profileId']) {
    if (profile === this.state.profileId) return;
    if (
      this.state.attempt ||
      this.state.externalActivity ||
      this.#modelJobs.size ||
      this.state.connection.status === 'testing'
    ) {
      this.#notice('Stop active work and setup before switching profiles.');
      return;
    }
    this.#clearSaveTimer();
    if (this.state.storage.status === 'available') this.#saveNow();
    this.#profiles.set(this.state.profileId, structuredClone(this.state));
    this.#profiles
      .get(this.state.profileId)!
      .models.filter((m) => m.place === 'local' && m.state === 'ready')
      .forEach((m) => (m.state = 'downloaded'));
    const existing = this.#profiles.get(profile);
    if (existing) {
      this.state = structuredClone(existing);
      this.state.models
        .filter((m) => m.place === 'local' && m.state === 'ready')
        .forEach((m) => (m.state = 'downloaded'));
    } else {
      const fresh = initialState();
      fresh.profileId = profile;
      fresh.firstRun = true;
      fresh.projects = [];
      fresh.chats = [emptyChat(this.#id('chat'), null)];
      fresh.activeChatId = fresh.chats[0].id;
      fresh.models.forEach((m) => {
        if (m.place === 'local') {
          m.state = 'offered';
          m.progress = 0;
        }
      });
      this.state = fresh;
      this.#commitJournal();
    }
    this.#notice(
      'Demo profile changed. Projects, search, connection bindings and usage are isolated in memory.',
    );
  }
  dismissOnboarding() {
    this.state.firstRun = false;
    this.#emit();
  }
  connectionDetails(label: string, provider: 'demo' | 'claude' | 'gpt') {
    if (
      this.state.models.find(
        (m) => m.id === (this.state.attempt?.modelId ?? this.state.externalActivity?.modelId),
      )?.place === 'cloud'
    ) {
      this.#notice('Stop cloud work before editing its binding.');
      return;
    }
    this.#connectionGeneration++;
    this.state.connection.label = label.trim().slice(0, 60) || 'Personal cloud connection';
    this.state.connection.provider = provider;
    this.state.connection.status = 'configured';
    this.#notice('Binding edited. Test again; project consent and existing pins are unchanged.');
  }
  setTheme(value: 'dark' | 'light') {
    this.state.theme = value;
    this.#emit();
  }
  setOpaque(value: boolean) {
    this.state.opaque = value;
    this.#emit();
  }
  setAppObjective(value: Objective) {
    this.state.appObjective = value;
    this.#emit();
  }
  configureConnection() {
    this.state.connection.status = 'configured';
    this.#notice('Demo connection added. Cloud permission is still a separate choice.');
  }
  setConnectionScenario(value: ConnectionScenario) {
    if (
      this.state.models.find(
        (m) => m.id === (this.state.attempt?.modelId ?? this.state.externalActivity?.modelId),
      )?.place === 'cloud'
    ) {
      this.#notice('Stop the cloud response before changing its connection test.');
      return;
    }
    this.state.connection.scenario = value;
    if (this.state.connection.status === 'testing') {
      this.#connectionGeneration++;
      this.state.connection.status = 'configured';
    }
    this.#emit();
  }
  testConnection() {
    if (this.state.connection.status === 'absent') {
      this.#notice('Add a demo binding before testing it. No request was made.');
      return;
    }
    if (
      this.state.models.find(
        (m) => m.id === (this.state.attempt?.modelId ?? this.state.externalActivity?.modelId),
      )?.place === 'cloud'
    ) {
      this.#notice('Stop the cloud response before testing its connection again.');
      return;
    }
    if (this.state.connection.status === 'testing') return;
    const generation = ++this.#connectionGeneration;
    const scenario = this.state.connection.scenario;
    this.state.connection.status = 'testing';
    this.#emit();
    this.#later(() => {
      if (generation !== this.#connectionGeneration) return;
      this.state.connection.status = scenario === 'success' ? 'verified' : scenario;
      this.#notice(
        scenario === 'success'
          ? 'Demo connection verified. No provider request was made.'
          : `Simulated ${scenario} connection. Change the test result and retry explicitly.`,
      );
    }, 900);
  }
  removeConnection() {
    const running = this.state.models.find(
      (m) => m.id === (this.state.attempt?.modelId ?? this.state.externalActivity?.modelId),
    );
    if (running?.place === 'cloud') {
      this.#notice('Stop the cloud response before removing its connection.');
      return;
    }
    this.#connectionGeneration++;
    this.state.connection.status = 'absent';
    this.#notice('Demo connection removed. Cloud pins remain selected and blocked.');
  }
  downloadModel(id: string) {
    const model = this.state.models.find((m) => m.id === id);
    if (
      !model ||
      model.place !== 'local' ||
      !model.fit ||
      !['offered', 'failed'].includes(model.state)
    )
      return;
    if (this.state.catalog === 'unavailable') {
      this.#notice(
        'Catalog unavailable. Already installed models still work; retry catalog lookup explicitly.',
      );
      return;
    }
    if (this.state.setupScenario === 'offline' || this.state.setupScenario === 'full') {
      model.state = 'failed';
      model.problem =
        this.state.setupScenario === 'full'
          ? 'Model store is full. Free space before an explicit retry.'
          : 'Download interrupted offline. Retry explicitly; the package is unverified.';
      this.#notice(model.problem);
      return;
    }
    const scenario = this.state.setupScenario;
    model.problem = '';
    const token = ++this.#sequence;
    this.#modelJobs.set(id, token);
    model.state = 'downloading';
    model.progress = 0;
    this.#emit();
    const step = (n: number) => {
      if (this.#modelJobs.get(id) !== token) return;
      model.progress = n * 25;
      if (n < 4) {
        this.#emit();
        this.#later(() => step(n + 1), 350);
      } else {
        model.state = 'verifying';
        this.#emit();
        this.#later(() => {
          if (this.#modelJobs.get(id) !== token) return;
          model.state = scenario === 'verify-failed' ? 'failed' : 'downloaded';
          model.problem =
            scenario === 'verify-failed'
              ? 'Verification failed. This package cannot be loaded. Remove or explicitly retry.'
              : '';
          this.#modelJobs.delete(id);
          this.#notice(
            model.problem || `${model.name} is downloaded in the simulation. It is not loaded yet.`,
          );
        }, 450);
      }
    };
    this.#later(() => step(1), 350);
  }
  cancelSetup(id: string) {
    const model = this.state.models.find((m) => m.id === id);
    if (!model || !this.#modelJobs.has(id)) return;
    this.#modelJobs.delete(id);
    model.state = model.state === 'loading' ? 'downloaded' : 'offered';
    model.progress = model.state === 'offered' ? 0 : 100;
    this.#notice('Model setup canceled. No weights or process were created.');
  }
  loadModel(id: string) {
    const model = this.state.models.find((m) => m.id === id);
    if (!model || model.state !== 'downloaded' || !model.fit) return;
    if (
      this.state.attempt ||
      this.state.externalActivity ||
      this.state.models.some((m) => m.state === 'loading')
    ) {
      this.#notice('One model operation at a time. Finish or stop current work.');
      return;
    }
    const token = ++this.#sequence;
    const scenario = this.state.setupScenario;
    this.#modelJobs.set(id, token);
    model.state = 'loading';
    this.#emit();
    this.#later(() => {
      if (this.#modelJobs.get(id) !== token) return;
      if (scenario === 'load-failed') {
        model.state = 'downloaded';
        model.problem =
          'Load failed. The downloaded copy is retained; choose an explicit recovery.';
        this.#modelJobs.delete(id);
        this.#notice(model.problem);
        return;
      }
      this.state.models
        .filter((m) => m.place === 'local' && m.state === 'ready')
        .forEach((m) => {
          m.state = 'downloaded';
        });
      model.state = 'ready';
      model.problem = '';
      this.#modelJobs.delete(id);
      this.#notice(`${model.name} is ready in the simulation.`);
    }, 900);
  }
  unloadModel(id: string, remove = false) {
    const model = this.state.models.find((m) => m.id === id);
    if (!model || model.place !== 'local') return;
    if (!remove && model.state !== 'ready') return;
    if (
      this.state.attempt?.modelId === id ||
      this.state.externalActivity?.modelId === id ||
      model.state === 'loading'
    ) {
      this.#notice('Stop current work before unloading or removing its model.');
      return;
    }
    this.#modelJobs.delete(id);
    model.state = remove ? 'offered' : 'downloaded';
    model.progress = remove ? 0 : 100;
    this.#notice(
      remove
        ? 'Sample model removed from inventory. Existing pins stay selected.'
        : 'Sample model unloaded. It can load again on Send.',
    );
  }
  cancelSendConfirmation() {
    this.state.sendConfirmation = null;
    this.#emit();
  }
  send(expectedFingerprint?: string, confirmed = false) {
    const chat = activeChat(this.state);
    if (!chat.draft.trim()) return;
    if (
      this.state.attempt ||
      this.state.externalActivity ||
      this.state.models.some((m) => m.state === 'loading')
    ) {
      this.#notice('Another operation is active. Return to it or stop it before Send.');
      return;
    }
    const seen = preview(this.state, chat);
    if (this.state.sendScenario === 'route-change' || this.state.sendScenario === 'price-change') {
      const change = this.state.sendScenario;
      this.state.sendScenario = 'normal';
      if (change === 'route-change' && seen.model?.place === 'local') {
        const model = this.state.models.find((m) => m.id === seen.model!.id)!;
        model.state = 'failed';
        model.problem = 'Became unavailable at the simulated Send boundary.';
      } else this.state.priceUpper = Math.min(this.state.priceUpper * 2, 1);
    }
    const plan = preview(this.state, chat);
    const description = (p: ReturnType<typeof preview>) =>
      `${p.model?.name ?? 'No eligible model'} · ${p.model?.place ?? 'blocked'} · ${p.context.length} selected copies · upper $${p.upper.toFixed(2)}${p.blocked ? ' · blocked' : ''}`;
    if (
      seen.fingerprint !== plan.fingerprint ||
      (expectedFingerprint && expectedFingerprint !== plan.fingerprint) ||
      (this.state.sendConfirmation &&
        (!confirmed ||
          this.state.sendConfirmation.chatId !== chat.id ||
          this.state.sendConfirmation.fingerprint !== plan.fingerprint))
    ) {
      this.state.sendConfirmation = {
        chatId: chat.id,
        fingerprint: plan.fingerprint,
        before: this.state.sendConfirmation?.before ?? description(seen),
        after: description(plan),
      };
      this.#notice(
        'The Send proposal changed. Review the new model, context, privacy and allowance before confirming. Nothing dispatched.',
      );
      return;
    }
    if (plan.blocked || !plan.model) {
      this.#notice(plan.reason);
      return;
    }
    this.state.sendConfirmation = null;
    this.#clearSaveTimer();
    const model = this.state.models.find((m) => m.id === plan.model!.id)!;
    const project = this.state.projects.find((p) => p.id === chat.projectId);
    const prompt = chat.draft;
    const attemptId = this.#id('attempt');
    const receipt: Receipt = {
      model: model.name,
      place: model.place,
      status: plan.preparation ? 'preparing' : 'running',
      context: plan.context.map((f) => `${f.name} · v${f.revision}`),
      instructions: plan.instructions,
      origin: project?.name ?? 'Temporary chat',
      estimate: plan.estimate,
      held: 0,
      attemptId,
      retryOf: chat.retryOf,
    };
    const responseId = this.#id('response');
    const token = ++this.#sequence;
    if (chat.title === 'New chat' || chat.title === 'Temporary chat')
      chat.title = prompt.trim().slice(0, 48);
    chat.messages.push(
      { id: this.#id('prompt'), role: 'user', text: prompt },
      { id: responseId, role: 'assistant', text: '', receipt },
    );
    chat.draft = '';
    this.state.attempt = {
      id: attemptId,
      chatId: chat.id,
      projectId: chat.projectId,
      modelId: model.id,
      messageId: responseId,
      phase: plan.preparation ? 'preparing' : 'running',
      origin: receipt.origin,
      token,
    };
    if (chat.projectId) this.state.storage.save = 'saving';
    this.#emit();
    const dispatch = () => {
      if (this.state.attempt?.token !== token) return;
      // Revalidate the frozen request under current restrictions after preparation.
      const frozen = {
        ...chat,
        draft: prompt,
        intent: model.id,
        inheritIntent: false,
        requestIntent: undefined,
        selectedFiles: plan.context.map((f) => f.id),
        instructions: chat.instructions,
        messages: chat.messages.slice(0, -2),
      };
      const current = preview(this.state, frozen);
      if (
        current.blocked ||
        model.state !== 'ready' ||
        current.instructions !== plan.instructions ||
        JSON.stringify(current.context) !== JSON.stringify(plan.context) ||
        current.upper !== plan.upper ||
        current.cloudAllowed !== plan.cloudAllowed ||
        current.objective !== plan.objective
      ) {
        receipt.status = 'blocked';
        this.state.attempt = null;
        if (chat.projectId) this.#changed();
        this.#notice(
          'Preparation changed eligibility or context. Nothing was dispatched; review and send again.',
        );
        return;
      }
      delete chat.requestIntent;
      delete chat.retryOf;
      this.state.accounting[attemptId] = {
        status: 'running',
        held: model.place === 'cloud' ? plan.upper : 0,
        actual: model.place === 'local' ? 0 : null,
        key: chat.projectId ?? chat.id,
        model: model.name,
        place: model.place,
      };
      if (model.place === 'cloud') {
        const key = chat.projectId ?? chat.id;
        this.state.usage[key] = (this.state.usage[key] ?? 0) + plan.upper;
        this.state.profileUsed += plan.upper;
        receipt.held = plan.upper;
      }
      this.state.attempt.phase = 'running';
      receipt.status = 'running';
      this.#emit();
      const words = scriptedReply(
        prompt,
        model.name,
        plan.context.map((f) => f.name),
      ).split(' ');
      let index = 0;
      const outcome = this.state.sendScenario;
      const stream = () => {
        if (this.state.attempt?.token !== token) return;
        const message = chat.messages.find((m) => m.id === responseId)!;
        index = Math.min(index + 4, words.length);
        message.text = words.slice(0, index).join(' ');
        if ((outcome === 'failed' || outcome === 'unknown') && index >= 12) {
          receipt.status = outcome;
          this.state.accounting[attemptId].status = outcome;
          this.state.attempt = null;
          if (chat.projectId) this.#changed();
          this.#notice(
            'Simulated response interrupted. Partial output and any unknown hold remain. Prepare a retry explicitly; no automatic replay.',
          );
          return;
        }
        if (index >= words.length) {
          receipt.status = 'completed';
          receipt.actual = plan.estimate;
          this.state.accounting[attemptId].status = 'completed';
          this.state.accounting[attemptId].actual = plan.estimate;
          this.state.accounting[attemptId].held = 0;
          if (model.place === 'cloud') {
            const key = chat.projectId ?? chat.id;
            const refund = plan.upper - plan.estimate;
            this.state.usage[key] -= refund;
            this.state.profileUsed -= refund;
            receipt.held = 0;
          }
          this.state.attempt = null;
          if (chat.projectId) this.#changed();
          else this.#emit();
        } else {
          this.#emit();
          this.#later(stream, 100);
        }
      };
      this.#later(stream, 120);
    };
    if (plan.preparation) {
      model.state = 'loading';
      this.#emit();
      this.#later(() => {
        if (this.state.attempt?.token !== token) return;
        if (this.state.setupScenario === 'load-failed') {
          model.state = 'downloaded';
          model.problem = 'Simulated load failed; no inference dispatched.';
          receipt.status = 'failed';
          this.state.attempt = null;
          if (chat.projectId) this.#changed();
          this.#notice(model.problem);
          return;
        }
        this.state.models
          .filter((m) => m.place === 'local' && m.state === 'ready')
          .forEach((m) => {
            m.state = 'downloaded';
          });
        model.state = 'ready';
        dispatch();
      }, 900);
    } else dispatch();
  }
  stop() {
    const attempt = this.state.attempt;
    if (!attempt) return;
    const chat = this.state.chats.find((c) => c.id === attempt.chatId)!;
    const message = chat.messages.find((m) => m.id === attempt.messageId)!;
    if (message.receipt) message.receipt.status = 'canceled';
    const cost = this.state.accounting[attempt.id];
    if (cost) cost.status = cost.held ? 'unknown' : 'failed';
    const model = this.state.models.find((m) => m.id === attempt.modelId);
    if (model?.state === 'loading') model.state = 'downloaded';
    this.state.attempt = null;
    if (chat.projectId) this.#changed();
    this.#notice(
      message.receipt?.held
        ? 'Stopped. Partial output retained; fictional cloud usage is unknown and its hold remains.'
        : 'Stopped. Partial output retained in its original chat.',
    );
  }
  beginReview(chatId: string): boolean {
    if (
      this.state.attempt ||
      this.state.externalActivity ||
      this.state.models.some((m) => m.state === 'loading')
    ) {
      this.#notice('One response or review at a time. Return to active work or stop it.');
      return false;
    }
    const chat = this.state.chats.find((c) => c.id === chatId);
    if (!chat) return false;
    this.#clearSaveTimer();
    this.state.externalActivity = {
      chatId,
      projectId: chat.projectId,
      origin: this.state.projects.find((p) => p.id === chat.projectId)?.name ?? 'Temporary chat',
      modelId: null,
      phase: 'Starting review',
    };
    this.#emit();
    return true;
  }
  reviewRole(modelId: string, phase: string, preparing = false) {
    if (!this.state.externalActivity) return;
    this.state.externalActivity.modelId = modelId;
    this.state.externalActivity.phase = phase;
    const model = this.state.models.find((m) => m.id === modelId);
    if (preparing && model?.place === 'local') model.state = 'loading';
    this.#emit();
  }
  readyReviewModel(modelId: string) {
    const model = this.state.models.find((m) => m.id === modelId);
    if (model?.place === 'local') {
      this.state.models
        .filter((m) => m.place === 'local' && m.state === 'ready')
        .forEach((m) => (m.state = 'downloaded'));
      model.state = 'ready';
    }
    this.#emit();
  }
  finishReview() {
    const model = this.state.models.find((m) => m.id === this.state.externalActivity?.modelId);
    if (model?.state === 'loading') model.state = 'downloaded';
    this.state.externalActivity = null;
    this.#emit();
  }
  adjustReviewAllowance(key: string, amount: number, profile = this.state.profileId) {
    // Same content-free fixture accounting owner used by ordinary chat.
    if (profile !== this.state.profileId) {
      const saved = this.#profiles.get(profile);
      if (saved) {
        saved.profileUsed = Math.max(0, saved.profileUsed + amount);
        saved.usage[key] = Math.max(0, (saved.usage[key] ?? 0) + amount);
      }
      return;
    }
    this.state.profileUsed = Math.max(0, this.state.profileUsed + amount);
    this.state.usage[key] = Math.max(0, (this.state.usage[key] ?? 0) + amount);
    this.#emit();
  }
  dispose() {
    this.stop();
    this.#disposed = true;
    for (const id of this.#timers) this.#timer.clear(id);
    this.#timers.clear();
    this.#saveTimer = null;
    this.#modelJobs.clear();
    this.#listeners.clear();
    this.#temporaryCopies.clear();
    this.#journalListeners.clear();
  }
}

function scriptedReply(prompt: string, model: string, files: string[]): string {
  const context = files.length
    ? `Your selected packet includes ${files.join(', ')}. `
    : 'No project files are included in this request. ';
  if (/\b(code|function|typescript|javascript)\b/i.test(prompt))
    return `### A small starting point\n\nKeep the logic explicit and test its boundaries.\n\n\`\`\`typescript\nfunction sum(values: number[]): number {\n  return values.reduce((total, value) => total + value, 0);\n}\n\`\`\`\n\n- Empty input returns zero.\n- Check the numeric inputs your real application permits.\n\n${context}This is a scripted code fixture using ${model}. No code was executed or model called.`;
  if (/\b(research|compare|evidence|sources|literature)\b/i.test(prompt))
    return `Here’s a starting point for the comparison.\n\nLocal models keep this task on your machine and offer direct control over setup. Cloud models add another option when you explicitly allow them. The right choice depends on the task, available hardware and evidence about output quality.\n\n${context}Before drawing a conclusion, define the question, compare the assumptions and check the original sources.\n\nThis is a scripted simulation using ${model}; no research or model inference was performed.`;
  if (/\b(write|draft|edit|rewrite|polish)\b/i.test(prompt))
    return `A clear first draft starts with the point you want the reader to remember. Then give it just enough detail to make it useful.\n\n${context}Try a short opening, one supporting example and a specific next step. Keep your language direct and leave space to refine the details.\n\nThis is a scripted writing sample using ${model}; it is not a model-generated answer.`;
  return `Let’s work through it one step at a time.\n\nStart by making the goal concrete, then choose the smallest useful next step. Keep relevant notes in this project and select only the context this question needs.\n\n${context}You can continue here, switch projects, or open the quick bar without moving this conversation.\n\nThis is a scripted simulation using ${model}. No backend or model was called.`;
}
