package gateway

import (
	"strings"
	"testing"

	taskv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/task/v1"
)

func TestPlanLabelsFollowTheGoal(t *testing.T) {
	goal := "先想清楚要做什么：\n扫文献 → 出假设 → 设计实验"
	got := planLabels(goal)
	if strings.Join(got, "|") != "扫文献|出假设|设计实验" {
		t.Fatalf("labels = %#v", got)
	}
	steps := newPlan(goal)
	if steps[0].State != "active" || steps[1].State != "pending" || steps[2].State != "pending" {
		t.Fatalf("states = %+v", steps)
	}
	if !strings.Contains(planText(steps), "扫文献 → 出假设 → 设计实验") {
		t.Fatalf("text = %s", planText(steps))
	}
}

func TestPlanLabelsDropTheSentenceTail(t *testing.T) {
	goal := "Given the user's library, produce a plan:\nliterature gap scan → hypothesis → experiment design → methods review, all written into the knowledge graph."
	got := planLabels(goal)
	if strings.Join(got, "|") != "literature gap scan|hypothesis|experiment design|methods review" {
		t.Fatalf("labels = %#v", got)
	}
	text := planText(newPlan(goal))
	if strings.Contains(text, "..") {
		t.Fatalf("text = %s", text)
	}
}

func TestBudgetOrTimeout(t *testing.T) {
	if !budgetOrTimeout(taskv1.TaskStatus_TASK_STATUS_CANCELLED, "") {
		t.Fatal("cancelled should fail the active step")
	}
	if !budgetOrTimeout(taskv1.TaskStatus_TASK_STATUS_FAILED, "BUDGET_EXCEEDED") {
		t.Fatal("budget failure")
	}
	if budgetOrTimeout(taskv1.TaskStatus_TASK_STATUS_FAILED, "WORKER_ERROR") {
		t.Fatal("ordinary worker errors are not a progress failure")
	}
}
