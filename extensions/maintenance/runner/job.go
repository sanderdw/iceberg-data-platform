// The job the gateway stages in /work/job.json, and the result this runner writes to /work/out/result.json.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
)

const (
	KindInspect  = "inspect"
	KindMaintain = "maintain"
	// Orphan files younger than this are never removed, whatever the policy says: a write in progress
	// has files that no snapshot references yet.
	MinOrphanAge = 24 * time.Hour
)

// Applied names the Apache Polaris policy a setting comes from, as the gateway resolved it for the table.
type Applied struct {
	Name      string `json:"name"`
	Namespace string `json:"namespace"`
	Version   int    `json:"version"`
	AppliedAt string `json:"appliedAt"` // catalog, namespace or table
}

// Source is what every policy type carries: the policy (nil when none applies) and, when the gateway did not
// apply it, why.
type Source struct {
	Policy  *Applied `json:"policy"`
	Problem string   `json:"problem"`
}

// SnapshotExpiry is system.snapshot-expiry.
type SnapshotExpiry struct {
	Source
	Enabled            bool `json:"enabled"`
	MaxSnapshotAgeDays int  `json:"maxSnapshotAgeDays"`
	MinSnapshotsToKeep int  `json:"minSnapshotsToKeep"`
}

// DataCompaction is system.data-compaction, which includes removing dangling delete files.
type DataCompaction struct {
	Source
	Enabled               bool    `json:"enabled"`
	TargetFileSizeBytes   int64   `json:"targetFileSizeBytes"`
	MinInputFiles         uint    `json:"minInputFiles"`
	DeleteRatioThreshold  float64 `json:"deleteRatioThreshold"`
	RemoveDanglingDeletes bool    `json:"removeDanglingDeletes"`
	MaxInputBytesPerRun   int64   `json:"maxInputBytesPerRun"`
}

// MetadataCompaction is system.metadata-compaction: rewriting manifests.
type MetadataCompaction struct {
	Source
	Enabled         bool `json:"enabled"`
	MinCountToMerge int  `json:"minCountToMerge"`
}

// OrphanFileRemoval is system.orphan-file-removal.
type OrphanFileRemoval struct {
	Source
	Enabled                bool `json:"enabled"`
	MaxOrphanFileAgeInDays int  `json:"maxOrphanFileAgeInDays"`
}

// Policies are the policies that apply to one table.
type Policies struct {
	SnapshotExpiry     SnapshotExpiry     `json:"snapshotExpiry"`
	DataCompaction     DataCompaction     `json:"dataCompaction"`
	MetadataCompaction MetadataCompaction `json:"metadataCompaction"`
	OrphanFileRemoval  OrphanFileRemoval  `json:"orphanFileRemoval"`
}

// JobTable is one table of the run, with the policies Polaris resolved for it.
type JobTable struct {
	Name       string   `json:"name"`
	Identifier []string `json:"identifier"`
	Policies   Policies `json:"policies"`
}

type Job struct {
	RunID            string     `json:"runId"`
	Kind             string     `json:"kind"`
	DryRun           bool       `json:"dryRun"`
	CatalogURI       string     `json:"catalogUri"`
	Warehouse        string     `json:"warehouse"`
	StorageEndpoint  string     `json:"storageEndpoint"`
	Tables           []JobTable `json:"tables"`
	Deadline         int64      `json:"deadline"`
	URL              string     `json:"url"`
	ExtensionVersion string     `json:"extensionVersion"`
}

func LoadJob(path string) (Job, error) {
	var job Job
	data, err := os.ReadFile(path)
	if err != nil {
		return job, err
	}
	if err := json.Unmarshal(data, &job); err != nil {
		return job, fmt.Errorf("job.json: %w", err)
	}
	return job, job.Validate()
}

// Validate repeats the gateway's checks: the runner never trusts its input more than the gateway does.
func (j Job) Validate() error {
	if j.Kind != KindInspect && j.Kind != KindMaintain {
		return fmt.Errorf("unknown job kind %q", j.Kind)
	}
	if j.CatalogURI == "" || j.Warehouse == "" || j.StorageEndpoint == "" {
		return errors.New("the job needs catalogUri, warehouse and storageEndpoint")
	}
	if len(j.Tables) > maxTables {
		return fmt.Errorf("the job names more than %d tables", maxTables)
	}
	for _, t := range j.Tables {
		if len(t.Identifier) < 2 || strings.Join(t.Identifier, ".") != t.Name {
			return fmt.Errorf("table %q needs an identifier of a namespace and a name", t.Name)
		}
		if err := t.Policies.Validate(); err != nil {
			return fmt.Errorf("%s: %w", t.Name, err)
		}
	}
	return nil
}

// AnyEnabled reports whether a policy of any type runs on the table.
func (p Policies) AnyEnabled() bool {
	return p.SnapshotExpiry.Enabled && p.SnapshotExpiry.Problem == "" ||
		p.DataCompaction.Enabled && p.DataCompaction.Problem == "" ||
		p.MetadataCompaction.Enabled && p.MetadataCompaction.Problem == "" ||
		p.OrphanFileRemoval.Enabled && p.OrphanFileRemoval.Problem == ""
}

// Validate checks the settings of every enabled type.
func (p Policies) Validate() error {
	if e := p.SnapshotExpiry; e.Enabled && (e.MaxSnapshotAgeDays < 1 || e.MinSnapshotsToKeep < 1) {
		return errors.New("snapshotExpiry needs maxSnapshotAgeDays and minSnapshotsToKeep of at least 1")
	}
	if o := p.OrphanFileRemoval; o.Enabled && time.Duration(o.MaxOrphanFileAgeInDays)*24*time.Hour < MinOrphanAge {
		return errors.New("orphanFileRemoval.maxOrphanFileAgeInDays must be at least 1")
	}
	if c := p.DataCompaction; c.Enabled && (c.TargetFileSizeBytes < 1 || c.MaxInputBytesPerRun < c.TargetFileSizeBytes) {
		return errors.New("dataCompaction needs a target file size and a budget of at least one target file")
	}
	if r := p.DataCompaction.DeleteRatioThreshold; r < 0 || r > 1 {
		return errors.New("dataCompaction.deleteRatioThreshold must be between 0 and 1")
	}
	return nil
}

// Action is one operation on one table.
type Action struct {
	Operation string         `json:"operation"`
	Status    string         `json:"status"` // done, planned, skipped, off, failed
	Detail    string         `json:"detail"`
	Metrics   map[string]any `json:"metrics,omitempty"`
	// The policy that decided this action; absent when no policy of its type applies.
	Policy *Applied `json:"policy,omitempty"`
	// Retries counts the commits Polaris refused as conflicts that the runner planned and tried again (retry.go).
	Retries int `json:"retries,omitempty"`
}

type TableResult struct {
	Name    string   `json:"name"`
	Status  string   `json:"status"` // success, skipped, failed
	Error   string   `json:"error,omitempty"`
	Before  *Health  `json:"before,omitempty"`
	Health  *Health  `json:"health,omitempty"`
	Actions []Action `json:"actions"`
}

type Totals struct {
	Tables             int   `json:"tables"`
	Failed             int   `json:"failed"`
	CommitRetries      int   `json:"commitRetries"`
	SnapshotsExpired   int   `json:"snapshotsExpired"`
	FilesCompacted     int   `json:"filesCompacted"`
	FilesWritten       int   `json:"filesWritten"`
	BytesCompacted     int64 `json:"bytesCompacted"`
	DeleteFilesRemoved int   `json:"deleteFilesRemoved"`
	OrphanFiles        int   `json:"orphanFiles"`
	OrphanBytes        int64 `json:"orphanBytes"`
	ManifestsRewritten int   `json:"manifestsRewritten"`
}

type Result struct {
	Status        string        `json:"status"` // success, partial, failed
	Kind          string        `json:"kind"`
	DryRun        bool          `json:"dryRun"`
	RunnerVersion string        `json:"runnerVersion"`
	IcebergGo     string        `json:"icebergGo"`
	Tables        []TableResult `json:"tables"`
	Totals        Totals        `json:"totals"`
	Error         string        `json:"error,omitempty"`
}

func (r *Result) Add(t TableResult) {
	r.Tables = append(r.Tables, t)
	r.Totals.Tables++
	if t.Status == "failed" {
		r.Totals.Failed++
	}
}
