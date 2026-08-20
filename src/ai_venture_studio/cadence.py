"""Loop cadence — the recurring loops' own watchdog.

Three loops in this system are designed to *recur*: the compounding loop
(§09.8), the Sweep role (doc 29), and the framework's own product-bench
(launch PRD O-L2). Each writes a dated artifact when it runs. None had a
trigger — "weekly" was a habit, and a habit that lapses is invisible. A loop
whose recurrence is unenforced degrades silently and reports nothing, which
is the "looks done" failure exactly.

(A different third loop existed until v0.81.0. Weekly attention collection
asked the operator to type a number every week and was withdrawn with the
kill criterion it fed — ADR-033. Every loop here runs itself, which is the
shape a scheduler should have had from the start: nothing it reports is
waiting on a human to answer a prompt. The bench added in v0.82.0 obeys the
same rule — it is a paid, hour-long run, but it is a run, not a question.)

This module reads the artifacts the loops already write and answers one
question mechanically: **which loop is overdue?** Two rules keep it honest:

1. **It states; it does not decide.** Staleness is arithmetic on file
   dates. Nothing here judges whether a loop's output was any good.
2. **A loop that never ran is `never_run`, not zero days old.** Absence of
   evidence is reported as absence, never rendered as a fresh pass — the
   one way a watchdog can lie.

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

#: Every loop name this module knows. A `--only` filter is checked against
#: this and not against what happens to be present, so a typo is refused
#: loudly instead of quietly selecting nothing — a scheduler watching an
#: empty set reports "all clear" forever, which is the one thing a watchdog
#: must never do.
LOOP_NAMES = ("compound", "sweep", "bench")

#: The labelled real-product cases the bench runs. Present only in a checkout
#: of the framework itself; its absence is what tells this module that a
#: workspace does not own the bench series.
BENCH_CASES = pathlib.Path("benchmarks") / "products-real"
#: Where each run's scoreboard lands (`product_bench.save_summary` writes both
#: the gitignored `.mas/` copy and this tracked one).
BENCH_RESULTS = pathlib.Path("benchmarks") / "results"

#: A bench pass drives the full autopilot over four real products. Run 11 took
#: 74 minutes of wall clock; the default hour would have killed it at the
#: three-quarter mark and reported a timeout as a capability failure — a lie
#: in the direction that costs the most, since this series is the only kill
#: criterion the launch PRD has left.
#:
#: Raised 6h → 8h for run 15. Run 14 spent 11,206s (3.1h) of the 6h, which
#: read as ample — but ADR-037 sends every medium-only review into a fix
#: iteration plus a re-review, and medium is the modal severity, so most of
#: the 17 tasks now spend two model round-trips they did not before. The
#: margin was sized against runs that never did that. A ceiling that is too
#: high costs nothing when the run finishes early; one that is too low
#: reports a capability failure that did not happen, on the only kill
#: criterion the launch PRD has left.
BENCH_TIMEOUT_S = 8 * 3600

#: What every other loop gets. Minutes of work, not hours.
DEFAULT_TIMEOUT_S = 3600


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
    #: How long `run_due` waits for this loop before giving up. Per loop, not
    #: global: the bench runs for over an hour and everything else runs for
    #: minutes, and one shared ceiling has to be wrong for one of them.
    timeout_s: int = DEFAULT_TIMEOUT_S

    @property
    def needs_run(self) -> bool:
        """Due, overdue, or never run — the scheduler should act."""
        return self.state in {"due", "overdue", "never_run"}

    @property
    def is_stale(self) -> bool:
        """Overdue or never run — a gate should fail."""
        return self.state in {"overdue", "never_run"}

    @property
    def next_due(self) -> str:
        """The date this loop next comes due — stated, not left as arithmetic.

        A row reading `last run 2026-08-14 | cadence 7d | ok (1d)` contains
        the answer to "so when does it run again?" only as a sum the reader
        has to perform. A reader who skips the sum sees a scheduler that
        fires every morning and concludes "tomorrow" — which is how the
        answer came out six days wrong. The scheduler's daily wake-up and
        the loop's weekly cadence are different periods, and the row showed
        neither the second one's consequence.

        Empty when the loop has never run, or when `last_run` is not a date
        this can add to — a stated date that was guessed is worse than none.
        """
        if not self.last_run:
            return ""
        try:
            last = dt.date.fromisoformat(self.last_run)
        except ValueError:
            return ""
        return (last + dt.timedelta(days=self.cadence_days)).isoformat()

    def describe(self) -> str:
        if self.state == "never_run":
            return "never run"
        if self.state == "ok":
            # Only when nothing needs doing: that is exactly the state whose
            # reader asks "when, then?". A due loop is already being told to
            # run now, and a date beside that would only compete with it.
            inside = f"{self.age_days}d"
            if self.next_due:
                inside += f", next {self.next_due}"
            return f"ok, empty ({inside})" if self.vacuous else f"ok ({inside})"
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


def _bench_status(
    repo_dir: pathlib.Path, today: dt.date
) -> LoopStatus | None:
    """The product-bench series — but only where that series lives.

    Returns None for every other workspace, and that is the whole point of
    the function. The bench is not a per-project loop: it measures the
    *framework's* capability against four labelled real products that ship in
    this repository. A product workspace has no `benchmarks/products-real/`,
    and reporting a loop it cannot run as `never_run` every morning would be
    a standing false alarm in the one channel that must not cry wolf.

    Where the cases *are* present and no result has ever been written, the
    answer is `never_run` — absence of evidence reported as absence, same as
    everywhere else here.

    Why it is watched at all: since v0.81.0 this series is the only kill
    criterion the launch PRD has (`bench_criterion.py` reads exactly these
    files). A criterion that reads a series nobody notices has stopped is a
    criterion that reports "not fired" forever. It went 16 days unnoticed
    before this loop existed.
    """
    if not (repo_dir / BENCH_CASES).is_dir():
        return None
    last, evidence = _latest_dated_file(repo_dir / BENCH_RESULTS, "result-*.yaml")
    state, age = _classify(last, today, WEEKLY)
    return LoopStatus(
        name="bench", last_run=last.isoformat() if last else "",
        age_days=age, state=state, evidence=evidence,
        command=f"avs product-bench --cases-dir {BENCH_CASES}",
        produced=_bench_rates(evidence) if last else "",
        timeout_s=BENCH_TIMEOUT_S,
    )


def result_concerns(repo_dir: str | pathlib.Path) -> list[tuple[str, str]]:
    """What each loop's last RESULT says, as against whether it ran on time.

    Every other reading in this module answers "is the loop keeping its
    cadence". A loop can keep it perfectly and still produce something a
    person has to see, and until v0.90.0 nothing anywhere looked: bench run
    12 finished with a crashed case and build 75% / probes 65%, and the
    alert path printed `nothing needs a person`, because the loop itself had
    exited 0. Liveness and results are different questions and only one of
    them was ever asked.

    A list of `(loop, sentence)` rather than a string: the alert names which
    loop is speaking, and the next loop to grow a result worth reading has
    somewhere to put it. Only the bench has one today.

    Still rule 1 — it states, it does not decide. The floors and the streak
    are `bench_criterion`'s, read from there and never re-derived here.
    """
    from ai_venture_studio import bench_criterion

    said = bench_criterion.concern(repo_dir)
    return [("bench", said)] if said else []


def _bench_rates(path: str) -> str:
    """The headline numbers the last run recorded, in its own words.

    Read rather than re-derived, and reported without judgement: whether they
    are low enough to fire the criterion is `bench_criterion.evaluate`'s call,
    not this module's (rule 1 — it states, it does not decide).
    """
    import yaml

    try:
        data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ""
    if not isinstance(data, dict):
        return ""
    # A rate over no cases is null, not zero (ADR-053). Saying nothing is
    # the right report for a run with an empty build axis — "build 0%,
    # probes 0%" would be this module deciding something it did not measure.
    if data.get("build_rate") is None or data.get("probe_pass_rate") is None:
        return ""
    try:
        build = float(data["build_rate"])
        probes = float(data["probe_pass_rate"])
    except (KeyError, TypeError, ValueError):
        return ""
    read = f"build {build:.0%}, probes {probes:.0%}"
    # A rate averaged over 3 of 4 cases is a different reading from one
    # averaged over 4, and the percentages alone cannot say which it is.
    rates = data.get("rates") if isinstance(data.get("rates"), dict) else {}
    measured, total = rates.get("cases_measured"), rates.get("cases_total")
    if isinstance(measured, int) and isinstance(total, int) and measured < total:
        read += f" — over {measured} of {total} cases"
    # The increment axis rides along on its own terms (ADR-049): it answers
    # a different question over different cases, so it is appended, never
    # averaged into the two rates above.
    inc = rates.get("increment") if isinstance(rates.get("increment"), dict) else {}
    gate = inc.get("gate_rate")
    if isinstance(gate, (int, float)):
        read += f", increment gate {float(gate):.0%}"
    # Which build produced the reading, beside the reading. `state` above
    # answers "did the loop run recently enough" in DAYS, and days is a proxy
    # that breaks exactly when releases outpace the cadence: the bench reads
    # "ok, 4d" while its newest numbers came from nine releases ago. The file
    # has recorded `avs_version` since run 15 for this reason, and the
    # scheduler line already prints the running build, so naming this one puts
    # the comparison in front of the reader instead of in three documents they
    # would have to go find. Stated, not judged — no threshold, no alarm; this
    # module does not decide how stale is too stale (rule 1).
    build = data.get("avs_version")
    if isinstance(build, str) and build.strip():
        read += f" · measured on v{build.strip()}"
    return read


def _selected(only) -> set[str] | None:
    """The `--only` filter, validated against the names this module knows.

    Checked against `LOOP_NAMES` rather than against the loops present here,
    so `--only bnech` is a refusal rather than a scheduler that watches
    nothing and reports all clear every morning.
    """
    if only is None:
        return None
    wanted = {str(name).strip() for name in only if str(name).strip()}
    if not wanted:
        raise CadenceError("--only was given with no loop names")
    unknown = sorted(wanted - set(LOOP_NAMES))
    if unknown:
        raise CadenceError(
            f"unknown loop(s): {', '.join(unknown)} — known loops are "
            f"{', '.join(LOOP_NAMES)}"
        )
    return wanted


def assess(
    repo_dir: str | pathlib.Path = ".", *, today: dt.date | None = None,
    only=None,
) -> CadenceReport:
    """Every recurring loop's standing, read from what the loops wrote.

    `only` restricts the report to named loops, for a scheduler that owns one
    loop in one directory. Naming a loop this workspace does not have is an
    error, not an empty report — see `_selected`.
    """
    root = pathlib.Path(repo_dir)
    day = today or dt.date.today()
    built = [
        _compound_status(root, day),
        _sweep_status(root, day),
        _bench_status(root, day),
    ]
    loops = [loop for loop in built if loop is not None]
    wanted = _selected(only)
    if wanted is not None:
        missing = sorted(wanted - {loop.name for loop in loops})
        if missing:
            raise CadenceError(
                f"{', '.join(missing)} is not tracked in {root}: the bench "
                f"needs {BENCH_CASES}/ and only the framework checkout has "
                f"it. Point --repo-dir at that checkout, or drop --only."
                if missing == ["bench"] else
                f"{', '.join(missing)} is not tracked in {root}"
            )
        loops = [loop for loop in loops if loop.name in wanted]
    return CadenceReport(
        at=day.isoformat(), repo_dir=str(root.resolve()), loops=loops
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
    timeout: int | None = None, executable: str | None = None, only=None,
) -> list[RunOutcome]:
    """Run each loop that is due; skip the ones that are not.

    Idempotent by construction: a second call the same day finds the loops
    fresh and does nothing, which is what lets the scheduler fire daily
    against weekly work.

    `timeout` overrides every loop's own ceiling; left unset, each loop waits
    as long as that loop actually takes (`LoopStatus.timeout_s`).
    """
    report = assess(repo_dir, today=today, only=only)
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
                argv, capture_output=True, text=True, check=False,
                timeout=timeout if timeout is not None else loop.timeout_s,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            outcomes.append(RunOutcome(
                loop=loop.name, ran=False, detail=f"could not run: {exc}"
            ))
            continue
        detail = (completed.stderr or completed.stdout or "").strip()
        outcomes.append(RunOutcome(
            loop=loop.name, ran=True, exit_code=completed.returncode,
            detail=detail[-2000:],
        ))
    return outcomes


def _argv_for(
    loop: LoopStatus, binary: str, repo_dir: pathlib.Path
) -> list[str]:
    """The command for one loop. Nothing about it is derived from the name:
    the subcommand, the workspace flag (`--repo-dir` vs `--workspace`) and
    the extra arguments all differ per loop, so each is looked up.

    The interpreter fallback is `-m ai_venture_studio.cli`, not
    `-m ai_venture_studio`: there is no `__main__.py`, so the package form
    fails with "No module named ai_venture_studio.__main__". The `.cli` form
    is the one the detached workers already run.
    """
    base = (
        [binary] if binary.endswith("avs")
        else [binary, "-m", "ai_venture_studio.cli"]
    )
    if loop.name == "bench":
        # The cases directory is passed explicitly: `product-bench` defaults
        # to `benchmarks/products` (the synthetic set), and the criterion
        # this loop feeds is defined over the *real* products. Defaulting
        # would keep the series alive with the wrong series.
        return [
            *base, "product-bench",
            "--cases-dir", str(repo_dir / BENCH_CASES),
            "--repo-dir", str(repo_dir),
        ]
    flag = {"sweep": "--workspace"}.get(loop.name, "--repo-dir")
    return [*base, loop.name, flag, str(repo_dir)]


# --------------------------------------------------------------------------
# The trigger: a machine-local LaunchAgent
# --------------------------------------------------------------------------

LAUNCH_AGENT_LABEL = "ai.venture.studio.loops"

#: A label may become a filename in `~/Library/LaunchAgents` and a launchd
#: service name, so it is checked rather than trusted: no slashes, no spaces,
#: nothing that could write outside the directory it is joined to.
_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _label(label: str | None) -> str:
    """The agent label, defaulted and validated.

    One label is one scheduled job. A second workspace needs a second label:
    installing over the first one would silently retarget it, and the
    operator would discover the loss of the original schedule only by
    noticing nothing had run — the failure this whole module exists to end.
    """
    name = (label or "").strip() or LAUNCH_AGENT_LABEL
    if not _LABEL_RE.fullmatch(name):
        raise CadenceError(
            f"invalid LaunchAgent label {name!r} — use reverse-DNS form, "
            f"e.g. {LAUNCH_AGENT_LABEL}.bench"
        )
    return name


def agent_plist_path(label: str | None = None) -> pathlib.Path:
    return (
        pathlib.Path.home() / "Library" / "LaunchAgents"
        / f"{_label(label)}.plist"
    )


def agent_log_path(label: str | None = None) -> pathlib.Path:
    # The default agent keeps `loops.log` — it is named in a plist already
    # installed on the operator's machine, and renaming it here would point
    # the running job at a file nothing else refers to.
    name = _label(label)
    stem = "loops" if name == LAUNCH_AGENT_LABEL else name.rsplit(".", 1)[-1]
    return (
        pathlib.Path.home() / "Library" / "Logs" / "ai-venture-studio"
        / f"{stem}.log"
    )


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
    # The alert's destination, as a pointer to a file. The URL itself is a
    # credential (see ENV_SECRETS below) — anyone holding it can post into
    # the channel — so it obeys the same rule as a model key.
    "AVS_DISCORD_WEBHOOK_FILE",
)

#: Raw secrets, never copied. Refused *by name* so the operator learns which
#: variable to convert to its `_FILE` form, rather than debugging a silent
#: 401 at nine in the morning.
ENV_SECRETS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AVS_DISCORD_WEBHOOK",
)


def scheduled_env(
    environ: dict | None = None, *, binary: str | None = None,
    label: str | None = None,
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
                f"value in a file and export {name}_FILE instead."
            )
    # `_KEY_FILE`, not `_FILE`: the alert webhook is also a `*_FILE` pointer,
    # and counting it here would let a workspace that can notify but cannot
    # authenticate pass as fully configured — the missing-credential warning
    # would vanish the day someone set up Discord.
    if not any(n.endswith("_KEY_FILE") for n in env):
        warnings.append(
            "No *_KEY_FILE pointer found, so the scheduled run may reach its "
            "provider without a credential. Loops that need one will fail into "
            f"{agent_log_path(label)}."
        )
    return env, warnings


def render_plist(
    workspace: str | pathlib.Path, *, executable: str | None = None,
    hour: int = 9, minute: int = 0, env: dict[str, str] | None = None,
    notify: bool = False, only=None, label: str | None = None,
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
    name = _label(label)
    log = agent_log_path(name)
    wanted = _selected(only)
    plan = {
        "Label": name,
        "ProgramArguments": [
            binary, "cadence", "--repo-dir", str(root), "--run-due",
            # Spelled out rather than left implicit, for the same reason as
            # --notify below: what the unwatched job does has to be readable
            # in the file, not inferred from the directory it points at.
            *(["--only", ",".join(sorted(wanted))] if wanted else []),
            # The flag is in the plist rather than inferred from the presence
            # of a webhook: the scheduled run is the one nobody watches, so
            # what it does has to be readable in the file itself.
            *(["--notify"] if notify else []),
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
        env if env is not None else scheduled_env(binary=binary, label=name)[0]
    )
    return plistlib.dumps(plan, sort_keys=True)


def install_agent(
    workspace: str | pathlib.Path, *, executable: str | None = None,
    hour: int = 9, minute: int = 0, load: bool = True,
    plist_path: pathlib.Path | None = None, notify: bool = False,
    only=None, label: str | None = None,
) -> dict:
    """Write the LaunchAgent, and by default ask launchd to load it.

    Returns what was done rather than printing it, so the CLI owns the words
    and a test owns the facts. With `load=False` the plist is written and the
    exact `launchctl` line is returned instead of run — the trigger is armed
    by a human, which is the same posture every other automation in this
    system takes.

    `only` + `label` are how a second workspace gets its own schedule without
    disturbing the first: the framework checkout runs the bench and nothing
    else, the product workspace runs compound and sweep and never sees a
    bench it has no cases for.
    """
    if sys.platform != "darwin" and plist_path is None:
        raise CadenceError(
            "LaunchAgents are macOS-only. On Linux, run "
            "`avs cadence --run-due` from cron or a systemd timer — the "
            "check itself is portable."
        )
    name = _label(label)
    root = pathlib.Path(workspace).resolve()
    # Whatever is being scheduled has to be readable here *now*. For a
    # filtered install that check is exact — `assess` raises if the named
    # loop is not tracked in this directory — and it is the better check, so
    # it replaces the .mas/ heuristic rather than adding to it.
    if only:
        assess(root, only=only)
    elif not (root / ".mas").is_dir():
        raise CadenceError(
            f"{root} has no .mas/ — the loops read their state from there, so "
            "a scheduler pointed here would find nothing to do. Point "
            "--repo-dir at the workspace the loops actually run in."
        )
    path = plist_path or agent_plist_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    agent_log_path(name).parent.mkdir(parents=True, exist_ok=True)
    binary = executable or shutil.which("avs") or sys.executable
    env, warnings = scheduled_env(binary=binary, label=name)
    body = render_plist(
        root, executable=executable, hour=hour, minute=minute, env=env,
        notify=notify, only=only, label=name,
    )
    path.write_bytes(body)
    if notify:
        from ai_venture_studio import notify as _notify

        # launchd sets HOME, so the saved default file is reachable from the
        # scheduled run with nothing carried in the plist at all — it counts
        # as configured. Said at install time either way, because the
        # alternative is finding out on the morning the alert mattered, and
        # the point of the alert is that nobody reads the log that failure
        # would land in.
        reachable = any(k.startswith("AVS_DISCORD_WEBHOOK") for k in env) or (
            _notify.default_webhook_path().exists()
        )
        if not reachable:
            warnings.append(
                "--notify is armed but there is no webhook to send through. "
                "Run `avs cadence --set-webhook <url>` (or export "
                "AVS_DISCORD_WEBHOOK_FILE and re-run --install), or the "
                "scheduled alert will fail every morning."
            )
    command = ["launchctl", "bootstrap", f"gui/{_uid()}", str(path)]
    result = {
        "plist": str(path),
        "label": name,
        "schedule": f"daily {hour:02d}:{minute:02d}",
        "workspace": str(root),
        "log": str(agent_log_path(name)),
        "loaded": False,
        "command": " ".join(command),
        "env_keys": sorted(k for k in env if k != "PATH"),
        "warnings": warnings,
        "notify": bool(notify),
        "loops": sorted(_selected(only) or ()),
    }
    if not load:
        return result
    # A previous copy must be removed first or bootstrap refuses; a failure
    # here is not an error, since nothing was loaded to remove.
    subprocess.run(  # noqa: S603 — argv list, never a shell
        ["launchctl", "bootout", f"gui/{_uid()}/{name}"],
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
    runner=None, label: str | None = None,
) -> SchedulerBuild:
    """Read the installed plist and ask its binary which version it is.

    `runner` is injected so the probe is a unit test rather than a subprocess:
    it takes an argv list and returns an object with `returncode` and `stdout`.
    """
    from ai_venture_studio import __version__

    path = plist_path or agent_plist_path(label)
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


def uninstall_agent(
    plist_path: pathlib.Path | None = None, *, label: str | None = None
) -> dict:
    """Remove the LaunchAgent. Absent is success — uninstall is idempotent."""
    name = _label(label)
    path = plist_path or agent_plist_path(name)
    subprocess.run(  # noqa: S603 — argv list, never a shell
        ["launchctl", "bootout", f"gui/{_uid()}/{name}"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    existed = path.exists()
    if existed:
        path.unlink()
    return {"plist": str(path), "label": name, "removed": existed}


def _uid() -> int:
    import os

    return os.getuid()
