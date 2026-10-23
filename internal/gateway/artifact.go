package gateway

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/Darth-S1d1ous/Termitaria/internal/swarm"
)

const (
	minReportBytes = 200
	minDeckBytes   = 800
	maxArtifact    = 2_000_000
)

func deliverablesReady(arts []artifactJSON) bool {
	var report, deck bool
	for _, art := range arts {
		if art.Kind == "report" && art.Bytes >= minReportBytes {
			report = true
		}
		if art.Kind == "deck" && art.Bytes >= minDeckBytes {
			deck = true
		}
	}
	return report && deck
}

func (s *Server) createArtifact(w http.ResponseWriter, r *http.Request) {
	projectID := r.PathValue("id")
	var body struct {
		Name          string `json:"name"`
		Kind          string `json:"kind"`
		ContentBase64 string `json:"content_base64"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, 400, "bad json")
		return
	}
	name, err := artifactName(body.Name, body.Kind)
	if err != nil {
		writeError(w, 400, err.Error())
		return
	}
	raw, err := base64.StdEncoding.DecodeString(body.ContentBase64)
	if err != nil || len(raw) == 0 || len(raw) > maxArtifact {
		writeError(w, 400, "content_base64 must be a file under 2MB")
		return
	}
	if body.Kind == "report" && len(raw) < minReportBytes {
		writeError(w, 400, "report is too short")
		return
	}
	if body.Kind == "deck" && len(raw) < minDeckBytes {
		writeError(w, 400, "deck is too short")
		return
	}
	path, err := s.artifactPath(projectID, name)
	if err != nil {
		writeError(w, 500, err.Error())
		return
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		writeError(w, 502, err.Error())
		return
	}
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		writeError(w, 502, err.Error())
		return
	}
	s.mu.Lock()
	var arts []artifactJSON
	found := false
	for _, p := range s.projects {
		if p.ID != projectID {
			continue
		}
		found = true
		item := artifactJSON{Name: name, Kind: body.Kind, Bytes: len(raw)}
		replaced := false
		for i := range p.Artifacts {
			if p.Artifacts[i].Name == name {
				p.Artifacts[i] = item
				replaced = true
				break
			}
		}
		if !replaced {
			p.Artifacts = append(p.Artifacts, item)
		}
		if err := s.save(); err != nil {
			s.mu.Unlock()
			writeError(w, 502, err.Error())
			return
		}
		arts = append([]artifactJSON(nil), p.Artifacts...)
		break
	}
	s.mu.Unlock()
	if !found {
		writeError(w, 404, "not found")
		return
	}
	s.broadcast(projectID, map[string]any{"type": "artifact", "artifacts": arts})
	if body.Kind == "report" {
		s.publishDocument(r.Context(), projectID, name, name, string(raw), "text/markdown")
	}
	writeJSON(w, 201, map[string]any{
		"name": name,
		"kind": body.Kind,
		"url":  "/api/projects/" + projectID + "/artifacts/" + name,
	})
}

func (s *Server) getArtifact(w http.ResponseWriter, r *http.Request) {
	projectID := r.PathValue("id")
	name := r.PathValue("name")
	s.mu.Lock()
	var kind string
	found := false
	for _, p := range s.projects {
		if p.ID != projectID {
			continue
		}
		for _, art := range p.Artifacts {
			if art.Name == name {
				kind = art.Kind
				found = true
				break
			}
		}
	}
	s.mu.Unlock()
	if !found {
		writeError(w, 404, "not found")
		return
	}
	path, err := s.artifactPath(projectID, name)
	if err != nil {
		writeError(w, 400, err.Error())
		return
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		writeError(w, 404, "not found")
		return
	}
	if kind == "deck" {
		w.Header().Set("Content-Type", "application/vnd.openxmlformats-officedocument.presentationml.presentation")
	} else {
		w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
	}
	w.Header().Set("Content-Disposition", "inline; filename=\""+name+"\"")
	w.WriteHeader(200)
	_, _ = w.Write(raw)
}

func (s *Server) artifactPath(projectID, name string) (string, error) {
	if s.path == "" {
		return "", errors.New("no state path")
	}
	clean, err := artifactName(name, "")
	if err != nil {
		return "", err
	}
	return filepath.Join(filepath.Dir(s.path), "artifacts", projectID, clean), nil
}

func artifactName(name, kind string) (string, error) {
	name = filepath.Base(strings.TrimSpace(name))
	if name == "." || name == ".." || strings.Contains(name, "/") || strings.Contains(name, "\\") {
		return "", errors.New("bad artifact name")
	}
	for _, r := range name {
		ok := r == '.' || r == '_' || r == '-' || (r >= '0' && r <= '9') || (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z')
		if !ok {
			return "", errors.New("bad artifact name")
		}
	}
	if len(name) < 4 || len(name) > 80 {
		return "", errors.New("bad artifact name")
	}
	switch kind {
	case "":
	case "report":
		if !strings.HasSuffix(name, ".md") {
			return "", errors.New("report name must end with .md")
		}
	case "deck":
		if !strings.HasSuffix(name, ".pptx") {
			return "", errors.New("deck name must end with .pptx")
		}
	default:
		return "", errors.New("kind must be report or deck")
	}
	return name, nil
}

func checkSkills(spec *swarm.Spec) error {
	var needed []string
	for _, agent := range spec.Agents {
		needed = append(needed, agent.Skills...)
	}
	if len(needed) == 0 {
		return nil
	}
	dir := os.Getenv("SKILLS_DIR")
	if dir == "" {
		dir = "skills"
	}
	known, err := swarm.SkillNames(dir)
	if err != nil {
		return err
	}
	for _, name := range needed {
		if _, ok := known[name]; !ok {
			return errors.New("unknown skill " + name)
		}
	}
	return nil
}

func normalizeSeeds(in []seedJSON) ([]seedJSON, error) {
	if len(in) == 0 {
		return nil, nil
	}
	if len(in) > 20 {
		return nil, errors.New("at most 20 seeds")
	}
	var out []seedJSON
	for _, seed := range in {
		switch seed.Kind {
		case "text", "link", "file", "image":
		default:
			return nil, errors.New("seed kind must be text, link, file, or image")
		}
		title := strings.TrimSpace(seed.Title)
		body := strings.TrimSpace(seed.Body)
		if title == "" && body == "" {
			continue
		}
		if title == "" {
			title = body
		}
		if len(title) > 120 {
			title = title[:120]
		}
		if len(body) > 4000 {
			body = body[:4000]
		}
		out = append(out, seedJSON{ID: "k_" + randHex(4), Kind: seed.Kind, Title: title, Body: body})
	}
	return out, nil
}
