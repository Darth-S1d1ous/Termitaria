package swarm

import "strings"

// Section 是成稿里的一节。MinChars 按字符计。Citations 为真时这一节至少要有一条工具返回过的链接。
type Section struct {
	Heading   string `json:"heading" yaml:"heading"`
	MinChars  int    `json:"min_chars" yaml:"minChars"`
	Citations bool   `json:"citations" yaml:"citations"`
}

// Deliverable 是项目创建时定下来的成稿验收条件。模型改不了它。
type Deliverable struct {
	Kind        string    `json:"kind" yaml:"kind"`
	MinChars    int       `json:"min_chars" yaml:"minChars"`
	RequireDeck bool      `json:"require_deck" yaml:"requireDeck"`
	Sections    []Section `json:"sections" yaml:"sections"`
}

// CompileDeliverable 在创建项目时定下验收条件。
// 写了 kind 就用它。没写就从目标里认 paper 或 brief。认不出就没有成稿规格，沿用原来的字节门槛。
// 没写 sections 时用该 kind 的内置模板，避免每个 swarm 抄一遍章节。
func CompileDeliverable(raw Deliverable, goal string) *Deliverable {
	kind := strings.TrimSpace(raw.Kind)
	if kind == "" {
		kind = kindFromGoal(goal)
	}
	base, ok := template(kind)
	if !ok {
		return nil
	}
	if len(raw.Sections) > 0 {
		base.Sections = append([]Section(nil), raw.Sections...)
		base.RequireDeck = raw.RequireDeck
		if raw.MinChars > 0 {
			base.MinChars = raw.MinChars
		}
	}
	base.Kind = kind
	return &base
}

func kindFromGoal(goal string) string {
	folded := strings.ToLower(goal)
	switch {
	case strings.Contains(folded, "trading brief"), strings.Contains(folded, "market brief"):
		return "brief"
	case strings.Contains(folded, "hypothesis"), strings.Contains(folded, "experiment"), strings.Contains(folded, "literature"):
		return "paper"
	default:
		return ""
	}
}

func template(kind string) (Deliverable, bool) {
	switch kind {
	case "paper":
		return Deliverable{
			Kind:     "paper",
			MinChars: 6000,
			Sections: []Section{
				{Heading: "Abstract", MinChars: 400},
				{Heading: "Related work", MinChars: 800, Citations: true},
				{Heading: "Hypothesis", MinChars: 400, Citations: true},
				{Heading: "Experiment", MinChars: 800, Citations: true},
				{Heading: "Limitations", MinChars: 300},
			},
		}, true
	case "brief":
		return Deliverable{
			Kind:        "brief",
			MinChars:    1200,
			RequireDeck: true,
			Sections: []Section{
				{Heading: "Price table", MinChars: 200, Citations: true},
				{Heading: "Why", MinChars: 400, Citations: true},
				{Heading: "What to watch", MinChars: 200},
				{Heading: "Sources", MinChars: 80, Citations: true},
			},
		}, true
	default:
		return Deliverable{}, false
	}
}
