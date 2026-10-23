import type { Seed } from "./seeds";

export type GraphKind = "document" | "paper" | "claim" | "concept";

export type GraphNode = {
  id: string;
  kind: GraphKind;
  title: string;
  content: string;
};

export type GraphEdge = {
  id: string;
  from: string;
  to: string;
  label: string;
};

export type GraphNeighbor = {
  edgeId: string;
  label: string;
  direction: "out" | "in";
  node: GraphNode;
};

const BASE_NODES: GraphNode[] = [
  {
    id: "gn_doc",
    kind: "document",
    title: "Swarm memory notes",
    content: "Uploaded project notes. The graph service is still a stub, so this document is canned data.",
  },
  {
    id: "gn_paper",
    kind: "paper",
    title: "Retrieval Augmented Generation",
    content: "Lewis et al., 2020. Answers should be grounded in retrieved passages.",
  },
  {
    id: "gn_claim",
    kind: "claim",
    title: "Retrieved passages should be cited in the answer",
    content: "Derived from the retrieval paper and the project notes.",
  },
  {
    id: "gn_claim_contra",
    kind: "claim",
    title: "The model can answer without opening the source",
    content: "A competing claim. It contradicts the citation claim.",
  },
  {
    id: "gn_concept",
    kind: "concept",
    title: "Citation",
    content: "A link from a sentence back to the passage it came from.",
  },
];

const BASE_EDGES: GraphEdge[] = [
  { id: "ge_cites", from: "gn_doc", to: "gn_paper", label: "cites" },
  { id: "ge_derives", from: "gn_claim", to: "gn_paper", label: "derives from" },
  { id: "ge_depends", from: "gn_paper", to: "gn_concept", label: "depends on" },
  { id: "ge_supports", from: "gn_claim", to: "gn_concept", label: "supports" },
  { id: "ge_contra", from: "gn_claim_contra", to: "gn_claim", label: "contradicts" },
];

export function stubGraph(seeds: Seed[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes = BASE_NODES.slice();
  const edges = BASE_EDGES.slice();
  for (const seed of seeds) {
    const id = `seed_${seed.id}`;
    nodes.push({
      id,
      kind: "document",
      title: seed.title,
      content: seed.body || seed.title,
    });
    edges.push({ id: `ge_${id}`, from: "gn_doc", to: id, label: "includes" });
  }
  return { nodes, edges };
}

export function neighborsOf(nodeId: string, nodes: GraphNode[], edges: GraphEdge[]): GraphNeighbor[] {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const out: GraphNeighbor[] = [];
  for (const edge of edges) {
    if (edge.from === nodeId) {
      const node = byId.get(edge.to);
      if (node) out.push({ edgeId: edge.id, label: edge.label, direction: "out", node });
    } else if (edge.to === nodeId) {
      const node = byId.get(edge.from);
      if (node) out.push({ edgeId: edge.id, label: edge.label, direction: "in", node });
    }
  }
  return out;
}
