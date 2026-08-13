"""Live-cycle state (the v3.0.0 design gate's instrument).

The v3.0.0 release bar is one product loop run end to end, ending in a
real recorded kill-or-pivot decision at Gate PL5 (README roadmap; doc 22
§65). Everything needed to *run* that loop already ships — what was
missing was a way to answer "where is the cycle, and what is left" without
a human re-reading eight YAML files and trusting their own summary.

This module reads the artifacts the stages already write and reports:

- which outer-loop stages have landed (P0 opportunity … P5 portfolio),
- which human gates carry recorded approvals (PL1, PL2, PL3, PL5),
- whether the v3.0.0 criteria are met, per criterion, with the reason.

It states, never decides. In particular the gate is met **only** when a
PL5 evaluation records an actual human kill-or-pivot decision: a cycle
where nothing fired and 'continue' stayed legal is explicitly *not* the
gate, because no decision was made (that is exactly what
`launch/gate-pl5-evaluation.yaml` says about loop 1). Nothing here can
mark the gate met on the system's own say-so.
"""

from __future__ import annotations

import pathlib

import yaml
from pydantic import BaseModel, Field

# (stage id, human label, artifact filenames that evidence it)
STAGE_ARTIFACTS: list[tuple[str, str, tuple[str, ...]]] = [
    ("P0", "opportunity sensing", ("opportunity-report.yaml", "signals.yaml")),
    ("P1", "market & viability", ("market-report.yaml", "market.yaml")),
    ("P2", "product definition (PRD)", ("prd.yaml", "prd.md")),
    ("P3", "launch & growth", ("post.md", "experiment.yaml")),
    ("P4", "product evidence", ("evidence-report.yaml", "experiment-run.yaml")),
    ("P5", "portfolio prioritization", ("gate-pl5-evaluation.yaml",)),
]

GATE_ARTIFACTS: list[tuple[str, str, tuple[str, ...]]] = [
    ("PL1", "market approval", ("gate-pl1.yaml", "gate-pl1-approval.yaml")),
    ("PL2", "PRD → Discovery handoff", ("gate-pl2.yaml", "p2-handoff.yaml")),
    ("PL3", "scoped publish approval", ("gate-pl3-approval.yaml", "gate-pl3-pypi.yaml")),
    ("PL5", "kill / pivot / continue", ("gate-pl5-evaluation.yaml",)),
]

# A PL5 record whose decision is one of these closes the v3.0.0 gate.
DECISIVE = {"kill", "pivot"}


class StageState(BaseModel):
    id: str
    label: str
    present: bool
    artifacts: list[str] = Field(default_factory=list)


class GateCriterion(BaseModel):
    id: str
    requirement: str
    met: bool
    detail: str


class CapabilityProgress(BaseModel):
    """The kill-criterion axis (PRD O-L2): product-bench capability.

    It was the second of two until v0.81.0. The first — weekly maintenance
    attention — was withdrawn with the loop that collected it (ADR-033),
    leaving the axis whose series the machine already gathers by itself.
    """

    tracked: bool = False
    streak: int = 0
    needed: int = 0
    fires: bool = False
    detail: str = ""


class CycleState(BaseModel):
    root: str
    entry_stage: str = "P0"
    entry_reason: str = ""
    stages: list[StageState]
    gates: list[StageState]
    pl5_decision: str | None = None  # kill | pivot | continue | None
    pl5_requires_human_decision: bool = False
    criteria: list[GateCriterion] = Field(default_factory=list)
    capability: CapabilityProgress | None = None
    next_action: str = ""

    @property
    def design_gate_met(self) -> bool:
        return bool(self.criteria) and all(c.met for c in self.criteria)


def read_capability(repo_dir: str | pathlib.Path) -> CapabilityProgress:
    """The capability axis, joined into the cycle report so an operator does
    not have to run two commands and join them mentally."""
    from ai_venture_studio.bench_criterion import evaluate

    state = evaluate(repo_dir)
    return CapabilityProgress(
        tracked=bool(state.runs_considered), streak=state.streak,
        needed=state.needed, fires=state.fires, detail=state.detail,
    )


def _found(root: pathlib.Path, names: tuple[str, ...]) -> list[str]:
    hits = []
    for name in names:
        for path in (root / name, root / "product" / name):
            if path.exists():
                hits.append(str(path.relative_to(root)))
    return hits


def _load(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def read_cycle(root: str | pathlib.Path) -> CycleState:
    """Read one cycle's state from its artifact directory (e.g. `launch/`
    for this repo's own loop, or a project's `.mas/product/`)."""
    base = pathlib.Path(root)
    # A cycle may DECLARE where it entered the loop — a product that already
    # exists starts at P2, not P0 (this repo's own launch cycle does). The
    # declaration is a human artifact with a stated reason; without one the
    # cycle is held to the full P0-P5 span.
    declared = _load(base / "cycle.yaml").get("cycle") or {}
    entry_stage = str(declared.get("entry_stage", "P0")).upper()
    known = [sid for sid, _, _ in STAGE_ARTIFACTS]
    if entry_stage not in known:
        raise ValueError(
            f"{base / 'cycle.yaml'}: entry_stage {entry_stage!r} is not one of {known}"
        )
    entry_reason = str(declared.get("entry_reason", ""))
    if entry_stage != "P0" and not entry_reason:
        raise ValueError(
            f"{base / 'cycle.yaml'}: entering at {entry_stage} skips earlier "
            "stages — an entry_reason is required, so the skip is a recorded "
            "decision rather than a silent gap"
        )
    in_scope = set(known[known.index(entry_stage):])

    stages = [
        StageState(id=sid, label=label, present=bool(found), artifacts=found)
        for sid, label, names in STAGE_ARTIFACTS
        for found in [_found(base, names)]
    ]
    gates = [
        StageState(id=gid, label=label, present=bool(found), artifacts=found)
        for gid, label, names in GATE_ARTIFACTS
        for found in [_found(base, names)]
    ]

    pl5_decision: str | None = None
    requires_human = False
    fired: list = []
    pl5_path = next(
        (base / a for g in gates if g.id == "PL5" for a in g.artifacts), None
    )
    if pl5_path is not None:
        record = _load(pl5_path).get("evaluation") or {}
        requires_human = bool(record.get("requires_human_decision"))
        fired = list(record.get("fired") or [])
        decision = record.get("human_decision") or record.get("decision")
        if isinstance(decision, dict):
            decision = decision.get("choice")
        if decision:
            pl5_decision = str(decision).lower()

    # The criterion's own series lives at the repo root, not in the cycle
    # directory (launch/../benchmarks/results/).
    repo_root = base.parent if base.name else base
    capability = read_capability(repo_root)
    scoped = [s for s in stages if s.id in in_scope]
    missing_scoped = [s for s in scoped if not s.present]
    span = f"{entry_stage}-P5" if entry_stage != "P0" else "P0-P5"
    criteria = [
        GateCriterion(
            id="V3-1",
            requirement=f"every in-scope outer-loop stage ({span}) has a landed artifact",
            met=not missing_scoped,
            detail=(
                f"all {len(scoped)} in-scope stage(s) present"
                + (f"; entered at {entry_stage}: {entry_reason}" if entry_reason else "")
                if not missing_scoped
                else "missing: " + ", ".join(s.id for s in missing_scoped)
            ),
        ),
        GateCriterion(
            id="V3-2",
            requirement="a Gate PL5 evaluation exists and was run mechanically",
            met=pl5_path is not None,
            detail=(
                f"evaluation at {pl5_path.name}" if pl5_path else "no PL5 evaluation yet"
            ),
        ),
        GateCriterion(
            id="V3-3",
            requirement="the PL5 record carries a human kill-or-pivot decision",
            met=pl5_decision in DECISIVE,
            detail=_decision_detail(pl5_path, pl5_decision, requires_human, fired,
                                    capability),
        ),
    ]
    return CycleState(
        root=str(base), entry_stage=entry_stage, entry_reason=entry_reason,
        stages=stages, gates=gates,
        pl5_decision=pl5_decision,
        pl5_requires_human_decision=requires_human,
        criteria=criteria,
        capability=capability if capability.tracked else None,
        next_action=_next_action(scoped, criteria, requires_human, pl5_decision,
                                 capability),
    )


def _decision_detail(pl5_path, decision, requires_human, fired,
                     capability=None) -> str:
    if decision in DECISIVE:
        return f"recorded decision: {decision}"
    if decision == "continue":
        return (
            "recorded decision is 'continue' — the gate needs a kill or a "
            "pivot, so the loop continues to the next cycle"
        )
    if pl5_path is None:
        return "no PL5 evaluation yet"
    if requires_human:
        return (
            f"{len(fired)} criterion/criteria fired and a human decision is "
            "DUE but unrecorded — record it in the evaluation "
            "(`human_decision: kill|pivot|continue`)"
        )
    quiet = (
        "no criterion fired, so no decision is due: 'continue' stays legal "
        "because nothing fired, not because anyone chose it — the gate is "
        "not met by a quiet cycle"
    )
    if capability is not None and capability.tracked:
        # Say how far away the axis actually is, rather than leaving the
        # operator to run another command and join the reports by hand.
        return quiet + ". " + capability.detail
    return quiet


def _next_action(stages, criteria, requires_human, decision,
                 capability=None) -> str:
    missing = [s for s in stages if not s.present]
    if missing:
        first = missing[0]
        return (
            f"run {first.id} ({first.label}) — it has no artifact yet; "
            f"`autoproduct {_command_for(first.id)}`"
        )
    if requires_human and decision not in DECISIVE and decision != "continue":
        return (
            "a kill criterion fired: record the human decision in the PL5 "
            "evaluation (invariant 14.20 — a fired criterion cannot close "
            "without one)"
        )
    if decision in DECISIVE:
        return "v3.0.0 design gate met — the loop closed on a real decision"
    if capability is not None and capability.tracked and capability.fires:
        return (
            "the capability criterion HAS FIRED (product-bench below its "
            "floors) — record the human decision in the PL5 evaluation "
            "(invariant 14.20)"
        )
    return (
        "wait for the next PL5 evaluation window: the criteria need data "
        "that does not exist yet, and a criterion cannot be declared safe "
        "on uncollected data any more than it can fire on it"
    )


def _command_for(stage_id: str) -> str:
    return {
        "P0": "opportunity <signals.yaml>",
        "P1": "market <candidate> --evidence <probes.yaml>",
        "P2": "prd",
        "P3": "preregister / publish under a scoped Gate PL3 approval",
        "P4": "evidence <events.yaml> --metric ... --cohort-start ...",
        "P5": "review the kill criteria and record the PL5 evaluation",
    }.get(stage_id, "loop")
