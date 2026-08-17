"""The five outer-loop metrics (§22.66.4) — mirroring the five upstream
and five downstream.

The last one is deliberately aimed at this framework itself: a design that
adds six stages to a system should say how it would know it made things
worse. If attention cost per resolved hypothesis does not improve over
quarters, the product loop is not paying for itself and should be
simplified or dropped.
"""

from __future__ import annotations

from pydantic import BaseModel

from ai_venture_studio.product.kill_registry import KillRecord
from ai_venture_studio.product.reconcile import HypothesisVerdict

_STRONG_TYPES = frozenset({"primary_measured", "primary_cited"})


class LoopMetrics(BaseModel):
    # Every rate here is None when its denominator was empty, never 0.0
    # (ADR-053). `kill_rate` and `attention_cost_per_resolved_hypothesis`
    # were written this way from the start and say why; the two above them
    # were not, in the same file, for the same kind of number.
    evidence_quality_ratio: dict[str, float | None]  # by stage; watch the trend
    hypothesis_resolution_rate: float | None
    decision_latency_days: dict[str, float]  # per gate
    kill_rate: float | None  # None until anything was decided at PL5
    attention_cost_per_resolved_hypothesis: float | None


def evidence_quality_ratio(ledgers_by_stage: dict[str, dict]) -> dict[str, float | None]:
    """Share of claims that are primary_measured or primary_cited, by
    stage — the outer loop's analogue of test coverage.

    A stage with no claims scores None. It has no evidence quality either
    way, and 0.0 would read as "every claim in this stage is weak" about a
    stage that made none — the reading this metric exists to make possible.
    """
    ratios: dict[str, float | None] = {}
    for stage, ledger in sorted(ledgers_by_stage.items()):
        claims = [c for c in ledger.get("claims") or [] if isinstance(c, dict)]
        if not claims:
            ratios[stage] = None
            continue
        strong = sum(1 for c in claims if c.get("source_type") in _STRONG_TYPES)
        ratios[stage] = strong / len(claims)
    return ratios


def hypothesis_resolution_rate(verdicts: list[HypothesisVerdict]) -> float | None:
    """Falsified-or-confirmed ÷ total open, per loop. A loop that resolves
    nothing is a ratchet.

    None when there were no hypotheses — which is exactly why: the docstring
    above is an indictment, and a loop that has not opened one yet has not
    earned it. `kill_rate` next door already draws this line ("a stated rate
    near zero over many loops is itself the finding"), and a near-zero rate
    can only be a finding if zero-from-nothing cannot reach it.
    """
    if not verdicts:
        return None
    resolved = sum(1 for v in verdicts if v.verdict in ("supported", "not_supported"))
    return resolved / len(verdicts)


def decision_latency_days(
    gate_entries: dict[str, str], gate_decisions: dict[str, str]
) -> dict[str, float]:
    """Gate entry → recorded decision, in days (ISO timestamps in, so the
    caller supplies time — nothing here reads a clock). The first thing to
    degrade under attention starvation."""
    import datetime as dt

    latencies = {}
    for gate, entered in sorted(gate_entries.items()):
        decided = gate_decisions.get(gate)
        if decided is None:
            continue
        delta = dt.datetime.fromisoformat(decided) - dt.datetime.fromisoformat(entered)
        latencies[gate] = round(delta.total_seconds() / 86400, 3)
    return latencies


def kill_rate(registry: list[KillRecord], decided_at_pl5: int) -> float | None:
    """Killed-or-pivoted ÷ decided at Gate PL5. There is no correct target
    — a stated rate near zero over many loops is itself the finding."""
    if decided_at_pl5 <= 0:
        return None
    stopped = sum(1 for r in registry if r.outcome in ("kill", "pivot"))
    return stopped / decided_at_pl5


def attention_cost_per_resolved_hypothesis(
    attention_spent: dict[str, int], verdicts: list[HypothesisVerdict]
) -> float | None:
    """Human approvals ÷ hypotheses resolved — the number by which the
    whole product loop is falsifiable."""
    resolved = sum(1 for v in verdicts if v.verdict in ("supported", "not_supported"))
    if resolved == 0:
        return None
    return sum(attention_spent.values()) / resolved


def loop_metrics(
    *,
    ledgers_by_stage: dict[str, dict],
    verdicts: list[HypothesisVerdict],
    gate_entries: dict[str, str],
    gate_decisions: dict[str, str],
    registry: list[KillRecord],
    decided_at_pl5: int,
    attention_spent: dict[str, int],
) -> LoopMetrics:
    return LoopMetrics(
        evidence_quality_ratio=evidence_quality_ratio(ledgers_by_stage),
        hypothesis_resolution_rate=hypothesis_resolution_rate(verdicts),
        decision_latency_days=decision_latency_days(gate_entries, gate_decisions),
        kill_rate=kill_rate(registry, decided_at_pl5),
        attention_cost_per_resolved_hypothesis=attention_cost_per_resolved_hypothesis(
            attention_spent, verdicts
        ),
    )
