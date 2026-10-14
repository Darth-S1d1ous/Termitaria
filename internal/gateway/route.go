package gateway

import (
	"errors"
	"strings"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
)

const channelChatID = "channel"

var (
	errNoOrchestrator = errors.New("swarm 没有 orchestrator")
	errUnknownAgent   = errors.New("unknown agent")
)

// ChannelAgent 决定频道消息交给谁。没有 @ 时是 orchestrator，@ 了就是那一个 agent。
func ChannelAgent(agents []agentJSON, mention string) (string, error) {
	mention = strings.TrimSpace(mention)
	if mention == "" {
		for _, agent := range agents {
			if agent.Kind == "ORCHESTRATOR" {
				return agent.ID, nil
			}
		}
		return "", errNoOrchestrator
	}
	for _, agent := range agents {
		if agent.ID == mention {
			return agent.ID, nil
		}
	}
	return "", errUnknownAgent
}

// sessionKey 把频道和私聊分成两条 session。同一对 user↔agent 可以各有一条。
func sessionKey(projectID, chatID, agentID string) string {
	if chatID == channelChatID {
		return projectID + "/channel/" + agentID
	}
	return projectID + "/" + chatID
}

func isChannelKey(projectID, key string) bool {
	return strings.HasPrefix(key, projectID+"/channel/")
}

func isDMKey(projectID, key string) bool {
	rest, ok := strings.CutPrefix(key, projectID+"/")
	return ok && rest != "" && !strings.Contains(rest, "/")
}

// chatIDForSession 把 session 映射回聊天行。私聊是 agent id，其余都归频道。
func chatIDForSession(projectID, sessionID string, sessions map[string]string) string {
	for key, id := range sessions {
		if id != sessionID || !strings.HasPrefix(key, projectID+"/") {
			continue
		}
		if isDMKey(projectID, key) {
			return strings.TrimPrefix(key, projectID+"/")
		}
		return channelChatID
	}
	return channelChatID
}

func bothAgents(sess *sessionv1.Session) bool {
	ps := sess.GetParticipants()
	if len(ps) != 2 {
		return false
	}
	return ps[0].GetAgentId() != "" && ps[1].GetAgentId() != ""
}
