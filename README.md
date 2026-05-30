# upwork-job-evaluator

A local Windows desktop tool that helps decide which Upwork jobs are worth pursuing and drafts a proposal for the ones that are. Reads jobs from the human's own browser via disciplined copy/paste — no scraping, no polling, no Upwork API contact — and uses Claude to produce a fit verdict and proposal in the owner's voice. The human submits everything to Upwork by hand.

The no-scraping design is **load-bearing**, not a stylistic choice. Upwork's automation policy is behavioral and enforced by automated detection; the read-via-copy/paste pattern is what makes this tool compliant. See ADR-0001 for the rationale.

---

## Read in this order

1. **ARCHITECTURE.md** — the spine. Read once, end to end. The load-bearing invariant is §2; the data contracts are §4; the stack and project layout are §5; the modules are §6.
2. **ADR-0001-read-via-human-copy-paste.md** — *why* the design is shaped this way. The compliance story and the alternatives rejected.
3. **ADR-0002-feedback-loop.md** — how the learning loop works (prompt drift via accumulated corrections, not weight drift).
4. **wheelhouse.md** — the owner's profile and evaluation rubric. **This is also the source of truth for the model's output contract** (Part 2 defines what the model produces). Edit freely; it's the primary tuning surface.

If you're Claude Code reading this for the first time: read all four documents before writing any code, and summarize back what you understand before generating anything.

---

## Setup

Prerequisites: Windows 11, Python 3.12+, `uv` (Astral). Install `uv` with:

```powershell
winget install --id=astral-sh.uv
```

Then, from the repo root:

```powershell
uv sync                     # creates .venv, installs dependencies from uv.lock
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # session-scoped; or set permanently in System Properties
uv run python src/main.py
```

The `ANTHROPIC_API_KEY` env var must be set or the app errors on startup.

---

## Where things live

| What | Where |
|---|---|
| Live queue database (SQLite) | `%LOCALAPPDATA%\UpworkReview\queue.db` |
| Corrections log (JSONL, append-only) | `%LOCALAPPDATA%\UpworkReview\corrections.jsonl` |
| OneDrive backups (timestamped copies) | `%USERPROFILE%\OneDrive\UpworkReview\backups\` |
| Wheelhouse definition | `wheelhouse.md` (repo root) |

**Do not** move the live DB into a OneDrive folder — SQLite corrupts when cloud-sync touches it mid-write. The backup-on-close pattern exists specifically so OneDrive holds *copies*, not the live file. See ARCHITECTURE §6.3.

---

## When verdicts feel wrong (the tuning ladder)

Reach for these in order. Don't change code when prose changes will do.

1. **Edit `wheelhouse.md`.** This is the rubric the model judges against. If verdicts are consistently mis-calibrated in a direction (too generous, too strict, missing a class of dealbreaker, wrong rate posture), the wheelhouse is where the fix belongs. Part 2 ("Evaluation Rules & Output") is the tunable section.
2. **Open `corrections.jsonl` and prune.** If a few bad corrections are pulling Claude in a wrong direction (e.g., you flagged something `disagree` for a reason that doesn't generalize), delete those lines. The corrections corpus is plain text and reversible by design.
3. **Swap the correction-selection strategy.** `select_corrections()` in `judgment.py` is an extensible seam (ADR-0002 §3). The v1 implementation is most-recent-N disagreements; a smarter strategy (semantic retrieval, weighted recency) is a one-function replacement.
4. **Change the model** (last resort). The Sonnet 4.6 model string is one config value in `judgment.py`. Try Opus if drafting voice or judgment quality feel off after the above; the cost difference is real but modest at this volume.

The order matters: prose changes are inspectable, reversible, and free; code changes are heavier and harder to undo. If you find yourself wanting to change code, ask first whether the wheelhouse can express the change.

---

## Project status

**v1.** What's built: paste-driven job evaluation, verdict + structured block + proposal generation, review queue, corrections capture, wheelhouse editor.

**Stubbed for Phase II:** desktop notifications (interface defined, body deferred), curation UI for the corrections corpus, pruning/archival of old corrections, analytics on the corpus.

**Done by hand for now, no v1 code surface:** the third-level deep-dive analysis on really interesting jobs (ARCHITECTURE §9.2).

**Future ADRs:** Zoho sync for bid/outcome tracking from the SQLite queue (see ARCHITECTURE §4.4 and ADR-0002's Context section for the channel separation).

---

## What this tool will not do

Stated here because the constraint is load-bearing and Claude Code (or a future contributor) will be tempted to "helpfully" add some of these:

- Read Upwork programmatically — no scraper, no poller, no API client, no headless browser, no browser extension.
- Auto-fill the proposal box on Upwork, or attach files to a submission, or click submit.
- Watch the clipboard in the background.
- Send any data to Upwork. Ever.

The human is the only interface between this tool and Upwork. See ARCHITECTURE §2 and §10, and ADR-0001.
