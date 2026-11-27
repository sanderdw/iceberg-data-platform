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

// catalogPolicy is how the gateway passes a policy attached to the database's catalog.
func catalogPolicy(name string) Source {
	return Source{Policy: &Applied{Name: name, Namespace: "_maintenance", Version: 1, AppliedAt: "catalog"}}
}

func policy() Policies {
	return Policies{
		SnapshotExpiry: SnapshotExpiry{Source: catalogPolicy("snapshot-expiry"), Enabled: true, MaxSnapshotAgeDays: 5, MinSnapshotsToKeep: 1},
		DataCompaction: DataCompaction{Source: catalogPolicy("data-compaction"), Enabled: true, TargetFileSizeBytes: 1 << 20,
			MinInputFiles: 2, DeleteRatioThreshold: 0.3, RemoveDanglingDeletes: true, MaxInputBytesPerRun: 64 << 20},
		OrphanFileRemoval:  OrphanFileRemoval{Source: catalogPolicy("orphan-file-removal"), Enabled: true, MaxOrphanFileAgeInDays: 3},
		MetadataCompaction: MetadataCompaction{Source: catalogPolicy("metadata-compaction"), Enabled: true, MinCountToMerge: 100},
	}
}

// smallTable is a table with one snapshot and one small data file per append.
func smallTable(t *testing.T, appends int) *table.Table {
	_, tbl := catalogTable(t, appends)
	return tbl
}

// catalogTable is smallTable with its Hadoop catalog, which another writer can commit through.
func catalogTable(t *testing.T, appends int) (*hadoop.Catalog, *table.Table) {
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
	for i := range appends {
		tbl = appendRows(t, tbl, i)
	}
	return cat, tbl
}

// appendRows commits ten rows with ids from i*10.
func appendRows(t *testing.T, tbl *table.Table, i int) *table.Table {
	t.Helper()
	arrowSchema, err := table.SchemaToArrowSchema(tbl.Schema(), nil, false, false)
	if err != nil {
		t.Fatal(err)
	}
	rows := make([]string, 0, 10)
	for j := range 10 {
		rows = append(rows, fmt.Sprintf(`{"id":%d,"name":"row %d"}`, i*10+j, j))
	}
	data, err := array.TableFromJSON(memory.DefaultAllocator, arrowSchema, []string{"[" + strings.Join(rows, ",") + "]"})
	if err != nil {
		t.Fatal(err)
	}
	defer data.Release()
	tbl, err = tbl.AppendTable(context.Background(), data, 1000, nil)
	if err != nil {
		t.Fatal(err)
	}
	return tbl
}

func maintainer(dryRun bool, p Policies) Maintainer {
	return Maintainer{Job: Job{RunID: "run-test", Kind: KindMaintain, DryRun: dryRun}, Policies: p,
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
	if a := action(t, result, "compaction"); a.Status != "done" || a.Policy == nil || a.Policy.Name != "data-compaction" {
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
	p.DataCompaction.MinInputFiles = 1
	// One target file of budget, and groups of at most one target each.
	p.DataCompaction.MaxInputBytesPerRun = 1 << 20
	tbl := smallTable(t, 3)
	result, _ := maintainer(true, p).Maintain(context.Background(), tbl, time.Now().Add(time.Minute))
	if a := action(t, result, "compaction"); a.Status != "planned" || a.Metrics["groups"].(int) < 1 {
		t.Fatalf("unexpected compaction plan %+v", a)
	}
}

func TestExpiryKeepsTheLastSnapshotsAndLeavesFilesForOrphanCleanup(t *testing.T) {
	ctx := context.Background()
	tbl := smallTable(t, 4)
	p := SnapshotExpiry{Source: catalogPolicy("snapshot-expiry"), Enabled: true, MaxSnapshotAgeDays: 0, MinSnapshotsToKeep: 2}
	expired, err := expirable(tbl, p)
	if err != nil || len(expired) != 2 {
		t.Fatalf("expected 2 expirable snapshots, got %d (%v)", len(expired), err)
	}
	full := policy()
	full.SnapshotExpiry = p
	full.DataCompaction.Enabled = false
	m := maintainer(false, full)
	tbl, a, totals, _ := m.ExpireSnapshots(ctx, tbl)
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
	if _, a, _, _ := m.ExpireSnapshots(ctx, tbl); a.Status != "skipped" {
		t.Fatalf("expiry must skip: %+v", a)
	}
	if a := m.RemoveOrphans(ctx, tbl); a.Status != "skipped" {
		t.Fatalf("orphan cleanup must skip: %+v", a)
	}
}

func TestJobValidation(t *testing.T) {
	table := JobTable{Name: "raw.readings", Identifier: []string{"raw", "readings"}, Policies: policy()}
	job := Job{Kind: KindMaintain, CatalogURI: "http://polaris", Warehouse: "db", StorageEndpoint: "http://rustfs:9000",
		Tables: []JobTable{table}}
	if err := job.Validate(); err != nil {
		t.Fatal(err)
	}
	job.Tables[0].Policies.OrphanFileRemoval.MaxOrphanFileAgeInDays = 0
	if err := job.Validate(); err == nil {
		t.Fatal("orphan cleanup younger than a day must be refused")
	}
	// A type that is off is not checked: its settings are only used to judge health.
	job.Tables[0].Policies.OrphanFileRemoval.Enabled = false
	if err := job.Validate(); err != nil {
		t.Fatal(err)
	}
	job.Tables[0].Policies = policy()
	job.Tables[0].Policies.SnapshotExpiry.MinSnapshotsToKeep = 0
	if err := job.Validate(); err == nil {
		t.Fatal("an expiry that keeps no snapshot must be refused")
	}
	job.Tables[0] = JobTable{Name: "raw.readings", Identifier: []string{"readings"}, Policies: policy()}
	if err := job.Validate(); err == nil {
		t.Fatal("a table needs its namespace")
	}
	job.Tables[0] = table
	job.Kind = "drop"
	if err := job.Validate(); err == nil {
		t.Fatal("unknown kinds must be refused")
	}
}

func TestEachActionNamesThePolicyThatDecidedIt(t *testing.T) {
	p := policy()
	p.OrphanFileRemoval = OrphanFileRemoval{Source: Source{Policy: &Applied{Name: "excluded-orphan-file-removal",
		Namespace: "_maintenance", AppliedAt: "table"}}, Enabled: false, MaxOrphanFileAgeInDays: 3}
	p.MetadataCompaction = MetadataCompaction{MinCountToMerge: 100}
	p.SnapshotExpiry.Problem = "max_snapshot_age_days: snapshotExpiry.maxSnapshotAgeDays is between 1 and 3650."
	result, _ := maintainer(true, p).Maintain(context.Background(), smallTable(t, 3), time.Now().Add(time.Minute))
	orphans := action(t, result, "orphanFiles")
	if orphans.Status != "off" || orphans.Policy.AppliedAt != "table" || !strings.Contains(orphans.Detail, "excluded-orphan-file-removal") {
		t.Fatalf("an override must say which policy switched it off: %+v", orphans)
	}
	if a := action(t, result, "rewriteManifests"); a.Status != "off" || a.Policy != nil || !strings.Contains(a.Detail, "No system.metadata-compaction policy") {
		t.Fatalf("a type without a policy is off: %+v", a)
	}
	if a := action(t, result, "expireSnapshots"); a.Status != "skipped" || !strings.HasPrefix(a.Detail, "Not applied:") {
		t.Fatalf("a policy the gateway did not apply is skipped: %+v", a)
	}
	if a := action(t, result, "danglingDeletes"); a.Policy == nil || a.Policy.Name != "data-compaction" {
		t.Fatalf("dangling deletes follow the data-compaction policy: %+v", a)
	}
	none := Policies{SnapshotExpiry: SnapshotExpiry{MaxSnapshotAgeDays: 5, MinSnapshotsToKeep: 1}}
	if none.AnyEnabled() || !policy().AnyEnabled() {
		t.Fatal("AnyEnabled is wrong")
	}
	if off := maintainer(false, none).Off(); len(off) != 5 || off[0].Status != "off" {
		t.Fatalf("unexpected off actions %+v", off)
	}
}

func TestSnapshotDaysAndBuckets(t *testing.T) {
	now := time.Now()
	perDay := snapshotDays([]table.Snapshot{{TimestampMs: now.Add(-time.Hour).UnixMilli()},
		{TimestampMs: now.Add(-13*24*time.Hour - time.Hour).UnixMilli()}, {TimestampMs: now.Add(-30 * 24 * time.Hour).UnixMilli()}}, now)
	if perDay[13] != 1 || perDay[0] != 1 || len(perDay) != 14 {
		t.Fatalf("unexpected days %v", perDay)
	}
	if bucket(500) != 0 || bucket(2<<20) != 1 || bucket(1<<30) != len(SizeBuckets) {
		t.Fatal("unexpected buckets")
	}
	if days(3) != "3 days" || days(1) != "1 day" {
		t.Fatal("unexpected days")
	}
}
