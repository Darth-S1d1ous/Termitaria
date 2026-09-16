### What Termitaria is

Imagine the following workflows:

> In a fund, shipping a single model requires: a researcher to derive it, a developer to code it, a data engineer to feed it, a reviewer to check it, an infrastructure engineer to run it, and a risk officer to sign it off.

> In a university lab, the completion of a good research project depends on: previous paper collection, idea formulation, mathematical deduction, experiment design and execution, A/B tests and validation.

These workflows look different on the surface, but they share the same structure: **complex intellectual work is never done by one mind — it is done by a coordinated group of specialized roles, communicating over shared context and accumulated knowledge.**

Today's AI tools mostly give you a single, monolithic assistant. One chat window, one model, one memory. That works for one-shot questions, but it breaks down for the workflows above:

- A single agent cannot simultaneously be a rigorous researcher, a fast coder, a skeptical reviewer, and a cautious risk officer — these roles demand different prompts, different rules, and different memories.
- Real collaboration is not a linear chat log. It is a *mesh* of conversations: the researcher debates with the reviewer, the data engineer negotiates with the infrastructure engineer, and not every conversation involves everyone.
- Knowledge produced along the way — documents, papers, intermediate results — is lost in scrollback instead of becoming a durable, queryable asset.

**Termitaria is a multi-agent harness built around this observation.** The name comes from *termitaria* — termite mounds: complex, adaptive structures built not by a single brilliant architect, but by a swarm of simple agents coordinating through shared signals. Termitaria applies the same principle to intellectual work.

### How it works

Termitaria is organized into three layers (see `images/architecture-1.png`):

1. **Client Layer** — a Discord-like chat interface where users talk with agents. Channels and threads host conversations between humans and the swarm, making multi-party discussion a first-class citizen rather than an afterthought.

2. **Agent Mesh Layer** — beneath the UI, agents talk to *each other*. Any agent can open one or more sessions with any other agent, forming a peer-to-peer mesh rather than a hub-and-spoke hierarchy. A "reviewer" agent can challenge a "researcher" agent directly; a "data engineer" agent can be pulled into a session only when needed.

3. **Project Memory Layer** — the shared substrate every agent reads from and writes to:
   - **Knowledge Graph**: every document a user adds and every important paper an agent finds becomes a node, with edges capturing citations, derivations, and dependencies. Knowledge compounds across sessions instead of evaporating.
   - **Agent Memory**: each agent carries its own role, rules, and a hierarchical memory organized on a Poincaré ball — hyperbolic geometry that naturally embeds tree-like structures, so an agent's memory is organized from broad principles down to fine-grained episodes.

### Customization

Termitaria is designed to be **highly customizable**. Users define their own agent swarm — roles, rules, topologies, and memory policies — through configuration, then point it at a problem. The same harness can be assembled into:

- **Market research** — a swarm of collectors, analysts, and fact-checkers sweeping a domain.
- **Investment analysis** — researchers, quants, reviewers, and a risk officer debating a thesis before it reaches the user.
- **Autoresearch** — literature collectors, idea generators, and experiment designers running a lab workflow end-to-end.

The goal is not to build one smarter agent, but to give users the tools to build *smarter organizations* of agents.
