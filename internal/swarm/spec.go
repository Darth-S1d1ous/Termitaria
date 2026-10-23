// Package swarm 读 SwarmSpec YAML（schemas/swarmspec.schema.json）。
// 控制面只取 agent 名册（id/显示名/角色 prompt/预算）与项目目标；
// 边、拓扑、记忆策略等调度语义由后续 slice 消费。
package swarm

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

type AgentSpec struct {
	ID          string   `yaml:"id"`
	DisplayName string   `yaml:"displayName"`
	Kind        string   `yaml:"kind"`
	RolePrompt  string   `yaml:"rolePrompt"`
	Tools       []string `yaml:"tools"`
	Skills      []string `yaml:"skills"`
	Budget      struct {
		MaxTokens uint32 `yaml:"maxTokens"`
	} `yaml:"budget"`
}

type Spec struct {
	Name   string
	Goal   string
	Agents []AgentSpec
	Raw    string // 原始 YAML，创建项目时原样回给前端
}

type specFile struct {
	Name   string      `yaml:"name"`
	Goal   string      `yaml:"goal"`
	Agents []AgentSpec `yaml:"agents"`
}

func LoadFile(path string) (*Spec, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	spec, err := Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return spec, nil
}

// Parse 编译一份 SwarmSpec YAML。恰好一个 ORCHESTRATOR，agent id 不重复。
func Parse(raw []byte) (*Spec, error) {
	var f specFile
	if err := yaml.Unmarshal(raw, &f); err != nil {
		return nil, fmt.Errorf("parse swarm spec: %w", err)
	}
	if f.Name == "" || strings.TrimSpace(f.Goal) == "" || len(f.Agents) == 0 {
		return nil, fmt.Errorf("name, goal, and agents are required")
	}
	seen := map[string]bool{}
	orchestrators := 0
	for i := range f.Agents {
		a := &f.Agents[i]
		if a.ID == "" {
			return nil, fmt.Errorf("agent #%d is missing an id", i)
		}
		if seen[a.ID] {
			return nil, fmt.Errorf("duplicate agent id %s", a.ID)
		}
		seen[a.ID] = true
		if a.DisplayName == "" {
			a.DisplayName = a.ID
		}
		if a.Kind == "" {
			a.Kind = "WORKER"
		}
		if a.Kind == "ORCHESTRATOR" {
			orchestrators++
		}
		a.RolePrompt = strings.TrimSpace(a.RolePrompt)
	}
	if orchestrators != 1 {
		return nil, fmt.Errorf("exactly one ORCHESTRATOR is required, found %d", orchestrators)
	}
	return &Spec{
		Name:   f.Name,
		Goal:   strings.TrimSpace(f.Goal),
		Agents: f.Agents,
		Raw:    string(raw),
	}, nil
}

// LoadDir 读目录下全部 *.yaml，按文件名排序，保证项目列表稳定。
func LoadDir(dir string) ([]*Spec, error) {
	paths, err := filepath.Glob(filepath.Join(dir, "*.yaml"))
	if err != nil {
		return nil, err
	}
	sort.Strings(paths)
	var specs []*Spec
	for _, path := range paths {
		spec, err := LoadFile(path)
		if err != nil {
			return nil, err
		}
		specs = append(specs, spec)
	}
	if len(specs) == 0 {
		return nil, fmt.Errorf("no swarm specs in %s", dir)
	}
	return specs, nil
}

// SkillNames 读 skills/*.md 的 frontmatter name。目录不存在时返回空表。
func SkillNames(dir string) (map[string]struct{}, error) {
	out := map[string]struct{}{}
	paths, err := filepath.Glob(filepath.Join(dir, "*.md"))
	if err != nil {
		return nil, err
	}
	for _, path := range paths {
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		name := strings.TrimSuffix(filepath.Base(path), filepath.Ext(path))
		text := string(raw)
		if strings.HasPrefix(text, "---") {
			rest := text[3:]
			end := strings.Index(rest, "\n---")
			if end >= 0 {
				var meta struct {
					Name string `yaml:"name"`
				}
				if err := yaml.Unmarshal([]byte(rest[:end]), &meta); err != nil {
					return nil, fmt.Errorf("%s: %w", path, err)
				}
				if strings.TrimSpace(meta.Name) != "" {
					name = strings.TrimSpace(meta.Name)
				}
			}
		}
		if name != "" {
			out[name] = struct{}{}
		}
	}
	return out, nil
}
