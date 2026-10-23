export type AgentKind = "ORCHESTRATOR" | "WORKER" | "";

export type StepState = "pending" | "active" | "done" | "failed";

export type ProjectStep = {
  id: string;
  label: string;
  state: StepState;
};

export type ProjectArtifact = {
  name: string;
  kind: string;
  bytes: number;
};

export type Project = {
  id: string;
  name: string;
  goal: string;
  agents: { id: string; display_name: string; kind: string; skills?: string[] }[];
  steps?: ProjectStep[];
  seeds?: { id: string; kind: "text" | "link" | "file" | "image"; title: string; body: string }[];
  artifacts?: ProjectArtifact[];
};

export type Chat = {
  id: string;
  kind: "channel" | "agent" | "saved";
  title: string;
  agent_kind: AgentKind | string;
  pinned: boolean;
  enabled: boolean;
  last_message: string;
  last_role: string;
  last_message_at_ms: number;
};

export type ChatMessage = {
  id: string;
  role: "user" | "agent";
  text: string;
  created_at_ms: number;
  agent_id?: string;
  sender?: string;
  session_id?: string;
  streaming?: boolean;
};

export type MeshEdge = { id: string; from: string; to: string };

export type Topology = {
  agents: { id: string; display_name: string; presence: string }[];
  sessions: MeshEdge[];
};

export type View = "chats" | "graph" | "lifecycle" | "create";
