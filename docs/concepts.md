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
  └── MemoryPolicy ◄── this agent's Memory Service · this project's Knowledge Graph
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

## 4. The Memory — two stores, two services

A project keeps two stores. They do not share records, and a recall on one
store does not return rows from the other.

| Store | Scope | Service | What a read returns |
| --- | --- | --- | --- |
| Knowledge Graph | one graph per project | `KnowledgeGraphService` | nodes and edges of **this** project |
| Agent Memory | one store per agent | `MemoryService` | items of **this** agent |

A project is one swarm. The graph is keyed by that project. Agent memory is
keyed by `agent_id`. Agent A cannot read agent B's store. Neither store is a
view of the other.

### Knowledge Graph (one graph per project)

A FalkorDB property graph. It exists for the project, independently of any
chat stream and independently of any agent. Every agent in the project may
read it. Agents in another project may not.

**Nodes** are `DOCUMENT` (a file a user uploaded), `PAPER` (literature an
agent found), `CLAIM` (a statement the ingest pipeline extracted), or
`CONCEPT`. **Edges** are `CITES`, `DERIVES_FROM`, `DEPENDS_ON`, `SUPPORTS`,
`CONTRADICTS`. File details such as author, year, and abstract sit in the
node's `props`. The file body stays in object storage (`content_ref`) and
does not ride the bus.

Reads are subgraph queries around seed nodes (`QuerySubgraph`). The only
writer is the ingest pipeline. The worker does not write the graph during
inference.

### Agent Memory (one store per agent)

`MemoryService` reads and writes a single agent's store. `MemoryKind` is
`ROLE`, `RULE`, `PRINCIPLE`, or `EPISODE`. A `MemoryItem` carries the text,
a recall `score`, and the session it came from.

Postgres holds the items. Qdrant holds that agent's episode vectors. A
recall names one `agent_id` and returns only that agent's items. It does
not return another agent's items, and it does not return Knowledge Graph
nodes.

A `PRINCIPLE` is a promotion inside this same store. It is not a graph node.

### Episode

The sediment of a finished session, written when the session closes. Each
participant agent gets its own episode in its own store. The episode has a
summary and salient spans. It may record the ids of project-graph nodes the
session touched. Those ids are references. The episode text stays in the
agent's store, and the nodes stay in the project graph.

### Recall

`MemoryService.Recall` takes `agent_id`, `query`, and `depth`. `RecallDepth`
is `PRINCIPLES_ONLY`, `EPISODES_ONLY`, or `AUTO` (default, no kind filter).
The service then truncates to `top_k`. Recall is synchronous, with a latency
budget of 50–200 ms. The Go dispatcher calls it before enqueue and puts the
items on `TaskContext`. The worker reads that context and does not call
`MemoryService` itself. When the backend is unavailable, the service returns
canned items and sets `RecallMeta.degraded`.

`KnowledgeGraphService.QuerySubgraph` is a different call. The dispatcher
collects seed node ids for **this project** and puts them on
`TaskContext.graph_node_ids`. Those ids are not agent-memory items.

### Document and the ingest pipeline

A `Document` is published on `memory.write.document`. The Knowledge Graph
ingest pipeline consumes it: chunk, embed, extract claims and citations,
then upsert nodes and edges into **this project's** graph. Ingest is
asynchronous (2–10 s) and does not block chat. The document is not written
into any agent's memory.

### MemoryPolicy

Per-swarm default, per-agent override, applied only to agent memory:
`write_episodes`, `recall_top_k`, `recall_depth`, and
`decay_half_life_days`. The policy does not merge the two stores and does
not let one agent read another.

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
3. Before enqueue, the dispatcher prefetches this agent's memory and this project's KG seeds into `TaskContext` (window + recalled items + `graph_node_ids`).
4. A LangGraph worker consumes it, calls Nemotron via Token Factory, streams `TaskDelta`s to the UI.
5. The worker returns a `TaskResult`; the session actor — the session's only writer — appends it as `MessageAppended`.
6. When the session closes, each participant receives its own `Episode` in its own agent store. A new document or paper is a separate write: `memory.write.document` into this project's Knowledge Graph. The episode is not inserted as a graph node.

The colony has spoken once — and remembered it.
