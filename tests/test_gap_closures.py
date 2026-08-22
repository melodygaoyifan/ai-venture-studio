"""v0.27.0 gap closures from the full audit: structured profiles (§41.1),
cascades + merge queue + GEPA budget (doc 16), debt tools (doc 11 §19)."""

from __future__ import annotations

import pytest

from ai_venture_studio.cascade import (
    CascadePolicy,
    GepaConfigError,
    cascade_route,
    heterogeneity_ok,
    load_gepa_budget,
    merge_queue_admit,
)
from ai_venture_studio.profile_schema import (
    ProfileSchemaError,
    compose_profiles,
    validate_profile,
)
from ai_venture_studio.tools.debt import jscpd_clones, radon_complexity, vulture_dead_code


def test_structured_profiles_add_only_and_compose():
    web = validate_profile({
        "name": "web", "det_tools_add": ["axe_scan", "lighthouse_budget"],
        "voter_deltas": ["DesignFidelity"], "paths": ["site/**"]})
    mp = validate_profile({
        "name": "miniprogram",
        "det_tools_add": ["mp_size_check", "mp_domain_check"],
        "forbidden_autonomous_add": ["platform_submission"]})
    merged = compose_profiles([web, mp])
    assert merged.name == "web+miniprogram"
    assert set(merged.det_tools_add) == {"axe_scan", "lighthouse_budget",
                                         "mp_size_check", "mp_domain_check"}
    assert merged.forbidden_autonomous_add == ["platform_submission"]

    with pytest.raises(ProfileSchemaError, match="only ADD"):
        validate_profile({"name": "x", "det_tools_remove": ["secret_scan"]})
    with pytest.raises(ProfileSchemaError, match="unknown"):
        validate_profile({"name": "x", "trust_mode": True})


def test_cascade_never_lowers_the_bar():
    off = CascadePolicy()
    assert cascade_route(off, screening_findings=0, screening_blocked=False).escalate

    on = CascadePolicy(screening_enabled=True)
    assert not cascade_route(on, screening_findings=0, screening_blocked=False).escalate
    assert cascade_route(on, screening_findings=1, screening_blocked=False).escalate
    assert cascade_route(on, screening_findings=0, screening_blocked=True).escalate

    assert heterogeneity_ok(on, ["anthropic", "openai"])
    assert not heterogeneity_ok(on, ["anthropic", "anthropic"])  # monoculture


def test_merge_queue_features_first_sweep_last():
    decision = merge_queue_admit(["f1", "f2", "f3"], ["s1"], ci_concurrency_max=3)
    assert decision.admit == ["f1", "f2", "f3"]
    assert decision.deferred == ["s1"]  # sweep never starves feature review
    small = merge_queue_admit(["f1"], ["s1"], ci_concurrency_max=2)
    assert small.admit == ["f1", "s1"]


def test_gepa_budget_schema(tmp_path):
    assert load_gepa_budget(tmp_path).budget_rollouts_weekly == 0  # off = safe
    (tmp_path / "gepa.yaml").write_text(
        "targets: [skills/security.md]\nbudget_rollouts_weekly: 20\n"
        "holdout_fixture_fraction: 0.25\none_agent_per_cycle: true\n")
    assert load_gepa_budget(tmp_path).targets == ["skills/security.md"]
    (tmp_path / "gepa.yaml").write_text(
        "budget_rollouts_weekly: 20\nholdout_fixture_fraction: 1.5\n")
    with pytest.raises(GepaConfigError, match="overfitting"):
        load_gepa_budget(tmp_path)
    (tmp_path / "gepa.yaml").write_text(
        "budget_rollouts_weekly: 20\none_agent_per_cycle: false\n")
    with pytest.raises(GepaConfigError, match="one thing"):
        load_gepa_budget(tmp_path)


def test_debt_tools_skip_visibly_when_absent(monkeypatch):
    # See test_phase_a_gaps: one PATH lookup, at the resolver (ADR-069).
    monkeypatch.setattr("ai_venture_studio.executables.shutil.which", lambda _: None)
    for report in (radon_complexity(), jscpd_clones(), vulture_dead_code()):
        assert report.status == "skipped"
        assert "VISIBLY" in report.detail
