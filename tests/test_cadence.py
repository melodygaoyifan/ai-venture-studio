"""The loops' watchdog. What these pin, in one line each: a loop that never
ran must never read as fresh, and a loop needing a human number must never be
answered by the machine."""

import datetime as dt
import plistlib

import pytest
import yaml

from ai_venture_studio import cadence


def _compound_proposal(root, date: str):
    directory = root / ".mas" / "compound"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"proposal-{date}.md").write_text("# proposal\n", encoding="utf-8")


def _sweep_digest(root, date: str):
    directory = root / ".mas" / "sweep"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"digest-{date}.yaml").write_text("at: x\n", encoding="utf-8")


def _attention_log(root, rows):
    directory = root / "metrics"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "attention-log.yaml").write_text(
        yaml.safe_dump({"log": rows}, sort_keys=False), encoding="utf-8"
    )


def _loop(report, name):
    return next(loop for loop in report.loops if loop.name == name)


TODAY = dt.date(2026, 8, 5)


def test_empty_workspace_is_never_run_not_fresh(tmp_path):
    """The failure mode a watchdog can actually have: reading absence as a
    clean pass."""
    report = cadence.assess(tmp_path, today=TODAY)
    assert {loop.state for loop in report.loops} == {"never_run"}
    assert all(loop.age_days is None for loop in report.loops)
    assert all(loop.last_run == "" for loop in report.loops)
    assert len(report.stale) == 3


def test_fresh_loops_are_ok(tmp_path):
    _compound_proposal(tmp_path, "2026-08-03")
    _sweep_digest(tmp_path, "2026-08-01")
    _attention_log(tmp_path, [
        {"week": "2026-W31", "window": "w", "hours": 2.0, "status": "logged",
         "decided_by": "melody"},
    ])
    report = cadence.assess(tmp_path, today=TODAY)
    assert _loop(report, "compound").state == "ok"
    assert _loop(report, "compound").age_days == 2
    assert _loop(report, "sweep").state == "ok"
    # 2026-W31 closes Sunday 2026-08-02; three days before "today".
    assert _loop(report, "attention").state == "ok"
    assert report.stale == []


def test_grace_separates_due_from_overdue(tmp_path):
    """A weekly loop is seven days old on the day it is next due — that is
    health, not staleness. Only a slide past the grace window fails a gate."""
    # Separate workspaces: the newest artifact wins, so an older file added
    # beside a newer one would not age the loop.
    _compound_proposal(tmp_path / "at_seven", "2026-07-29")  # 7 days
    due = _loop(cadence.assess(tmp_path / "at_seven", today=TODAY), "compound")
    assert due.state == "due"
    assert due.needs_run is True
    assert due.is_stale is False

    _compound_proposal(tmp_path / "at_ten", "2026-07-26")  # 10 days > 7 + 2
    late = _loop(cadence.assess(tmp_path / "at_ten", today=TODAY), "compound")
    assert late.state == "overdue"
    assert late.is_stale is True


def test_newest_artifact_wins_and_names_itself(tmp_path):
    _compound_proposal(tmp_path, "2026-06-01")
    _compound_proposal(tmp_path, "2026-08-04")
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "compound")
    assert loop.last_run == "2026-08-04"
    # Provenance travels with the verdict so a surprise is checkable.
    assert loop.evidence.endswith("proposal-2026-08-04.md")


def test_future_dated_artifact_clamps_to_zero(tmp_path):
    """Clock skew must not invent a negative staleness on top of a bad date."""
    _sweep_digest(tmp_path, "2026-09-01")
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "sweep")
    assert loop.age_days == 0
    assert loop.state == "ok"


def test_not_tracked_attention_week_is_not_a_run(tmp_path):
    """The live log's only row is `not_tracked`. Counting it would let the
    series the kill criterion depends on look maintained while measuring
    nothing."""
    _attention_log(tmp_path, [
        {"week": "2026-W30", "window": "w", "hours": None,
         "status": "not_tracked", "decided_by": "melody"},
    ])
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "attention")
    assert loop.state == "never_run"
    assert loop.human_input_required is True


def test_unreadable_attention_log_is_not_fresh(tmp_path):
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "attention-log.yaml").write_text(
        "log: [ this is not: valid: yaml", encoding="utf-8"
    )
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "attention")
    assert loop.state == "never_run"
    assert "unreadable" in loop.evidence


def test_iso_week_end_is_the_sunday():
    assert cadence._iso_week_end("2026-W31") == dt.date(2026, 8, 2)
    assert cadence._iso_week_end("nonsense") is None
    assert cadence._iso_week_end("") is None


def test_run_due_skips_fresh_loops(tmp_path, monkeypatch):
    """What makes a daily trigger safe against weekly work."""
    _compound_proposal(tmp_path, "2026-08-04")
    _sweep_digest(tmp_path, "2026-08-04")
    _attention_log(tmp_path, [
        {"week": "2026-W31", "window": "w", "hours": 1.0, "status": "logged",
         "decided_by": "melody"},
    ])
    calls = []
    monkeypatch.setattr(
        cadence.subprocess, "run",
        lambda argv, **kw: calls.append(argv) or _completed(),
    )
    outcomes = cadence.run_due(tmp_path, today=TODAY)
    assert calls == []
    assert all(not o.ran for o in outcomes)
    assert all("not due" in o.detail for o in outcomes)


def test_run_due_runs_only_what_is_due(tmp_path, monkeypatch):
    _compound_proposal(tmp_path, "2026-08-04")  # fresh
    _sweep_digest(tmp_path, "2026-06-01")       # overdue
    calls = []
    monkeypatch.setattr(
        cadence.subprocess, "run",
        lambda argv, **kw: calls.append(argv) or _completed(),
    )
    outcomes = cadence.run_due(tmp_path, today=TODAY, executable="/usr/local/bin/avs")
    ran = {o.loop for o in outcomes if o.ran}
    assert ran == {"sweep", "attention"}
    # Each command spells its workspace option the way that command spells it.
    by_name = {argv[1]: argv for argv in calls}
    assert by_name["sweep"][2] == "--workspace"
    assert by_name["attention"][2] == "--repo-dir"


def test_attention_is_surfaced_never_answered(tmp_path, monkeypatch):
    """`avs attention` logs a row only with --confirm-hours, and the machine
    must never supply one: the number is the operator's."""
    monkeypatch.setattr(
        cadence.subprocess, "run", lambda argv, **kw: _completed(argv=argv)
    )
    outcomes = cadence.run_due(tmp_path, today=TODAY, executable="/usr/local/bin/avs")
    attention = next(o for o in outcomes if o.loop == "attention")
    assert "--confirm-hours" not in " ".join(attention.detail.split())
    assert "surfaced for your decision" in attention.detail


def test_interpreter_fallback_is_a_module_that_exists(tmp_path, monkeypatch):
    """`-m ai_venture_studio` has no `__main__.py` and dies with "No module
    named ai_venture_studio.__main__". The `.cli` form is the one that runs."""
    import importlib.util

    calls = []
    monkeypatch.setattr(
        cadence.subprocess, "run",
        lambda argv, **kw: calls.append(argv) or _completed(),
    )
    cadence.run_due(tmp_path, today=TODAY, executable="/usr/bin/python3")
    assert calls[0][1:3] == ["-m", "ai_venture_studio.cli"]
    assert importlib.util.find_spec("ai_venture_studio.cli") is not None


def test_run_due_survives_a_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cadence.subprocess, "run",
        lambda argv, **kw: (_ for _ in ()).throw(FileNotFoundError("no avs")),
    )
    outcomes = cadence.run_due(tmp_path, today=TODAY, executable="/nope/avs")
    assert all(not o.ran for o in outcomes)
    assert all("could not run" in o.detail for o in outcomes)


def test_plist_schedules_daily_and_does_not_run_at_load(tmp_path):
    body = plistlib.loads(
        cadence.render_plist(tmp_path, executable="/usr/local/bin/avs", hour=9)
    )
    assert body["Label"] == cadence.LAUNCH_AGENT_LABEL
    assert body["ProgramArguments"][:2] == ["/usr/local/bin/avs", "cadence"]
    assert "--run-due" in body["ProgramArguments"]
    assert body["StartCalendarInterval"] == {"Hour": 9, "Minute": 0}
    # Installing a trigger must not itself start a run.
    assert body["RunAtLoad"] is False


def test_plist_rejects_an_impossible_time(tmp_path):
    with pytest.raises(cadence.CadenceError):
        cadence.render_plist(tmp_path, hour=25)


def test_install_refuses_a_workspace_with_no_state(tmp_path):
    """A scheduler pointed at a workspace without `.mas/` would run forever
    and find nothing — the silent-success failure, installed."""
    with pytest.raises(cadence.CadenceError, match=r"\.mas"):
        cadence.install_agent(
            tmp_path, load=False, plist_path=tmp_path / "agent.plist"
        )


def test_install_writes_the_plist_but_arms_nothing(tmp_path, monkeypatch):
    (tmp_path / ".mas").mkdir()
    monkeypatch.setattr(
        cadence, "agent_log_path", lambda: tmp_path / "logs" / "loops.log"
    )
    target = tmp_path / "agent.plist"
    done = cadence.install_agent(
        tmp_path, executable="/usr/local/bin/avs", load=False, plist_path=target
    )
    assert target.exists()
    assert done["loaded"] is False
    assert done["command"].startswith("launchctl bootstrap")
    assert plistlib.loads(target.read_bytes())["Label"] == cadence.LAUNCH_AGENT_LABEL


def _proposal_text(root, date: str, *, reviews: int, barren: bool = True,
                   why: str = ""):
    directory = root / ".mas" / "compound"
    directory.mkdir(parents=True, exist_ok=True)
    body = [
        f"# Compounding-loop proposal — {date}", "",
        f"Window: {reviews} review(s). Verdicts: {{}}.",
    ]
    if why:
        body.append(f"Nothing reached this window: {why}")
    body += [
        "",
        "## Proposed CLAUDE.md constraints",
        "- (no constraint met the evidence bar this window)" if barren
        else "- always name the port explicitly",
    ]
    (directory / f"proposal-{date}.md").write_text(
        "\n".join(body), encoding="utf-8"
    )


def test_a_run_that_read_nothing_is_not_a_productive_run(tmp_path):
    """The narrow "looks done": compound with no reviews in its window writes
    a proposal without ever calling a provider, and a date-only check calls
    that fresh for seven days."""
    _proposal_text(tmp_path, "2026-08-04", reviews=0)
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "compound")
    # The loop genuinely ran, so it is NOT stale and must not fail a gate.
    assert loop.state == "ok"
    assert loop.is_stale is False
    # But it read nothing, and that has to be visible.
    assert loop.vacuous is True
    assert "0 reviews" in loop.produced
    assert "empty" in loop.describe()


def test_reading_reviews_and_finding_nothing_is_a_real_result(tmp_path):
    """The distinction the fix turns on: examining twelve reviews and
    concluding nothing crossed the bar is work, not emptiness."""
    _proposal_text(tmp_path, "2026-08-04", reviews=12, barren=True)
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "compound")
    assert loop.vacuous is False
    assert "12 review(s)" in loop.produced
    assert "no constraint met the bar" in loop.produced


def test_a_proposed_constraint_is_reported_as_such(tmp_path):
    _proposal_text(tmp_path, "2026-08-04", reviews=9, barren=False)
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "compound")
    assert loop.vacuous is False
    assert "constraint(s) proposed" in loop.produced


def test_summary_will_not_call_an_empty_window_simply_fine(tmp_path):
    _proposal_text(tmp_path, "2026-08-04", reviews=0)
    _sweep_digest(tmp_path, "2026-08-04")
    _attention_log(tmp_path, [
        {"week": "2026-W31", "window": "w", "hours": 1.0, "status": "logged",
         "decided_by": "melody"},
    ])
    report = cadence.assess(tmp_path, today=TODAY)
    assert report.stale == []
    assert [loop.name for loop in report.vacuous] == ["compound"]
    assert "nothing to read" in report.summary()
    assert "compound" in report.summary()


def test_an_unreadable_proposal_format_claims_nothing(tmp_path):
    """An older artifact says nothing about its own substance. Silence beats
    a guess in either direction."""
    directory = tmp_path / ".mas" / "compound"
    directory.mkdir(parents=True)
    (directory / "proposal-2026-08-04.md").write_text("# old\n", encoding="utf-8")
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "compound")
    assert loop.vacuous is False
    assert loop.produced == ""
    assert loop.state == "ok"


def test_a_sweep_clean_pass_is_never_called_vacuous(tmp_path):
    """Invariant 14.30: a clean pass is a finding recorded, not a silence.
    Sweep inspecting its surface and finding no chores is real work."""
    directory = tmp_path / ".mas" / "sweep"
    directory.mkdir(parents=True)
    (directory / "digest-2026-08-04.yaml").write_text(
        yaml.safe_dump({
            "at": "2026-08-04", "items_inspected": 0, "clean_pass": True,
            "note": "clean pass — recorded, not silent (invariant 14.30)",
        }),
        encoding="utf-8",
    )
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "sweep")
    assert loop.vacuous is False
    assert "clean pass" in loop.produced


def test_plist_carries_the_credential_pointer(tmp_path):
    """launchd does not read a login shell. Without this the 09:00 run reaches
    its provider with no credential and fails every morning into a log."""
    env, warnings = cadence.scheduled_env(
        {"ANTHROPIC_API_KEY_FILE": "/Users/x/.secrets/k", "IRRELEVANT": "y"},
        binary="/opt/env/bin/avs",
    )
    assert env["ANTHROPIC_API_KEY_FILE"] == "/Users/x/.secrets/k"
    assert "IRRELEVANT" not in env
    # The interpreter's own bin dir leads, so a subprocess resolving by name
    # finds this `avs` and not launchd's bare four-entry PATH.
    assert env["PATH"].split(":")[0] == "/opt/env/bin"
    assert warnings == []

    body = plistlib.loads(cadence.render_plist(tmp_path, env=env))
    assert body["EnvironmentVariables"]["ANTHROPIC_API_KEY_FILE"] == "/Users/x/.secrets/k"


def test_a_raw_secret_is_never_written_to_the_plist(tmp_path):
    """The plist is a readable file in ~/Library. A key in it would make the
    scheduler a credential leak, so the secret is refused and named."""
    env, warnings = cadence.scheduled_env({"ANTHROPIC_API_KEY": "sk-ant-REAL"})
    assert "ANTHROPIC_API_KEY" not in env
    assert not any("sk-ant-REAL" in v for v in env.values())
    assert any("ANTHROPIC_API_KEY" in w and "_FILE" in w for w in warnings)

    body = plistlib.loads(cadence.render_plist(tmp_path, env=env))
    assert "sk-ant-REAL" not in plistlib.dumps(body).decode()


def test_no_credential_at_all_is_a_warning_not_a_silent_install(tmp_path):
    _, warnings = cadence.scheduled_env({})
    assert any("without a credential" in w for w in warnings)


def test_install_reports_what_it_carried(tmp_path, monkeypatch):
    (tmp_path / ".mas").mkdir()
    monkeypatch.setattr(
        cadence, "agent_log_path", lambda: tmp_path / "logs" / "loops.log"
    )
    # The real shell may export any of these; the assertion is about what the
    # installer carries, not about this machine.
    for name in (*cadence.ENV_SECRETS, *cadence.ENV_POINTERS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", "/Users/x/.secrets/k")
    done = cadence.install_agent(
        tmp_path, executable="/usr/local/bin/avs", load=False,
        plist_path=tmp_path / "agent.plist",
    )
    assert done["env_keys"] == ["ANTHROPIC_API_KEY_FILE"]
    assert done["warnings"] == []


def test_the_webhook_pointer_travels_but_the_webhook_itself_does_not(tmp_path):
    """A Discord webhook URL is a credential: whoever holds it can post into
    the channel as this app. So it follows the same rule as a model key —
    the pointer goes into the world-readable plist, the URL never does."""
    env, warnings = cadence.scheduled_env({
        "ANTHROPIC_API_KEY_FILE": "/Users/x/.secrets/k",
        "AVS_DISCORD_WEBHOOK_FILE": "/Users/x/.secrets/hook",
        "AVS_DISCORD_WEBHOOK": "https://discord.com/api/webhooks/1/REAL",
    })
    assert env["AVS_DISCORD_WEBHOOK_FILE"] == "/Users/x/.secrets/hook"
    assert "AVS_DISCORD_WEBHOOK" not in env
    assert not any("REAL" in value for value in env.values())
    assert any("AVS_DISCORD_WEBHOOK" in w and "_FILE" in w for w in warnings)

    body = plistlib.loads(cadence.render_plist(tmp_path, env=env))
    assert "REAL" not in plistlib.dumps(body).decode()


def test_a_webhook_is_not_a_model_credential(tmp_path):
    """The trap this guards: the webhook pointer also ends in `_FILE`, so a
    naive "is anything pointed at a secret?" check would read a workspace
    that can notify but cannot authenticate as fully configured — and the
    09:00 run would fail every morning with no warning at install time."""
    _, warnings = cadence.scheduled_env(
        {"AVS_DISCORD_WEBHOOK_FILE": "/Users/x/.secrets/hook"}
    )
    assert any("without a credential" in w for w in warnings)


def test_the_scheduler_only_notifies_when_asked(tmp_path):
    plain = plistlib.loads(cadence.render_plist(tmp_path))
    asked = plistlib.loads(cadence.render_plist(tmp_path, notify=True))
    assert "--notify" not in plain["ProgramArguments"]
    assert "--notify" in asked["ProgramArguments"]


def test_installing_alerts_with_nowhere_to_send_them_warns(tmp_path, monkeypatch):
    """Otherwise the install prints success, the plist carries `--notify`, and
    every morning the alert fails to a log — the exact silence it was
    installed to end."""
    from ai_venture_studio import notify as notifier

    (tmp_path / ".mas").mkdir()
    monkeypatch.setattr(
        cadence, "agent_log_path", lambda: tmp_path / "logs" / "loops.log"
    )
    monkeypatch.setattr(
        notifier, "DEFAULT_WEBHOOK_PATH", str(tmp_path / "cfg" / "hook")
    )
    for name in (*cadence.ENV_SECRETS, *cadence.ENV_POINTERS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", "/Users/x/.secrets/k")
    done = cadence.install_agent(
        tmp_path, executable="/usr/local/bin/avs", load=False, notify=True,
        plist_path=tmp_path / "agent.plist",
    )
    assert done["notify"] is True
    assert any("--set-webhook" in w for w in done["warnings"])


def test_a_saved_webhook_counts_as_configured(tmp_path, monkeypatch):
    """launchd sets HOME, so the saved file is reachable from the scheduled
    run with nothing carried in the plist — warning about it anyway would
    train the operator to scroll past install warnings."""
    from ai_venture_studio import notify as notifier

    (tmp_path / ".mas").mkdir()
    monkeypatch.setattr(
        cadence, "agent_log_path", lambda: tmp_path / "logs" / "loops.log"
    )
    monkeypatch.setattr(
        notifier, "DEFAULT_WEBHOOK_PATH", str(tmp_path / "cfg" / "hook")
    )
    notifier.save_webhook("https://discord.com/api/webhooks/1/abc")
    for name in (*cadence.ENV_SECRETS, *cadence.ENV_POINTERS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", "/Users/x/.secrets/k")
    done = cadence.install_agent(
        tmp_path, executable="/usr/local/bin/avs", load=False, notify=True,
        plist_path=tmp_path / "agent.plist",
    )
    assert done["warnings"] == []


def test_summary_names_the_overdue_loops(tmp_path):
    _compound_proposal(tmp_path, "2026-08-04")
    _sweep_digest(tmp_path, "2026-01-01")
    _attention_log(tmp_path, [
        {"week": "2026-W31", "window": "w", "hours": 1.0, "status": "logged",
         "decided_by": "melody"},
    ])
    report = cadence.assess(tmp_path, today=TODAY)
    assert report.summary() == "1 loop overdue: sweep"


def _completed(argv=None, returncode=0, stdout="", stderr=""):
    import subprocess

    return subprocess.CompletedProcess(
        args=argv or [], returncode=returncode, stdout=stdout, stderr=stderr
    )


# --- the trigger runs a build; is it the build you shipped? -----------------
#
# v0.72.2 published a metering fix while the LaunchAgent went on running
# v0.72.1, because the plist names an absolute path to a *different* install
# from the one that releases. Nothing connected the two, so "published" and
# "deployed" drifted silently — the green-over-stale shape this whole module
# exists to catch.


def _plist_at(tmp_path, binary: str):
    path = tmp_path / "agent.plist"
    path.write_bytes(plistlib.dumps({
        "Label": cadence.LAUNCH_AGENT_LABEL,
        "ProgramArguments": [binary, "cadence", "--repo-dir", "/ws", "--run-due"],
    }))
    return path


def _console_script(tmp_path, interpreter: str = "/usr/bin/python3"):
    script = tmp_path / "avs"
    script.write_text(f"#!{interpreter}\nfrom x import main\n", encoding="utf-8")
    return str(script)


def test_a_scheduler_left_on_the_previous_release_is_a_finding(tmp_path):
    build = cadence.scheduler_build(
        _plist_at(tmp_path, _console_script(tmp_path)),
        running="0.72.2",
        runner=lambda argv: _completed(argv, stdout="0.72.1\n"),
    )
    assert build.scheduled_version == "0.72.1"
    assert build.behind, "publishing moved PyPI and the daily loop kept the old build"
    assert "0.72.1" in build.describe()


def test_a_scheduler_on_the_same_build_is_not_a_finding(tmp_path):
    build = cadence.scheduler_build(
        _plist_at(tmp_path, _console_script(tmp_path)),
        running="0.72.2",
        runner=lambda argv: _completed(argv, stdout="0.72.2\n"),
    )
    assert not build.behind


def test_a_newer_scheduled_build_is_not_reported_as_drift(tmp_path):
    """Running `avs cadence` from a development checkout while the scheduler
    holds the last release is normal. Reporting it would train the operator to
    scroll past the line that matters."""
    build = cadence.scheduler_build(
        _plist_at(tmp_path, _console_script(tmp_path)),
        running="0.72.0",
        runner=lambda argv: _completed(argv, stdout="0.73.0\n"),
    )
    assert not build.behind


def test_no_scheduler_installed_claims_nothing(tmp_path):
    build = cadence.scheduler_build(tmp_path / "absent.plist", running="0.72.2")
    assert not build.installed
    assert not build.behind
    assert build.describe() == "no LaunchAgent installed"


def test_an_unreadable_version_is_reported_not_guessed(tmp_path):
    """An install too broken to import must not read as up to date — that is
    the same silence the check exists to break."""
    build = cadence.scheduler_build(
        _plist_at(tmp_path, _console_script(tmp_path)),
        running="0.72.2",
        runner=lambda argv: _completed(argv, returncode=1, stderr="boom"),
    )
    assert build.scheduled_version == ""
    assert not build.behind
    assert "cannot import" in build.detail
    assert "unreadable" in build.describe()


def test_the_probe_targets_the_interpreter_that_owns_the_script(tmp_path):
    """It must work against builds older than this one, so it cannot assume a
    flag or attribute added later — it asks the script's own interpreter."""
    seen = []

    def _runner(argv):
        seen.append(argv)
        return _completed(argv, stdout="0.71.1\n")

    cadence.scheduler_build(
        _plist_at(tmp_path, _console_script(tmp_path, "/opt/py/bin/python3")),
        running="0.72.2", runner=_runner,
    )
    assert seen[0][0] == "/opt/py/bin/python3"
    assert "importlib.metadata" in seen[0][2], "pip's own record, which cannot drift"


def test_a_plist_pointing_at_a_missing_binary_says_so(tmp_path):
    build = cadence.scheduler_build(
        _plist_at(tmp_path, str(tmp_path / "gone" / "avs")),
        running="0.72.2",
        runner=lambda argv: _completed(argv, stdout="0.1.0\n"),
    )
    assert not build.behind
    assert build.detail, "a binary that is not there must not read as current"


def _healthy_workspace(tmp_path):
    _compound_proposal(tmp_path, "2026-08-05")
    _sweep_digest(tmp_path, "2026-08-05")
    _attention_log(tmp_path, [
        {"week": "2026-W31", "window": "w", "hours": 1.0, "status": "logged",
         "decided_by": "melody"},
    ])
    return tmp_path


def _run_cadence(tmp_path, build, monkeypatch):
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    monkeypatch.setattr(cadence, "scheduler_build", lambda *a, **k: build)
    return CliRunner().invoke(
        app, ["cadence", "--repo-dir", str(_healthy_workspace(tmp_path)),
              "--today", "2026-08-05"]
    )


def test_a_stale_scheduler_build_gates_the_exit_code(tmp_path, monkeypatch):
    """Every loop is on time and the report is still not a pass — the loops
    are being kept by a build that is not the one that was shipped. A yellow
    line alone would be scrolled past; the exit code is what a script reads."""
    result = _run_cadence(tmp_path, cadence.SchedulerBuild(
        plist="/p", installed=True, binary="/opt/bin/avs",
        scheduled_version="0.72.1", running_version="0.72.2",
    ), monkeypatch)
    assert result.exit_code == 3, result.output
    assert "0.72.1" in result.output and "0.72.2" in result.output
    assert "pip" in result.output, "the operator is told the exact fix"


def test_loops_on_time_on_the_current_build_is_a_clean_pass(tmp_path, monkeypatch):
    result = _run_cadence(tmp_path, cadence.SchedulerBuild(
        plist="/p", installed=True, binary="/opt/bin/avs",
        scheduled_version="0.72.2", running_version="0.72.2",
    ), monkeypatch)
    assert result.exit_code == 0, result.output


def test_version_flag_reports_the_installed_build():
    from typer.testing import CliRunner

    from ai_venture_studio import __version__
    from ai_venture_studio.cli import app

    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert __version__ in result.output

# ── an empty window has two opposite causes ──────────────────────────────


def test_an_empty_window_says_the_work_stopped_when_that_is_why(tmp_path):
    """"Nothing to compound" named the symptom and no cause, so the founder
    was left to work out which of two opposite problems they had. Reviews on
    disk, all older than the window, means the loop is doing its job."""
    _proposal_text(tmp_path, "2026-08-04", reviews=0,
                   why="15 review(s) exist, newest 2026-07-24.")
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "compound")

    assert loop.vacuous is True
    assert loop.empty_because == "work_stopped"
    assert "15 on disk" in loop.produced
    assert "2026-07-24" in loop.produced


def test_the_age_of_the_newest_review_is_measured_not_recited(tmp_path):
    """The actionable number is how long ago the work stopped, and it moves
    every day the artifact does not."""
    _proposal_text(tmp_path, "2026-08-04", reviews=0,
                   why="3 review(s) exist, newest 2026-07-24.")
    loop = _loop(cadence.assess(tmp_path, today=dt.date(2026, 8, 4)), "compound")

    assert "11d old" in loop.produced


def test_a_workspace_that_never_produced_a_review_says_so(tmp_path):
    """The other cause, and the one that means the loop is watching the
    wrong directory — an answer nobody can reach from "nothing to read"."""
    _proposal_text(tmp_path, "2026-08-04", reviews=0,
                   why="no review has ever been written here.")
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "compound")

    assert loop.empty_because == "never_any"
    assert "ever been written here" in loop.produced


def test_an_older_artifact_is_not_made_to_confess_a_cause(tmp_path):
    """A proposal written before the run recorded why says nothing about
    why. It stays vacuous — that much is still true — and claims no
    diagnosis it does not have."""
    _proposal_text(tmp_path, "2026-08-04", reviews=0)
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "compound")

    assert loop.vacuous is True
    assert loop.empty_because == ""
    assert "nothing to compound" in loop.produced


def test_a_window_with_reviews_in_it_claims_no_emptiness(tmp_path):
    _proposal_text(tmp_path, "2026-08-04", reviews=4, barren=False)
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "compound")

    assert loop.vacuous is False
    assert loop.empty_because == ""


def test_a_future_dated_review_is_not_reported_as_negative_days(tmp_path):
    """Clock skew, or a hand-written date. Same rule `_classify` already
    follows for a future-dated artifact: it means "recent", not "-3 days"."""
    _proposal_text(tmp_path, "2026-08-04", reviews=0,
                   why="2 review(s) exist, newest 2026-08-20.")
    loop = _loop(cadence.assess(tmp_path, today=dt.date(2026, 8, 4)), "compound")

    assert "0d old" in loop.produced
