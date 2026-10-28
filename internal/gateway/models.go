package gateway

import (
	"errors"
	"net/http"
	"os"
	"strings"

	commonv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/common/v1"
)

// modelOption 是 message 栏下拉列表的一行。DeepSeek 总是可选；云端模型只有
// MODEL_CLOUD_ENABLED=1 且对应 key 存在时才出现在列表里，避免测试误花 credit。
type modelOption struct {
	ID       string `json:"id"`
	Label    string `json:"label"`
	Provider string `json:"provider"`
	Model    string `json:"model"`
	Tier     string `json:"tier,omitempty"`
	Enabled  bool   `json:"enabled"`
}

func modelEnv(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func modelCloudEnabled() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("MODEL_CLOUD_ENABLED"))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func modelOptions() []modelOption {
	// 有 Nebius key 时，下拉里的 DeepSeek 走 Token Factory，不再要 DEEPSEEK_API_KEY。
	// 模型 id 以 api.tokenfactory.nebius.com 的目录为准。
	flashProvider, proProvider := "deepseek", "deepseek"
	flashModel, proModel := "deepseek-flash", "deepseek-v4-pro"
	if modelCloudEnabled() && strings.TrimSpace(os.Getenv("NEBIUS_API_KEY")) != "" {
		flashProvider, proProvider = "nebius-token-factory", "nebius-token-factory"
		flashModel = "deepseek-ai/DeepSeek-V4.1-Flash"
		proModel = "deepseek-ai/DeepSeek-V4-Pro"
	}
	options := []modelOption{
		{
			ID: "deepseek-flash", Label: modelEnv("DEEPSEEK_MODEL_FLASH_LABEL", "DeepSeek-V4.1-Flash"),
			Provider: flashProvider,
			Model:    modelEnv("DEEPSEEK_MODEL_FLASH", flashModel), Tier: "SUPER", Enabled: true,
		},
		{
			ID: "deepseek-v4-pro", Label: modelEnv("DEEPSEEK_MODEL_PRO_LABEL", "DeepSeek-V4-Pro"),
			Provider: proProvider,
			Model:    modelEnv("DEEPSEEK_MODEL_PRO", proModel), Tier: "SUPER", Enabled: true,
		},
	}
	if !modelCloudEnabled() {
		return options
	}
	if os.Getenv("NEBIUS_API_KEY") != "" || os.Getenv("MEMORY_LLM_API_KEY") != "" {
		options = append(options,
			modelOption{
				ID: "nebius-nemotron-super", Label: "Nemotron 3 Super (Nebius)",
				Provider: "nebius-token-factory",
				Model:    modelEnv("MODEL_TIER_SUPER", "nvidia/nemotron-3-super-120b-a12b"),
				Tier:     "SUPER", Enabled: true,
			},
			modelOption{
				ID: "nebius-nemotron-ultra", Label: "Nemotron 3 Ultra (Nebius)",
				Provider: "nebius-token-factory",
				Model:    modelEnv("MODEL_TIER_ULTRA", "nvidia/Nemotron-3-Ultra-550b-a55b"),
				Tier:     "ULTRA", Enabled: true,
			},
			modelOption{
				ID: "nebius-kimi-k3", Label: "Kimi K3",
				Provider: "nebius-token-factory",
				Model:    "moonshotai/Kimi-K3", Tier: "SUPER", Enabled: true,
			},
			modelOption{
				ID: "nebius-kimi-k2.6", Label: "Kimi K2.6",
				Provider: "nebius-token-factory",
				Model:    "moonshotai/Kimi-K2.6", Tier: "SUPER", Enabled: true,
			},
			modelOption{
				ID: "nebius-kimi-k2.7-code", Label: "Kimi K2.7 Code",
				Provider: "nebius-token-factory",
				Model:    "moonshotai/Kimi-K2.7-Code", Tier: "SUPER", Enabled: true,
			},
			modelOption{
				ID: "nebius-minimax-m3", Label: "MiniMax M3",
				Provider: "nebius-token-factory",
				Model:    "MiniMaxAI/MiniMax-M3", Tier: "SUPER", Enabled: true,
			},
			modelOption{
				ID: "nebius-minimax-m3.1-flash", Label: "MiniMax M3.1 Flash",
				Provider: "nebius-token-factory",
				Model:    "MiniMaxAI/MiniMax-M3.1-Flash", Tier: "SUPER", Enabled: true,
			},
		)
	}
	if os.Getenv("NVIDIA_API_KEY") != "" {
		options = append(options,
			modelOption{
				ID: "nvidia-nemotron-super", Label: "Nemotron 3 Super (NVIDIA)",
				Provider: "nvidia",
				Model:    modelEnv("NVIDIA_MODEL_SUPER", "nvidia/nemotron-3-super-120b-a12b"),
				Tier:     "SUPER", Enabled: true,
			},
			modelOption{
				ID: "nvidia-nemotron-ultra", Label: "Nemotron 3 Ultra (NVIDIA)",
				Provider: "nvidia",
				Model:    modelEnv("NVIDIA_MODEL_ULTRA", "nvidia/Nemotron-3-Ultra-550b-a55b"),
				Tier:     "ULTRA", Enabled: true,
			},
		)
	}
	return options
}

func (s *Server) listModels(w http.ResponseWriter, r *http.Request) {
	options := modelOptions()
	defaultID := strings.TrimSpace(os.Getenv("DEFAULT_MODEL_ID"))
	validDefault := false
	for _, option := range options {
		if option.ID == defaultID && option.Enabled {
			validDefault = true
			break
		}
	}
	if !validDefault {
		defaultID = ""
		for _, option := range options {
			if option.Enabled {
				defaultID = option.ID
				break
			}
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"models":     options,
		"default_id": defaultID,
	})
}

// resolveModel turns a client-selected id into the ModelRef carried by the
// next task. Empty id keeps the agent's configured model.
func resolveModel(id string) (*commonv1.ModelRef, error) {
	id = strings.TrimSpace(id)
	if id == "" {
		return nil, nil
	}
	for _, option := range modelOptions() {
		if option.ID != id || !option.Enabled {
			continue
		}
		tier := commonv1.ModelTier_MODEL_TIER_SUPER
		if option.Tier == "ULTRA" {
			tier = commonv1.ModelTier_MODEL_TIER_ULTRA
		}
		return &commonv1.ModelRef{
			Provider: option.Provider,
			Tier:     tier,
			Model:    option.Model,
		}, nil
	}
	return nil, errors.New("unknown or disabled model")
}
