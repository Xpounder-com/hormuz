import { emptyChat, initialState, sampleFiles } from './fixtures.ts';
import { activeChat, preview } from './planner.ts';
import type { Chat, Objective, Receipt, Timer, WorkspaceState } from './types.ts';

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

  constructor(state = initialState(), timer = defaultTimer) {
    this.state = state;
    this.#timer = timer;
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
  }
  #notice(message: string) {
    this.state.notice = message;
    this.#emit();
  }
  clearNotice() {
    this.state.notice = '';
    this.#emit();
  }
  navigate(chatId: string) {
    if (!this.state.chats.some((c) => c.id === chatId)) return;
    this.state.activeChatId = chatId;
    this.#emit();
  }
  selectProject(projectId: string) {
    const existing = this.state.chats.find((c) => c.projectId === projectId);
    if (existing) this.navigate(existing.id);
    else this.newChat(projectId);
  }
  newChat(projectId: string | null = activeChat(this.state).projectId) {
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
    this.#emit();
  }
  createProject(name: string) {
    if (!name.trim()) return;
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
    });
    this.newChat(id);
  }
  renameProject(id: string, name: string) {
    const project = this.state.projects.find((p) => p.id === id);
    if (project && name.trim()) {
      project.name = name.trim().slice(0, 100);
      this.#emit();
    }
  }
  archiveProject(id: string) {
    const project = this.state.projects.find((p) => p.id === id);
    if (project) {
      project.archived = !project.archived;
      this.#emit();
    }
  }
  deleteProject(id: string) {
    if (this.state.attempt?.projectId === id || this.state.reviewActivity?.projectId === id) {
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
    this.#notice(
      'Project content removed from this simulation. Models, connections and usage are retained.',
    );
  }
  renameChat(id: string, name: string) {
    const chat = this.state.chats.find((c) => c.id === id);
    if (chat && name.trim()) {
      chat.title = name.trim().slice(0, 100);
      this.#emit();
    }
  }
  deleteChat(id: string) {
    if (this.state.attempt?.chatId === id || this.state.reviewActivity?.chatId === id) {
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
      this.state.reviewActivity?.chatId === source.id
    ) {
      this.#notice('Stop the Temporary response before saving a copy.');
      return;
    }
    const project = this.state.projects.find((p) => p.id === projectId);
    if (!project) return;
    const copy = structuredClone(source);
    copy.id = this.#id('chat');
    copy.projectId = projectId;
    copy.cloudAllowed = false;
    copy.intent = 'auto';
    copy.selectedFiles = [];
    copy.instructions = '';
    copy.objective = 'inherit';
    copy.title = source.title === 'Temporary chat' ? 'Saved from Temporary' : source.title;
    this.state.chats = this.state.chats.filter((c) => c.id !== source.id);
    this.state.chats.push(copy);
    this.state.activeChatId = copy.id;
    for (const listener of this.#temporaryCopies) listener(source.id, copy.id);
    this.#notice(
      `Saved a simulated copy to ${project.name}. Future requests use its context and permission; earlier receipts keep their origin.`,
    );
  }
  setDraft(text: string, chatId = this.state.activeChatId) {
    const chat = this.state.chats.find((c) => c.id === chatId);
    if (!chat) return;
    chat.draft = text.slice(0, 30000);
    this.#emit();
  }
  pin(intent: string) {
    activeChat(this.state).intent = intent;
    this.#emit();
  }
  setChatInstructions(value: string) {
    activeChat(this.state).instructions = value.slice(0, 8000);
    this.#emit();
  }
  setChatObjective(value: Objective | 'inherit') {
    activeChat(this.state).objective = value;
    this.#emit();
  }
  setLocalRestriction(value: boolean) {
    activeChat(this.state).localOnly = value;
    this.#emit();
  }
  setCloudAllowed(value: boolean) {
    const chat = activeChat(this.state);
    const project = this.state.projects.find((p) => p.id === chat.projectId);
    if (project) project.cloudAllowed = value;
    else chat.cloudAllowed = value;
    this.#emit();
  }
  setCap(value: number) {
    if (!Number.isFinite(value) || value < 0 || value > 10) return;
    const chat = activeChat(this.state);
    const project = this.state.projects.find((p) => p.id === chat.projectId);
    if (project) project.cap = value;
    else chat.cap = value;
    this.#emit();
  }
  setProjectInstructions(value: string) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    if (project) {
      project.instructions = value.slice(0, 8000);
      this.#emit();
    }
  }
  setProjectObjective(value: Objective) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    if (project) {
      project.objective = value;
      this.#emit();
    }
  }
  toggleFile(id: string) {
    const chat = activeChat(this.state);
    const project = this.state.projects.find((p) => p.id === chat.projectId);
    const file = project?.files.find((f) => f.id === id);
    if (!file || file.status !== 'available') return;
    chat.selectedFiles = chat.selectedFiles.includes(id)
      ? chat.selectedFiles.filter((f) => f !== id)
      : [...chat.selectedFiles, id];
    this.#emit();
  }
  addSampleFile(id: string) {
    const project = this.state.projects.find((p) => p.id === activeChat(this.state).projectId);
    const file = sampleFiles.find((f) => f.id === id);
    if (!project || !file) return;
    if (!project.files.some((f) => f.id === id)) {
      project.files.push(structuredClone(file));
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
    this.#notice('Sample project copy removed. Submitted response snapshots are unchanged.');
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
  setConnectionScenario(value: 'success' | 'invalid' | 'offline') {
    if (
      this.state.models.find(
        (m) => m.id === (this.state.attempt?.modelId ?? this.state.reviewActivity?.modelId),
      )?.place === 'cloud'
    ) {
      this.#notice('Stop the cloud response before changing its connection test.');
      return;
    }
    this.state.connection.scenario = value;
    this.#emit();
  }
  testConnection() {
    if (
      this.state.models.find(
        (m) => m.id === (this.state.attempt?.modelId ?? this.state.reviewActivity?.modelId),
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
      (m) => m.id === (this.state.attempt?.modelId ?? this.state.reviewActivity?.modelId),
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
          model.state = 'downloaded';
          this.#modelJobs.delete(id);
          this.#notice(`${model.name} is downloaded in the simulation. It is not loaded yet.`);
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
    if (!model || model.state !== 'downloaded') return;
    if (
      this.state.attempt ||
      this.state.reviewActivity ||
      this.state.models.some((m) => m.state === 'loading')
    ) {
      this.#notice('One model operation at a time. Finish or stop current work.');
      return;
    }
    const token = ++this.#sequence;
    this.#modelJobs.set(id, token);
    model.state = 'loading';
    this.#emit();
    this.#later(() => {
      if (this.#modelJobs.get(id) !== token) return;
      this.state.models
        .filter((m) => m.place === 'local' && m.state === 'ready')
        .forEach((m) => {
          m.state = 'downloaded';
        });
      model.state = 'ready';
      this.#modelJobs.delete(id);
      this.#notice(`${model.name} is ready in the simulation.`);
    }, 900);
  }
  unloadModel(id: string, remove = false) {
    const model = this.state.models.find((m) => m.id === id);
    if (!model || model.place !== 'local') return;
    if (
      this.state.attempt?.modelId === id ||
      this.state.reviewActivity?.modelId === id ||
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
  send() {
    const chat = activeChat(this.state);
    if (!chat.draft.trim()) return;
    if (
      this.state.attempt ||
      this.state.reviewActivity ||
      this.state.models.some((m) => m.state === 'loading')
    ) {
      this.#notice('Another operation is active. Return to it or stop it before Send.');
      return;
    }
    const plan = preview(this.state, chat);
    if (plan.blocked || !plan.model) {
      this.#notice(plan.reason);
      return;
    }
    const model = this.state.models.find((m) => m.id === plan.model!.id)!;
    const project = this.state.projects.find((p) => p.id === chat.projectId);
    const prompt = chat.draft;
    const receipt: Receipt = {
      model: model.name,
      place: model.place,
      status: plan.preparation ? 'preparing' : 'running',
      context: plan.context.map((f) => `${f.name} · v${f.revision}`),
      instructions: plan.instructions,
      origin: project?.name ?? 'Temporary chat',
      estimate: plan.estimate,
      held: 0,
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
      id: this.#id('attempt'),
      chatId: chat.id,
      projectId: chat.projectId,
      modelId: model.id,
      messageId: responseId,
      phase: plan.preparation ? 'preparing' : 'running',
      origin: receipt.origin,
      token,
    };
    this.#emit();
    const dispatch = () => {
      if (this.state.attempt?.token !== token) return;
      // Revalidate the frozen request under current restrictions after preparation.
      const frozen = {
        ...chat,
        draft: prompt,
        intent: model.id,
        selectedFiles: plan.context.map((f) => f.id),
        instructions: chat.instructions,
        messages: chat.messages.slice(0, -2),
      };
      const current = preview(this.state, frozen);
      if (
        current.blocked ||
        model.state !== 'ready' ||
        current.instructions !== plan.instructions ||
        JSON.stringify(current.context) !== JSON.stringify(plan.context)
      ) {
        receipt.status = 'blocked';
        this.state.attempt = null;
        this.#notice(
          'Preparation changed eligibility or context. Nothing was dispatched; review and send again.',
        );
        return;
      }
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
      const stream = () => {
        if (this.state.attempt?.token !== token) return;
        const message = chat.messages.find((m) => m.id === responseId)!;
        index = Math.min(index + 4, words.length);
        message.text = words.slice(0, index).join(' ');
        if (index >= words.length) {
          receipt.status = 'completed';
          if (model.place === 'cloud') {
            const key = chat.projectId ?? chat.id;
            const refund = plan.upper - plan.estimate;
            this.state.usage[key] -= refund;
            this.state.profileUsed -= refund;
            receipt.held = 0;
          }
          this.state.attempt = null;
          this.#emit();
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
    const model = this.state.models.find((m) => m.id === attempt.modelId);
    if (model?.state === 'loading') model.state = 'downloaded';
    this.state.attempt = null;
    this.#notice(
      message.receipt?.held
        ? 'Stopped. Partial output retained; fictional cloud usage is unknown and its hold remains.'
        : 'Stopped. Partial output retained in its original chat.',
    );
  }
  beginReview(chatId: string): boolean {
    if (
      this.state.attempt ||
      this.state.reviewActivity ||
      this.state.models.some((m) => m.state === 'loading')
    ) {
      this.#notice('One response or review at a time. Return to active work or stop it.');
      return false;
    }
    const chat = this.state.chats.find((c) => c.id === chatId);
    if (!chat) return false;
    this.state.reviewActivity = {
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
    if (!this.state.reviewActivity) return;
    this.state.reviewActivity.modelId = modelId;
    this.state.reviewActivity.phase = phase;
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
    const model = this.state.models.find((m) => m.id === this.state.reviewActivity?.modelId);
    if (model?.state === 'loading') model.state = 'downloaded';
    this.state.reviewActivity = null;
    this.#emit();
  }
  adjustReviewAllowance(key: string, amount: number) {
    // Same content-free fixture accounting owner used by ordinary chat.
    this.state.profileUsed = Math.max(0, this.state.profileUsed + amount);
    this.state.usage[key] = Math.max(0, (this.state.usage[key] ?? 0) + amount);
    this.#emit();
  }
  dispose() {
    this.stop();
    this.#disposed = true;
    for (const id of this.#timers) this.#timer.clear(id);
    this.#timers.clear();
    this.#modelJobs.clear();
    this.#listeners.clear();
    this.#temporaryCopies.clear();
  }
}

function scriptedReply(prompt: string, model: string, files: string[]): string {
  const context = files.length
    ? `Your selected packet includes ${files.join(', ')}. `
    : 'No project files are included in this request. ';
  if (/\b(research|compare|evidence|sources|literature)\b/i.test(prompt))
    return `Here’s a starting point for the comparison.\n\nLocal models keep this task on your machine and offer direct control over setup. Cloud models add another option when you explicitly allow them. The right choice depends on the task, available hardware and evidence about output quality.\n\n${context}Before drawing a conclusion, define the question, compare the assumptions and check the original sources.\n\nThis is a scripted simulation using ${model}; no research or model inference was performed.`;
  if (/\b(write|draft|edit|rewrite|polish)\b/i.test(prompt))
    return `A clear first draft starts with the point you want the reader to remember. Then give it just enough detail to make it useful.\n\n${context}Try a short opening, one supporting example and a specific next step. Keep your language direct and leave space to refine the details.\n\nThis is a scripted writing sample using ${model}; it is not a model-generated answer.`;
  return `Let’s work through it one step at a time.\n\nStart by making the goal concrete, then choose the smallest useful next step. Keep relevant notes in this project and select only the context this question needs.\n\n${context}You can continue here, switch projects, or open the quick bar without moving this conversation.\n\nThis is a scripted simulation using ${model}. No backend or model was called.`;
}
