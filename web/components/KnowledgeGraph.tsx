"use client";

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  loadProjectGraph,
  uploadProjectKnowledge,
  type ProjectGraph,
  type ProjectGraphEdge,
  type ProjectGraphNode,
} from "../lib/api";
import { ProjectPaneSwitch, type ProjectPane } from "./ProjectPaneSwitch";

const KIND_COLOR: Record<string, string> = {
  CONCEPT: "#2b5278",
  DOCUMENT: "#3390ec",
  PAPER: "#8b7ec8",
  CLAIM: "#3d8b6e",
};

const KINDS = ["CONCEPT", "DOCUMENT", "PAPER", "CLAIM"] as const;
const TEXT_FILE = /\.(txt|md|markdown|csv|json|tsv|log|yaml|yml|html|xml|tex)$/i;

type Point = { x: number; y: number };
type View = { x: number; y: number; k: number };

function linkPhrase(direction: "out" | "in", edge: ProjectGraphEdge) {
  if (direction === "in" && edge.relation === "CITES") return "cited by";
  if (direction === "in" && edge.relation === "DERIVES_FROM") return "derived claim";
  return (edge.relation || "link").toLowerCase().replace(/_/g, " ");
}

function neighborsOf(nodeId: string, nodes: ProjectGraphNode[], edges: ProjectGraphEdge[]) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const out: { edge: ProjectGraphEdge; direction: "out" | "in"; node: ProjectGraphNode }[] = [];
  for (const edge of edges) {
    if (edge.source === nodeId) {
      const node = byId.get(edge.target);
      if (node) out.push({ edge, direction: "out", node });
    } else if (edge.target === nodeId) {
      const node = byId.get(edge.source);
      if (node) out.push({ edge, direction: "in", node });
    }
  }
  return out;
}

function collideRadius(node: ProjectGraphNode) {
  return 70 + Math.min(28, shortLabel(node).length * 0.35);
}

function layoutNetwork(nodes: ProjectGraphNode[], edges: ProjectGraphEdge[]) {
  const pos = new Map<string, Point>();
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
  const count = items.length;
  items.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(count, 1) - Math.PI / 2;
    const ring = 36 + Math.sqrt(count) * 28;
    pos.set(node.id, { x: ring * Math.cos(angle), y: ring * Math.sin(angle) });
  });
  const iterations = Math.min(240, 90 + count * 2);
  for (let step = 0; step < iterations; step++) {
    const cool = 1 - step / iterations;
    const disp = new Map<string, Point>();
    for (const node of items) disp.set(node.id, { x: 0, y: 0 });
    for (let i = 0; i < items.length; i++) {
      for (let j = i + 1; j < items.length; j++) {
        const a = items[i].id;
        const b = items[j].id;
        const pa = pos.get(a)!;
        const pb = pos.get(b)!;
        let dx = pa.x - pb.x;
        let dy = pa.y - pb.y;
        let dist = Math.hypot(dx, dy) || 0.01;
        const min = (collideRadius(items[i]) + collideRadius(items[j])) * 0.46;
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
  return { pos, degree };
}

function nodeRadius(degree: number, selected: boolean) {
  const base = 12 + Math.min(14, degree * 1.7);
  return selected ? base + 6 : base;
}

function endPoint(from: Point, to: Point, pad: number) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.hypot(dx, dy) || 1;
  const span = Math.max(dist - pad, 0) / dist;
  return { x: from.x + dx * span, y: from.y + dy * span };
}

function shortLabel(node: ProjectGraphNode) {
  const raw = (node.title || node.id).replace(/\s+/g, " ").trim();
  return raw.length > 24 ? `${raw.slice(0, 23)}…` : raw;
}

function bounds(pos: Map<string, Point>, nodes: ProjectGraphNode[]) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    const at = pos.get(node.id);
    if (!at) continue;
    const half = shortLabel(node).length * 3.6 + 16;
    minX = Math.min(minX, at.x - half);
    maxX = Math.max(maxX, at.x + half);
    minY = Math.min(minY, at.y - 32);
    maxY = Math.max(maxY, at.y + 48);
  }
  if (!Number.isFinite(minX)) return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  return { minX, minY, maxX, maxY };
}

function describe(graph: ProjectGraph | null, status: string) {
  if (status) return status;
  if (!graph || graph.nodes.length === 0) return "This project has no documents in the knowledge graph yet.";
  const docs = graph.nodes.filter((node) => node.kind === "DOCUMENT" || node.kind === "PAPER").length;
  const claims = graph.nodes.filter((node) => node.kind === "CLAIM").length;
  return `${docs} documents, ${claims} claims. Drag to move. Scroll to zoom. Click a node to read it.`;
}

export function KnowledgeGraph({
  projectId,
  onPane,
}: {
  projectId: string;
  onPane: (pane: ProjectPane) => void;
}) {
  const [graph, setGraph] = useState<ProjectGraph | null>(null);
  const [status, setStatus] = useState("Loading the knowledge graph.");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [uploadNote, setUploadNote] = useState("");
  const [uploading, setUploading] = useState(false);
  const [view, setView] = useState<View>({ x: 40, y: 40, k: 1 });
  const [pendingFocus, setPendingFocus] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const stageRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const framed = useRef(false);
  const framedRev = useRef(0);
  const focusedQuery = useRef("");
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const viewRef = useRef(view);
  viewRef.current = view;

  useEffect(() => {
    if (!projectId) {
      setGraph(null);
      setStatus("This project has no knowledge graph yet.");
      return;
    }
    let cancelled = false;
    const load = (first: boolean) => {
      if (first) setStatus("Loading the knowledge graph.");
      loadProjectGraph(projectId)
        .then((payload) => {
          if (cancelled) return;
          setGraph(payload);
          setStatus("");
          setSelectedId((current) => (current && payload.nodes.some((node) => node.id === current) ? current : null));
        })
        .catch((err: Error) => {
          if (cancelled || !first) return;
          setGraph(null);
          setStatus(err.message || "The knowledge graph is not reachable.");
        });
    };
    load(true);
    const timer = window.setInterval(() => load(false), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId]);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const placed = useMemo(() => layoutNetwork(nodes, edges), [nodes, edges]);
  const pos = placed.pos;
  const degree = placed.degree;
  const selected = nodes.find((node) => node.id === selectedId) ?? null;
  const neighbors = selected ? neighborsOf(selected.id, nodes, edges) : [];
  const neighborIds = new Set(neighbors.map((item) => item.node.id));
  const needle = query.trim().toLowerCase();
  const hits = needle
    ? nodes.filter((node) => `${node.title} ${node.text}`.toLowerCase().includes(needle))
    : [];
  const hitIds = new Set(hits.map((node) => node.id));

  function canvasRect() {
    return stageRef.current?.querySelector("svg")?.getBoundingClientRect();
  }

  function focusAt(id: string, scale = 1) {
    const at = pos.get(id);
    const rect = canvasRect();
    if (!at || !rect) return;
    setSelectedId(id);
    setView({ k: scale, x: rect.width / 2 - at.x * scale, y: rect.height / 2 - at.y * scale });
  }

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const update = () => {
      const svg = el.querySelector("svg");
      setSize({
        w: Math.max(svg?.clientWidth || el.clientWidth, 1),
        h: Math.max(svg?.clientHeight || el.clientHeight, 1),
      });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const el = stageRef.current;
    if (!el || nodes.length === 0) return;
    if (framed.current && framedRev.current === 4) return;
    const rect = canvasRect();
    if (!rect) return;
    if (rect.width < 40 || rect.height < 40) return;
    const box = bounds(pos, nodes);
    const w = box.maxX - box.minX;
    const h = box.maxY - box.minY;
    const cx = (box.minX + box.maxX) / 2;
    const cy = (box.minY + box.maxY) / 2;
    const k = Math.min(1.35, (rect.width - 72) / w, (rect.height - 72) / h);
    setView({ k, x: rect.width / 2 - cx * k, y: rect.height / 2 - cy * k });
    framed.current = true;
    framedRev.current = 4;
  }, [nodes, pos, size]);

  useEffect(() => {
    if (!pendingFocus || !pos.has(pendingFocus)) return;
    focusAt(pendingFocus);
    setPendingFocus(null);
  }, [pendingFocus, pos]);

  useEffect(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      focusedQuery.current = "";
      return;
    }
    if (q === focusedQuery.current) return;
    const matched = nodes.filter((node) => `${node.title} ${node.text}`.toLowerCase().includes(q));
    if (matched.length !== 1) return;
    focusedQuery.current = q;
    focusAt(matched[0].id);
  }, [query, nodes]);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const current = viewRef.current;
      const next = event.deltaY > 0 ? current.k * 0.9 : current.k * 1.1;
      const k = Math.min(2.6, Math.max(0.35, next));
      const rect = el.querySelector("svg")?.getBoundingClientRect() ?? el.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      const wx = (px - current.x) / current.k;
      const wy = (py - current.y) / current.k;
      setView({ k, x: px - wx * k, y: py - wy * k });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  function zoomBy(factor: number) {
    const rect = canvasRect();
    const current = viewRef.current;
    const k = Math.min(2.6, Math.max(0.35, current.k * factor));
    if (!rect) {
      setView({ ...current, k });
      return;
    }
    const px = rect.width / 2;
    const py = rect.height / 2;
    const wx = (px - current.x) / current.k;
    const wy = (py - current.y) / current.k;
    setView({ k, x: px - wx * k, y: py - wy * k });
  }

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    const target = event.target as Element;
    if (target.closest(".kg-node") || target.closest(".kg-float") || target.closest(".kg-search")) return;
    drag.current = { x: event.clientX, y: event.clientY, vx: viewRef.current.x, vy: viewRef.current.y };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const start = drag.current;
    if (!start) return;
    setView({
      k: viewRef.current.k,
      x: start.vx + event.clientX - start.x,
      y: start.vy + event.clientY - start.y,
    });
  }

  async function onFile(file: File | undefined) {
    if (!file || !projectId) return;
    const typed = !file.type || file.type.startsWith("text") || file.type === "application/json" || file.type === "application/xml";
    if (!typed && !TEXT_FILE.test(file.name)) {
      setUploadNote("Upload a text file.");
      return;
    }
    setUploading(true);
    setUploadNote("");
    try {
      const text = await file.text();
      if (!text.trim() || text.includes("\u0000")) {
        setUploadNote("This file has no text.");
        return;
      }
      const saved = await uploadProjectKnowledge(projectId, file.name, text);
      setUploadNote(saved.truncated ? `Stored the first part of ${saved.title}.` : `Added ${saved.title}.`);
      const payload = await loadProjectGraph(projectId);
      setGraph(payload);
      setStatus("");
      setPendingFocus(saved.node_id);
    } catch (err) {
      setUploadNote(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  const width = size.w;
  const height = size.h;
  const paint = [...nodes].sort((a, b) => Number(a.id === selectedId) - Number(b.id === selectedId));

  return (
    <section className="kg" aria-label="Knowledge graph">
      <header className="pane-header">
        <div className="pane-title">
          <h1>Knowledge graph</h1>
          <p>{describe(graph, status)}</p>
          {uploadNote ? <p className="kg-note">{uploadNote}</p> : null}
          <div className="kg-legend">
            {KINDS.map((kind) => (
              <span key={kind}>
                <i className="kg-swatch" style={{ background: KIND_COLOR[kind] }} />
                {kind.toLowerCase()}
              </span>
            ))}
          </div>
        </div>
        <ProjectPaneSwitch pane="knowledge" onChange={onPane} />
      </header>
      <div className="kg-body">
        <div
          className="kg-stage"
          ref={stageRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={() => {
            drag.current = null;
          }}
        >
          <svg className={selected ? "picked" : ""} viewBox={`0 0 ${width} ${height}`} role="group" aria-label="Project knowledge graph">
            <defs>
              <marker id="kg-arrow" markerUnits="userSpaceOnUse" markerWidth="4.8" markerHeight="4.8" refX="4.8" refY="2.4" orient="auto">
                <path d="M0 0 L4.8 2.4 L0 4.8 z" fill="#8aa4b8" />
              </marker>
            </defs>
            <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
              {edges.map((edge) => {
                const a = pos.get(edge.source);
                const b = pos.get(edge.target);
                if (!a || !b) return null;
                const hot = Boolean(selected && (edge.source === selected.id || edge.target === selected.id));
                const tone = edge.relation && edge.relation !== "CITES" && edge.relation !== "DERIVES_FROM" ? " named" : "";
                const start = endPoint(a, b, nodeRadius(degree.get(edge.source) ?? 0, selected?.id === edge.source));
                const end = endPoint(b, a, nodeRadius(degree.get(edge.target) ?? 0, selected?.id === edge.target));
                return (
                  <line
                    key={edge.id}
                    x1={start.x}
                    y1={start.y}
                    x2={end.x}
                    y2={end.y}
                    className={`kg-edge${tone}${hot ? " hot" : ""}`}
                    markerEnd="url(#kg-arrow)"
                  />
                );
              })}
              {paint.map((node) => {
                const at = pos.get(node.id);
                if (!at) return null;
                const on = selected?.id === node.id;
                const near = neighborIds.has(node.id);
                const match = needle && hitIds.has(node.id);
                const dim = needle && !match;
                const radius = nodeRadius(degree.get(node.id) ?? 0, on);
                const label = shortLabel(node);
                const textWidth = label.length * 6.3 + 14;
                const ly = radius + 14;
                return (
                  <g
                    key={node.id}
                    transform={`translate(${at.x} ${at.y})`}
                    className={`kg-node${on ? " on" : ""}${near ? " near" : ""}${match ? " hit" : ""}${dim ? " dim" : ""}`}
                    role="button"
                    tabIndex={0}
                    aria-label={`${node.kind} ${node.title}`}
                    onClick={() => setSelectedId(node.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") setSelectedId(node.id);
                    }}
                  >
                    <circle r={radius} fill={KIND_COLOR[node.kind] ?? "#8b7ec8"} />
                    <title>{node.title || node.id}</title>
                    <rect className="kg-label-bg" x={-textWidth / 2} y={ly - 9} width={textWidth} height={18} rx={4} />
                    <text textAnchor="middle" dominantBaseline="middle" y={ly} className="kg-label">
                      {label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
          <input
            className="kg-search"
            aria-label="Search nodes"
            placeholder="Search nodes"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="kg-float">
            <button type="button" aria-label="Zoom in" onClick={() => zoomBy(1.15)}>
              <BarIcon d="M12 5v14M6 12h12" />
            </button>
            <button type="button" aria-label="Zoom out" onClick={() => zoomBy(0.87)}>
              <BarIcon d="M6 12h12" />
            </button>
            <button type="button" aria-label="Upload file" disabled={uploading} onClick={() => fileRef.current?.click()}>
              <BarIcon d="M12 16V5M8 8.5 12 4.5 16 8.5M5 19h14" />
            </button>
            <input
              ref={fileRef}
              type="file"
              hidden
              aria-label="Upload a text file"
              accept=".txt,.md,.markdown,.csv,.json,.tsv,.log,.yaml,.yml,.html,.xml,.tex,text/plain"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                void onFile(file);
              }}
            />
          </div>
        </div>
        <aside className="kg-detail" aria-label="Selected node">
          {selected ? (
            <>
              <span className="kg-kind">{selected.kind}</span>
              <h2>{selected.title || selected.id}</h2>
              {selected.text && selected.text.length > selected.title.length ? <p className="body">{selected.text}</p> : null}
              <h3>Links</h3>
              {neighbors.length === 0 ? <p className="slot-hint">No links</p> : null}
              <ul>
                {neighbors.map((item) => (
                  <li key={item.edge.id + item.direction}>
                    <button type="button" onClick={() => focusAt(item.node.id, viewRef.current.k)}>
                      <span>
                        {linkPhrase(item.direction, item.edge)}
                        {item.edge.fact ? ` — ${item.edge.fact}` : ""}
                      </span>
                      <strong>{item.node.title || item.node.id}</strong>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="slot-hint">Select a node to read its text and links. Upload a file to add it to this graph.</p>
          )}
        </aside>
      </div>
    </section>
  );
}

function BarIcon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
