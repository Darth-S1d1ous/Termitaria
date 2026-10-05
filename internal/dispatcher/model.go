package dispatcher

import (
	"os"
	"strconv"

	commonv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/common/v1"
)

// FromEnv 解析 MVP 的共享模型。调度器按角色覆盖之前，runtime 给每个 agent 各调一次。
// 未设 NVIDIA_MODEL 时仍是 stub-model / 512 token；设了则 provider=nvidia，
// 预算取 MODEL_MAX_TOKENS（默认 16384）。
func FromEnv() AgentConfig {
	name := envOr("NVIDIA_MODEL", "stub-model")
	provider := "nebius-token-factory"
	maxTokens := uint32(512)
	if name != "stub-model" {
		provider = "nvidia"
		if n := envOrUint64("MODEL_MAX_TOKENS", 16384); n > 0 && n <= uint64(^uint32(0)) {
			maxTokens = uint32(n)
		}
	}
	return AgentConfig{
		Model: &commonv1.ModelRef{
			Provider: provider,
			Tier:     commonv1.ModelTier_MODEL_TIER_SUPER,
			Model:    name,
		},
		Budget: &commonv1.Budget{MaxTokens: maxTokens},
	}
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envOrUint64(key string, fallback uint64) uint64 {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.ParseUint(v, 10, 64); err == nil {
			return n
		}
	}
	return fallback
}
