"use client";

import { AnimalIcon, markFor } from "../lib/animals";
import type { MeshEdge } from "../lib/types";

type Agent = { id: string; display_name: string };

export function SwarmView({
  agents,
  edges,
  presence,
  focus,
  onFocus,
}: {
  agents: Agent[];
  edges: MeshEdge[];
  presence: Record<string, string>;
  focus: string | null;
  onFocus: (sessionId: string | null) => void;
}) {
  const size = 520;
  const center = size / 2;
  const radius = 180;
  const pos = new Map<string, { x: number; y: number }>();
  agents.forEach((agent, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(agents.length, 1) - Math.PI / 2;
    pos.set(agent.id, {
      x: center + radius * Math.cos(angle),
      y: center + radius * Math.sin(angle),
    });
  });

  return (
    <main className="blank swarm" aria-label="Swarm">
      {agents.length === 0 ? (
        <div className="empty">
          <strong>Swarm</strong>
          <p>这个项目还没有 agent</p>
        </div>
      ) : (
        <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Agent mesh">
          {edges.map((edge) => {
            const a = pos.get(edge.from);
            const b = pos.get(edge.to);
            if (!a || !b) return null;
            const active = focus === edge.id;
            return (
              <line
                key={edge.id}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                className={`mesh-edge${active ? " active" : ""}`}
                onClick={() => onFocus(active ? null : edge.id)}
              />
            );
          })}
          {agents.map((agent) => {
            const at = pos.get(agent.id);
            if (!at) return null;
            const state = presence[agent.id] ?? "offline";
            const mark = markFor(agent.id, agents.map((item) => item.id));
            const label = state === "thinking" ? "思考中" : state === "online" ? "在线" : "离线";
            return (
              <g key={agent.id} transform={`translate(${at.x} ${at.y})`}>
                <title>{`${agent.display_name} ${label}`}</title>
                <circle r="28" fill={mark.color} />
                <g transform="translate(-12 -12)" color="white">
                  <AnimalIcon path={mark.path} />
                </g>
                <circle className={`mesh-presence ${state}`} cx="20" cy="20" r="6" />
                <text textAnchor="middle" dy="52" className="mesh-name">
                  {agent.display_name}
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </main>
  );
}
