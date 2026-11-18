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

type ExpirePolicy struct {
	Enabled        bool `json:"enabled"`
	OlderThanHours int  `json:"olderThanHours"`
	RetainLast     int  `json:"retainLast"`
}

type CompactionPolicy struct {
	Enabled          bool    `json:"enabled"`
	TargetFileSizeMB int64   `json:"targetFileSizeMb"`
	MinInputFiles    uint    `json:"minInputFiles"`
	DeleteRatio      float64 `json:"deleteRatio"`
	MaxInputMBPerRun int64   `json:"maxInputMbPerRun"`
}

type OrphanPolicy struct {
	Enabled        bool `json:"enabled"`
	OlderThanHours int  `json:"olderThanHours"`
}

type ManifestPolicy struct {
	Enabled      bool `json:"enabled"`
	MinManifests int  `json:"minManifests"`
}

type DeletesPolicy struct {
	Enabled bool `json:"enabled"`
}

type Policy struct {
	ExpireSnapshots  ExpirePolicy     `json:"expireSnapshots"`
	Compaction       CompactionPolicy `json:"compaction"`
	OrphanFiles      OrphanPolicy     `json:"orphanFiles"`
	RewriteManifests ManifestPolicy   `json:"rewriteManifests"`
	DanglingDeletes  DeletesPolicy    `json:"danglingDeletes"`
}

type Job struct {
	RunID            string   `json:"runId"`
	Kind             string   `json:"kind"`
	DryRun           bool     `json:"dryRun"`
	CatalogURI       string   `json:"catalogUri"`
	Warehouse        string   `json:"warehouse"`
	StorageEndpoint  string   `json:"storageEndpoint"`
	Tables           []string `json:"tables"`
	Exclude          []string `json:"exclude"`
	Policy           Policy   `json:"policy"`
	Deadline         int64    `json:"deadline"`
	URL              string   `json:"url"`
	ExtensionVersion string   `json:"extensionVersion"`
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
	p := j.Policy
	if p.ExpireSnapshots.Enabled && (p.ExpireSnapshots.OlderThanHours < 1 || p.ExpireSnapshots.RetainLast < 1) {
		return errors.New("expireSnapshots needs olderThanHours and retainLast of at least 1")
	}
	if p.OrphanFiles.Enabled && time.Duration(p.OrphanFiles.OlderThanHours)*time.Hour < MinOrphanAge {
		return errors.New("orphanFiles.olderThanHours must be at least 24")
	}
	if p.Compaction.Enabled && (p.Compaction.TargetFileSizeMB < 1 || p.Compaction.MaxInputMBPerRun < p.Compaction.TargetFileSizeMB) {
		return errors.New("compaction needs a target file size and a budget of at least one target file")
	}
	if p.Compaction.DeleteRatio < 0 || p.Compaction.DeleteRatio > 1 {
		return errors.New("compaction.deleteRatio must be between 0 and 1")
	}
	return nil
}

// Selected reports whether a table (namespace.table) is part of this job.
func (j Job) Selected(name string) bool {
	if len(j.Tables) == 0 {
		return true
	}
	for _, wanted := range j.Tables {
		if strings.EqualFold(wanted, name) {
			return true
		}
	}
	return false
}

// Excluded reports whether the policy leaves a table alone: its health is read, nothing is changed.
func (j Job) Excluded(name string) bool {
	for _, excluded := range j.Exclude {
		if strings.EqualFold(excluded, name) {
			return true
		}
	}
	return false
}

// Action is one operation on one table.
type Action struct {
	Operation string         `json:"operation"`
	Status    string         `json:"status"` // done, planned, skipped, off, failed
	Detail    string         `json:"detail"`
	Metrics   map[string]any `json:"metrics,omitempty"`
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
