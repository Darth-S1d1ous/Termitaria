"use client";

import { useMemo, useState } from "react";
import { neighborsOf, stubGraph, type GraphNode } from "../lib/graphStub";
import type { Seed } from "../lib/seeds";

const KIND_COLOR: Record<GraphNode["kind"], string> = {
  document: "#3390ec",
  paper: "#4fae4e",
  claim: "#c9a227",
  concept: "#8b7ec8",
};

export function KnowledgeGraph({
  seeds,
  onChats,
}: {
  seeds: Seed[];
  onChats: () => void;
}) {
  const graph = useMemo(() => stubGraph(seeds), [seeds]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = graph.nodes.find((node) => node.id === selectedId) ?? null;
  const neighbors = selected ? neighborsOf(selected.id, graph.nodes, graph.edges) : [];
  const neighborIds = new Set(neighbors.map((item) => item.node.id));

  const size = 640;
  const center = size / 2;
  const radius = 210;
  const pos = new Map<string, { x: number; y: number }>();
  graph.nodes.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(graph.nodes.length, 1) - Math.PI / 2;
    pos.set(node.id, {
      x: center + radius * Math.cos(angle),
      y: center + radius * Math.sin(angle),
    });
  });

  return (
    <section className="kg" aria-label="Knowledge graph">
      <header className="pane-header">
        <div className="pane-title">
          <h1>Knowledge graph</h1>
          <p>Stub graph. Click a node to read it and see its neighbors.</p>
        </div>
        <div className="pane-switch" role="tablist" aria-label="Project views">
          <button type="button" role="tab" aria-selected={false} onClick={onChats}>
            Chats
          </button>
          <button type="button" role="tab" aria-selected>
            Knowledge
          </button>
        </div>
      </header>
      <div className="kg-body">
        <svg className={selected ? "picked" : ""} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Knowledge graph structure">
          {graph.edges.map((edge) => {
            const a = pos.get(edge.from);
            const b = pos.get(edge.to);
            if (!a || !b) return null;
            const hot = selected && (edge.from === selected.id || edge.to === selected.id);
            return (
              <line
                key={edge.id}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                className={`kg-edge${hot ? " hot" : ""}`}
              />
            );
          })}
          {graph.nodes.map((node) => {
            const at = pos.get(node.id);
            if (!at) return null;
            const on = selected?.id === node.id;
            const near = neighborIds.has(node.id);
            return (
              <g
                key={node.id}
                transform={`translate(${at.x} ${at.y})`}
                className={`kg-node${on ? " on" : ""}${near ? " near" : ""}`}
                role="button"
                tabIndex={0}
                aria-label={node.title}
                onClick={() => setSelectedId(node.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") setSelectedId(node.id);
                }}
              >
                <circle r={on ? 28 : 22} fill={KIND_COLOR[node.kind]} />
                <text textAnchor="middle" dy="44" className="kg-label">
                  {node.title.length > 28 ? `${node.title.slice(0, 26)}…` : node.title}
                </text>
              </g>
            );
          })}
        </svg>
        <aside className="kg-detail" aria-label="Selected node">
          {selected ? (
            <>
              <span className="seed-kind">{selected.kind}</span>
              <h2>{selected.title}</h2>
              <p>{selected.content}</p>
              <h3>Neighbors</h3>
              {neighbors.length === 0 ? <p className="slot-hint">No neighbors</p> : null}
              <ul>
                {neighbors.map((item) => (
                  <li key={item.edgeId}>
                    <button type="button" onClick={() => setSelectedId(item.node.id)}>
                      <span>{item.direction === "out" ? item.label : `${item.label} · incoming`}</span>
                      <strong>{item.node.title}</strong>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="slot-hint">Select a node to read its content and neighbors.</p>
          )}
        </aside>
      </div>
    </section>
  );
}
