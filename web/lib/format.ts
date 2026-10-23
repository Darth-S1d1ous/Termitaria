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

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) {
    const kb = bytes / 1024;
    return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  }
  const mb = bytes / (1024 * 1024);
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

export function fileTitle(name: string): string {
  const stem = name.replace(/\.[^.]+$/, "");
  const words = stem
    .replace(/[-_]+/g, (match, offset) => {
      const before = stem[offset - 1];
      const after = stem[offset + match.length];
      if (before && after && /\d/.test(before) && /\d/.test(after)) return "-";
      return " ";
    })
    .trim();
  if (!words) return name;
  return words.charAt(0).toUpperCase() + words.slice(1);
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
