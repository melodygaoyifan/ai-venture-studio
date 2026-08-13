import shutil
from pathlib import Path

import pytest

from ai_venture_studio import testing as testing_mod
from ai_venture_studio.product_bench import load_cases, run_case

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

CASES = Path(__file__).parent.parent / "benchmarks" / "products"


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import ai_venture_studio.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def test_cases_load():
    cases = load_cases(CASES)
    assert len(cases) == 5
    assert all(c.probes for c in cases)


def test_feature_add_case_accretes_modules(tmp_path):
    case = next(c for c in load_cases(CASES) if c.name == "04-feature-add")
    result = run_case(case, provider="mock", keep_dir=tmp_path / "keep")
    assert result.autopilot_status == "completed"
    assert result.tasks_total == 6  # 3 base + 3 feature tasks
    assert result.probe_pass_rate == 1.0, [p.model_dump() for p in result.probes]


def test_miniprogram_profile_case(tmp_path):
    case = next(c for c in load_cases(CASES) if c.name == "05-miniprogram-profile")
    result = run_case(case, provider="mock", keep_dir=tmp_path / "keep")
    assert result.autopilot_status == "completed"
    assert result.probe_pass_rate == 1.0


def test_probes_pass_against_mock_built_product(tmp_path):
    case = load_cases(CASES)[0]  # 01-item-store
    result = run_case(case, provider="mock", keep_dir=tmp_path / "keep")
    assert result.autopilot_status == "completed"
    assert result.build_rate == 1.0
    # Independent probes exercise the BUILT modules, not the builder's tests.
    assert result.probe_pass_rate == 1.0, [p.model_dump() for p in result.probes]
    assert result.clean_review_rate == 1.0


def test_bench_is_honest_about_failing_probes(tmp_path):
    case = next(c for c in load_cases(CASES) if c.name == "03-honesty-check")
    result = run_case(case, provider="mock", keep_dir=tmp_path / "keep")
    assert result.build_rate == 1.0  # everything built…
    assert result.probe_pass_rate < 1.0  # …but the impossible probe fails, visibly
    failing = [p for p in result.probes if not p.passed]
    assert failing and "unreasonable-demand" in failing[0].name


def test_crashed_case_still_records_duration(monkeypatch, tmp_path):
    import ai_venture_studio.product_bench as pb

    def _boom(case, provider=None):
        import time

        time.sleep(0.05)
        raise KeyError("new_content")

    monkeypatch.setattr(pb, "run_case", _boom)
    # repo_dir=tmp_path: the lock must not touch the real repo's pidfile —
    # a live bench in this checkout would otherwise fail a hermetic test.
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    (case,) = summary.cases
    assert case.autopilot_status.startswith("error: KeyError")
    # A crashed case spent real wall-clock; 0.0 would read as "died instantly".
    assert case.duration_s > 0.0


# ---------------------------------------------------------------------------
# A case that never ran is UNMEASURED, not a zero. Run 12 (2026-08-13) had
# case 04 die on a hung test suite; it scored 0/0, entered every average as
# 0.0, and dragged the probe rate from 87% to 65% — a number the launch
# PRD's kill criterion reads. An infrastructure crash must not be able to
# fire a capability verdict.
# ---------------------------------------------------------------------------


def _crashing_bench(monkeypatch, tmp_path, results):
    """Run the bench over N cases where `results` decides each outcome."""
    import ai_venture_studio.product_bench as pb

    calls = iter(results)

    def _next(case, provider=None):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(pb, "run_case", _next)
    return pb


def _case(name, *, total, built, clean, probes_passed, probes_total):
    from ai_venture_studio.product_bench import CaseResult, ProbeResult

    return CaseResult(
        name=name,
        autopilot_status="completed",
        tasks_total=total,
        tasks_built=built,
        clean_reviews=clean,
        probes=[
            ProbeResult(name=f"p{i}", passed=i < probes_passed)
            for i in range(probes_total)
        ],
    )


def test_a_case_that_never_ran_is_dropped_from_the_rates_not_scored_zero(
    monkeypatch, tmp_path
):
    pb = _crashing_bench(
        monkeypatch,
        tmp_path,
        [
            _case("ok", total=4, built=4, clean=2, probes_passed=3, probes_total=3),
            RuntimeError("pytest timed out"),
        ],
    )
    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)
    # Averaged over the one case that produced data, not over two.
    assert summary.build_rate == 1.0
    assert summary.probe_pass_rate == 1.0
    assert summary.clean_review_rate == 0.5
    assert summary.cases_measured == 1


def test_the_unmeasured_case_is_named_so_the_scope_is_not_invisible(
    monkeypatch, tmp_path
):
    pb = _crashing_bench(
        monkeypatch,
        tmp_path,
        [
            _case("ok", total=2, built=2, clean=2, probes_passed=1, probes_total=1),
            RuntimeError("boom"),
        ],
    )
    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)
    # Two 100% readings, one over two cases and one over one, are different
    # measurements; the percentages alone cannot tell them apart.
    assert len(summary.unmeasured) == 1
    assert summary.cases_measured == 1 and len(summary.cases) == 2


def test_a_real_zero_is_still_a_zero(monkeypatch, tmp_path):
    """The exclusion is for cases with NO denominator. A case that ran and
    built nothing failed, and must keep dragging the build rate down."""
    pb = _crashing_bench(
        monkeypatch,
        tmp_path,
        [
            _case("ok", total=2, built=2, clean=2, probes_passed=2, probes_total=2),
            _case("bad", total=4, built=0, clean=0, probes_passed=0, probes_total=2),
        ],
    )
    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)
    assert summary.build_rate == 0.5          # (1.0 + 0.0) / 2
    assert summary.probe_pass_rate == 0.5     # (1.0 + 0.0) / 2
    assert summary.unmeasured == []
    # It built nothing, so it has no reviews to be clean — that axis has no
    # denominator and the failure is already fully visible in build_rate.
    assert summary.clean_review_rate == 1.0


def test_the_saved_series_carries_its_own_denominator(monkeypatch, tmp_path):
    pb = _crashing_bench(
        monkeypatch,
        tmp_path,
        [
            _case("ok", total=2, built=2, clean=1, probes_passed=1, probes_total=2),
            RuntimeError("boom"),
        ],
    )
    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)
    path = pb.save_summary(summary, tmp_path)
    import yaml as _yaml

    rates = _yaml.safe_load(path.read_text())["rates"]
    # A later reader of the series has only this file.
    assert rates["cases_measured"] == 1
    assert rates["cases_total"] == 2
    assert len(rates["unmeasured"]) == 1


# ---------------------------------------------------------------------------
# ...and it must reach a person. Run 12 exited 0 with a quarter of the
# benchmark never run, so `avs cadence --notify` printed "nothing needs a
# person" — the silent-success shape one level inside the loop built to
# catch silent success.
# ---------------------------------------------------------------------------


def test_a_bench_that_could_not_measure_a_case_exits_nonzero(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import ai_venture_studio.product_bench as pb
    from ai_venture_studio.cli import app

    calls = iter([
        _case("ok", total=2, built=2, clean=2, probes_passed=2, probes_total=2),
        RuntimeError("pytest timed out"),
    ])

    def _next(case, provider=None):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(pb, "run_case", _next)
    result = CliRunner().invoke(
        app, ["product-bench", "--cases-dir", CASES, "--limit", "2",
              "--repo-dir", str(tmp_path)]
    )
    # Non-zero is what carries it to Discord: since v0.81.0 any non-zero
    # exit from a scheduled loop is a failure, full stop.
    assert result.exit_code == 3, result.output
    assert "never ran" in result.output
    # The rates are still printed and still saved — the run measured what
    # it measured, and the series must not lose a real reading.
    assert "over 1 of 2 cases" in result.output
    assert list(tmp_path.glob(".mas/product-bench/result-*.yaml"))


def test_a_bench_that_measured_everything_stays_quiet(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import ai_venture_studio.product_bench as pb
    from ai_venture_studio.cli import app

    monkeypatch.setattr(
        pb, "run_case",
        lambda case, provider=None: _case(
            "ok", total=2, built=1, clean=0, probes_passed=0, probes_total=2
        ),
    )
    result = CliRunner().invoke(
        app, ["product-bench", "--cases-dir", CASES, "--limit", "1",
              "--repo-dir", str(tmp_path)]
    )
    # A BAD result is not a broken run. Poor rates are the measurement
    # working; only an unmeasured case needs a person.
    assert result.exit_code == 0, result.output
    assert "never ran" not in result.output
    assert "over 1 of" not in result.output
