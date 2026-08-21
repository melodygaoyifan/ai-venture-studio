"""`--limit` wrote a truncated run into the capability ledger (ADR-066).

Found while answering "is there another way to run bench 19", where the
account cannot afford five hours in one sitting. The obvious answer — buy it a
case at a time with `--limit`, bank the checkpoints, close with `--resume` —
was the trap: `run_product_bench` sliced the case list before counting it, so
`cases_total` was the number of cases the run was HANDED, not the number the
suite has. A `--limit 1` run wrote `1 of 1`.

That file is not partial (`cases_measured == cases_total`), not aborted, and
not simulated, so every guard on the ledger passed it through as a complete
reading of the suite. The five-case suite has a case that has built nothing in
two consecutive runs; a slice that lands on it reads `build 0% over 1 of 1`,
which is below floor, and `CONSECUTIVE_RUNS_TO_FIRE = 2` — two such purchases
fire a criterion whose only remedy is a human deciding whether to kill the
project.

Third instance of one shape, and worth naming as such: ADR-053 (a rate over no
cases), ADR-056 (a reading that cannot name its instrument), and this one are
all *the cheap substitute for an expensive measurement corrupting the ledger
the measurement lives in*.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

import ai_venture_studio.product_bench as pb
from ai_venture_studio.bench_criterion import evaluate, truncated_runs

CASES = "benchmarks/products"
SUITE = len(list(pathlib.Path(CASES).glob("*.yaml")))


def _completed(case, provider=None, **_):
    return pb.CaseResult(
        name=case.name,
        autopilot_status="completed",
        tasks_total=2,
        tasks_built=2,
        clean_reviews=1,
        probes=[pb.ProbeResult(name="p", passed=True)],
        axis=case.axis,
    )


@pytest.fixture
def bench(monkeypatch):
    """The suite, with every case that is actually run scoring perfectly.

    Perfect scores on purpose: the finding is not about bad numbers. A slice
    that scores 100% is just as wrong in the ledger as one that scores 0% —
    it is a complete-looking reading of a suite nobody ran.
    """
    monkeypatch.setattr(pb, "run_case", _completed)


# ---------------------------------------------------------------------------
# The denominator
# ---------------------------------------------------------------------------


def test_a_limited_run_counts_the_whole_suite(bench, tmp_path):
    """THE defect. `--limit 1` used to report one case of one."""
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    assert summary.cases_total + summary.gate_cases_total == SUITE, (
        "the limit shrank the denominator — a slice is claiming to be the suite"
    )
    assert summary.cases_measured == 1


def test_the_cases_nobody_paid_for_are_named(bench, tmp_path):
    """`unmeasured` has to list them, or the file records a smaller suite
    rather than an unfinished run."""
    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)
    unasked = set(summary.unmeasured) | set(summary.gate_unmeasured)
    assert len(unasked) == SUITE - 2


def test_a_skipped_case_says_why_it_was_skipped(bench, tmp_path):
    """"Unmeasured" covers a case that crashed and a case nobody bought, and
    those are different findings with different next steps (ADR-058). The row
    carries the distinction the name list cannot."""
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    skipped = [c for c in summary.cases if c.name not in {summary.cases[0].name}]
    assert skipped and all("--limit 1" in c.autopilot_status for c in skipped)
    assert all(not c.measured for c in skipped)


def test_a_skipped_case_is_not_scored_as_a_zero(bench, tmp_path):
    """ADR-035's rule, on the new row type: a case that was never asked is
    dropped from the rate, never entered as a failure. A slice that scored
    100% on what it ran must not read as 20%."""
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    assert summary.build_rate == 1.0


def test_an_unlimited_run_is_unchanged(bench, tmp_path):
    """The control: nothing about the ordinary path moves."""
    summary = pb.run_product_bench(CASES, repo_dir=tmp_path)
    assert summary.truncated is False
    assert summary.cases_measured == summary.cases_total


def test_a_limit_covering_the_suite_is_not_truncated(bench, tmp_path):
    """`truncated` is read off the rows, not off the flag. `--limit 6` over
    six cases asked every one of them, and refusing it would be a second
    defect wearing the first one's clothes."""
    summary = pb.run_product_bench(CASES, limit=SUITE, repo_dir=tmp_path)
    assert summary.truncated is False


# ---------------------------------------------------------------------------
# Layer 1: it does not reach the tracked ledger
# ---------------------------------------------------------------------------


def _write(summary, tmp_path):
    (tmp_path / "benchmarks" / "results").mkdir(parents=True, exist_ok=True)
    return pb.save_summary(summary, tmp_path, provider="anthropic")


def test_a_slice_stays_out_of_the_tracked_results(bench, tmp_path):
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    saved = _write(summary, tmp_path)
    assert saved.exists(), "the operator's own copy must still be written"
    assert not (tmp_path / "benchmarks" / "results" / saved.name).exists(), (
        "a truncated run reached the directory the kill criterion reads"
    )


def test_a_complete_run_still_reaches_the_tracked_results(bench, tmp_path):
    """The half that proves the guard is narrow. A guard that kept everything
    out would pass the test above and silently end the series."""
    summary = pb.run_product_bench(CASES, repo_dir=tmp_path)
    saved = _write(summary, tmp_path)
    assert (tmp_path / "benchmarks" / "results" / saved.name).exists()


def test_a_limit_that_covered_the_suite_is_still_a_reading(bench, tmp_path):
    """The inverted defect, and it was live in this change's first draft.

    `limited_to` is a model field, so `model_dump` put it in the payload on
    its own — a complete `--limit 6` run wrote `limited_to: 6`, and the
    criterion would have refused a perfectly good reading for carrying a key
    about a flag. The key is placed by `save_summary` or not at all.
    """
    summary = pb.run_product_bench(CASES, limit=SUITE, repo_dir=tmp_path)
    saved = _write(summary, tmp_path)
    data = yaml.safe_load(saved.read_text(encoding="utf-8"))
    assert "limited_to" not in data
    assert (tmp_path / "benchmarks" / "results" / saved.name).exists()
    assert truncated_runs(tmp_path) == []


def test_the_file_says_it_was_a_slice(bench, tmp_path):
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    data = yaml.safe_load(_write(summary, tmp_path).read_text(encoding="utf-8"))
    assert data["limited_to"] == 1
    assert data["rates"]["cases_measured"] < data["rates"]["cases_total"]


# ---------------------------------------------------------------------------
# Layer 2: and if one arrives anyway, the criterion refuses it BY NAME
# ---------------------------------------------------------------------------


def _tracked(tmp_path, name, payload):
    out = tmp_path / "benchmarks" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(yaml.safe_dump(payload), encoding="utf-8")


def _slice_payload(build_rate):
    return {
        "build_rate": build_rate, "probe_pass_rate": 0.2,
        "clean_review_rate": 0.2, "cases": [], "limited_to": 1,
        "rates": {"cases_measured": 1, "cases_total": 5},
    }


def test_a_hand_copied_slice_is_refused(tmp_path):
    """Copied in by hand, or written by a build older than this rule. Two
    layers because either alone is silent about the other's case (ADR-056)."""
    _tracked(tmp_path, "result-2026-08-22-0000.yaml", _slice_payload(0.0))
    assert truncated_runs(tmp_path) == [
        "benchmarks/results/result-2026-08-22-0000.yaml"
    ]
    assert evaluate(tmp_path).runs_considered == []


def test_two_slices_cannot_fire_the_kill_criterion(tmp_path):
    """The scenario in full, and the reason this is not a tidiness fix.

    Buying run 19 two cases at a time, landing on the case that builds
    nothing: before ADR-066 this was two consecutive below-floor runs, and the
    criterion asks a human to consider killing the project.
    """
    _tracked(tmp_path, "result-2026-08-22-0000.yaml", _slice_payload(0.0))
    _tracked(tmp_path, "result-2026-08-23-0000.yaml", _slice_payload(0.0))
    state = evaluate(tmp_path)
    assert state.fires is False
    assert state.streak == 0
    assert len(state.truncated_skipped) == 2


def test_a_refused_slice_is_named_not_silently_dropped(tmp_path):
    """Excluding a file without mentioning it is the defect ADR-054's own
    first draft shipped. A reader comparing the directory to the ledger has to
    be able to find out why a file they can see is not counted."""
    _tracked(tmp_path, "result-2026-08-22-0000.yaml", _slice_payload(0.8))
    state = evaluate(tmp_path)
    assert state.truncated_skipped == [
        "benchmarks/results/result-2026-08-22-0000.yaml"
    ]


def test_a_slice_does_not_reset_the_bench_cadence(tmp_path):
    """The watchdog reads the same directory, and a watchdog that counts a
    slice reports "ran recently, all clear" about a suite nobody read
    (ADR-056's rule, applied to the new exclusion)."""
    import datetime

    from ai_venture_studio.cadence import BENCH_CASES, _bench_status

    _tracked(tmp_path, "result-2026-08-22-0000.yaml", _slice_payload(0.8))
    (tmp_path / BENCH_CASES).mkdir(parents=True, exist_ok=True)
    status = _bench_status(tmp_path, datetime.date(2026, 9, 30))
    assert status is not None
    assert status.last_run == "", (
        "a slice was counted as the bench having run"
    )


# ---------------------------------------------------------------------------
# And the third reader: the alert
# ---------------------------------------------------------------------------


def test_the_alert_says_slice_in_its_heading(bench, tmp_path):
    """Real percentages, real provider, real spend — nothing in the numbers
    marks it, and the reader waiting on the weekly reading is exactly the one
    who would take it for one (ADR-056's argument, same shape)."""
    from ai_venture_studio.notify import bench_alert

    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    alert = bench_alert(summary, provider="anthropic")
    assert "SLICE" in alert.heading
    assert any("--resume" in line for line in alert.lines)


def test_a_complete_run_is_not_labelled_a_slice(bench, tmp_path):
    from ai_venture_studio.notify import bench_alert

    summary = pb.run_product_bench(CASES, repo_dir=tmp_path)
    assert "SLICE" not in bench_alert(summary, provider="anthropic").heading
