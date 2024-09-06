package main

import (
	"os"
	"strings"
	"time"

	"artifact-downloader/src/data"
	"artifact-downloader/src/modules"
)

func main() {
	token := getTokenArgs()
	if token == "" {
		println("token not provided")
		return
	}

	println("Getting workflow latest run...")
	prevTime := time.Now()
	modules.SetClient(token)
	latestRun, err := modules.GetWorkflowLatestRun()
	if err != nil {
		println(err)
		return
	}

	err = modules.DownloadArtifacts(latestRun)
	if err != nil {
		println(err)
		return
	}

	data.UpdateInfo(data.Info{ElapsedTime: time.Since(prevTime).Seconds(), Status: "success"})
}

func getTokenArgs() string {
	return strings.TrimPrefix(modules.Filter(os.Args[1:], func(arg string) bool {
		return strings.HasPrefix(arg, "--token=")
	})[0], "--token=")
}
