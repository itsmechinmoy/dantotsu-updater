package data

import (
	"encoding/json"
	"os"
	"path/filepath"
)

type SWorkflow struct {
	Owner  string `json:"owner"`
	Repo   string `json:"repo"`
	Name   string `json:"name"`
	Branch string `json:"branch"`
}

type Settings struct {
	Workflow SWorkflow `json:"workflow"`
	Delay    int64     `json:"delay"`
}

func GetSettings() Settings {
	file := filepath.Join("data/settings.json")
	data, _ := os.ReadFile(file)

	var settings Settings
	json.Unmarshal(data, &settings)

	return settings
}
