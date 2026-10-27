package gateway

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
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
	view := graphView(id, resp)
	attachUploads(&view, id, s.projectSeeds(id))
	mergeDocuments(&view)
	writeJSON(w, http.StatusOK, view)
}

// uploadKnowledge 把用户刚传的文件写进这个 project，并送进知识图。任务进行中也能读到。
func (s *Server) uploadKnowledge(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	raw, err := io.ReadAll(io.LimitReader(r.Body, 200_001))
	if err != nil {
		writeError(w, http.StatusBadRequest, "file is too large")
		return
	}
	if len(raw) > 200_000 {
		writeError(w, http.StatusBadRequest, "file is too large")
		return
	}
	var body struct {
		Title string `json:"title"`
		Text  string `json:"text"`
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad json")
		return
	}
	text := strings.TrimSpace(body.Text)
	if text == "" || strings.Contains(body.Text, "\x00") {
		writeError(w, http.StatusBadRequest, "file is empty")
		return
	}
	truncated := false
	runes := []rune(text)
	if len(runes) > 20000 {
		text = string(runes[:20000])
		truncated = true
	}
	title := strings.TrimSpace(filepath.Base(strings.TrimSpace(body.Title)))
	if title == "" || title == "." || title == ".." {
		title = "upload.txt"
	}
	if len([]rune(title)) > 120 {
		title = string([]rune(title)[:120])
	}
	seed := seedJSON{ID: "u_" + randHex(4), Kind: "file", Title: title, Body: text}
	s.mu.Lock()
	found := false
	for _, p := range s.projects {
		if p.ID != id {
			continue
		}
		found = true
		if len(p.Seeds) >= 60 {
			s.mu.Unlock()
			writeError(w, http.StatusBadRequest, "this project has too many files")
			return
		}
		p.Seeds = append(p.Seeds, seed)
		if err := s.save(); err != nil {
			s.mu.Unlock()
			writeError(w, http.StatusBadGateway, err.Error())
			return
		}
		break
	}
	s.mu.Unlock()
	if !found {
		writeError(w, http.StatusNotFound, "project not found")
		return
	}
	s.publishDocument(r.Context(), id, seed.ID, seed.Title, seed.Body, "text/plain")
	s.broadcast(id, map[string]any{"type": "knowledge"})
	writeJSON(w, http.StatusCreated, map[string]any{
		"id":        seed.ID,
		"node_id":   id + "/" + seed.ID,
		"title":     seed.Title,
		"truncated": truncated,
	})
}

func (s *Server) projectSeeds(id string) []seedJSON {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, p := range s.projects {
		if p.ID == id {
			return append([]seedJSON(nil), p.Seeds...)
		}
	}
	return nil
}

// 创建时保存的材料（k_）和后来上传的文件（u_）先作为 document 出现。
// Graphiti 抽出的实体和关系随后从查询里一起回来。
func attachUploads(view *projectGraphJSON, projectID string, seeds []seedJSON) {
	if view == nil {
		return
	}
	have := map[string]bool{}
	for _, node := range view.Nodes {
		have[node.ID] = true
	}
	for _, seed := range seeds {
		if (!strings.HasPrefix(seed.ID, "k_") && !strings.HasPrefix(seed.ID, "u_")) || strings.TrimSpace(seed.Body) == "" {
			continue
		}
		id := projectID + "/" + seed.ID
		if have[id] {
			continue
		}
		have[id] = true
		view.Nodes = append(view.Nodes, graphNodeJSON{
			ID:    id,
			Kind:  "DOCUMENT",
			Title: seed.Title,
			Text:  seed.Body,
		})
	}
}

// mergeDocuments keeps one node per document title. Graphiti can return an
// Episodic document and a Concept with the same file name, and an upload can
// add a third node. The node with the longest text wins and links move to it.
func mergeDocuments(view *projectGraphJSON) {
	if view == nil || len(view.Nodes) < 2 {
		return
	}
	winners := map[string]int{}
	for i, node := range view.Nodes {
		if node.Kind != "DOCUMENT" {
			continue
		}
		key := documentKey(node.Title)
		if key == "" {
			continue
		}
		current, ok := winners[key]
		if !ok || len(node.Text) > len(view.Nodes[current].Text) {
			winners[key] = i
		}
	}
	if len(winners) == 0 {
		return
	}
	remap := map[string]string{}
	keep := make([]bool, len(view.Nodes))
	for i := range keep {
		keep[i] = true
	}
	for i, node := range view.Nodes {
		if node.Kind != "DOCUMENT" && node.Kind != "CONCEPT" {
			continue
		}
		winner, ok := winners[documentKey(node.Title)]
		if !ok || i == winner {
			continue
		}
		remap[node.ID] = view.Nodes[winner].ID
		keep[i] = false
	}
	if len(remap) == 0 {
		return
	}
	nodes := make([]graphNodeJSON, 0, len(view.Nodes)-len(remap))
	for i, node := range view.Nodes {
		if keep[i] {
			nodes = append(nodes, node)
		}
	}
	edges := make([]graphEdgeJSON, 0, len(view.Edges))
	seen := map[string]bool{}
	for _, edge := range view.Edges {
		if to, ok := remap[edge.Source]; ok {
			edge.Source = to
		}
		if to, ok := remap[edge.Target]; ok {
			edge.Target = to
		}
		if edge.Source == edge.Target {
			continue
		}
		key := edge.Source + "\x00" + edge.Target + "\x00" + edge.Relation + "\x00" + edge.Fact
		if seen[key] {
			continue
		}
		seen[key] = true
		edges = append(edges, edge)
	}
	view.Nodes = nodes
	view.Edges = edges
}

func documentKey(title string) string {
	return strings.ToLower(strings.Join(strings.Fields(title), " "))
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
