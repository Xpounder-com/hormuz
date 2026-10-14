export type Place = 'local' | 'cloud';
export type Objective = 'balanced' | 'quality' | 'speed';
export type ModelState =
  'offered' | 'downloading' | 'verifying' | 'downloaded' | 'loading' | 'ready' | 'failed';
export type Model = {
  id: string;
  name: string;
  detail: string;
  place: Place;
  state: ModelState;
  progress: number;
  fit: boolean;
  inputLimit: number;
  size: string;
  specialty: 'general' | 'writing' | 'research';
  identity?: string;
  problem?: string;
};
export type ProjectFile = {
  id: string;
  name: string;
  text: string;
  revision: number;
  localOnly: boolean;
  status:
    | 'available'
    | 'unsupported'
    | 'missing'
    | 'oversized'
    | 'denied'
    | 'read-failed'
    | 'changed'
    | 'unknown';
  ownership?: 'copy' | 'unbound-link';
};
export type Project = {
  id: string;
  name: string;
  color: string;
  instructions: string;
  cloudAllowed: boolean;
  cap: number;
  objective: Objective;
  files: ProjectFile[];
  archived: boolean;
  pinned?: boolean;
  modelIntent?: string;
  localOnly?: boolean;
};
export type Receipt = {
  model: string;
  place: Place;
  status: 'preparing' | 'running' | 'completed' | 'canceled' | 'blocked' | 'failed' | 'unknown';
  context: string[];
  instructions: string;
  origin: string;
  estimate: number;
  held: number;
  attemptId?: string;
  retryOf?: string;
  actual?: number;
  restored?: boolean;
  imported?: boolean;
};
export type Message = { id: string; role: 'user' | 'assistant'; text: string; receipt?: Receipt };
export type Chat = {
  id: string;
  projectId: string | null;
  title: string;
  draft: string;
  intent: string;
  selectedFiles: string[];
  instructions: string;
  objective: Objective | 'inherit';
  localOnly: boolean;
  cloudAllowed: boolean;
  cap: number;
  messages: Message[];
  pinned?: boolean;
  archived?: boolean;
  requestIntent?: string;
  inheritIntent?: boolean;
  continuationModel?: string;
  retryOf?: string;
};
export type ConnectionScenario =
  'success' | 'invalid' | 'offline' | 'expired' | 'locked' | 'rate-limited' | 'provider-error';
export type Connection = {
  status: 'absent' | 'configured' | 'testing' | 'verified' | Exclude<ConnectionScenario, 'success'>;
  scenario: ConnectionScenario;
  label?: string;
  provider?: 'demo' | 'claude' | 'gpt';
};
export type Attempt = {
  id: string;
  chatId: string;
  projectId: string | null;
  modelId: string;
  messageId: string;
  phase: 'preparing' | 'running';
  origin: string;
  token: number;
};
export type WorkspaceState = {
  projects: Project[];
  chats: Chat[];
  models: Model[];
  activeChatId: string;
  connection: Connection;
  attempt: Attempt | null;
  reviewActivity: {
    chatId: string;
    projectId: string | null;
    origin: string;
    modelId: string | null;
    phase: string;
  } | null;
  revision: number;
  notice: string;
  theme: 'dark' | 'light';
  opaque: boolean;
  profileUsed: number;
  usage: Record<string, number>;
  accounting: Record<
    string,
    {
      status: 'running' | 'completed' | 'unknown' | 'failed';
      held: number;
      actual: number | null;
      key: string;
      model: string;
      place: Place;
    }
  >;
  appObjective: Objective;
  appInstructions: string;
  appIntent: string;
  profileId: 'personal' | 'sandbox';
  profileLocalOnly: boolean;
  storage: {
    status: 'available' | 'unavailable' | 'corrupt' | 'full' | 'write-failed';
    save: 'saved' | 'saving' | 'failed';
    error: string;
  };
  reducedMotion: boolean;
  highContrast: boolean;
  catalog: 'available' | 'unavailable';
  setupScenario: 'normal' | 'offline' | 'full' | 'verify-failed' | 'load-failed';
  sendScenario: 'normal' | 'route-change' | 'price-change' | 'failed' | 'unknown';
  priceUpper: number;
  hostScenario: 'normal' | 'shortcut-collision' | 'focus-unavailable' | 'host-error';
  firstRun: boolean;
  telemetry: boolean;
  updateChecks: boolean;
  sendConfirmation: null | {
    chatId: string;
    fingerprint: string;
    before: string;
    after: string;
  };
};
export type RoutePlan = {
  model: Model | null;
  blocked: boolean;
  reason: string;
  preparation: boolean;
  tokens: number;
  estimate: number;
  upper: number;
  fingerprint: string;
  context: ProjectFile[];
  instructions: string;
  objective: Objective;
  cloudAllowed: boolean;
  historyCount: number;
  intent: string;
};
export type Timer = {
  after: (fn: () => void, ms: number) => unknown;
  clear: (id: unknown) => void;
};
