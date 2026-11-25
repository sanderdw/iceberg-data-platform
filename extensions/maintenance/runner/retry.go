// Commit conflicts. Since Apache Polaris 1.8 a commit that loses to a concurrent writer is a retryable 409
// (CommitFailedException), which iceberg-go reports as table.ErrCommitFailed.
//
// iceberg-go already retries most commits itself: it reloads the table and replays the change, with
// exponential backoff (the table properties commit.retry.*, 4 retries by default). It does not replay a commit
// that removes delete files (compaction that applies deletes, dangling-delete removal), nor one whose plan no
// longer holds, such as a rewrite of files a concurrent commit removed. Those come back here as
// ErrCommitFailed. The runner then waits, with bounded exponential backoff and jitter, reloads the table and
// plans the operation again against its new snapshot. Only when that fails too does the table fail; the run
// is then partial and the next run tries again.
package main

import (
	"context"
	"errors"
	"fmt"
	"math/rand/v2"
	"time"

	"github.com/apache/iceberg-go/table"
)

// Retry bounds how often and how long an operation is planned and committed again after a conflict.
type Retry struct {
	Attempts int           // tries in all, the first one included
	Base     time.Duration // the wait before the first retry, doubled for each further one
	Max      time.Duration // the longest wait
	// Sleep waits, or returns early when ctx ends. nil: a timer.
	Sleep func(context.Context, time.Duration) error
}

var DefaultRetry = Retry{Attempts: 4, Base: 2 * time.Second, Max: 30 * time.Second}

// retryable reports whether a new plan against the reloaded table may succeed.
func retryable(err error) bool {
	return err != nil && errors.Is(err, table.ErrCommitFailed)
}

// Wait before retry n (1 for the first): Base·2^(n-1), at most Max, of which a random half (full jitter on
// the upper half), so runners that conflicted together do not retry together.
func (r Retry) Wait(n int) time.Duration {
	d := r.Max
	if n >= 1 && n < 31 {
		if scaled := r.Base << (n - 1); scaled > 0 && scaled < r.Max {
			d = scaled
		}
	}
	if d <= 0 {
		return 0
	}
	return d/2 + rand.N(d/2+1)
}

func (r Retry) sleep(ctx context.Context, d time.Duration) error {
	if r.Sleep != nil {
		return r.Sleep(ctx, d)
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// A step plans and applies one operation to tbl. It returns the table after it, its action, totals, and the
// commit error, if any, so that a conflict can be told from other failures.
type step func(ctx context.Context, tbl *table.Table) (*table.Table, Action, Totals, error)

// retrying runs a step and, after a retryable conflict, reloads the table and runs it again from the start.
func (m Maintainer) retrying(ctx context.Context, tbl *table.Table, deadline time.Time, run step) (*table.Table, Action, Totals) {
	retry := m.Retry
	if retry.Attempts < 1 {
		retry.Attempts = 1
	}
	retries := 0
	for attempt := 1; ; attempt++ {
		next, action, totals, err := run(ctx, tbl)
		totals.CommitRetries = retries
		action.Retries = retries
		if !retryable(err) || m.Reload == nil {
			if retries > 0 && err == nil {
				action.Detail += fmt.Sprintf(" Committed after %d %s with another writer.", retries, plural(retries, "conflict", "conflicts"))
			}
			return next, action, totals
		}
		giveUp := func(why string) (*table.Table, Action, Totals) {
			action.Detail += " " + why
			return next, action, totals
		}
		if attempt >= retry.Attempts {
			return giveUp(fmt.Sprintf("The commit conflicted with another writer %d times; the next run tries again.", attempt))
		}
		wait := retry.Wait(attempt)
		if time.Now().Add(wait).After(deadline) {
			return giveUp("The commit conflicted with another writer, and the run's time is up; the next run tries again.")
		}
		m.Log.Printf("%s %s: the commit conflicted with another writer; planning again in %s (retry %d of %d)",
			tableName(tbl.Identifier()), action.Operation, wait.Round(time.Millisecond), attempt, retry.Attempts-1)
		if err := retry.sleep(ctx, wait); err != nil {
			return giveUp("Stopped while waiting to retry: " + err.Error())
		}
		reloaded, err := m.Reload(ctx)
		if err != nil {
			return giveUp("Reloading the table after the conflict failed: " + err.Error())
		}
		tbl = reloaded
		retries++
	}
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}
