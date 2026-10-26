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
				Relation:   "LOCATED_IN",
			},
		},
	})
	if view.ProjectID != "p_north" || len(view.Nodes) != 2 || len(view.Edges) != 1 {
		t.Fatalf("view = %+v", view)
	}
	if view.Nodes[1].Kind != "DOCUMENT" || view.Nodes[1].Text != "The quote is 4.80 dollars per kilogram." {
		t.Fatalf("document = %+v", view.Nodes[1])
	}
	if view.Edges[0].Relation != "LOCATED_IN" || view.Edges[0].Source != "p_north/root" || view.Edges[0].Fact != "" {
		t.Fatalf("edge = %+v", view.Edges[0])
	}
	named := graphView("p_north", &graphv1.QuerySubgraphResponse{
		Edges: []*graphv1.Edge{{
			EdgeId:     "e1",
			FromNodeId: "p_north/report/c0",
			ToNodeId:   "p_north/memo/c0",
			Relation:   "PRICED_AT",
			Fact:       "The brief prices the wire at 4.80 dollars per kilogram.",
		}},
	})
	if named.Edges[0].Relation != "PRICED_AT" {
		t.Fatalf("named edge = %+v", named.Edges[0])
	}
	if named.Edges[0].Fact != "The brief prices the wire at 4.80 dollars per kilogram." {
		t.Fatalf("fact = %q", named.Edges[0].Fact)
	}
	empty := graphView("p_north", nil)
	if len(empty.Nodes) != 0 || len(empty.Edges) != 0 {
		t.Fatalf("nil response = %+v", empty)
	}
}

func TestAttachUploadsShowsTheFile(t *testing.T) {
	view := graphView("p_north", &graphv1.QuerySubgraphResponse{
		Nodes: []*graphv1.Node{{
			NodeId: "n1",
			Kind:   graphv1.NodeKind_NODE_KIND_CONCEPT,
			Title:  "Northwire",
		}},
	})
	attachUploads(&view, "p_north", []seedJSON{
		{ID: "k_old", Kind: "text", Title: "Seed", Body: "already in the prompt"},
		{ID: "u_file", Kind: "file", Title: "notes.md", Body: "Copper closed at 9840."},
	})
	if len(view.Nodes) != 2 {
		t.Fatalf("nodes = %+v", view.Nodes)
	}
	doc := view.Nodes[1]
	if doc.ID != "p_north/u_file" || doc.Kind != "DOCUMENT" || doc.Title != "notes.md" || doc.Text != "Copper closed at 9840." {
		t.Fatalf("upload = %+v", doc)
	}
	attachUploads(&view, "p_north", []seedJSON{{ID: "u_file", Kind: "file", Title: "notes.md", Body: "Copper closed at 9840."}})
	if len(view.Nodes) != 2 {
		t.Fatalf("duplicated nodes = %+v", view.Nodes)
	}
}
