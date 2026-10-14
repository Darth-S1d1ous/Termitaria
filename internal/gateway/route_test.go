package gateway

import "testing"

func TestChannelAgent(t *testing.T) {
	agents := []agentJSON{
		{ID: "pi", Kind: "ORCHESTRATOR"},
		{ID: "reviewer", Kind: "WORKER"},
	}
	got, err := ChannelAgent(agents, "")
	if err != nil || got != "pi" {
		t.Fatalf("no mention = %s %v", got, err)
	}
	got, err = ChannelAgent(agents, "reviewer")
	if err != nil || got != "reviewer" {
		t.Fatalf("mention = %s %v", got, err)
	}
	if _, err := ChannelAgent(agents, "nobody"); err == nil {
		t.Fatal("unknown agent should fail")
	}
}

func TestSessionKeySeparatesChannelAndDM(t *testing.T) {
	dm := sessionKey("autoresearch", "pi", "pi")
	ch := sessionKey("autoresearch", channelChatID, "pi")
	if dm == ch {
		t.Fatalf("channel and dm collapsed: %s", dm)
	}
	if !isDMKey("autoresearch", dm) || !isChannelKey("autoresearch", ch) {
		t.Fatalf("dm=%s channel=%s", dm, ch)
	}
	sessions := map[string]string{dm: "s_dm", ch: "s_ch"}
	if chatIDForSession("autoresearch", "s_dm", sessions) != "pi" {
		t.Fatal("dm session should stay on the agent chat")
	}
	if chatIDForSession("autoresearch", "s_ch", sessions) != channelChatID {
		t.Fatal("channel session should stay on the channel")
	}
	if chatIDForSession("autoresearch", "s_mesh", sessions) != channelChatID {
		t.Fatal("agent mesh session should show on the channel")
	}
}
