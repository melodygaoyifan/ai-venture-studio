"""A rate over no cases is not a rate (ADR-053).

Found while pricing a cheaper alternative to a full five-case bench run:
run only the increment case, get the gate rate that has never been measured,
and pay ~2h instead of ~5h. That run is well-formed — the increment axis is
scored on its own denominator by construction (ADR-049) — but the build axis
comes out EMPTY, and `_avg([])` returned `0.0`.

Which meant the saved file said `build_rate: 0.0, probe_pass_rate: 0.0`, and
`bench_criterion.below_floor` is `build_rate < 0.60 or probe_pass_rate < 0.50`
with no measured-check. So the cheap run would have entered the capability
ledger below floor, and `CONSECUTIVE_RUNS_TO_FIRE = 2` means one more would
fire a criterion that asks a human to consider killing the project — over a
run that never asked the question.

This is ADR-035's own rule, broken one level above where ADR-035 enforced it:
`CaseResult.build_rate` has returned None for an unmeasured case since then;
`BenchSummary.build_rate` was typed `float` and flattened it back to a zero.

The two halves here must fail independently, and the second is the important
one: a real zero must still be a zero, or this fix would blind the criterion
it was written to protect.

The third group covers the same defect where the audit found it again — in
`product/loop_metrics.py`, one layer above per-metric code that already had
it right, which is the pattern rather than the coincidence.
"""

from __future__ import annotations

import ai_venture_studio.bench_criterion as bc
import ai_venture_studio.cadence as cadence
import ai_venture_studio.notify as notify
import ai_venture_studio.product_bench as pb
# From the module, not the package: `product/__init__.py` re-exports the
# `loop_metrics` FUNCTION, which shadows the submodule of the same name.
from ai_venture_studio.product.loop_metrics import (
    evidence_quality_ratio,
    hypothesis_resolution_rate,
    loop_metrics,
)
from ai_venture_studio.product.reconcile import HypothesisVerdict


def _increment_case_dir(tmp_path):
    """A cases dir holding exactly one increment-axis case."""
    d = tmp_path / "cases"
    d.mkdir()
    (d / "05-only.yaml").write_text(
        "name: 05-only\n"
        "profile: web\n"
        "axis: increment\n"
        "fdr: 报修\n"
        "feature_fdrs: ['a']\n"
        "feature_expectations: [completed]\n"
        "probes:\n"
        "- name: p\n"
        "  script: 'print(1)'\n",
        encoding="utf-8",
    )
    return d


def _increment_result(name="05-only", *, correct=True):
    return pb.CaseResult(
        name=name,
        autopilot_status="completed",
        axis="increment",
        increments=[
            pb.IncrementResult(
                index=0, fdr="a", expected="completed",
                actual="completed", correct=correct,
            )
        ],
    )


def _build_result(name, *, total, built, clean):
    return pb.CaseResult(
        name=name,
        autopilot_status="completed",
        axis="build",
        tasks_total=total,
        tasks_built=built,
        clean_reviews=clean,
        probes=[pb.ProbeResult(name="p", passed=True)],
    )


# ---------------------------------------------------------------------------
# An empty axis
# ---------------------------------------------------------------------------


def test_an_empty_build_axis_scores_no_build_rate(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "run_case", lambda case, provider=None: _increment_result())
    summary = pb.run_product_bench(
        _increment_case_dir(tmp_path), provider="mock", repo_dir=str(tmp_path)
    )
    assert summary.build_rate is None
    assert summary.probe_pass_rate is None
    assert summary.clean_review_rate is None
    # The question the run DID ask still has its answer.
    assert summary.gate_rate == 1.0


def test_the_saved_file_says_null_not_zero(monkeypatch, tmp_path):
    import yaml

    monkeypatch.setattr(pb, "run_case", lambda case, provider=None: _increment_result())
    summary = pb.run_product_bench(
        _increment_case_dir(tmp_path), provider="mock", repo_dir=str(tmp_path)
    )
    path = pb.save_summary(summary, tmp_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Present and null, not absent: an absent key is indistinguishable from a
    # file written before the field existed.
    assert "build_rate" in data["rates"]
    assert data["rates"]["build_rate"] is None
    assert data["rates"]["probe_pass_rate"] is None
    assert data["rates"]["clean_review_rate"] is None
    assert data["rates"]["increment"]["gate_rate"] == 1.0


def test_a_run_with_no_build_axis_never_reaches_the_floor(monkeypatch, tmp_path):
    """The whole reason this matters: the capability criterion."""
    monkeypatch.setattr(pb, "run_case", lambda case, provider=None: _increment_result())
    summary = pb.run_product_bench(
        _increment_case_dir(tmp_path), provider="mock", repo_dir=str(tmp_path)
    )
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True)
    pb.save_summary(summary, tmp_path)

    state = bc.evaluate(tmp_path)
    assert state.streak == 0
    assert state.fires is False
    # Not merely "does not fire" — it is not in the ledger at all, because it
    # made no claim a floor could judge.
    assert [r.path for r in state.runs_considered] == []


def test_the_cadence_reports_nothing_rather_than_zero(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text(
        "build_rate: null\nprobe_pass_rate: null\n"
        "rates: {cases_measured: 0, cases_total: 0}\n",
        encoding="utf-8",
    )
    # "build 0%, probes 0%" is the reading this prevents — and note it would
    # NOT have carried the "over N of M cases" qualifier, because that line
    # only appears when measured < total, and 0 < 0 is false.
    assert cadence._bench_rates(str(path)) == ""


def test_neither_the_table_nor_the_alert_prints_a_percent(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "run_case", lambda case, provider=None: _increment_result())
    summary = pb.run_product_bench(
        _increment_case_dir(tmp_path), provider="mock", repo_dir=str(tmp_path)
    )
    body = notify.bench_alert(summary).render()
    assert "not measured" in body
    assert "build 0%" not in body


# ---------------------------------------------------------------------------
# A real zero is still a zero
#
# If this fix made the criterion blind, it would be worse than the defect.
# ---------------------------------------------------------------------------


def _run_build_cases(monkeypatch, tmp_path, results):
    calls = iter(results)
    monkeypatch.setattr(pb, "run_case", lambda case, provider=None: next(calls))
    d = tmp_path / "cases"
    d.mkdir()
    for i in range(len(results)):
        (d / f"{i:02d}-c.yaml").write_text(
            f"name: {i:02d}-c\nprofile: web\nfdr: x\n"
            "probes:\n- name: p\n  script: 'print(1)'\n",
            encoding="utf-8",
        )
    return pb.run_product_bench(d, provider="mock", repo_dir=str(tmp_path))


def test_a_case_that_built_nothing_still_scores_zero(monkeypatch, tmp_path):
    """`tasks_total == 0` is a measured, total build failure — not an absence."""
    summary = _run_build_cases(
        monkeypatch, tmp_path, [_build_result("00-c", total=0, built=0, clean=0)]
    )
    assert summary.build_rate == 0.0


def test_a_real_zero_still_fires_the_floor(monkeypatch, tmp_path):
    summary = _run_build_cases(
        monkeypatch, tmp_path, [_build_result("00-c", total=4, built=0, clean=0)]
    )
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True)
    pb.save_summary(summary, tmp_path)
    state = bc.evaluate(tmp_path)
    assert len(state.runs_considered) == 1
    assert state.runs_considered[0].below_floor is True
    assert state.streak == 1


def test_a_mixed_run_keeps_the_build_rate_it_earned(monkeypatch, tmp_path):
    """The ordinary five-case shape: an increment case beside build cases
    must not turn the headline rates into None."""
    calls = iter([
        _build_result("00-c", total=4, built=4, clean=4),
        _increment_result("01-c"),
    ])
    monkeypatch.setattr(pb, "run_case", lambda case, provider=None: next(calls))
    d = tmp_path / "cases"
    d.mkdir()
    (d / "00-c.yaml").write_text(
        "name: 00-c\nprofile: web\nfdr: x\nprobes:\n- name: p\n  script: 'print(1)'\n",
        encoding="utf-8",
    )
    (d / "01-c.yaml").write_text(
        "name: 01-c\nprofile: web\naxis: increment\nfdr: x\n"
        "feature_fdrs: ['a']\nfeature_expectations: [completed]\n"
        "probes:\n- name: p\n  script: 'print(1)'\n",
        encoding="utf-8",
    )
    summary = pb.run_product_bench(d, provider="mock", repo_dir=str(tmp_path))
    assert summary.build_rate == 1.0
    assert summary.gate_rate == 1.0


def test_an_all_unmeasured_build_axis_is_absent_not_zero(monkeypatch, tmp_path):
    """The other way to get an empty average: every build case crashed.

    ADR-035 already drops each from the rate; what was left was the run-level
    reading, which said 0% — the exact sentence ADR-035 exists to prevent, in
    the file the kill criterion reads.
    """
    summary = _run_build_cases(
        monkeypatch, tmp_path,
        [pb.CaseResult(name="00-c", autopilot_status="error: boom", axis="build")],
    )
    assert summary.build_rate is None
    assert summary.unmeasured == ["00-c"]


# ---------------------------------------------------------------------------
# The rest of the class
#
# The audit behind ADR-053's "rest of the class" section found the same shape
# once more, in `product/loop_metrics.py` — where two of the five metrics
# already returned None on an empty denominator and said why, and two beside
# them returned 0.0. Nothing consumed those two at n=0 yet, which is why it
# survived; that is the same reason the bench defect survived seventeen runs.
# ---------------------------------------------------------------------------


def test_a_stage_with_no_claims_has_no_evidence_quality():
    """0.0 here reads as "every claim in this stage is weak" about a stage
    that made none — an accusation manufactured from an absence."""
    ratios = evidence_quality_ratio({"market": {"claims": []}, "user": {}})
    assert ratios == {"market": None, "user": None}


def test_a_loop_with_no_hypotheses_is_not_a_ratchet():
    """The function's own docstring calls a loop that resolves nothing a
    ratchet. A loop that has not opened a hypothesis has not earned that."""
    assert hypothesis_resolution_rate([]) is None


def test_the_metrics_object_carries_the_nulls():
    """Typed `float`, `LoopMetrics` would have coerced or rejected these —
    the field types are half the fix, as they were for `BenchSummary`."""
    metrics = loop_metrics(
        ledgers_by_stage={"market": {"claims": []}},
        verdicts=[],
        gate_entries={},
        gate_decisions={},
        registry=[],
        decided_at_pl5=0,
        attention_spent={},
    )
    assert metrics.evidence_quality_ratio == {"market": None}
    assert metrics.hypothesis_resolution_rate is None
    # The two that were already right, unchanged.
    assert metrics.kill_rate is None
    assert metrics.attention_cost_per_resolved_hypothesis is None


def test_a_populated_ledger_still_scores_what_it_earned():
    """The other half again: a real weak ledger must still read as weak, and
    an unresolved hypothesis must still read as unresolved."""
    ratios = evidence_quality_ratio(
        {"market": {"claims": [
            {"id": "C-1", "source_type": "model_inference"},
            {"id": "C-2", "source_type": "model_inference"},
        ]}}
    )
    assert ratios["market"] == 0.0
    rate = hypothesis_resolution_rate(
        [HypothesisVerdict(id="H-1", verdict="open")]
    )
    assert rate == 0.0
