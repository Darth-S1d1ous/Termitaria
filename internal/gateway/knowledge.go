package gateway

import (
	"context"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	graphv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/graph/v1"
	memoryv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/memory/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

var (
	graphOnce sync.Once
	graphRPC  graphv1.KnowledgeGraphServiceClient
)

func graphClient() graphv1.KnowledgeGraphServiceClient {
	graphOnce.Do(func() {
		addr := os.Getenv("GRAPH_GRPC_ADDR")
		if addr == "" {
			addr = "localhost:50051"
		}
		conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
		if err != nil {
			log.Printf("graph client: %v", err)
			return
		}
		graphRPC = graphv1.NewKnowledgeGraphServiceClient(conn)
	})
	return graphRPC
}

type projectGraphJSON struct {
	ProjectID string          `json:"project_id"`
	Nodes     []graphNodeJSON `json:"nodes"`
	Edges     []graphEdgeJSON `json:"edges"`
}

type graphNodeJSON struct {
	ID    string `json:"id"`
	Kind  string `json:"kind"`
	Title string `json:"title"`
	Text  string `json:"text"`
}

type graphEdgeJSON struct {
	ID       string `json:"id"`
	Source   string `json:"source"`
	Target   string `json:"target"`
	Relation string `json:"relation"`
	Fact     string `json:"fact,omitempty"`
}

// projectGraph 把这个 project 的知识图交给页面。读的是 KnowledgeGraphService，不是 agent 记忆。
func (s *Server) projectGraph(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if s.project(id) == nil {
		writeError(w, http.StatusNotFound, "project not found")
		return
	}
	client := graphClient()
	if client == nil {
		writeError(w, http.StatusBadGateway, "knowledge graph is not reachable")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	resp, err := client.QuerySubgraph(ctx, &graphv1.QuerySubgraphRequest{
		SeedNodeIds: []string{id + "/root"},
		Depth:       4,
		MaxNodes:    200,
	})
	if err != nil {
		log.Printf("project graph %s: %v", id, err)
		writeError(w, http.StatusBadGateway, "knowledge graph is not reachable")
		return
	}
	writeJSON(w, http.StatusOK, graphView(id, resp))
}

func graphView(projectID string, resp *graphv1.QuerySubgraphResponse) projectGraphJSON {
	out := projectGraphJSON{ProjectID: projectID, Nodes: []graphNodeJSON{}, Edges: []graphEdgeJSON{}}
	if resp == nil {
		return out
	}
	for _, node := range resp.GetNodes() {
		out.Nodes = append(out.Nodes, graphNodeJSON{
			ID:    node.GetNodeId(),
			Kind:  nodeKindName(node.GetKind()),
			Title: node.GetTitle(),
			Text:  nodeText(node),
		})
	}
	for _, edge := range resp.GetEdges() {
		out.Edges = append(out.Edges, graphEdgeJSON{
			ID:       edge.GetEdgeId(),
			Source:   edge.GetFromNodeId(),
			Target:   edge.GetToNodeId(),
			Relation: edge.GetRelation(),
			Fact:     edge.GetFact(),
		})
	}
	return out
}

func nodeText(node *graphv1.Node) string {
	props := node.GetProps()
	if props == nil {
		return ""
	}
	text, _ := props.AsMap()["text"].(string)
	return text
}

func nodeKindName(kind graphv1.NodeKind) string {
	switch kind {
	case graphv1.NodeKind_NODE_KIND_DOCUMENT:
		return "DOCUMENT"
	case graphv1.NodeKind_NODE_KIND_PAPER:
		return "PAPER"
	case graphv1.NodeKind_NODE_KIND_CLAIM:
		return "CLAIM"
	case graphv1.NodeKind_NODE_KIND_CONCEPT:
		return "CONCEPT"
	default:
		return "NODE"
	}
}

// publishDocument 把一份材料送进这个 project 的知识图。agent 记忆不收这份正文。
func (s *Server) publishDocument(ctx context.Context, projectID, local, title, body, mime string) {
	if s.bus == nil || projectID == "" || local == "" {
		return
	}
	text := strings.TrimSpace(body)
	if text == "" {
		text = strings.TrimSpace(title)
	}
	if text == "" {
		return
	}
	doc := &memoryv1.Document{
		DocId:      projectID + "/" + local,
		Title:      title,
		ContentRef: text,
		MimeType:   mime,
		UploaderId: "user",
	}
	if _, err := s.bus.Publish(ctx, bus.MemoryWriteDocument, "gateway", projectID, &memoryv1.WriteDocumentRequest{Document: doc}); err != nil {
		log.Printf("memory.write.document %s: %v", doc.DocId, err)
	}
}
