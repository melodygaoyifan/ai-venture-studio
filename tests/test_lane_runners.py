"""Execution wrappers (availability-gated, skipped VISIBLY) and the seeded
perf-defect calibration that converted the lane from PROVISIONAL."""

from __future__ import annotations

import pytest

from ai_venture_studio.lanes.calibrate_perf import (
    CALIBRATION_FILE,
    lane_status,
    run_perf_calibration,
)
from ai_venture_studio.lanes.runners import (
    NETEM_PROFILES,
    apply_netem,
    k6_script_from_ac,
    netem_command,
    reconcile_contracts,
    registry_compat_check,
    run_k6,
)


def test_k6_script_compiles_the_ac_into_thresholds():
    script = k6_script_from_ac(
        "UNDER 200 rps open-model arrival THE SYSTEM SHALL http_req_duration "
        "< 300ms AT p95 FOR 10m", url="http://127.0.0.1:8000/")
    assert "constant-arrival-rate" in script and "rate: 200" in script
    assert "'http_req_duration': ['p(95)<300']" in script
    assert "duration: '10m'" in script
    with pytest.raises(ValueError, match="not a perf AC"):
        k6_script_from_ac("handles high traffic", url="http://x/")


def test_run_k6_skips_visibly_without_the_binary(monkeypatch):
    # Since ADR-064 the lookup lives in one place, so this patches there.
    monkeypatch.setattr("ai_venture_studio.executables.shutil.which", lambda _: None)
    report = run_k6("UNDER 50 rps open THE SYSTEM SHALL http_req_duration "
                    "< 500ms AT p95 FOR 60s", url="http://127.0.0.1:1/")
    assert report.status == "skipped"
    assert "script" in report.data  # the would-be run is in the record


def test_netem_profiles_and_gated_apply():
    for profile in NETEM_PROFILES:
        command = netem_command(profile)
        assert command[:4] == ["tc", "qdisc", "add", "dev"]
        assert "loss" in command
    report = apply_netem("wifi_poor")
    # macOS/no-tc: skipped with the command recorded; Linux CI: tc exists
    # and the real invocation may lack privileges/interface — "error" with
    # detail is an honest outcome there, never a silent pass.
    assert report.status in ("skipped", "ok", "error")
    if report.status == "skipped":
        assert report.data["command"][0] == "tc"
    assert apply_netem("marsnet").status == "error"


def test_registry_wrapper_and_reconciliation(monkeypatch):
    monkeypatch.delenv("SCHEMA_REGISTRY_URL", raising=False)
    report = registry_compat_check("orders-value", "{}")
    assert report.status == "skipped"
    assert "in-repo" in report.detail  # the deterministic check still gates

    findings = reconcile_contracts(
        {"orders": "BACKWARD", "users": "FULL", "ghost": "NONE"},
        {"orders": "BACKWARD", "users": "FORWARD", "rogue": "NONE"})
    rules = {(f["topic"], f["rule"]) for f in findings}
    assert ("users", "mode_drift") in rules
    assert ("ghost", "not_in_registry") in rules
    assert ("rogue", "undeclared_topic") in rules
    assert ("orders", "mode_drift") not in set(rules)


def test_calibration_catches_the_seeded_manifest():
    result = run_perf_calibration(requests_per_endpoint=15)
    by_defect = {r.defect: r for r in result.readings}
    # The two unambiguous defects must always be caught; the environment-
    # sensitive three give the rate its remaining headroom.
    assert by_defect["sync_call_in_async_handler"].caught
    assert by_defect["quadratic_serializer_on_list_endpoint"].caught
    assert result.catch_rate >= 0.6
    assert result.parity == "low" and "satisfies no AC" in result.note


def test_lane_status_reflects_the_recorded_calibration():
    assert CALIBRATION_FILE.exists()  # the committed 2026-07-26 run
    assert lane_status().startswith("CALIBRATED")
