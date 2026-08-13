"""The alert that leaves the machine.

The daily agent already knows when a loop needs a person. Until now it said
so into `~/Library/Logs/ai-venture-studio/loops.log`, which is a file nobody
opens — so these tests are mostly about the ways a notifier can be worse than
no notifier: sending nothing and saying it sent, sending the same thing every
morning until it is muted, or posting a private workspace's state to whatever
host an environment variable happened to name.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from ai_venture_studio import notify
from ai_venture_studio.cadence import (
    CadenceReport,
    LoopStatus,
    RunOutcome,
    SchedulerBuild,
)

TODAY = dt.date(2026, 8, 12)


@pytest.fixture(autouse=True)
def _no_real_webhook(tmp_path_factory, monkeypatch):
    """No test may read this machine's saved webhook, or reach the network.

    The default path is real and, once configured, exists — a test that
    silently resolved it would pass here and fail on a fresh checkout, which
    is the least useful direction for a test to be wrong in."""
    monkeypatch.setattr(
        notify, "DEFAULT_WEBHOOK_PATH",
        str(tmp_path_factory.mktemp("config") / "discord-webhook"),
    )


def _report(*loops: LoopStatus, repo_dir: str = "/tmp/avs-studio") -> CadenceReport:
    return CadenceReport(at=TODAY.isoformat(), repo_dir=repo_dir, loops=list(loops))


def _ok(name: str = "sweep") -> LoopStatus:
    return LoopStatus(
        name=name, last_run="2026-08-12", age_days=0, state="ok",
        command=f"avs {name}",
    )


def _overdue(name: str = "compound") -> LoopStatus:
    return LoopStatus(name=name, state="never_run", command=f"avs {name}")


def _empty(because: str = "work_stopped") -> LoopStatus:
    return LoopStatus(
        name="compound", last_run="2026-08-12", age_days=0, state="ok",
        command="avs compound", vacuous=True, empty_because=because,
        produced="read 0 reviews — 9 on disk, newest 2026-08-01 (11d old)",
    )


class _Sent:
    """A stand-in for urlopen that records rather than dials."""

    def __init__(self, status: int = 204):
        self.status, self.calls = status, []

    def __call__(self, request, timeout=None):  # noqa: ARG002
        self.calls.append(request)
        outer = self

        class _Response:
            status = outer.status

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        return _Response()


# ── what is worth saying ────────────────────────────────────────────────


def test_a_healthy_workspace_produces_no_alert():
    """A daily 'all green' is how a channel gets muted, and a muted channel
    is worth less than the log this replaces."""
    assert notify.build_alert(_report(_ok(), _ok("compound"))) is None


def test_an_overdue_loop_names_itself_and_carries_its_command():
    alert = notify.build_alert(_report(_ok(), _overdue()), workspace="avs-studio")
    body = alert.render()

    assert "compound" in body
    assert "`avs compound`" in body
    assert "avs-studio" in body


def test_no_loop_claims_a_person_has_to_close_it():
    """Until v0.81.0 one loop could only be closed by someone typing a number,
    and the alert said so. That loop is gone (ADR-033): every loop here is one
    the machine will retry by itself, and none may claim otherwise."""
    body = notify.build_alert(_report(_overdue("compound"), _overdue("sweep"))).render()

    assert "cannot log this one" not in body


@pytest.mark.parametrize(
    ("because", "said"),
    [
        ("work_stopped", "the work is what paused"),
        ("never_any", "wrong workspace"),
        ("", "Check that work is reaching it"),
    ],
)
def test_an_empty_window_carries_which_kind_of_empty(because, said):
    """The v0.78 diagnosis has to survive the trip to Discord — 'read
    nothing' alone leaves the reader with the same two opposite guesses."""
    alert = notify.build_alert(_report(_ok(), _empty(because)))

    assert said in alert.render()


def test_a_stale_scheduler_is_reported_with_the_line_that_fixes_it():
    build = SchedulerBuild(
        installed=True, binary="/opt/x/bin/avs",
        scheduled_version="0.77.0", running_version="0.78.0",
    )
    alert = notify.build_alert(_report(_ok()), build)
    body = alert.render()

    assert "0.77.0" in body and "0.78.0" in body
    assert "/opt/x/bin/pip install --upgrade ai-venture-studio" in body


def test_a_message_too_long_for_discord_is_cut_and_says_it_was_cut():
    """Discord rejects an over-length message whole, so an uncut alert is a
    silent non-delivery — the failure mode this module exists to end."""
    many = [_overdue(f"loop{i}") for i in range(200)]
    body = notify.build_alert(_report(*many)).render()

    assert len(body) <= notify.MAX_CONTENT
    assert "cut to fit" in body


# ── what actually broke this morning ────────────────────────────────────


def _ran(loop: str, exit_code: int, detail: str) -> RunOutcome:
    return RunOutcome(loop=loop, ran=True, exit_code=exit_code, detail=detail)


def test_a_loop_that_crashed_reaches_the_channel_with_its_output():
    """The whole point of the request: the scheduler already captured the
    traceback and printed it into the log nobody opens."""
    alert = notify.build_alert(
        _report(_ok(), _ok("compound")),
        outcomes=[_ran("compound", 1, "Traceback...\nKeyError: 'slug'")],
    )
    body = alert.render()

    assert "compound" in body and "exit 1" in body
    assert "KeyError: 'slug'" in body


def test_a_failure_alone_is_enough_to_speak():
    """Every loop is on schedule and one of them exploded — before this, that
    combination produced silence in Discord and green in the table."""
    assert notify.build_alert(_report(_ok()), outcomes=[_ran("sweep", 2, "boom")])


def test_a_loop_that_could_not_start_says_so_differently_from_one_that_failed():
    """Different fix: a missing binary is not a broken sweep."""
    alert = notify.build_alert(
        _report(_ok()),
        outcomes=[RunOutcome(
            loop="sweep", ran=False,
            detail="could not run: [Errno 2] No such file or directory: 'avs'",
        )],
    )
    body = alert.render()

    assert "could not be started at all" in body
    assert "No such file or directory" in body


def test_a_loop_that_was_not_due_is_not_an_error():
    outcomes = [RunOutcome(loop="sweep", ran=False, detail="not due (3d of 7d)")]

    assert notify.build_alert(_report(_ok()), outcomes=outcomes) is None


def test_a_successful_run_says_nothing_however_loud_it_was():
    outcomes = [_ran("sweep", 0, "warning: 400 lines of chatter")]

    assert notify.build_alert(_report(_ok()), outcomes=outcomes) is None


def test_a_non_zero_exit_is_a_failure_with_no_exceptions():
    """Until v0.81.0 the `attention` loop exited non-zero every morning by
    design, so it had to be exempted here or the channel would cry wolf daily.
    With that loop withdrawn (ADR-033) the exemption is gone too: any loop the
    scheduler runs and that exits non-zero is broken, full stop."""
    alert = notify.build_alert(
        _report(_overdue("compound")),
        outcomes=[_ran("compound", 3, "boom")],
    )
    body = alert.render()

    assert "1 loop FAILED this run: compound" in alert.heading
    assert "exit 3" in body


def test_the_heading_leads_with_the_breakage_not_the_backlog():
    """The heading is the phone preview. A loop that broke this morning
    outranks a loop that is merely late."""
    alert = notify.build_alert(
        _report(_overdue("compound"), _ok("sweep")),
        outcomes=[_ran("sweep", 1, "boom")],
        workspace="avs-studio",
    )

    assert alert.heading == "**avs-studio** — 1 loop FAILED this run: sweep"


def test_two_broken_loops_are_counted_and_both_named():
    alert = notify.build_alert(
        _report(_ok("sweep"), _ok("compound")),
        outcomes=[_ran("sweep", 1, "a"), _ran("compound", 1, "b")],
    )

    assert "2 loops FAILED" in alert.heading
    assert "sweep, compound" in alert.heading


def test_a_flood_of_backlog_cannot_push_the_error_out_of_the_message():
    """`render` truncates from the end, so failures are written first — an
    error cut to fit is a silent one."""
    body = notify.build_alert(
        _report(_ok("sweep"), *[_overdue(f"loop{i}") for i in range(200)]),
        outcomes=[_ran("sweep", 1, "KeyError: 'slug'")],
    ).render()

    assert len(body) <= notify.MAX_CONTENT
    assert "KeyError: 'slug'" in body
    assert "cut to fit" in body


def test_a_long_traceback_is_cut_from_the_front_where_the_error_is_not():
    detail = "\n".join(f"  File \"x{i}.py\", line {i}" for i in range(300))
    tail = notify.error_tail(detail + "\nValueError: the actual problem")

    assert "ValueError: the actual problem" in tail
    assert len(tail) < len(detail)
    assert tail.startswith("```") and tail.endswith("```")


def test_a_failure_with_nothing_to_say_still_says_that():
    """An empty detail rendering as an empty code fence reads like a display
    bug; it is a real and reportable state."""
    assert "said nothing" in notify.error_tail("   \n\n  ")


# ── not the same thing every morning ────────────────────────────────────


def test_the_same_alert_is_not_sent_twice_in_a_row(tmp_path):
    report, sender = _report(_ok(), _overdue()), _Sent()
    env = {notify.WEBHOOK_ENV: "https://discord.com/api/webhooks/1/abc"}

    first = notify.notify(tmp_path, report, today=TODAY, environ=env, opener=sender)
    second = notify.notify(tmp_path, report, today=TODAY, environ=env, opener=sender)

    assert first.sent and not second.sent
    assert "already sent" in second.reason
    assert len(sender.calls) == 1


def test_an_unchanged_alert_says_itself_again_after_the_repeat_window(tmp_path):
    report, sender = _report(_ok(), _overdue()), _Sent()
    env = {notify.WEBHOOK_ENV: "https://discord.com/api/webhooks/1/abc"}

    notify.notify(tmp_path, report, today=TODAY, environ=env, opener=sender)
    later = notify.notify(
        tmp_path, report,
        today=TODAY + dt.timedelta(days=notify.REPEAT_DAYS),
        environ=env, opener=sender,
    )

    assert later.sent
    assert len(sender.calls) == 2


def test_new_news_is_never_held_back(tmp_path):
    """Delay is for repetition. A *changed* alert waiting six days would make
    the channel less trustworthy than the log it replaces."""
    sender = _Sent()
    env = {notify.WEBHOOK_ENV: "https://discord.com/api/webhooks/1/abc"}

    notify.notify(
        tmp_path, _report(_ok(), _overdue()), today=TODAY, environ=env, opener=sender
    )
    changed = notify.notify(
        tmp_path, _report(_overdue(), _empty()),
        today=TODAY + dt.timedelta(days=1), environ=env, opener=sender,
    )

    assert changed.sent
    assert len(sender.calls) == 2


def test_force_sends_a_repeat_so_the_wiring_can_be_checked(tmp_path):
    sender = _Sent()
    env = {notify.WEBHOOK_ENV: "https://discord.com/api/webhooks/1/abc"}
    report = _report(_ok(), _overdue())

    notify.notify(tmp_path, report, today=TODAY, environ=env, opener=sender)
    forced = notify.notify(
        tmp_path, report, today=TODAY, environ=env, opener=sender, force=True
    )

    assert forced.sent
    assert len(sender.calls) == 2


def test_a_quiet_workspace_needs_no_webhook_at_all(tmp_path):
    """Nothing to say must not be reported as a configuration error, or every
    healthy day looks like a broken notifier."""
    result = notify.notify(tmp_path, _report(_ok()), today=TODAY, environ={})

    assert not result.sent
    assert "nothing needs a person" in result.reason


# ── where it is allowed to go ───────────────────────────────────────────


def test_a_missing_webhook_names_the_command_that_fixes_it(tmp_path):
    with pytest.raises(notify.NotifyError) as caught:
        notify.notify(tmp_path, _report(_overdue()), today=TODAY, environ={})

    assert "--set-webhook" in str(caught.value)
    assert notify.WEBHOOK_FILE_ENV in str(caught.value)


def test_a_saved_webhook_needs_no_environment_at_all(tmp_path, monkeypatch):
    """launchd hands a job four environment variables and no login shell. If
    the only way to configure this were an export, the daily run — the one
    that matters — would be the one that never finds it."""
    monkeypatch.setattr(
        notify, "DEFAULT_WEBHOOK_PATH", str(tmp_path / "cfg" / "hook")
    )
    notify.save_webhook("https://discord.com/api/webhooks/1/saved")

    assert notify.resolve_webhook({}).endswith("saved")


def test_an_explicit_pointer_still_wins_over_the_saved_one(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notify, "DEFAULT_WEBHOOK_PATH", str(tmp_path / "cfg" / "hook")
    )
    notify.save_webhook("https://discord.com/api/webhooks/1/saved")
    elsewhere = tmp_path / "other"
    elsewhere.write_text("https://discord.com/api/webhooks/2/pointed", "utf-8")

    resolved = notify.resolve_webhook({notify.WEBHOOK_FILE_ENV: str(elsewhere)})

    assert resolved.endswith("pointed")


def test_a_saved_webhook_is_readable_only_by_its_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notify, "DEFAULT_WEBHOOK_PATH", str(tmp_path / "cfg" / "hook")
    )
    saved = notify.save_webhook("https://discord.com/api/webhooks/1/abc")

    assert saved.stat().st_mode & 0o077 == 0


def test_a_bad_url_is_refused_before_it_reaches_disk(tmp_path, monkeypatch):
    """Saved unvalidated, it would sit there looking configured and fail once
    a week at 09:00 — into the log this feature exists to stop using."""
    monkeypatch.setattr(
        notify, "DEFAULT_WEBHOOK_PATH", str(tmp_path / "cfg" / "hook")
    )
    with pytest.raises(notify.NotifyError):
        notify.save_webhook("https://example.com/api/webhooks/1/abc")

    assert not (tmp_path / "cfg" / "hook").exists()


def test_the_url_is_read_from_the_file_the_pointer_names(tmp_path):
    hook = tmp_path / "webhook.txt"
    hook.write_text("https://discord.com/api/webhooks/9/xyz\n", encoding="utf-8")

    assert notify.resolve_webhook({notify.WEBHOOK_FILE_ENV: str(hook)}).endswith("xyz")


@pytest.mark.parametrize("body", ["", "   \n"])
def test_a_pointer_at_an_empty_file_is_an_error_not_an_empty_url(tmp_path, body):
    hook = tmp_path / "webhook.txt"
    hook.write_text(body, encoding="utf-8")

    with pytest.raises(notify.NotifyError):
        notify.resolve_webhook({notify.WEBHOOK_FILE_ENV: str(hook)})


def test_a_pointer_at_a_missing_file_says_which_path(tmp_path):
    missing = str(tmp_path / "nope.txt")

    with pytest.raises(notify.NotifyError) as caught:
        notify.resolve_webhook({notify.WEBHOOK_FILE_ENV: missing})

    assert missing in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/api/webhooks/1/abc",
        "http://discord.com/api/webhooks/1/abc",
        "https://discord.com.evil.test/api/webhooks/1/abc",
        "file:///etc/passwd",
    ],
)
def test_the_alert_goes_to_discord_or_nowhere(url):
    """The URL arrives from an environment variable. Without this check a
    typo or a tampered profile POSTs a private workspace's state to a
    stranger, and the run still reports success."""
    with pytest.raises(notify.NotifyError):
        notify.check_url(url)


def test_a_refused_host_is_never_dialled():
    sender = _Sent()

    with pytest.raises(notify.NotifyError):
        notify.deliver("https://example.com/hook", "hi", opener=sender)

    assert sender.calls == []


# ── the delivery itself ─────────────────────────────────────────────────


def test_the_message_is_posted_as_discord_expects_it():
    sender = _Sent()

    notify.deliver(
        "https://discord.com/api/webhooks/1/abc", "hello", opener=sender
    )

    request = sender.calls[0]
    assert request.method == "POST"
    assert json.loads(request.data.decode("utf-8")) == {"content": "hello"}
    assert request.headers["Content-type"] == "application/json"


def test_discord_refusing_the_message_is_an_error_not_a_traceback():
    import urllib.error

    def refuse(request, timeout=None):  # noqa: ARG001
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)

    with pytest.raises(notify.NotifyError) as caught:
        notify.deliver("https://discord.com/api/webhooks/1/a", "x", opener=refuse)

    assert "429" in str(caught.value)


def test_an_unreachable_webhook_does_not_take_the_run_down():
    import urllib.error

    def drop(request, timeout=None):  # noqa: ARG001
        raise urllib.error.URLError("no route")

    with pytest.raises(notify.NotifyError):
        notify.deliver("https://discord.com/api/webhooks/1/a", "x", opener=drop)


def test_a_failed_delivery_is_not_recorded_as_sent(tmp_path):
    """Otherwise the dedupe suppresses tomorrow's retry of an alert that
    never arrived — silence, remembered as success."""
    import urllib.error

    def drop(request, timeout=None):  # noqa: ARG001
        raise urllib.error.URLError("no route")

    env = {notify.WEBHOOK_ENV: "https://discord.com/api/webhooks/1/abc"}
    with pytest.raises(notify.NotifyError):
        notify.notify(
            tmp_path, _report(_overdue()), today=TODAY, environ=env, opener=drop
        )

    assert notify.load_sent(tmp_path) == {}


def test_the_alert_never_carries_the_webhook_url(tmp_path):
    """The message is posted into a channel other people may read."""
    url = "https://discord.com/api/webhooks/1/s3cr3t-token-value"
    sender = _Sent()

    result = notify.notify(
        tmp_path, _report(_overdue()), today=TODAY,
        environ={notify.WEBHOOK_ENV: url}, opener=sender,
    )

    assert "s3cr3t" not in result.content
    assert "s3cr3t" not in (tmp_path / notify.SENT_LOG).read_text(encoding="utf-8")


def test_an_unreadable_send_log_reads_as_never_sent(tmp_path):
    path = tmp_path / notify.SENT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{[not yaml", encoding="utf-8")

    assert notify.load_sent(tmp_path) == {}


# ── through the command the scheduler actually runs ─────────────────────


def _cadence(tmp_path, monkeypatch, *flags, sent=None):
    """`avs cadence` over a workspace whose loops have never run."""
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    (tmp_path / ".mas").mkdir(exist_ok=True)
    monkeypatch.setattr(notify, "deliver", sent or (lambda *a, **k: 204))
    monkeypatch.setenv(notify.WEBHOOK_ENV, "https://discord.com/api/webhooks/1/a")
    monkeypatch.delenv(notify.WEBHOOK_FILE_ENV, raising=False)
    return CliRunner().invoke(
        app,
        ["cadence", "--repo-dir", str(tmp_path), "--today", "2026-08-12", *flags],
    )


def test_without_the_flag_nothing_leaves_the_machine(tmp_path, monkeypatch):
    posts = []
    result = _cadence(
        tmp_path, monkeypatch, sent=lambda *a, **k: posts.append(a)
    )

    assert result.exit_code == 3, result.output  # loops are overdue, as before
    assert posts == []


def test_the_flag_sends_and_the_run_says_it_sent(tmp_path, monkeypatch):
    posts = []
    result = _cadence(
        tmp_path, monkeypatch, "--notify", sent=lambda *a, **k: posts.append(a)
    )

    assert len(posts) == 1
    assert "alert sent" in result.output
    assert result.exit_code == 3, "notifying is not fixing — the gate still fails"


def test_a_loop_that_fails_under_the_scheduler_is_what_gets_posted(
    tmp_path, monkeypatch
):
    """End to end over the exact command the LaunchAgent runs: `run_due`
    caught the exit code, and until now only the log ever saw it."""
    from ai_venture_studio import cadence

    posts = []
    monkeypatch.setattr(cadence, "run_due", lambda *a, **k: [
        RunOutcome(loop="compound", ran=True, exit_code=1,
                   detail="Traceback...\nKeyError: 'slug'"),
    ])
    result = _cadence(
        tmp_path, monkeypatch, "--run-due", "--notify",
        sent=lambda url, content, **k: posts.append(content),
    )

    assert result.exit_code == 3, result.output
    assert len(posts) == 1
    assert "compound" in posts[0] and "KeyError: 'slug'" in posts[0]


def test_setting_the_webhook_never_echoes_it(tmp_path, monkeypatch):
    """It scrolls back in a terminal, and whoever reads it can post into the
    channel as this app."""
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    monkeypatch.setattr(
        notify, "DEFAULT_WEBHOOK_PATH", str(tmp_path / "cfg" / "hook")
    )
    url = "https://discord.com/api/webhooks/1/t0k3n-here"
    result = CliRunner().invoke(app, ["cadence", "--set-webhook", url])

    assert result.exit_code == 0, result.output
    assert "t0k3n" not in result.output
    assert notify.resolve_webhook({}) == url


def test_setting_a_webhook_that_is_not_discord_fails_loudly(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    monkeypatch.setattr(
        notify, "DEFAULT_WEBHOOK_PATH", str(tmp_path / "cfg" / "hook")
    )
    result = CliRunner().invoke(
        app, ["cadence", "--set-webhook", "https://example.com/hook"]
    )

    assert result.exit_code == 2, result.output


def test_a_webhook_that_fails_is_reported_and_does_not_mask_the_gate(
    tmp_path, monkeypatch
):
    """The run must not exit 0 because the alert broke, and must not exit 3
    quietly as if the alert had gone out."""
    def refuse(*_a, **_k):
        raise notify.NotifyError("Discord refused the message: HTTP 401")

    result = _cadence(tmp_path, monkeypatch, "--notify", sent=refuse)

    assert "alert NOT sent" in result.output
    assert "401" in result.output
    assert result.exit_code == 3
