"""The two-stage run harness (§21.61.2, ADR-U24): screen many, validate
the leader on a fresh sample.

Structurally identical to the framework's vote → verify → leader pattern,
which is why the stage fits at all. Decision rules that do the work:
adopt only if the stage-2 primary is significant AND no guardrail degrades
beyond its bound (guardrails can veto a win — a subject line that lifts
opens and raises complaints is a loss); inconclusive results ENTER
NOTHING — a non-result updates the priors ledger's n and nothing else,
closing the path by which false discoveries become permanent institutional
knowledge.
"""

from __future__ import annotations

import math
from statistics import NormalDist

from pydantic import BaseModel, Field

from ai_venture_studio.experiment.design import (
    ExperimentDesign,
    verify_at_analysis,
)
from ai_venture_studio.experiment.fdr import benjamini_hochberg

_NORMAL = NormalDist()


class ArmReading(BaseModel):
    arm: str
    hits: int
    n: int

    @property
    def rate(self) -> float:
        return self.hits / self.n if self.n else 0.0


class GuardrailReading(BaseModel):
    metric: str
    control: float
    treatment: float
    max_degradation: float  # absolute; direction-aware sign convention below
    degrades_when: str = "higher"  # higher | lower


class DecisionRecord(BaseModel):
    experiment_id: str
    decision: str  # adopt | reject | inconclusive
    winner: str = ""
    stage1_survivors: list[str] = Field(default_factory=list)
    guardrail_vetoes: list[str] = Field(default_factory=list)
    detail: str
    compounding_eligible: bool  # inconclusive-enters-nothing, wired here
    priors_update: dict = Field(default_factory=dict)


class CompoundingBoundaryError(RuntimeError):
    """Only an adopted, pre-registration-verified result may enter the
    compounding loop."""


def two_proportion_p(a: ArmReading, b: ArmReading) -> float:
    if a.n == 0 or b.n == 0:
        return 1.0
    pooled = (a.hits + b.hits) / (a.n + b.n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / a.n + 1 / b.n))
    if se == 0:
        return 1.0
    z = (a.rate - b.rate) / se
    return 2 * (1 - _NORMAL.cdf(abs(z)))


def screen_stage1(
    control: ArmReading, variants: list[ArmReading], *, q: float
) -> list[str]:
    """BH-controlled screening: the surviving variant names, best first."""
    p_values = [two_proportion_p(v, control) for v in variants]
    results = benjamini_hochberg(p_values, q=q)
    survivors = [
        v.arm
        # strict: BH returns one result per p-value, in input order, and
        # there is one p-value per variant. Truncating here drops an arm
        # out of a significance test without saying so.
        for v, r in zip(variants, results, strict=True)
        if r.significant and v.rate > control.rate
    ]
    return sorted(survivors, key=lambda arm: -next(v.rate for v in variants if v.arm == arm))


def _guardrail_breaches(guardrails: list[GuardrailReading]) -> list[str]:
    breaches = []
    for g in guardrails:
        delta = g.treatment - g.control
        degraded = delta > g.max_degradation if g.degrades_when == "higher" else (
            -delta > g.max_degradation
        )
        if degraded:
            breaches.append(
                f"{g.metric}: {g.control:g} → {g.treatment:g} exceeds the "
                f"{g.max_degradation:g} bound"
            )
    return breaches


def run_two_stage(
    design: ExperimentDesign,
    design_yaml_text: str,
    *,
    stage1_control: ArmReading,
    stage1_variants: list[ArmReading],
    stage2_control: ArmReading | None = None,
    stage2_treatment: ArmReading | None = None,
    guardrails: list[GuardrailReading] | None = None,
) -> DecisionRecord:
    """The full §21.61.2 flow. Pre-registration is verified BEFORE any
    reading; stage 2 runs on a fresh sample against the screening leader."""
    verify_at_analysis(design_yaml_text, design.preregistration_hash)

    survivors = screen_stage1(
        stage1_control, stage1_variants, q=design.design_stage1.q
    )
    if not survivors:
        return DecisionRecord(
            experiment_id=design.id,
            decision="inconclusive",
            detail="no variant survived BH screening — a non-result; nothing "
            "enters the compounding loop",
            compounding_eligible=False,
            priors_update={"n": stage1_control.n + sum(v.n for v in stage1_variants)},
        )

    if stage2_control is None or stage2_treatment is None:
        return DecisionRecord(
            experiment_id=design.id,
            decision="inconclusive",
            stage1_survivors=survivors,
            detail=f"screening leader {survivors[0]!r} awaits stage-2 validation "
            "on a fresh sample — screening alone adopts nothing (ADR-U24)",
            compounding_eligible=False,
            priors_update={"n": stage1_control.n + sum(v.n for v in stage1_variants)},
        )

    p_final = two_proportion_p(stage2_treatment, stage2_control)
    primary_wins = (
        p_final <= design.power.alpha
        and stage2_treatment.rate > stage2_control.rate
    )
    vetoes = _guardrail_breaches(guardrails or [])

    if primary_wins and not vetoes:
        decision, detail = "adopt", (
            f"stage-2 primary significant (p={p_final:.4f}) on a fresh sample; "
            "no guardrail degraded beyond its bound"
        )
    elif primary_wins and vetoes:
        decision, detail = "reject", (
            f"primary won (p={p_final:.4f}) but guardrails veto: {'; '.join(vetoes)} "
            "— a win that costs a guardrail is a loss"
        )
    elif p_final <= design.power.alpha:
        decision, detail = "reject", f"significant in the wrong direction (p={p_final:.4f})"
    else:
        decision, detail = "inconclusive", (
            f"stage-2 primary not significant (p={p_final:.4f}) — enters nothing"
        )

    total_n = (
        stage1_control.n
        + sum(v.n for v in stage1_variants)
        + stage2_control.n
        + stage2_treatment.n
    )
    return DecisionRecord(
        experiment_id=design.id,
        decision=decision,
        winner=stage2_treatment.arm if decision == "adopt" else "",
        stage1_survivors=survivors,
        guardrail_vetoes=vetoes,
        detail=detail,
        compounding_eligible=decision == "adopt",
        priors_update={"n": total_n},
    )


def admit_to_compounding(record: DecisionRecord) -> DecisionRecord:
    """The compounding-loop boundary (§21.61.3): only adopted results pass.
    An inconclusive 'learning' written into the loop is a false discovery
    with tenure."""
    if not record.compounding_eligible:
        raise CompoundingBoundaryError(
            f"{record.experiment_id}: decision {record.decision!r} enters "
            "nothing — the priors ledger takes the n, the compounding loop "
            "takes only adoptions"
        )
    return record
