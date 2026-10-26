package gateway

import (
	"strings"
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

func TestMergeDocumentsKeepsLongestAndMovesEdges(t *testing.T) {
	view := projectGraphJSON{
		ProjectID: "p_north",
		Nodes: []graphNodeJSON{
			{ID: "doc", Kind: "DOCUMENT", Title: "notes.md", Text: "full text"},
			{ID: "doc_old", Kind: "DOCUMENT", Title: "Notes.MD", Text: "old"},
			{ID: "concept", Kind: "CONCEPT", Title: "notes.md", Text: "summary"},
			{ID: "other", Kind: "CONCEPT", Title: "GPQA", Text: "benchmark"},
		},
		Edges: []graphEdgeJSON{
			{ID: "e1", Source: "concept", Target: "other", Relation: "MENTIONS"},
			{ID: "e2", Source: "doc_old", Target: "other", Relation: "MENTIONS"},
			{ID: "e3", Source: "doc_old", Target: "doc", Relation: "DUPLICATE"},
		},
	}
	mergeDocuments(&view)
	if len(view.Nodes) != 2 || view.Nodes[0].ID != "doc" || view.Nodes[1].ID != "other" {
		t.Fatalf("nodes = %+v", view.Nodes)
	}
	if view.Nodes[0].Text != "full text" {
		t.Fatalf("winner = %+v", view.Nodes[0])
	}
	if len(view.Edges) != 1 || view.Edges[0].Source != "doc" || view.Edges[0].Target != "other" {
		t.Fatalf("edges = %+v", view.Edges)
	}
}

func TestNormalizeSeedsKeepsFileText(t *testing.T) {
	body := strings.Repeat("x", 25000)
	seeds, err := normalizeSeeds([]seedJSON{{Kind: "file", Title: "notes.md", Body: body}})
	if err != nil {
		t.Fatal(err)
	}
	if got := len([]rune(seeds[0].Body)); got != 20000 {
		t.Fatalf("body length = %d, want 20000", got)
	}
}
