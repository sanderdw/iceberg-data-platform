// Table health from metadata only: snapshots, refs, data and delete files of the current snapshot.
package main

import (
	"context"
	"fmt"
	"time"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
)

const (
	PropertyPrefix   = "iceberg-data-platform."
	maxSnapshotAge   = "history.expire.max-snapshot-age-ms"
	minSnapshotsKeep = "history.expire.min-snapshots-to-keep"
)

// SizeBuckets are the upper bounds (exclusive) of the file-size histogram, in bytes; the last bucket is open.
var SizeBuckets = []int64{1 << 20, 4 << 20, 16 << 20, 64 << 20, 128 << 20, 256 << 20}

type Health struct {
	FormatVersion     int      `json:"formatVersion"`
	Snapshots         int      `json:"snapshots"`
	OldestSnapshotMs  int64    `json:"oldestSnapshotMs"`
	CurrentSnapshotMs int64    `json:"currentSnapshotMs"`
	CurrentSnapshotID int64    `json:"currentSnapshotId"`
	Branches          int      `json:"branches"`
	Tags              []string `json:"tags"`
	// Snapshots per day for the last 14 days, oldest first.
	SnapshotDays        []int  `json:"snapshotDays"`
	ExpirableSnapshots  int    `json:"expirableSnapshots"`
	DataFiles           int    `json:"dataFiles"`
	DataBytes           int64  `json:"dataBytes"`
	Records             int64  `json:"records"`
	SmallFiles          int    `json:"smallFiles"`
	SmallFileThreshold  int64  `json:"smallFileThreshold"`
	SizeHistogram       []int  `json:"sizeHistogram"`
	CompactableFiles    int    `json:"compactableFiles"`
	CompactableBytes    int64  `json:"compactableBytes"`
	EstimatedFilesAfter int    `json:"estimatedFilesAfter"`
	PositionDeleteFiles int    `json:"positionDeleteFiles"`
	EqualityDeleteFiles int    `json:"equalityDeleteFiles"`
	DeletionVectors     int    `json:"deletionVectors"`
	Manifests           int    `json:"manifests"`
	GCEnabled           bool   `json:"gcEnabled"`
	SortOrder           bool   `json:"sortOrder"`
	PartitionFields     int    `json:"partitionFields"`
	Producer            string `json:"producer"`
	// Table properties that override the policy's snapshot expiry, as Iceberg defines them.
	ExpiryOverrides map[string]string `json:"expiryOverrides"`
	Location        string            `json:"location"`
}

func countRefs(meta table.Metadata) (branches int, tags []string) {
	tags = []string{}
	for name, ref := range meta.Refs() {
		if ref.Type == table.TagRef {
			tags = append(tags, name)
		} else {
			branches++
		}
	}
	return branches, tags
}

func snapshotDays(snapshots []table.Snapshot, now time.Time) []int {
	days := make([]int, 14)
	start := now.Add(-14 * 24 * time.Hour)
	for _, s := range snapshots {
		at := time.UnixMilli(s.TimestampMs)
		if at.Before(start) || at.After(now) {
			continue
		}
		index := int(at.Sub(start) / (24 * time.Hour))
		if index > 13 {
			index = 13
		}
		days[index]++
	}
	return days
}

func bucket(size int64) int {
	for i, bound := range SizeBuckets {
		if size < bound {
			return i
		}
	}
	return len(SizeBuckets)
}

// Inspect reads a table's health, judged by the settings of its policies whether they are on or not. tasks
// are the current snapshot's scan tasks (nil plans them here).
func Inspect(ctx context.Context, tbl *table.Table, policies Policies, tasks []table.FileScanTask) (*Health, []table.FileScanTask, error) {
	meta := tbl.Metadata()
	props := tbl.Properties()
	spec := meta.PartitionSpec()
	h := &Health{
		FormatVersion: meta.FormatVersion(), GCEnabled: table.IsGCEnabled(props), Location: tbl.Location(),
		SortOrder: !tbl.SortOrder().IsUnsorted(), PartitionFields: spec.NumFields(),
		Producer: props[PropertyPrefix+"producer"], ExpiryOverrides: map[string]string{},
		SizeHistogram: make([]int, len(SizeBuckets)+1),
	}
	for _, key := range []string{maxSnapshotAge, minSnapshotsKeep} {
		if value, ok := props[key]; ok {
			h.ExpiryOverrides[key] = value
		}
	}
	snapshots := meta.Snapshots()
	h.Snapshots = len(snapshots)
	for _, s := range snapshots {
		if h.OldestSnapshotMs == 0 || s.TimestampMs < h.OldestSnapshotMs {
			h.OldestSnapshotMs = s.TimestampMs
		}
	}
	h.Branches, h.Tags = countRefs(meta)
	h.SnapshotDays = snapshotDays(snapshots, time.Now())
	current := tbl.CurrentSnapshot()
	if current == nil {
		return h, nil, nil
	}
	h.CurrentSnapshotMs, h.CurrentSnapshotID = current.TimestampMs, current.SnapshotID

	fs, err := tbl.FS(ctx)
	if err != nil {
		return nil, nil, fmt.Errorf("open storage: %w", err)
	}
	manifests, err := current.Manifests(ctx, fs)
	if err != nil {
		return nil, nil, fmt.Errorf("read manifests: %w", err)
	}
	h.Manifests = len(manifests)

	if tasks == nil {
		scan := tbl.Scan()
		tasks, err = scan.PlanFiles(ctx)
		scan.Close()
		if err != nil {
			return nil, nil, fmt.Errorf("plan files: %w", err)
		}
	}
	config := compactionConfig(policies.DataCompaction)
	h.SmallFileThreshold = config.MinFileSizeBytes
	deletes := map[string]iceberg.ManifestEntryContent{}
	for _, task := range tasks {
		size := task.File.FileSizeBytes()
		h.DataFiles++
		h.DataBytes += size
		h.Records += task.File.Count()
		h.SizeHistogram[bucket(size)]++
		if size < config.MinFileSizeBytes {
			h.SmallFiles++
		}
		for _, d := range task.DeleteFiles {
			deletes[d.FilePath()] = iceberg.EntryContentPosDeletes
		}
		for _, d := range task.EqualityDeleteFiles {
			deletes[d.FilePath()] = iceberg.EntryContentEqDeletes
		}
		for _, d := range task.DeletionVectorFiles {
			deletes[d.FilePath()+"#dv"] = iceberg.EntryContentData // counted as a deletion vector below
		}
	}
	for path, content := range deletes {
		switch {
		case len(path) > 3 && path[len(path)-3:] == "#dv":
			h.DeletionVectors++
		case content == iceberg.EntryContentEqDeletes:
			h.EqualityDeleteFiles++
		default:
			h.PositionDeleteFiles++
		}
	}
	if plan, err := config.PlanCompaction(tasks); err == nil {
		for _, group := range plan.Groups {
			h.CompactableFiles += len(group.Tasks)
			h.CompactableBytes += group.TotalSizeBytes
		}
		h.EstimatedFilesAfter = plan.EstOutputFiles
	}
	if expired, err := expirable(tbl, policies.SnapshotExpiry); err == nil {
		h.ExpirableSnapshots = len(expired)
	}
	return h, tasks, nil
}
