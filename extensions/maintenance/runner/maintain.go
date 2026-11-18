// The maintenance operations, in the order a run applies them to one table.
package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"time"

	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/table"
	"github.com/apache/iceberg-go/table/compaction"
	"github.com/apache/iceberg-go/table/maintenance"
)

func compactionConfig(p CompactionPolicy) compaction.Config {
	config := compaction.DefaultConfig()
	if p.TargetFileSizeMB > 0 {
		config.TargetFileSizeBytes = p.TargetFileSizeMB << 20
		config.MinFileSizeBytes = config.TargetFileSizeBytes * 3 / 4
		config.MaxFileSizeBytes = config.TargetFileSizeBytes * 9 / 5
	}
	if p.MinInputFiles > 0 {
		config.MinInputFiles = p.MinInputFiles
	}
	config.DeleteRatioThreshold = p.DeleteRatio
	return config
}

// expirable lists the snapshots an expiry with this policy would remove, without committing anything.
func expirable(tbl *table.Table, p ExpirePolicy) ([]table.Snapshot, error) {
	if !p.Enabled || !table.IsGCEnabled(tbl.Properties()) || tbl.CurrentSnapshot() == nil {
		return nil, nil
	}
	tx := tbl.NewTransaction()
	if err := tx.ExpireSnapshots(expireOptions(p, false)...); err != nil {
		return nil, err
	}
	staged, err := tx.StagedTable()
	if err != nil {
		return nil, err
	}
	kept := map[int64]bool{}
	for _, s := range staged.Metadata().Snapshots() {
		kept[s.SnapshotID] = true
	}
	var expired []table.Snapshot
	for _, s := range tbl.Metadata().Snapshots() {
		if !kept[s.SnapshotID] {
			expired = append(expired, s)
		}
	}
	return expired, nil
}

// expireOptions never deletes files at commit: the files expired snapshots leave behind become orphans,
// which orphan cleanup removes in a later run. That later run is the grace period.
func expireOptions(p ExpirePolicy, _ bool) []table.ExpireSnapshotsOpt {
	return []table.ExpireSnapshotsOpt{
		table.WithOlderThan(time.Duration(p.OlderThanHours) * time.Hour),
		table.WithRetainLast(p.RetainLast),
		table.WithPostCommit(false),
	}
}

type Maintainer struct {
	Job   Job
	Props iceberg.Properties
	Log   *log.Logger
}

func (m Maintainer) snapshotProps(operation string) iceberg.Properties {
	props := iceberg.Properties{
		PropertyPrefix + "maintenance.run-id":    m.Job.RunID,
		PropertyPrefix + "maintenance.operation": operation,
	}
	if m.Job.URL != "" {
		props[PropertyPrefix+"maintenance.url"] = m.Job.URL
	}
	return props
}

func off(operation string) Action {
	return Action{Operation: operation, Status: "off", Detail: "Off in the policy."}
}

// Compact rewrites small files and files with many deletes, bin-packed per partition, and sorted when
// the table has a sort order. It never deletes files: the replaced ones leave with snapshot expiry.
func (m Maintainer) Compact(ctx context.Context, tbl *table.Table, tasks []table.FileScanTask) (*table.Table, Action, Totals) {
	const op = "compaction"
	var totals Totals
	p := m.Job.Policy.Compaction
	if !p.Enabled {
		return tbl, off(op), totals
	}
	config := compactionConfig(p)
	plan, err := config.PlanCompaction(tasks)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error()}, totals
	}
	if len(plan.Groups) == 0 {
		return tbl, Action{Operation: op, Status: "skipped", Detail: fmt.Sprintf(
			"No files need compaction (%d files, %d already a good size).", plan.TotalInputFiles, plan.SkippedFiles)}, totals
	}
	// The budget keeps one run's work, and the runner's memory, bounded; the rest waits for the next run.
	budget := p.MaxInputMBPerRun << 20
	var groups []table.CompactionTaskGroup
	var files int
	var bytes int64
	for _, g := range plan.Groups {
		if len(groups) > 0 && bytes+g.TotalSizeBytes > budget {
			break
		}
		groups = append(groups, table.CompactionTaskGroup{PartitionKey: g.PartitionKey, Tasks: g.Tasks,
			TotalSizeBytes: g.TotalSizeBytes})
		files += len(g.Tasks)
		bytes += g.TotalSizeBytes
	}
	left := len(plan.Groups) - len(groups)
	metrics := map[string]any{"groups": len(groups), "inputFiles": files, "inputBytes": bytes,
		"groupsLeft": left, "estimatedOutputFiles": plan.EstOutputFiles}
	sorted := !tbl.SortOrder().IsUnsorted()
	strategy := "bin-pack"
	if sorted {
		strategy = "sort"
	}
	metrics["strategy"] = strategy
	if m.Job.DryRun {
		detail := fmt.Sprintf("Would rewrite %d files (%s) in %d groups with %s.", files, human(bytes), len(groups), strategy)
		if left > 0 {
			detail += fmt.Sprintf(" %d more groups wait for a later run.", left)
		}
		return tbl, Action{Operation: op, Status: "planned", Detail: detail, Metrics: metrics}, totals
	}
	options := table.RewriteDataFilesOptions{SnapshotProps: m.snapshotProps(op)}
	if sorted {
		options.GroupOptions = append(options.GroupOptions, table.WithCompactionSortOrder(tbl.SortOrder()))
	}
	// Equality deletes that apply to no file after the rewrite leave with it, as in the iceberg CLI.
	if current := tbl.CurrentSnapshot(); current != nil {
		rewritten := map[string]struct{}{}
		for _, g := range groups {
			for _, task := range g.Tasks {
				rewritten[task.File.FilePath()] = struct{}{}
			}
		}
		if fs, err := tbl.FS(ctx); err == nil {
			if dead, err := compaction.CollectDeadEqualityDeletesWithSpecs(ctx, fs, tbl.Metadata(), current, rewritten); err == nil {
				options.ExtraDeleteFilesToRemove = dead
			}
		}
	}
	result, err := tbl.RewriteDataFiles(ctx, groups, options)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}, totals
	}
	removedDeletes := result.RemovedPositionDeleteFiles + result.RemovedEqualityDeleteFiles + result.RemovedDeletionVectorFiles
	totals.FilesCompacted, totals.FilesWritten = result.RemovedDataFiles, result.AddedDataFiles
	totals.BytesCompacted, totals.DeleteFilesRemoved = result.BytesBefore, removedDeletes
	metrics["removedDataFiles"], metrics["addedDataFiles"] = result.RemovedDataFiles, result.AddedDataFiles
	metrics["bytesBefore"], metrics["bytesAfter"] = result.BytesBefore, result.BytesAfter
	metrics["removedDeleteFiles"] = removedDeletes
	detail := fmt.Sprintf("Rewrote %d files into %d (%s → %s) with %s.", result.RemovedDataFiles,
		result.AddedDataFiles, human(result.BytesBefore), human(result.BytesAfter), strategy)
	if removedDeletes > 0 {
		detail += fmt.Sprintf(" Applied and removed %d delete files.", removedDeletes)
	}
	if left > 0 {
		detail += fmt.Sprintf(" %d more groups wait for a later run.", left)
	}
	if result.Table != nil {
		tbl = result.Table
	}
	return tbl, Action{Operation: op, Status: "done", Detail: detail, Metrics: metrics}, totals
}

// RemoveDanglingDeletes drops delete files that no live data file needs any more: metadata only.
func (m Maintainer) RemoveDanglingDeletes(ctx context.Context, tbl *table.Table) (*table.Table, Action, Totals) {
	const op = "danglingDeletes"
	var totals Totals
	if !m.Job.Policy.DanglingDeletes.Enabled {
		return tbl, off(op), totals
	}
	if tbl.Metadata().FormatVersion() < 2 || tbl.CurrentSnapshot() == nil {
		return tbl, Action{Operation: op, Status: "skipped", Detail: "This table has no delete files."}, totals
	}
	dangling, err := tbl.PlanDanglingDeleteFiles(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error()}, totals
	}
	if len(dangling) == 0 {
		return tbl, Action{Operation: op, Status: "skipped", Detail: "No dangling delete files."}, totals
	}
	metrics := map[string]any{"deleteFiles": len(dangling)}
	if m.Job.DryRun {
		return tbl, Action{Operation: op, Status: "planned", Metrics: metrics,
			Detail: fmt.Sprintf("Would remove %d dangling delete files from the metadata.", len(dangling))}, totals
	}
	result, err := tbl.RemoveDanglingDeleteFiles(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}, totals
	}
	totals.DeleteFilesRemoved = result.RemovedDeleteFiles
	if result.Table != nil {
		tbl = result.Table
	}
	return tbl, Action{Operation: op, Status: "done", Metrics: metrics,
		Detail: fmt.Sprintf("Removed %d dangling delete files from the metadata.", result.RemovedDeleteFiles)}, totals
}

// RewriteManifests merges small manifests, so planning a query reads less metadata.
func (m Maintainer) RewriteManifests(ctx context.Context, tbl *table.Table) (*table.Table, Action, Totals) {
	const op = "rewriteManifests"
	var totals Totals
	p := m.Job.Policy.RewriteManifests
	if !p.Enabled {
		return tbl, off(op), totals
	}
	current := tbl.CurrentSnapshot()
	if current == nil {
		return tbl, Action{Operation: op, Status: "skipped", Detail: "The table has no snapshot."}, totals
	}
	fs, err := tbl.FS(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error()}, totals
	}
	manifests, err := current.Manifests(ctx, fs)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error()}, totals
	}
	metrics := map[string]any{"manifests": len(manifests)}
	if len(manifests) <= p.MinManifests {
		return tbl, Action{Operation: op, Status: "skipped", Metrics: metrics,
			Detail: fmt.Sprintf("%d manifests, at most %d.", len(manifests), p.MinManifests)}, totals
	}
	if m.Job.DryRun {
		return tbl, Action{Operation: op, Status: "planned", Metrics: metrics,
			Detail: fmt.Sprintf("Would merge %d manifests.", len(manifests))}, totals
	}
	tx := tbl.NewTransaction()
	result, err := tx.RewriteManifests(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}, totals
	}
	if result.IsNoOp() {
		return tbl, Action{Operation: op, Status: "skipped", Metrics: metrics,
			Detail: "The manifests are already well sized (" + result.NoOpReason.String() + ")."}, totals
	}
	committed, err := tx.Commit(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}, totals
	}
	totals.ManifestsRewritten = len(result.RewrittenManifests)
	metrics["rewritten"], metrics["added"] = len(result.RewrittenManifests), len(result.AddedManifests)
	return committed, Action{Operation: op, Status: "done", Metrics: metrics, Detail: fmt.Sprintf(
		"Merged %d manifests into %d.", len(result.RewrittenManifests), len(result.AddedManifests))}, totals
}

// RemoveOrphans deletes files under the table's location that no metadata references and that are older
// than the policy's age, never younger than a day. It runs before expiry, so the files an expiry frees
// stay until the next run.
func (m Maintainer) RemoveOrphans(ctx context.Context, tbl *table.Table) Action {
	const op = "orphanFiles"
	p := m.Job.Policy.OrphanFiles
	if !p.Enabled {
		return off(op)
	}
	if !table.IsGCEnabled(tbl.Properties()) {
		return Action{Operation: op, Status: "skipped", Detail: "gc.enabled is false on this table."}
	}
	age := time.Duration(p.OlderThanHours) * time.Hour
	if age < MinOrphanAge {
		age = MinOrphanAge
	}
	plan, err := maintenance.PlanOrphanFiles(ctx, tbl, maintenance.WithCleanupFilesOlderThan(age))
	if err != nil {
		return Action{Operation: op, Status: "failed", Detail: err.Error()}
	}
	files := plan.OrphanFiles()
	metrics := map[string]any{"files": len(files), "bytes": plan.TotalSizeBytes()}
	if len(files) == 0 {
		return Action{Operation: op, Status: "skipped", Metrics: metrics,
			Detail: fmt.Sprintf("No orphan files older than %s.", hours(p.OlderThanHours))}
	}
	if m.Job.DryRun {
		return Action{Operation: op, Status: "planned", Metrics: metrics, Detail: fmt.Sprintf(
			"Would delete %d orphan files (%s).", len(files), human(plan.TotalSizeBytes()))}
	}
	result, err := maintenance.ExecuteOrphanCleanup(ctx, tbl, plan)
	if err != nil {
		return Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}
	}
	metrics["deleted"] = len(result.DeletedFiles)
	return Action{Operation: op, Status: "done", Metrics: metrics, Detail: fmt.Sprintf(
		"Deleted %d orphan files (%s).", len(result.DeletedFiles), human(result.TotalSizeBytes))}
}

// ExpireSnapshots removes snapshots older than the policy's age, keeping the last ones, every branch and
// tag head, and each ref's own retention. Table properties override the policy, as Iceberg defines.
func (m Maintainer) ExpireSnapshots(ctx context.Context, tbl *table.Table) (*table.Table, Action, Totals) {
	const op = "expireSnapshots"
	var totals Totals
	p := m.Job.Policy.ExpireSnapshots
	if !p.Enabled {
		return tbl, off(op), totals
	}
	if !table.IsGCEnabled(tbl.Properties()) {
		return tbl, Action{Operation: op, Status: "skipped", Detail: "gc.enabled is false on this table."}, totals
	}
	expired, err := expirable(tbl, p)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error()}, totals
	}
	metrics := map[string]any{"snapshots": len(expired)}
	if len(expired) == 0 {
		return tbl, Action{Operation: op, Status: "skipped", Metrics: metrics, Detail: fmt.Sprintf(
			"No snapshots older than %s beyond the last %d.", hours(p.OlderThanHours), p.RetainLast)}, totals
	}
	if m.Job.DryRun {
		return tbl, Action{Operation: op, Status: "planned", Metrics: metrics,
			Detail: fmt.Sprintf("Would expire %d snapshots.", len(expired))}, totals
	}
	tx := tbl.NewTransaction()
	if err := tx.ExpireSnapshots(expireOptions(p, true)...); err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}, totals
	}
	committed, err := tx.Commit(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}, totals
	}
	totals.SnapshotsExpired = len(expired)
	return committed, Action{Operation: op, Status: "done", Metrics: metrics, Detail: fmt.Sprintf(
		"Expired %d snapshots. Their files are deleted by orphan cleanup in a later run.", len(expired))}, totals
}

func (t *Totals) add(o Totals) {
	t.SnapshotsExpired += o.SnapshotsExpired
	t.FilesCompacted += o.FilesCompacted
	t.FilesWritten += o.FilesWritten
	t.BytesCompacted += o.BytesCompacted
	t.DeleteFilesRemoved += o.DeleteFilesRemoved
	t.OrphanFiles += o.OrphanFiles
	t.OrphanBytes += o.OrphanBytes
	t.ManifestsRewritten += o.ManifestsRewritten
}

var errDeadline = errors.New("the run's time is up; the remaining tables wait for the next run")

// Maintain applies the policy to one table: compaction, dangling deletes, manifests, orphan files, expiry.
func (m Maintainer) Maintain(ctx context.Context, tbl *table.Table, deadline time.Time) (TableResult, Totals) {
	var totals Totals
	name := tableName(tbl.Identifier())
	result := TableResult{Name: name, Status: "success", Actions: []Action{}}
	before, tasks, err := Inspect(ctx, tbl, m.Job.Policy, nil)
	if err != nil {
		result.Status, result.Error = "failed", err.Error()
		return result, totals
	}
	result.Before = before

	steps := []func() Action{
		func() Action {
			var a Action
			var t Totals
			tbl, a, t = m.Compact(ctx, tbl, tasks)
			totals.add(t)
			return a
		},
		func() Action {
			var a Action
			var t Totals
			tbl, a, t = m.RemoveDanglingDeletes(ctx, tbl)
			totals.add(t)
			return a
		},
		func() Action {
			var a Action
			var t Totals
			tbl, a, t = m.RewriteManifests(ctx, tbl)
			totals.add(t)
			return a
		},
		func() Action {
			a := m.RemoveOrphans(ctx, tbl)
			if a.Status == "done" {
				totals.OrphanFiles += intMetric(a.Metrics, "deleted")
				totals.OrphanBytes += int64Metric(a.Metrics, "bytes")
			}
			return a
		},
		func() Action {
			var a Action
			var t Totals
			tbl, a, t = m.ExpireSnapshots(ctx, tbl)
			totals.add(t)
			return a
		},
	}
	for _, step := range steps {
		if time.Now().After(deadline) {
			result.Actions = append(result.Actions, Action{Operation: "deadline", Status: "skipped", Detail: errDeadline.Error()})
			break
		}
		action := step()
		m.Log.Printf("%s %s: %s %s", name, action.Operation, action.Status, action.Detail)
		result.Actions = append(result.Actions, action)
		if action.Status == "failed" {
			result.Status = "failed"
			if result.Error == "" {
				result.Error = action.Operation + ": " + action.Detail
			}
		}
	}
	after, _, err := Inspect(ctx, tbl, m.Job.Policy, nil)
	if err == nil {
		result.Health = after
	} else {
		result.Health = before
	}
	return result, totals
}

func intMetric(metrics map[string]any, key string) int {
	if v, ok := metrics[key].(int); ok {
		return v
	}
	return 0
}

func int64Metric(metrics map[string]any, key string) int64 {
	if v, ok := metrics[key].(int64); ok {
		return v
	}
	return 0
}

func hours(h int) string {
	if h%24 == 0 {
		if h == 24 {
			return "1 day"
		}
		return fmt.Sprintf("%d days", h/24)
	}
	return fmt.Sprintf("%d hours", h)
}

func human(b int64) string {
	switch {
	case b >= 1<<30:
		return fmt.Sprintf("%.1f GB", float64(b)/float64(1<<30))
	case b >= 1<<20:
		return fmt.Sprintf("%.1f MB", float64(b)/float64(1<<20))
	case b >= 1<<10:
		return fmt.Sprintf("%.1f KB", float64(b)/float64(1<<10))
	default:
		return fmt.Sprintf("%d B", b)
	}
}
