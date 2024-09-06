package modules

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"artifact-downloader/src/data"

	"github.com/google/go-github/v61/github"
	"golang.org/x/oauth2"
)

var (
	workflowInfo = data.GetInfo()

	settings = data.GetSettings()
	owner    = settings.Workflow.Owner
	repo     = settings.Workflow.Repo
	name     = settings.Workflow.Name

	client *github.Client
)

func SetClient(token string) {
	ts := oauth2.StaticTokenSource(
		&oauth2.Token{AccessToken: token, TokenType: "Bearer"},
	)
	tc := oauth2.NewClient(context.Background(), ts)
	client = github.NewClient(tc)
}

func GetWorkflowLatestRun() (int64, error) {
	if client == nil {
		return 0, fmt.Errorf("client not set")
	}

	workflowRuns, _, err := client.Actions.ListWorkflowRunsByFileName(context.Background(), owner, repo, name, &github.ListWorkflowRunsOptions{
		Branch: settings.Workflow.Branch,
		Status: "success",
	})

	if _, ok := err.(*github.RateLimitError); ok {
		data.UpdateInfo(data.Info{Status: "hit rate limit"})
		return 0, fmt.Errorf("hit rate limit")
	}

	if len(workflowRuns.WorkflowRuns) == 0 {
		data.UpdateInfo(data.Info{Status: "no workflow runs found"})
		return 0, fmt.Errorf("no workflow runs found")
	}

	latestRun := workflowRuns.WorkflowRuns[0]
	oldRun := workflowRuns.WorkflowRuns[1]
	if latestRun.GetID() == workflowInfo.Workflow.ID {
		time.Sleep(time.Duration(settings.Delay) * time.Second)
		return GetWorkflowLatestRun()
	}

	fmt.Printf("Workflow run named: \"%s\" found with id %d\n", latestRun.GetDisplayTitle(), latestRun.GetID())
	workflowInfo = data.UpdateInfo(data.Info{
		Workflow: data.IWorkflow{
			ID:    latestRun.GetID(),
			Title: latestRun.GetDisplayTitle(),
		},
	})

	getCommitHistory(oldRun.GetCreatedAt().Time, latestRun.GetCreatedAt().Time)
	return latestRun.GetID(), nil
}

func DownloadArtifacts(runID int64) error {
	artifacts, _, err := client.Actions.ListWorkflowRunArtifacts(context.Background(), owner, repo, runID, &github.ListOptions{})
	if _, ok := err.(*github.RateLimitError); ok {
		data.UpdateInfo(data.Info{Status: "hit rate limit"})
		return fmt.Errorf("hit rate limit")
	}

	MakeDir("archive")
	err = Parallel(artifacts.Artifacts, func(artifact *github.Artifact) error {
		if artifact.GetExpired() {
			data.UpdateInfo(data.Info{Status: "artifacts expired"})
			return fmt.Errorf("artifact expired")
		}

		artifactDownloadUrl, _, err := client.Actions.DownloadArtifact(context.Background(), owner, repo, artifact.GetID(), 0)
		if _, ok := err.(*github.RateLimitError); ok {
			data.UpdateInfo(data.Info{Status: "hit rate limit"})
			return fmt.Errorf("hit rate limit")
		}

		err = DownloadFile(artifactDownloadUrl.String(), "archive")
		if err != nil {
			return err
		}

		err = ExtractFromZip("archive/"+artifact.GetName()+".zip", "archive")
		if err != nil {
			return err
		}

		return nil
	})
	if err != nil {
		return err
	}

	files, _ := os.ReadDir("archive")
	Parallel(files, func(file os.DirEntry) {
		if file.IsDir() || file.Name()[len(file.Name())-4:] != ".apk" {
			os.Remove("archive/" + file.Name())
		}
	})

	println("Artifacts downloaded successfully")
	return nil
}

func getCommitHistory(since, until time.Time) error {
	println("Getting commit history...")
	commits, _, err := client.Repositories.ListCommits(context.Background(), owner, repo, &github.CommitsListOptions{
		SHA:   settings.Workflow.Branch,
		Since: since,
		Until: until,
	})

	if _, ok := err.(*github.RateLimitError); ok {
		data.UpdateInfo(data.Info{Status: "hit rate limit"})
		return fmt.Errorf("hit rate limit")
	}

	var commitLog strings.Builder
	for i, commit := range commits {
		message := strings.TrimFunc(commit.GetCommit().GetMessage(), func(r rune) bool { return r == '\n' || r == '\r' || r == '\t' || r == ' ' })
		author := commit.GetCommit().GetAuthor().GetName()

		if !strings.Contains(author, "(bot)") {
			commitLog.WriteString(fmt.Sprintf("- %s ~%s", message, author))
			if i != len(commits)-1 {
				commitLog.WriteRune('\n')
			}
		}
	}

	workflowInfo = data.UpdateInfo(data.Info{CommitLog: commitLog.String()})
	println("Commit history updated successfully")
	return nil
}

// func GetRateLimit() int {
// 	rateLimit, _, err := client.RateLimit.Get(context.Background());
// 	if _, ok := err.(*github.RateLimitError); ok {
// 		println("hit rate limit")
// 		return 0
// 	}

// 	return rateLimit.Core.Remaining
// }
