# Termitaria

> **Command on the surface. Agents work in the tunnels. The colony remembers.**

Built for the **Nebius × NVIDIA Global AI Hackathon** — Best Apps & Agents track.

Termitaria is a **multi-agent harness that scales and memorizes** — it pushes a project forward like an established group, not a single assistant. The name comes from *termitaria* (termite mounds): complex, adaptive structures built not by one brilliant architect, but by a swarm of simple agents coordinating through shared signals. Termitaria applies the same principle to intellectual work.

## Why

Complex intellectual work — shipping a fund model, running a lab project — is never done by one mind. It is done by a coordinated group of specialized roles communicating over shared context and accumulated knowledge. Today's AI tools give you one chat window, one model, one memory. That breaks down when a researcher must debate a reviewer, a data engineer must negotiate with infra, and the knowledge produced along the way evaporates in scrollback.

Termitaria gives you not one smarter agent, but the tools to build **smarter organizations of agents**.

## Two Core Innovations

### 1. Agent Swarm — a mesh, not a chat log

- **Sessions are first-class citizens**: any agent can open *one or more* concurrent, addressable, persistent sessions with any other agent — a peer-to-peer mesh, not a hub-and-spoke hierarchy or a linear transcript.
- **Declarative `SwarmSpec`**: roles, rules, topology, and memory policies are compiled from configuration into a running colony. The swarm is config, not code — the same harness reassembles into market research, investment analysis, or autoresearch in minutes.
- **Go control plane built for scale**: supervisor-per-agent fault isolation, one actor goroutine per session, policy enforcement (turn-taking, budgets, tool ACLs), all decoupled over NATS JetStream streams (`sessions.*` / `tasks.*` / `memory.*`) — persistent, replayable, horizontally scalable.

### 2. Memory Layer — the colony remembers

- **Knowledge Graph** (Graphiti on FalkorDB): one graph per project. Graphiti reads document text and extracts entities. Each edge has the name Graphiti chose and one fact sentence. Every agent in the project can read it. The graph is not split per agent. Install the library with `uv sync --extra memory` (`graphiti-core`).
- **Agent memory**: one store per agent (role, rules, principles, episodes). A recall returns only that agent's items. It does not return another agent's memory, and it does not return graph nodes. Qdrant stores that agent's vectors. Postgres stores the items.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Client Layer        Next.js · Discord-like chat · swarm view │
├─────────────────────────────────────────────────────────────┤
│  Control Plane (Go)  Gateway · Swarm API · Scheduler · Router │
│  Agent Mesh   (Go)   Supervisor · Session actors · Policy     │
│  Bus                 NATS JetStream (sessions / tasks / memory)│
├─────────────────────────────────────────────────────────────┤
│  Intelligence (Py)   LangGraph workers · LLM client           │
│  Memory       (Py)   Knowledge Graph · Agent Memory           │
└─────────────────────────────────────────────────────────────┘
```

Go control plane for scheduling, sessions, and the gateway; Python intelligence plane for inference, memory, and the knowledge graph. The two sides communicate only via messages/RPC — PyTorch never enters the cluster's control processes.

## Stack

| Layer | Technology |
| --- | --- |
| LLM serving | **Nebius Token Factory** (OpenAI-compatible), model routing across **NVIDIA Nemotron** tiers — Super for daily interaction, Ultra for deep reasoning |
| Control plane | Go · NATS JetStream |
| Intelligence plane | Python · LangGraph |
| Memory | Graphiti on FalkorDB, one graph per project · Postgres + Qdrant, one store per agent |
| Client | Next.js 15 · React · shadcn/ui · WebSocket streaming |

## Status

MVP in active development. See [`docs/concepts.md`](docs/concepts.md) for the domain language, [`docs/architecture.md`](docs/architecture.md) for the L1 architecture, and [`docs/background.md`](docs/background.md) for the motivation. The two memory stores are specified in [`docs/concepts.md`](docs/concepts.md) §4.