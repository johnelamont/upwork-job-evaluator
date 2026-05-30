# ADR-0002: Feedback loop via JSONL corrections injected as prompt context

**Status:** Accepted (with stubbed seams for v1)
**Date:** 2026-05-27
**Owner:** John Lamont
**Related:** ARCHITECTURE.md §6.1, §6.3, §8, §9.1; ADR-0001 (the read-via-paste decision this builds on)

---

## Context

The tool's value compounds only if Claude's fit verdicts get *better* over time at matching the owner's actual taste. Without a feedback mechanism, the system is static — Claude reads `wheelhouse.md` and the job summary on every call with no idea whether its prior verdicts were any good. The owner manually pursues some jobs the system flagged "out," skips some it flagged "in," and that signal is currently lost.

A feedback mechanism captures those disagreements and feeds them back into future calls, so Claude's calibration drifts toward the owner's actual decisions.

### What "learning" means here, plainly

This is **not** training, fine-tuning, or weight updates. Claude has no memory between API calls. Every call is fresh.

What this design actually does is **consistent re-prompting with accumulated taste-notes**. The corrections log is a file. The most recent N relevant entries get pasted into the next prompt as labeled calibration notes. The system "learns" because its *instructions sharpen* — not because the model changes.

This framing is a feature, not a limitation:

- Every correction is human-readable plain text. The owner can open the file and see exactly what Claude is being told about their taste.
- Mistakes are reversible. A bad correction that pulls Claude in a wrong direction can be deleted from the file in one edit. Try doing that with a fine-tune.
- No training infrastructure. No data pipeline. No model artifact to version. The "weights" are a JSONL file in a folder.

Calling this a "learning loop" in conversation is fine shorthand; the ADR records what it actually is so future-you (or future Claude Code) doesn't expect more than the mechanism provides.

### What feedback we want vs. what we don't

Two channels of signal need to stay separate:

- **Corrections** — judgments about *Claude's verdict quality*. "You said in, I disagree because the rate's a joke." "You said out, I'd actually pursue this — I do API integration." This is what feeds forward into future verdicts because it generalizes across jobs.
- **Bid/outcome tracking** — what the owner *did* with a job that cleared the bar. Did they submit a proposal? Did they get an interview? Win? This is per-job lifecycle data, not verdict-quality data. It lives in the SQLite queue table (the `queue_state` field already in ARCHITECTURE §4.4, extended in a future ADR for outcome states), and it eventually syncs to Zoho. **It does not go in the corrections log.** Mixing the channels would dilute the calibration corpus with action data that has nothing to teach about Claude's judgment.

This separation is intentional and load-bearing for the ADR. The corrections log knows nothing about bids.

---

## Decision

A JSONL file of corrections, written on every review, with the most recent N disagreements injected into each subsequent Claude call as labeled calibration notes.

### 1. Storage: JSONL, separate file from the SQLite queue

**File:** `%LOCALAPPDATA%\UpworkReview\corrections.jsonl`
**Format:** One JSON object per line, append-only.
**Backup:** Copied to OneDrive alongside `queue.db` on app close, per ARCHITECTURE §6.3.

JSONL fits the data shape exactly: append-only, simple structure, trivially streamable, diffable as plain text, schema-tolerant (new fields appear in newer lines without migration). SQLite was considered and rejected for this log specifically — corrections never get updated, only appended and read sequentially, which is what JSONL is for. (The *queue* table stays in SQLite per ARCHITECTURE §6.3; this ADR concerns only the corrections log.)

### 2. Correction record schema

```json
{
  "timestamp": "ISO 8601",
  "job_summary_excerpt": "first 200 chars of the pasted summary",
  "claude_fit": "strong | medium | weak",
  "claude_wheelhouse": "yes | partial | no",
  "claude_model_fit": "yes | poor",
  "claude_next_step": "draft proposal | ask questions first | pass",
  "user_correction": "agree | disagree",
  "user_note": "free-text, REQUIRED when user_correction is 'disagree', optional when 'agree'"
}
```

Notes on fields:

- `job_summary_excerpt` (not the full summary) keeps the log compact and prevents the corrections file from ballooning. 200 chars is enough to recognize the job; the full text lives in the queue DB anyway.
- The captured Claude fields (`claude_fit`, `claude_wheelhouse`, `claude_model_fit`, `claude_next_step`) are the structured-block conclusions from the verdict — they give the disagreement *something specific to push against* when the corpus is re-read months later. "I disagreed" is weaker than "Claude rated this strong / wheelhouse-yes / model-fit-yes and recommended drafting, and I disagreed because…". The prose reasoning is not stored in the correction record (it lives in the queue DB); the structured conclusions are enough to anchor the note.
- `user_note` is the **entire teaching signal**. It is **required on disagreement** — the owner must articulate the *why* of any pushback. This is more friction per disagreement, deliberately, because a corpus of "disagree / disagree / disagree" with no reasoning teaches nothing. The note is what generalizes; the bucket values alone do not.

### 3. Injection strategy: most recent N disagreements only, N = 10–15

Each Claude call's `recent_corrections` input field (per ARCHITECTURE §4.1) is populated by:

1. Reading the JSONL file from the tail.
2. Filtering to entries where `user_correction == "disagree"`.
3. Taking the most recent 10–15.
4. Formatting each as a calibration note (see §4 below).

Agrees are excluded. They confirm Claude got it right and add nothing to the next prompt — every token of an "agree" example is a token *not* spent on a disagreement. Disagreements are where the teaching lives.

**N starts at 10 for v1; tunable.** Token cost is modest (~50–150 tokens per correction = 1–2K total at N=15), well within budget. The real risk of larger N is *dilution of the wheelhouse statement*, not cost. Start small, raise if calibration feels weak.

**The selection step is an extensible seam, not a hardcoded query.** `N` is a config value, but more importantly the *strategy* that picks which corrections to inject must be replaceable without disturbing the rest of the pipeline. The whole feedback corpus is a research surface — a more effective form of feedback may be discovered (semantic retrieval of corrections similar to the current job, recency-weighted sampling, owner-curated sets, mixing in select agrees, etc.), and the design must allow swapping the selection logic without touching the UI, the storage format, or the prompt assembly.

Required interface:

```python
def select_corrections(current_job: dict, corpus_path: str, n: int = 10) -> list[dict]:
    """Return the correction records to inject for THIS job's call.
    v1 implementation: most-recent-N where user_correction == 'disagree'.
    May be replaced wholesale by any strategy with the same signature."""
```

The pipeline calls `select_corrections(...)` and passes the result to prompt assembly (§4). Everything upstream (how corrections are written) and downstream (how they're framed in the prompt) is independent of *which* strategy is in use. Swapping strategies is a one-function replacement. `current_job` is passed in even though the v1 implementation ignores it — so that similarity- or context-aware strategies need no signature change later.

**Recency-biased by design.** Taste shifts. A correction from three months ago about a stack the owner has since stopped working with should age out, and most-recent-N does that for free. **No curation in v1** — curation (an editor over `corrections.jsonl`, hand-selecting the most informative entries) is a Phase II concern once the corpus is large enough to know what informative *looks like*.

### 4. Prompt framing: corrections as labeled calibration notes, after the wheelhouse

The Claude call's prompt template assembles in this order:

1. **Wheelhouse** — the standing definition from `wheelhouse.md`, presented as the primary instruction.
2. **Recent calibration notes from the owner** — the injected corrections, framed as: *"On a prior job whose summary began [excerpt], you rated it FIT=[strong/medium/weak], WHEELHOUSE=[yes/partial/no], MODEL FIT=[yes/poor], and recommended NEXT STEP=[…]. The owner disagreed. They said: [user_note]. Take this calibration into account."*
3. **The current job** — `summary` and `user_annotations` per the input contract.

This framing tells Claude these are *prior calls where it got it wrong*, not generic examples. The "Take this calibration into account" is intentional — the corrections are steering, not just data.

### 5. UI: prominent, multi-line note capture

Per ARCHITECTURE §6.1, the review pane includes a correction-feedback control. Concretely:

- Two radio buttons: **Agree** / **Disagree**.
- A multi-line `Text` widget (~3 rows tall) for the note. **Prominently sized** — not a single-line input — because the note *is* the learning engine.
- A **Submit** button that:
  - Validates: if Disagree is selected, the note must be non-empty. If Agree, the note is optional.
  - Appends a record to `corrections.jsonl`.
  - Advances the review queue.

The note field's prominence is a design instruction, not decoration. A tiny input field would tell the owner the note is an afterthought; the engine depends on the opposite signal.

---

## Alternatives Considered

**Markdown corrections log.** Rejected explicitly per owner decision. Freeform Markdown is easy to write but requires parsing back into structured records for injection, which contradicts the "no parser, file flows as prose" stance taken for the wheelhouse. The wheelhouse can be unstructured because Claude reads it as-is; corrections must be *filtered and selected* by Python before injection, which requires structure.

**SQLite for corrections.** Considered. Rejected because corrections are append-only and read sequentially — the workload SQLite adds nothing for. JSONL is the format that fits append-only structured logs. (SQLite remains correct for the queue and outcome tracking; see ARCHITECTURE §6.3 and the future Zoho-sync ADR.)

**Include agree entries in injection corpus.** Rejected. Agrees confirm correctness and consume tokens that disagreements would use better. They're still *written* to the log (for full record-keeping and possible Phase II analysis), but excluded from injection.

**Optional note on disagreement.** Rejected. A corpus of unexplained disagreements is nearly useless for calibration — Claude has no way to generalize "disagree" without the *why*. The friction of being required to articulate the reason is the price of the corpus being worth anything.

**Curated selection of which corrections inject.** Deferred to Phase II. v1 uses most-recent-N because it's mechanical, recency-correct, and removes the temptation to over-engineer before the corpus reveals what curation should optimize for. If calibration drifts wrong, the lever is *first* sharpening `wheelhouse.md`, *second* deleting bad entries from `corrections.jsonl`, *third* (only if those don't work) building a curation UI.

**Embeddings-based correction retrieval** (find corrections similar to the current job and inject those, rather than most-recent). Considered for v1, deferred — but **not foreclosed**. It is exactly the kind of strategy the `select_corrections` seam (§3) exists to allow: swapping it in later is a one-function replacement, no pipeline change. It is deferred from v1 only because it adds embedding infrastructure and an opaque retrieval step whose value is unproven at this corpus size, and most-recent-N is legible and debuggable. When the corpus is large enough that recency-N feels insufficient, this is the first upgrade to try.

---

## Consequences

### Positive

- **Fully inspectable.** The owner can open `corrections.jsonl` at any time and see exactly what Claude is being told. There is no opaque state.
- **Fully reversible.** A bad correction is one line in a text file. Delete it, save, next call uses the cleaned corpus. No retraining, no migration, no model artifact to invalidate.
- **No training infrastructure.** Append, read tail, format into prompt. The entire mechanism is file I/O and string assembly.
- **The cost of feedback is paid where it teaches.** Required notes on disagreement mean the owner only pays friction *when pushing back*, and pays it *into the corpus that will benefit*.
- **Recency-correct by default.** Most-recent-N captures taste drift automatically.

### Negative / accepted costs

- **Required notes on disagreement add friction per pushback.** Accepted: the alternative is a corpus that doesn't teach.
- **Not real learning.** The system improves only as fast as the owner provides corrections. Claude's underlying capabilities are unchanged. (This is the honest scope; the Context section above states it plainly.)
- **Corpus quality is owner-dependent.** Vague notes ("nah") produce vague calibration. Specific notes ("the rate is below my floor for ongoing work") produce specific calibration. The system's ceiling is the quality of the owner's articulated reasoning.
- **Recency bias may discard still-relevant older corrections.** Acceptable for v1; the curation seam exists for Phase II if this proves to be a problem.

### Out-of-scope by construction

- **Bid/outcome tracking is NOT in the corrections log.** Those are queue-state transitions in SQLite, syncing to Zoho via a future ADR. Mixing them dilutes the calibration corpus.
- **No automatic correction generation.** Claude does not write corrections to itself based on its own verdicts. Every entry comes from the owner reviewing a real job.
- **No multi-user or shared corpora.** The corrections log is one owner's taste. If the tool ever serves multiple users, each gets their own file; corpora do not merge.

---

## Stubbed seams for v1

Per ARCHITECTURE §9.1, the feedback loop is implemented for v1 with the design above intact. The seams explicitly **stubbed** are:

- **Curation UI** — there is no in-app editor over `corrections.jsonl` in v1. The file can be edited manually with any text editor if needed. Phase II adds an editor analogous to the wheelhouse editor (ARCHITECTURE §6.5) if the corpus grows enough to warrant it.
- **Pruning / archival** — the file grows unbounded in v1. Anticipated growth (a few entries per work session, maybe hundreds per year) is well within "no problem" for years. Phase II addresses pruning if it becomes one.
- **Analytics on the corpus** — "how often does Claude get it right?" is a real question with an obvious answer in the data (count agrees vs disagrees over time), but no v1 surface. Phase II.

---

## References

- ARCHITECTURE.md §4.1 (`recent_corrections` input field), §6.1 (UI feedback control), §6.3 (storage location and OneDrive backup pattern), §8 (calibration is a prompt concern), §9.1 (feedback loop seam).
- ADR-0001 (the load-bearing read-via-paste decision; this ADR builds on the input/output contracts that decision protected).
- This conversation, 2026-05-27.
