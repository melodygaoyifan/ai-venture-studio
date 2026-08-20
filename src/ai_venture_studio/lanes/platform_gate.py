"""Gate P1 — the external-platform-review gate class (doc 17 §41.3).

App stores, mini-program review, marketplace listings: an external party
judges the artifact on their clock, by their rules. The framework's job is
the preflight (their checklist, checked before submission) and the record;
`platform_submission` is already forbidden_autonomous (ADR-U14) — a human
presses submit, always.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class PlatformPreflightItem(BaseModel):
    item: str
    satisfied: bool
    evidence: str = ""  # how we know (a check name, an artifact path)


class GateP1Preflight(BaseModel):
    platform: str  # wechat_review | app_store | play_store | marketplace
    checklist_verified_on: str  # platform rules rot; date the checklist
    items: list[PlatformPreflightItem]
    submitter: str = ""  # the named human, set at submission time


class GateP1Result(BaseModel):
    ready: bool
    findings: list[str] = Field(default_factory=list)


def gate_p1_check(
    preflight: GateP1Preflight, *, today: dt.date, checklist_max_age_days: int = 90
) -> GateP1Result:
    findings = []
    verified = dt.date.fromisoformat(preflight.checklist_verified_on)
    if today > verified + dt.timedelta(days=checklist_max_age_days):
        findings.append(f"checklist verified {preflight.checklist_verified_on} — "
                        "stale; platform rules rot (§17.43 discipline)")
    for item in preflight.items:
        if not item.satisfied:
            findings.append(f"unsatisfied: {item.item}")
        elif not item.evidence.strip():
            findings.append(f"'{item.item}' satisfied with no evidence — a "
                            "checkbox is not a check")
    return GateP1Result(ready=not findings, findings=findings)


def record_submission(preflight: GateP1Preflight, submitter: str) -> GateP1Preflight:
    if not submitter.strip():
        raise ValueError("platform_submission is forbidden_autonomous (ADR-U14) "
                         "— a named human submits")
    return preflight.model_copy(update={"submitter": submitter})
