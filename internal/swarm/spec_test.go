package swarm

import "testing"

func TestParseRequiresOneOrchestrator(t *testing.T) {
	raw := []byte(`
name: lab
goal: find a gap
agents:
  - id: pi
    kind: ORCHESTRATOR
    displayName: PI
    rolePrompt: lead
  - id: reviewer
    rolePrompt: attack
`)
	spec, err := Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	if spec.Name != "lab" || len(spec.Agents) != 2 {
		t.Fatalf("spec = %+v", spec)
	}
	if spec.Agents[1].Kind != "WORKER" {
		t.Fatalf("default kind = %s", spec.Agents[1].Kind)
	}

	if _, err := Parse([]byte("name: lab\ngoal: x\nagents:\n  - id: a\n    rolePrompt: p\n  - id: b\n    rolePrompt: p\n")); err == nil {
		t.Fatal("missing orchestrator should fail")
	}
	if _, err := Parse([]byte("name: lab\ngoal: x\nagents:\n  - id: a\n    kind: ORCHESTRATOR\n    rolePrompt: p\n  - id: a\n    rolePrompt: p\n")); err == nil {
		t.Fatal("duplicate id should fail")
	}
}

func TestLoadExample(t *testing.T) {
	spec, err := LoadFile("../../examples/swarms/autoresearch.yaml")
	if err != nil {
		t.Fatal(err)
	}
	if spec.Name != "autoresearch" || len(spec.Agents) < 3 || spec.Raw == "" {
		t.Fatalf("example spec = %s agents=%d", spec.Name, len(spec.Agents))
	}
}
