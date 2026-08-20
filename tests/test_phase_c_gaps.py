"""Phase C: cost/observability ledger, module-spec invariants, named
signal webhooks + /metrics."""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from ai_venture_studio.module_specs import (
    ModuleSpecError,
    load_module_specs,
    spec_drift_check,
)
from ai_venture_studio.observability import (
    ToolAuditEntry,
    append_tool_audit,
    estimate_cost,
    load_cost_model,
    month_spend,
    prometheus_metrics,
    write_evidence_ledger,
)


def test_cost_ledger_prices_are_config_and_unpriced_is_visible(tmp_path):
    # An old cost-model.yaml carrying the retired `monthly_cap_usd` key must
    # still load (ADR-032 removed the cap; the key is ignored, never an error).
    (tmp_path / "cost-model.yaml").write_text(yaml.safe_dump({
        "prices": {"claude-opus-4-8": {"input": 15.0, "output": 75.0}},
        "monthly_cap_usd": 200.0}))
    model = load_cost_model(tmp_path)
    priced = estimate_cost("claude-opus-4-8", 1_000_000, 100_000, model)
    assert priced.cost_usd == pytest.approx(15.0 + 7.5)
    unpriced = estimate_cost("mystery-model", 1000, 1000, model)
    assert unpriced.cost_usd is None  # never silently zero
    total, unpriced_count = month_spend([priced, unpriced])
    assert total == 22.5 and unpriced_count == 1


def test_tool_audit_and_evidence_ledger(tmp_path):
    review = tmp_path / "reviews" / "abc"
    append_tool_audit(review, ToolAuditEntry(tool="secret_scan",
                                             at="2026-07-26T10:00:00Z", status="ok"))
    append_tool_audit(review, ToolAuditEntry(tool="slopsquat_check",
                                             at="2026-07-26T10:00:01Z", status="ok"))
    audit = yaml.safe_load((review / "tool-audit.yaml").read_text())
    assert [e["tool"] for e in audit] == ["secret_scan", "slopsquat_check"]

    ledger = write_evidence_ledger(review, review_id="abc",
                                   sources_read=["src/app.py"],
                                   tools_run=["secret_scan"], verdict="APPROVE")
    text = ledger.read_text()
    assert "receipt" in text and "src/app.py" in text


def test_module_spec_drift(tmp_path):
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    (specs_dir / "billing.spec.yaml").write_text(yaml.safe_dump({
        "module": "billing", "paths": ["modules/billing/*"],
        "invariants": ["amounts are integer cents end to end"],
        "forbidden_side_effects": [r"requests\.(get|post)\("],
        "expected_change_pattern": ["modules/billing/rates*"],
        "unexpected_change_pattern": ["modules/billing/ledger*"]}))
    specs = load_module_specs(tmp_path)

    findings = spec_drift_check(
        specs,
        ["modules/billing/ledger_core.py", "modules/billing/rates.py",
         "modules/billing/webhooks.py", "modules/users/api.py"],
        added_lines={"modules/billing/webhooks.py":
                     "resp = requests.post(url, json=payload)"})
    rules = {(f.file, f.rule) for f in findings}
    assert ("modules/billing/ledger_core.py", "SPEC_DRIFT_UNDOCUMENTED") in rules
    assert ("modules/billing/webhooks.py", "SPEC_DRIFT_UNDOCUMENTED") in rules
    assert ("modules/billing/webhooks.py", "forbidden_side_effect") in rules
    assert not any(f == "modules/billing/rates.py" for f, _ in rules)  # expected
    assert not any(f == "modules/users/api.py" for f, _ in rules)  # not owned

    (specs_dir / "bad.spec.yaml").write_text("module: x\n")
    with pytest.raises(ModuleSpecError, match="paths"):
        load_module_specs(tmp_path)


def test_metrics_and_named_webhooks(tmp_path, monkeypatch):
    text = prometheus_metrics(tmp_path)
    assert "autoproduct_schema_version 1" in text

    from ai_venture_studio.server import create_app

    monkeypatch.setenv("AUTOPRODUCT_WEBHOOK_SECRET", "s3cret")
    client = TestClient(create_app(str(tmp_path)))
    assert client.get("/metrics").status_code == 200

    headers = {"Authorization": "Bearer s3cret"}
    first = client.post("/webhooks/sentry", headers=headers,
                        json={"id": "evt-1", "event": {"title": "KeyError in exports"}})
    assert first.status_code == 202 and first.json()["status"] == "accepted"
    again = client.post("/webhooks/sentry", headers=headers,
                        json={"id": "evt-1", "event": {"title": "KeyError in exports"}})
    assert again.json()["status"] == "deduplicated"  # the dedupe window
    assert client.post("/webhooks/sentry", json={"id": "x"}).status_code == 401
    assert client.post("/webhooks/nagios", headers=headers, json={}).status_code == 404
    incidents = list((tmp_path / ".mas" / "inbox").glob("*.yaml"))
    assert len(incidents) == 1  # deduplicated re-fire wrote nothing new
