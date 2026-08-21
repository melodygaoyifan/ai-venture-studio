"""The per-task step journal — what the run is doing RIGHT NOW.

The gap this closes is the founder signal the launch PRD is built on: *"I
started a build and stared at the terminal for 40 minutes with no idea whether
it was progressing or stuck."* That signal was answered at the task level in
v0.34 (the Studio's per-task panel) and remains unanswered *inside* a task,
which is where the 40 minutes actually go. One task is a spec writer plus five
charter critics, a verify pass and a leader, then up to three
implement-and-run-the-suite iterations, then six review voters with their own
verify pass, then possibly a fix iteration. Until it ends, the task is `pending`
and every one of those steps looks identical from outside: nothing.

Three properties, because a progress channel that lies is worse than none:

- **Observed, never predicted.** A step is appended when the thing has actually
  started. There are no percentages and no ETAs — the system genuinely does not
  know whether iteration 2 of 3 will be the last one, and inventing a number
  would be the same class of dishonesty the claim ledger exists to prevent.
- **Append-only, and never load-bearing.** This is a record of what happened, in
  `.mas/` with the rest of the run history. Nothing reads it back to make a
  decision, so a lost or truncated journal can never change a build's outcome —
  and `step()` swallows its own I/O errors for the same reason. A disk that
  cannot take a log line must not fail a build that is otherwise fine.
- **One source of truth.** The CLI and the Studio render the same journal; the
  Studio stays a veneer over files the CLI writes.

Live console output is opt-in through `set_sink`, so a long `avs create` can
narrate itself without this module knowing what a terminal is.
"""

from __future__ import annotations

import logging
import datetime as dt
import json
import pathlib
import threading
from collections.abc import Callable

#: Where a deliberate degradation says what it degraded. Every handler
#: below that skips a row, a page, or a piece of bookkeeping logs here
#: first: CLAUDE.md forbids swallowing an exception silently, and until
#: ADR-062 nothing enforced it (`S110`/`S112` found 15). DEBUG, so it is
#: silent unless asked for — `AVS_DEBUG=1` is the ask.
_log = logging.getLogger(__name__)

JOURNAL_FILE = "progress.jsonl"

#: Task id for steps that belong to the RUN rather than to any one task —
#: the assess/brief/plan stretch before tasks exist. That stretch is the
#: longest silent part of a build (a writer, four charter voters with a
#: verify pass each, a leader, then planning) and it used to render as a
#: bare "planning…" for minutes, which is the same 40-minutes-of-nothing
#: this module exists to end.
SETUP = "setup"

_lock = threading.Lock()
_sink: Callable[[str], None] | None = None


def set_sink(sink: Callable[[str], None] | None) -> None:
    """Route steps to a live consumer (the CLI console) as well as the journal.

    Deliberately a module-level hook rather than a parameter threaded through
    the stages: every stage function would otherwise grow a `reporter` argument
    it does nothing with but pass along."""
    global _sink
    _sink = sink


def _journal(repo_dir: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(repo_dir) / ".mas" / JOURNAL_FILE


def step(
    repo_dir: str | pathlib.Path,
    task_id: str,
    stage: str,
    detail: str,
) -> None:
    """Record that `task_id` has just entered `detail` within `stage`.

    `stage` is coarse (spec | build | review | fix) and `detail` is the
    human-readable present-tense thing happening, in the founder's vocabulary
    where there is a choice: "running your tests", not "gate 2".
    """
    entry = {
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "task_id": task_id,
        "stage": stage,
        "detail": detail,
    }
    line = json.dumps(entry, ensure_ascii=False)
    if _sink is not None:
        try:
            _sink(f"  {task_id} · {stage}: {detail}")
        except Exception as exc:  # noqa: BLE001 — a broken console never fails a build
            _log.debug("progress sink refused a line; the journal below "
                       "still gets it: %s", exc)
    try:
        path = _journal(repo_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        # See the module docstring: this journal is a record, never an input.
        pass


def steps(
    repo_dir: str | pathlib.Path, task_id: str | None = None, limit: int = 0
) -> list[dict]:
    """Journal entries oldest-first, optionally for one task.

    A malformed line is skipped rather than raising: the file is appended to by
    a long-running build that can be killed mid-write, and a half-written last
    line must not make the whole history unreadable."""
    path = _journal(repo_dir)
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if task_id is not None and entry.get("task_id") != task_id:
            continue
        out.append(entry)
    return out[-limit:] if limit > 0 else out


def current(repo_dir: str | pathlib.Path) -> dict | None:
    """The most recent step across all tasks — "what is it doing right now"."""
    all_steps = steps(repo_dir)
    return all_steps[-1] if all_steps else None


def latest_by_task(repo_dir: str | pathlib.Path) -> dict[str, dict]:
    """Task id → its most recent step. The Studio's per-task line."""
    latest: dict[str, dict] = {}
    for entry in steps(repo_dir):
        task_id = entry.get("task_id")
        if isinstance(task_id, str):
            latest[task_id] = entry
    return latest
