export type AgentMark = {
  name: string;
  color: string;
  path: string;
};

// 每个 agent 一个固定的颜色 + 动物。同一批 id 里不重复。
const MARKS: AgentMark[] = [
  { name: "fox", color: "#e07a3d", path: "M4 13c1-5 4-8 8-8s7 3 8 8c0 4-3 8-8 8s-8-4-8-8zM7 11 8.2 4 11 10zm6 0 2.8-6L17 11zM10 16h4l-2 3z" },
  { name: "owl", color: "#5c6bc0", path: "M8 8 6 3l4 4 2-5 2 5 4-4-2 5c2 1 3 3 3 6a7 7 0 0 1-14 0c0-3 1-5 3-6zm1 6a1.6 1.6 0 1 0 0.01 0zm6 0a1.6 1.6 0 1 0 0.01 0z" },
  { name: "whale", color: "#2f80b0", path: "M2 14c3-5 8-7 13-5 2 .7 4 2 5 4l2 2-4-1v4l-3-3c-3 2-8 2-13-1z" },
  { name: "rabbit", color: "#d36b8e", path: "M8 12c0-6 .6-9 1.6-9S11 6 11 12a5 5 0 0 0 2 0c0-6 .4-9 1.4-9s1.6 3 1.6 9c2 .4 3 2 3 4a6 6 0 0 1-12 0c0-2 1-3.6 3-4z" },
  { name: "frog", color: "#2f9e57", path: "M4 10a3 3 0 0 1 5.2 1.2A7 7 0 0 0 5 16a6 6 0 0 0 14 0 7 7 0 0 0-4.2-4.8A3 3 0 0 1 20 10a3 3 0 0 1-3 4 8 8 0 0 1-10 0 3 3 0 0 1-3-4z" },
  { name: "penguin", color: "#3d4f66", path: "M12 2a5 5 0 0 0-5 6c0 6 2 9 3.2 11L12 22l1.8-3C15 17 17 14 17 8a5 5 0 0 0-5-6zm-2.2 8h4.4v2.2a2.2 2.2 0 0 1-4.4 0z" },
  { name: "cat", color: "#7b5ea7", path: "M4 11 7 3l3 5h4l3-5 3 8c1 1 2 3 2 5a7 7 0 0 1-14 0c0-2 1-4 2-5z" },
  { name: "lion", color: "#c8962e", path: "M12 2a3 3 0 0 0-1 5.8A6 6 0 0 0 6 13a6 6 0 0 0 12 0 6 6 0 0 0-5-5.2A3 3 0 0 0 12 2zm-7 8a3 3 0 0 0 2 5 8 8 0 0 0 10 0 3 3 0 0 0 2-5 8 8 0 0 1-14 0z" },
  { name: "octopus", color: "#c44b7a", path: "M12 2a5 5 0 0 1 5 6c1 0 3 1 3 3 0 2-2 3-3 2l1 3-2 1 1 4h-2l-1-4h-1l1 5h-2l-1-5H9l1 4H8l1-4-2-1 1-3c-1 1-3 0-3-2 0-2 2-3 3-2a5 5 0 0 1 5-6z" },
  { name: "turtle", color: "#1f8a7a", path: "M8 9c0-3 2-5 4-5s4 2 4 5c3 1 5 3 5 5s-3 4-6 4c0 2-1 3-3 3s-3-1-3-3c-3 0-6-2-6-4s2-4 5-5z" },
  { name: "panda", color: "#4b5563", path: "M6 4a2.2 2.2 0 0 0-.2 4A6 6 0 0 0 12 20a6 6 0 0 0 6.2-12A2.2 2.2 0 0 0 18 4a2.2 2.2 0 0 0-2 2.2A6 6 0 0 0 8 6.2 2.2 2.2 0 0 0 6 4zm2.2 8a1.5 1.5 0 1 0 .01 0zm7.5 0a1.5 1.5 0 1 0 .01 0z" },
  { name: "tiger", color: "#c4512c", path: "M5 12 8 4l2.5 5h3L16 4l3 8c1 1 2 3 2 5a7 7 0 0 1-14 0c0-2 1-4 2-5zm2 3.2h2.4V16H7zm7.6 0H17V16h-2.4z" },
];

function hash(id: string) {
  let value = 0;
  for (const char of id) value = (value * 31 + char.charCodeAt(0)) >>> 0;
  return value;
}

export function markFor(id: string, ids: string[]): AgentMark {
  const sorted = [...new Set(ids)].sort();
  const used = new Set<number>();
  let chosen = hash(id) % MARKS.length;
  for (const item of sorted) {
    let index = hash(item) % MARKS.length;
    let guard = 0;
    while (used.has(index) && guard < MARKS.length) {
      index = (index + 1) % MARKS.length;
      guard += 1;
    }
    used.add(index);
    if (item === id) chosen = index;
  }
  return MARKS[chosen];
}

export function AnimalIcon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
      <path fill="currentColor" fillRule="evenodd" d={path} />
    </svg>
  );
}
