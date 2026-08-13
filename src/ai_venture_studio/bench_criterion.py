"""The capability kill criterion (launch PRD O-L2, doc 25 §76.4).

Added 2026-07-27 by a recorded human choice as the second of two axes, and
since v0.81.0 the only one — the other measured weekly maintenance hours a
person had to type in, and was withdrawn with them (ADR-033). Its whole point
was always that its series ALREADY EXISTS: `benchmarks/results/*.yaml` carries
a build / probe / clean rate per weekly run, so this criterion can fire on the
next run without anyone being asked anything.

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


class BenchCriterionError(RuntimeError):
    pass


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def load_runs(repo_dir: str | pathlib.Path) -> list[BenchRun]:
    """Every recorded run, oldest first, by filename (they are timestamped).

    A malformed or rate-less file is skipped rather than fatal: the tracked
    scoreboard also holds notes and reconstructions, and one unreadable file
    must not blind the criterion.
    """
    root = pathlib.Path(repo_dir) / RESULTS_DIR
    runs: list[BenchRun] = []
    if not root.is_dir():
        return runs
    for path in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        if "build_rate" not in data or "probe_pass_rate" not in data:
            continue
        rates = data.get("rates") if isinstance(data.get("rates"), dict) else {}
        try:
            runs.append(BenchRun(
                path=str(path.relative_to(pathlib.Path(repo_dir))),
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
    return runs


def evaluate(repo_dir: str | pathlib.Path) -> BenchCriterionState:
    """Has the capability criterion fired? Mechanically, from the ledger."""
    runs = load_runs(repo_dir)
    if not runs:
        return BenchCriterionState(
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
        runs_considered=recent, streak=streak, fires=fires, detail=detail
    )
