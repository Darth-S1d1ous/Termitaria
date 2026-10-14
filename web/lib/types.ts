export type AgentKind = "ORCHESTRATOR" | "WORKER" | "";

export type Project = {
  id: string;
  name: string;
  goal: string;
  agents: { id: string; display_name: string; kind: string }[];
};

export type Chat = {
  id: string;
  kind: "agent" | "saved";
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
};

export type View = "chats" | "graph" | "lifecycle" | "create";
