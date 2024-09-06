package modules

import (
	"archive/zip"
	"fmt"
	"io"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"reflect"
	"regexp"
	"sync"
)

func ReadFile(path string) (string, error) {
	file := filepath.Join(path)
	data, err := os.ReadFile(file)
	if err != nil {
		return "", fmt.Errorf("error reading file: %v", err)
	}

	return string(data), nil
}

func WriteFile(path string, data string) error {
	file := filepath.Join(path)
	err := os.WriteFile(file, []byte(data), 0o644)
	if err != nil {
		return fmt.Errorf("error writing file: %v", err)
	}

	return nil
}

func MakeDir(dir string) error {
	err := os.MkdirAll(dir, 0o755)
	if err != nil {
		return fmt.Errorf("error creating directory: %v", err)
	}

	return nil
}

func RemoveDir(dir string) error {
	err := os.RemoveAll(dir)
	if err != nil {
		return fmt.Errorf("error removing directory: %v", err)
	}

	return nil
}

func DownloadFile(urlStr string, outputDir string) error {
	return Parallel([]string{urlStr}, func(url string) error {
		resp, err := http.Get(url)
		if err != nil {
			return fmt.Errorf("error downloading file: %v", err)
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			return fmt.Errorf("unable to download file %s: %s", url, resp.Status)
		}

		contentDisposition := resp.Header.Get("Content-Disposition")
		regexp := regexp.MustCompile(`filename=\s*(?:"([^"]+)"|([^;]+))`)
		fileName := Filter(regexp.FindStringSubmatch(contentDisposition)[1:], func(group string) bool { return group != "" })[0]
		outFile, err := os.Create(filepath.Join(outputDir, fileName))
		if err != nil {
			return fmt.Errorf("error creating file: %v", err)
		}
		defer outFile.Close()

		_, err = io.Copy(outFile, resp.Body)
		if err != nil {
			return fmt.Errorf("error copying content to file %s: %v", fileName, err)
		}

		fmt.Printf("Downloaded file %s\n", fileName)
		return nil
	})
}

func ExtractFromZip(zipFile string, outputDir string) error {
	reader, err := zip.OpenReader(zipFile)
	if err != nil {
		return fmt.Errorf("unable to open zip file: %v", err)
	}
	defer reader.Close()

	var filesToExtract []string
	for _, file := range reader.File {
		if path.Ext(file.Name) == ".apk" {
			filesToExtract = append(filesToExtract, file.Name)
		}
	}

	err = Parallel(filesToExtract, func(fileName string) error {
		file, err := reader.Open(fileName)
		if err != nil {
			return fmt.Errorf("error opening file %s from zip: %v", fileName, err)
		}
		defer file.Close()

		outFile, err := os.Create(filepath.Join(outputDir, fileName))
		if err != nil {
			return fmt.Errorf("error creating file %s: %v", fileName, err)
		}
		defer outFile.Close()

		_, err = io.Copy(outFile, file)
		if err != nil {
			return fmt.Errorf("error copying content to file %s: %v", fileName, err)
		}

		fmt.Printf("Extracted file %s\n", fileName)
		return nil
	})

	return err
}

func Parallel[TYPE any](data []TYPE, f interface{}) error {
	var wg sync.WaitGroup
	var mu sync.Mutex
	var firstErr error

	funcType := reflect.TypeOf(f)
	numOut := funcType.NumOut()
	errCh := make(chan error, len(data))

	for _, d := range data {
		wg.Add(1)
		go func(d TYPE) {
			defer wg.Done()
			out := reflect.ValueOf(f).Call([]reflect.Value{reflect.ValueOf(d)})
			if numOut == 1 && funcType.Out(0).Kind() == reflect.Interface && funcType.Out(0).String() == "error" {
				if err, ok := out[0].Interface().(error); ok && err != nil {
					errCh <- err
				}
			}
		}(d)
	}

	go func() {
		wg.Wait()
		close(errCh)
	}()

	for err := range errCh {
		mu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		mu.Unlock()
	}

	return firstErr
}

func Filter[TYPE any](data []TYPE, f func(TYPE) bool) []TYPE {
	var result []TYPE
	for _, d := range data {
		if f(d) {
			result = append(result, d)
		}
	}
	return result
}
