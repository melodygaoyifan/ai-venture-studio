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

This module states; it never decides. A fired criterion demands a recorded
human decision at Gate PL5 (invariant 14.20).
"""

from __future__ import annotations

import pathlib

import yaml
from pydantic import BaseModel, Field

RESULTS_DIR = pathlib.Path("benchmarks") / "results"
BUILD_FLOOR = 0.60
PROBE_FLOOR = 0.50
CONSECUTIVE_RUNS_TO_FIRE = 2


class BenchRun(BaseModel):
    path: str
    build_rate: float
    probe_pass_rate: float
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
        return self.build_rate < BUILD_FLOOR or self.probe_pass_rate < PROBE_FLOOR

    def summary(self) -> str:
        return (
            f"{pathlib.Path(self.path).name}: build {self.build_rate:.0%}, "
            f"probes {self.probe_pass_rate:.0%}"
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


class BenchCriterionError(RuntimeError):
    pass


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _scan(repo_dir: str | pathlib.Path) -> tuple[list[BenchRun], list[str]]:
    """The series and the attempts, read in one pass so they cannot disagree.

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
    if not root.is_dir():
        return runs, aborted
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
        # Absent OR null. A run whose build axis was empty writes the keys
        # with `null` (a rate over no cases is not a rate), and such a run
        # must not reach `below_floor` — it made no claim about build
        # capability, and reading it as 0% would advance the streak toward a
        # criterion that asks a human to consider killing the project. The
        # null case used to be caught only by `float(None)` raising into the
        # handler below, which is accidental correctness, not a rule.
        if data.get("build_rate") is None or data.get("probe_pass_rate") is None:
            continue
        rates = data.get("rates") if isinstance(data.get("rates"), dict) else {}
        try:
            runs.append(BenchRun(
                path=rel,
                build_rate=float(data["build_rate"]),
                probe_pass_rate=float(data["probe_pass_rate"]),
                clean_review_rate=(
                    float(data["clean_review_rate"])
                    if data.get("clean_review_rate") is not None else None
                ),
                cases_measured=_as_int(rates.get("cases_measured")),
                cases_total=_as_int(rates.get("cases_total")),
            ))
        except (TypeError, ValueError):
            continue
    return runs, aborted


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


def evaluate(repo_dir: str | pathlib.Path) -> BenchCriterionState:
    """Has the capability criterion fired? Mechanically, from the ledger."""
    runs, aborted = _scan(repo_dir)
    if not runs:
        return BenchCriterionState(
            aborted_skipped=aborted,
            detail="no recorded bench runs — the criterion cannot fire on "
                   "data that does not exist, and cannot be declared safe on "
                   "it either (run `avs product-bench`)",
        )
    # Streak over the most recent runs, newest last.
    streak = 0
    for run in runs:
        streak = streak + 1 if run.below_floor else 0
    fires = streak >= CONSECUTIVE_RUNS_TO_FIRE
    recent = runs[-CONSECUTIVE_RUNS_TO_FIRE:]
    if fires:
        detail = (
            f"{streak} consecutive run(s) below the floors "
            f"(build {BUILD_FLOOR:.0%} / probes {PROBE_FLOOR:.0%}) — the "
            "capability criterion HAS FIRED; Gate PL5 requires a recorded "
            "human decision (invariant 14.20). Latest: "
            + "; ".join(r.summary() for r in recent)
        )
    else:
        remaining = CONSECUTIVE_RUNS_TO_FIRE - streak
        detail = (
            f"{streak}/{CONSECUTIVE_RUNS_TO_FIRE} consecutive run(s) below the "
            f"floors (build {BUILD_FLOOR:.0%} / probes {PROBE_FLOOR:.0%}); "
            f"{remaining} more would fire it. Latest: "
            + "; ".join(r.summary() for r in recent)
        )
    return BenchCriterionState(
        runs_considered=recent, streak=streak, fires=fires, detail=detail,
        aborted_skipped=aborted,
    )


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
