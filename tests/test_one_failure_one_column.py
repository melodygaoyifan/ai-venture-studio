"""ADR-061: one failure must cost one column, and our bugs are not theirs.

Both defects here were found by mining run 18's recorded rows rather than by
paying for run 19 — the cheap half of the same question ADR-060 asked
mechanically. Neither had ever been visible in a headline: each one moved a
rate in the direction of "worse", which is the direction nobody audits.

  1. A case blocked before it built anything scored a hard 0.0 on the probe
     axis as well as the build axis. `clean_review_rate` had the correct rule
     written on it verbatim, one property below, and the probe column simply
     did not apply it.
  2. A probe that cannot parse is our defect, and it was scored against the
     product it was supposed to be measuring.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_venture_studio import bench_criterion, notify
from ai_venture_studio.product_bench import (
    CaseResult,
    Probe,
    ProbeResult,
    run_probe,
    summarise,
)


def _case(name: str = "c", **kw) -> CaseResult:
    return CaseResult(name=name, autopilot_status="completed", **kw)


# --------------------------------------------------------------------------
# One failure, one column
# --------------------------------------------------------------------------
def test_a_case_that_built_nothing_has_no_probe_reading():
    """Run 18's `03-groupbuy-auto`, reduced to its shape: blocked at planning,
    0 of 0 tasks, and a probe column that read 0.0 for a reading nobody took."""
    case = _case(
        tasks_total=0,
        tasks_built=0,
        failure_reason="planning blocked: lane collision",
        probes=[ProbeResult(name="probe-generation", passed=False)],
    )
    assert case.probe_pass_rate is None
    # The failure is not forgiven — it is counted ONCE, where it happened.
    assert case.build_rate == 0.0


def test_the_probe_column_still_scores_a_case_that_built_something():
    """The control. The exclusion above is about having no product to probe,
    not about probes being optional once a case is in trouble."""
    case = _case(
        tasks_total=2,
        tasks_built=1,
        probes=[ProbeResult(name="p1", passed=True),
                ProbeResult(name="p2", passed=False)],
    )
    assert case.probe_pass_rate == 0.5
    assert case.build_rate == 0.5


def test_the_three_axes_disagree_on_purpose():
    """`measured`, `build_rate` and `probe_pass_rate` answer three different
    questions about the same zero, and ADR-035's rule survives this change:
    a case that RAN and built nothing is measured, and its build rate is a
    real zero, not an absence."""
    case = _case(tasks_total=4, tasks_built=0)
    assert case.measured is True
    assert case.build_rate == 0.0
    assert case.probe_pass_rate is None
    assert case.clean_review_rate is None


# --------------------------------------------------------------------------
# Our bug is not their failure
# --------------------------------------------------------------------------
def test_a_probe_that_cannot_parse_is_charged_to_us(tmp_path):
    result = run_probe(tmp_path, Probe(name="broken", script="def (:\n"))
    assert result.passed is False
    assert result.harness_fault is True
    assert "OUR bug" in result.detail
    # And it says which one. "probe failed" was the shape that cost run 18 a
    # five-hour, sixty-eight-dollar reading of the wrong thing.
    assert "SyntaxError" in result.detail or "invalid syntax" in result.detail


def test_a_harness_fault_leaves_the_denominator_rather_than_scoring_zero():
    case = _case(
        tasks_total=1,
        tasks_built=1,
        probes=[ProbeResult(name="ok", passed=True),
                ProbeResult(name="ours", passed=False, harness_fault=True)],
    )
    assert case.probe_pass_rate == 1.0


def test_a_case_whose_every_probe_was_ours_has_no_reading():
    """Not 100%. Excluding a broken instrument must not manufacture a pass —
    the reading is missing, and missing is the honest answer (ADR-035)."""
    case = _case(
        tasks_total=1,
        tasks_built=1,
        probes=[ProbeResult(name="ours", passed=False, harness_fault=True)],
    )
    assert case.probe_pass_rate is None


def test_a_harness_fault_is_still_recorded_in_the_row():
    """Excluded from the rate, never from the record. A defect in our
    instrument that vanishes from the row is one nobody fixes."""
    case = _case(
        tasks_total=1, tasks_built=1,
        probes=[ProbeResult(name="ours", passed=False, harness_fault=True,
                            detail="probe does not parse")],
    )
    dumped = case.model_dump()
    assert dumped["probes"][0]["harness_fault"] is True
    assert dumped["probes"][0]["detail"] == "probe does not parse"


def test_an_older_probe_row_still_loads():
    """Every result file on disk was written before this field existed."""
    # `model_validate` on a plain dict, not kwargs: the point is that this
    # is the shape a row comes off disk in, missing the new field.
    assert ProbeResult.model_validate(
        {"name": "p", "passed": True}
    ).harness_fault is False


def test_a_working_probe_is_not_flagged_as_ours(tmp_path):
    """The other control: the compile guard must not intercept probes that
    are merely failing, which is what a probe is for."""
    result = run_probe(tmp_path, Probe(name="honest", script="raise SystemExit(1)"))
    assert result.passed is False
    assert result.harness_fault is False


# --------------------------------------------------------------------------
# The price of the exclusion: it has to be visible
# --------------------------------------------------------------------------
def test_a_narrower_probe_denominator_names_itself():
    """Run 16's correction, applied to the fix for run 18: "the exclusion is
    per-rate, and it should be per-case". A case can now be measured, score a
    real 0.0 on build, and still be outside the probe average — which is only
    honest if the summary says so."""
    summary = summarise([
        _case(name="ok", tasks_total=2, tasks_built=2,
              probes=[ProbeResult(name="p", passed=True)]),
        _case(name="blocked-at-planning", tasks_total=0, tasks_built=0,
              probes=[ProbeResult(name="probe-generation", passed=False)]),
    ])
    assert summary.probe_pass_rate == 1.0
    assert summary.build_rate == 0.5
    # Not unmeasured — it ran, and its build zero is real.
    assert summary.unmeasured == []
    assert summary.no_probe_reading == ["blocked-at-planning"]


def test_a_run_where_every_case_built_says_nothing_extra():
    """The control. A caveat that always prints is one nobody reads."""
    summary = summarise([
        _case(name="ok", tasks_total=1, tasks_built=1,
              probes=[ProbeResult(name="p", passed=True)]),
    ])
    assert summary.no_probe_reading == []


def test_the_alert_carries_the_narrower_denominator_too():
    """A qualifier that reaches only the operator's screen is one the 3am
    reader did not get."""
    summary = summarise([
        _case(name="ok", tasks_total=2, tasks_built=2,
              probes=[ProbeResult(name="p", passed=True)]),
        _case(name="blocked-at-planning", tasks_total=0, tasks_built=0),
    ])
    body = notify.bench_alert(summary, provider="mock").render()
    assert "blocked-at-planning built nothing to probe" in body
    assert "1 of 2 cases" in body


# --------------------------------------------------------------------------
# The worst run must stay visible to the criterion
# --------------------------------------------------------------------------
def _ledger(tmp_path, name, **rates):
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / name).write_text(yaml.safe_dump(rates), encoding="utf-8")


def test_a_run_that_built_nothing_is_still_judged(tmp_path):
    """The trap this change walked into and out of. Once a nothing-built case
    has no probe reading, a run where EVERY case failed writes a null probe
    rate — and the kill criterion used to skip any run with one. The worst
    reading the series can produce would have become the one it could not
    see."""
    _ledger(tmp_path, "result-2026-01-01-0000.yaml",
            build_rate=0.0, probe_pass_rate=None, clean_review_rate=None)
    state = bench_criterion.evaluate(tmp_path)
    assert len(state.runs_considered) == 1
    assert state.runs_considered[0].below_floor is True
    assert state.streak == 1


def test_a_missing_probe_rate_is_not_a_failing_one(tmp_path):
    """The other half: a run comfortably over the build floor with no probe
    reading must not be pushed under a floor it has no number for."""
    _ledger(tmp_path, "result-2026-01-02-0000.yaml",
            build_rate=1.0, probe_pass_rate=None)
    run = bench_criterion.load_runs(tmp_path)[0]
    assert run.below_floor is False
    assert "probes not measured" in run.summary()


def test_an_empty_build_axis_is_still_skipped(tmp_path):
    """Unchanged, and the reason the two nulls had to be told apart: a run
    that never asked the build question makes no claim to judge."""
    _ledger(tmp_path, "result-2026-01-03-0000.yaml",
            build_rate=None, probe_pass_rate=None)
    assert bench_criterion.load_runs(tmp_path) == []


# --------------------------------------------------------------------------
# The run this was mined from
# --------------------------------------------------------------------------
def test_run_18s_recorded_rates_are_left_alone():
    """The series is only worth something if old readings are not re-scored
    to match new code (ADR-051). Run 18's file says 0.75 and must keep saying
    it; HISTORY.md carries the note that the rule changed after it.
    """
    path = (Path(__file__).resolve().parent.parent
            / "benchmarks" / "results" / "result-2026-08-20-1459.yaml")
    if not path.exists():  # pragma: no cover - the ledger is not a fixture
        return
    recorded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert recorded["probe_pass_rate"] == 0.75
