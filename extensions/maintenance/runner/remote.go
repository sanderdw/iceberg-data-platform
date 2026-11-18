// Remote mode: a run as a Kubernetes Job (the launcher's LAUNCHER_BACKEND=kubernetes).
//
// There is no docker cp there. The launcher hands the run RUN_URL, RUN_ID and a one-run RUN_TICKET; with
// RUN_URL set, the runner fetches its staged work directory (job.json and an empty out/) from
// GET {RUN_URL}/jobs/{RUN_ID}/input, runs as usual, and returns out/ as a tar with
// PUT {RUN_URL}/jobs/{RUN_ID}/output. Each works once per ticket (launcher/kubernetes.py).
package main

import (
	"archive/tar"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"
)

const (
	envRunURL    = "RUN_URL"
	envRunID     = "RUN_ID"
	envRunTicket = "RUN_TICKET"
	// The staged input is job.json; the launcher refuses more than a few MB.
	maxInput = 64 << 20
	// The launcher refuses larger output (launcher/kubernetes.py MAX_OUTPUT).
	maxOutput = 256 << 20
)

type Remote struct {
	URL    string
	RunID  string
	Ticket string
	Client *http.Client
}

// RemoteFromEnv returns the remote settings when RUN_URL is set, and removes the ticket from the
// environment so nothing the run starts sees it.
func RemoteFromEnv() (*Remote, error) {
	base := os.Getenv(envRunURL)
	if base == "" {
		return nil, nil
	}
	ticket := os.Getenv(envRunTicket)
	os.Unsetenv(envRunTicket)
	id := os.Getenv(envRunID)
	if id == "" || ticket == "" {
		return nil, fmt.Errorf("%s needs %s and %s", envRunURL, envRunID, envRunTicket)
	}
	return &Remote{URL: strings.TrimRight(base, "/"), RunID: id, Ticket: ticket, Client: newClient()}, nil
}

func newClient() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	// The launcher is a Service in the cluster: never through a proxy.
	transport.Proxy = nil
	return &http.Client{Transport: transport, Timeout: 5 * time.Minute}
}

func (r *Remote) call(ctx context.Context, method, name string, body []byte) (*http.Response, error) {
	var reader io.Reader
	if body != nil {
		reader = bytes.NewReader(body)
	}
	request, err := http.NewRequestWithContext(ctx, method, fmt.Sprintf("%s/jobs/%s/%s", r.URL, r.RunID, name), reader)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Authorization", "Bearer "+r.Ticket)
	if body != nil {
		request.Header.Set("Content-Type", "application/x-tar")
	}
	response, err := r.Client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("%s %s: %w", method, name, err)
	}
	if response.StatusCode/100 != 2 {
		detail, _ := io.ReadAll(io.LimitReader(response.Body, 2048))
		response.Body.Close()
		return nil, fmt.Errorf("%s %s: launcher answered %s: %s", method, name, response.Status, strings.TrimSpace(string(detail)))
	}
	return response, nil
}

// Fetch downloads the staged work directory and unpacks it into work.
func (r *Remote) Fetch(ctx context.Context, work string) error {
	response, err := r.call(ctx, http.MethodGet, "input", nil)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	return untar(io.LimitReader(response.Body, maxInput), work)
}

// Upload returns work/out to the launcher as a tar whose entries start with out/.
func (r *Remote) Upload(ctx context.Context, work string) error {
	var buffer bytes.Buffer
	if err := tarDir(&buffer, filepath.Join(work, "out"), "out"); err != nil {
		return fmt.Errorf("pack out/: %w", err)
	}
	if buffer.Len() > maxOutput {
		return fmt.Errorf("out/ is %d bytes, more than the launcher accepts", buffer.Len())
	}
	response, err := r.call(ctx, http.MethodPut, "output", buffer.Bytes())
	if err != nil {
		return err
	}
	response.Body.Close()
	return nil
}

// untar unpacks regular files and directories only, never links, absolute paths or paths outside target.
func untar(reader io.Reader, target string) error {
	archive := tar.NewReader(reader)
	for {
		header, err := archive.Next()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return fmt.Errorf("read the staged input: %w", err)
		}
		name := path.Clean(strings.TrimPrefix(header.Name, "./"))
		if name == "." || path.IsAbs(name) || name == ".." || strings.HasPrefix(name, "../") {
			continue
		}
		destination := filepath.Join(target, filepath.FromSlash(name))
		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(destination, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
				return err
			}
			file, err := os.OpenFile(destination, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o644)
			if err != nil {
				return err
			}
			_, err = io.Copy(file, archive)
			if closeErr := file.Close(); err == nil {
				err = closeErr
			}
			if err != nil {
				return err
			}
		}
	}
}

// tarDir writes dir as prefix/..., with its regular files and directories; links are left out.
func tarDir(writer io.Writer, dir, prefix string) error {
	archive := tar.NewWriter(writer)
	err := filepath.WalkDir(dir, func(file string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !entry.IsDir() && !entry.Type().IsRegular() {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		relative, err := filepath.Rel(dir, file)
		if err != nil {
			return err
		}
		header, err := tar.FileInfoHeader(info, "")
		if err != nil {
			return err
		}
		header.Name = path.Join(prefix, filepath.ToSlash(relative))
		header.Uname, header.Gname = "", ""
		if entry.IsDir() {
			header.Name += "/"
		}
		if err := archive.WriteHeader(header); err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		source, err := os.Open(file)
		if err != nil {
			return err
		}
		defer source.Close()
		_, err = io.Copy(archive, source)
		return err
	})
	if err != nil {
		return err
	}
	return archive.Close()
}
