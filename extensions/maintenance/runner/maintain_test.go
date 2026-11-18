package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/apache/arrow-go/v18/arrow/array"
	"github.com/apache/arrow-go/v18/arrow/memory"
	"github.com/apache/iceberg-go"
	"github.com/apache/iceberg-go/catalog/hadoop"
	"github.com/apache/iceberg-go/table"
)

func policy() Policy {
	return Policy{
		ExpireSnapshots:  ExpirePolicy{Enabled: true, OlderThanHours: 120, RetainLast: 1},
		Compaction:       CompactionPolicy{Enabled: true, TargetFileSizeMB: 1, MinInputFiles: 2, DeleteRatio: 0.3, MaxInputMBPerRun: 64},
		OrphanFiles:      OrphanPolicy{Enabled: true, OlderThanHours: 72},
		RewriteManifests: ManifestPolicy{Enabled: true, MinManifests: 100},
		DanglingDeletes:  DeletesPolicy{Enabled: true},
	}
}

// smallTable is a table with one snapshot and one small data file per append.
func smallTable(t *testing.T, appends int) *table.Table {
	t.Helper()
	ctx := context.Background()
	cat, err := hadoop.NewCatalog(ctx, "test", t.TempDir(), nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := cat.CreateNamespace(ctx, table.Identifier{"raw"}, nil); err != nil {
		t.Fatal(err)
	}
	schema := iceberg.NewSchema(0,
		iceberg.NestedField{ID: 1, Name: "id", Type: iceberg.PrimitiveTypes.Int64, Required: true},
		iceberg.NestedField{ID: 2, Name: "name", Type: iceberg.PrimitiveTypes.String})
	tbl, err := cat.CreateTable(ctx, table.Identifier{"raw", "readings"}, schema)
	if err != nil {
		t.Fatal(err)
	}
	arrowSchema, err := table.SchemaToArrowSchema(tbl.Schema(), nil, false, false)
	if err != nil {
		t.Fatal(err)
	}
	for i := range appends {
		rows := make([]string, 0, 10)
		for j := range 10 {
			rows = append(rows, fmt.Sprintf(`{"id":%d,"name":"row %d"}`, i*10+j, j))
		}
		data, err := array.TableFromJSON(memory.DefaultAllocator, arrowSchema, []string{"[" + strings.Join(rows, ",") + "]"})
		if err != nil {
			t.Fatal(err)
		}
		tbl, err = tbl.AppendTable(ctx, data, 1000, nil)
		data.Release()
		if err != nil {
			t.Fatal(err)
		}
	}
	return tbl
}

func maintainer(dryRun bool, p Policy) Maintainer {
	return Maintainer{Job: Job{RunID: "run-test", Kind: KindMaintain, DryRun: dryRun, Policy: p},
		Log: log.New(io.Discard, "", 0)}
}

func action(t *testing.T, r TableResult, operation string) Action {
	t.Helper()
	for _, a := range r.Actions {
		if a.Operation == operation {
			return a
		}
	}
	t.Fatalf("no %s action in %+v", operation, r.Actions)
	return Action{}
}

func TestInspectCountsSnapshotsAndSmallFiles(t *testing.T) {
	tbl := smallTable(t, 6)
	h, _, err := Inspect(context.Background(), tbl, policy(), nil)
	if err != nil {
		t.Fatal(err)
	}
	if h.Snapshots != 6 || h.DataFiles != 6 || h.SmallFiles != 6 || h.Records != 60 {
		t.Fatalf("unexpected health %+v", h)
	}
	if h.CompactableFiles != 6 || h.EstimatedFilesAfter != 1 || h.SizeHistogram[0] != 6 {
		t.Fatalf("unexpected compaction view %+v", h)
	}
	if h.SnapshotDays[13] != 6 || h.ExpirableSnapshots != 0 || !h.GCEnabled {
		t.Fatalf("unexpected snapshot view %+v", h)
	}
}

func TestADryRunChangesNothing(t *testing.T) {
	tbl := smallTable(t, 5)
	before := tbl.CurrentSnapshot().SnapshotID
	result, _ := maintainer(true, policy()).Maintain(context.Background(), tbl, time.Now().Add(time.Minute))
	if result.Status != "success" {
		t.Fatalf("dry run failed: %+v", result)
	}
	if a := action(t, result, "compaction"); a.Status != "planned" || !strings.Contains(a.Detail, "Would rewrite 5 files") {
		t.Fatalf("unexpected compaction action %+v", a)
	}
	if result.Health.CurrentSnapshotID != before || result.Health.DataFiles != 5 {
		t.Fatalf("a dry run committed: %+v", result.Health)
	}
}

func TestCompactionRewritesSmallFilesAndKeepsTheRows(t *testing.T) {
	tbl := smallTable(t, 5)
	result, totals := maintainer(false, policy()).Maintain(context.Background(), tbl, time.Now().Add(time.Minute))
	if result.Status != "success" {
		t.Fatalf("maintenance failed: %+v", result)
	}
	if a := action(t, result, "compaction"); a.Status != "done" {
		t.Fatalf("unexpected compaction action %+v", a)
	}
	if totals.FilesCompacted != 5 || totals.FilesWritten != 1 {
		t.Fatalf("unexpected totals %+v", totals)
	}
	if result.Health.DataFiles != 1 || result.Health.Records != 50 || result.Health.Snapshots != 6 {
		t.Fatalf("unexpected health after compaction %+v", result.Health)
	}
	// Fresh snapshots are younger than the policy's age: none expire, so time travel still works.
	if a := action(t, result, "expireSnapshots"); a.Status != "skipped" {
		t.Fatalf("unexpected expiry %+v", a)
	}
}

func TestTheBudgetLeavesGroupsForALaterRun(t *testing.T) {
	p := policy()
	p.Compaction.MinInputFiles = 1
	// One target file of budget, and groups of at most one target each.
	p.Compaction.MaxInputMBPerRun = 1
	tbl := smallTable(t, 3)
	result, _ := maintainer(true, p).Maintain(context.Background(), tbl, time.Now().Add(time.Minute))
	if a := action(t, result, "compaction"); a.Status != "planned" || a.Metrics["groups"].(int) < 1 {
		t.Fatalf("unexpected compaction plan %+v", a)
	}
}

func TestExpiryKeepsTheLastSnapshotsAndLeavesFilesForOrphanCleanup(t *testing.T) {
	ctx := context.Background()
	tbl := smallTable(t, 4)
	p := ExpirePolicy{Enabled: true, OlderThanHours: 0, RetainLast: 2}
	expired, err := expirable(tbl, p)
	if err != nil || len(expired) != 2 {
		t.Fatalf("expected 2 expirable snapshots, got %d (%v)", len(expired), err)
	}
	full := policy()
	full.ExpireSnapshots = p
	full.Compaction.Enabled = false
	m := maintainer(false, full)
	tbl, a, totals := m.ExpireSnapshots(ctx, tbl)
	if a.Status != "done" || totals.SnapshotsExpired != 2 || len(tbl.Metadata().Snapshots()) != 2 {
		t.Fatalf("unexpected expiry %+v %+v", a, totals)
	}
	// The expiry deleted nothing: the freed manifest lists stay until orphan cleanup finds them old enough.
	location := strings.TrimPrefix(tbl.Location(), "file://")
	var files []string
	_ = filepath.Walk(location, func(path string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() {
			files = append(files, path)
		}
		return nil
	})
	if a := m.RemoveOrphans(ctx, tbl); a.Status != "skipped" {
		t.Fatalf("young orphans must stay: %+v", a)
	}
	old := time.Now().Add(-100 * time.Hour)
	for _, f := range files {
		_ = os.Chtimes(f, old, old)
	}
	a = m.RemoveOrphans(ctx, tbl)
	if a.Status != "done" || a.Metrics["deleted"].(int) == 0 {
		t.Fatalf("old orphans must go: %+v", a)
	}
	// The table still reads after the cleanup.
	if h, _, err := Inspect(ctx, tbl, full, nil); err != nil || h.Records != 40 {
		t.Fatalf("the table is damaged: %+v %v", h, err)
	}
}

func TestGCDisabledTablesKeepTheirHistory(t *testing.T) {
	ctx := context.Background()
	tbl := smallTable(t, 3)
	tx := tbl.NewTransaction()
	if err := tx.SetProperties(iceberg.Properties{"gc.enabled": "false"}); err != nil {
		t.Fatal(err)
	}
	tbl, err := tx.Commit(ctx)
	if err != nil {
		t.Fatal(err)
	}
	m := maintainer(false, policy())
	if _, a, _ := m.ExpireSnapshots(ctx, tbl); a.Status != "skipped" {
		t.Fatalf("expiry must skip: %+v", a)
	}
	if a := m.RemoveOrphans(ctx, tbl); a.Status != "skipped" {
		t.Fatalf("orphan cleanup must skip: %+v", a)
	}
}

func TestJobValidation(t *testing.T) {
	job := Job{Kind: KindMaintain, CatalogURI: "http://polaris", Warehouse: "db", StorageEndpoint: "http://rustfs:9000",
		Policy: policy()}
	if err := job.Validate(); err != nil {
		t.Fatal(err)
	}
	job.Policy.OrphanFiles.OlderThanHours = 2
	if err := job.Validate(); err == nil {
		t.Fatal("orphan cleanup younger than a day must be refused")
	}
	job.Policy = policy()
	job.Kind = "drop"
	if err := job.Validate(); err == nil {
		t.Fatal("unknown kinds must be refused")
	}
	job = Job{Tables: []string{"raw.a", "raw.b"}, Exclude: []string{"RAW.B"}}
	if !job.Selected("raw.a") || !job.Selected("raw.b") || job.Selected("raw.c") {
		t.Fatal("selection is wrong")
	}
	if job.Excluded("raw.a") || !job.Excluded("raw.b") {
		t.Fatal("exclusion is wrong")
	}
}

func TestSnapshotDaysAndBuckets(t *testing.T) {
	now := time.Now()
	days := snapshotDays([]table.Snapshot{{TimestampMs: now.Add(-time.Hour).UnixMilli()},
		{TimestampMs: now.Add(-13*24*time.Hour - time.Hour).UnixMilli()}, {TimestampMs: now.Add(-30 * 24 * time.Hour).UnixMilli()}}, now)
	if days[13] != 1 || days[0] != 1 || len(days) != 14 {
		t.Fatalf("unexpected days %v", days)
	}
	if bucket(500) != 0 || bucket(2<<20) != 1 || bucket(1<<30) != len(SizeBuckets) {
		t.Fatal("unexpected buckets")
	}
	if hours(72) != "3 days" || hours(24) != "1 day" || hours(5) != "5 hours" {
		t.Fatal("unexpected hours")
	}
}
