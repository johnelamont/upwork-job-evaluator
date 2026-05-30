"""Tkinter window, fields, results pane, review queue.

The UI is the only place that touches Tkinter, threads the Claude call (§7),
and owns record assembly (§4.4). It wires the pure judgment module to the
storage tier; judgment itself knows nothing of either (§6.2).

Three tabs: Evaluate (paste a job, get a verdict, save to the queue), Review
Queue (read verdicts, copy proposals, capture corrections per ADR-0002 §5), and
Wheelhouse (the in-app editor, §6.5).

Threading rule (§7): the Claude call runs on a background thread; its result is
marshalled back to the main thread via a queue that the main thread polls with
`after()`. Record assembly and every widget touch happen on the main thread.
"""

import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import judgment
import storage

# wheelhouse.md lives at the repo root; this file is src/ui.py.
WHEELHOUSE_PATH = Path(__file__).resolve().parent.parent / "wheelhouse.md"

# How many recent corrections to inject (ADR-0002 §3; starts at 10, tunable).
CORRECTIONS_N = 10

_POLL_MS = 100


class App:
    """The single application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Upwork Job Review")
        root.geometry("900x720")

        self._result_queue: queue.Queue = queue.Queue()
        self._current_record: dict | None = None  # queue item under review
        self._last_verdict: dict | None = None  # most recent Evaluate-tab result

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)
        self._build_evaluate_tab(notebook)
        self._build_queue_tab(notebook)
        self._build_wheelhouse_tab(notebook)

        self._refresh_queue()
        self._load_wheelhouse()

    # --- Evaluate tab ---------------------------------------------------------

    def _build_evaluate_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Evaluate")

        ttk.Label(tab, text="Job URL (stored only — never sent to Claude):").pack(
            anchor="w"
        )
        self.url_entry = ttk.Entry(tab)
        self.url_entry.pack(fill="x", pady=(0, 6))

        ttk.Label(tab, text="Summary (paste the Upwork Summary block):").pack(
            anchor="w"
        )
        self.summary_text = tk.Text(tab, height=6, wrap="word")
        self.summary_text.pack(fill="both", expand=False, pady=(0, 6))

        ttk.Label(tab, text="Annotations (optional — your steering for this job):").pack(
            anchor="w"
        )
        self.annot_text = tk.Text(tab, height=3, wrap="word")
        self.annot_text.pack(fill="x", pady=(0, 6))

        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=(0, 4))
        self.review_btn = ttk.Button(controls, text="Review", command=self._on_review)
        self.review_btn.pack(side="left")
        ttk.Button(controls, text="Clear", command=self._clear_inputs).pack(
            side="left", padx=(6, 0)
        )

        # Status is a read-only Text (not a Label) so its content — including
        # error messages — can be selected and copied.
        self.status_box = _readonly_text(tab, height=2)
        self.status_box.pack(fill="x", pady=(0, 6))
        self._set_status("Paste a job summary and click Review.")

        ttk.Label(tab, text="Brief (the narrative judgment):").pack(anchor="w")
        self.prose_text = _readonly_text(tab, height=8)
        self.prose_text.pack(fill="both", expand=True, pady=(0, 6))

        ttk.Label(tab, text="Verdict (at a glance):").pack(anchor="w")
        self.block_text = _readonly_text(tab, height=6)
        self.block_text.pack(fill="x", pady=(0, 6))

        prop_header = ttk.Frame(tab)
        prop_header.pack(fill="x")
        ttk.Label(prop_header, text="Proposal:").pack(side="left")
        ttk.Button(
            prop_header, text="Copy proposal", command=self._copy_eval_proposal
        ).pack(side="right")
        self.eval_proposal = _readonly_text(tab, height=7)
        self.eval_proposal.pack(fill="both", expand=True, pady=(0, 6))

    def _set_status(self, text: str) -> None:
        _set_readonly(self.status_box, text)

    def _copy_eval_proposal(self) -> None:
        verdict = self._last_verdict
        if not verdict or not verdict.get("proposal"):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(verdict["proposal"])

    def _on_review(self) -> None:
        summary = _strip_summary_header(self.summary_text.get("1.0", "end").strip())
        if not summary:
            self._set_status("Paste a summary first.")
            return
        try:
            wheelhouse = WHEELHOUSE_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            self._set_status(f"Cannot read wheelhouse.md: {exc}")
            return

        annotations = self.annot_text.get("1.0", "end").strip()
        url = self.url_entry.get().strip()
        corrections = storage.select_corrections(
            {"summary": summary}, str(storage.CORRECTIONS_LOG), CORRECTIONS_N
        )

        self.review_btn.config(state="disabled")
        self._set_status("Evaluating…")
        worker = threading.Thread(
            target=self._evaluate_worker,
            args=(summary, annotations, url, wheelhouse, corrections),
            daemon=True,
        )
        worker.start()
        self.root.after(_POLL_MS, self._poll_result)

    def _evaluate_worker(
        self,
        summary: str,
        annotations: str,
        url: str,
        wheelhouse: str,
        corrections: list[dict],
    ) -> None:
        """Background thread: the blocking Claude call only. No widget access."""
        try:
            verdict = judgment.evaluate(summary, annotations, wheelhouse, corrections)
            self._result_queue.put(("ok", url, summary, annotations, verdict))
        except Exception as exc:  # surfaced on the main thread (R8: halt, store nothing)
            self._result_queue.put(("err", exc))

    def _poll_result(self) -> None:
        try:
            msg = self._result_queue.get_nowait()
        except queue.Empty:
            self.root.after(_POLL_MS, self._poll_result)
            return

        self.review_btn.config(state="normal")
        if msg[0] == "err":
            # R8 failure path: surface the error, store nothing.
            self._set_status(f"Evaluation failed — nothing saved: {msg[1]}")
            return

        _, url, summary, annotations, verdict = msg
        record = _assemble_record(url, summary, annotations, verdict)
        storage.save_record(record)
        self._last_verdict = verdict
        _show_verdict(self.prose_text, self.block_text, verdict)
        proposal = verdict.get("proposal")
        _set_readonly(
            self.eval_proposal,
            proposal if proposal else f"(no proposal — next step: {verdict['next_step']})",
        )
        self._set_status(
            f"Saved to review queue (FIT={verdict['fit']}, "
            f"NEXT STEP={verdict['next_step']})."
        )
        self._refresh_queue()

    def _clear_inputs(self) -> None:
        self.url_entry.delete(0, "end")
        self.summary_text.delete("1.0", "end")
        self.annot_text.delete("1.0", "end")
        self._last_verdict = None
        _set_readonly(self.prose_text, "")
        _set_readonly(self.block_text, "")
        _set_readonly(self.eval_proposal, "")
        self._set_status("Paste a job summary and click Review.")

    # --- Review Queue tab -----------------------------------------------------

    def _build_queue_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Review Queue")

        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Label(top, text="New jobs (strong → medium → weak, then recent):").pack(
            side="left"
        )
        ttk.Button(top, text="Refresh", command=self._refresh_queue).pack(side="right")

        self.tree = ttk.Treeview(
            tab, columns=("fit", "next", "when"), show="tree headings", height=8
        )
        self.tree.heading("#0", text="Job")
        self.tree.heading("fit", text="FIT")
        self.tree.heading("next", text="NEXT STEP")
        self.tree.heading("when", text="When")
        self.tree.column("#0", width=360)
        self.tree.column("fit", width=80, anchor="center")
        self.tree.column("next", width=140, anchor="center")
        self.tree.column("when", width=140, anchor="center")
        self.tree.pack(fill="x", pady=(4, 6))
        self.tree.bind("<<TreeviewSelect>>", self._on_queue_select)

        ttk.Label(tab, text="Verdict:").pack(anchor="w")
        self.q_prose = _readonly_text(tab, height=8)
        self.q_prose.pack(fill="both", expand=True, pady=(0, 4))
        self.q_block = _readonly_text(tab, height=6)
        self.q_block.pack(fill="x", pady=(0, 4))

        prop_header = ttk.Frame(tab)
        prop_header.pack(fill="x")
        ttk.Label(prop_header, text="Proposal:").pack(side="left")
        ttk.Button(
            prop_header, text="Copy proposal", command=self._copy_proposal
        ).pack(side="right")
        self.q_proposal = _readonly_text(tab, height=7)
        self.q_proposal.pack(fill="both", expand=True, pady=(0, 6))

        # Correction capture (ADR-0002 §5): radios + a prominent multi-line note.
        corr = ttk.LabelFrame(tab, text="Your verdict on Claude's verdict", padding=6)
        corr.pack(fill="x")
        self.corr_var = tk.StringVar(value="")
        ttk.Radiobutton(
            corr, text="Agree", variable=self.corr_var, value="agree"
        ).pack(side="top", anchor="w")
        ttk.Radiobutton(
            corr, text="Disagree", variable=self.corr_var, value="disagree"
        ).pack(side="top", anchor="w")
        ttk.Label(corr, text="Note (required when you disagree — this is what teaches):").pack(
            anchor="w"
        )
        self.note_text = tk.Text(corr, height=3, wrap="word")
        self.note_text.pack(fill="x", pady=(0, 4))
        ttk.Button(corr, text="Submit", command=self._submit_correction).pack(
            anchor="e"
        )

    def _refresh_queue(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._records_by_id: dict[str, dict] = {}
        for rec in storage.list_queue("new"):
            iid = str(rec["id"])
            self._records_by_id[iid] = rec
            label = rec["url"] or rec["summary"][:60]
            when = rec["timestamp"][:16].replace("T", " ")
            self.tree.insert(
                "", "end", iid=iid, text=label,
                values=(rec["fit"], rec["next_step"], when),
            )
        self._current_record = None
        self._clear_review_pane()

    def _on_queue_select(self, _event: object) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        rec = self._records_by_id.get(selection[0])
        if rec is None:
            return
        self._current_record = rec
        _show_verdict(self.q_prose, self.q_block, rec)
        proposal = rec.get("proposal")
        _set_readonly(
            self.q_proposal,
            proposal if proposal else f"(no proposal — next step: {rec['next_step']})",
        )
        self.corr_var.set("")
        self.note_text.delete("1.0", "end")

    def _copy_proposal(self) -> None:
        rec = self._current_record
        if not rec or not rec.get("proposal"):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(rec["proposal"])

    def _submit_correction(self) -> None:
        rec = self._current_record
        if rec is None:
            messagebox.showinfo("No job selected", "Select a job from the queue first.")
            return
        choice = self.corr_var.get()
        if choice not in ("agree", "disagree"):
            messagebox.showwarning("Choose one", "Select Agree or Disagree.")
            return
        note = self.note_text.get("1.0", "end").strip()
        if choice == "disagree" and not note:
            # Required-note rule (ADR-0002 §5): a disagreement teaches nothing
            # without the why.
            messagebox.showwarning(
                "Note required", "A note is required when you disagree."
            )
            return

        correction = {
            "timestamp": datetime.now().isoformat(),
            "job_summary_excerpt": rec["summary"][:200],
            "claude_fit": rec["fit"],
            "claude_wheelhouse": rec["wheelhouse"],
            "claude_model_fit": rec["model_fit"],
            "claude_next_step": rec["next_step"],
            "user_correction": choice,
            "user_note": note,
        }
        storage.append_correction(correction)
        storage.update_queue_state(rec["id"], "reviewed")
        self._refresh_queue()  # drops the reviewed job and advances

    def _clear_review_pane(self) -> None:
        _set_readonly(self.q_prose, "")
        _set_readonly(self.q_block, "")
        _set_readonly(self.q_proposal, "")
        self.corr_var.set("")
        self.note_text.delete("1.0", "end")

    # --- Wheelhouse tab (§6.5: load-text / save-text only) --------------------

    def _build_wheelhouse_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="Wheelhouse")

        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        ttk.Button(bar, text="Reload", command=self._load_wheelhouse).pack(side="left")
        ttk.Button(bar, text="Save", command=self._save_wheelhouse).pack(
            side="left", padx=(6, 0)
        )
        self.wh_status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.wh_status).pack(side="left", padx=10)

        self.wh_text = tk.Text(tab, wrap="word", undo=True)
        self.wh_text.pack(fill="both", expand=True, pady=(6, 0))

    def _load_wheelhouse(self) -> None:
        try:
            content = WHEELHOUSE_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            self.wh_status.set(f"Cannot read wheelhouse.md: {exc}")
            return
        self.wh_text.delete("1.0", "end")
        self.wh_text.insert("1.0", content)
        self.wh_status.set(f"Loaded {WHEELHOUSE_PATH.name}.")

    def _save_wheelhouse(self) -> None:
        content = self.wh_text.get("1.0", "end-1c")
        try:
            WHEELHOUSE_PATH.write_text(content, encoding="utf-8")
        except OSError as exc:
            self.wh_status.set(f"Save failed: {exc}")
            return
        self.wh_status.set("Saved.")


# --- Module-level helpers -----------------------------------------------------


def _assemble_record(url: str, summary: str, annotations: str, verdict: dict) -> dict:
    """Combine the verdict with the storage-tier fields (§4.4). Main thread."""
    return {
        "url": url,
        "summary": summary,
        "annotations": annotations,
        "timestamp": datetime.now().isoformat(),
        "prose": verdict["prose"],
        "fit": verdict["fit"],
        "wheelhouse": verdict["wheelhouse"],
        "model_fit": verdict["model_fit"],
        "top_flags": verdict["top_flags"],
        "lead_with": verdict["lead_with"],
        "pricing": verdict["pricing"],
        "retainer": verdict["retainer"],
        "next_step": verdict["next_step"],
        "proposal": verdict["proposal"],
        "queue_state": "new",
    }


def _show_verdict(prose_widget: tk.Text, block_widget: tk.Text, verdict: dict) -> None:
    _set_readonly(prose_widget, verdict["prose"])
    _set_readonly(block_widget, _format_block(verdict))


def _format_block(v: dict) -> str:
    return "\n".join(
        [
            f"FIT: {v['fit']}    WHEELHOUSE: {v['wheelhouse']}    "
            f"MODEL FIT: {v['model_fit']}",
            f"TOP FLAGS: {', '.join(v['top_flags']) or 'none'}",
            f"LEAD WITH: {', '.join(v['lead_with']) or '—'}",
            f"PRICING: {v['pricing']}",
            f"RETAINER: {v['retainer']}    NEXT STEP: {v['next_step']}",
        ]
    )


def _strip_summary_header(summary: str) -> str:
    """Drop a leading 'Summary' header line if the paste included it (§6.1)."""
    lines = summary.splitlines()
    if lines and lines[0].strip().lower() == "summary":
        return "\n".join(lines[1:]).strip()
    return summary


def _readonly_text(parent: tk.Misc, height: int) -> tk.Text:
    """A Text the user can select and copy from but not edit.

    A `state="disabled"` Text blocks *selection* too, so instead we leave it
    editable and swallow editing keystrokes — mouse selection and Ctrl+C/Ctrl+A
    keep working, typing and paste do not.
    """
    widget = tk.Text(parent, height=height, wrap="word")
    widget.bind("<Key>", _block_edit_keys)
    return widget


def _block_edit_keys(event: tk.Event) -> str | None:
    ctrl = (event.state & 0x4) != 0
    if ctrl and event.keysym.lower() in ("c", "a"):
        return None  # copy / select-all
    if event.keysym in (
        "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
        "Shift_L", "Shift_R", "Control_L", "Control_R",
    ):
        return None  # navigation and modifier keys (allow shift-select)
    return "break"  # everything else (typing, paste, delete) is blocked


def _set_readonly(widget: tk.Text, content: str) -> None:
    widget.delete("1.0", "end")
    widget.insert("1.0", content)
