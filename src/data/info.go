package data

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
)

type IWorkflow struct {
	ID    int64  `json:"id"`
	Title string `json:"title"`
}

type Info struct {
	CommitLog   string    `json:"commit-log"`
	Status      string    `json:"status"`
	ElapsedTime float64   `json:"elapsed-time"`
	Workflow    IWorkflow `json:"workflow"`
}

func GetInfo() Info {
	file := filepath.Join("data/info.json")
	data, _ := os.ReadFile(file)

	var info Info
	json.Unmarshal(data, &info)
	return info
}

func UpdateInfo(info Info) Info {
	oldInfo := GetInfo()
	v := reflect.ValueOf(&info).Elem()
	for i := 0; i < v.NumField(); i++ {
		if v.Field(i).IsZero() {
			v.Field(i).Set(reflect.ValueOf(reflect.ValueOf(&oldInfo).Elem().Field(i).Interface()))
		}
	}

	file := filepath.Join("data/info.json")
	data, _ := json.Marshal(info)
	os.WriteFile(file, data, 0o644)
	return info
}

func GetGitHubToken() string {
	tokenPat := os.Getenv("TOKEN_PAT")
	if tokenPat == "" {
		token_pat_file := filepath.Join("data", "github-token.txt")
		data, _ := os.ReadFile(token_pat_file)
		return string(data)
	}
	return tokenPat
}
