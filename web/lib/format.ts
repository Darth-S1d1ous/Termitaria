const PALETTE = ["#e17076", "#7bc862", "#e5ca77", "#65aadd", "#ee7aae", "#6ec9cb", "#faa774"];

export function colorFor(id: string): string {
  let hash = 0;
  for (const char of id) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  return PALETTE[hash % PALETTE.length];
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2 && /^[A-Za-z0-9]/.test(parts[0]) && /^[A-Za-z0-9]/.test(parts[1])) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return name.trim().slice(0, 1).toUpperCase() || "?";
}

export function formatTime(ms: number): string {
  if (!ms) return "";
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function preview(chat: { last_message: string; last_role: string }): string {
  if (!chat.last_message) return "";
  return chat.last_role === "user" ? `You: ${chat.last_message}` : chat.last_message;
}

export function statusLine(chat: { kind: string; agent_kind: string }): string {
  if (chat.kind === "channel") return "channel";
  if (chat.kind === "saved") return "saved messages";
  if (chat.agent_kind === "ORCHESTRATOR") return "orchestrator";
  return "agent";
}
