"""SQLite queue + corrections JSONL."""

import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

LIVE_DIR = Path(os.environ["LOCALAPPDATA"]) / "UpworkReview"
BACKUP_DIR = Path(os.environ["USERPROFILE"]) / "OneDrive" / "UpworkReview" / "backups"
QUEUE_DB = LIVE_DIR / "queue.db"
CORRECTIONS_LOG = LIVE_DIR / "corrections.jsonl"

BACKUP_INTERVAL_MIN = 15
BACKUP_RETENTION = 30

_FIT_ORDER_SQL = (
    "CASE fit WHEN 'strong' THEN 1 WHEN 'medium' THEN 2 WHEN 'weak' THEN 3 END"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT NOT NULL,
    summary      TEXT NOT NULL,
    annotations  TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    prose        TEXT NOT NULL,
    fit          TEXT NOT NULL,
    wheelhouse   TEXT NOT NULL,
    model_fit    TEXT NOT NULL,
    top_flags    TEXT NOT NULL,
    lead_with    TEXT NOT NULL,
    pricing      TEXT NOT NULL,
    retainer     TEXT NOT NULL,
    next_step    TEXT NOT NULL,
    proposal     TEXT,
    queue_state  TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_queue_state ON queue(queue_state);
"""

_INSERT_SQL = """
INSERT INTO queue (
    url, summary, annotations, timestamp,
    prose, fit, wheelhouse, model_fit,
    top_flags, lead_with, pricing, retainer,
    next_step, proposal, queue_state
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_conn: sqlite3.Connection | None = None
_stop_backup = threading.Event()
_backup_thread: threading.Thread | None = None


def open_storage() -> None:
    """Create dirs, open the queue DB, start the backup timer. Idempotent."""
    global _conn, _backup_thread
    if _conn is not None:
        return
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(QUEUE_DB)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.executescript(_SCHEMA)
    _conn.commit()
    _stop_backup.clear()
    _backup_thread = threading.Thread(
        target=_backup_loop, daemon=True, name="storage-backup"
    )
    _backup_thread.start()


def close_storage() -> None:
    """Stop the backup timer, run a final backup, close the DB."""
    global _conn, _backup_thread
    if _conn is None:
        return
    _stop_backup.set()
    if _backup_thread is not None:
        _backup_thread.join(timeout=2.0)
        _backup_thread = None
    _do_backup()
    _conn.close()
    _conn = None


def save_record(record: dict) -> int:
    """Insert one verdict record. Returns the new rowid."""
    assert _conn is not None
    cur = _conn.execute(_INSERT_SQL, (
        record["url"],
        record["summary"],
        record["annotations"],
        record["timestamp"],
        record["prose"],
        record["fit"],
        record["wheelhouse"],
        record["model_fit"],
        json.dumps(record["top_flags"]),
        json.dumps(record["lead_with"]),
        record["pricing"],
        record["retainer"],
        record["next_step"],
        record["proposal"],
        record.get("queue_state", "new"),
    ))
    _conn.commit()
    return cur.lastrowid


def list_queue(queue_state: str | None = "new") -> list[dict]:
    """Return queue records sorted strong->medium->weak, then by recency."""
    assert _conn is not None
    sql = "SELECT * FROM queue"
    params: tuple = ()
    if queue_state is not None:
        sql += " WHERE queue_state = ?"
        params = (queue_state,)
    sql += f" ORDER BY {_FIT_ORDER_SQL}, timestamp DESC"
    return [_row_to_dict(r) for r in _conn.execute(sql, params).fetchall()]


def update_queue_state(record_id: int, queue_state: str) -> None:
    assert _conn is not None
    _conn.execute(
        "UPDATE queue SET queue_state = ? WHERE id = ?",
        (queue_state, record_id),
    )
    _conn.commit()


def append_correction(correction: dict) -> None:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    with CORRECTIONS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(correction) + "\n")


def select_corrections(
    current_job: dict, corpus_path: str, n: int = 10
) -> list[dict]:
    """Return correction records to inject into the next Claude call.

    v1 strategy (ADR-0002 §3): most-recent N where user_correction == 'disagree'.
    `current_job` is unused in v1 but reserved for similarity-aware strategies
    that may swap in later via this same signature.
    """
    path = Path(corpus_path)
    if not path.exists():
        return []
    disagrees: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("user_correction") == "disagree":
                disagrees.append(entry)
    return disagrees[-n:]


def _row_to_dict(row: sqlite3.Row) -> dict:
    rec = dict(row)
    rec["top_flags"] = json.loads(rec["top_flags"])
    rec["lead_with"] = json.loads(rec["lead_with"])
    return rec


def _backup_loop() -> None:
    interval_sec = BACKUP_INTERVAL_MIN * 60
    while not _stop_backup.wait(interval_sec):
        try:
            _do_backup()
        except Exception:
            # Backup failures must never crash the timer thread.
            pass


def _do_backup() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if QUEUE_DB.exists():
        src = sqlite3.connect(QUEUE_DB)
        dst = sqlite3.connect(BACKUP_DIR / f"queue-{ts}.db")
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    if CORRECTIONS_LOG.exists():
        shutil.copy2(CORRECTIONS_LOG, BACKUP_DIR / f"corrections-{ts}.jsonl")
    _prune_backups()


def _prune_backups() -> None:
    for prefix, suffix in (("queue-", ".db"), ("corrections-", ".jsonl")):
        files = sorted(
            BACKUP_DIR.glob(f"{prefix}*{suffix}"),
            key=lambda p: p.stat().st_mtime,
        )
        for old in files[:-BACKUP_RETENTION]:
            old.unlink()
