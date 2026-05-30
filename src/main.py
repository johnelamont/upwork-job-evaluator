"""Entry point: wires UI, judgment, storage.

Validates the environment, opens local storage (which starts the backup timer),
builds the Tkinter window, and guarantees the DB is closed cleanly on exit so
the on-close OneDrive backup runs and no -wal/-shm sidecars are left mid-write
(ARCHITECTURE §6.3). The judgment module is never wired to storage directly;
the UI is the only seam between them (§6.2).
"""

import os
import sys
import tkinter as tk

import storage
from ui import App


def main() -> int:
    # API key is read from the environment only (§5). Absence is a clear,
    # up-front startup error rather than a failure on the first Claude call.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set. Set it before running "
            "(see README Setup).",
            file=sys.stderr,
        )
        return 1

    storage.open_storage()
    try:
        root = tk.Tk()
        App(root)

        def on_close() -> None:
            storage.close_storage()
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()
    finally:
        # Idempotent: harmless if on_close already closed it, and the backstop
        # for an exit that bypassed the window-close handler.
        storage.close_storage()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
