"""Push the cadence alert to where a human already is.

The daily LaunchAgent runs `avs cadence --run-due`, exits 3 when a loop
needs a person, and writes the reason to
`~/Library/Logs/ai-venture-studio/loops.log` — a file nobody opens. That is
the same silent-success shape the loops exist to prevent, one level up: the
machine noticed, told nothing that listens, and the week passed.

So the alert goes out over a Discord webhook. Three rules shape it:

- **Only when something needs a person.** A daily "all green" trains the
  reader to swipe the notification away, and then the one that mattered gets
  swiped too. Two things qualify: a loop that is *late*, and a loop that
  *broke* — `run_due` already captured the exit code and the tail of stderr,
  and for one release the only place either landed was that same unread log.
- **Not the same thing every morning.** An overdue weekly loop stays overdue
  until someone acts; repeating it at 09:00 for six days is how a channel
  becomes noise. The same alert re-sends at most every `REPEAT_DAYS`, and a
  *changed* alert goes out immediately — new news is never delayed.
- **Carry the command, not the diagnosis.** The reader is on a phone. Every
  line either says what is true or gives them something to paste.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field

#: Points at a FILE holding the webhook URL. A webhook URL is a credential —
#: anyone holding it can post into the channel as this app — so it follows the
#: `*_KEY_FILE` rule the LaunchAgent already enforces: pointers travel into the
#: plist (a world-readable file in ~/Library/LaunchAgents), secrets never do.
WEBHOOK_FILE_ENV = "AVS_DISCORD_WEBHOOK_FILE"
#: The URL inline. Works for an interactive run and is deliberately REFUSED
#: into the plist by name, so nobody learns the habit from the scheduler.
WEBHOOK_ENV = "AVS_DISCORD_WEBHOOK"

#: Where `--set-webhook` puts the URL when nobody says otherwise. Having a
#: default at all is the point: the alternative setup is "write the URL to a
#: file, chmod it, export a variable in your shell profile, re-run install" —
#: four steps of typing to receive one message a week, which is how a feature
#: ends up installed by nobody.
DEFAULT_WEBHOOK_PATH = "~/.config/ai-venture-studio/discord-webhook"

#: Where the last delivery is remembered, so the same news is not re-sent
#: every morning. In the workspace, because the cadence being reported is
#: this workspace's.
SENT_LOG = ".mas/alerts-sent.yaml"

#: How long an unchanged alert waits before it is worth saying again.
REPEAT_DAYS = 7

#: Discord's per-message ceiling. A message over it is rejected whole, so the
#: alert is cut here instead — with a line saying it was cut.
MAX_CONTENT = 2000

#: The only hosts a webhook may point at. Not paranoia about Discord: this
#: URL comes from an environment variable, and without the check a typo or a
#: tampered profile would quietly POST the workspace's state to a stranger.
ALLOWED_HOSTS = (
    "discord.com",
    "discordapp.com",
    "ptb.discord.com",
    "canary.discord.com",
)


class NotifyError(Exception):
    """The alert could not be delivered — never a loop being overdue."""


class Alert(BaseModel):
    """What to say, and enough to decide whether it has been said before."""

    heading: str = ""
    lines: list[str] = Field(default_factory=list)

    def render(self) -> str:
        body = "\n".join([self.heading, *self.lines]).strip()
        if len(body) <= MAX_CONTENT:
            return body
        keep = MAX_CONTENT - len(_CUT_NOTE) - 1
        return body[:keep].rstrip() + "\n" + _CUT_NOTE

    @property
    def digest(self) -> str:
        """Identity by CONTENT, not by date.

        The heading carries the workspace and the body carries what is wrong;
        neither carries today's date, so tomorrow's identical alert hashes the
        same and is recognised as already said.
        """
        return hashlib.sha256(
            "\n".join([self.heading, *self.lines]).encode("utf-8")
        ).hexdigest()[:16]


_CUT_NOTE = "_(cut to fit one message — run `avs cadence` for the rest)_"

#: How much of a failing loop's output to carry. The end, not the beginning:
#: a traceback puts the exception on its last line and the call stack above
#: it, so the front is the part you can afford to lose.
ERROR_TAIL = 500


def error_tail(text: str, limit: int = ERROR_TAIL) -> str:
    """The last of a failed run's output, fenced for Discord.

    Errors are the one part of an alert that must not be paraphrased — a
    summarised traceback is a traceback you have to go and read anyway,
    which puts the log back in the loop this exists to cut out.
    """
    body = "\n".join(
        line.rstrip() for line in str(text).splitlines() if line.strip()
    ).strip()
    if not body:
        return "```(the run failed and said nothing)```"
    if len(body) > limit:
        body = "…" + body[-limit:]
    return f"```\n{body}\n```"


def build_alert(
    report, build=None, *, workspace: str = "", outcomes=None
) -> Alert | None:
    """The alert for this cadence report, or None when nobody is needed.

    `report` is a `cadence.CadenceReport`, `build` a `cadence.SchedulerBuild`,
    `outcomes` the `RunOutcome`s from this morning's `--run-due` if there was
    one. Typed loosely on purpose: this module is downstream of cadence and
    importing it back would close a cycle for a handful of attribute reads.
    """
    where = workspace or pathlib.Path(str(getattr(report, "repo_dir", ""))).name
    stale = list(getattr(report, "stale", []))
    vacuous = list(getattr(report, "vacuous", []))
    behind = bool(build is not None and getattr(build, "behind", False))
    failed = _failures(report, outcomes)
    if not (failed or stale or vacuous or behind):
        return None

    lines: list[str] = []
    # Failures first, and not only for emphasis: `render` truncates from the
    # end, so anything below this can be cut to fit and an error cannot.
    for name, said in failed:
        lines.append(f"**{name}** {said[0]}")
        lines.append(said[1])

    for loop in stale:
        state = "has never run" if loop.state == "never_run" else "is overdue"
        lines.append(f"**{loop.name}** {state}.")
        if loop.human_input_required:
            # The distinction worth making on a phone: this one will not fix
            # itself tonight, because no machine can answer it.
            lines.append(
                "  Needs your number — the scheduler cannot log this one for you."
            )
        lines.append(f"  `{loop.command}`")

    for loop in vacuous:
        why = {
            "never_any": (
                "No review has ever been written here — the loop is almost "
                "certainly pointed at the wrong workspace."
            ),
            "work_stopped": (
                "Reviews exist but all of them are older than the window — "
                "the loop is fine; the work is what paused."
            ),
        }.get(loop.empty_because, "Check that work is reaching it.")
        lines.append(f"**{loop.name}** kept its cadence over an empty window.")
        lines.append(f"  {why}")

    if behind:
        lines.append(
            f"**scheduler** is running v{build.scheduled_version} while the "
            f"latest here is v{build.running_version}."
        )
        lines.append(
            f"  `{str(build.binary).replace('/avs', '/pip')} install "
            f"--upgrade ai-venture-studio`"
        )

    # The heading is the notification preview — the whole message, for
    # anyone who does not open it. A loop that broke this morning outranks a
    # loop that is merely late, so it takes that line when there is one.
    if failed:
        names = ", ".join(name for name, _ in failed)
        noun = "loop" if len(failed) == 1 else "loops"
        headline = f"{len(failed)} {noun} FAILED this run: {names}"
    else:
        headline = report.summary()
    return Alert(heading=f"**{where or 'workspace'}** — {headline}", lines=lines)


def _failures(report, outcomes) -> list[tuple[str, tuple[str, str]]]:
    """The loops this run tried and did not get through.

    Three things are deliberately not failures. A loop that was **not due**
    did not run and that is the design. A loop needing a human exits non-zero
    *by definition* — `attention` surfaces its ask that way every single day,
    and calling that an error would make the channel cry wolf daily about the
    one loop that is behaving exactly as specified; it is already reported as
    overdue, which is the true statement. And a run that succeeded says
    nothing here even if its output was noisy.
    """
    human = {
        loop.name for loop in getattr(report, "loops", [])
        if getattr(loop, "human_input_required", False)
    }
    found: list[tuple[str, tuple[str, str]]] = []
    for outcome in outcomes or []:
        if outcome.loop in human:
            continue
        if not outcome.ran:
            if str(outcome.detail).startswith("not due"):
                continue
            # Could not be started at all: a missing binary, a bad path, a
            # timeout. Distinct from failing, and a different fix.
            found.append((outcome.loop, (
                "could not be started at all.", error_tail(outcome.detail)
            )))
        elif outcome.exit_code:
            found.append((outcome.loop, (
                f"ran and failed (exit {outcome.exit_code}).",
                error_tail(outcome.detail),
            )))
    return found


def default_webhook_path() -> pathlib.Path:
    return pathlib.Path(DEFAULT_WEBHOOK_PATH).expanduser()


def save_webhook(url: str, path: pathlib.Path | None = None) -> pathlib.Path:
    """Store the URL where `resolve_webhook` will find it without being told.

    Checked before it is written: a URL saved unvalidated would sit on disk
    looking configured and fail once a week, at 09:00, into the log this
    whole feature exists to stop using. Written 0600 — it is a credential,
    and `~/.config` is not private by default.
    """
    check_url(url.strip())
    target = pathlib.Path(path) if path is not None else default_webhook_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(url.strip() + "\n", encoding="utf-8")
    target.chmod(0o600)
    return target


def resolve_webhook(environ: dict | None = None) -> str:
    """The webhook URL: the pointer file, the inline variable, or the default.

    Raises `NotifyError` naming what to run when none of the three is there,
    because the alternative is a scheduler that silently notifies nobody —
    the exact failure this module exists to end.
    """
    import os

    source = os.environ if environ is None else environ
    path = str(source.get(WEBHOOK_FILE_ENV, "")).strip()
    if path:
        try:
            url = pathlib.Path(path).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise NotifyError(
                f"{WEBHOOK_FILE_ENV} points at {path}, which cannot be read: {exc}"
            ) from exc
        if not url:
            raise NotifyError(f"{WEBHOOK_FILE_ENV} points at {path}, which is empty")
        return url
    url = str(source.get(WEBHOOK_ENV, "")).strip()
    if url:
        return url
    saved = default_webhook_path()
    try:
        url = saved.read_text(encoding="utf-8").strip()
    except OSError:
        url = ""
    if url:
        return url
    raise NotifyError(
        f"No webhook configured — run `avs cadence --set-webhook <url>` "
        f"(stores it 0600 at {DEFAULT_WEBHOOK_PATH}), or point "
        f"{WEBHOOK_FILE_ENV} at a file holding the URL."
    )


def check_url(url: str) -> str:
    """The URL, if it is a Discord webhook. Otherwise `NotifyError`.

    Named hosts rather than a pattern: the point is that an alert about a
    private workspace goes to Discord or goes nowhere.
    """
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https":
        raise NotifyError(
            f"the webhook URL must be https, not {parts.scheme or 'a bare path'}"
        )
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise NotifyError(
            f"{host or 'that URL'} is not a Discord webhook host — refusing to "
            f"post this workspace's state to it. Allowed: "
            f"{', '.join(ALLOWED_HOSTS)}."
        )
    return url


def deliver(url: str, content: str, *, opener=None, timeout: float = 15.0) -> int:
    """POST the message. Returns the HTTP status.

    `opener` is the seam every test uses — nothing here reaches the network
    unless a caller hands it a real one.
    """
    check_url(url)
    payload = json.dumps({"content": content}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 — scheme+host checked above
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "ai-venture-studio"},
    )
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=timeout) as response:
            return int(getattr(response, "status", 0) or 0)
    except urllib.error.HTTPError as exc:
        raise NotifyError(f"Discord refused the message: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NotifyError(f"the webhook could not be reached: {exc}") from exc


def _sent_path(repo_dir: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(repo_dir) / SENT_LOG


def load_sent(repo_dir: str | pathlib.Path) -> dict:
    import yaml

    try:
        data = yaml.safe_load(_sent_path(repo_dir).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def record_sent(repo_dir: str | pathlib.Path, alert: Alert, *, at: dt.date) -> pathlib.Path:
    import yaml

    path = _sent_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"digest": alert.digest, "at": at.isoformat(), "heading": alert.heading},
            sort_keys=False, allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def is_worth_repeating(
    sent: dict, alert: Alert, *, today: dt.date, repeat_days: int = REPEAT_DAYS
) -> bool:
    """True when this alert is new, or old enough to say again.

    A changed alert is never held back — the delay is for repetition, and
    holding new news would make the channel less trustworthy than the log it
    replaces.
    """
    if str(sent.get("digest", "")) != alert.digest:
        return True
    try:
        last = dt.date.fromisoformat(str(sent.get("at", "")))
    except ValueError:
        return True
    return (today - last).days >= repeat_days


class NotifyResult(BaseModel):
    sent: bool = False
    reason: str = ""
    content: str = ""
    digest: str = ""


def notify(
    repo_dir: str | pathlib.Path,
    report,
    build=None,
    *,
    today: dt.date | None = None,
    environ: dict | None = None,
    opener=None,
    repeat_days: int = REPEAT_DAYS,
    force: bool = False,
    outcomes=None,
) -> NotifyResult:
    """Send the alert if there is one, it is not a repeat, and a webhook exists.

    Every "no" is a stated reason rather than a silent return — the operator
    needs to be able to tell "nothing to report" from "misconfigured", and
    those look identical from the outside.
    """
    day = today or dt.datetime.now().date()
    alert = build_alert(report, build, outcomes=outcomes)
    if alert is None:
        return NotifyResult(sent=False, reason="nothing needs a person")
    if not force and not is_worth_repeating(
        load_sent(repo_dir), alert, today=day, repeat_days=repeat_days
    ):
        return NotifyResult(
            sent=False, reason=f"already sent; unchanged for under {repeat_days}d",
            content=alert.render(), digest=alert.digest,
        )
    url = resolve_webhook(environ)
    content = alert.render()
    deliver(url, content, opener=opener)
    record_sent(repo_dir, alert, at=day)
    return NotifyResult(sent=True, reason="delivered", content=content, digest=alert.digest)
