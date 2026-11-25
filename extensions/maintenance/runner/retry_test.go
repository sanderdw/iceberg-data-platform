package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/apache/iceberg-go/table"
)

// conflict is what iceberg-go returns when Polaris refuses a commit with a retryable 409.
var conflict = fmt.Errorf("commit table: %w", table.ErrCommitFailed)

func noWait(context.Context, time.Duration) error { return nil }

func TestTheWaitGrowsAndStaysBoundedWithJitter(t *testing.T) {
	r := Retry{Attempts: 10, Base: time.Second, Max: 8 * time.Second}
	for n, want := range map[int]time.Duration{1: time.Second, 2: 2 * time.Second, 3: 4 * time.Second, 4: 8 * time.Second,
		5: 8 * time.Second, 40: 8 * time.Second} {
		for range 50 {
			if got := r.Wait(n); got < want/2 || got > want {
				t.Fatalf("retry %d waited %s, want between %s and %s", n, got, want/2, want)
			}
		}
	}
}

func TestACompactionThatConflictsIsPlannedAgainAgainstTheNewSnapshot(t *testing.T) {
	ctx := context.Background()
	cat, tbl := catalogTable(t, 5)
	id := tbl.Identifier()
	m := maintainer(false, policy())
	reloads := 0
	m.Reload = func(ctx context.Context) (*table.Table, error) { reloads++; return cat.LoadTable(ctx, id) }
	m.Retry = Retry{Attempts: 3, Base: time.Millisecond, Max: time.Millisecond, Sleep: noWait}
	var planned []int
	attempt := func(ctx context.Context, current *table.Table) (*table.Table, Action, Totals, error) {
		scan := current.Scan()
		tasks, err := scan.PlanFiles(ctx)
		scan.Close()
		if err != nil {
			t.Fatal(err)
		}
		planned = append(planned, len(tasks))
		if len(planned) == 1 {
			// Another writer commits first, and Polaris refuses this commit.
			other, err := cat.LoadTable(ctx, id)
			if err != nil {
				t.Fatal(err)
			}
			appendRows(t, other, 5)
			return current, Action{Operation: "compaction", Status: "failed", Detail: conflict.Error()}, Totals{}, conflict
		}
		return m.Compact(ctx, current, nil)
	}
	tbl, a, totals := m.retrying(ctx, tbl, time.Now().Add(time.Minute), attempt)
	if a.Status != "done" || a.Retries != 1 || totals.CommitRetries != 1 || reloads != 1 {
		t.Fatalf("expected one retry that commits: %+v %+v (reloads %d)", a, totals, reloads)
	}
	if !strings.Contains(a.Detail, "Committed after 1 conflict") {
		t.Fatalf("the detail must say the commit was retried: %q", a.Detail)
	}
	// The retry planned the new snapshot, with the other writer's file, and kept every row.
	if planned[0] != 5 || planned[1] != 6 || totals.FilesCompacted != 6 {
		t.Fatalf("the retry did not plan again: planned %v, totals %+v", planned, totals)
	}
	h, _, err := Inspect(ctx, tbl, policy(), nil)
	if err != nil || h.Records != 60 || h.DataFiles != 1 {
		t.Fatalf("unexpected table after the retry: %+v %v", h, err)
	}
}

func TestConflictsStopAtTheBound(t *testing.T) {
	_, tbl := catalogTable(t, 1)
	m := maintainer(false, policy())
	reloads, calls := 0, 0
	m.Reload = func(context.Context) (*table.Table, error) { reloads++; return tbl, nil }
	m.Retry = Retry{Attempts: 3, Base: time.Millisecond, Max: time.Millisecond, Sleep: noWait}
	always := func(_ context.Context, current *table.Table) (*table.Table, Action, Totals, error) {
		calls++
		return current, Action{Operation: "expireSnapshots", Status: "failed", Detail: "conflict."}, Totals{}, conflict
	}
	_, a, totals := m.retrying(context.Background(), tbl, time.Now().Add(time.Minute), always)
	if calls != 3 || reloads != 2 || a.Status != "failed" || a.Retries != 2 || totals.CommitRetries != 2 {
		t.Fatalf("expected 3 attempts: calls %d, reloads %d, %+v", calls, reloads, a)
	}
	if !strings.Contains(a.Detail, "conflicted with another writer 3 times") {
		t.Fatalf("unexpected detail %q", a.Detail)
	}
}

func TestOnlyConflictsAreRetried(t *testing.T) {
	_, tbl := catalogTable(t, 1)
	m := maintainer(false, policy())
	m.Reload = func(context.Context) (*table.Table, error) { t.Fatal("no reload expected"); return nil, nil }
	m.Retry = Retry{Attempts: 4, Base: time.Millisecond, Max: time.Millisecond, Sleep: noWait}
	calls := 0
	for _, err := range []error{errors.New("access denied"), nil} {
		calls = 0
		_, _, _ = m.retrying(context.Background(), tbl, time.Now().Add(time.Minute),
			func(_ context.Context, current *table.Table) (*table.Table, Action, Totals, error) {
				calls++
				return current, Action{Operation: "compaction", Status: "failed"}, Totals{}, err
			})
		if calls != 1 {
			t.Fatalf("%v was tried %d times", err, calls)
		}
	}
}

func TestNoRetryPastTheDeadline(t *testing.T) {
	_, tbl := catalogTable(t, 1)
	m := maintainer(false, policy())
	m.Reload = func(context.Context) (*table.Table, error) { t.Fatal("no reload expected"); return nil, nil }
	m.Retry = Retry{Attempts: 4, Base: time.Hour, Max: time.Hour, Sleep: noWait}
	_, a, _ := m.retrying(context.Background(), tbl, time.Now().Add(time.Minute),
		func(_ context.Context, current *table.Table) (*table.Table, Action, Totals, error) {
			return current, Action{Operation: "compaction", Status: "failed"}, Totals{}, conflict
		})
	if !strings.Contains(a.Detail, "time is up") {
		t.Fatalf("unexpected detail %q", a.Detail)
	}
}
