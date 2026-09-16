# Termitaria Concepts

> The domain language of Termitaria. Read this before [`architecture.md`](architecture.md):
> that document describes *how the system is built*; this one defines *what the things are*.
> Every concept here is backed by a frozen protobuf contract under [`proto/`](../proto).

## The big picture

Termitaria's concepts live at three different time scales:

| Scale | Concepts | Question answered |
| --- | --- | --- |
| **Configuration** (project lifetime) | Swarm, Agent, Edge, Policies | *Who is allowed to talk to whom, under what rules?* |
| **Conversation** (minutes to days) | Session, Message, Turn, SessionEvent | *What has been said, and whose turn is it?* |
| **Computation** (seconds) | Task, TaskContext, TaskDelta, TaskResult | *How does one agent produce one response?* |

Cutting across all three: **Memory** (what the colony retains) and the **Bus** (how everything moves).

```
Swarm ──1..n──► Session ──1..n──► Message
  │                                │ triggers
  │                                ▼
  │                              Task ──► TaskResult ──► MessageAppended
  │                                │
  └── MemoryPolicy ◄── reads/writes ── Memory Service + Knowledge Graph
```

---

## 1. The Colony — organization concepts

### Swarm

A configured colony of agents pointed at a problem. A swarm is the unit of
lifecycle: it is created, started, stopped as one thing (`SwarmService`).
Status: `DRAFT → STARTING → RUNNING → STOPPED`.

### SwarmSpec

The **declarative description of a swarm** — "the swarm is configuration, not code."
Users author it in YAML (validated against `schemas/swarmspec.schema.json`);
the Swarm Scheduler compiles it into the running topology. A `SwarmSpec` has a
semver `version`, because configuration is code. Contract: `swarm.v1.SwarmSpec`.

### Agent (AgentSpec)

A specialized role in the colony: `role_prompt`, a `ModelPolicy` (which model
tier it thinks with), a `tool_allowlist` (tool ACL), a `Budget`, and an optional
`MemoryPolicy` overriding the swarm default. Agents are typed and reusable —
the same "reviewer" spec can serve many swarms.

### Edge (EdgeSpec)

Permission for two agents to hold sessions, plus the `SessionPolicy` governing
those sessions. **No edge, no conversation** — the mesh is explicitly declared,
not emergent.

### TopologyPolicy

The safety valve against topology explosion: `max_sessions_total` caps live
sessions swarm-wide; `max_fanout_per_agent` caps how many peers one agent may
talk to concurrently. Agents are free to initiate conversations; the
infrastructure holds the brake. Enforced by the Session Router at `OpenSession`.

---

## 2. The Conversations — session concepts

### Session

A persistent, addressable conversation between **exactly two participants**
(MVP), where a participant is an agent or a user. Sessions are first-class
citizens: the same pair of agents may hold several concurrent sessions.
State machine: `OPEN → SUSPENDED → CLOSED`. Contract: `session.v1.Session`.

### SessionPolicy

The rules of engagement for a session: `TurnTaking` (`STRICT` alternation vs
`FREE` speech bounded by budget), `max_turns`, and `max_concurrent_sessions`
per edge. Enforced by the Policy component of the Agent Runtime.

### Message & ContentBlock

A `Message` is one utterance in a session. Its content is a list of
**ContentBlocks** — composable units: `text`, `tool_call`, `tool_result`, or
`attachment`. This is why tool use is native to the conversation model rather
than a side channel.

### SessionEvent (event sourcing)

Every change to a session is an immutable event — `SessionOpened`,
`MessageAppended`, `TurnAdvanced`, `SessionSuspended`, `SessionClosed` —
appended to the JetStream `sessions.*` stream. **The stream is the source of
truth; the `Session` state is a fold over its events.** A crashed session actor
is rebuilt by replaying its event log, which is what makes the Go control
plane stateless and horizontally scalable.

### Single-writer principle

For each session, its **session actor is the only writer** of its event stream.
Humans write via `AppendMessage`; agent output arrives only as a `TaskResult`
that the actor appends. Workers never touch `sessions.*` directly. This is what
keeps the event log race-free.

---

## 3. The Work — computation concepts

### Task

One unit of inference work: "agent X, it is your turn in session Y — think."
A `Task` carries its own `Budget`, a resolved `ModelRef`, and a
`delta_subject` to stream back on. Tasks are published to the JetStream
`tasks.*` stream and consumed by stateless Python LangGraph workers.
**The control plane never blocks on an LLM** — all inference is async through
`tasks.*`. Contract: `task.v1.Task`.

### TaskContext

Everything a worker needs, self-contained, because workers are stateless:
the conversation `window`, pre-fetched `recalled` memory items, and relevant
`graph_node_ids`. Memory recall happens *before* task dispatch, not during
inference.

### TaskDelta / TaskResult / TaskUsage

`TaskDelta` is a streaming increment (text chunk, tool call, or terminal
`usage` frame) flowing worker → Policy → Gateway → UI over core NATS —
deliberately **not persisted**; durability comes from the final `TaskResult`,
which the session actor appends as `MessageAppended`. `TaskUsage` records
tokens, cost, and the actual model used — the data behind the hackathon
model-tiering story.

### Budget

A triple constraint on one inference or one recall: `max_tokens`,
`max_cost_usd`, `deadline_ms`. Exhaustion cancels the task
(`TASK_STATUS_CANCELLED`) without corrupting the session — a cancelled turn is
just another event.

### ModelTier & ModelRef

`ModelTier` maps roles to **NVIDIA Nemotron** tiers served via **Nebius Token
Factory**: `NANO` / `SUPER` for responsiveness, `ULTRA` for deep reasoning.
The model router resolves a tier into a concrete `ModelRef` before the task is
enqueued — model names come from configuration, never hardcoded in contracts.

---

## 4. The Memory — what the colony retains

Two substrates, one API (`MemoryService` + `KnowledgeGraphService`).

### Knowledge Graph (shared, project-level)

A FalkorDB property graph that exists **independently of any chat stream**.
**Nodes** are `DOCUMENT` (user uploads), `PAPER` (agent-discovered literature),
`CLAIM` (extracted by the ingest pipeline), or `CONCEPT`. **Edges** are typed:
`CITES`, `DERIVES_FROM`, `DEPENDS_ON`, `SUPPORTS`, `CONTRADICTS`. Knowledge
compounds across sessions instead of evaporating in scrollback. Recall is by
subgraph query around seed nodes.

### Agent Memory (per-agent, hierarchical)

Each agent's own memory, organized on a **Poincaré ball** — hyperbolic space
that natively embeds tree-like structure. `MemoryKind` runs from the center
outward: `ROLE` (center) → `RULE` → `PRINCIPLE` → `EPISODE` (edge). A
`MemoryItem` carries its `poincare_radius` (smaller = more abstract) and a
`parent_id`, so clients can reassemble the memory tree (Memory Inspector).

### Episode

The sediment of a finished session: a summary, salient spans, and links to the
KG nodes it touched. Episodes are how conversations become durable knowledge;
over time, frequently-confirmed episodes distill upward into `PRINCIPLE`s.

### Recall (hierarchy-aware)

`recall(query, depth)` with a `RecallDepth` spectrum: `PRINCIPLES_ONLY`,
`EPISODES_ONLY`, or `AUTO` (default, full hierarchy-aware). Two-stage ranking:
**Qdrant generates Euclidean candidates; Poincaré distance does the final
ranking.** Recall is synchronous with a latency budget (50–200 ms) and can
degrade to pure Euclidean ranking without changing the interface
(`RecallMeta.degraded`).

### Document & ingest pipeline

A `Document` enters the pipeline: chunk → embed → LLM extracts claims and
citations → write to KG. Ingest is asynchronous (2–10 s) and never blocks
chat. Large bodies stay in object storage (`content_ref`) — they never ride
the bus.

### MemoryPolicy

Per-swarm default, per-agent override: whether to `write_episodes`,
`recall_top_k`, `recall_depth`, and `decay_half_life_days` — memories fade on
a half-life unless reinforced.

---

## 5. The Plumbing — how everything moves

### The three streams

| Stream | Carries | Persistence |
| --- | --- | --- |
| `sessions.*` | `SessionEvent`s — the session ledger | JetStream, durable, replayable |
| `tasks.*` | `Task`s / `TaskResult`s — the work queue | JetStream, durable; depth drives worker autoscaling |
| `memory.*` | async memory writes | JetStream, durable |

Streaming deltas (`TaskDelta`) ride core NATS — high-frequency, transient,
safe to drop because final state is guaranteed by `TaskResult` + `MessageAppended`.

### Envelope

The uniform shell for every bus message: `event_id` (ULID, idempotency key),
`trace_id` (user request → session → task → memory), `producer`, `schema`
(fully-qualified proto type), and `payload` (serialized bytes). Chosen over
`google.protobuf.Any` for zero cross-language type-registration cost — every
message on the bus is self-describing, which makes replay and debugging sane.

### Error

One machine-readable error contract across all planes: `code`
(`BUDGET_EXCEEDED`, `RECALL_TIMEOUT`, `POLICY_DENIED`, …), human-readable
`message`, and a `retryable` flag.

---

## One turn, end to end

1. A user's message is appended to a session (`MessageAppended` on `sessions.*`).
2. The session actor advances the turn (`TurnAdvanced`) and sees agent B speaks next.
3. The actor enqueues a `Task` with a pre-fetched `TaskContext` (window + recalled memory + KG seeds).
4. A LangGraph worker consumes it, calls Nemotron via Token Factory, streams `TaskDelta`s to the UI.
5. The worker returns a `TaskResult`; the session actor — the session's only writer — appends it as `MessageAppended`.
6. Later, when the session closes, an `Episode` is distilled into agent memory and any new sources into the Knowledge Graph.

The colony has spoken once — and remembered it.
