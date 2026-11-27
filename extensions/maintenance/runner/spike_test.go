//go:build spike

// Snapshot expiry and orphan cleanup against a real Iceberg REST catalog and S3 storage, without the
// policy's minimum ages. Opt-in: it deletes files. Seed a catalog first (see DECISIONS.md), then
//
//	SPIKE_CATALOG_URI=http://polaris-control-plane:8181/api/catalog SPIKE_WAREHOUSE=db-spike \
//	SPIKE_STORAGE=http://rustfs:9000 SPIKE_TOKEN=... SPIKE_TABLE=raw.feeder_load_readings go test -tags spike -run Spike
package main

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/apache/iceberg-go/catalog/rest"
	"github.com/apache/iceberg-go/table"
	"github.com/apache/iceberg-go/table/maintenance"
)

func TestSpikeExpiryThenOrphanCleanupOnRealStorage(t *testing.T) {
	ctx := context.Background()
	registry, err := storageRegistry(os.Getenv("SPIKE_STORAGE"))
	if err != nil {
		t.Fatal(err)
	}
	cat, err := rest.NewCatalog(ctx, "spike", os.Getenv("SPIKE_CATALOG_URI"), rest.WithOAuthToken(os.Getenv("SPIKE_TOKEN")),
		rest.WithWarehouseLocation(os.Getenv("SPIKE_WAREHOUSE")), rest.WithIORegistry(registry))
	if err != nil {
		t.Fatal(err)
	}
	id := table.Identifier(strings.Split(os.Getenv("SPIKE_TABLE"), "."))
	tbl, err := cat.LoadTable(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	before, _, err := Inspect(ctx, tbl, policy(), nil)
	if err != nil {
		t.Fatal(err)
	}
	m := maintainer(false, policy())
	m.Policies.SnapshotExpiry = SnapshotExpiry{Enabled: true, MaxSnapshotAgeDays: 0, MinSnapshotsToKeep: 1}
	tbl, action, totals, _ := m.ExpireSnapshots(ctx, tbl)
	t.Logf("expiry: %s %s", action.Status, action.Detail)
	if action.Status != "done" || totals.SnapshotsExpired == 0 {
		t.Fatalf("nothing expired: %+v", action)
	}
	// The tag keeps its snapshot: one for main's head, one for the tag.
	if got := len(tbl.Metadata().Snapshots()); got != 2 {
		t.Fatalf("expected the head and the tagged snapshot to stay, got %d", got)
	}
	plan, err := maintenance.PlanOrphanFiles(ctx, tbl, maintenance.WithCleanupFilesOlderThan(time.Millisecond))
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("orphans: %d files, %d bytes", len(plan.OrphanFiles()), plan.TotalSizeBytes())
	if len(plan.OrphanFiles()) == 0 {
		t.Fatal("the expired snapshots' files must be orphans now")
	}
	result, err := maintenance.ExecuteOrphanCleanup(ctx, tbl, plan)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("deleted %d orphan files", len(result.DeletedFiles))
	after, _, err := Inspect(ctx, tbl, policy(), nil)
	if err != nil {
		t.Fatal(err)
	}
	if after.Records != before.Records || after.DataFiles != before.DataFiles {
		t.Fatalf("the current snapshot changed: %+v → %+v", before, after)
	}
	arrow, err := tbl.Scan().ToArrowTable(ctx)
	if err != nil {
		t.Fatalf("the table no longer reads: %v", err)
	}
	defer arrow.Release()
	if arrow.NumRows() != before.Records {
		t.Fatalf("read %d rows, expected %d", arrow.NumRows(), before.Records)
	}
}
