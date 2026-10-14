import type { Chat, ChatMessage, Project, Topology } from "./types";

const API = process.env.NEXT_PUBLIC_CLIENT_API ?? "http://127.0.0.1:8787";

export function liveSocketURL(projectId: string) {
  return `${API.replace(/^http/, "ws")}/api/ws?project=${encodeURIComponent(projectId)}`;
}

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    method: init?.method ?? "GET",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    body: init?.body ? JSON.stringify(init.body) : undefined,
  });
  if (res.status === 204) {
    return undefined as T;
  }
  const payload = (await res.json().catch(() => ({}))) as { error?: string };
  if (!res.ok) {
    throw new Error(payload.error || `request failed (${res.status})`);
  }
  return payload as T;
}

export function loadProjects() {
  return api<{ projects: Project[] }>("/api/projects");
}

export function createProject(name: string) {
  return api<Project>("/api/projects", { method: "POST", body: { name } });
}

export function deleteProject(projectId: string) {
  return api<void>(`/api/projects/${projectId}`, { method: "DELETE" });
}

export function loadChats(projectId: string) {
  return api<{ chats: Chat[] }>(`/api/projects/${projectId}/chats`);
}

export function loadTopology(projectId: string) {
  return api<Topology>(`/api/projects/${projectId}/topology`);
}

export function loadMessages(projectId: string, chatId: string) {
  return api<{ messages: ChatMessage[] }>(`/api/projects/${projectId}/chats/${chatId}/messages`);
}

export function sendMessage(projectId: string, chatId: string, text: string) {
  return api<{ user: ChatMessage; agent: ChatMessage }>(
    `/api/projects/${projectId}/chats/${chatId}/messages`,
    { method: "POST", body: { text } },
  );
}
