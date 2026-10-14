package live

import "testing"

func TestTrackFinishPresence(t *testing.T) {
	h := New()
	ch := h.Subscribe("sw1")
	defer h.Unsubscribe(ch)

	h.Track(TaskMeta{TaskID: "t1", SessionID: "s1", AgentID: "pi", SwarmID: "sw1"})
	if h.State("pi") != "thinking" {
		t.Fatalf("state = %s", h.State("pi"))
	}
	meta, ok := h.Task("t1")
	if !ok || meta.SessionID != "s1" {
		t.Fatalf("task = %+v ok=%v", meta, ok)
	}
	if MessageIDForTask("t1") != "m_task:t1" {
		t.Fatalf("message id = %s", MessageIDForTask("t1"))
	}

	h.SetPresence("pi", "online")
	if h.State("pi") != "thinking" {
		t.Fatalf("在途 task 不应被 online 覆盖，state=%s", h.State("pi"))
	}

	h.Finish("t1")
	if h.State("pi") != "online" {
		t.Fatalf("finish 后 state = %s", h.State("pi"))
	}
	h.Finish("t1")
	if _, ok := h.Task("t1"); ok {
		t.Fatal("重复 finish 应是空操作")
	}
}

func TestOfflineClearsThinking(t *testing.T) {
	h := New()
	h.Track(TaskMeta{TaskID: "t1", AgentID: "pi", SwarmID: "sw1"})
	h.SetPresence("pi", "offline")
	if h.State("pi") != "offline" {
		t.Fatalf("state = %s", h.State("pi"))
	}
}
