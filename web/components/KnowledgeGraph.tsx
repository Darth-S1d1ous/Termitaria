"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  loadProjectGraph,
  uploadProjectKnowledge,
  type ProjectGraph,
  type ProjectGraphEdge,
  type ProjectGraphNode,
} from "../lib/api";
import {
  ZOOM_MAX,
  ZOOM_MIN,
  alwaysLabeled,
  edgePath,
  frameView,
  graphSignature,
  labelWidth,
  layoutNetwork,
  pairKey,
  shortLabel,
  type Point,
  type View,
} from "../lib/graphLayout";
import { ProjectPaneSwitch, type ProjectPane } from "./ProjectPaneSwitch";

const KIND_COLOR: Record<string, string> = {
  CONCEPT: "#2b5278",
  DOCUMENT: "#3390ec",
  PAPER: "#8b7ec8",
  CLAIM: "#3d8b6e",
};

const KINDS = ["CONCEPT", "DOCUMENT", "PAPER", "CLAIM"] as const;
const TEXT_FILE = /\.(txt|md|markdown|csv|json|tsv|log|yaml|yml|html|xml|tex)$/i;
const LABEL_ZOOM = 1;

function linkPhrase(direction: "out" | "in", edge: ProjectGraphEdge) {
  if (direction === "in" && edge.relation === "CITES") return "cited by";
  if (direction === "in" && edge.relation === "DERIVES_FROM") return "derived claim";
  return (edge.relation || "link").toLowerCase().replace(/_/g, " ");
}

function relationLabel(relation: string) {
  const text = (relation || "link").toLowerCase().replace(/_/g, " ");
  return text.length > 18 ? `${text.slice(0, 17)}…` : text;
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

function nodeRadius(degree: number, selected: boolean) {
  const base = 12 + Math.min(14, degree * 1.7);
  return selected ? base + 6 : base;
}

function showNodeLabel(node: ProjectGraphNode, on: boolean, near: boolean, match: boolean, hover: boolean, k: number) {
  if (alwaysLabeled(node.kind)) return true;
  if (on || near || match || hover) return true;
  return k >= LABEL_ZOOM;
}

function matchCount(count: number) {
  if (count === 0) return "No matches";
  if (count === 1) return "1 match";
  return `${count} matches`;
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
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [uploadNote, setUploadNote] = useState("");
  const [uploading, setUploading] = useState(false);
  const [view, setView] = useState<View>({ x: 40, y: 40, k: 1 });
  const [pendingFocus, setPendingFocus] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const stageRef = useRef<HTMLDivElement>(null);
  const worldRef = useRef<SVGGElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const positionsRef = useRef<Map<string, Point>>(new Map());
  const didFit = useRef(false);
  const userMoved = useRef(false);
  const projectSeen = useRef(projectId);
  const focusedQuery = useRef("");
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const viewRef = useRef(view);

  if (projectSeen.current !== projectId) {
    projectSeen.current = projectId;
    positionsRef.current = new Map();
    didFit.current = false;
    userMoved.current = false;
  }

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
  const graphRef = useRef({ nodes, edges });
  graphRef.current = { nodes, edges };
  const signature = useMemo(() => graphSignature(nodes, edges), [nodes, edges]);
  const placed = useMemo(
    () => layoutNetwork(graphRef.current.nodes, graphRef.current.edges, positionsRef.current),
    [signature],
  );
  positionsRef.current = placed.pos;
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
  const listedHits = hits.slice(0, 8);

  function paintTransform(next: View) {
    viewRef.current = next;
    worldRef.current?.setAttribute("transform", `translate(${next.x} ${next.y}) scale(${next.k})`);
  }

  function commitView(next: View) {
    paintTransform(next);
    setView(next);
  }

  function noteUserMove() {
    if (didFit.current) userMoved.current = true;
  }

  function canvasRect() {
    return stageRef.current?.querySelector("svg")?.getBoundingClientRect();
  }

  function focusAt(id: string, scale = viewRef.current.k) {
    const at = pos.get(id);
    const rect = canvasRect();
    if (!at || !rect) return;
    setSelectedId(id);
    noteUserMove();
    commitView({ k: scale, x: rect.width / 2 - at.x * scale, y: rect.height / 2 - at.y * scale });
  }

  function fitView() {
    const rect = canvasRect();
    if (!rect || nodes.length === 0) return;
    commitView(frameView(pos, nodes, rect.width, rect.height));
  }

  useLayoutEffect(() => {
    worldRef.current?.setAttribute("transform", `translate(${viewRef.current.x} ${viewRef.current.y}) scale(${viewRef.current.k})`);
  });

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
    if (didFit.current || userMoved.current || nodes.length === 0) return;
    const rect = canvasRect();
    if (!rect || rect.width < 40 || rect.height < 40) return;
    commitView(frameView(pos, nodes, rect.width, rect.height));
    didFit.current = true;
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
    let timer = 0;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      noteUserMove();
      const current = viewRef.current;
      const next = event.deltaY > 0 ? current.k * 0.9 : current.k * 1.1;
      const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next));
      const rect = el.querySelector("svg")?.getBoundingClientRect() ?? el.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      const wx = (px - current.x) / current.k;
      const wy = (py - current.y) / current.k;
      paintTransform({ k, x: px - wx * k, y: py - wy * k });
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setView({ ...viewRef.current }), 80);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      window.clearTimeout(timer);
      el.removeEventListener("wheel", onWheel);
    };
  }, []);

  function zoomBy(factor: number) {
    const rect = canvasRect();
    const current = viewRef.current;
    const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, current.k * factor));
    noteUserMove();
    if (!rect) {
      commitView({ ...current, k });
      return;
    }
    const px = rect.width / 2;
    const py = rect.height / 2;
    const wx = (px - current.x) / current.k;
    const wy = (py - current.y) / current.k;
    commitView({ k, x: px - wx * k, y: py - wy * k });
  }

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    const target = event.target as Element;
    if (target.closest(".kg-node") || target.closest(".kg-float") || target.closest(".kg-search-wrap")) return;
    drag.current = { x: event.clientX, y: event.clientY, vx: viewRef.current.x, vy: viewRef.current.y };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const start = drag.current;
    if (!start) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 3) noteUserMove();
    paintTransform({
      k: viewRef.current.k,
      x: start.vx + event.clientX - start.x,
      y: start.vy + event.clientY - start.y,
    });
  }

  function onPointerUp() {
    if (!drag.current) return;
    drag.current = null;
    setView({ ...viewRef.current });
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
  const pairCounts = new Map<string, number>();
  for (const edge of edges) {
    const key = pairKey(edge.source, edge.target);
    pairCounts.set(key, (pairCounts.get(key) ?? 0) + 1);
  }
  const pairSeen = new Map<string, number>();
  const hotCount = selected ? edges.filter((edge) => edge.source === selected.id || edge.target === selected.id).length : 0;
  const rawText = selected?.text?.trim() ?? "";
  const bodyText = rawText && rawText !== (selected?.title || "").trim() ? selected?.text ?? "" : "";

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
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <svg className={selected ? "picked" : ""} viewBox={`0 0 ${width} ${height}`} role="group" aria-label="Project knowledge graph">
            <defs>
              <marker id="kg-arrow" markerUnits="userSpaceOnUse" markerWidth="4.8" markerHeight="4.8" refX="4.8" refY="2.4" orient="auto">
                <path d="M0 0 L4.8 2.4 L0 4.8 z" fill="#8aa4b8" />
              </marker>
            </defs>
            <g ref={worldRef} transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
              {edges.map((edge) => {
                const a = pos.get(edge.source);
                const b = pos.get(edge.target);
                if (!a || !b || edge.source === edge.target) return null;
                const key = pairKey(edge.source, edge.target);
                const count = pairCounts.get(key) ?? 1;
                const index = pairSeen.get(key) ?? 0;
                pairSeen.set(key, index + 1);
                const hot = Boolean(selected && (edge.source === selected.id || edge.target === selected.id));
                const tone = edge.relation && edge.relation !== "CITES" && edge.relation !== "DERIVES_FROM" ? " named" : "";
                const curve = edgePath(
                  a,
                  b,
                  nodeRadius(degree.get(edge.source) ?? 0, selected?.id === edge.source),
                  nodeRadius(degree.get(edge.target) ?? 0, selected?.id === edge.target),
                  (index - (count - 1) / 2) * 36,
                );
                const phrase = relationLabel(edge.relation);
                const phraseWidth = phrase.length * 5.6 + 10;
                return (
                  <g key={edge.id}>
                    <path d={curve.d} className={`kg-edge${tone}${hot ? " hot" : ""}`} markerEnd="url(#kg-arrow)" />
                    {hot && hotCount <= 8 ? (
                      <g className="kg-edge-label" transform={`translate(${curve.labelAt.x} ${curve.labelAt.y})`}>
                        <rect x={-phraseWidth / 2} y={-8} width={phraseWidth} height={16} rx={4} />
                        <text textAnchor="middle" dominantBaseline="middle">
                          {phrase}
                        </text>
                      </g>
                    ) : null}
                  </g>
                );
              })}
              {paint.map((node) => {
                const at = pos.get(node.id);
                if (!at) return null;
                const on = selected?.id === node.id;
                const near = neighborIds.has(node.id);
                const match = Boolean(needle && hitIds.has(node.id));
                const hover = hoverId === node.id;
                const dim = Boolean(needle && !match);
                const radius = nodeRadius(degree.get(node.id) ?? 0, on);
                const visible = showNodeLabel(node, on, near, match, hover, view.k);
                const label = shortLabel(node);
                const textWidth = labelWidth(node);
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
                    onPointerEnter={() => setHoverId(node.id)}
                    onPointerLeave={() => setHoverId((current) => (current === node.id ? null : current))}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") setSelectedId(node.id);
                    }}
                  >
                    <circle r={radius} fill={KIND_COLOR[node.kind] ?? "#8b7ec8"} />
                    <title>{node.title || node.id}</title>
                    {visible ? (
                      <>
                        <rect className="kg-label-bg" x={-textWidth / 2} y={ly - 9} width={textWidth} height={18} rx={4} />
                        <text textAnchor="middle" dominantBaseline="middle" y={ly} className="kg-label">
                          {label}
                        </text>
                      </>
                    ) : null}
                  </g>
                );
              })}
            </g>
          </svg>
          <div className="kg-search-wrap">
            <input
              className="kg-search"
              aria-label="Search nodes"
              placeholder="Search nodes"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            {needle ? (
              <div className="kg-matches">
                <p>{matchCount(hits.length)}</p>
                <ul>
                  {listedHits.map((node) => (
                    <li key={node.id}>
                      <button type="button" onClick={() => focusAt(node.id, viewRef.current.k)}>
                        {node.title || node.id}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
          <div className="kg-float">
            <button type="button" aria-label="Zoom in" onClick={() => zoomBy(1.15)}>
              <BarIcon d="M12 5v14M6 12h12" />
            </button>
            <button type="button" aria-label="Zoom out" onClick={() => zoomBy(0.87)}>
              <BarIcon d="M6 12h12" />
            </button>
            <button type="button" className="kg-fit" aria-label="Fit graph" onClick={fitView}>
              Fit
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
              {bodyText ? (
                <div className="kg-text">
                  <p className="body">{bodyText}</p>
                </div>
              ) : null}
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
