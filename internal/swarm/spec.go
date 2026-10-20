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
		return nil, fmt.Errorf("name、goal、agents 不能为空")
	}
	seen := map[string]bool{}
	orchestrators := 0
	for i := range f.Agents {
		a := &f.Agents[i]
		if a.ID == "" {
			return nil, fmt.Errorf("agent #%d 缺 id", i)
		}
		if seen[a.ID] {
			return nil, fmt.Errorf("agent id %s 重复", a.ID)
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
		return nil, fmt.Errorf("需要恰好一个 ORCHESTRATOR，现在是 %d 个", orchestrators)
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
