package gateway

import (
	"testing"

	graphv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/graph/v1"
	"google.golang.org/protobuf/types/known/structpb"
)

func TestGraphViewReadsProjectNodes(t *testing.T) {
	props, err := structpb.NewStruct(map[string]any{"text": "The quote is 4.80 dollars per kilogram."})
	if err != nil {
		t.Fatal(err)
	}
	view := graphView("p_north", &graphv1.QuerySubgraphResponse{
		Nodes: []*graphv1.Node{
			{
				NodeId: "p_north/root",
				Kind:   graphv1.NodeKind_NODE_KIND_CONCEPT,
				Title:  "Project sources",
			},
			{
				NodeId: "p_north/memo",
				Kind:   graphv1.NodeKind_NODE_KIND_DOCUMENT,
				Title:  "Northwire memo",
				Props:  props,
			},
		},
		Edges: []*graphv1.Edge{
			{
				EdgeId:     "p_north/memo/cited",
				FromNodeId: "p_north/root",
				ToNodeId:   "p_north/memo",
				Kind:       graphv1.EdgeKind_EDGE_KIND_CITES,
			},
		},
	})
	if view.ProjectID != "p_north" || len(view.Nodes) != 2 || len(view.Edges) != 1 {
		t.Fatalf("view = %+v", view)
	}
	if view.Nodes[1].Kind != "DOCUMENT" || view.Nodes[1].Text != "The quote is 4.80 dollars per kilogram." {
		t.Fatalf("document = %+v", view.Nodes[1])
	}
	if view.Edges[0].Kind != "CITES" || view.Edges[0].Source != "p_north/root" {
		t.Fatalf("edge = %+v", view.Edges[0])
	}
	empty := graphView("p_north", nil)
	if len(empty.Nodes) != 0 || len(empty.Edges) != 0 {
		t.Fatalf("nil response = %+v", empty)
	}
}
