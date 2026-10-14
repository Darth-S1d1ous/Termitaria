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
	ID          string `yaml:"id"`
	DisplayName string `yaml:"displayName"`
	Kind        string `yaml:"kind"`
	RolePrompt  string `yaml:"rolePrompt"`
	Budget      struct {
		MaxTokens uint32 `yaml:"maxTokens"`
	} `yaml:"budget"`
}

type Spec struct {
	Name   string
	Goal   string
	Agents []AgentSpec
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
	var f specFile
	if err := yaml.Unmarshal(raw, &f); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	if f.Name == "" || len(f.Agents) == 0 {
		return nil, fmt.Errorf("%s: name/agents 不能为空", path)
	}
	for i := range f.Agents {
		a := &f.Agents[i]
		if a.ID == "" {
			return nil, fmt.Errorf("%s: agent #%d 缺 id", path, i)
		}
		if a.DisplayName == "" {
			a.DisplayName = a.ID
		}
		if a.Kind == "" {
			a.Kind = "WORKER"
		}
		a.RolePrompt = strings.TrimSpace(a.RolePrompt)
	}
	return &Spec{Name: f.Name, Goal: strings.TrimSpace(f.Goal), Agents: f.Agents}, nil
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
