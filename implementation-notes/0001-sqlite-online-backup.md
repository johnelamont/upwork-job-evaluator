# 0001: SQLite online backup via `conn.backup()` instead of close+copy

**Status:** Implementation note (not an ADR)
**Date:** 2026-05-30
**Touches:** [src/storage.py:174-180](../src/storage.py#L174-L180) — `_do_backup()`
**Related spec:** [ARCHITECTURE.md §6.3](../ARCHITECTURE.md#L215-L230)

## What

`_do_backup()` uses Python stdlib's `sqlite3.Connection.backup()` to copy the live queue DB into the OneDrive backups folder, rather than the close-the-connection-and-file-copy pattern that ARCHITECTURE §6.3 prescribes.

## Why this differs from the spec

ARCHITECTURE §6.3 says:

> Export on app close (and optionally on a timer): close the DB connection fully, then copy `queue.db` to the OneDrive backups folder... A plain file copy is safe *only* once the connection is closed and no `-wal`/`-shm` are mid-write; closing the connection first checkpoints the WAL into the main file.

That mechanism is correct. `sqlite3.Connection.backup()` is a strictly better one for the same goal:

- **Hot:** copies a WAL-mode DB while the live connection stays open. No close-reopen cycle, no brief window where the UI's DB ops would have to wait or fail.
- **Correct:** the API exists for exactly this scenario; WAL is handled transparently.
- **Stdlib:** no new dependency. Available since Python 3.7.
- **Atomic from the destination's POV:** copies pages within a transaction; a reader of the destination file mid-write either sees the pre-state or the post-state, not a torn write.

The architectural commitment ("safe backup to OneDrive on a timer plus at close, never run the app off a OneDrive-resident DB") is unchanged. Only the mechanism differs. The spec's prescribed close+copy is the obvious-first-mechanism description; `conn.backup()` is the canonical stdlib way to do exactly the same thing without the close-reopen tax.

## What would make this graduate to an ADR

If the close+copy mechanism ever needs to be restored — for instance, a future SQLite/Python regression in `backup()`, or a switch away from WAL mode — that's an architectural change worth a real ADR. Until then, this note exists so a future reader doesn't see code-vs-spec mismatch and wonder which one is wrong.
