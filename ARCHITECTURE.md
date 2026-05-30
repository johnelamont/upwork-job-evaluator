# Architecture — Upwork Job Review Tool

**Status:** v1
**Owner:** John Lamont
**Implementer:** Claude Code (against this document + the ADRs)

---

## 1. Purpose

A local desktop tool that helps decide which Upwork jobs are worth pursuing and drafts a proposal for the ones that are. The human does the reading of Upwork, the triggering, the judgment review, and all submission. Claude does fit-scoring and proposal drafting on demand.

This is **not** a scraper, a poller, an alerting bot, or a CRM pipeline. It replaces an earlier three-server design (scrape → filter → write-to-Zoho) that those servers are retired by this one.

---

## 2. The load-bearing invariant

> **Python and the local tool never communicate with Upwork. The browser is operated only by the human. All data crosses the boundary via human copy/paste and manual file download/upload.**

This is the compliance property the entire design exists to preserve. Upwork's automation policy is behavioral and enforced by automated detection: it flags non-browser request signatures, scheduled feed reads, and any tool that performs actions faster or more often than a human could. By keeping every Upwork interaction inside the human's own browser session, the tool produces no detectable automation signature at all — the reads and writes to Upwork *are* a human reading and writing.

**Any feature that opens an interface between Python and Upwork is out of scope by definition**, regardless of how convenient. This includes: reading the job feed programmatically, auto-filling the proposal box, attaching files to a submission, posting to a client, or driving the browser via extension/Puppeteer/Playwright. If a future feature seems to require crossing this boundary, the answer is "the human copies/pastes it," not "automate the crossing."

The cost of compliance is a few seconds of manual copy/paste per job. That cost is the point.

---

## 3. The flow

```
[Upwork job open in Chrome — human reading]
        │  human selects + copies (disciplined)
        ▼
[Tkinter window]
   ├─ URL field        ← paste 1 (job URL)
   ├─ Summary field    ← paste 2 (the "Summary" block only)
   └─ Annotations field← optional human margin notes
        │  human clicks [Review]
        ▼
[Judgment module]  ── threaded Claude call ──▶  verdict JSON
        │
        ▼
[Record assembly]  combine Claude output + URL + timestamp
        │
        ▼
[Local storage]    review queue (sortable by fit: strong→medium→weak)
        │
        ▼
[Review queue UI]  human reads verdict + proposal,
                   copies proposal, gives correction feedback
        │
        ▼
[Human]  pastes/uploads into Upwork by hand
```

Nothing in this chain touches Upwork except the human at the top and bottom.

---

## 4. Data contracts (frozen)

These are the contracts between the UI, the judgment module, and storage. Field-level additions are cheap and expected as the tool matures; the *shape* is stable.

### 4.1 Input to the Claude call

```json
{
  "summary": "<pasted Summary block, leading 'Summary' header line stripped by UI>",
  "user_annotations": "<optional human margin notes, may be empty string>",
  "wheelhouse": "<current wheelhouse definition, loaded from wheelhouse.md>",
  "recent_corrections": ["<correction strings from the log, may be empty>"]
}
```

Notes:
- The **URL is NOT sent to Claude.** It is a storage/UI field only. Claude does not need it to judge fit, and shipping it wastes tokens.
- `summary` is the Upwork **Summary block only** — clean client prose, no budget, no skill tags, no payment-verified badge, no "posted N hours ago." The human's disciplined copy/paste guarantees this. Because budget/rate/skills are NOT in this input, the output contract does not ask Claude to produce them (see §4.3).
- `user_annotations` is kept as a **separate field**, never concatenated into `summary`, so the prompt can instruct Claude to treat annotations as the *user's steering* and `summary` as the *client's words*. Two distinct feedback surfaces: annotations steer *this* verdict; corrections (below) steer *future* verdicts.

### 4.2 Output from the Claude call (frozen)

The output contract is defined by `wheelhouse.md` Part 2 ("What the evaluation should produce"), not by this document. `wheelhouse.md` is the source of truth for output shape; ARCHITECTURE conforms to it. The model returns **prose first, then — when `NEXT STEP` is `draft proposal` — a delimited proposal section, then a trailing fenced structured block**, exactly as the wheelhouse instructs. Python extracts the structured block into machine-readable fields and the proposal section into its own field; the prose is stored and displayed verbatim.

The model's raw response:

```
<narrative prose — the judgment brief: what the job is, fit verdict and why,
risk flags, differentiators to lead with, commercial posture, clarifying
questions. Written as a colleague's briefing, not checkboxes.>

===PROPOSAL===
<full drafted proposal in John's voice — present only when NEXT STEP is
"draft proposal"; the whole section is omitted otherwise>
===END PROPOSAL===

```
FIT:          strong | medium | weak
WHEELHOUSE:   yes | partial | no
MODEL FIT:    yes | poor
TOP FLAGS:    [short list, or "none"]
LEAD WITH:    [the 1–2 differentiators to open with]
PRICING:      [recommended structure, one line]
RETAINER:     yes | maybe | no
NEXT STEP:    draft proposal | ask questions first | pass
```
```

Python parses that into the structured contract used by storage and the UI:

```json
{
  "prose": "<the full narrative brief, verbatim>",
  "fit": "strong | medium | weak",
  "wheelhouse": "yes | partial | no",
  "model_fit": "yes | poor",
  "top_flags": ["<flag>", "..."],
  "lead_with": ["<differentiator>", "..."],
  "pricing": "<one-line recommended structure>",
  "retainer": "yes | maybe | no",
  "next_step": "draft proposal | ask questions first | pass",
  "proposal": "<full drafted proposal in John's voice, or null>"
}
```

- The structured block is **regular and machine-parseable**: fixed keys, one per line, inside a fenced code block at the end of the response. Python extracts it by locating the final fenced block and splitting on the `KEY:` labels. A `===PROPOSAL===` … `===END PROPOSAL===` section, when present, sits between the prose and that block; Python lifts it out as the `proposal` field. The prose is everything before the proposal section — or before the block when there is no proposal. (The block is kept *last* deliberately: a proposal may itself contain a fenced code snippet, so anchoring the parse on the final fence keeps the structured block unambiguous.)
- **`proposal`**: drafted inline in the same response, wrapped in `===PROPOSAL===` markers, when `next_step` is `draft proposal`. When `next_step` is `pass` or `ask questions first`, the model omits the section and `proposal` is `null` — there is nothing to draft yet. (This replaces the old `verdict == out` gate; `next_step` is now the generation trigger, and it carries more nuance — "ask questions first" is neither a yes nor a no.)
- The model returns prose + block (not JSON) because that is what the wheelhouse asks for and what reads naturally to the owner. Python does the structuring. This is the same principle as the input side: the human/model work in natural form, Python handles machine-readability.

### 4.3 Why the output is shaped this way

- **Prose first, then a structured block — both, deliberately.** The prose is where the judgment lives; it's what the owner actually reads to decide. The structured block is the prose's own conclusions restated in fixed labels, so Python can sort the queue and store queryable fields. The block costs almost nothing to generate (~40–60 tokens — the model is restating reasoning it already did, not doing more), and it buys queue-sorting and storage for free.
- **`FIT` is a three-bucket ordinal (`strong/medium/weak`), not a 0–100 score.** An earlier draft used a numeric `fit_score`. The wheelhouse uses three honest buckets, which is better: a 0–100 score is false precision the owner would second-guess ("is this a 72 or a 75?"), whereas strong/medium/weak maps to real decisions. The **teaching surface** is therefore the `FIT` bucket *plus the prose reasoning* — disagreeing with "you called this strong, it's weak because the rate floor isn't met" is the rich correction signal (this matters for ADR-0002's correction schema).
- **`MODEL FIT` is a first-class field, separate from `WHEELHOUSE`.** Capability fit and operating-model fit can diverge: a job can be squarely Zoho (wheelhouse: yes) but framed as hands-on staff augmentation under someone else's architecture (model fit: poor). The wheelhouse calls this out explicitly; the contract preserves it as its own axis because it's one of the owner's sharpest filters.
- **`RETAINER` and `PRICING` are captured** because "ongoing support" language is, per the wheelhouse, "the real prize," and commercial posture is a real decision input — not an afterthought. The old thin contract dropped these; the wheelhouse restores them.
- **`NEXT STEP` is the proposal-generation trigger** and carries three states, not two. `draft proposal` → generate. `ask questions first` → no draft yet, the job needs clarification before a proposal makes sense. `pass` → no draft, recommend declining. This is more honest than a binary in/out: many real jobs are "interested but need to ask first."
- **No `budget`, `rate`, `skills` as extracted fields.** Unchanged from the prior design: none are present in a Summary-only paste, so asking for them invites fabrication. Pricing *guidance* (the `PRICING` line) is the model's *recommendation* based on the wheelhouse's rate posture, explicitly not an extracted client budget.
- **Currency is a prose/questions concern, not a structured field.** The wheelhouse instructs the model to confirm the client's currency before quoting (a Canadian posting quoted in CAD, not USD). This surfaces in the prose and the clarifying questions, not as a contract field, because the summary often won't contain it.

### 4.4 Storage record (superset)

Assembled in Python *after* the Claude call returns, on the main thread:

```python
record = {
    "url": "<from UI, never sent to Claude>",
    "summary": "<what was pasted>",
    "annotations": "<human margin notes>",
    "timestamp": "<ISO 8601, set locally>",
    "prose": "<from Claude — the full narrative brief>",
    "fit": "<from Claude — strong | medium | weak>",
    "wheelhouse": "<from Claude — yes | partial | no>",
    "model_fit": "<from Claude — yes | poor>",
    "top_flags": ["<from Claude>"],
    "lead_with": ["<from Claude>"],
    "pricing": "<from Claude>",
    "retainer": "<from Claude — yes | maybe | no>",
    "next_step": "<from Claude — draft proposal | ask questions first | pass>",
    "proposal": "<from Claude or null>",
    "queue_state": "new | reviewed | dismissed"
}
```

URL, timestamp, and queue_state belong to the **storage tier**, not the Claude contract. The `queue_state` enum is extended later (per the future Zoho-sync ADR) with outcome states (`bid_submitted`, `interview`, `hired`, `lost`) for application tracking. This separation is why both contracts stay clean.

The review queue sorts by `fit` (strong → medium → weak), then secondarily by `timestamp`. With a three-bucket ordinal rather than a 0–100 score, ties within a bucket are broken by recency.

---

## 5. Stack & conventions

The concrete substrate. Pinned here so the implementer (Claude Code) does not reach for reasonable-but-unrequested defaults.

- **Project root folder:** `upwork-job-evaluator` (existing — do not rename or relocate).
- **OS target:** Windows 11. Single-user, single-machine. Cross-platform is not a goal; Windows path conventions (`%LOCALAPPDATA%`, `%USERPROFILE%\OneDrive\...`) are used throughout.
- **Python:** 3.12 minimum. `requires-python = ">=3.12"` in `pyproject.toml`.
- **Dependency manager:** **`uv`** (Astral). Dependencies declared in `pyproject.toml` under `[project] dependencies`; `uv.lock` committed to the repo for byte-exact reproducibility. Run commands via `uv run python <script>`; never assume an active virtual environment. No `requirements.txt` in this project.
- **Anthropic SDK:** the official `anthropic` Python library. Only third-party dependency.
- **Model:** **Claude Sonnet 4.6** (`claude-sonnet-4-6` or the current pinned model string), declared as a config value at one location in `judgment.py` so the model can be swapped (e.g., to Opus) by changing one line.
- **API key:** read from the `ANTHROPIC_API_KEY` environment variable via `os.environ`. Never in code, never in a config file, never in a committed `.env`. Absence of the env var is a clear startup error.
- **Other libraries:** Python standard library only. Specifically: `tkinter` (UI), `sqlite3` (queue storage), `threading` (background API calls per §7), `json` (corrections log + parsing), `pathlib` (paths), `datetime` (timestamps). **Do not** introduce `pydantic`, `sqlalchemy`, `customtkinter`, `requests`, or any other third-party package without an ADR justifying it. The intent is a small, legible, dependency-light tool.

### Project layout

Flat structure, modules per §6:

```
upwork-job-evaluator/
├── README.md
├── ARCHITECTURE.md
├── ADR-0001-read-via-human-copy-paste.md
├── ADR-0002-feedback-loop.md
├── wheelhouse.md
├── pyproject.toml
├── uv.lock
├── src/
│   ├── main.py            # entry point: wires UI, judgment, storage
│   ├── ui.py              # Tkinter window, fields, results pane, review queue
│   ├── judgment.py        # Claude call, prompt assembly, response parsing
│   └── storage.py         # SQLite queue + corrections JSONL
└── tests/                 # optional for v1
```

No nested `src/upwork_job_evaluator/` package directory, no Cookiecutter scaffolding, no `setup.py`. The layout above is the layout.

---

## 6. Modules

### 6.1 UI / clipboard layer (Tkinter)
- Fields: URL, Summary, Annotations. A `[Review]` button. A results pane showing the **prose brief** (primary, read first) followed by the structured block (`FIT`, `WHEELHOUSE`, `MODEL FIT`, `TOP FLAGS`, `LEAD WITH`, `PRICING`, `RETAINER`, `NEXT STEP`), with the proposal in a copyable box when present. A correction-feedback control (see §9.1 and ADR-0002). A review-queue view, sortable by `fit` (strong → medium → weak, then recency).
- Strips the leading `Summary` header line from the pasted summary before handing it on.
- Tkinter constraint: see §7.

### 6.2 Judgment module (the Claude call)
- Pure function of its input contract → output contract. Stateless. No knowledge of URL, storage, or queues.
- Builds the prompt from `summary` + `user_annotations` + `wheelhouse` + `recent_corrections`. The output format is defined by `wheelhouse.md` Part 2 (prose, an optional `===PROPOSAL===` section, and a trailing fenced structured block); the module parses the response by lifting out the proposal section, separating the prose from the final fenced block, and extracting the block's `KEY: value` lines into the structured fields (§4.2). Validates that all required keys are present.
- Exposes a second, stubbed call-type for the deep-dive (see §9.2).

### 6.3 Local state / storage
- Persists the review queue and the corrections log. **Must survive a reboot.**
- Implementation: **SQLite on local disk**, with a scheduled/on-close export to OneDrive for durability. (Flat JSON is acceptable for a first cut but SQLite is preferred — queryable, sortable, transactional.)
- **Do NOT put the live SQLite file in a cloud-sync folder (OneDrive/Dropbox/iCloud).** SQLite writes the main DB plus `-wal`/`-shm` sidecar files and expects exclusive, low-latency control of them. A sync client touching those files mid-write — or two app instances opening the DB across machines — causes *file-level corruption*, not just bad rows. It works for weeks, then eats the queue. This hazard is the reason for the local-disk + export split below.
- The seen-set / dedup machinery from the old design is **gone**. With one-at-a-time manual paste, the human is the dedup. No content hashing (annotations mutate the text anyway).

**Setup**
- Live DB path: `%LOCALAPPDATA%\UpworkReview\queue.db` (local disk, never synced).
- Create the directory on first run if absent; initialize schema if the DB is new.
- Export target: a path inside the OneDrive folder, e.g. `%USERPROFILE%\OneDrive\UpworkReview\backups\`.

**Maintenance**
- Export on app close (and optionally on a timer): close the DB connection fully, then copy `queue.db` to the OneDrive backups folder with a timestamped filename (e.g. `queue-YYYYMMDD-HHMMSS.db`). A plain file copy is safe *only* once the connection is closed and no `-wal`/`-shm` are mid-write; closing the connection first checkpoints the WAL into the main file.
- Keep the last N backups; prune older ones so the folder doesn't grow unbounded.
- Restore = copy a backup out of OneDrive to the local path while the app is closed. Never run the app against a DB living in OneDrive.
- Single-instance only: the app assumes one running instance owning the local DB. (A lockfile or single-instance guard is a reasonable Phase-II hardening, not required for v1.)

### 6.4 Notify stub (Phase II)
- Defines the interface only: `notify(job_record) -> None`. Body deferred. The stub's signature must carry everything a real notifier needs (title/summary, `fit`, url) so Phase II doesn't force an upstream refactor.

### 6.5 Wheelhouse editor (in-app)
- An **Edit Wheelhouse** button loads `wheelhouse.md` into a `Text` widget inside the Tkinter window; save writes it back to disk. Removes the friction of locating the file and opening an external editor.
- **No Markdown parser.** The file flows to the judgment module — and therefore to Claude — as **raw prose**; Claude reads Markdown natively, so nothing in Python needs to interpret its structure. The editor is load-text / save-text only (~15 lines).
- Section-aware editing is explicit non-goal polish, not v1.

---

## 7. Threading constraint (flagged)

The Claude API call takes several seconds. If run on the main Tkinter thread, **the window freezes** for that duration (no repaint, possible "not responding" state).

Required pattern:
- Run the Claude call on a **background thread** (`threading.Thread`).
- Marshal the result back to the main thread for display via `widget.after()` or a queue. **Tkinter widgets may only be touched from the main thread** — violating this causes intermittent, hard-to-reproduce crashes.
- Record assembly (§4.4) happens *after* the threaded call returns, back on the main thread.

The URL handling itself needs no thread — it is plain synchronous local Python (read the field, staple onto the record). Threading exists *only* to keep the UI responsive during the API round-trip.

---

## 8. The wheelhouse (external file)

The fit judgment is only as good as the wheelhouse definition. This lives in **`wheelhouse.md`**, an external file the judgment module loads at call time — *not* in code and *not* in this document, so it can evolve on its own cadence without churning the architecture. `wheelhouse.md` is also the **source of truth for the output contract** (§4.2): its Part 2 defines what the model produces, and ARCHITECTURE conforms to it.

**Calibration note:** because the human pre-filters obvious mismatches (a COBOL job never gets copied in the first place), Claude rarely sees clear out-of-wheelhouse jobs. Verdicts therefore cluster toward the favorable end, and the real discrimination is between `strong` and `medium` — and, importantly, on the `MODEL FIT` axis, where a job can be squarely in the wheelhouse yet a poor operating-model fit (staff-aug under someone else's architecture). A model that rates everything `strong / yes / yes` is the failure mode to watch — and the fix is **sharpening `wheelhouse.md`** (and feeding corrections per ADR-0002), not changing code. Calibration is a prompt concern, not an architecture concern.

---

## 9. Stubbed seams

Both are scaffolded in v1 with defined interfaces but empty/minimal bodies, so the structure is correct and Phase II adds no upstream refactor.

### 9.1 Feedback / learning loop — see ADR-0002
- Interface: `record_correction(job_record, user_correction) -> None` writes the human's post-verdict correction to a local log.
- The recent N corrections are injected into the next call's `recent_corrections` input field.
- This is **prompt drift**, not weight drift: the system improves because its instructions sharpen, and every correction is human-readable and editable. No training infrastructure.
- Open questions deferred to ADR-0002: storage format (md/json/csv), curation vs. unbounded append, how many corrections to inject.

### 9.2 Deep-dive (third-level analysis) — Phase II, **fully manual for now**
- For a job that clears first-pass and is *really* interesting, a heavier re-analysis. **In v1 this is done entirely by hand — there is no code surface for it.** The owner does the deep work manually.
- Deep-dive output is **per-job and terminal**: a tailored proposal, clarifying questions, or scope notes for *one specific client*. It has **no bearing on future verdicts** — the next job is an unrelated posting. Deep-dive content therefore does **not** feed forward into later analyses.
- The only thing that feeds forward is **corrections** (§9.1): judgments about Claude's *verdicts*, which generalize across jobs. Keep the two channels separate — deep-dive is per-job and cumulative-to-nothing; corrections are cross-job and cumulative.
- Someday-maybe seam (NOT a v1 stub): if Claude assistance is later wanted, `deep_dive(job_record, prior_verdict) -> [local artifacts]`, artifacts landing **locally** for manual upload per the §2 invariant.

---

## 10. Explicit non-goals

- No scraping, no polling, no scheduled feed reads.
- No alerting bot, no clipboard *monitoring* (the human presses the button; the tool does not watch the clipboard in the background).
- No dedup / seen-set machinery.
- No Zoho, no CRM write, no Leads.
- No browser automation of any kind (extension, Puppeteer, Playwright, session-cookie reuse).
- No automated submission of anything to Upwork, ever.

---

## 11. Document set

- **ARCHITECTURE.md** — this file (the spine).
- **ADR-0001** — read via human copy/paste rather than scraping (the load-bearing ToS decision + evidence).
- **ADR-0002** — feedback/learning loop (stubbed; design + open questions).
- **README.md** — written last; summarizes the above, how to run, the compliance rationale in brief.
- **wheelhouse.md** — owner-authored; the fit definition. Not part of the architecture; referenced by it.
