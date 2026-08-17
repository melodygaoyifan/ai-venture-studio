import shutil
import subprocess
import sys
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
    """Every case file on disk parses into a case with probes.

    The count is read from the directory rather than typed here: a
    hand-maintained number only ever records that someone remembered to
    edit it, while this fails if a case file stops loading.
    """
    cases = load_cases(CASES)
    assert len(cases) == len(list(CASES.glob("*.yaml")))
    assert len(cases) >= 5
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


def test_a_rejected_task_keeps_its_whole_reason_in_the_row():
    """The row is the durable record — the workspace it points at is
    gitignored. A rejection clipped to 200 chars alongside the clean rows is
    how 11 of run 14's 17 outcomes reached the series unexplained."""
    from ai_venture_studio.product_bench import _row_detail

    reason = "[review: 3 medium — " + ("unbounded query; " * 30) + "]"
    assert _row_detail("built", "REQUEST_CHANGES", reason) == reason
    assert len(_row_detail("built", "APPROVE", reason)) == 200
    # a failure keeps everything, as it always did
    assert _row_detail("spec_blocked", None, reason) == reason


def test_the_saved_series_names_the_build_that_produced_it(monkeypatch, tmp_path):
    """Attributing run 14 to a version meant diffing git commit timestamps
    against the result filename, and that only worked because the release
    happened to land 9 minutes before the run started. A row that cannot name
    its own build cannot be compared to the row above it."""
    from ai_venture_studio import __version__

    pb = _crashing_bench(
        monkeypatch, tmp_path,
        [_case("ok", total=1, built=1, clean=1, probes_passed=1, probes_total=1)],
    )
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    path = pb.save_summary(summary, tmp_path)
    import yaml as _yaml

    assert _yaml.safe_load(path.read_text())["avs_version"] == __version__


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


# ---------------------------------------------------------------------------
# The case that CRASHED is the one whose workspace you need. Run 12's case 04
# hung on `pytest`, and preservation lived after the autopilot call — so the
# exception jumped straight over it into the `finally` that deletes the temp
# dir. The row said `preserved_workspace: ''`, the product that hung existed
# nowhere afterwards, and the hang could never be explained.
# ---------------------------------------------------------------------------


def _case_that_crashes(monkeypatch, exc):
    """Point run_case at an autopilot that dies after the workspace exists."""
    import ai_venture_studio.product_bench as pb

    def _boom(workspace, fdr, provider=None, yes=False):
        (workspace / "the-evidence.txt").write_text("what the hung case built")
        raise exc

    monkeypatch.setattr(pb, "run_autopilot", _boom)
    return pb


def test_a_crashed_case_keeps_the_workspace_it_crashed_in(monkeypatch, tmp_path):
    pb = _case_that_crashes(monkeypatch, RuntimeError("pytest timed out"))
    case = load_cases(CASES)[0]

    with pytest.raises(RuntimeError) as caught:
        run_case(case, provider="mock", keep_dir=tmp_path / "keep")

    kept = Path(getattr(caught.value, "avs_preserved_workspace", ""))
    assert str(kept), "the crashed case's workspace was thrown away — this is run 12"
    assert (kept / "the-evidence.txt").read_text() == "what the hung case built"


def test_the_crash_row_points_at_the_preserved_workspace(monkeypatch, tmp_path):
    pb = _case_that_crashes(monkeypatch, RuntimeError("pytest timed out"))
    monkeypatch.chdir(tmp_path)

    summary = pb.run_product_bench(CASES, provider="mock", limit=1, repo_dir=tmp_path)

    (row,) = summary.cases
    assert row.autopilot_status.startswith("error: RuntimeError")
    # A row naming a failure whose evidence is deleted is not a record.
    kept = Path(row.preserved_workspace)
    assert row.preserved_workspace and kept.is_dir()
    assert (kept / "the-evidence.txt").exists()


def test_preserving_the_workspace_never_replaces_the_real_failure(monkeypatch, tmp_path):
    pb = _case_that_crashes(monkeypatch, RuntimeError("pytest timed out"))

    def _cannot_copy(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(pb, "_preserve_workspace", _cannot_copy)
    case = load_cases(CASES)[0]

    # Forensics is bookkeeping. If it fails, the report must still name the
    # hang — not an OSError from the code that was trying to explain it.
    with pytest.raises(RuntimeError, match="pytest timed out"):
        run_case(case, provider="mock", keep_dir=tmp_path / "keep")


# ---------------------------------------------------------------------------
# A probe boots the product's server. `subprocess.run`'s timeout kills the
# probe alone, so a wedged probe used to leave that server alive holding its
# port — and the next probe then measured someone else's product (the run 13
# harness failure, on the constant port 8646).
# ---------------------------------------------------------------------------


def test_a_wedged_probe_does_not_leave_the_product_server_running(tmp_path, monkeypatch):
    import ai_venture_studio.product_bench as pb
    from ai_venture_studio.product_bench import Probe, run_probe

    monkeypatch.setattr(pb, "_PROBE_TIMEOUT_S", 5)
    monkeypatch.setattr(pb, "workspace_python", lambda ws: sys.executable)
    marker = tmp_path / "server-started"
    result = run_probe(
        tmp_path,
        Probe(
            name="boots-a-server",
            script=(
                "import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', "
                "\"import time; time.sleep(120)\"])\n"
                f"open({str(marker)!r}, 'w').write('up')\n"
                "print('serving', flush=True)\n"
                "time.sleep(120)\n"
            ),
        ),
    )

    assert not result.passed
    assert marker.exists(), "the probe never got as far as starting anything"
    # The last thing it said, not a bare 'probe timed out'.
    assert "serving" in result.detail
    survivors = subprocess.run(
        ["pgrep", "-f", "time.sleep(120)"], capture_output=True, text=True
    )
    assert "time.sleep(120)" not in survivors.stdout, (
        "the probe's server outlived the probe and still holds its port"
    )


# ---------------------------------------------------------------------------
# The increment axis (ADR-049). ADR-046 added the only refusal path this
# product has, and nothing measured it: a bench that only ever asks "can it
# build" scores a gate that never fires exactly the same as one that fires
# correctly. ADR-048 then found the gate INERT in Chinese — passing every
# English test, refusing nothing, erroring never. These tests exist because
# that failure mode is invisible to every rate above this line.
# ---------------------------------------------------------------------------


REAL_CASES = Path(__file__).parent.parent / "benchmarks" / "products-real"


def _increment_case(name, *, expected, actual, axis="increment"):
    from ai_venture_studio.product_bench import CaseResult, IncrementResult

    return CaseResult(
        name=name,
        autopilot_status="completed",
        axis=axis,
        increments=[
            IncrementResult(
                index=i, fdr=f"f{i}", expected=e, actual=a, correct=e == a
            )
            for i, (e, a) in enumerate(zip(expected, actual))
        ],
        tasks_total=2,
        tasks_built=2,
        clean_reviews=2,
    )


def test_an_increment_case_stays_out_of_the_three_headline_rates(
    monkeypatch, tmp_path
):
    """Otherwise the bench punishes the system for being right.

    An increment case whose CORRECT answer is `already_satisfied` builds
    nothing on purpose. Averaged into the build rate that is a 0.0 — the
    same score a case gets for failing outright — and the run-13..17 series
    would step down for a reason that has nothing to do with capability.
    """
    pb = _crashing_bench(
        monkeypatch,
        tmp_path,
        [
            _case("ok", total=4, built=4, clean=4, probes_passed=2, probes_total=2),
            _case_with_axis(
                _case("inc", total=2, built=0, clean=0,
                      probes_passed=0, probes_total=1),
            ),
        ],
    )
    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)
    assert summary.build_rate == 1.0
    assert summary.probe_pass_rate == 1.0
    # ...and the denominator the reader sees is the build series' own, so
    # "of 4" keeps meaning what it meant in every row above.
    assert summary.cases_total == 1
    assert summary.cases_measured == 1


def _case_with_axis(result, axis="increment"):
    return result.model_copy(update={"axis": axis})


def test_the_gate_rate_is_scored_over_the_expectations_the_case_declared(
    monkeypatch, tmp_path
):
    pb = _crashing_bench(
        monkeypatch,
        tmp_path,
        [
            _increment_case(
                "inc",
                expected=["already_satisfied", "raises_scr", "completed"],
                actual=["already_satisfied", "completed", "completed"],
            ),
        ],
    )
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    assert summary.gate_rate == pytest.approx(2 / 3)
    assert summary.gate_cases_measured == 1 and summary.gate_cases_total == 1
    # The headline three have no build-axis case to average, and must not
    # report the increment case's numbers under their own names.
    assert summary.cases_total == 0


def test_a_run_that_never_asked_the_gate_reports_no_gate_rate(
    monkeypatch, tmp_path
):
    """None, not 0.0. Zero says the gate answered wrongly every time."""
    pb = _crashing_bench(
        monkeypatch,
        tmp_path,
        [_case("ok", total=1, built=1, clean=1, probes_passed=1, probes_total=1)],
    )
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    assert summary.gate_rate is None
    assert summary.gate_cases_total == 0


def test_the_saved_series_carries_the_gate_rate_and_its_own_denominator(
    monkeypatch, tmp_path
):
    """A later reader has only this file — and the increment rate is over
    different cases than the three above it."""
    import yaml as _yaml

    pb = _crashing_bench(
        monkeypatch,
        tmp_path,
        [
            _case("ok", total=2, built=2, clean=2, probes_passed=1, probes_total=1),
            _increment_case(
                "inc",
                expected=["already_satisfied", "raises_scr"],
                actual=["already_satisfied", "raises_scr"],
            ),
        ],
    )
    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)
    rates = _yaml.safe_load(pb.save_summary(summary, tmp_path).read_text())["rates"]
    assert rates["cases_total"] == 1, "the headline denominator counted an increment case"
    assert rates["increment"]["gate_rate"] == 1.0
    assert rates["increment"]["cases_total"] == 1
    assert rates["increment"]["cases_measured"] == 1


def test_a_raised_scr_outranks_the_status_in_both_directions():
    """A contradiction under `--yes` PROCEEDS to build (ADR-046: `--yes`
    may approve work, never a change to what the product promises), so its
    status reads `completed` — indistinguishable from a gate that never
    fired. The SCR on disk is the only thing that tells them apart, which
    is exactly the reading ADR-048's inert gate would have passed."""
    from ai_venture_studio.product_bench import _score_increment

    fired = _score_increment(
        index=0, fdr="删除报修", expected="raises_scr",
        status="completed", new_scrs={"SCR-002.yaml"},
    )
    assert fired.actual == "raises_scr" and fired.correct
    assert "SCR-002.yaml" in fired.detail

    inert = _score_increment(
        index=0, fdr="删除报修", expected="raises_scr",
        status="completed", new_scrs=set(),
    )
    assert inert.actual == "completed" and not inert.correct

    # ...and the other direction: a clean addition that trips the gate is a
    # wrong answer, not a passing `completed` row.
    misfire = _score_increment(
        index=1, fdr="评分", expected="completed",
        status="completed", new_scrs={"SCR-003.yaml"},
    )
    assert misfire.actual == "raises_scr" and not misfire.correct


def test_only_unapproved_scrs_count_as_the_gate_having_fired(tmp_path):
    """ADR-046 only ever writes them `proposed`. One a human later approved
    is a decision that was made, not a clash the gate just raised."""
    from ai_venture_studio.product_bench import _proposed_scrs

    scr = tmp_path / ".mas" / "scr"
    scr.mkdir(parents=True)
    (scr / "SCR-001.yaml").write_text("status: approved\n", encoding="utf-8")
    (scr / "SCR-002.yaml").write_text("status: proposed\n", encoding="utf-8")
    (scr / "SCR-003.yaml").write_text("{{ not yaml", encoding="utf-8")
    assert _proposed_scrs(tmp_path) == {"SCR-002.yaml"}
    assert _proposed_scrs(tmp_path / "nowhere") == set()


def test_a_case_whose_expectations_do_not_line_up_is_refused_at_load():
    """They are paired by POSITION. A mismatch means the file says something
    other than what it means, and the run costs hours — refused at load
    beats scored and read wrongly."""
    from ai_venture_studio.product_bench import ProductCase

    with pytest.raises(ValueError, match="paired by position"):
        ProductCase(
            name="x", fdr="f", probes=[],
            auto_probes=True,
            feature_fdrs=["a", "b"],
            feature_expectations=["completed"],
        )
    with pytest.raises(ValueError, match="unknown expectation"):
        ProductCase(
            name="x", fdr="f", auto_probes=True,
            feature_fdrs=["a"], feature_expectations=["probably_fine"],
        )
    with pytest.raises(ValueError, match="measures nothing"):
        ProductCase(name="x", fdr="f", auto_probes=True, axis="increment")


def test_the_real_increment_case_asks_the_gate_all_three_of_its_questions():
    """A gate that only ever says no scores 100% on a case that only ever
    asks it to say no. The case has to contain a duplicate, a contradiction
    AND a clean addition, or it measures half a gate.

    It is also asserted to be Chinese: ADR-048 found the gate inert in
    exactly that language while every English test passed, and a case
    quietly rewritten into English would restore the blind spot.
    """
    from ai_venture_studio.product_bench import EXPECTATIONS

    cases = [c for c in load_cases(REAL_CASES) if c.axis == "increment"]
    assert cases, "the real series has no increment case (ADR-049)"
    for case in cases:
        assert set(case.feature_expectations) == set(EXPECTATIONS)
        assert len(case.feature_expectations) == len(case.feature_fdrs)
        text = case.fdr + "".join(case.feature_fdrs)
        assert any("一" <= ch <= "鿿" for ch in text), (
            f"{case.name} is not written in the language the gate was inert in"
        )


def test_the_real_build_series_is_still_the_same_four_cases():
    """The comparability guard. Run 13..17 are read as one series; adding a
    case to the build axis silently redefines every rate in it, and the
    percentages give the reader no way to notice."""
    build = [c.name for c in load_cases(REAL_CASES) if c.axis == "build"]
    assert len(build) == 4, f"the headline series changed shape: {build}"
