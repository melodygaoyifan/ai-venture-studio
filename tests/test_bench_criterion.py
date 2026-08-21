"""v0.51.0 — the second kill-criterion axis (PRD O-L2), added by a recorded
human choice on 2026-07-27.

Its reason for existing is that its series ALREADY EXISTS, so it can fire on
the next run without asking anyone anything — which is why it is the only axis
left after v0.81.0 withdrew the other (ADR-033). The assertions below are mostly
about not over-firing: one bad run is noise at n=4 cases, and a criterion
that cries wolf gets ignored, which is worse than not having it.
"""

from __future__ import annotations

import pathlib

import yaml

from ai_venture_studio.bench_criterion import (
    BUILD_FLOOR,
    CONSECUTIVE_RUNS_TO_FIRE,
    PROBE_FLOOR,
    _scan,
    evaluate,
    load_runs,
)

REPO = pathlib.Path(__file__).resolve().parents[1]


def _runs(tmp_path, rows: list[tuple[float, float]]) -> None:
    """Write result YAMLs in timestamp order, oldest first."""
    out = tmp_path / "benchmarks" / "results"
    out.mkdir(parents=True, exist_ok=True)
    for i, (build, probes) in enumerate(rows):
        (out / f"result-2026-07-{10 + i:02d}-0000.yaml").write_text(
            yaml.safe_dump({"build_rate": build, "probe_pass_rate": probes,
                            "clean_review_rate": 0.4, "cases": []}),
            encoding="utf-8",
        )


# --- not firing is the common case -------------------------------------------


def test_no_runs_cannot_fire_and_says_why(tmp_path):
    state = evaluate(tmp_path)
    assert state.fires is False and state.streak == 0
    assert "cannot fire on data that does not exist" in state.detail
    assert "cannot be declared safe on it either" in state.detail


def test_healthy_runs_do_not_fire(tmp_path):
    _runs(tmp_path, [(0.75, 0.75), (0.74, 0.75)])
    state = evaluate(tmp_path)
    assert state.fires is False and state.streak == 0
    assert "0/2 consecutive" in state.detail
    assert "build 75%" in state.detail  # names the actual readings


def test_one_bad_run_is_noise_not_a_kill(tmp_path):
    """n=4 cases: a single dip must not fire. That is the whole reason the
    criterion says '2 consecutive'."""
    _runs(tmp_path, [(0.75, 0.75), (0.40, 0.75)])
    state = evaluate(tmp_path)
    assert state.streak == 1 and state.fires is False
    assert "1 more would fire it" in state.detail


def test_a_recovery_resets_the_streak(tmp_path):
    _runs(tmp_path, [(0.40, 0.75), (0.75, 0.75)])
    assert evaluate(tmp_path).streak == 0


# --- firing ------------------------------------------------------------------


def test_two_consecutive_low_build_runs_fire(tmp_path):
    _runs(tmp_path, [(0.75, 0.75), (0.40, 0.75), (0.35, 0.75)])
    state = evaluate(tmp_path)
    assert state.streak == 2 and state.fires is True
    assert "HAS FIRED" in state.detail
    assert "invariant 14.20" in state.detail  # the human decision it demands


def test_the_probe_floor_fires_independently_of_build(tmp_path):
    """Either floor is enough: a product that builds but does not work is
    not a working product."""
    _runs(tmp_path, [(0.90, 0.40), (0.90, 0.30)])
    state = evaluate(tmp_path)
    assert state.fires is True
    assert "probes" in state.detail


def test_exactly_at_the_floor_is_not_below_it(tmp_path):
    _runs(tmp_path, [(BUILD_FLOOR, PROBE_FLOOR), (BUILD_FLOOR, PROBE_FLOOR)])
    assert evaluate(tmp_path).fires is False


# --- ledger robustness -------------------------------------------------------


def test_unreadable_and_rateless_files_are_skipped_not_fatal(tmp_path):
    _runs(tmp_path, [(0.75, 0.75)])
    out = tmp_path / "benchmarks" / "results"
    (out / "broken.yaml").write_text("not: [valid", encoding="utf-8")
    (out / "notes.yaml").write_text(yaml.safe_dump({"note": "no rates here"}),
                                    encoding="utf-8")
    runs = load_runs(tmp_path)
    assert len(runs) == 1  # the one real reading survives
    assert evaluate(tmp_path).fires is False


# --- the denominator reaches the person deciding (ADR-035) -------------------


def _run_with_rates(tmp_path, name: str, rates: dict) -> None:
    out = tmp_path / "benchmarks" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(
        yaml.safe_dump({"build_rate": 0.75, "probe_pass_rate": 0.65,
                        "clean_review_rate": 0.4, "cases": [], "rates": rates}),
        encoding="utf-8",
    )


def test_a_partial_run_says_so_in_the_line_gate_pl5_reads(tmp_path):
    """The Gate PL5 decision is made on these two numbers. If one of them was
    averaged over three cases instead of four, the sentence has to say so —
    nobody should have to open the YAML to find out what they are killing on."""
    _run_with_rates(tmp_path, "result-2026-08-13-0000.yaml",
                    {"cases_measured": 3, "cases_total": 4, "unmeasured": ["04"]})
    run = load_runs(tmp_path)[0]
    assert run.partial is True
    assert "over 3 of 4 cases" in run.summary()
    assert "over 3 of 4 cases" in evaluate(tmp_path).detail


def test_a_complete_run_carries_no_scope_note(tmp_path):
    _run_with_rates(tmp_path, "result-2026-08-13-0000.yaml",
                    {"cases_measured": 4, "cases_total": 4, "unmeasured": []})
    run = load_runs(tmp_path)[0]
    assert run.partial is False
    assert "cases)" not in run.summary()


def test_runs_predating_the_denominator_are_read_as_complete(tmp_path):
    """Runs 1-12 carry no rates block. They were complete runs (except the
    already-noted crashes), and reading them as partial would put a caveat on
    the whole historical series that is not true of it."""
    _runs(tmp_path, [(0.75, 0.75)])
    run = load_runs(tmp_path)[0]
    assert run.cases_measured is None and run.partial is False
    assert "cases)" not in run.summary()


# --- the real ledger and the PRD ---------------------------------------------


def test_this_repos_own_scoreboard_is_read_and_is_healthy():
    """The live series: runs 10-11 sit at 74-75% build, 75% probes."""
    state = evaluate(REPO)
    assert state.runs_considered, "the tracked scoreboard should be readable"
    assert state.fires is False
    assert all(r.build_rate >= BUILD_FLOOR for r in state.runs_considered)


def test_the_floors_match_what_the_prd_states():
    """The thresholds are the PRD's, not this module's invention."""
    prd = yaml.safe_load((REPO / "launch" / "prd.yaml").read_text(encoding="utf-8"))
    criteria = " ".join((prd.get("prd") or {}).get("kill_criteria") or [])
    assert "60%" in criteria and "50%" in criteria
    assert "2 consecutive" in criteria
    assert int(BUILD_FLOOR * 100) == 60 and int(PROBE_FLOOR * 100) == 50
    assert CONSECUTIVE_RUNS_TO_FIRE == 2


def test_the_prd_carries_one_axis_and_it_names_its_series():
    prd = yaml.safe_load((REPO / "launch" / "prd.yaml").read_text(encoding="utf-8"))["prd"]
    # One, not two: O-L1 went out with the loop that fed it (v0.81.0, ADR-033).
    assert len(prd["kill_criteria"]) == 1
    assert {o["id"] for o in prd["outcomes"]} == {"O-L2"}
    capability = next(o for o in prd["outcomes"] if o["id"] == "O-L2")
    # Its instrumentation EXISTS — that is why this axis can fire now.
    assert capability["instrumentation"]["exists"] is True
    assert "HISTORY.md" in capability["baseline"]["source"]
    assert pathlib.Path(REPO / capability["definition_ref"]).exists()


def test_the_loop_reports_the_capability_axis():
    from ai_venture_studio.product.cycle import read_cycle

    state = read_cycle(REPO / "launch")
    assert state.capability is not None and state.capability.tracked
    assert state.capability.fires is False
    v3_3 = next(c for c in state.criteria if c.id == "V3-3")
    assert "below the floors" in v3_3.detail


# --- The two abort guards must not disagree (ADR-058) ------------------------


def test_every_aborted_file_on_disk_says_so_in_its_content():
    """The filename guard and the content guard must agree on every real file.

    `_scan` excludes an aborted run two ways: the `aborted-*.yaml` glob and a
    `data.get("aborted")` content check. Two guards are only worth having if
    they cover for each other, and run 17's file was tripping exactly one —
    the filename. Copy that file under a `result-` name, or restore it from a
    backup that lost the prefix, and it re-enters the series as a build-100%
    reading over 1 of 5 cases. The redundancy has to be real on disk, not just
    present in the reader.
    """
    root = pathlib.Path(__file__).parent.parent / "benchmarks" / "results"
    files = sorted(root.glob("aborted-*.yaml"))
    assert files, "no aborted results on disk — this test has stopped checking anything"
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert data.get("aborted"), (
            f"{path.name} is excluded from the series only by its filename. "
            "Add an `aborted:` key naming why the run stopped, so the content "
            "check catches it too."
        )


def test_the_run_17_abort_is_excluded_by_content_alone(tmp_path):
    """Rename it to a `result-` name and it must still be kept out."""
    src = (
        pathlib.Path(__file__).parent.parent
        / "benchmarks" / "results"
        / "aborted-2026-08-17-1412-credit-exhausted.yaml"
    )
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True)
    (results / "result-2026-08-17-1412.yaml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    runs, aborted, _simulated = _scan(tmp_path)
    assert runs == []
    assert aborted == ["benchmarks/results/result-2026-08-17-1412.yaml"]
