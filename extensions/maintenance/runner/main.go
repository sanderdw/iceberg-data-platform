// The Maintenance runner: one run of one database, as the team's automation principal.
//
// It reads /work/job.json and the catalog token from MNT_TOKEN, inspects or maintains the tables the job
// names, each by the Apache Polaris policies the gateway resolved for it, through the platform's Iceberg REST
// catalog and storage with iceberg-go, and writes /work/out/result.json and /work/out/run.log. It never
// reaches anything but the catalog and storage (and, as a Kubernetes Job, the launcher that hands it its job:
// remote.go).
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"maps"
	"net/url"
	"os"
	"path/filepath"
	"runtime/debug"
	"strings"
	"time"

	"github.com/apache/iceberg-go/catalog/rest"
	iceio "github.com/apache/iceberg-go/io"
	_ "github.com/apache/iceberg-go/io/gocloud/s3"
	"github.com/apache/iceberg-go/table"
)

const (
	Version     = "0.0.1"
	icebergGo   = "github.com/apache/iceberg-go"
	defaultWork = "/work"
	// At most this many tables per run, as the gateway resolves them.
	maxTables = 2000
)

func icebergGoVersion() string {
	if info, ok := debug.ReadBuildInfo(); ok {
		for _, dep := range info.Deps {
			if dep.Path == icebergGo {
				return dep.Version
			}
		}
	}
	return "unknown"
}

// storageRegistry reaches the buckets through the platform's internal storage endpoint. Polaris vends the
// endpoint for hosts outside the platform; vended credentials are not bound to a host name.
func storageRegistry(endpoint string) (*iceio.Registry, error) {
	base := iceio.NewRegistry()
	registry := base.Clone()
	for _, scheme := range []string{"s3", "s3a", "s3n"} {
		registry.Unregister(scheme)
		err := registry.Register(scheme, func(ctx context.Context, parsed *url.URL, props map[string]string) (iceio.IO, error) {
			props = maps.Clone(props)
			if props == nil {
				props = map[string]string{}
			}
			props[iceio.S3EndpointURL] = endpoint
			return base.LoadFS(ctx, props, parsed.String())
		})
		if err != nil {
			return nil, err
		}
	}
	return registry, nil
}

func tableName(id table.Identifier) string {
	return strings.Join(id, ".")
}

func run(ctx context.Context, job Job, token string, logger *log.Logger) Result {
	result := Result{Status: "success", Kind: job.Kind, DryRun: job.DryRun, RunnerVersion: Version,
		IcebergGo: icebergGoVersion(), Tables: []TableResult{}}
	fail := func(err error) Result {
		result.Status, result.Error = "failed", err.Error()
		logger.Printf("ERROR %v", err)
		return result
	}
	deadline := time.Unix(job.Deadline, 0)
	if job.Deadline == 0 {
		deadline = time.Now().Add(45 * time.Minute)
	}
	registry, err := storageRegistry(job.StorageEndpoint)
	if err != nil {
		return fail(err)
	}
	cat, err := rest.NewCatalog(ctx, "polaris", job.CatalogURI, rest.WithOAuthToken(token),
		rest.WithWarehouseLocation(job.Warehouse), rest.WithIORegistry(registry))
	if err != nil {
		return fail(fmt.Errorf("connect to the catalog: %w", err))
	}
	defer cat.Close()
	mode := job.Kind
	if job.DryRun {
		mode += " (dry run)"
	}
	logger.Printf("Maintenance runner %s, iceberg-go %s: %s of %d tables in %s", Version, result.IcebergGo, mode,
		len(job.Tables), job.Warehouse)
	for _, jt := range job.Tables {
		name, id := jt.Name, table.Identifier(jt.Identifier)
		if time.Now().After(deadline) {
			result.Add(TableResult{Name: name, Status: "skipped", Error: errDeadline.Error(), Actions: []Action{}})
			continue
		}
		tbl, err := cat.LoadTable(ctx, id)
		if err != nil {
			logger.Printf("%s: %v", name, err)
			result.Add(TableResult{Name: name, Status: "failed", Error: "load: " + err.Error(), Actions: []Action{}})
			continue
		}
		maintainer := Maintainer{Job: job, Policies: jt.Policies, Log: logger, Retry: DefaultRetry,
			Reload: func(ctx context.Context) (*table.Table, error) { return cat.LoadTable(ctx, id) }}
		if job.Kind == KindInspect || !jt.Policies.AnyEnabled() {
			entry := TableResult{Name: name, Status: "success", Actions: []Action{}}
			if job.Kind == KindMaintain {
				// No policy of any type runs here: read its health, change nothing.
				entry.Status, entry.Actions = "skipped", maintainer.Off()
			}
			health, _, err := Inspect(ctx, tbl, jt.Policies, nil)
			entry.Health = health
			if err != nil {
				entry.Status, entry.Error = "failed", err.Error()
			}
			logger.Printf("%s: %s", name, describe(health))
			result.Add(entry)
			continue
		}
		entry, totals := maintainer.Maintain(ctx, tbl, deadline)
		result.Totals.add(totals)
		result.Add(entry)
	}
	switch {
	case result.Totals.Failed > 0 && result.Totals.Failed == result.Totals.Tables:
		result.Status = "failed"
		result.Error = "Every table failed. Read the run log."
	case result.Totals.Failed > 0:
		result.Status = "partial"
	}
	logger.Printf("Done: %d tables, %d failed", result.Totals.Tables, result.Totals.Failed)
	return result
}

func describe(h *Health) string {
	if h == nil {
		return "no health"
	}
	return fmt.Sprintf("%d snapshots, %d data files (%s), %d small, %d delete files, %d manifests",
		h.Snapshots, h.DataFiles, human(h.DataBytes), h.SmallFiles,
		h.PositionDeleteFiles+h.EqualityDeleteFiles+h.DeletionVectors, h.Manifests)
}

func write(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}

func main() {
	work := os.Getenv("MNT_WORK_DIR")
	if work == "" {
		work = defaultWork
	}
	remote, err := RemoteFromEnv()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if remote == nil {
		os.Exit(execute(work, nil))
	}
	// On Kubernetes: fetch the staged work directory, run, and always return out/ to the launcher.
	ctx := context.Background()
	fetched := remote.Fetch(ctx, work)
	if fetched != nil {
		fetched = fmt.Errorf("fetch the job from the launcher: %w", fetched)
	}
	code := execute(work, fetched)
	if err := remote.Upload(ctx, work); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR returning the results to the launcher: %v\n", err)
		if code == 0 {
			code = 2
		}
	}
	os.Exit(code)
}

// execute runs the job in work and writes out/result.json and out/run.log. A setup error fails the run
// before the job is read. It returns the process's exit code.
func execute(work string, setup error) int {
	out := filepath.Join(work, "out")
	if err := os.MkdirAll(out, 0o755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	logFile, err := os.Create(filepath.Join(out, "run.log"))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	defer logFile.Close()
	logger := log.New(io.MultiWriter(os.Stdout, logFile), "", log.LstdFlags|log.LUTC)

	var job Job
	err = setup
	if err == nil {
		job, err = LoadJob(filepath.Join(work, "job.json"))
	}
	token := os.Getenv("MNT_TOKEN")
	if err == nil && token == "" {
		err = errors.New("MNT_TOKEN is not set")
	}
	var result Result
	if err != nil {
		logger.Printf("ERROR %v", err)
		result = Result{Status: "failed", Error: err.Error(), RunnerVersion: Version, Tables: []TableResult{}}
	} else {
		ctx, cancel := context.WithDeadline(context.Background(), time.Unix(job.Deadline, 0).Add(5*time.Minute))
		if job.Deadline == 0 {
			ctx, cancel = context.WithTimeout(context.Background(), time.Hour)
		}
		result = run(ctx, job, token, logger)
		cancel()
	}
	if err := write(filepath.Join(out, "result.json"), result); err != nil {
		logger.Printf("ERROR writing the result: %v", err)
		return 2
	}
	if result.Status == "failed" {
		return 1
	}
	return 0
}
