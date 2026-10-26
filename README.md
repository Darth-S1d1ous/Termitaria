# Termitaria

**Command on the surface. Agents work in the tunnels. The colony remembers.**

Termitaria is a multi-agent harness for work that usually takes a team: a fund shipping a model, a lab turning a library into an experiment plan, a desk writing a sourced market brief. You describe the team in a config file. An orchestrator splits the job, specialists do their part, and the project keeps what they learned.

Built for the [Nebius × NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/), **Best Apps & Agents** track. Inference runs on [Nebius Token Factory](https://tokenfactory.nebius.com/). The colony thinks with **NVIDIA Nemotron 3** open models.

## The job

A single chat window can answer a question. It cannot be the researcher, the reviewer, and the person who remembers last week's papers at the same time.

Those jobs need different instructions, different tools, and different memories. They also need a place where the papers, claims, and intermediate notes survive after the chat scrolls away.

Termitaria is that place. The name comes from *termitaria*, the mounds termites build. No single termite holds the blueprint. The colony does, through shared signals. Termitaria applies that idea to intellectual work.

## What you do

1. Open the app and create a project.
2. Pick a team template, or paste your own `SwarmSpec`.
3. Optionally leave notes, links, or files with the project.
4. Send a message. The orchestrator plans the work and delegates to specialists.
5. Read the thread as replies stream in. Open the swarm view to see who is talking.

Two teams ship in [`examples/swarms/`](examples/swarms/):

| Template | Team | What it produces |
| --- | --- | --- |
| **Copper Desk** | Desk lead, market researcher, reviewer | A sourced trading brief and a deck. The reviewer drops any number the tools did not return. |
| **Autoresearch** | Principal investigator, literature collector, idea generator, experiment designer, skeptical reviewer | A reviewed hypothesis and an experiment plan, written against the project's knowledge graph. |

The same harness runs both. Roles, tools, budgets, and memory policy live in the YAML, not in a new codebase.

## How a turn works

1. Your message lands in a session.
2. The session actor decides who speaks next and publishes one task.
3. Before the task runs, the dispatcher loads that agent's own memory and seeds from this project's knowledge graph.
4. A Python worker calls Nemotron on Token Factory and streams the reply to the UI.
5. The session actor appends the finished reply. Workers never write the session log themselves.
6. When the session closes, each participant stores its own episode. A document written into the project goes to the knowledge graph, not into anyone's private memory.

## Two ideas

### The team is a spec

A `SwarmSpec` names the orchestrator, the specialists, the tools each one may call, and the token budget for a turn. The Go control plane compiles that file into a running colony: one supervisor per agent, one actor per session, policy checks for turns and budgets. Sessions are durable NATS JetStream logs, so a crashed actor rebuilds itself by replaying events.

### The colony has two memories

They do not share rows.

| Store | Who can read it | What it holds |
| --- | --- | --- |
| **Knowledge graph** | Every agent in this project | One [Graphiti](https://github.com/getzep/graphiti) graph in FalkorDB. Graphiti reads document text and writes entities and fact edges. |
| **Agent memory** | That agent only | Role, rules, principles, and episodes. Postgres stores the items. Qdrant stores that agent's vectors. |

Agent A cannot recall agent B's notes. A recall on agent memory does not return graph nodes. The graph is the project's library. Agent memory is the specialist's notebook.

## NVIDIA Nemotron on Nebius Token Factory

Every model call from the worker goes out as an OpenAI-compatible chat completion. For this hackathon, that call hits **Nebius Token Factory**, and the model is an **NVIDIA Nemotron 3** open model.

| Tier | Model id on Token Factory | Use it when |
| --- | --- | --- |
| Super | `nvidia/nemotron-3-super-120b-a12b` | Everyday turns: collecting, drafting, delegating. Fast enough to keep the thread moving, cheaper than the flagship. |
| Ultra | `nvidia/Nemotron-3-Ultra-550b-a55b` | Hard reasoning: a skeptical review, a synthesis the lead must stand behind. 550B hybrid MoE, 55B active, tool calling. |

`SwarmSpec` records which tier a role should use. The Autoresearch reviewer is marked Ultra. The other specialists are marked Super. The worker resolves the live model from `MODEL_TIER_SUPER` (see [Run it](#run-it)). Point that variable at Super for the fast colony, or at Ultra when you want the flagship on every turn. Both models are served from Token Factory `us-central1`.

Token Factory is the only inference path the colony needs. The Go processes never load a model. They publish a task and stream tokens back. That split is what lets a long review sit on Ultra while the rest of the colony stays on Super.

Without an API key, the worker still runs. It returns a stub stream so you can click through the UI. The stub does not call Token Factory.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Client              Next.js · chat · swarm view            │
├─────────────────────────────────────────────────────────────┤
│  Control plane (Go)  Gateway · Swarm API · session actors   │
│  Bus                 NATS JetStream                         │
│                      sessions.*  tasks.*  memory.*          │
├─────────────────────────────────────────────────────────────┤
│  Workers (Python)    LangGraph · Token Factory · Nemotron   │
│  Memory (Python)     Knowledge graph · per-agent memory     │
└─────────────────────────────────────────────────────────────┘
```

| Layer | Technology |
| --- | --- |
| Models | Nebius Token Factory, NVIDIA Nemotron 3 Super and Ultra |
| Control plane | Go, NATS JetStream |
| Workers | Python, LangGraph |
| Knowledge graph | Graphiti on FalkorDB, one graph per project |
| Agent memory | Postgres + Qdrant, one store per agent |
| Client | Next.js 15, React 19 |

Domain language: [`docs/concepts.md`](docs/concepts.md). Component map: [`docs/architecture.md`](docs/architecture.md).

## Run it

You need Docker, Go 1.26+, Python 3.11+, [uv](https://docs.astral.sh/uv/), and Node.js 20+. The dev script is bash and uses `lsof` and `curl`.

```bash
git clone https://github.com/Darth-S1d1ous/Termitaria.git
cd Termitaria

uv sync --extra memory
npm install --prefix web
```

Create a `.env` in the repo root. `make up` loads it.

```bash
# Nebius Token Factory + NVIDIA Nemotron 3
MODEL_PROVIDER=nebius-token-factory
NEBIUS_API_KEY=your-token-factory-key
NEBIUS_BASE_URL=https://api.tokenfactory.us-central1.nebius.com/v1
MODEL_TIER_SUPER=nvidia/nemotron-3-super-120b-a12b

# Optional. Copper Desk and Autoresearch call web_search.
# SEARCH_API_KEY is a Brave Search key.
SEARCH_API_KEY=
```

Leave `NVIDIA_MODEL` unset. If it is set, the control plane sends that model name instead of the Token Factory default above.

Start the stack:

```bash
make up
```

`make up` starts NATS, FalkorDB, Postgres, and Qdrant, then the Go runtime, the worker, the memory service, the graph service, and the web app. When it prints the URLs, open:

- App: http://127.0.0.1:3000
- Gateway health: http://127.0.0.1:8787/api/health

In the app, create a project, choose **copper-desk** or **autoresearch**, and send a message. Copper Desk is the shorter demo: ask for last week's copper move and a reviewed brief.

Stop the app with Ctrl-C. That stops the processes `make up` started. The databases keep running until you stop them:

```bash
docker compose down
```

Ports: web `3000`, gateway `8787`, NATS `4222` (monitor `8222`), Postgres `5432`, Qdrant `6333`, FalkorDB `6379` and browser `3001`.

## Layout

```
cmd/runtime/          Go gateway, scheduler, session actors
services/worker/      LangGraph worker and Token Factory client
services/memory/      Per-agent memory
services/graph/       Project knowledge graph
examples/swarms/      SwarmSpec templates
web/                  Next.js client
proto/                Frozen contracts
docs/                 Concepts, architecture, requirements
```

## Status

This is the MVP. One workspace. A project is a swarm. You can define a team, talk to it, and watch it work. Memory services start with the stack and fall back to canned recall if Postgres, Qdrant, or FalkorDB is unreachable.

Deeper design notes live in [`docs/background.md`](docs/background.md), [`docs/concepts.md`](docs/concepts.md), and [`docs/architecture.md`](docs/architecture.md).
