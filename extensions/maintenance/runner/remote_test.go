package main

import (
	"archive/tar"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

const testRun = "run-bbbbbbbbbbbbbbbbbbbbbbbb"

type tarEntry struct {
	name, body string
	kind       byte
}

func makeTar(t *testing.T, entries ...tarEntry) []byte {
	t.Helper()
	var buffer bytes.Buffer
	archive := tar.NewWriter(&buffer)
	for _, e := range entries {
		header := &tar.Header{Name: e.name, Typeflag: e.kind, Mode: 0o644, Size: int64(len(e.body))}
		switch e.kind {
		case tar.TypeDir:
			header.Mode, header.Size = 0o755, 0
		case tar.TypeSymlink:
			header.Linkname, header.Size = e.body, 0
		}
		if err := archive.WriteHeader(header); err != nil {
			t.Fatal(err)
		}
		if e.kind == tar.TypeReg {
			if _, err := archive.Write([]byte(e.body)); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

func readTar(t *testing.T, data []byte) map[string]string {
	t.Helper()
	found := map[string]string{}
	archive := tar.NewReader(bytes.NewReader(data))
	for {
		header, err := archive.Next()
		if err == io.EOF {
			return found
		}
		if err != nil {
			t.Fatal(err)
		}
		body, _ := io.ReadAll(archive)
		found[header.Name] = string(body)
	}
}

// fakeLauncher serves the two job endpoints as launcher/app.py does: the input once, the output once,
// both only with the run's ticket.
type fakeLauncher struct {
	mu       sync.Mutex
	ticket   string
	input    []byte
	output   []byte
	requests []string
}

func (f *fakeLauncher) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.requests = append(f.requests, r.Method+" "+r.URL.Path)
	if r.Header.Get("Authorization") != "Bearer "+f.ticket {
		http.Error(w, `{"error": "Unknown run or ticket."}`, http.StatusUnauthorized)
		return
	}
	switch {
	case r.Method == http.MethodGet && r.URL.Path == "/jobs/"+testRun+"/input" && f.input != nil:
		w.Header().Set("Content-Type", "application/x-tar")
		w.Write(f.input)
		f.input = nil
	case r.Method == http.MethodPut && r.URL.Path == "/jobs/"+testRun+"/output" && f.output == nil:
		f.output, _ = io.ReadAll(r.Body)
		w.Write([]byte(`{"stored": true}`))
	default:
		http.Error(w, `{"error": "no"}`, http.StatusUnauthorized)
	}
}

func remoteEnv(t *testing.T, url, ticket string) {
	t.Setenv(envRunURL, url+"/")
	t.Setenv(envRunID, testRun)
	t.Setenv(envRunTicket, ticket)
}

func TestWithoutRunURLTheRunnerIsLocal(t *testing.T) {
	t.Setenv(envRunURL, "")
	remote, err := RemoteFromEnv()
	if remote != nil || err != nil {
		t.Fatalf("got %v, %v", remote, err)
	}
}

func TestRemoteNeedsARunIDAndATicket(t *testing.T) {
	t.Setenv(envRunURL, "http://mnt-launcher:3019")
	t.Setenv(envRunID, testRun)
	t.Setenv(envRunTicket, "")
	if _, err := RemoteFromEnv(); err == nil {
		t.Fatal("a remote run without a ticket must fail")
	}
}

func TestARemoteRunFetchesItsJobAndReturnsOut(t *testing.T) {
	job, _ := json.Marshal(map[string]any{"runId": testRun, "kind": "compact"})
	launcher := &fakeLauncher{ticket: "one-run-ticket", input: makeTar(t,
		tarEntry{name: "out", kind: tar.TypeDir},
		tarEntry{name: "job.json", body: string(job), kind: tar.TypeReg},
		// Never unpacked: paths outside the work directory and links.
		tarEntry{name: "../escape", body: "x", kind: tar.TypeReg},
		tarEntry{name: "/etc/escape", body: "x", kind: tar.TypeReg},
		tarEntry{name: "link", body: "/etc/passwd", kind: tar.TypeSymlink},
	)}
	server := httptest.NewServer(launcher)
	defer server.Close()
	remoteEnv(t, server.URL, "one-run-ticket")
	t.Setenv("MNT_TOKEN", "catalog-token")

	remote, err := RemoteFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := os.LookupEnv(envRunTicket); ok {
		t.Fatal("the ticket must leave the environment")
	}
	if remote.URL != server.URL || remote.RunID != testRun {
		t.Fatalf("remote %+v", remote)
	}
	parent := t.TempDir()
	work := filepath.Join(parent, "work")
	if err := remote.Fetch(context.Background(), work); err != nil {
		t.Fatal(err)
	}
	if data, err := os.ReadFile(filepath.Join(work, "job.json")); err != nil || string(data) != string(job) {
		t.Fatalf("job.json: %q, %v", data, err)
	}
	for _, unwanted := range []string{filepath.Join(parent, "escape"), filepath.Join(work, "etc", "escape"), filepath.Join(work, "link")} {
		if _, err := os.Lstat(unwanted); err == nil {
			t.Fatalf("%s must not be unpacked", unwanted)
		}
	}
	// The input is handed out once.
	if err := remote.Fetch(context.Background(), work); err == nil {
		t.Fatal("a second fetch must fail")
	}

	// The job's kind is invalid, so the run fails without a catalog, but it still reports.
	if code := execute(work, nil); code != 1 {
		t.Fatalf("exit code %d", code)
	}
	if err := os.Symlink("/etc/passwd", filepath.Join(work, "out", "passwd")); err != nil {
		t.Fatal(err)
	}
	if err := remote.Upload(context.Background(), work); err != nil {
		t.Fatal(err)
	}
	files := readTar(t, launcher.output)
	if _, ok := files["out/"]; !ok {
		t.Fatalf("no out/ directory in %v", files)
	}
	if _, ok := files["out/passwd"]; ok {
		t.Fatal("links must not be uploaded")
	}
	var result Result
	if err := json.Unmarshal([]byte(files["out/result.json"]), &result); err != nil {
		t.Fatal(err)
	}
	if result.Status != "failed" || !strings.Contains(result.Error, `unknown job kind "compact"`) {
		t.Fatalf("result %+v", result)
	}
	if !strings.Contains(files["out/run.log"], "unknown job kind") {
		t.Fatalf("run.log %q", files["out/run.log"])
	}
	if strings.Contains(files["out/run.log"]+files["out/result.json"], "catalog-token") {
		t.Fatal("the catalog token must not reach the output")
	}
	// The output is accepted once.
	if err := remote.Upload(context.Background(), work); err == nil {
		t.Fatal("a second upload must fail")
	}
}

func TestAFailedFetchIsReportedInTheResult(t *testing.T) {
	launcher := &fakeLauncher{ticket: "right", input: makeTar(t, tarEntry{name: "job.json", body: "{}", kind: tar.TypeReg})}
	server := httptest.NewServer(launcher)
	defer server.Close()
	remoteEnv(t, server.URL, "wrong")
	remote, err := RemoteFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	work := t.TempDir()
	fetched := remote.Fetch(context.Background(), work)
	if fetched == nil || !strings.Contains(fetched.Error(), "401") {
		t.Fatalf("fetch with a wrong ticket: %v", fetched)
	}
	if code := execute(work, fetched); code != 1 {
		t.Fatalf("exit code %d", code)
	}
	var result Result
	data, _ := os.ReadFile(filepath.Join(work, "out", "result.json"))
	if err := json.Unmarshal(data, &result); err != nil || result.Status != "failed" || !strings.Contains(result.Error, "401") {
		t.Fatalf("result %s, %v", data, err)
	}
}
