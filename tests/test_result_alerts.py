"""A run that finished is not the same question as a run that went well.

The channel reported only whether the machine RAN. Bench run 12 finished with
a crashed case, build 75% and probes 65%, and the alert path printed
`no alert: nothing needs a person` — because the loop itself had exited 0.
Run 14 took clean reviews from 75% to 38% while builds and probes went to
100%, and said nothing either, because every number involved was above its
floor and no floor covers clean review.

And none of it could have reached anyone anyway for runs 13, 14 and 15, which
were started by hand: the only thing in the system that could post to Discord
was `avs cadence --notify`, so a result only counted if launchd had produced
it. Two halves of one rule — **anything that can need a person reaches the
person, whatever produced it and whatever started it.**
"""
from __future__ import annotations

import datetime as dt
import types

import pytest
import yaml

from ai_venture_studio import bench_criterion, cadence, notify
from ai_venture_studio.cadence import CadenceReport, LoopStatus
from ai_venture_studio.product_bench import BenchSummary, CaseResult

TODAY = dt.date(2026, 8, 14)


@pytest.fixture(autouse=True)
def _no_real_webhook(tmp_path_factory, monkeypatch):
    """No test may read this machine's saved webhook, or reach the network."""
    monkeypatch.setattr(
        notify, "DEFAULT_WEBHOOK_PATH",
        str(tmp_path_factory.mktemp("config") / "discord-webhook"),
    )


def _result(root, name: str, build: float, probes: float, clean: float, **extra):
    """One recorded run, in the shape `bench_criterion.load_runs` reads."""
    results = root / "benchmarks" / "results"
    results.mkdir(parents=True, exist_ok=True)
    body = {
        "build_rate": build, "probe_pass_rate": probes,
        "clean_review_rate": clean, **extra,
    }
    (results / name).write_text(yaml.safe_dump(body), encoding="utf-8")


def _report(*loops: LoopStatus) -> CadenceReport:
    return CadenceReport(
        at=TODAY.isoformat(), repo_dir="/tmp/avs", loops=list(loops)
    )


def _ran_fine(name: str = "bench") -> LoopStatus:
    return LoopStatus(
        name=name, last_run=TODAY.isoformat(), age_days=0, state="ok",
        command=f"avs {name}",
    )


# --- a loop can run perfectly and still produce something you must see -------


def test_a_loop_that_ran_fine_can_still_need_a_person(tmp_path):
    """The run-12 shape at the alert layer: the loop exited 0, so nothing
    asked what it had produced."""
    _result(tmp_path, "result-2026-08-01-0000.yaml", 0.90, 0.90, 0.80)
    _result(tmp_path, "result-2026-08-14-0000.yaml", 0.33, 0.20, 0.10)

    concerns = cadence.result_concerns(tmp_path)
    assert concerns and concerns[0][0] == "bench"

    alert = notify.build_alert(_report(_ran_fine()), concerns=concerns)
    assert alert is not None, (
        "a healthy loop with an alarming result produced no alert — this is "
        "exactly the `nothing needs a person` that followed run 12"
    )
    assert "bench" in alert.heading


def test_nothing_to_say_is_still_nothing_to_say(tmp_path):
    """The rule the whole channel rests on. A concern that is empty must not
    become a daily all-green, or the one that matters gets swiped too."""
    _result(tmp_path, "result-2026-08-14-0000.yaml", 0.94, 0.92, 0.75)
    assert cadence.result_concerns(tmp_path) == []
    assert notify.build_alert(_report(_ran_fine()), concerns=[]) is None
    # And an empty sentence is not a concern, however it is spelled.
    assert notify.build_alert(_report(_ran_fine()), concerns=[("bench", "")]) is None


def test_a_fired_kill_criterion_says_so_in_words(tmp_path):
    for i, name in enumerate(("result-2026-08-12-0000.yaml", "result-2026-08-13-0000.yaml")):
        _result(tmp_path, name, 0.30, 0.20, 0.10 + i / 100)
    said = bench_criterion.concern(tmp_path)
    assert "HAS FIRED" in said
    assert "Gate PL5" in said


def test_one_run_below_the_floor_says_how_close_the_criterion_is(tmp_path):
    _result(tmp_path, "result-2026-08-12-0000.yaml", 0.94, 0.92, 0.75)
    _result(tmp_path, "result-2026-08-13-0000.yaml", 0.30, 0.20, 0.10)
    said = bench_criterion.concern(tmp_path)
    assert "HAS FIRED" not in said
    assert "1 more consecutive run(s) would fire" in said


def test_an_unmeasured_case_is_a_concern_even_when_the_rates_look_fine(tmp_path):
    """ADR-035's denominator, carried into the channel. The percentages are
    honest and still describe less of the machine than they appear to."""
    _result(
        tmp_path, "result-2026-08-14-0000.yaml", 1.0, 1.0, 1.0,
        rates={"cases_measured": 3, "cases_total": 4},
    )
    assert "did not measure the whole bench" in bench_criterion.concern(tmp_path)


def test_a_poor_result_is_a_finding_not_a_failure(tmp_path):
    """THE DISTINCTION THAT MUST NOT BE CONSISTENCY-FIXED AWAY (ADR-035).

    A run that measured everything and scored badly needs a person to LOOK.
    It does not mean the machine broke, and turning it into a non-zero exit
    would report every weak week as a broken scheduler.
    """
    _result(tmp_path, "result-2026-08-14-0000.yaml", 0.10, 0.10, 0.10)
    assert bench_criterion.concern(tmp_path)  # someone is told
    report = _report(_ran_fine())
    assert not report.stale  # ...and nothing is failed over it
    assert not notify._failures([])


def test_the_floors_have_exactly_one_definition():
    """`notify` and `cadence` both speak about being below the floor and
    neither may know what the floor IS — two copies drift the moment one
    moves (ADR-038). Comments stripped first: this file's own prose quotes
    the numbers, and so does the module docstring being read."""
    import pathlib

    for mod in (notify, cadence):
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "0.60" not in code and "0.50" not in code, (
            f"{mod.__name__} names a floor value; bench_criterion owns those"
        )


# --- movement: the sentence run 14 needed and no threshold could give it -----


def test_movement_reports_a_collapse_no_floor_would_catch(tmp_path):
    """Run 14's real shape: builds and probes to 100%, clean reviews 75 → 38,
    every number above its floor, nobody told anything."""
    _result(tmp_path, "result-2026-08-13-0837.yaml", 0.94, 0.92, 0.75)
    _result(tmp_path, "result-2026-08-14-0139.yaml", 1.0, 1.0, 0.38)
    said = bench_criterion.movement(tmp_path)
    assert "clean -37pp" in said
    assert "build +6pp" in said
    # And it stays a statement, never a verdict — there is no floor on clean
    # review and this module does not get to invent one.
    assert bench_criterion.concern(tmp_path) == ""


def test_movement_needs_two_runs_to_have_a_direction(tmp_path):
    _result(tmp_path, "result-2026-08-14-0139.yaml", 1.0, 1.0, 0.38)
    assert bench_criterion.movement(tmp_path) == ""


# --- the manual run reports in too ------------------------------------------


def _summary(*, unmeasured=(), status="completed") -> BenchSummary:
    return BenchSummary(
        cases=[
            CaseResult(name="01-groupbuy", autopilot_status=status),
            CaseResult(name="02-shortener", autopilot_status="completed"),
        ],
        build_rate=0.94, probe_pass_rate=0.92, clean_review_rate=0.75,
        unmeasured=list(unmeasured),
    )


def test_a_finished_run_reports_its_own_result(tmp_path):
    """Runs 13, 14 and 15 were all started by hand, so nothing about them
    could reach Discord at all — the alert existed only for launchd."""
    alert = notify.bench_alert(
        _summary(), workspace="autoproduct", saved="benchmarks/results/r.yaml",
        movement="vs r0.yaml: clean -37pp",
    )
    assert "build 94%" in alert.heading
    assert "clean -37pp" in alert.render()
    assert "r.yaml" in alert.render()


def test_a_finished_run_leads_with_what_never_ran(tmp_path):
    alert = notify.bench_alert(
        _summary(unmeasured=["04-direction-workbench"], status="error: TimeoutExpired"),
        workspace="autoproduct",
    )
    body = alert.render()
    assert "⚠" in alert.heading
    assert "over 1 of 2 cases" in alert.heading
    # Named before the rates are explained away, and the crashed case's own
    # words survive rather than being paraphrased into "a case failed".
    assert body.index("04-direction-workbench") < body.index("Excluded from the rates")
    assert "TimeoutExpired" in body


def test_a_run_that_never_finished_is_the_loudest_case(tmp_path):
    alert = notify.bench_failed_alert(
        "RuntimeError: no product cases in benchmarks/products-real",
        workspace="autoproduct",
    )
    assert "FAILED to finish" in alert.heading
    assert "no product cases" in alert.render()


def test_a_clean_run_still_reports(tmp_path):
    """The one documented deviation from `only when something needs a
    person`: this is weekly, costs hours and real money, and someone is
    waiting on it. Silence on success is the original complaint."""
    alert = notify.bench_alert(_summary(), workspace="autoproduct")
    assert alert is not None
    assert "⚠" not in alert.heading


# --- two alert kinds must not erase each other's memory ---------------------


def test_two_alert_kinds_remember_separately(tmp_path):
    """One shared sent-record would let each alert re-send the other's
    suppressed news — the de-duplication is the only thing holding the
    channel back from repeating itself."""
    cadence_alert = notify.Alert(heading="**avs** — compound is overdue")
    bench = notify.bench_alert(_summary(), workspace="avs")

    notify.record_sent(tmp_path, cadence_alert, at=TODAY, kind=notify.CADENCE_KIND)
    notify.record_sent(tmp_path, bench, at=TODAY, kind=notify.BENCH_KIND)

    assert not notify.is_worth_repeating(
        notify.load_sent(tmp_path, kind=notify.CADENCE_KIND),
        cadence_alert, today=TODAY,
    )
    assert not notify.is_worth_repeating(
        notify.load_sent(tmp_path, kind=notify.BENCH_KIND), bench, today=TODAY,
    )


def test_a_pre_kind_sent_file_is_read_as_the_cadence_alert(tmp_path):
    """The file shape before v0.90.0. Reading it as a bench record would
    hand a brand-new alert a digest it had never sent, and silence its
    first message."""
    alert = notify.Alert(heading="**avs** — compound is overdue")
    path = tmp_path / notify.SENT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({
            "digest": alert.digest, "at": TODAY.isoformat(),
            "heading": alert.heading,
        }),
        encoding="utf-8",
    )
    assert not notify.is_worth_repeating(
        notify.load_sent(tmp_path, kind=notify.CADENCE_KIND), alert, today=TODAY
    )
    assert notify.load_sent(tmp_path, kind=notify.BENCH_KIND) == {}


def test_recording_one_kind_keeps_the_other(tmp_path):
    first = notify.Alert(heading="**avs** — compound is overdue")
    notify.record_sent(tmp_path, first, at=TODAY, kind=notify.CADENCE_KIND)
    notify.record_sent(
        tmp_path, notify.bench_alert(_summary()), at=TODAY, kind=notify.BENCH_KIND,
    )
    assert notify.load_sent(tmp_path, kind=notify.CADENCE_KIND)["digest"] == first.digest


# --- one delivery path, whatever the alert is -------------------------------


class _Sent:
    def __init__(self):
        self.calls = []

    def __call__(self, request, timeout=None):  # noqa: ARG002
        self.calls.append(request)

        class _Response:
            status = 204

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return _Response()


def test_every_alert_goes_out_through_one_sender(tmp_path):
    """The second sender is where a notifier grows a second de-dup rule, a
    second webhook lookup, and a second silent failure mode."""
    sender = _Sent()
    env = {notify.WEBHOOK_ENV: "https://discord.com/api/webhooks/1/abc"}
    result = notify.send(
        tmp_path, notify.bench_alert(_summary(), workspace="avs"),
        kind=notify.BENCH_KIND, today=TODAY, environ=env, opener=sender,
    )
    assert result.sent and len(sender.calls) == 1


def test_a_bench_run_is_an_event_not_a_standing_condition(monkeypatch, tmp_path):
    """Two crashed runs in one week are two things that happened. The repeat
    window exists for a condition that stays true, and applying it here would
    drop the second crash on the floor."""
    sender = _Sent()
    env = {notify.WEBHOOK_ENV: "https://discord.com/api/webhooks/1/abc"}
    monkeypatch.setattr(notify, "resolve_webhook", lambda environ=None: env[notify.WEBHOOK_ENV])
    same = notify.bench_failed_alert("RuntimeError: boom", workspace="avs")
    for _ in range(2):
        notify.send(
            tmp_path, same, kind=notify.BENCH_KIND, today=TODAY,
            environ=env, opener=sender, force=True,
        )
    assert len(sender.calls) == 2


def test_the_cli_forces_the_bench_alert_past_the_repeat_window():
    """The `force=True` above is the CLI's call, so read it there."""
    import inspect

    from ai_venture_studio import cli

    source = inspect.getsource(cli._bench_notify)
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "force=True" in code
    assert "BENCH_KIND" in code


def test_the_manual_path_reports_a_failed_delivery(capsys, monkeypatch, tmp_path):
    """A notifier that fails quietly is worse than none: the log nobody reads
    at least never claimed to have told anyone."""
    from ai_venture_studio import cli

    def _refuse(*a, **k):
        raise notify.NotifyError("no webhook configured")

    monkeypatch.setattr(notify, "send", _refuse)
    cli._bench_notify(str(tmp_path), notify.bench_alert(_summary()))
    assert "alert NOT sent" in capsys.readouterr().out


def test_product_bench_offers_the_flag_at_all():
    """The gap was never that the alert was wrong — it was that the only
    door to Discord was the scheduler's."""
    import inspect

    from ai_venture_studio import cli

    option = inspect.signature(cli.product_bench).parameters["notify"].default
    assert "--notify" in getattr(option, "param_decls", [])


def test_the_crash_path_notifies_too():
    """A run that dies has no result file, no rates, and — started by hand —
    no exit code anyone reads. It was the quietest of the three outcomes."""
    import inspect

    from ai_venture_studio import cli

    source = inspect.getsource(cli.product_bench)
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "bench_failed_alert" in code


def test_cadence_hands_its_concerns_to_the_alert():
    """The wiring, read where it is: computing concerns and not passing them
    would look identical from every test above."""
    import inspect

    from ai_venture_studio import cli

    source = inspect.getsource(cli.cadence_cmd)
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "concerns=cad.result_concerns" in code


def test_a_concern_is_reported_but_never_fails_the_scheduler():
    """ADR-035's rule, one level up: the exit code answers `did the machine
    break`, and a bad number is not a break. Consistency-fixing this is how
    every weak week becomes a broken scheduler."""
    import inspect

    from ai_venture_studio import cli

    source = inspect.getsource(cli.cadence_cmd)
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "if report.stale or build.behind:" in code
    assert "concern" not in code.split("if report.stale")[1]


def test_result_concerns_is_silent_where_the_bench_does_not_live(tmp_path):
    """A product workspace owns no bench series. Reporting one it cannot run
    would be a standing false alarm in the channel that must not cry wolf."""
    assert cadence.result_concerns(tmp_path) == []
    assert bench_criterion.movement(tmp_path) == ""


def test_a_concern_names_the_loop_that_raised_it():
    alert = notify.build_alert(
        _report(_ran_fine()), concerns=[("bench", "below the floors")]
    )
    assert "**bench**" in alert.render()
    assert "below the floors" in alert.render()


def test_failures_still_outrank_concerns_in_the_heading():
    """`render` truncates from the end and the heading is the phone preview:
    a loop that broke this morning outranks one that merely scored badly."""
    outcome = types.SimpleNamespace(
        loop="sweep", ran=True, exit_code=1, detail="Traceback: boom"
    )
    alert = notify.build_alert(
        _report(_ran_fine()), outcomes=[outcome],
        concerns=[("bench", "below the floors")],
    )
    assert "FAILED this run" in alert.heading
    body = alert.render()
    assert body.index("**sweep**") < body.index("**bench**")
