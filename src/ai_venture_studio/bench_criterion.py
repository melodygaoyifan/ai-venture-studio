"""The capability kill criterion (launch PRD O-L2, doc 25 §76.4).

Added 2026-07-27 by a recorded human choice as the second of two axes, and
since v0.81.0 the only one — the other measured weekly maintenance hours a
person had to type in, and was withdrawn with them (ADR-033). Its whole point
was always that its series ALREADY EXISTS: `benchmarks/results/result-*.yaml`
carries a build / probe / clean rate per weekly run, so this criterion can fire
on the next run without anyone being asked anything.

    build rate < 60% OR probe pass rate < 50%, for 2 consecutive runs
        → the capability claim is not holding → Gate PL5

The floors are read off the observed distribution, not chosen: runs 4–5 sat
at 8–33% build, runs 6–9 climbed 42–72%, runs 10–11 hold 74–75%. Crossing
60/50 means regressing into territory the system has already climbed out of.

Two runs, not one, because at n=4 real-product cases a single run is noise.

Since v0.83.0 a run also records how much of the bench it measured, and this
module carries that into the sentence a human reads at Gate PL5 (ADR-035):
run 12 reported 65% probes only because a case that never ran was averaged in
as a zero, and a rate is not evidence without its denominator.

Since v0.105.0 a result also records WHICH PROVIDER produced it, and a run
measured against a fabricating one is not a reading of this system (ADR-056).

This module states; it never decides. A fired criterion demands a recorded
human decision at Gate PL5 (invariant 14.20).
"""

from __future__ import annotations

import pathlib

import yaml
from pydantic import BaseModel, Field

from ai_venture_studio.providers.base import is_simulated

RESULTS_DIR = pathlib.Path("benchmarks") / "results"
BUILD_FLOOR = 0.60
PROBE_FLOOR = 0.50
CONSECUTIVE_RUNS_TO_FIRE = 2


class BenchRun(BaseModel):
    path: str
    build_rate: float
    #: None when the run built nothing anywhere, so there was no product to
    #: probe (ADR-061). Optional rather than skip-the-run: a run where every
    #: case failed to build is the WORST reading the series can produce, and
    #: dropping it for lacking a probe number would make the criterion blind
    #: to exactly the outcome it exists to catch. It is judged on the floor
    #: it does have.
    probe_pass_rate: float | None = None
    clean_review_rate: float | None = None
    # Runs from v0.83.0 on record how much of the bench they actually
    # measured (ADR-035). Older files carry neither, and are read as
    # complete — which is what they were.
    cases_measured: int | None = None
    cases_total: int | None = None

    @property
    def partial(self) -> bool:
        return (
            self.cases_measured is not None
            and self.cases_total is not None
            and self.cases_measured < self.cases_total
        )

    @property
    def below_floor(self) -> bool:
        # A missing probe rate is not a passing one and not a failing one —
        # the floor it has no reading for simply does not apply. The build
        # floor still does, and a run with no probe reading is almost always
        # a run that is already through it.
        return self.build_rate < BUILD_FLOOR or (
            self.probe_pass_rate is not None and self.probe_pass_rate < PROBE_FLOOR
        )

    def summary(self) -> str:
        return (
            f"{pathlib.Path(self.path).name}: build {self.build_rate:.0%}, "
            + (f"probes {self.probe_pass_rate:.0%}"
               if self.probe_pass_rate is not None
               else "probes not measured (nothing built to probe)")
            + (f", clean {self.clean_review_rate:.0%}"
               if self.clean_review_rate is not None else "")
            # A human at Gate PL5 is deciding whether to kill the project on
            # these two numbers. They must not have to open the file to find
            # out that one of them was averaged over three cases, not four.
            + (f" (over {self.cases_measured} of {self.cases_total} cases)"
               if self.partial else "")
        )


class BenchCriterionState(BaseModel):
    build_floor: float = BUILD_FLOOR
    probe_floor: float = PROBE_FLOOR
    needed: int = CONSECUTIVE_RUNS_TO_FIRE
    runs_considered: list[BenchRun] = Field(default_factory=list)
    streak: int = 0  # consecutive most-recent runs below a floor
    fires: bool = False
    detail: str = ""
    #: Attempts the environment cut short, named rather than silently absent.
    #: They are not runs of the series and never reach a floor, but a reader
    #: comparing this ledger to the directory must be able to see why a file
    #: they can see is not counted.
    aborted_skipped: list[str] = Field(default_factory=list)
    #: Runs measured against a provider that fabricates its answers. Named for
    #: the same reason as the aborts above: the failure mode this list exists
    #: to prevent is a file that is in the directory, absent from the ledger,
    #: and unexplained. A simulated run is not resumable and not finishable —
    #: it is not a reading at all, and re-running it will not make it one.
    simulated_skipped: list[str] = Field(default_factory=list)
    #: Runs `--limit` stopped short of the suite. Named for the same reason
    #: as the two lists above, and the reason differs from both in the way
    #: the reader needs: an abort is worth going back to finish, a simulated
    #: run is worth nothing, and a slice is worth exactly what it measured —
    #: its checkpoints are banked, and the run that closes the suite will
    #: reuse them instead of re-paying (ADR-052/066).
    truncated_skipped: list[str] = Field(default_factory=list)


class BenchCriterionError(RuntimeError):
    pass


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _scan(
    repo_dir: str | pathlib.Path,
) -> tuple[list[BenchRun], list[str], list[str], list[str]]:
    """The series and what it excludes, read in one pass so they cannot disagree.

    One scan with one filter rather than two functions that each glob: two
    readers of the same directory drift, and the thing they would drift about
    is which files the kill criterion counts (ADR-051).

    `result-*.yaml`, not `*.yaml`. The docstring below this one has always
    promised "oldest first, by filename (they are timestamped)", and that
    holds only while every name shares a prefix. ADR-052 added
    `aborted-<date>-<reason>.yaml` beside them, and `a` sorts before `r` — so
    the NEWEST run on disk was being ordered as the OLDEST, and the "latest
    two runs" window silently excluded it. Narrowing the glob restores the
    invariant the docstring states, and stops any future notes.yaml in a
    tracked directory from being parsed as a capability reading.
    """
    root = pathlib.Path(repo_dir) / RESULTS_DIR
    runs: list[BenchRun] = []
    aborted: list[str] = []
    simulated: list[str] = []
    truncated: list[str] = []
    if not root.is_dir():
        return runs, aborted, simulated, truncated
    # Both names, because the two guards catch different mistakes and each
    # would be silent about the other's. The glob keeps `aborted-*.yaml` out
    # of the SERIES (and fixes its ordering); walking those files anyway is
    # what keeps them out of the series without making them disappear —
    # excluding a file and never mentioning it is the failure this list
    # exists to prevent. The content check below then catches an abort
    # written under a `result-` name, which is what a future writer is most
    # likely to get wrong.
    aborted.extend(
        str(p.relative_to(pathlib.Path(repo_dir)))
        for p in sorted(root.glob("aborted-*.yaml"))
    )
    for path in sorted(root.glob("result-*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        rel = str(path.relative_to(pathlib.Path(repo_dir)))
        # An interrupted attempt is not a run of the series. `save_summary`
        # writes `aborted:` ABOVE the rates for precisely this reason — in its
        # own words, "four cases failed" and "this run never got to ask them"
        # are different findings whose percentages look identical — and this,
        # the one reader where the distinction decides something, did not
        # look. Run 17 died on credit exhaustion after one case and sat in the
        # ledger at build 100% over 1 of 5; it was harmless only because it
        # scored well. Inverted, an exhausted billing account would have
        # advanced a streak that asks a human to consider killing the project.
        # ADR-052 made such a run resumable, which is the same statement: it
        # is not final, so it is not a reading.
        if data.get("aborted"):
            aborted.append(rel)
            continue
        # A run measured against a fabricating provider is not a reading of
        # this system's capability; it is a reading of a regex table. The file
        # is otherwise byte-identical in shape to a real one — same rates, same
        # denominators, same version — so nothing downstream could ever have
        # told them apart. `--provider mock` is a documented option on the
        # command that writes here, which is what makes this reachable rather
        # than theoretical.
        #
        # A file with no `provider:` key is read as real. Every result written
        # before v0.105.0 lacks the field and every one of them was a genuine
        # run against anthropic; guessing the other way would silently drop
        # eleven real capability readings out of the series.
        if is_simulated(data.get("provider")):
            simulated.append(rel)
            continue
        # A run stopped short of the suite by `--limit`. Not an abort — the
        # environment was fine and nothing is owed — and not simulated: the
        # cases it DID measure were measured for real, at full price, against
        # the real provider. It is simply not a reading of the suite, and this
        # ledger is a series of readings of the suite.
        #
        # This is the reachable half of the same shape ADR-056 closed. Buying
        # run 19 a case at a time is the obvious way to run a five-hour bench
        # on an account that cannot afford five hours at once, and before
        # ADR-066 each slice wrote a file claiming to be complete over a
        # truncated denominator. A slice that happened to contain the case
        # that builds nothing reads as build 0% over 1 of 1 — below floor,
        # not partial, not excluded — and two of those fire a criterion whose
        # only remedy is a human deciding whether to kill the project.
        # `only_cases` is the same purchase cut the other way — by name
        # instead of by count — and gets the same refusal for the same
        # reason: a run that never asked two of the five cases has not
        # read the suite, however real the three it did ask were.
        if data.get("limited_to") is not None or data.get("only_cases"):
            truncated.append(rel)
            continue
        # Absent OR null. A run whose build axis was empty writes the keys
        # with `null` (a rate over no cases is not a rate), and such a run
        # must not reach `below_floor` — it made no claim about build
        # capability, and reading it as 0% would advance the streak toward a
        # criterion that asks a human to consider killing the project. The
        # null case used to be caught only by `float(None)` raising into the
        # handler below, which is accidental correctness, not a rule.
        #
        # `probe_pass_rate` is deliberately NOT part of this test any more
        # (ADR-061). It is null both when the build axis was empty — caught
        # by `build_rate` above — and when the run built nothing to probe,
        # and skipping the second case would hide the worst run the series
        # can produce. `BenchRun` carries it as optional and judges the floor
        # it has.
        if data.get("build_rate") is None:
            continue
        rates = data.get("rates") if isinstance(data.get("rates"), dict) else {}
        try:
            runs.append(BenchRun(
                path=rel,
                build_rate=float(data["build_rate"]),
                probe_pass_rate=(
                    float(data["probe_pass_rate"])
                    if data.get("probe_pass_rate") is not None else None
                ),
                clean_review_rate=(
                    float(data["clean_review_rate"])
                    if data.get("clean_review_rate") is not None else None
                ),
                cases_measured=_as_int(rates.get("cases_measured")),
                cases_total=_as_int(rates.get("cases_total")),
            ))
        except (TypeError, ValueError):
            continue
    return runs, aborted, simulated, truncated


def load_runs(repo_dir: str | pathlib.Path) -> list[BenchRun]:
    """Every recorded run, oldest first, by filename (they are timestamped).

    A malformed or rate-less file is skipped rather than fatal: the tracked
    scoreboard also holds notes and reconstructions, and one unreadable file
    must not blind the criterion.
    """
    return _scan(repo_dir)[0]


def aborted_runs(repo_dir: str | pathlib.Path) -> list[str]:
    """Attempts the environment cut short — excluded from the series above.

    Reported rather than dropped in silence. A run that vanishes from the
    ledger without a word is indistinguishable from a run that never
    happened, and the reason this one is absent (an abort, therefore
    resumable) is the reason a human might want to go finish it.
    """
    return _scan(repo_dir)[1]


def simulated_runs(repo_dir: str | pathlib.Path) -> list[str]:
    """Result files produced by a fabricating provider — never in the series.

    Reported, like the aborts, because the alternative is a file a reader can
    see in the directory and cannot find in the ledger. Unlike an abort there
    is nothing to go finish here: the run is complete, and its numbers are
    real numbers about the wrong thing.
    """
    return _scan(repo_dir)[2]


def truncated_runs(repo_dir: str | pathlib.Path) -> list[str]:
    """Result files `--limit` stopped short of the suite — never in the series.

    Reported, like the two above, and for the third distinct reason: this one
    IS resumable, cheaply, and the resume is the point. Its measured cases are
    banked as checkpoints, so the run that finally covers the suite pays only
    for what is left (ADR-052). A slice is how an expensive bench gets bought
    on an account that cannot afford it in one sitting; it is not how the
    bench gets read.
    """
    return _scan(repo_dir)[3]


def evaluate(repo_dir: str | pathlib.Path) -> BenchCriterionState:
    """Has the capability criterion fired? Mechanically, from the ledger."""
    runs, aborted, simulated, truncated = _scan(repo_dir)
    # Build the state FIRST and read the floors back off it, rather than
    # interpolating the module constants into a message about a state that
    # separately carries its own copy. The two agreed only by coincidence:
    # `build_floor` and `probe_floor` had no reader at all (ADR-060), so a
    # change to either field would have left every sentence below still
    # printing the old number, and the ledger's own record of the bar it
    # judged against would have been the half nobody saw.
    state = BenchCriterionState(
        aborted_skipped=aborted,
        simulated_skipped=simulated,
        truncated_skipped=truncated,
    )
    bars = f"build {state.build_floor:.0%} / probes {state.probe_floor:.0%}"
    if not runs:
        state.detail = (
            f"no recorded bench runs — the criterion cannot fire on data that "
            f"does not exist, and cannot be declared safe on it either (run "
            f"`avs product-bench`). The floors it would judge against: {bars}."
        )
        return state
    # Streak over the most recent runs, newest last.
    streak = 0
    for run in runs:
        streak = streak + 1 if run.below_floor else 0
    fires = streak >= state.needed
    recent = runs[-state.needed:]
    if fires:
        detail = (
            f"{streak} consecutive run(s) below the floors ({bars}) — the "
            "capability criterion HAS FIRED; Gate PL5 requires a recorded "
            "human decision (invariant 14.20). Latest: "
            + "; ".join(r.summary() for r in recent)
        )
    else:
        remaining = state.needed - streak
        detail = (
            f"{streak}/{state.needed} consecutive run(s) below the floors "
            f"({bars}); {remaining} more would fire it. Latest: "
            + "; ".join(r.summary() for r in recent)
        )
    state.runs_considered = recent
    state.streak = streak
    state.fires = fires
    state.detail = detail
    return state


def movement(repo_dir: str | pathlib.Path) -> str:
    """How the newest run moved against the one before it, in points.

    No threshold and no verdict, deliberately. There is NO floor on the
    clean-review rate, and inventing one here would be adding an axis to the
    launch PRD's only kill criterion through the back door — that is a
    recorded human decision (doc 25 §76.4), not a constant this module gets
    to introduce because it would be convenient.

    Stating the move needs no such licence, and it is the single most
    informative sentence about a run: run 14 took clean reviews from 75% to
    38% while builds and probes went to 100%, and nobody was told anything
    at all, because every number involved was comfortably above its floor.
    """
    runs = load_runs(repo_dir)
    if len(runs) < 2:
        return ""
    now, before = runs[-1], runs[-2]
    parts: list[str] = []
    for label, new, old in (
        ("build", now.build_rate, before.build_rate),
        ("probes", now.probe_pass_rate, before.probe_pass_rate),
        ("clean", now.clean_review_rate, before.clean_review_rate),
    ):
        if new is None or old is None:
            continue
        parts.append(f"{label} {round((new - old) * 100):+d}pp")
    if not parts:
        return ""
    return f"vs {pathlib.Path(before.path).name}: " + ", ".join(parts)


def concern(repo_dir: str | pathlib.Path) -> str:
    """What a person needs told about this series, or "" when nothing does.

    THE FLOORS LIVE HERE AND NOWHERE ELSE. A caller that wanted to alert on a
    bad run could read `build_rate` and compare it against 0.60 itself — and
    from that moment there would be two definitions of "below the floor",
    drifting apart the first time either moved (ADR-038). This is the one.

    Deliberately NOT a verdict on the harness, and the distinction is the
    whole reason this function is separate from an exit code: **a run that
    measured everything and scored badly is a FINDING, not a failure.** It
    needs a person to LOOK; it does not need the machine to stop. ADR-035
    pinned that for `product-bench`'s exit code and it holds here — the
    tempting "consistency fix" of failing the scheduler on a low rate would
    turn every weak week into a broken scheduler.
    """
    state = evaluate(repo_dir)
    if not state.runs_considered:
        return ""
    newest = state.runs_considered[-1]
    if state.fires:
        return (
            f"the capability kill criterion HAS FIRED — {state.streak} "
            f"consecutive runs below the floors (build {BUILD_FLOOR:.0%} / "
            f"probes {PROBE_FLOOR:.0%}). Gate PL5 requires a recorded human "
            f"decision (invariant 14.20). Latest: {newest.summary()}"
        )
    if newest.below_floor:
        remaining = CONSECUTIVE_RUNS_TO_FIRE - state.streak
        return (
            f"below the floors (build {BUILD_FLOOR:.0%} / probes "
            f"{PROBE_FLOOR:.0%}) — {remaining} more consecutive run(s) would "
            f"fire the kill criterion. {newest.summary()}"
        )
    if newest.partial:
        return (
            f"the newest run did not measure the whole bench: "
            f"{newest.summary()}. The rates exclude what never ran, so this "
            f"measured less of the machine than the percentages look like."
        )
    return ""
