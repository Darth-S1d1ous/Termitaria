package dispatcher

import (
	"context"
	"fmt"
	"log"
	"os"
	"strings"
	"sync"
	"time"

	graphv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/graph/v1"
	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

var (
	graphOnce sync.Once
	graphConn *grpc.ClientConn
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
		graphConn = conn
		graphRPC = graphv1.NewKnowledgeGraphServiceClient(conn)
	})
	return graphRPC
}

func closeGraph() {
	if graphConn != nil {
		_ = graphConn.Close()
	}
}

// projectKnowledge 读取这一个 project 的子图。图服务不可用时任务照常入队。
func (d *Dispatcher) projectKnowledge(ctx context.Context, swarmID string, window []*sessionv1.Message) ([]string, string) {
	if swarmID == "" {
		return nil, ""
	}
	client := graphClient()
	if client == nil {
		return nil, ""
	}
	call, cancel := context.WithTimeout(ctx, 800*time.Millisecond)
	defer cancel()
	resp, err := client.QuerySubgraph(call, &graphv1.QuerySubgraphRequest{
		SeedNodeIds: graphSeeds(swarmID, window),
		Depth:       2,
		MaxNodes:    24,
	})
	if err != nil {
		log.Printf("graph query project=%s: %v", swarmID, err)
		return nil, ""
	}
	return formatKnowledge(resp)
}

func graphSeeds(swarmID string, window []*sessionv1.Message) []string {
	root := swarmID + "/root"
	seeds := []string{root}
	seen := map[string]bool{root: true}
	for _, msg := range window {
		for _, block := range msg.GetContent() {
			att := block.GetAttachment()
			if att == nil {
				continue
			}
			id := att.GetGraphNodeId()
			if id == "" || seen[id] {
				continue
			}
			seen[id] = true
			seeds = append(seeds, id)
		}
	}
	return seeds
}

func formatKnowledge(resp *graphv1.QuerySubgraphResponse) ([]string, string) {
	if resp == nil {
		return nil, ""
	}
	var b strings.Builder
	b.WriteString("Project knowledge graph (this project only):")
	var ids []string
	for _, node := range resp.GetNodes() {
		if strings.HasSuffix(node.GetNodeId(), "/root") {
			continue
		}
		ids = append(ids, node.GetNodeId())
		snippet := node.GetTitle()
		if props := node.GetProps(); props != nil {
			if text, ok := props.AsMap()["text"].(string); ok && text != "" {
				snippet = text
			}
		}
		if len(snippet) > 400 {
			snippet = snippet[:400]
		}
		fmt.Fprintf(&b, "\n- [%s] %s", kindName(node.GetKind()), snippet)
		if b.Len() > 4000 {
			break
		}
	}
	if len(ids) == 0 {
		return nil, ""
	}
	writeRelations(&b, resp)
	return ids, b.String()
}

func writeRelations(b *strings.Builder, resp *graphv1.QuerySubgraphResponse) {
	titles := map[string]string{}
	for _, node := range resp.GetNodes() {
		titles[node.GetNodeId()] = node.GetTitle()
	}
	written := 0
	for _, edge := range resp.GetEdges() {
		label, fact := relationText(edge)
		if label == "" || b.Len() > 4000 {
			continue
		}
		if written == 0 {
			b.WriteString("\nRelations:")
		}
		fmt.Fprintf(
			b,
			"\n- %s: %s -> %s",
			label,
			shortTitle(titles[edge.GetFromNodeId()]),
			shortTitle(titles[edge.GetToNodeId()]),
		)
		if fact != "" {
			fmt.Fprintf(b, " — %s", fact)
		}
		written++
		if written >= 12 {
			break
		}
	}
}

func shortTitle(title string) string {
	title = strings.TrimSpace(title)
	if len(title) > 80 {
		return title[:80]
	}
	return title
}

func relationText(edge *graphv1.Edge) (string, string) {
	label, fact := "", ""
	if props := edge.GetProps(); props != nil {
		values := props.AsMap()
		if text, ok := values["relation"].(string); ok {
			label = strings.TrimSpace(text)
		}
		if text, ok := values["fact"].(string); ok {
			fact = strings.TrimSpace(text)
		}
	}
	if label == "" {
		label = relationName(edge.GetKind())
	}
	if len(fact) > 180 {
		fact = fact[:180]
	}
	return label, fact
}

func relationName(kind graphv1.EdgeKind) string {
	switch kind {
	case graphv1.EdgeKind_EDGE_KIND_SUPPORTS:
		return "SUPPORTS"
	case graphv1.EdgeKind_EDGE_KIND_CONTRADICTS:
		return "CONTRADICTS"
	case graphv1.EdgeKind_EDGE_KIND_RELATES:
		return "RELATES"
	default:
		return ""
	}
}

func kindName(kind graphv1.NodeKind) string {
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
