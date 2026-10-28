export type Point = { x: number; y: number };
export type View = { x: number; y: number; k: number };

export const ZOOM_MIN = 0.2;
export const ZOOM_MAX = 2.6;
export const FIT_MAX = 1.35;

export type LayoutNode = { id: string; title: string; kind: string };
export type LayoutEdge = { id: string; source: string; target: string };

export function shortLabel(node: { title?: string; id: string }) {
  const raw = (node.title || node.id).replace(/\s+/g, " ").trim();
  return raw.length > 24 ? `${raw.slice(0, 23)}…` : raw;
}

export function labelWidth(node: { title?: string; id: string }) {
  return shortLabel(node).length * 6.3 + 14;
}

export function alwaysLabeled(kind: string) {
  return kind === "DOCUMENT" || kind === "PAPER";
}

function collideRadius(node: LayoutNode) {
  if (alwaysLabeled(node.kind)) return labelWidth(node) / 2 + 8;
  return 26;
}

function angleOf(id: string) {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 33 + id.charCodeAt(i)) >>> 0;
  return ((hash % 360) * Math.PI) / 180;
}

function neighborPoint(id: string, links: LayoutEdge[], pos: Map<string, Point>) {
  for (const edge of links) {
    const other = edge.source === id ? edge.target : edge.target === id ? edge.source : "";
    const at = other ? pos.get(other) : undefined;
    if (at) return at;
  }
  return undefined;
}

// A new node starts beside a node that is already placed. Later iterations
// only move the new nodes, so a poll does not reshuffle the picture.
function placeNewcomers(items: LayoutNode[], links: LayoutEdge[], prev: Map<string, Point>) {
  const pos = new Map<string, Point>();
  const fresh = new Set<string>();
  const newcomers: LayoutNode[] = [];
  for (const node of items) {
    const old = prev.get(node.id);
    if (old) pos.set(node.id, { x: old.x, y: old.y });
    else newcomers.push(node);
  }
  const waiting = [...newcomers];
  const deferred: LayoutNode[] = [];
  for (const node of waiting) {
    const anchor = neighborPoint(node.id, links, pos);
    if (!anchor) {
      deferred.push(node);
      continue;
    }
    const angle = angleOf(node.id);
    pos.set(node.id, { x: anchor.x + Math.cos(angle) * 96, y: anchor.y + Math.sin(angle) * 96 });
    fresh.add(node.id);
  }
  deferred.forEach((node, index) => {
    const anchor = neighborPoint(node.id, links, pos);
    const angle = angleOf(node.id);
    if (anchor) {
      pos.set(node.id, { x: anchor.x + Math.cos(angle) * 96, y: anchor.y + Math.sin(angle) * 96 });
    } else {
      const turn = (Math.PI * 2 * index) / Math.max(deferred.length, 1);
      pos.set(node.id, { x: 160 * Math.cos(turn), y: 160 * Math.sin(turn) });
    }
    fresh.add(node.id);
  });
  return { pos, fresh };
}

function relax(items: LayoutNode[], links: LayoutEdge[], pos: Map<string, Point>, movable: Set<string>, iterations: number) {
  for (let step = 0; step < iterations; step++) {
    const cool = 1 - step / iterations;
    const disp = new Map<string, Point>();
    for (const node of items) disp.set(node.id, { x: 0, y: 0 });
    for (let i = 0; i < items.length; i++) {
      for (let j = i + 1; j < items.length; j++) {
        const a = items[i].id;
        const b = items[j].id;
        if (!movable.has(a) && !movable.has(b)) continue;
        const pa = pos.get(a)!;
        const pb = pos.get(b)!;
        let dx = pa.x - pb.x;
        let dy = pa.y - pb.y;
        let dist = Math.hypot(dx, dy) || 0.01;
        const min = collideRadius(items[i]) + collideRadius(items[j]);
        dx /= dist;
        dy /= dist;
        const push = (min * min) / (dist * dist) * 14 + (dist < min ? (min - dist) * 0.65 : 0);
        disp.get(a)!.x += dx * push;
        disp.get(a)!.y += dy * push;
        disp.get(b)!.x -= dx * push;
        disp.get(b)!.y -= dy * push;
      }
    }
    for (const edge of links) {
      if (!movable.has(edge.source) && !movable.has(edge.target)) continue;
      const pa = pos.get(edge.source)!;
      const pb = pos.get(edge.target)!;
      const dx = pb.x - pa.x;
      const dy = pb.y - pa.y;
      const dist = Math.hypot(dx, dy) || 0.01;
      const pull = (dist - 112) * 0.08;
      disp.get(edge.source)!.x += (dx / dist) * pull;
      disp.get(edge.source)!.y += (dy / dist) * pull;
      disp.get(edge.target)!.x -= (dx / dist) * pull;
      disp.get(edge.target)!.y -= (dy / dist) * pull;
    }
    const limit = 28 * cool + 2;
    for (const node of items) {
      if (!movable.has(node.id)) continue;
      const point = pos.get(node.id)!;
      const delta = disp.get(node.id)!;
      delta.x -= point.x * 0.02;
      delta.y -= point.y * 0.02;
      const mag = Math.hypot(delta.x, delta.y) || 1;
      const stepLen = Math.min(mag, limit);
      point.x += (delta.x / mag) * stepLen;
      point.y += (delta.y / mag) * stepLen;
    }
  }
  return pos;
}

export function layoutNetwork(nodes: LayoutNode[], edges: LayoutEdge[], prev?: Map<string, Point> | null) {
  const degree = new Map<string, number>();
  const items = [...nodes].sort((a, b) => a.id.localeCompare(b.id));
  for (const node of items) degree.set(node.id, 0);
  const links = edges
    .filter((edge) => degree.has(edge.source) && degree.has(edge.target) && edge.source !== edge.target)
    .sort((a, b) => a.id.localeCompare(b.id));
  for (const edge of links) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const kept = prev ? items.filter((node) => prev.has(node.id)).length : 0;
  if (!prev || kept === 0) {
    const pos = new Map<string, Point>();
    const count = items.length;
    items.forEach((node, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(count, 1) - Math.PI / 2;
      const ring = 36 + Math.sqrt(count) * 28;
      pos.set(node.id, { x: ring * Math.cos(angle), y: ring * Math.sin(angle) });
    });
    const iterations = Math.min(240, 90 + count * 2);
    return { pos: relax(items, links, pos, new Set(items.map((node) => node.id)), iterations), degree };
  }
  const placed = placeNewcomers(items, links, prev);
  if (placed.fresh.size === 0) return { pos: placed.pos, degree };
  return { pos: relax(items, links, placed.pos, placed.fresh, 40), degree };
}

export function graphSignature(nodes: { id: string }[], edges: { id: string }[]) {
  return `${nodes.map((node) => node.id).sort().join("\0")}\n${edges.map((edge) => edge.id).sort().join("\0")}`;
}

export function bounds(pos: Map<string, Point>, nodes: LayoutNode[]) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    const at = pos.get(node.id);
    if (!at) continue;
    const labeled = alwaysLabeled(node.kind);
    const half = labeled ? shortLabel(node).length * 3.6 + 16 : 24;
    minX = Math.min(minX, at.x - half);
    maxX = Math.max(maxX, at.x + half);
    minY = Math.min(minY, at.y - 28);
    maxY = Math.max(maxY, at.y + (labeled ? 48 : 28));
  }
  if (!Number.isFinite(minX)) return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  return { minX, minY, maxX, maxY };
}

export function frameView(pos: Map<string, Point>, nodes: LayoutNode[], width: number, height: number): View {
  const box = bounds(pos, nodes);
  const w = Math.max(box.maxX - box.minX, 1);
  const h = Math.max(box.maxY - box.minY, 1);
  const cx = (box.minX + box.maxX) / 2;
  const cy = (box.minY + box.maxY) / 2;
  const raw = Math.min((width - 72) / w, (height - 72) / h);
  const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.min(FIT_MAX, raw)));
  return { k, x: width / 2 - cx * k, y: height / 2 - cy * k };
}

function endPoint(from: Point, to: Point, pad: number) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.hypot(dx, dy) || 1;
  const span = Math.max(dist - pad, 0) / dist;
  return { x: from.x + dx * span, y: from.y + dy * span };
}

export function edgePath(from: Point, to: Point, sourcePad: number, targetPad: number, offset: number) {
  const start = endPoint(from, to, sourcePad);
  const end = endPoint(to, from, targetPad);
  if (Math.abs(offset) < 1) {
    return {
      d: `M${start.x} ${start.y} L${end.x} ${end.y}`,
      labelAt: { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 },
    };
  }
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const dist = Math.hypot(dx, dy) || 1;
  const cx = (start.x + end.x) / 2 + (-dy / dist) * offset;
  const cy = (start.y + end.y) / 2 + (dx / dist) * offset;
  return {
    d: `M${start.x} ${start.y} Q${cx} ${cy} ${end.x} ${end.y}`,
    labelAt: { x: 0.25 * start.x + 0.5 * cx + 0.25 * end.x, y: 0.25 * start.y + 0.5 * cy + 0.25 * end.y },
  };
}

export function pairKey(source: string, target: string) {
  return source < target ? `${source}\0${target}` : `${target}\0${source}`;
}
