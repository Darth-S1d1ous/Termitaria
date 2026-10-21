"use client";

import { useEffect, useState } from "react";
import { loadMemoryGraph, type MemoryEdge, type MemoryGraph, type MemoryNode } from "../lib/api";

const ENTITY_COLOR = "#8b7ec8";

type Agent = { id: string; display_name: string };

function pickAgent(agents: Agent[], current: string) {
  if (current && agents.some((agent) => agent.id === current)) return current;
  if (agents.some((agent) => agent.id === "pi")) return "pi";
  return agents[0]?.id ?? "";
}

function neighborsOf(nodeId: string, nodes: MemoryNode[], edges: MemoryEdge[]) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const out: { edge: MemoryEdge; direction: "out" | "in"; node: MemoryNode }[] = [];
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

export function KnowledgeGraph({
  agents,
  onChats,
}: {
  agents: Agent[];
  onChats: () => void;
}) {
  const [agentId, setAgentId] = useState(() => pickAgent(agents, ""));
  const [graph, setGraph] = useState<MemoryGraph | null>(null);
  const [status, setStatus] = useState("Loading the graph.");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    const next = pickAgent(agents, agentId);
    if (next !== agentId) setAgentId(next);
  }, [agents, agentId]);

  useEffect(() => {
    if (!agentId) {
      setGraph(null);
      setStatus("This project has no agent.");
      return;
    }
    let cancelled = false;
    setStatus(`Loading the graph for ${agentId}.`);
    loadMemoryGraph(agentId)
      .then((payload) => {
        if (cancelled) return;
        setGraph(payload);
        setSelectedId(null);
        setStatus("");
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setGraph(null);
        setStatus(err.message || "The memory graph is not reachable.");
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const selected = nodes.find((node) => node.id === selectedId) ?? null;
  const neighbors = selected ? neighborsOf(selected.id, nodes, edges) : [];
  const neighborIds = new Set(neighbors.map((item) => item.node.id));
  const caption = describe(agentId, graph, status);

  const size = 640;
  const center = size / 2;
  const radius = 210;
  const pos = new Map<string, { x: number; y: number }>();
  nodes.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
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
          <p>{caption}</p>
          {agents.length > 0 ? (
            <label className="kg-agent">
              Agent
              <select value={agentId} onChange={(event) => setAgentId(event.target.value)}>
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.display_name || agent.id}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
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
          {edges.map((edge) => {
            const a = pos.get(edge.source);
            const b = pos.get(edge.target);
            if (!a || !b) return null;
            const hot = selected && (edge.source === selected.id || edge.target === selected.id);
            const expired = edge.expired ? " expired" : "";
            return (
              <line
                key={edge.id}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                className={`kg-edge${hot ? " hot" : ""}${expired}`}
              />
            );
          })}
          {nodes.map((node) => {
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
                aria-label={node.name}
                onClick={() => setSelectedId(node.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") setSelectedId(node.id);
                }}
              >
                <circle r={on ? 28 : 22} fill={ENTITY_COLOR} />
                <text textAnchor="middle" dy="44" className="kg-label">
                  {node.name.length > 28 ? `${node.name.slice(0, 26)}…` : node.name}
                </text>
              </g>
            );
          })}
        </svg>
        <aside className="kg-detail" aria-label="Selected node">
          {selected ? (
            <>
              <span className="seed-kind">entity</span>
              <h2>{selected.name}</h2>
              <p>{selected.summary || "No summary."}</p>
              <h3>Neighbors</h3>
              {neighbors.length === 0 ? <p className="slot-hint">No neighbors</p> : null}
              <ul>
                {neighbors.map((item) => (
                  <li key={item.edge.id + item.direction}>
                    <button type="button" onClick={() => setSelectedId(item.node.id)}>
                      <span>
                        {item.direction === "out" ? item.edge.fact : `${item.edge.fact} · incoming`}
                        {item.edge.expired ? " · expired" : ""}
                      </span>
                      <strong>{item.node.name}</strong>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="slot-hint">Select a node to read its summary and facts.</p>
          )}
        </aside>
      </div>
    </section>
  );
}

function describe(agentId: string, graph: MemoryGraph | null, status: string) {
  if (status) return status;
  if (!graph) return status;
  if (!graph.backend) return "The memory graph is not reachable.";
  if (graph.backend !== "graphiti") {
    return "This view reads the Graphiti graph. Set MEMORY_BACKEND=graphiti and restart.";
  }
  if (graph.nodes.length === 0) {
    return `No entities have been stored for ${agentId} yet.`;
  }
  return `Graphiti graph for ${agentId}. Click a node to read its summary and facts.`;
}
