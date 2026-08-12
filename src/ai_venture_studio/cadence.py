"""Loop cadence — the recurring loops' own watchdog.

Three loops in this system are designed to *recur*: the compounding loop
(§09.8), the Sweep role (doc 29), and weekly attention collection (doc 25
§76.4). Each writes a dated artifact when it runs. None of them had a
trigger — "weekly" was a habit, and a habit that lapses is invisible.

`attention.py` already records what that costs, in its own words: a missed
week does not merely lose a week, it *resets the streak* the kill criterion
depends on. A loop whose recurrence is unenforced degrades silently and
reports nothing, which is the "looks done" failure exactly.

This module reads the artifacts the loops already write and answers one
question mechanically: **which loop is overdue?** Three rules keep it
honest:

1. **It states; it does not decide.** Staleness is arithmetic on file
   dates. Nothing here judges whether a loop's output was any good.
2. **A loop that never ran is `never_run`, not zero days old.** Absence of
   evidence is reported as absence, never rendered as a fresh pass — the
   one way a watchdog can lie.
3. **Loops needing a human number are never run for them.** `avs
   attention` without `--confirm-hours` is read-only by design ("the
   machine never sets this"). The scheduler surfaces the ask; the human
   answers it. Mechanical recurrence is the machine's job; judgment is not.

**Why this is machine-local and not CI.** Every artifact read here lives
under `.mas/`, which is gitignored. A CI runner checks out an empty `.mas/`
and would find every loop `never_run` on every run — or, worse, be tuned
until it reported a clean pass forever against state it cannot see. The
trigger has to run where the state lives, so the installer here writes a
user LaunchAgent on the operator's own machine.

The LaunchAgent fires *daily* and runs only what is due. A weekly timer has
one chance to be missed; a daily due-check has seven, and re-running when
nothing is due costs a file-stat.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import plistlib
import re
import shutil
import subprocess
import sys

from pydantic import BaseModel, Field

WEEKLY = 7

#: Slack before "due" becomes "overdue". A weekly loop run every Monday is
#: seven days old the next Monday — due, and entirely healthy. Only a loop
#: that has slid past a whole extra weekend is stale enough to fail a gate.
GRACE_DAYS = 2

#: Filenames the loops already write. Parsed, never written, by this module.
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


class CadenceError(Exception):
    """A cadence check could not be made — never a loop being overdue."""


class LoopStatus(BaseModel):
    """One recurring loop's standing, as fact plus provenance.

    `evidence` carries where the date came from so a surprising verdict can
    be checked against the filesystem rather than believed.
    """

    name: str
    cadence_days: int = WEEKLY
    last_run: str = ""  # ISO date; "" when the loop has never run
    age_days: int | None = None  # None when never run
    state: str = "ok"  # ok | due | overdue | never_run
    evidence: str = ""
    command: str = ""
    human_input_required: bool = False
    produced: str = ""  # what the last run actually yielded, in its own words
    #: The loop ran on schedule but had no input to read. Not stale — the loop
    #: is healthy — but reporting it as a plain "ok" would let a loop that
    #: compounds nothing read as a loop that is working.
    vacuous: bool = False
    #: WHY it was empty, when the run recorded it: `never_any` (this workspace
    #: has never produced a review — the loop is almost certainly pointed at
    #: the wrong directory) or `work_stopped` (reviews exist, all older than
    #: the window — the loop is fine and the work is what paused). Empty when
    #: the artifact predates the distinction. Two opposite responses hid
    #: behind one "nothing to read" until this.
    empty_because: str = ""

    @property
    def needs_run(self) -> bool:
        """Due, overdue, or never run — the scheduler should act."""
        return self.state in {"due", "overdue", "never_run"}

    @property
    def is_stale(self) -> bool:
        """Overdue or never run — a gate should fail."""
        return self.state in {"overdue", "never_run"}

    def describe(self) -> str:
        if self.state == "never_run":
            return "never run"
        if self.state == "ok":
            return f"ok, empty ({self.age_days}d)" if self.vacuous else (
                f"ok ({self.age_days}d)"
            )
        return f"{self.state.upper()} ({self.age_days}d)"


class CadenceReport(BaseModel):
    at: str = ""
    repo_dir: str = ""
    loops: list[LoopStatus] = Field(default_factory=list)

    @property
    def stale(self) -> list[LoopStatus]:
        return [loop for loop in self.loops if loop.is_stale]

    @property
    def due(self) -> list[LoopStatus]:
        return [loop for loop in self.loops if loop.needs_run]

    @property
    def vacuous(self) -> list[LoopStatus]:
        """Ran on time, read nothing. Healthy by date, hollow by content."""
        return [loop for loop in self.loops if loop.vacuous]

    def summary(self) -> str:
        if not self.loops:
            return "no recurring loops configured"
        if self.stale:
            names = ", ".join(loop.name for loop in self.stale)
            noun = "loop" if len(self.stale) == 1 else "loops"
            return f"{len(self.stale)} {noun} overdue: {names}"
        if self.vacuous:
            # "All within cadence" would be true and misleading in the same
            # breath — the schedule is being kept over an empty window.
            names = ", ".join(loop.name for loop in self.vacuous)
            noun = "loop" if len(self.vacuous) == 1 else "loops"
            return (
                f"all {len(self.loops)} loops within cadence, but "
                f"{len(self.vacuous)} {noun} had nothing to read: {names}"
            )
        return f"all {len(self.loops)} loops within cadence"


def _classify(
    last: dt.date | None, today: dt.date, cadence_days: int
) -> tuple[str, int | None]:
    """Date arithmetic, isolated so the rule is readable and testable.

    A future-dated artifact (clock skew, a hand-written `--today`) is clamped
    to zero rather than reported as a negative age: it means "ran", and
    inventing a negative staleness would be a second lie on top of the first.
    """
    if last is None:
        return "never_run", None
    age = max((today - last).days, 0)
    if age < cadence_days:
        return "ok", age
    if age <= cadence_days + GRACE_DAYS:
        return "due", age
    return "overdue", age


def _latest_dated_file(
    directory: pathlib.Path, pattern: str
) -> tuple[dt.date | None, str]:
    """Newest ISO date embedded in a filename, plus the file it came from.

    The date is read from the *name*, not the mtime: the loops name their
    output for the day they cover, and a file copied or restored later would
    otherwise read as a run that never happened.
    """
    if not directory.is_dir():
        return None, f"{directory} (absent)"
    best: dt.date | None = None
    best_name = ""
    for path in directory.glob(pattern):
        found = _DATE_RE.search(path.name)
        if not found:
            continue
        try:
            day = dt.date.fromisoformat(found.group(1))
        except ValueError:
            continue
        if best is None or day > best:
            best, best_name = day, path.name
    if best is None:
        return None, f"{directory} (no dated artifact)"
    return best, str(directory / best_name)


#: compound's own header line: `Window: 12 review(s). Verdicts: {...}.`
#: Matched rather than re-derived, so the reading is of what that run actually
#: saw — not of what the window happens to hold now, days later.
_WINDOW_RE = re.compile(r"Window:\s*(\d+)\s*review")

#: The sentinel `render_proposal` writes when nothing cleared the evidence bar.
_NO_CONSTRAINT = "no constraint met the evidence bar"
#: What the run itself recorded about an empty window. Written by
#: `compound.render_proposal`; absent from artifacts older than it, which is
#: why every reader of it falls back rather than assuming.
_EMPTY_WHY_RE = re.compile(
    r"Nothing reached this window: (\d+) review\(s\) exist, "
    r"newest (\d{4}-\d{2}-\d{2})"
)
_NEVER_WRITTEN = "no review has ever been written here"


def _proposal_substance(
    path: str, today: dt.date
) -> tuple[int | None, str, bool, str]:
    """What the last compounding run actually read, and whether it read at all.

    Returns `(reviews, produced, vacuous, empty_because)`. An unrecognised
    format returns `(None, "", False, "")` — an older artifact says nothing
    about its own substance, and guessing would be worse than silence.
    """
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, "", False, ""
    found = _WINDOW_RE.search(text)
    if not found:
        return None, "", False, ""
    reviews = int(found.group(1))
    if reviews == 0:
        # The loop ran and wrote a file, so every date-based check calls it
        # fresh. It compounded nothing, because nothing reached it — and the
        # two ways that happens want opposite responses from the reader, so
        # the report has to tell them apart instead of saying "nothing".
        stale = _EMPTY_WHY_RE.search(text)
        if stale:
            count, newest = int(stale.group(1)), stale.group(2)
            age = (today - dt.date.fromisoformat(newest)).days
            return 0, (
                f"read 0 reviews — {count} on disk, newest {newest} "
                f"({max(age, 0)}d old)"
            ), True, "work_stopped"
        if _NEVER_WRITTEN in text:
            return 0, "read 0 reviews — none has ever been written here", \
                True, "never_any"
        return 0, "read 0 reviews — nothing to compound", True, ""
    verdict = ("no constraint met the bar" if _NO_CONSTRAINT in text
               else "constraint(s) proposed")
    return reviews, f"read {reviews} review(s), {verdict}", False, ""


def _compound_status(repo_dir: pathlib.Path, today: dt.date) -> LoopStatus:
    last, evidence = _latest_dated_file(
        repo_dir / ".mas" / "compound", "proposal-*.md"
    )
    state, age = _classify(last, today, WEEKLY)
    produced, vacuous, because = "", False, ""
    if evidence:
        _, produced, vacuous, because = _proposal_substance(evidence, today)
    return LoopStatus(
        name="compound", last_run=last.isoformat() if last else "",
        age_days=age, state=state, evidence=evidence,
        command="avs compound", produced=produced, vacuous=vacuous,
        empty_because=because,
    )


def _sweep_status(repo_dir: pathlib.Path, today: dt.date) -> LoopStatus:
    last, evidence = _latest_dated_file(
        repo_dir / ".mas" / "sweep", "digest-*.yaml"
    )
    state, age = _classify(last, today, WEEKLY)
    # Sweep is deliberately NOT judged vacuous on an empty result: invariant
    # 14.30 makes a clean pass a real finding that must be recorded rather
    # than pass silently. Its own note is carried through instead.
    produced = ""
    if evidence:
        produced = _digest_note(evidence)
    return LoopStatus(
        name="sweep", last_run=last.isoformat() if last else "",
        age_days=age, state=state, evidence=evidence,
        command="avs sweep", produced=produced,
    )


def _digest_note(path: str) -> str:
    """Sweep already writes a one-line account of its pass; carry it, don't
    re-derive it."""
    import yaml

    try:
        data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("note", "")).strip()


def _attention_status(repo_dir: pathlib.Path, today: dt.date) -> LoopStatus:
    """Attention's marker is a logged row, not a file.

    Only `status: logged` rows count. A `not_tracked` row records that the
    week was *considered* and left unmeasured — treating it as a run would
    let the series look maintained while measuring nothing, and this is the
    one series the launch kill criterion is falsifiable by.
    """
    from ai_venture_studio import attention

    evidence = str(pathlib.Path(repo_dir) / attention.ATTENTION_LOG)
    try:
        rows = attention.load_log(repo_dir)
    except Exception as exc:  # noqa: BLE001 — an unreadable log is not "fresh"
        return LoopStatus(
            name="attention", state="never_run", human_input_required=True,
            evidence=f"{evidence} (unreadable: {exc})",
            command="avs attention",
        )
    logged = [row for row in rows if row.status == "logged" and row.week]
    last: dt.date | None = None
    for row in logged:
        week_end = _iso_week_end(row.week)
        if week_end and (last is None or week_end > last):
            last, evidence = week_end, f"{evidence} ({row.week})"
    state, age = _classify(last, today, WEEKLY)
    return LoopStatus(
        name="attention", last_run=last.isoformat() if last else "",
        age_days=age, state=state, evidence=evidence,
        command="avs attention", human_input_required=True,
    )


def _iso_week_end(week: str) -> dt.date | None:
    """`YYYY-Www` → the Sunday that closes it, the earliest date by which
    that week could have been logged. Unparseable input is None, not today."""
    try:
        year, _, number = week.partition("-W")
        return dt.date.fromisocalendar(int(year), int(number), 7)
    except (ValueError, TypeError):
        return None


def assess(
    repo_dir: str | pathlib.Path = ".", *, today: dt.date | None = None
) -> CadenceReport:
    """Every recurring loop's standing, read from what the loops wrote."""
    root = pathlib.Path(repo_dir)
    day = today or dt.date.today()
    return CadenceReport(
        at=day.isoformat(),
        repo_dir=str(root.resolve()),
        loops=[
            _compound_status(root, day),
            _sweep_status(root, day),
            _attention_status(root, day),
        ],
    )


# --------------------------------------------------------------------------
# Running what is due
# --------------------------------------------------------------------------


class RunOutcome(BaseModel):
    loop: str
    ran: bool = False
    exit_code: int | None = None
    detail: str = ""


def run_due(
    repo_dir: str | pathlib.Path = ".", *, today: dt.date | None = None,
    timeout: int = 3600, executable: str | None = None,
) -> list[RunOutcome]:
    """Run each loop that is due; skip the ones that are not.

    Idempotent by construction: a second call the same day finds the loops
    fresh and does nothing, which is what lets the scheduler fire daily
    against weekly work.

    Loops marked `human_input_required` are run in their read-only form —
    they surface the ask into the scheduler's log without answering it.
    """
    report = assess(repo_dir, today=today)
    binary = executable or shutil.which("avs") or sys.executable
    outcomes: list[RunOutcome] = []
    for loop in report.loops:
        if not loop.needs_run:
            outcomes.append(RunOutcome(
                loop=loop.name, detail=f"not due ({loop.describe()})"
            ))
            continue
        argv = _argv_for(loop, binary, pathlib.Path(repo_dir))
        try:
            completed = subprocess.run(  # noqa: S603 — argv list, never a shell
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            outcomes.append(RunOutcome(
                loop=loop.name, ran=False, detail=f"could not run: {exc}"
            ))
            continue
        detail = (completed.stderr or completed.stdout or "").strip()
        if loop.human_input_required:
            detail = f"surfaced for your decision — {detail}" if detail else \
                "surfaced for your decision"
        outcomes.append(RunOutcome(
            loop=loop.name, ran=True, exit_code=completed.returncode,
            detail=detail[-2000:],
        ))
    return outcomes


def _argv_for(
    loop: LoopStatus, binary: str, repo_dir: pathlib.Path
) -> list[str]:
    """The command for one loop. The three commands spell their workspace
    option differently (`--repo-dir` vs `--workspace`), so it is looked up
    rather than assumed.

    The interpreter fallback is `-m ai_venture_studio.cli`, not
    `-m ai_venture_studio`: there is no `__main__.py`, so the package form
    fails with "No module named ai_venture_studio.__main__". The `.cli` form
    is the one the detached workers already run.
    """
    base = (
        [binary] if binary.endswith("avs")
        else [binary, "-m", "ai_venture_studio.cli"]
    )
    flag = {"sweep": "--workspace"}.get(loop.name, "--repo-dir")
    return [*base, loop.name, flag, str(repo_dir)]


# --------------------------------------------------------------------------
# The trigger: a machine-local LaunchAgent
# --------------------------------------------------------------------------

LAUNCH_AGENT_LABEL = "ai.venture.studio.loops"


def agent_plist_path() -> pathlib.Path:
    return (
        pathlib.Path.home() / "Library" / "LaunchAgents"
        / f"{LAUNCH_AGENT_LABEL}.plist"
    )


def agent_log_path() -> pathlib.Path:
    return pathlib.Path.home() / "Library" / "Logs" / "ai-venture-studio" / "loops.log"


#: launchd does not read a login shell. A credential the operator keeps in
#: `.zshrc` is simply absent at 09:00, so a scheduled `compound` reaches its
#: provider with nothing and fails every morning into a log nobody opens.
#: These names travel into the plist so a scheduled run resolves the same
#: credential an interactive run resolves.
#:
#: Only *pointers* are eligible — a variable naming a file, or a non-secret
#: setting. The plist is a readable file in `~/Library/LaunchAgents`; writing
#: a key into it would turn the scheduler into a credential leak.
ENV_POINTERS = (
    "ANTHROPIC_API_KEY_FILE",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "AWS_PROFILE",
    "AWS_REGION",
    "AVS_PROVIDER",
)

#: Raw secrets, never copied. Refused *by name* so the operator learns which
#: variable to convert to its `_FILE` form, rather than debugging a silent
#: 401 at nine in the morning.
ENV_SECRETS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
)


def scheduled_env(
    environ: dict | None = None, *, binary: str | None = None
) -> tuple[dict[str, str], list[str]]:
    """The environment a launchd job needs, and what could not be carried.

    Returns `(env, warnings)`. Pure over `environ` so a test can assert both
    halves without touching the real process environment.
    """
    import os

    source = os.environ if environ is None else environ
    env: dict[str, str] = {}
    # launchd's default PATH is `/usr/bin:/bin:/usr/sbin:/sbin` — it does not
    # contain the interpreter `avs` was installed into, so any subprocess that
    # resolves a tool by name would miss it.
    parts = [str(pathlib.Path(binary).parent)] if binary else []
    env["PATH"] = ":".join([*parts, "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    for name in ENV_POINTERS:
        value = str(source.get(name, "")).strip()
        if value:
            env[name] = value
    warnings: list[str] = []
    for name in ENV_SECRETS:
        if str(source.get(name, "")).strip() and name not in env:
            warnings.append(
                f"{name} is set in your shell but will NOT be written to the "
                f"plist — a secret does not belong in a readable file. Put the "
                f"key in a file and export {name}_FILE instead."
            )
    if not any(n.endswith("_FILE") for n in env):
        warnings.append(
            "No *_KEY_FILE pointer found, so the scheduled run may reach its "
            "provider without a credential. Loops that need one will fail into "
            f"{agent_log_path()}."
        )
    return env, warnings


def render_plist(
    workspace: str | pathlib.Path, *, executable: str | None = None,
    hour: int = 9, minute: int = 0, env: dict[str, str] | None = None,
) -> bytes:
    """The LaunchAgent, as plist bytes.

    Pure and returned rather than written, so a test asserts on the exact
    schedule and argv without touching `~/Library`.

    `RunAtLoad` is deliberately false: installing a scheduler must not itself
    start a run. The operator installs the trigger, then the trigger fires on
    its own schedule.
    """
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise CadenceError(f"invalid schedule: {hour:02d}:{minute:02d}")
    binary = executable or shutil.which("avs") or sys.executable
    root = pathlib.Path(workspace).resolve()
    log = agent_log_path()
    plan = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            binary, "cadence", "--repo-dir", str(root), "--run-due",
        ],
        "WorkingDirectory": str(root),
        # Daily, not weekly. A weekly timer has one chance to be missed; the
        # loops themselves enforce the weekly cadence, so a daily check that
        # finds nothing due is a no-op that costs a file-stat.
        "StartCalendarInterval": {"Hour": int(hour), "Minute": int(minute)},
        "RunAtLoad": False,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    plan["EnvironmentVariables"] = (
        env if env is not None else scheduled_env(binary=binary)[0]
    )
    return plistlib.dumps(plan, sort_keys=True)


def install_agent(
    workspace: str | pathlib.Path, *, executable: str | None = None,
    hour: int = 9, minute: int = 0, load: bool = True,
    plist_path: pathlib.Path | None = None,
) -> dict:
    """Write the LaunchAgent, and by default ask launchd to load it.

    Returns what was done rather than printing it, so the CLI owns the words
    and a test owns the facts. With `load=False` the plist is written and the
    exact `launchctl` line is returned instead of run — the trigger is armed
    by a human, which is the same posture every other automation in this
    system takes.
    """
    if sys.platform != "darwin" and plist_path is None:
        raise CadenceError(
            "LaunchAgents are macOS-only. On Linux, run "
            "`avs cadence --run-due` from cron or a systemd timer — the "
            "check itself is portable."
        )
    root = pathlib.Path(workspace).resolve()
    if not (root / ".mas").is_dir():
        raise CadenceError(
            f"{root} has no .mas/ — the loops read their state from there, so "
            "a scheduler pointed here would find nothing to do. Point "
            "--repo-dir at the workspace the loops actually run in."
        )
    path = plist_path or agent_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    agent_log_path().parent.mkdir(parents=True, exist_ok=True)
    binary = executable or shutil.which("avs") or sys.executable
    env, warnings = scheduled_env(binary=binary)
    body = render_plist(
        root, executable=executable, hour=hour, minute=minute, env=env
    )
    path.write_bytes(body)
    command = ["launchctl", "bootstrap", f"gui/{_uid()}", str(path)]
    result = {
        "plist": str(path),
        "schedule": f"daily {hour:02d}:{minute:02d}",
        "workspace": str(root),
        "log": str(agent_log_path()),
        "loaded": False,
        "command": " ".join(command),
        "env_keys": sorted(k for k in env if k != "PATH"),
        "warnings": warnings,
    }
    if not load:
        return result
    # A previous copy must be removed first or bootstrap refuses; a failure
    # here is not an error, since nothing was loaded to remove.
    subprocess.run(  # noqa: S603 — argv list, never a shell
        ["launchctl", "bootout", f"gui/{_uid()}/{LAUNCH_AGENT_LABEL}"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    completed = subprocess.run(  # noqa: S603 — argv list, never a shell
        command, capture_output=True, text=True, timeout=30, check=False,
    )
    result["loaded"] = completed.returncode == 0
    if completed.returncode != 0:
        result["detail"] = (completed.stderr or completed.stdout or "").strip()
    return result


# --------------------------------------------------------------------------
# Is the trigger running the build you think it is?
# --------------------------------------------------------------------------
#
# The plist names an absolute path to an `avs` binary — whichever one was on
# PATH at install time. That install is a *different* install from the one you
# release with, and nothing connects them: `git push` + a green publish moves
# PyPI, and the thing that fires at 09:00 goes on running whatever it has.
#
# v0.72.2 shipped a metering fix and the daily loop kept running v0.72.1 for
# as long as nobody thought to look. That is the same shape as every other bug
# this module exists to catch: a green report over a stale reality. So the
# check is mechanical now, and the operator is told rather than trusted to
# remember.

#: Asked of the *scheduled* interpreter, so it must run on builds older than
#: this one — it cannot assume a flag or an attribute added later. Distribution
#: metadata is what pip actually wrote to disk and cannot drift from it;
#: `__version__` is hand-maintained and did drift (0.70.1 shipped inside both
#: v0.71.0 and v0.71.1). Metadata first, attribute only as a fallback for a
#: source checkout that was never installed.
_VERSION_PROBE = (
    "import importlib.metadata as m\n"
    "try:\n"
    "    print(m.version('ai-venture-studio'))\n"
    "except Exception:\n"
    "    import ai_venture_studio as p\n"
    "    print(p.__version__)\n"
)


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


class SchedulerBuild(BaseModel):
    """What the daily trigger will execute, next to what is reporting here."""

    plist: str = ""
    installed: bool = False
    binary: str = ""
    scheduled_version: str = ""
    running_version: str = ""
    detail: str = ""  # why the scheduled version could not be read

    @property
    def known(self) -> bool:
        return bool(self.scheduled_version and self.running_version)

    @property
    def behind(self) -> bool:
        """The scheduler runs an OLDER build than this one.

        Only older counts. A newer or equal scheduled build is not a finding:
        running `avs cadence` from a development checkout while the scheduler
        holds the last release is normal, and reporting it would train the
        operator to scroll past the line that matters.
        """
        if not (self.installed and self.known):
            return False
        return _version_tuple(self.scheduled_version) < _version_tuple(
            self.running_version
        )

    def describe(self) -> str:
        if not self.installed:
            return "no LaunchAgent installed"
        if not self.scheduled_version:
            return f"{self.binary} — version unreadable ({self.detail})"
        return f"{self.binary} runs v{self.scheduled_version}"


def _interpreter_for(binary: str) -> tuple[str | None, str]:
    """The python that owns a console script, read from its shebang.

    The scheduled binary is usually `.../bin/avs`, a generated console script
    whose first line names its interpreter. Probing that interpreter works
    against every past build; probing `avs --version` would only work against
    builds new enough to have the flag, which is exactly the builds that are
    not the problem.
    """
    path = pathlib.Path(binary)
    if path.name in ("python", "python3") or path.name.startswith("python"):
        return binary, ""
    try:
        first = path.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
    except OSError as exc:
        return None, f"cannot read {binary}: {exc.strerror or exc}"
    if not first.startswith("#!"):
        return None, f"{binary} is not a console script"
    return first[2:].strip().split()[0], ""


def scheduler_build(
    plist_path: pathlib.Path | None = None, *, running: str | None = None,
    runner=None,
) -> SchedulerBuild:
    """Read the installed plist and ask its binary which version it is.

    `runner` is injected so the probe is a unit test rather than a subprocess:
    it takes an argv list and returns an object with `returncode` and `stdout`.
    """
    from ai_venture_studio import __version__

    path = plist_path or agent_plist_path()
    build = SchedulerBuild(
        plist=str(path), running_version=running or __version__
    )
    if not path.exists():
        return build
    build.installed = True
    try:
        plan = plistlib.loads(path.read_bytes())
        build.binary = str(plan["ProgramArguments"][0])
    except (OSError, ValueError, KeyError, IndexError) as exc:
        build.detail = f"unreadable plist: {exc}"
        return build

    interpreter, why = _interpreter_for(build.binary)
    if interpreter is None:
        build.detail = why
        return build

    if runner is None:
        def runner(argv):
            return subprocess.run(  # noqa: S603 — argv list, never a shell
                argv, capture_output=True, text=True, timeout=30, check=False,
            )

    try:
        done = runner([interpreter, "-c", _VERSION_PROBE])
    except (OSError, subprocess.SubprocessError) as exc:
        build.detail = f"probe failed: {exc}"
        return build
    if done.returncode != 0:
        build.detail = "the scheduled install cannot import ai-venture-studio"
        return build
    build.scheduled_version = (done.stdout or "").strip().splitlines()[-1:][0] \
        if (done.stdout or "").strip() else ""
    if not build.scheduled_version:
        build.detail = "the version probe printed nothing"
    return build


def uninstall_agent(plist_path: pathlib.Path | None = None) -> dict:
    """Remove the LaunchAgent. Absent is success — uninstall is idempotent."""
    path = plist_path or agent_plist_path()
    subprocess.run(  # noqa: S603 — argv list, never a shell
        ["launchctl", "bootout", f"gui/{_uid()}/{LAUNCH_AGENT_LABEL}"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    existed = path.exists()
    if existed:
        path.unlink()
    return {"plist": str(path), "removed": existed}


def _uid() -> int:
    import os

    return os.getuid()
