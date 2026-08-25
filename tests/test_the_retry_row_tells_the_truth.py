"""Bench run 19, case 05 t5: the retry's own review read yesterday's row.

`_retry_failed_tasks` recorded the retry's outcome only AFTER `_attempt_task`
returned, and the review runs INSIDE the attempt — so it read the stale
`build_failed / workspace reset` row from disk next to a commit that
implements the task, and flagged the shipped work as contradicting the
product's own record. A critical finding, a rollback, and a genuine recovery
lost to bookkeeping that described the past. The row is now rewritten before
the attempt to say the retry is in progress; the transient note is replaced
by the final row either way.
"""

from __future__ import annotations

from types import SimpleNamespace

from ai_venture_studio.upstream import autopilot
from ai_venture_studio.upstream.autopilot import TaskOutcome


def _run_retry(tmp_path, monkeypatch, *, result_status: str):
    root = tmp_path / "p"
    (root / "product").mkdir(parents=True)
    prior = TaskOutcome(
        task_id="t5",
        title="repairs endpoint",
        status="build_failed",
        detail="build gate still failing after max iterations; nothing "
        "committed (failed attempt preserved at .mas/x; workspace reset)",
    )
    outcomes = [prior]
    seen: dict = {}

    def fake_attempt(root_, task, **kwargs):
        seen["disk"] = (root / "product" / "outcomes.yaml").read_text(
            encoding="utf-8"
        )
        return TaskOutcome(
            task_id="t5", title="repairs endpoint", status=result_status
        )

    monkeypatch.setattr(autopilot, "_attempt_task", fake_attempt)
    autopilot._retry_failed_tasks(
        root,
        [SimpleNamespace(id="t5", title="repairs endpoint")],
        outcomes,
        provider="mock",
        model="m",
        fdr_text="",
        auto_approvals=[],
    )
    final = (root / "product" / "outcomes.yaml").read_text(encoding="utf-8")
    return seen["disk"], final


def test_the_disk_row_describes_the_retry_while_it_runs(tmp_path, monkeypatch):
    during, final = _run_retry(tmp_path, monkeypatch, result_status="built")
    assert "auto-retry in progress" in during
    assert "records the first attempt" in during
    # The first attempt's diagnosis still travels with the transient row.
    assert "workspace reset" in during
    # The transient note never outlives the attempt.
    assert "auto-retry in progress" not in final
    assert "(recovered on auto-retry)" in final


def test_a_failed_retry_keeps_both_diagnoses_and_no_transient_note(
    tmp_path, monkeypatch
):
    during, final = _run_retry(tmp_path, monkeypatch, result_status="build_failed")
    assert "auto-retry in progress" in during
    assert "auto-retry in progress" not in final
    assert "auto-retry also failed; first attempt: build_failed" in final
