package swarm

import (
	"os"
	"path/filepath"
	"testing"
)

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

func TestParseReadsSkills(t *testing.T) {
	raw := []byte(`
name: lab
goal: find a gap
agents:
  - id: pi
    kind: ORCHESTRATOR
    rolePrompt: lead
  - id: researcher
    rolePrompt: search
    skills: [market-data, filings]
`)
	spec, err := Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	if len(spec.Agents[1].Skills) != 2 || spec.Agents[1].Skills[0] != "market-data" {
		t.Fatalf("skills = %+v", spec.Agents[1].Skills)
	}
}

func TestSkillNames(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "market-data.md"), []byte("---\nname: market-data\ndescription: prices\n---\nbody\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	names, err := SkillNames(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := names["market-data"]; !ok {
		t.Fatalf("names = %+v", names)
	}
	empty, err := SkillNames(filepath.Join(dir, "missing"))
	if err != nil || len(empty) != 0 {
		t.Fatalf("missing dir = %+v %v", empty, err)
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
