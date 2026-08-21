"""Doc 16 §40 deterministic parts: voter cascades (ADR-U10), the serial
merge queue (§38.2 rule 2), and the GEPA budget schema (§40.1, ADR-U11).

Cascades: a cheap screening pass may run first, but anything it flags —
or cannot judge — escalates to the full panel, and the ESCALATED set must
satisfy the heterogeneity floor (distinct model families). The cascade
saves money on clean diffs; it never lowers the bar on dirty ones.

Merge queue: serial admission, feature lanes before sweep (sweep is
lowest priority by ADR-U37), bounded by ci_concurrency_max.

GEPA: the proposer's BUDGET is config the harness enforces; the proposer
itself (an LLM optimization loop) is a recorded open item — a budget
schema without a spender is safe; a spender without a budget is not.
"""

from __future__ import annotations

import pathlib

import yaml
from pydantic import BaseModel, Field, field_validator

GEPA_FILE = "gepa.yaml"


#: The triggers a cascade policy may not drop. They are the module docstring's
#: invariant — "never lowers the bar on dirty ones" — and a policy that omits
#: one is refused rather than honored, on the same principle as
#: `automation.PolicyError`: never a silent disarm.
MANDATORY_TRIGGERS = ("finding", "blocked")
#: Every trigger `cascade_route` can actually act on. A policy naming anything
#: else is refused too — the failure this guards is precisely the one ADR-060
#: found here: `low_confidence` sat in the default tuple for its whole life
#: while `cascade_route` had no confidence input and could not have honored it.
KNOWN_TRIGGERS = (*MANDATORY_TRIGGERS, "low_confidence")


class CascadePolicy(BaseModel):
    screening_enabled: bool = False  # off by default: full panel is the default
    critics_min_distinct_families: int = 2  # the heterogeneity floor
    #: The two mandatory triggers, and ONLY those. `low_confidence` sat in
    #: this default from the day the field was written until ADR-060, naming a
    #: check `cascade_route` had no confidence input to perform — so the
    #: default policy advertised three triggers and ran two. It is opt-in now,
    #: and opting in obliges the caller to supply a confidence: a policy that
    #: asks for a check nothing feeds is the same defect one layer up.
    escalate_on: tuple[str, ...] = MANDATORY_TRIGGERS
    #: Below this, the screening pass is treated as not having judged. Only
    #: consulted when "low_confidence" is among the triggers.
    low_confidence_floor: float = 0.7

    @field_validator("escalate_on")
    @classmethod
    def _honorable(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(KNOWN_TRIGGERS))
        if unknown:
            raise ValueError(
                f"escalate_on names trigger(s) {unknown} that cascade_route "
                f"cannot act on; known triggers are {list(KNOWN_TRIGGERS)}. A "
                "trigger nothing reads is a policy that quietly does nothing."
            )
        missing = sorted(set(MANDATORY_TRIGGERS) - set(value))
        if missing:
            raise ValueError(
                f"escalate_on must include {missing}: a screening pass that "
                "found something, or could not judge, always escalates "
                "(§40 — the cascade saves money on clean diffs, it never "
                "lowers the bar on dirty ones). Set screening_enabled=false "
                "to turn cascades off; do not disarm them one trigger at a "
                "time."
            )
        return value


class CascadeDecision(BaseModel):
    escalate: bool
    reason: str
    #: Which trigger fired, or "" when nothing did. Named so a caller counting
    #: escalations can tell an expensive-but-correct cascade from one that is
    #: escalating on confidence it should be reporting instead.
    trigger: str = ""


def cascade_route(policy: CascadePolicy, *, screening_findings: int,
                  screening_blocked: bool,
                  screening_confidence: float | None = None) -> CascadeDecision:
    """Route a diff past the cheap pass, or escalate it to the full panel.

    Every branch below is gated on `policy.escalate_on`, which until ADR-060
    was decoration: the triggers were hard-coded here, so narrowing the tuple
    changed nothing and the `low_confidence` entry named a check this function
    had no input to perform. The validator now refuses a tuple that drops a
    mandatory trigger, so honoring the field cannot be used to disarm it.
    """
    if not policy.screening_enabled:
        return CascadeDecision(escalate=True, reason="cascades off: full panel",
                               trigger="disabled")
    if screening_blocked and "blocked" in policy.escalate_on:
        return CascadeDecision(escalate=True, reason="screening could not judge",
                               trigger="blocked")
    if screening_findings and "finding" in policy.escalate_on:
        return CascadeDecision(
            escalate=True,
            reason=f"{screening_findings} screening finding(s) — the cheap "
                   "pass saves money on clean diffs, never lowers the bar",
            trigger="finding")
    if "low_confidence" in policy.escalate_on:
        # Loud, not silent, and not a blanket escalate either. A policy that
        # opts into this trigger while the call site feeds it nothing is a
        # wiring mistake, and the two ways to paper over it are both worse
        # than saying so: passing it through as "clean" restores the exact
        # dead trigger ADR-060 removed, and escalating everything turns the
        # cascade off while looking like it is on.
        if screening_confidence is None:
            raise ValueError(
                "escalate_on includes 'low_confidence' but no "
                "screening_confidence was supplied — the policy asks for a "
                "check this call cannot perform. Pass the screening pass's "
                "confidence, or drop the trigger from the policy."
            )
        if screening_confidence < policy.low_confidence_floor:
            return CascadeDecision(
                escalate=True,
                reason=f"screening confidence {screening_confidence:.2f} is "
                       f"below the floor {policy.low_confidence_floor:.2f}",
                trigger="low_confidence")
    return CascadeDecision(escalate=False, reason="screening clean")


def heterogeneity_ok(policy: CascadePolicy, model_families: list[str]) -> bool:
    """The escalated panel must span distinct families (§40.3) — a cascade
    that escalates into a monoculture kept the cost and lost the point."""
    return len(set(model_families)) >= policy.critics_min_distinct_families


class MergeQueueDecision(BaseModel):
    admit: list[str]
    deferred: list[str]
    reason: str


def merge_queue_admit(
    feature_prs: list[str], sweep_prs: list[str], *, ci_concurrency_max: int
) -> MergeQueueDecision:
    """Serial admission (§38.2 rule 2): features first, sweep last
    (ADR-U37 — sweep can never starve feature review), bounded by CI
    concurrency (F-16.1: the bound ships on by default)."""
    ordered = list(feature_prs) + list(sweep_prs)
    admit = ordered[:ci_concurrency_max]
    return MergeQueueDecision(
        admit=admit, deferred=ordered[ci_concurrency_max:],
        reason=f"serial queue, ci_concurrency_max={ci_concurrency_max}, "
               "sweep lowest priority")


class GepaBudget(BaseModel):
    targets: list[str] = Field(default_factory=list)  # which skills may evolve
    budget_rollouts_weekly: int = 0  # 0 = proposer disabled
    holdout_fixture_fraction: float = 0.2
    one_agent_per_cycle: bool = True


class GepaConfigError(RuntimeError):
    pass


def load_gepa_budget(mas_dir: str | pathlib.Path) -> GepaBudget:
    path = pathlib.Path(mas_dir) / GEPA_FILE
    if not path.exists():
        return GepaBudget()  # proposer off — the safe default
    raw = yaml.safe_load(path.read_text()) or {}
    budget = GepaBudget(**raw)
    if not 0 < budget.holdout_fixture_fraction < 1:
        raise GepaConfigError("holdout_fixture_fraction must be in (0,1) — an "
                              "optimizer scored on its own training fixtures "
                              "is overfitting with a budget")
    if budget.budget_rollouts_weekly and not budget.one_agent_per_cycle:
        raise GepaConfigError("one_agent_per_cycle is the floor: attributing a "
                              "regression requires changing one thing")
    return budget
