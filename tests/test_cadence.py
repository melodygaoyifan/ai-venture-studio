"""The loops' watchdog. What it pins, in one line: a loop that never ran must
never read as fresh — the only way a watchdog can actually lie."""

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
    assert len(report.stale) == 2


def test_fresh_loops_are_ok(tmp_path):
    _compound_proposal(tmp_path, "2026-08-03")
    _sweep_digest(tmp_path, "2026-08-01")
    report = cadence.assess(tmp_path, today=TODAY)
    assert _loop(report, "compound").state == "ok"
    assert _loop(report, "compound").age_days == 2
    assert _loop(report, "sweep").state == "ok"
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


def test_run_due_skips_fresh_loops(tmp_path, monkeypatch):
    """What makes a daily trigger safe against weekly work."""
    _compound_proposal(tmp_path, "2026-08-04")
    _sweep_digest(tmp_path, "2026-08-04")
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
    assert ran == {"sweep"}
    # The command spells its workspace option the way that command spells it.
    by_name = {argv[1]: argv for argv in calls}
    assert by_name["sweep"][2] == "--workspace"


def test_every_loop_the_scheduler_drives_can_close_itself(tmp_path, monkeypatch):
    """The shape a scheduler should have. Until v0.81.0 one loop (`attention`)
    could only be closed by a person typing a number, so it exited non-zero
    every morning by design and had to be exempted from the error channel.
    Nothing here is exempt now, and this pins that no such loop creeps back."""
    monkeypatch.setattr(
        cadence.subprocess, "run", lambda argv, **kw: _completed(argv=argv)
    )
    outcomes = cadence.run_due(tmp_path, today=TODAY, executable="/usr/local/bin/avs")
    assert outcomes, "an empty workspace has every loop due"
    assert all(o.ran for o in outcomes)
    assert all(o.exit_code == 0 for o in outcomes)


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
        cadence, "agent_log_path", lambda label=None: tmp_path / "logs" / "loops.log"
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
        cadence, "agent_log_path", lambda label=None: tmp_path / "logs" / "loops.log"
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
        cadence, "agent_log_path", lambda label=None: tmp_path / "logs" / "loops.log"
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
        cadence, "agent_log_path", lambda label=None: tmp_path / "logs" / "loops.log"
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


# --- the bench: the only kill criterion left, and nobody was watching it ----
#
# v0.81.0 withdrew the maintenance-attention axis (ADR-033), leaving the
# product-bench series as the launch PRD's sole kill criterion. That series
# was collected by a cron job that had not fired since 2026-07-27 — sixteen
# days, three scheduled Mondays, no result and no complaint. `avs cadence`
# watched compound and sweep and did not watch the one series a criterion
# actually reads. These pin the loop that closes that gap.


def _bench_cases(root):
    (root / "benchmarks" / "products-real").mkdir(parents=True, exist_ok=True)


def _bench_result(root, date: str, *, build=0.74, probes=0.75):
    directory = root / "benchmarks" / "results"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"result-{date}-0449.yaml").write_text(
        yaml.safe_dump({"build_rate": build, "probe_pass_rate": probes}),
        encoding="utf-8",
    )


def test_a_workspace_without_the_cases_is_not_told_it_owes_a_bench(tmp_path):
    """The bench measures the *framework*, not a product. Reporting it as
    never_run in every workspace that cannot run it would put a standing
    false alarm in the one channel that must not cry wolf."""
    report = cadence.assess(tmp_path, today=TODAY)
    assert [loop.name for loop in report.loops] == ["compound", "sweep"]


def test_the_bench_series_going_quiet_is_now_a_finding(tmp_path):
    """The actual 2026 incident, as a test: cases present, last result 16
    days old, and until v0.82.0 nothing said a word."""
    _bench_cases(tmp_path)
    _bench_result(tmp_path, "2026-07-27")
    loop = _loop(cadence.assess(tmp_path, today=dt.date(2026, 8, 12)), "bench")

    assert loop.state == "overdue"
    assert loop.age_days == 16
    assert loop.is_stale is True
    assert loop in cadence.assess(tmp_path, today=dt.date(2026, 8, 12)).stale


def test_a_bench_that_never_ran_is_never_run_not_fresh(tmp_path):
    _bench_cases(tmp_path)
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "bench")
    assert loop.state == "never_run"
    assert loop.last_run == ""


def test_the_bench_carries_the_rates_it_read_without_judging_them(tmp_path):
    """Rule 1: it states, it does not decide. Whether 40% fires the criterion
    is `bench_criterion.evaluate`'s call, and it is not made here."""
    _bench_cases(tmp_path)
    _bench_result(tmp_path, "2026-08-04", build=0.40, probes=0.30)
    loop = _loop(cadence.assess(tmp_path, today=TODAY), "bench")

    assert loop.produced == "build 40%, probes 30%"
    assert loop.state == "ok"
    assert loop.vacuous is False


def test_the_bench_runs_the_real_cases_not_the_synthetic_default(tmp_path, monkeypatch):
    """`product-bench` defaults to benchmarks/products. The criterion is
    defined over benchmarks/products-real, so a scheduled run that took the
    default would keep the series alive with the wrong series."""
    _bench_cases(tmp_path)
    calls = []
    monkeypatch.setattr(
        cadence.subprocess, "run",
        lambda argv, **kw: calls.append((argv, kw)) or _completed(),
    )
    outcomes = cadence.run_due(tmp_path, today=TODAY, executable="/usr/local/bin/avs")

    assert {o.loop for o in outcomes if o.ran} >= {"bench"}
    argv = next(a for a, _ in calls if a[1] == "product-bench")
    assert argv[2] == "--cases-dir"
    assert argv[3].endswith("benchmarks/products-real")


def test_the_bench_is_given_the_hours_it_actually_takes(tmp_path, monkeypatch):
    """Run 11 took 74 minutes of wall clock. A one-hour ceiling would have
    killed it at the three-quarter mark and reported the timeout as a
    capability failure — a lie in the direction that costs the most."""
    _bench_cases(tmp_path)
    _compound_proposal(tmp_path, "2026-06-01")
    calls = []
    monkeypatch.setattr(
        cadence.subprocess, "run",
        lambda argv, **kw: calls.append((argv, kw)) or _completed(),
    )
    cadence.run_due(tmp_path, today=TODAY, executable="/usr/local/bin/avs")

    timeouts = {a[1]: kw["timeout"] for a, kw in calls}
    assert timeouts["product-bench"] == cadence.BENCH_TIMEOUT_S
    assert timeouts["product-bench"] > 3600
    assert timeouts["compound"] == cadence.DEFAULT_TIMEOUT_S


def test_the_bench_closes_itself_like_every_other_loop(tmp_path, monkeypatch):
    """ADR-033's rule, applied to the loop added after it: the bench is a
    paid hour-long run, but it is a run, not a question. Nothing the
    scheduler drives may need a person to type something."""
    _bench_cases(tmp_path)
    monkeypatch.setattr(
        cadence.subprocess, "run", lambda argv, **kw: _completed(argv=argv)
    )
    outcomes = cadence.run_due(tmp_path, today=TODAY, executable="/usr/local/bin/avs")

    assert {o.loop for o in outcomes} == {"compound", "sweep", "bench"}
    assert all(o.ran and o.exit_code == 0 for o in outcomes)


# --- one scheduler per workspace, and a filter that refuses to select nothing


def test_only_restricts_the_report_to_the_named_loop(tmp_path):
    _bench_cases(tmp_path)
    report = cadence.assess(tmp_path, today=TODAY, only=["bench"])
    assert [loop.name for loop in report.loops] == ["bench"]


def test_a_misspelled_loop_is_refused_not_silently_ignored(tmp_path):
    """A filter that matches nothing gives a scheduler that watches nothing
    and reports all clear every morning — the exact lie this module exists
    to prevent, arrived at through a typo."""
    _bench_cases(tmp_path)
    with pytest.raises(cadence.CadenceError) as caught:
        cadence.assess(tmp_path, today=TODAY, only=["bnech"])
    assert "bnech" in str(caught.value)
    assert "compound, sweep, bench" in str(caught.value)


def test_asking_for_a_loop_this_workspace_lacks_is_an_error(tmp_path):
    """Not an empty report. A product workspace cannot run the bench, and a
    scheduler installed there pointing at it must fail loudly on the first
    morning rather than report a clean sheet forever."""
    with pytest.raises(cadence.CadenceError) as caught:
        cadence.assess(tmp_path, today=TODAY, only=["bench"])
    assert "not tracked" in str(caught.value)
    assert "products-real" in str(caught.value)


def test_a_filtered_run_only_runs_the_loop_it_was_given(tmp_path, monkeypatch):
    _bench_cases(tmp_path)
    calls = []
    monkeypatch.setattr(
        cadence.subprocess, "run",
        lambda argv, **kw: calls.append(argv) or _completed(),
    )
    outcomes = cadence.run_due(
        tmp_path, today=TODAY, executable="/usr/local/bin/avs", only=["bench"]
    )
    assert [o.loop for o in outcomes] == ["bench"]
    assert [argv[1] for argv in calls] == ["product-bench"]


def test_a_second_workspace_gets_its_own_label_and_log(tmp_path):
    """One label is one scheduled job. Installing a second workspace under
    the shared label would silently retarget the first, and the loss would
    surface only as nothing running."""
    body = plistlib.loads(cadence.render_plist(
        tmp_path, executable="/usr/local/bin/avs", hour=9, minute=7,
        only=["bench"], label="ai.venture.studio.bench", env={},
    ))
    assert body["Label"] == "ai.venture.studio.bench"
    assert body["ProgramArguments"][-2:] == ["--only", "bench"]
    assert cadence.agent_plist_path("ai.venture.studio.bench").name == (
        "ai.venture.studio.bench.plist"
    )
    assert cadence.agent_log_path("ai.venture.studio.bench").name == "bench.log"


def test_the_default_agent_keeps_the_name_already_installed(tmp_path):
    """`loops.log` and the unsuffixed label are named in a plist already on
    the operator's machine; renaming them here would point the running job
    at a file nothing refers to."""
    assert cadence.agent_plist_path().name == f"{cadence.LAUNCH_AGENT_LABEL}.plist"
    assert cadence.agent_log_path().name == "loops.log"
    body = plistlib.loads(cadence.render_plist(
        tmp_path, executable="/usr/local/bin/avs", env={}
    ))
    assert "--only" not in body["ProgramArguments"]


def test_a_label_that_could_escape_its_directory_is_refused():
    """The label becomes a filename under ~/Library/LaunchAgents and a
    launchd service name, so it is checked rather than trusted."""
    for bad in ("../../etc/cron", "two words", "/absolute", ".leading-dot"):
        with pytest.raises(cadence.CadenceError):
            cadence.agent_plist_path(bad)
    # Blank is not an attack, it is "unspecified" — it takes the default.
    assert cadence.agent_plist_path("   ") == cadence.agent_plist_path()


def test_a_partial_bench_says_so_in_the_line_the_reader_sees(tmp_path):
    """The rates go to Discord as one line. A run that could not measure a
    case must not read like a run that measured them all."""
    _bench_cases(tmp_path)
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "result-2026-08-13-0347.yaml").write_text(
        "build_rate: 1.0\nprobe_pass_rate: 0.87\n"
        "rates:\n  cases_measured: 3\n  cases_total: 4\n",
        encoding="utf-8",
    )
    status = cadence.assess(tmp_path, today=dt.date(2026, 8, 13), only=["bench"]).loops[0]
    assert "over 3 of 4 cases" in status.produced


def test_a_complete_bench_does_not_carry_a_scope_note(tmp_path):
    _bench_cases(tmp_path)
    _bench_result(tmp_path, "2026-08-13")
    status = cadence.assess(tmp_path, today=dt.date(2026, 8, 13), only=["bench"]).loops[0]
    assert "of" not in status.produced.replace("probes", "")


# --- when does it run again? --------------------------------------------
#
# A row reading `last run 2026-08-14 | cadence 7d | ok (1d)` holds the
# answer only as a sum the reader has to perform, and the scheduler that
# fires every morning invites the wrong one: "daily wake-up, so tomorrow".
# The wake-up is daily and the cadence is weekly, and the row showed the
# consequence of neither.


def test_a_loop_within_cadence_says_when_it_next_comes_due():
    status = cadence.LoopStatus(
        name="bench", last_run="2026-08-14", age_days=1, state="ok"
    )
    assert status.next_due == "2026-08-21"
    assert "next 2026-08-21" in status.describe()


def test_the_next_due_date_follows_the_loop_s_own_cadence():
    """Not every loop is weekly; the date is arithmetic on its own period."""
    fortnightly = cadence.LoopStatus(
        name="bench", last_run="2026-08-14", cadence_days=14, age_days=1, state="ok"
    )
    assert fortnightly.next_due == "2026-08-28"


def test_an_empty_window_still_says_when_it_next_comes_due():
    status = cadence.LoopStatus(
        name="compound", last_run="2026-08-12", age_days=3, state="ok", vacuous=True
    )
    assert status.describe() == "ok, empty (3d, next 2026-08-19)"


def test_a_loop_that_needs_running_is_not_given_a_date_instead():
    """DUE already says what to do; a date beside it competes with the
    instruction rather than adding to it."""
    for state in ("due", "overdue"):
        status = cadence.LoopStatus(
            name="bench", last_run="2026-08-01", age_days=14, state=state
        )
        assert status.describe() == f"{state.upper()} (14d)"


def test_a_loop_that_never_ran_states_no_date():
    status = cadence.LoopStatus(name="sweep", state="never_run")
    assert status.next_due == ""
    assert status.describe() == "never run"


def test_an_unparseable_last_run_yields_no_date_rather_than_a_guess():
    status = cadence.LoopStatus(
        name="bench", last_run="last Tuesday", age_days=1, state="ok"
    )
    assert status.next_due == ""
    assert status.describe() == "ok (1d)"


def test_the_date_a_real_assessment_reports_is_the_one_a_reader_can_check(tmp_path):
    _bench_cases(tmp_path)
    _bench_result(tmp_path, "2026-08-14")
    status = cadence.assess(
        tmp_path, today=dt.date(2026, 8, 15), only=["bench"]
    ).loops[0]
    assert status.state == "ok"
    assert status.next_due == "2026-08-21"
