"""Whether a case was measured is ONE decision, and every rate reads it.

ADR-043, written from bench run 16.

`02-shortener-api` planned for six minutes, came back with no tasks, and
the run reported it two ways at once: named in `unmeasured` and declared
"excluded from the rates above, not scored as zero" — while its two probes,
run against a product that was never built, were averaged into the probe
rate as a real `0.0`. The exclusion was decided per RATE (`unmeasured` came
from `build_rate is None`, and each rate averaged independently), so one
summary could exclude a case from two rates and count it in the third.

Both halves were wrong, in opposite directions:

  - the probe half, because a case that is excluded is excluded everywhere;
  - the build half, because that case should never have been excluded at
    all. ADR-035 already said so in words — "a case that ran and built
    nothing still scores a real 0.0" — and the code read `tasks_total == 0`
    as "no denominator" instead of as the most complete build failure
    available. Build 100% was the rate over cases that got a plan, not over
    cases that were asked for a product.

So these tests pin the invariant rather than the instance: a case is
measured or it is not, the answer is the same for every rate, and only the
harness dying makes it "not".
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

CASES = Path(__file__).parent.parent / "benchmarks" / "products"


def _bench(monkeypatch, results):
    import ai_venture_studio.product_bench as pb

    calls = iter(results)

    def _next(case, provider=None, **_):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(pb, "run_case", _next)
    return pb


def _case(name, *, status="completed", total, built, clean, passed, probes):
    from ai_venture_studio.product_bench import CaseResult, ProbeResult

    return CaseResult(
        name=name,
        autopilot_status=status,
        tasks_total=total,
        tasks_built=built,
        clean_reviews=clean,
        probes=[
            ProbeResult(name=f"p{i}", passed=i < passed) for i in range(probes)
        ],
    )


def test_a_case_that_ran_and_produced_no_tasks_is_measured(monkeypatch, tmp_path):
    """Run 16's case 02, exactly: it ran, it decomposed to nothing, and its
    probes failed because there was no product to probe."""
    pb = _bench(monkeypatch, [
        _case("ok", total=4, built=4, clean=4, passed=3, probes=3),
        _case("planned-nothing", status="failed",
              total=0, built=0, clean=0, passed=0, probes=2),
    ])
    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)

    assert summary.unmeasured == [], (
        "a case that ran and produced nothing is a build failure, not an "
        "absence of measurement"
    )
    assert summary.cases_measured == 2
    assert summary.build_rate == 0.5, "(1.0 + 0.0) / 2 — it was asked for a product"
    # AMENDED BY ADR-061. This asserted 0.5 — the failing case's two probes
    # averaged in as a second 0.0 — and that was one failure charged to two
    # of the three headline rates. There was no product to probe. The case is
    # out of the probe average and NAMED, which is the half of this record
    # that survives: an exclusion nobody can see is the defect it was written
    # about.
    assert summary.probe_pass_rate == 1.0
    assert summary.no_probe_reading == ["planned-nothing"]


def test_the_build_rate_does_not_round_up_by_forgetting_a_case(
    monkeypatch, tmp_path
):
    """The headline this was found by. Three products out of four is 75%,
    and the run reported 100% because the fourth was dropped."""
    pb = _bench(monkeypatch, [
        _case("a", total=4, built=4, clean=2, passed=3, probes=3),
        _case("b", status="failed", total=0, built=0, clean=0, passed=0, probes=2),
        _case("c", total=5, built=5, clean=0, passed=5, probes=5),
        _case("d", total=7, built=7, clean=3, passed=3, probes=3),
    ])
    summary = pb.run_product_bench(CASES, limit=4, repo_dir=tmp_path)
    assert summary.build_rate == 0.75
    assert summary.cases_measured == 4


def test_a_crashed_case_is_still_dropped_from_every_rate(monkeypatch, tmp_path):
    """The other direction must not move: an infrastructure death says
    nothing about whether the machine can build software, and entering it
    as 0.0 lets a provider outage fire a capability verdict (ADR-035)."""
    pb = _bench(monkeypatch, [
        _case("ok", total=2, built=2, clean=2, passed=2, probes=2),
        RuntimeError("OverloadedError: 529"),
    ])
    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)
    assert summary.unmeasured == ["02-shortener-api"] or len(summary.unmeasured) == 1
    assert summary.build_rate == 1.0
    assert summary.cases_measured == 1


def test_an_excluded_case_is_excluded_from_every_rate(monkeypatch, tmp_path):
    """THE INVARIANT, not the instance.

    Whatever decides exclusion, the answer must be the same for all three
    rates — that is the only thing that makes the run's own sentence
    ("excluded from the rates above") true rather than true-of-two-thirds.
    """
    from ai_venture_studio.product_bench import CaseResult, ProbeResult

    shapes = [
        CaseResult(name="crashed-with-probes-recorded",
                   autopilot_status="error: RuntimeError: boom",
                   probes=[ProbeResult(name="p", passed=False)]),
        CaseResult(name="crashed-bare", autopilot_status="error: TimeoutExpired"),
        CaseResult(name="crashed-mid-build",
                   autopilot_status="error: OverloadedError",
                   tasks_total=3, tasks_built=1, clean_reviews=1,
                   probes=[ProbeResult(name="p", passed=True)]),
    ]
    for case in shapes:
        assert not case.measured, case.name
        assert case.build_rate is None, case.name
        assert case.probe_pass_rate is None, (
            f"{case.name}: excluded from build and counted in probes — this "
            "is the run-16 defect"
        )
        assert case.clean_review_rate is None, case.name


def test_the_named_exclusions_are_exactly_the_unmeasured_cases(
    monkeypatch, tmp_path
):
    """`unmeasured` must be read from the case's own decision, never
    re-derived from one rate — re-deriving it is how the list came to
    describe averages it did not match."""
    pb = _bench(monkeypatch, [
        _case("built", total=2, built=2, clean=1, passed=2, probes=2),
        _case("nothing", status="failed", total=0, built=0, clean=0,
              passed=0, probes=1),
        RuntimeError("boom"),
    ])
    summary = pb.run_product_bench(CASES, limit=3, repo_dir=tmp_path)
    named = set(summary.unmeasured)
    for case in summary.cases:
        assert (case.name in named) is (not case.measured), case.name


def test_a_case_that_produced_nothing_says_why(monkeypatch, tmp_path):
    """The rate says how badly, and it is now a 0.0 that no longer appears
    in the excluded list — so the reason has to travel with it or the run
    reports a total failure it cannot explain (ADR-041/042's family)."""
    from ai_venture_studio.product_bench import CaseResult

    case = CaseResult(
        name="planned-nothing",
        autopilot_status="failed",
        failure_reason="planning blocked: unparseable planner output "
                       "(ScannerError: line 3, column 9)",
    )
    assert case.measured and case.build_rate == 0.0
    assert "ScannerError" in case.failure_reason


def test_run_16_reads_as_the_adr_says_it_does():
    """The instance, read from the artifact rather than retyped.

    ADR-043, HISTORY.md, the CHANGELOG and PC-17 all assert the same
    corrected reading of run 16 — no case excluded, build 75%, clean 31%.
    That claim is only as good as the rule that produces it, so it is
    computed here from the run's own recorded per-case rows.

    AMENDED BY ADR-061: the probe figure is 100% over 3 of 4, not 75%.
    02-shortener-api built nothing, so its two failing probes were the build
    failure printed a second time. Its exclusion is now named rather than
    silent, which is what ADR-043's correction was actually about.
    """
    import yaml

    from ai_venture_studio.product_bench import CaseResult

    path = (Path(__file__).parent.parent / "benchmarks" / "results"
            / "result-2026-08-16-0612.yaml")
    cases = [
        CaseResult.model_validate(c)
        for c in yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]
    ]

    def _avg(values):
        measured = [v for v in values if v is not None]
        return sum(measured) / len(measured) if measured else 0.0

    assert [c.name for c in cases if not c.measured] == [], (
        "the run recorded 02-shortener-api as unmeasured; under ADR-043 it "
        "ran and produced nothing, which is a 0.0"
    )
    assert round(_avg([c.build_rate for c in cases]), 2) == 0.75
    probed = [c for c in cases if c.probe_pass_rate is not None]
    assert [c.name for c in cases if c.probe_pass_rate is None] == [
        "02-shortener-api"
    ]
    assert round(_avg([c.probe_pass_rate for c in probed]), 2) == 1.0
    assert round(_avg([c.clean_review_rate for c in cases]), 2) == 0.31


def test_the_saved_file_carries_the_reason_a_case_produced_nothing(
    monkeypatch, tmp_path
):
    """The result file is the durable record — the workspace it points at
    is gitignored and has been lost before."""
    import yaml

    pb = _bench(monkeypatch, [
        _case("ok", total=2, built=2, clean=2, passed=2, probes=2),
    ])
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    summary.cases[0].failure_reason = "planning blocked: dag_check said so"
    path = pb.save_summary(summary, tmp_path)
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["cases"][0]["failure_reason"] == (
        "planning blocked: dag_check said so"
    )
