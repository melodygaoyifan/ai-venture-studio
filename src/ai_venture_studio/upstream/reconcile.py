"""Reconciliation (ADR-046) — a new request is read against the old ones.

`avs add` has always planned a feature against the CODE. It has never
asked the question a person asks first: *do we already promise this, and
does it fight something we promised before?* Bench run 16 recorded the
consequence in a reviewer's own words — a finding calling a product's
choice "the exact anti-pattern a prior task in this repo was already
dinged for". The reviewer remembered. The system had no way to.

Two stages, and the split is the point:

1. **Deterministic retrieval** (`requirements.relevant`) narrows the corpus
   to the requirements a request plausibly touches. No model, no cost, and
   it is what makes this affordable at 300 requirements.

2. **One model call** judges ONLY those candidates and returns a typed
   relation each. It never sees the whole ledger, so its answer cannot
   degrade as the product grows.

Three relations, deliberately not four. There is no `unclear`: a judgment
the reconciler cannot make must fall through to `extends`, which is the
behaviour that existed before this file — build it, and let the review
stage catch the overlap. A gate that stops on its own uncertainty stops
constantly, and `ears.py` already carries the scar from a lint that
rejected correct work.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ai_venture_studio.providers import get_provider
from ai_venture_studio.providers.base import last_response_truncated
from ai_venture_studio.upstream.requirements import RequirementSlice, render_slice
from ai_venture_studio.yamlx import extract_mapping

RECONCILER_MARKER = "requirement reconciler for an existing product"

RELATIONS = ("duplicate", "contradicts", "extends")


class Relation(BaseModel):
    requirement_id: str
    relation: str
    reason: str = ""


class Reconciliation(BaseModel):
    #: Whether the check actually RAN and produced a readable answer. An
    #: unreadable reconciliation must never present as a clean one: empty
    #: relations with `checked=True` says "nothing conflicts", and empty
    #: relations with `checked=False` says "nobody looked". Reading the
    #: second as the first is the defect ADR-041 removed from the spec
    #: stage, and it would be worse here — this gate exists to say no.
    checked: bool = False
    relations: list[Relation] = Field(default_factory=list)
    note: str = ""

    def of(self, relation: str) -> list[Relation]:
        return [r for r in self.relations if r.relation == relation]

    @property
    def duplicates(self) -> list[Relation]:
        return self.of("duplicate")

    @property
    def conflicts(self) -> list[Relation]:
        return self.of("contradicts")


_SYSTEM = f"""You are the {RECONCILER_MARKER}. You are given a new feature
request and the acceptance criteria the product ALREADY promises. Say how
the request relates to each one.

For each requirement listed, choose exactly one relation:
- duplicate: the request asks for behaviour this requirement already
  promises. Only when it is genuinely the same promise — a request that
  extends, refines, or adds a case is NOT a duplicate.
- contradicts: the request asks for behaviour that cannot be true at the
  same time as this requirement. Both cannot hold; one must replace the
  other.
- extends: anything else, including "related but compatible" and "you are
  not sure". This is the safe answer and it is the right answer most of
  the time.

Judge the PROMISE, not the wording. Two criteria about the same endpoint
are not duplicates if they promise different behaviour.

Reason: ONE short sentence a non-technical person can read, in the same
language the request is written in. For duplicate and contradicts it must
say what the overlap or the clash actually is.

Respond with ONLY YAML, one entry per requirement you were shown:
relations:
  - requirement_id: R-007
    relation: extends
    reason: "..."
"""


def reconcile(
    request_text: str,
    slice_: RequirementSlice,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> Reconciliation:
    """How this request relates to what the product already promises.

    Returns `checked=False` — never a clean verdict — when nothing was
    retrieved to judge, when the model's answer will not parse, or when it
    was cut off mid-answer.
    """
    if not slice_.shown:
        return Reconciliation(
            checked=False, note="no existing requirement matched this request"
        )
    known = {req.id for req in slice_.shown}
    raw = get_provider(provider).complete(
        model=model,
        system=_SYSTEM,
        user=f"<existing_requirements>\n{render_slice(slice_)}\n"
        f"</existing_requirements>\n\n<request>\n{request_text}\n</request>",
        max_tokens=2048,
    )
    if last_response_truncated():
        return Reconciliation(
            checked=False,
            note="the reconciler's answer was cut off — a partial list of "
            "relations is not a finding of no conflict",
        )
    try:
        data = extract_mapping(raw, ("relations",))
    except ValueError as exc:
        return Reconciliation(
            checked=False, note=f"reconciler output did not parse: {exc}"
        )
    relations = []
    for entry in data.get("relations") or []:
        if not isinstance(entry, dict):
            continue
        rid = str(entry.get("requirement_id", "")).strip()
        rel = str(entry.get("relation", "")).strip().lower()
        # A verdict about a requirement that was never shown is a verdict
        # about nothing — most likely an id the model invented. Dropping it
        # is safe; acting on it would supersede a promise chosen at random.
        if rid not in known or rel not in RELATIONS:
            continue
        relations.append(
            Relation(requirement_id=rid, relation=rel,
                     reason=str(entry.get("reason", "")).strip())
        )
    if not relations:
        return Reconciliation(
            checked=False,
            note="the reconciler named no requirement it was shown",
        )
    return Reconciliation(checked=True, relations=relations)


def render_for_planner(rec: Reconciliation) -> str:
    """What the planner is told about the clash it must plan around."""
    if not rec.checked:
        return f"(not reconciled: {rec.note})" if rec.note else "(not reconciled)"
    lines = [
        f"{r.requirement_id}: {r.relation} — {r.reason}"
        for r in rec.relations
        if r.relation != "extends"
    ]
    return "\n".join(lines) if lines else "(nothing this request conflicts with)"


def save(feature_dir: str | Path, rec: Reconciliation) -> Path:
    path = Path(feature_dir) / "reconciliation.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(rec.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
