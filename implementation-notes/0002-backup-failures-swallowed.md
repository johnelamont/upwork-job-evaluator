# 0002: Backup failures swallowed silently in the timer thread

**Status:** Implementation note (not an ADR)
**Date:** 2026-05-30
**Touches:** [src/storage.py:163-170](../src/storage.py#L163-L170) — `_backup_loop()`
**Related spec:** [ARCHITECTURE.md §6.3](../ARCHITECTURE.md#L226-L228) (backup pattern; no error policy stated)

## What

`_backup_loop()` wraps each `_do_backup()` call in a bare `except Exception: pass`. Failures (OneDrive folder locked by the sync client, disk full, transient SQLite busy errors) cause that interval's backup to be skipped silently. The next interval tries again 15 minutes later.

## Why

Two reasons.

1. **An unhandled exception in the timer thread kills the thread.** Daemon stops; subsequent intervals never fire; you've lost all future backups until app restart. The failure mode of "no more backups ever" is worse than the failure mode of "this interval's backup skipped."
2. **A backup failure should not interrupt the app's primary job.** Crashing the app — or even raising a dialog — over a transient OneDrive sync lock would surface a maintenance concern as user-facing noise during job evaluation.

## What this costs

Silent skip is not zero-cost. If backups stop working entirely — disk full, OneDrive permission changed, OneDrive path moved — there is no UI signal. You'd notice via absence of recent timestamped files in the backup folder, which depends on you looking.

The implicit reliability contract is therefore: *backups are best-effort on a 15-minute cadence; the only signal of failure is the absence of recent files in `%USERPROFILE%\OneDrive\UpworkReview\backups\`.*

## What would make this graduate to an ADR

Any escalation policy beyond silent retry — a log file, an in-app status indicator, a "backup stale > 1 hour" warning, a startup-time check that the backup folder has recent activity. Those are real design decisions (where does the log live, what is the UI surface, what's the staleness threshold) and warrant ADR-level review. A single line in `_backup_loop()` does not.
