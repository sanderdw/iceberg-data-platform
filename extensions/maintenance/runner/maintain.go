// The maintenance operations, in the order a run applies them to one table, each by the Apache Polaris policy
// of its type that applies to the table.
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

func compactionConfig(p DataCompaction) compaction.Config {
	config := compaction.DefaultConfig()
	if p.TargetFileSizeBytes > 0 {
		config.TargetFileSizeBytes = p.TargetFileSizeBytes
		config.MinFileSizeBytes = config.TargetFileSizeBytes * 3 / 4
		config.MaxFileSizeBytes = config.TargetFileSizeBytes * 9 / 5
	}
	if p.MinInputFiles > 0 {
		config.MinInputFiles = p.MinInputFiles
	}
	config.DeleteRatioThreshold = p.DeleteRatioThreshold
	return config
}

// expirable lists the snapshots an expiry with these settings would remove, without committing anything. It
// ignores whether expiry is on, so that health shows what a policy would expire.
func expirable(tbl *table.Table, p SnapshotExpiry) ([]table.Snapshot, error) {
	if !table.IsGCEnabled(tbl.Properties()) || tbl.CurrentSnapshot() == nil || p.MinSnapshotsToKeep < 1 {
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
func expireOptions(p SnapshotExpiry, _ bool) []table.ExpireSnapshotsOpt {
	return []table.ExpireSnapshotsOpt{
		table.WithOlderThan(time.Duration(p.MaxSnapshotAgeDays) * 24 * time.Hour),
		table.WithRetainLast(p.MinSnapshotsToKeep),
		table.WithPostCommit(false),
	}
}

// Maintainer maintains one table of a job by the policies that apply to it.
type Maintainer struct {
	Job      Job
	Policies Policies
	Log      *log.Logger
	// Reload reads the table again from the catalog, after a commit conflict. nil: no retries.
	Reload func(context.Context) (*table.Table, error)
	Retry  Retry
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

// off is the action of an operation its policy does not run: no policy of its type applies, the policy is off,
// or the gateway did not apply it (problem).
func off(operation, ptype string, s Source) Action {
	switch {
	case s.Problem != "":
		return Action{Operation: operation, Status: "skipped", Policy: s.Policy, Detail: "Not applied: " + s.Problem}
	case s.Policy == nil:
		return Action{Operation: operation, Status: "off", Detail: fmt.Sprintf("No %s policy applies to this table.", ptype)}
	default:
		return Action{Operation: operation, Status: "off", Policy: s.Policy, Detail: fmt.Sprintf(
			"Off in the policy %s, attached to the %s.", s.Policy.Name, s.Policy.AppliedAt)}
	}
}

// Off lists every operation as its policy leaves it, for a table no policy runs on.
func (m Maintainer) Off() []Action {
	p := m.Policies
	return []Action{
		off("compaction", "system.data-compaction", p.DataCompaction.Source),
		off("danglingDeletes", "system.data-compaction", p.DataCompaction.Source),
		off("rewriteManifests", "system.metadata-compaction", p.MetadataCompaction.Source),
		off("orphanFiles", "system.orphan-file-removal", p.OrphanFileRemoval.Source),
		off("expireSnapshots", "system.snapshot-expiry", p.SnapshotExpiry.Source),
	}
}

func (a Action) by(s Source) Action {
	a.Policy = s.Policy
	return a
}

// Compact rewrites small files and files with many deletes, bin-packed per partition, and sorted when
// the table has a sort order. It never deletes files: the replaced ones leave with snapshot expiry. nil
// tasks plans the table's current snapshot, as a retry after a conflict does.
func (m Maintainer) Compact(ctx context.Context, tbl *table.Table, tasks []table.FileScanTask) (*table.Table, Action, Totals, error) {
	const op = "compaction"
	var totals Totals
	p := m.Policies.DataCompaction
	if !p.Enabled || p.Problem != "" {
		return tbl, off(op, "system.data-compaction", p.Source), totals, nil
	}
	if tasks == nil {
		scan := tbl.Scan()
		planned, err := scan.PlanFiles(ctx)
		scan.Close()
		if err != nil {
			return tbl, Action{Operation: op, Status: "failed", Detail: "plan files: " + err.Error()}.by(p.Source), totals, nil
		}
		tasks = planned
	}
	config := compactionConfig(p)
	plan, err := config.PlanCompaction(tasks)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error()}.by(p.Source), totals, nil
	}
	if len(plan.Groups) == 0 {
		return tbl, Action{Operation: op, Status: "skipped", Detail: fmt.Sprintf(
			"No files need compaction (%d files, %d already a good size).", plan.TotalInputFiles, plan.SkippedFiles)}.by(p.Source), totals, nil
	}
	// The budget keeps one run's work, and the runner's memory, bounded; the rest waits for the next run.
	budget := p.MaxInputBytesPerRun
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
		return tbl, Action{Operation: op, Status: "planned", Detail: detail, Metrics: metrics}.by(p.Source), totals, nil
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
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}.by(p.Source), totals, err
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
	return tbl, Action{Operation: op, Status: "done", Detail: detail, Metrics: metrics}.by(p.Source), totals, nil
}

// RemoveDanglingDeletes drops delete files that no live data file needs any more: metadata only. It is part of
// the data-compaction policy (remove-dangling-deletes, as in Iceberg's rewrite_data_files).
func (m Maintainer) RemoveDanglingDeletes(ctx context.Context, tbl *table.Table) (*table.Table, Action, Totals, error) {
	const op = "danglingDeletes"
	var totals Totals
	p := m.Policies.DataCompaction
	if !p.Enabled || p.Problem != "" {
		return tbl, off(op, "system.data-compaction", p.Source), totals, nil
	}
	if !p.RemoveDanglingDeletes {
		return tbl, Action{Operation: op, Status: "off", Detail: "remove-dangling-deletes is false in the policy " +
			p.Policy.Name + "."}.by(p.Source), totals, nil
	}
	if tbl.Metadata().FormatVersion() < 2 || tbl.CurrentSnapshot() == nil {
		return tbl, Action{Operation: op, Status: "skipped", Detail: "This table has no delete files."}.by(p.Source), totals, nil
	}
	dangling, err := tbl.PlanDanglingDeleteFiles(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error()}.by(p.Source), totals, nil
	}
	if len(dangling) == 0 {
		return tbl, Action{Operation: op, Status: "skipped", Detail: "No dangling delete files."}.by(p.Source), totals, nil
	}
	metrics := map[string]any{"deleteFiles": len(dangling)}
	if m.Job.DryRun {
		return tbl, Action{Operation: op, Status: "planned", Metrics: metrics,
			Detail: fmt.Sprintf("Would remove %d dangling delete files from the metadata.", len(dangling))}.by(p.Source), totals, nil
	}
	result, err := tbl.RemoveDanglingDeleteFiles(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}.by(p.Source), totals, err
	}
	totals.DeleteFilesRemoved = result.RemovedDeleteFiles
	if result.Table != nil {
		tbl = result.Table
	}
	return tbl, Action{Operation: op, Status: "done", Metrics: metrics,
		Detail: fmt.Sprintf("Removed %d dangling delete files from the metadata.", result.RemovedDeleteFiles)}.by(p.Source), totals, nil
}

// RewriteManifests merges small manifests, so planning a query reads less metadata: the metadata-compaction
// policy.
func (m Maintainer) RewriteManifests(ctx context.Context, tbl *table.Table) (*table.Table, Action, Totals, error) {
	const op = "rewriteManifests"
	var totals Totals
	p := m.Policies.MetadataCompaction
	if !p.Enabled || p.Problem != "" {
		return tbl, off(op, "system.metadata-compaction", p.Source), totals, nil
	}
	current := tbl.CurrentSnapshot()
	if current == nil {
		return tbl, Action{Operation: op, Status: "skipped", Detail: "The table has no snapshot."}.by(p.Source), totals, nil
	}
	fs, err := tbl.FS(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error()}.by(p.Source), totals, nil
	}
	manifests, err := current.Manifests(ctx, fs)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error()}.by(p.Source), totals, nil
	}
	metrics := map[string]any{"manifests": len(manifests)}
	if len(manifests) <= p.MinCountToMerge {
		return tbl, Action{Operation: op, Status: "skipped", Metrics: metrics,
			Detail: fmt.Sprintf("%d manifests, at most %d.", len(manifests), p.MinCountToMerge)}.by(p.Source), totals, nil
	}
	if m.Job.DryRun {
		return tbl, Action{Operation: op, Status: "planned", Metrics: metrics,
			Detail: fmt.Sprintf("Would merge %d manifests.", len(manifests))}.by(p.Source), totals, nil
	}
	tx := tbl.NewTransaction()
	result, err := tx.RewriteManifests(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}.by(p.Source), totals, nil
	}
	if result.IsNoOp() {
		return tbl, Action{Operation: op, Status: "skipped", Metrics: metrics,
			Detail: "The manifests are already well sized (" + result.NoOpReason.String() + ")."}.by(p.Source), totals, nil
	}
	committed, err := tx.Commit(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}.by(p.Source), totals, err
	}
	totals.ManifestsRewritten = len(result.RewrittenManifests)
	metrics["rewritten"], metrics["added"] = len(result.RewrittenManifests), len(result.AddedManifests)
	return committed, Action{Operation: op, Status: "done", Metrics: metrics, Detail: fmt.Sprintf(
		"Merged %d manifests into %d.", len(result.RewrittenManifests), len(result.AddedManifests))}.by(p.Source), totals, nil
}

// RemoveOrphans deletes files under the table's location that no metadata references and that are older
// than the policy's age, never younger than a day. It runs before expiry, so the files an expiry frees
// stay until the next run. It searches the table's location only: a policy's locations are not used.
func (m Maintainer) RemoveOrphans(ctx context.Context, tbl *table.Table) Action {
	const op = "orphanFiles"
	p := m.Policies.OrphanFileRemoval
	if !p.Enabled || p.Problem != "" {
		return off(op, "system.orphan-file-removal", p.Source)
	}
	if !table.IsGCEnabled(tbl.Properties()) {
		return Action{Operation: op, Status: "skipped", Detail: "gc.enabled is false on this table."}.by(p.Source)
	}
	age := time.Duration(p.MaxOrphanFileAgeInDays) * 24 * time.Hour
	if age < MinOrphanAge {
		age = MinOrphanAge
	}
	plan, err := maintenance.PlanOrphanFiles(ctx, tbl, maintenance.WithCleanupFilesOlderThan(age))
	if err != nil {
		return Action{Operation: op, Status: "failed", Detail: err.Error()}.by(p.Source)
	}
	files := plan.OrphanFiles()
	metrics := map[string]any{"files": len(files), "bytes": plan.TotalSizeBytes()}
	if len(files) == 0 {
		return Action{Operation: op, Status: "skipped", Metrics: metrics,
			Detail: fmt.Sprintf("No orphan files older than %s.", days(int(age/(24*time.Hour))))}.by(p.Source)
	}
	if m.Job.DryRun {
		return Action{Operation: op, Status: "planned", Metrics: metrics, Detail: fmt.Sprintf(
			"Would delete %d orphan files (%s).", len(files), human(plan.TotalSizeBytes()))}.by(p.Source)
	}
	result, err := maintenance.ExecuteOrphanCleanup(ctx, tbl, plan)
	if err != nil {
		return Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}.by(p.Source)
	}
	metrics["deleted"] = len(result.DeletedFiles)
	return Action{Operation: op, Status: "done", Metrics: metrics, Detail: fmt.Sprintf(
		"Deleted %d orphan files (%s).", len(result.DeletedFiles), human(result.TotalSizeBytes))}.by(p.Source)
}

// ExpireSnapshots removes snapshots older than the policy's age, keeping the last ones, every branch and
// tag head, and each ref's own retention. Table properties override the policy, as Iceberg defines.
func (m Maintainer) ExpireSnapshots(ctx context.Context, tbl *table.Table) (*table.Table, Action, Totals, error) {
	const op = "expireSnapshots"
	var totals Totals
	p := m.Policies.SnapshotExpiry
	if !p.Enabled || p.Problem != "" {
		return tbl, off(op, "system.snapshot-expiry", p.Source), totals, nil
	}
	if !table.IsGCEnabled(tbl.Properties()) {
		return tbl, Action{Operation: op, Status: "skipped", Detail: "gc.enabled is false on this table."}.by(p.Source), totals, nil
	}
	expired, err := expirable(tbl, p)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error()}.by(p.Source), totals, nil
	}
	metrics := map[string]any{"snapshots": len(expired)}
	if len(expired) == 0 {
		return tbl, Action{Operation: op, Status: "skipped", Metrics: metrics, Detail: fmt.Sprintf(
			"No snapshots older than %s beyond the last %d.", days(p.MaxSnapshotAgeDays), p.MinSnapshotsToKeep)}.by(p.Source), totals, nil
	}
	if m.Job.DryRun {
		return tbl, Action{Operation: op, Status: "planned", Metrics: metrics,
			Detail: fmt.Sprintf("Would expire %d snapshots.", len(expired))}.by(p.Source), totals, nil
	}
	tx := tbl.NewTransaction()
	if err := tx.ExpireSnapshots(expireOptions(p, true)...); err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}.by(p.Source), totals, nil
	}
	committed, err := tx.Commit(ctx)
	if err != nil {
		return tbl, Action{Operation: op, Status: "failed", Detail: err.Error(), Metrics: metrics}.by(p.Source), totals, err
	}
	totals.SnapshotsExpired = len(expired)
	return committed, Action{Operation: op, Status: "done", Metrics: metrics, Detail: fmt.Sprintf(
		"Expired %d snapshots. Their files are deleted by orphan cleanup in a later run.", len(expired))}.by(p.Source), totals, nil
}

func (t *Totals) add(o Totals) {
	t.CommitRetries += o.CommitRetries
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

// Maintain applies the table's policies: compaction, dangling deletes, manifests, orphan files, expiry. A step
// whose commit conflicts with another writer is planned and committed again (retry.go).
func (m Maintainer) Maintain(ctx context.Context, tbl *table.Table, deadline time.Time) (TableResult, Totals) {
	var totals Totals
	name := tableName(tbl.Identifier())
	result := TableResult{Name: name, Status: "success", Actions: []Action{}}
	before, tasks, err := Inspect(ctx, tbl, m.Policies, nil)
	if err != nil {
		result.Status, result.Error = "failed", err.Error()
		return result, totals
	}
	result.Before = before
	planned := tbl
	committing := func(run step) func() Action {
		return func() Action {
			var a Action
			var t Totals
			tbl, a, t = m.retrying(ctx, tbl, deadline, run)
			totals.add(t)
			return a
		}
	}
	steps := []func() Action{
		committing(func(ctx context.Context, t *table.Table) (*table.Table, Action, Totals, error) {
			// The inspection's plan holds for the table it read; after a conflict, plan the new snapshot.
			if t == planned {
				return m.Compact(ctx, t, tasks)
			}
			return m.Compact(ctx, t, nil)
		}),
		committing(m.RemoveDanglingDeletes),
		committing(m.RewriteManifests),
		func() Action {
			a := m.RemoveOrphans(ctx, tbl)
			if a.Status == "done" {
				totals.OrphanFiles += intMetric(a.Metrics, "deleted")
				totals.OrphanBytes += int64Metric(a.Metrics, "bytes")
			}
			return a
		},
		committing(m.ExpireSnapshots),
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
	after, _, err := Inspect(ctx, tbl, m.Policies, nil)
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

func days(d int) string {
	if d == 1 {
		return "1 day"
	}
	return fmt.Sprintf("%d days", d)
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
