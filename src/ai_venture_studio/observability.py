"""Observability & cost ledger (doc 09 §6/§10, plan phase C item 10).

Costs are computed from provider usage against a CONFIG price table
(.mas/cost-model.yaml — prices rot; they are never constants in code);
the monthly cap is a budget the harness checks, spending decisions stay
human. Tool invocations append to a per-review audit; the evidence ledger
is the human-readable "what did this review actually consult" artifact.
/metrics (server) renders Prometheus text from the same counters
telemetry uses — aggregate-only by construction.
"""

from __future__ import annotations

import pathlib

import yaml
from pydantic import BaseModel, Field

COST_MODEL_FILE = "cost-model.yaml"


class CostModel(BaseModel):
    # USD per 1M tokens; empty by default — no price, no estimate, visibly.
    # There is deliberately no cap field: budget enforcement belongs to the
    # provider account that does the billing (ADR-032). An old cost-model.yaml
    # carrying `monthly_cap_usd` still loads — the key is simply ignored.
    prices: dict[str, dict[str, float]] = Field(default_factory=dict)


def load_cost_model(mas_dir: str | pathlib.Path) -> CostModel:
    path = pathlib.Path(mas_dir) / COST_MODEL_FILE
    if not path.exists():
        return CostModel()
    raw = yaml.safe_load(path.read_text()) or {}
    return CostModel(**raw)


class CostRecord(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None  # None = unpriced, never silently 0


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cost_model: CostModel) -> CostRecord:
    price = cost_model.prices.get(model)
    cost = (None if price is None else
            round(input_tokens / 1e6 * price.get("input", 0)
                  + output_tokens / 1e6 * price.get("output", 0), 6))
    return CostRecord(model=model, input_tokens=input_tokens,
                      output_tokens=output_tokens, cost_usd=cost)


def month_spend(records: list[CostRecord]) -> tuple[float, int]:
    """(priced total, unpriced call count) — the unpriced count is part of
    the answer; a total that hides unpriced calls understates."""
    priced = sum(r.cost_usd for r in records if r.cost_usd is not None)
    return round(priced, 4), sum(1 for r in records if r.cost_usd is None)


class ToolAuditEntry(BaseModel):
    tool: str
    at: str
    status: str
    detail: str = ""


def append_tool_audit(review_dir: str | pathlib.Path, entry: ToolAuditEntry) -> pathlib.Path:
    path = pathlib.Path(review_dir) / "tool-audit.yaml"
    existing = yaml.safe_load(path.read_text()) if path.exists() else []
    existing = existing or []
    existing.append(entry.model_dump())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(existing, sort_keys=False))
    return path


def write_evidence_ledger(
    review_dir: str | pathlib.Path, *, review_id: str,
    sources_read: list[str], tools_run: list[str], verdict: str,
) -> pathlib.Path:
    path = pathlib.Path(review_dir) / "evidence-ledger.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# Evidence ledger — review {review_id}\n\n"
        f"verdict: **{verdict}**\n\n## Sources read\n"
        + "".join(f"- {s}\n" for s in sources_read)
        + "\n## Deterministic tools run\n"
        + "".join(f"- {t}\n" for t in tools_run)
        + "\nA gate that cannot show what it consulted is theater; this file "
        "is the receipt.\n")
    return path


def prometheus_metrics(workspace: str | pathlib.Path) -> str:
    """Aggregate counters in Prometheus text format — same fields as the
    opt-in telemetry payload, no content, by construction."""
    from ai_venture_studio.usage_telemetry import build_payload

    payload = build_payload(workspace)
    # Metric names are KEPT across the rename (v0.54): renaming a Prometheus
    # series silently breaks every dashboard, alert and recording rule built
    # on it. A rename would need a migration window with both series emitted.
    lines = ["# TYPE autoproduct_reviews_total counter"]
    for verdict, count in sorted(payload["gate_outcome_counts"].items()):
        lines.append(f'autoproduct_reviews_total{{verdict="{verdict}"}} {count}')
    lines.append("# TYPE autoproduct_errors_total counter")
    for cls, count in sorted(payload["error_classes"].items()):
        lines.append(f'autoproduct_errors_total{{class="{cls}"}} {count}')
    lines.append(f'autoproduct_schema_version {payload["schema_version"]}')
    return "\n".join(lines) + "\n"
