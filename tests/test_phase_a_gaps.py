"""Phase A of the gap-closure plan (docs/gap-closure-plan.md)."""

from __future__ import annotations

import datetime as dt

import pytest

from ai_venture_studio.classification import classification_check
from ai_venture_studio.lanes.data_nfr import lineage_impact_check, lint_data_nfr
from ai_venture_studio.lanes.platform_gate import (
    GateP1Preflight,
    PlatformPreflightItem,
    gate_p1_check,
    record_submission,
)
from ai_venture_studio.lanes.web_tools import axe_scan, lighthouse_budget, size_limit_check
from ai_venture_studio.upstream.verdicts import ALL_VERDICTS, is_escalation

TODAY = dt.date(2026, 7, 26)


def test_web_tools_skip_visibly(monkeypatch):
    # Patched at the resolver, not at the lane: since ADR-069 the lane asks
    # `executables.find` once and runs what it gets back, so this is the only
    # PATH lookup left to stub — the same seam test_lane_runners and
    # test_forge already use.
    monkeypatch.setattr("ai_venture_studio.executables.shutil.which", lambda _: None)
    for report in (axe_scan("http://x/"), lighthouse_budget("http://x/", "b.json"),
                   size_limit_check()):
        assert report.status == "skipped" and "VISIBLY" in report.detail


def test_data_nfr_grammar():
    issues = lint_data_nfr([
        "orders.daily SHALL freshness <= 2h AT p95",
        "orders.daily SHALL row_count_delta <= 5%",
        "the pipeline keeps data fresh enough",
        "orders.daily is fast",
    ])
    assert [i.index for i in issues] == [2, 3]
    assert "vague data term" in issues[0].problem


def test_lineage_impact():
    lineage = {"orders.daily": {"consumers": ["finance.report", "ml.features"]},
               "leaf.table": {"consumers": []}}
    issues = lineage_impact_check(lineage, ["orders.daily", "leaf.table", "ghost"])
    rules = {(i.dataset, i.rule) for i in issues}
    assert ("orders.daily", "impact_review") in rules
    assert ("ghost", "undeclared_lineage") in rules
    assert not any(d == "leaf.table" for d, _ in rules)  # declared leaf is fine


def test_verdict_vocabulary():
    assert "ESCALATE_SPEC_GAP" in ALL_VERDICTS and "APPROVE_BRIEF" in ALL_VERDICTS
    assert is_escalation("ESCALATE_MIGRATION_DESTRUCTIVE")
    assert not is_escalation("APPROVE_PLAN")


def test_gate_p1_preflight():
    preflight = GateP1Preflight(
        platform="wechat_review", checklist_verified_on="2026-07-01",
        items=[
            PlatformPreflightItem(item="package under 2MB", satisfied=True,
                                  evidence="mp_size_check dist-wx"),
            PlatformPreflightItem(item="隐私协议 declared", satisfied=True,
                                  evidence="mp_privacy_check"),
        ])
    assert gate_p1_check(preflight, today=TODAY).ready
    stale = preflight.model_copy(update={"checklist_verified_on": "2026-01-01"})
    assert not gate_p1_check(stale, today=TODAY).ready
    no_evidence = GateP1Preflight(
        platform="app_store", checklist_verified_on="2026-07-01",
        items=[PlatformPreflightItem(item="signing", satisfied=True)])
    result = gate_p1_check(no_evidence, today=TODAY)
    assert any("checkbox is not a check" in f for f in result.findings)
    with pytest.raises(ValueError, match="named human"):
        record_submission(preflight, " ")
    assert record_submission(preflight, "melody").submitter == "melody"


def test_classification_tags():
    findings = classification_check({
        "evidence": "confidential", "claims": None, "telemetry": "public",
        "reviews": "public",
    })
    assert any("claims: no classification" in f for f in findings)
    assert any("reviews: public downgrades the internal floor" in f
               for f in findings)
    assert not any("evidence" in f for f in findings)
