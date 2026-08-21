"""The kill criterion must survive being read (ADR-054).

Three defects, all in the path between the bench result files and the human
at Gate PL5, and all found by running `avs bench-criterion` once against the
real repository rather than by reading the module that backs it.

1. The command crashed. Below the `evaluate()` block sat ten orphaned lines
   from the implementation it replaced, calling a `streak_state` that no
   longer exists anywhere. They were unreachable ONLY when the criterion
   fires — `typer.Exit(3)` raises above them — so the command raised
   `NameError` on every run where the project is healthy and "worked" only
   in the one case where it is not.

2. The ledger was ordered by filename with a glob of `*.yaml`, on a stated
   invariant ("oldest first, by filename — they are timestamped") that holds
   only while every name shares a prefix. ADR-052 added `aborted-*.yaml`
   beside `result-*.yaml`, and `a` sorts before `r`.

3. An aborted attempt counted as a run. `save_summary` records `aborted:`
   above the rates because "four cases failed" and "this run never got to
   ask them" look identical as percentages — and the one reader where the
   difference decides something never looked.

The common shape: a writer added something, and a reader that had documented
its assumptions was not updated with it.
"""

from __future__ import annotations

import pathlib

import ai_venture_studio.bench_criterion as bc


def _results(tmp_path):
    d = tmp_path / "benchmarks" / "results"
    d.mkdir(parents=True)
    return d


def _write(d, name, *, build, probes, aborted=None, measured=4, total=4):
    body = (
        f"build_rate: {build}\nprobe_pass_rate: {probes}\n"
        f"clean_review_rate: 0.5\n"
    )
    if aborted:
        # Quoted: real abort reasons carry colons ("error: environment: ...")
        # and `save_summary` writes them through `yaml.safe_dump`. Unquoted
        # here, the file is malformed and gets skipped one branch earlier —
        # which would have made this test pass for the wrong reason.
        body += f"aborted: {aborted!r}\n"
    body += f"rates: {{cases_measured: {measured}, cases_total: {total}}}\n"
    (d / name).write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# The command runs
# ---------------------------------------------------------------------------


def test_the_command_does_not_crash_when_the_criterion_holds(tmp_path):
    """The healthy path is the one that was broken, which is why nothing
    noticed: a fired criterion exits at 3 before reaching the dead code."""
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    d = _results(tmp_path)
    _write(d, "result-2026-08-01-0100.yaml", build=0.9, probes=0.9)
    _write(d, "result-2026-08-08-0100.yaml", build=0.9, probes=0.9)

    result = CliRunner().invoke(app, ["bench-criterion", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "NameError" not in result.output
    assert "0/2" in result.output


def test_a_fired_criterion_still_exits_three(tmp_path):
    """The half that did work must keep working — a script gating on this
    command is gating on the exit code, not the text."""
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    d = _results(tmp_path)
    _write(d, "result-2026-08-01-0100.yaml", build=0.1, probes=0.1)
    _write(d, "result-2026-08-08-0100.yaml", build=0.1, probes=0.1)

    result = CliRunner().invoke(app, ["bench-criterion", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 3
    assert "Gate PL5" in result.output


# ---------------------------------------------------------------------------
# An aborted attempt is not a run
# ---------------------------------------------------------------------------


def test_an_aborted_attempt_is_not_in_the_series(tmp_path):
    d = _results(tmp_path)
    _write(d, "result-2026-08-01-0100.yaml", build=0.9, probes=0.9)
    _write(d, "aborted-2026-08-08-credit.yaml", build=1.0, probes=1.0,
           aborted="credit balance too low", measured=1, total=5)

    assert [r.path.split("/")[-1] for r in bc.load_runs(tmp_path)] == [
        "result-2026-08-01-0100.yaml"
    ]
    # Out of the series, but not out of sight: excluding a file the reader
    # can see on disk and never mentioning it is the other half of the bug.
    assert [p.split("/")[-1] for p in bc.aborted_runs(tmp_path)] == [
        "aborted-2026-08-08-credit.yaml"
    ]


def test_an_abort_cannot_advance_the_kill_streak(tmp_path):
    """The inversion that made this worth fixing. Run 17 aborted on credit
    exhaustion and scored 100%, so it was harmless. Had the account died
    during a bad case instead, a BILLING failure would have advanced a
    streak whose consequence is a human deciding whether to kill the
    project."""
    d = _results(tmp_path)
    _write(d, "result-2026-08-01-0100.yaml", build=0.1, probes=0.1)
    _write(d, "result-2026-08-08-0100.yaml", build=0.1, probes=0.1,
           aborted="credit balance too low", measured=1, total=5)

    state = bc.evaluate(tmp_path)
    assert state.streak == 1
    assert state.fires is False


def test_the_abort_is_named_rather_than_silently_dropped(tmp_path):
    """A file the reader can see on disk and cannot find in the ledger is a
    reason to distrust the ledger."""
    d = _results(tmp_path)
    _write(d, "result-2026-08-01-0100.yaml", build=0.9, probes=0.9)
    _write(d, "result-2026-08-08-0100.yaml", build=1.0, probes=1.0,
           aborted="credit balance too low", measured=1, total=5)

    state = bc.evaluate(tmp_path)
    assert [p.split("/")[-1] for p in state.aborted_skipped] == [
        "result-2026-08-08-0100.yaml"
    ]
    assert bc.aborted_runs(tmp_path) == state.aborted_skipped


def test_the_abort_is_caught_by_content_not_by_its_name(tmp_path):
    """Both guards are real. The glob keeps `aborted-*.yaml` out and fixes
    the ordering; this check keeps an abort out even under a `result-` name,
    which is what a future writer is most likely to get wrong."""
    d = _results(tmp_path)
    _write(d, "result-2026-08-08-0100.yaml", build=0.1, probes=0.1,
           aborted="environment: disk full")
    assert bc.load_runs(tmp_path) == []
    assert len(bc.aborted_runs(tmp_path)) == 1


# ---------------------------------------------------------------------------
# The series is ordered, and holds only runs
# ---------------------------------------------------------------------------


def test_the_newest_run_is_last_even_beside_an_abort(tmp_path):
    """The ordering defect on its own: `a` sorts before `r`, so the newest
    file on disk was being placed at the OLDEST position and the "latest two
    runs" window silently excluded it."""
    d = _results(tmp_path)
    _write(d, "result-2026-08-01-0100.yaml", build=0.9, probes=0.9)
    _write(d, "result-2026-08-16-0100.yaml", build=0.7, probes=0.7)
    _write(d, "aborted-2026-08-17-credit.yaml", build=1.0, probes=1.0,
           aborted="credit balance too low")

    runs = bc.load_runs(tmp_path)
    # The WHOLE order, not just the tail. Asserting only `runs[-1]` passed on
    # the pre-fix build as well: `aborted-` sorts before `result-`, so the
    # misplaced file went to the FRONT and the newest *result* stayed last
    # either way. The defect this test is named for is visible only at the
    # position the abort actually took.
    assert [pathlib.Path(r.path).name for r in runs] == [
        "result-2026-08-01-0100.yaml",
        "result-2026-08-16-0100.yaml",
    ]
    # Excluded, and excluded is not invisible (ADR-054's own second draft).
    assert bc.aborted_runs(tmp_path) == [
        "benchmarks/results/aborted-2026-08-17-credit.yaml"
    ]


def test_a_stray_yaml_is_not_a_capability_reading(tmp_path):
    """`*.yaml` in a tracked directory meant any file someone dropped there
    could be parsed as a run of the series the kill criterion reads."""
    d = _results(tmp_path)
    _write(d, "result-2026-08-01-0100.yaml", build=0.9, probes=0.9)
    (d / "notes.yaml").write_text(
        "build_rate: 0.0\nprobe_pass_rate: 0.0\n", encoding="utf-8"
    )

    state = bc.evaluate(tmp_path)
    assert len(bc.load_runs(tmp_path)) == 1
    assert state.streak == 0


# ---------------------------------------------------------------------------
# The reading names its build
# ---------------------------------------------------------------------------


def test_the_cadence_says_which_build_produced_the_numbers(tmp_path):
    """`state` answers liveness in DAYS, and days is a proxy that breaks when
    releases outpace the cadence — the bench reads "ok, 4d" while its newest
    numbers came from nine releases back. The scheduler line already prints
    the running build; this puts the measured one beside it."""
    import ai_venture_studio.cadence as cadence

    path = tmp_path / "result-2026-08-16-0612.yaml"
    path.write_text(
        "build_rate: 1.0\nprobe_pass_rate: 0.75\navs_version: 0.93.0\n"
        "rates: {cases_measured: 3, cases_total: 4}\n",
        encoding="utf-8",
    )
    read = cadence._bench_rates(str(path))
    assert "build 100%, probes 75%" in read
    assert "over 3 of 4 cases" in read
    assert "measured on v0.93.0" in read


def test_a_run_too_old_to_name_its_build_says_nothing_extra(tmp_path):
    """Runs before 15 carry no `avs_version`. Absent is absent — inventing
    "unknown build" here would be noise on every historical row."""
    import ai_venture_studio.cadence as cadence

    path = tmp_path / "result-2026-07-27-0129.yaml"
    path.write_text(
        "build_rate: 0.75\nprobe_pass_rate: 0.75\n"
        "rates: {cases_measured: 4, cases_total: 4}\n",
        encoding="utf-8",
    )
    assert cadence._bench_rates(str(path)) == "build 75%, probes 75%"
