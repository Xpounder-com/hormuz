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
};
export type ProjectFile = {
  id: string;
  name: string;
  text: string;
  revision: number;
  localOnly: boolean;
  status: 'available' | 'unsupported' | 'missing' | 'oversized';
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
};
export type Receipt = {
  model: string;
  place: Place;
  status: 'preparing' | 'running' | 'completed' | 'canceled' | 'blocked';
  context: string[];
  instructions: string;
  origin: string;
  estimate: number;
  held: number;
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
};
export type Connection = {
  status: 'absent' | 'configured' | 'testing' | 'verified' | 'invalid' | 'offline';
  scenario: 'success' | 'invalid' | 'offline';
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
  appObjective: Objective;
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
};
export type Timer = {
  after: (fn: () => void, ms: number) => unknown;
  clear: (id: unknown) => void;
};
