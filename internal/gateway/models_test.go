package gateway

import (
	"testing"

	commonv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/common/v1"
)

func TestModelOptionsStayDeepSeekOnlyUntilCloudIsEnabled(t *testing.T) {
	t.Setenv("MODEL_CLOUD_ENABLED", "")
	t.Setenv("NEBIUS_API_KEY", "")
	t.Setenv("NVIDIA_API_KEY", "")
	t.Setenv("DEEPSEEK_MODEL_FLASH", "")
	t.Setenv("DEEPSEEK_MODEL_PRO", "")
	t.Setenv("DEEPSEEK_MODEL_FLASH_LABEL", "")
	t.Setenv("DEEPSEEK_MODEL_PRO_LABEL", "")

	options := modelOptions()
	if len(options) != 2 {
		t.Fatalf("options = %+v", options)
	}
	if options[0].ID != "deepseek-flash" || options[1].ID != "deepseek-v4-pro" {
		t.Fatalf("options = %+v", options)
	}
	if options[0].Label != "DeepSeek-V4.1-Flash" || options[1].Label != "DeepSeek-V4-Pro" {
		t.Fatalf("labels = %q, %q", options[0].Label, options[1].Label)
	}
	ref, err := resolveModel("deepseek-v4-pro")
	if err != nil {
		t.Fatal(err)
	}
	if ref.GetProvider() != "deepseek" || ref.GetModel() != "deepseek-v4-pro" || ref.GetTier() != commonv1.ModelTier_MODEL_TIER_SUPER {
		t.Fatalf("model = %+v", ref)
	}
	if _, err := resolveModel("nebius-nemotron-super"); err == nil {
		t.Fatal("cloud model should be disabled")
	}
}

func TestModelOptionsExposeCloudModelsWhenEnabled(t *testing.T) {
	t.Setenv("MODEL_CLOUD_ENABLED", "1")
	t.Setenv("NEBIUS_API_KEY", "nb")
	t.Setenv("NVIDIA_API_KEY", "")
	t.Setenv("MODEL_TIER_SUPER", "nvidia/nemotron-3-super-120b-a12b")

	options := modelOptions()
	var found *modelOption
	for i := range options {
		if options[i].ID == "nebius-nemotron-super" {
			found = &options[i]
		}
	}
	if found == nil || !found.Enabled || found.Provider != "nebius-token-factory" {
		t.Fatalf("options = %+v", options)
	}
	ref, err := resolveModel("nebius-nemotron-super")
	if err != nil {
		t.Fatal(err)
	}
	if ref.GetProvider() != "nebius-token-factory" || ref.GetModel() != "nvidia/nemotron-3-super-120b-a12b" {
		t.Fatalf("model = %+v", ref)
	}
}

func TestResolveModelEmptyKeepsAgentDefault(t *testing.T) {
	ref, err := resolveModel("  ")
	if err != nil || ref != nil {
		t.Fatalf("ref = %+v err = %v", ref, err)
	}
}
