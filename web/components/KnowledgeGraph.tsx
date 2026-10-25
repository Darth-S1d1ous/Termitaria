"use client";

import { useEffect, useState } from "react";
import { loadProjectGraph, type ProjectGraph, type ProjectGraphEdge, type ProjectGraphNode } from "../lib/api";
import { ProjectPaneSwitch, type ProjectPane } from "./ProjectPaneSwitch";

const KIND_COLOR: Record<string, string> = {
  CONCEPT: "#2b5278",
  DOCUMENT: "#3390ec",
  PAPER: "#8b7ec8",
  CLAIM: "#3d8b6e",
};

const KINDS = ["CONCEPT", "DOCUMENT", "PAPER", "CLAIM"] as const;

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

function ring(items: { id: string }[], cx: number, cy: number, radius: number, pos: Map<string, { x: number; y: number }>) {
  items.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(items.length, 1) - Math.PI / 2;
    pos.set(node.id, {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    });
  });
}

function place(nodes: ProjectGraphNode[], edges: ProjectGraphEdge[], size: number) {
  const pos = new Map<string, { x: number; y: number }>();
  const cx = size / 2;
  const cy = size / 2;
  const concepts = nodes.filter((node) => node.kind === "CONCEPT");
  const docs = nodes.filter((node) => node.kind === "DOCUMENT" || node.kind === "PAPER");
  const claims = nodes.filter((node) => node.kind === "CLAIM");
  const rest = nodes.filter((node) => node.kind !== "CONCEPT" && node.kind !== "DOCUMENT" && node.kind !== "PAPER" && node.kind !== "CLAIM");
  if (concepts.length <= 1) {
    concepts.forEach((node) => pos.set(node.id, { x: cx, y: cy }));
  } else {
    ring(concepts, cx, cy, 48, pos);
  }
  ring(docs, cx, cy, 188, pos);
  const docIds = new Set(docs.map((node) => node.id));
  const docAngle = new Map<string, number>();
  docs.forEach((node, index) => {
    docAngle.set(node.id, (Math.PI * 2 * index) / Math.max(docs.length, 1) - Math.PI / 2);
  });
  const grouped = new Map<string, ProjectGraphNode[]>();
  const loose: ProjectGraphNode[] = [];
  for (const claim of claims) {
    let docId = "";
    for (const edge of edges) {
      if (edge.source === claim.id && docIds.has(edge.target)) docId = edge.target;
      if (edge.target === claim.id && docIds.has(edge.source)) docId = edge.source;
    }
    if (!docId) {
      loose.push(claim);
      continue;
    }
    const list = grouped.get(docId) ?? [];
    list.push(claim);
    grouped.set(docId, list);
  }
  for (const [docId, list] of grouped) {
    const base = docAngle.get(docId) ?? -Math.PI / 2;
    const span = Math.min(Math.PI * 0.95, Math.max(list.length - 1, 1) * 0.55);
    list.forEach((claim, index) => {
      const t = list.length === 1 ? 0 : index / (list.length - 1) - 0.5;
      const angle = base + t * span;
      pos.set(claim.id, {
        x: cx + 330 * Math.cos(angle),
        y: cy + 330 * Math.sin(angle),
      });
    });
  }
  ring(loose, cx, cy, 310, pos);
  ring(rest, cx, cy, 310, pos);
  return pos;
}

function shortLabel(node: ProjectGraphNode) {
  const raw = node.title || node.id;
  const max = node.kind === "CLAIM" ? 18 : 22;
  return raw.length > max ? `${raw.slice(0, max - 1)}…` : raw;
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

  useEffect(() => {
    if (!projectId) {
      setGraph(null);
      setStatus("This project has no knowledge graph yet.");
      return;
    }
    let cancelled = false;
    setStatus("Loading the knowledge graph.");
    loadProjectGraph(projectId)
      .then((payload) => {
        if (cancelled) return;
        setGraph(payload);
        setSelectedId(null);
        setStatus("");
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setGraph(null);
        setStatus(err.message || "The knowledge graph is not reachable.");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const selected = nodes.find((node) => node.id === selectedId) ?? null;
  const neighbors = selected ? neighborsOf(selected.id, nodes, edges) : [];
  const neighborIds = new Set(neighbors.map((item) => item.node.id));
  const caption = describe(graph, status);

  const size = 760;
  const pos = place(nodes, edges, size);

  return (
    <section className="kg" aria-label="Knowledge graph">
      <header className="pane-header">
        <div className="pane-title">
          <h1>Knowledge graph</h1>
          <p>{caption}</p>
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
        <svg className={selected ? "picked" : ""} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Project knowledge graph">
          {edges.map((edge) => {
            const a = pos.get(edge.source);
            const b = pos.get(edge.target);
            if (!a || !b) return null;
            const hot = selected && (edge.source === selected.id || edge.target === selected.id);
            const tone = edge.relation && edge.relation !== "CITES" && edge.relation !== "DERIVES_FROM" ? " named" : "";
            return (
              <line
                key={edge.id}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                className={`kg-edge${tone}${hot ? " hot" : ""}`}
              />
            );
          })}
          {nodes.map((node) => {
            const at = pos.get(node.id);
            if (!at) return null;
            const on = selected?.id === node.id;
            const near = neighborIds.has(node.id);
            const radius = node.kind === "CLAIM" ? 16 : on ? 28 : 22;
            const outward = node.kind === "CONCEPT" || at.y >= size / 2 ? 1 : -1;
            return (
              <g
                key={node.id}
                transform={`translate(${at.x} ${at.y})`}
                className={`kg-node${on ? " on" : ""}${near ? " near" : ""}`}
                role="button"
                tabIndex={0}
                aria-label={`${node.kind} ${node.title}`}
                onClick={() => setSelectedId(node.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") setSelectedId(node.id);
                }}
              >
                <circle r={radius} fill={KIND_COLOR[node.kind] ?? "#8b7ec8"} />
                <text textAnchor="middle" dy={outward * (radius + 16)} className="kg-label">
                  {shortLabel(node)}
                </text>
              </g>
            );
          })}
        </svg>
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
                    <button type="button" onClick={() => setSelectedId(item.node.id)}>
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
            <p className="slot-hint">Select a node to read its text and links.</p>
          )}
        </aside>
      </div>
    </section>
  );
}

function describe(graph: ProjectGraph | null, status: string) {
  if (status) return status;
  if (!graph || graph.nodes.length === 0) return "This project has no documents in the knowledge graph yet.";
  const docs = graph.nodes.filter((node) => node.kind === "DOCUMENT" || node.kind === "PAPER").length;
  const claims = graph.nodes.filter((node) => node.kind === "CLAIM").length;
  return `${docs} documents, ${claims} claims. Click a node to read its text and links.`;
}
