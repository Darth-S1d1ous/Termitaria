package gateway

import (
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/Darth-S1d1ous/Termitaria/internal/swarm"
)

var (
	headingLine = regexp.MustCompile(`(?m)^#{1,3}\s+(.+?)\s*$`)
	urlPattern  = regexp.MustCompile(`https?://[^\s<>"]+`)
)

// reportError 按项目上的成稿规格检查正文。空字符串表示通过。
// urls 是工具结果里出现过的链接，由 worker 附在落盘请求上，不由模型另外申报。
func reportError(body string, d *swarm.Deliverable, urls []string) string {
	if d == nil {
		return ""
	}
	known := map[string]struct{}{}
	for _, link := range urls {
		if cleaned := cleanURL(link); cleaned != "" {
			known[cleaned] = struct{}{}
		}
	}
	found := sectionBodies(body)
	for _, section := range d.Sections {
		text, ok := found[foldHeading(section.Heading)]
		if !ok {
			return "write_report is missing the section " + section.Heading
		}
		if utf8.RuneCountInString(text) < section.MinChars {
			return "write_report section " + section.Heading + " is too short"
		}
	}
	for _, section := range d.Sections {
		if !section.Citations {
			continue
		}
		text := found[foldHeading(section.Heading)]
		if !hasKnownLink(text, known) {
			return "write_report section " + section.Heading + " needs a link that a tool returned"
		}
	}
	if utf8.RuneCountInString(strings.TrimSpace(body)) < d.MinChars {
		return "write_report is too short"
	}
	return ""
}

func sectionBodies(body string) map[string]string {
	found := map[string]string{}
	current := ""
	var buf []string
	flush := func() {
		if current == "" {
			buf = nil
			return
		}
		found[current] = strings.TrimSpace(strings.Join(buf, "\n"))
		buf = nil
	}
	for _, line := range strings.Split(body, "\n") {
		if heading := headingOf(line); heading != "" {
			flush()
			current = foldHeading(heading)
			continue
		}
		buf = append(buf, line)
	}
	flush()
	return found
}

func headingOf(line string) string {
	m := headingLine.FindStringSubmatch(strings.TrimSpace(line))
	if m == nil {
		return ""
	}
	return strings.TrimSpace(m[1])
}

func foldHeading(heading string) string {
	return strings.ToLower(strings.TrimSpace(heading))
}

func linksIn(text string) []string {
	var out []string
	for _, raw := range urlPattern.FindAllString(text, -1) {
		if cleaned := cleanURL(raw); cleaned != "" {
			out = append(out, cleaned)
		}
	}
	return out
}

func cleanURL(raw string) string {
	return strings.TrimRight(strings.TrimSpace(raw), ".,);]>")
}

func hasKnownLink(text string, known map[string]struct{}) bool {
	for _, link := range linksIn(text) {
		if _, ok := known[link]; ok {
			return true
		}
	}
	return false
}
