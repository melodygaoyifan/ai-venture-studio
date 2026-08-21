"""What makes the $67.88 run due — and what stops making it due.

ADR-063. The bench measures the FRAMEWORK's capability against four labelled
real products. It was scheduled by age alone: seven days since the last
result and the daily LaunchAgent fires a five-hour, API-billed run. Run 19 was
about to be bought that way — by a calendar, over a framework that had not
necessarily changed.

So the trigger becomes the reading's own `avs_version`, floored and capped:

  - the build changed and the floor has passed → due;
  - the build has not changed → the long drift backstop, because the provider
    moves whether or not we ship, and a criterion that only ever asks after
    OUR edits is a criterion that can stop firing.

The second half is the load-bearing one. `cadence.LOOP_NAMES` names the single
thing a watchdog must never do — report "all clear" forever — and "the version
has not changed" is exactly the sentence that talks one into it. Half of these
tests exist to keep this change from becoming that.
"""

from __future__ import annotations

import datetime as dt

import pytest
import yaml

from ai_venture_studio import cadence, notify

TODAY = dt.date(2026, 8, 21)


def _cases(root):
    (root / "benchmarks" / "products-real").mkdir(parents=True, exist_ok=True)


def _result(root, date: str, *, version: str | None = "0.109.0"):
    directory = root / "benchmarks" / "results"
    directory.mkdir(parents=True, exist_ok=True)
    body = {"build_rate": 0.75, "probe_pass_rate": 0.75}
    if version is not None:
        body["avs_version"] = version
    (directory / f"result-{date}-0449.yaml").write_text(
        yaml.safe_dump(body), encoding="utf-8"
    )


def _bench(root, today=TODAY):
    loops = cadence.assess(root, today=today, only=["bench"]).loops
    return loops[0]


@pytest.fixture
def running(monkeypatch):
    """Pin the running build so these read as fixtures and not as history."""

    def _set(version: str):
        monkeypatch.setattr("ai_venture_studio.__version__", version)

    return _set


# --- the run that no longer gets bought --------------------------------------


def test_a_week_passing_over_an_unchanged_framework_is_not_news(
    tmp_path, running
):
    """Run 19, as it stood: the last reading is eight days old, nothing has
    shipped since, and the old rule scheduled $67.88 to measure it again."""
    running("0.109.0")
    _cases(tmp_path)
    _result(tmp_path, "2026-08-13", version="0.109.0")

    loop = _bench(tmp_path)
    assert loop.age_days == 8
    assert loop.state == "ok", (
        "eight days is past the old cadence; the framework that produced the "
        "reading is the same one running now, so there is nothing to remeasure"
    )
    assert loop.needs_run is False


def test_the_same_eight_days_across_a_release_is_due(tmp_path, running):
    """The other half of the same fixture — only the version differs."""
    running("0.110.0")
    _cases(tmp_path)
    _result(tmp_path, "2026-08-13", version="0.109.0")

    loop = _bench(tmp_path)
    assert loop.needs_run is True
    assert "0.109.0" in loop.due_because and "0.110.0" in loop.due_because, (
        f"a reader deciding whether to spend $67.88 needs both builds named; "
        f"got {loop.due_because!r}"
    )


def test_ten_releases_in_a_week_do_not_buy_ten_runs(tmp_path, running):
    """The floor. Change is necessary and it is not sufficient."""
    running("0.120.0")
    _cases(tmp_path)
    _result(tmp_path, "2026-08-19", version="0.110.0")

    loop = _bench(tmp_path)
    assert loop.age_days == 2
    assert loop.state == "ok"
    assert loop.cadence_days == cadence.BENCH_MIN_SPACING_DAYS


# --- the half that keeps this from becoming a check that never fires ---------


def test_an_unchanged_framework_still_comes_due_eventually(tmp_path, running):
    """The provider underneath this system changes whether or not we ship.
    Without this, a quiet release month is indistinguishable from a bench that
    has been switched off, and the row says `ok` through both."""
    running("0.109.0")
    _cases(tmp_path)
    _result(tmp_path, "2026-01-01", version="0.109.0")

    loop = _bench(tmp_path)
    assert loop.state == "overdue", (
        "232 days on one reading is not a healthy loop, whatever the version "
        "field says"
    )
    assert loop.due_because, "and it has to say which of the two rules fired"
    assert "drift" in loop.due_because


def test_the_backstop_is_a_number_and_not_infinity():
    """Written as a test because the failure mode is a config edit: someone
    sets this to 3650 'for now' and the difference between that and 'never' is
    a career. `LOOP_NAMES`' comment is the standing rule this enforces."""
    assert cadence.BENCH_DRIFT_BACKSTOP_DAYS <= 120, (
        "past a quarter, an unchanged-framework reading is stale on the "
        "provider's account alone"
    )
    assert (
        cadence.BENCH_MIN_SPACING_DAYS
        < cadence.BENCH_DRIFT_BACKSTOP_DAYS
    )


def test_a_reading_that_does_not_say_which_build_made_it_counts_as_changed(
    tmp_path, running
):
    """Runs before 15 recorded no `avs_version`. An unknown build is not
    evidence of the same build, and the direction to fail in is the one that
    measures rather than the one that skips forever."""
    running("0.109.0")
    _cases(tmp_path)
    _result(tmp_path, "2026-08-01", version=None)

    loop = _bench(tmp_path)
    assert loop.needs_run is True
    assert loop.cadence_days == cadence.BENCH_MIN_SPACING_DAYS
    assert "does not record" in loop.due_because


def test_the_new_rule_never_schedules_a_run_the_old_one_would_not_have(
    tmp_path, running
):
    """The cost claim, checked rather than asserted in prose: this change is
    strictly cheaper. Every date on which the version rule fires is a date on
    which a 7-day timer would also have fired."""
    running("0.110.0")
    _cases(tmp_path)
    _result(tmp_path, "2026-06-01", version="0.109.0")

    for offset in range(0, 120, 3):
        day = dt.date(2026, 6, 1) + dt.timedelta(days=offset)
        loop = _bench(tmp_path, today=day)
        if loop.needs_run:
            assert loop.age_days >= cadence.WEEKLY, (
                f"due at {loop.age_days}d — inside the old weekly cadence, "
                f"which would make this change more expensive, not less"
            )


# --- and the reason has to reach a person ------------------------------------


def test_an_ok_loop_carries_no_reason(tmp_path, running):
    """A sentence explaining why something is due, printed beside a thing that
    is not due, is noise that trains the reader to skip the column."""
    running("0.109.0")
    _cases(tmp_path)
    _result(tmp_path, "2026-08-20", version="0.109.0")

    loop = _bench(tmp_path)
    assert loop.state == "ok"
    assert loop.due_because == ""
    assert "next 2026-11-18" in loop.describe(), (
        f"the stated next-due date has to follow the cadence actually in "
        f"force, not the 7 days it used to be: {loop.describe()!r}"
    )


def test_the_scheduler_row_says_what_made_it_due(tmp_path, running):
    running("0.110.0")
    _cases(tmp_path)
    _result(tmp_path, "2026-08-01", version="0.109.0")

    said = _bench(tmp_path).describe()
    assert said.startswith("OVERDUE (20d)")
    assert "framework changed" in said, (
        f"'OVERDUE (20d)' alone reads as a timer, and a timer is no longer "
        f"what raises this: {said!r}"
    )


def test_the_alert_carries_the_reason_next_to_the_command(tmp_path, running):
    """The alert is the surface where the money gets spent — it names the
    command to run. It has to name what changed too."""
    running("0.110.0")
    _cases(tmp_path)
    _result(tmp_path, "2026-08-01", version="0.109.0")

    report = cadence.assess(tmp_path, today=TODAY, only=["bench"])
    alert = notify.build_alert(report, workspace="autoproduct")
    assert alert is not None
    body = "\n".join(alert.lines)
    assert "framework changed" in body
    assert "product-bench" in body
