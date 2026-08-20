"""Doc 16 operations policy + doc 17 miniprogram checks — the gap-closure
items from the full design-doc cross-reference audit."""

from __future__ import annotations

import pytest

from ai_venture_studio.lanes.miniprogram import (
    MAIN_PACKAGE_BUDGET_BYTES,
    mp_domain_check,
    mp_privacy_check,
    mp_setdata_lint,
    mp_size_check,
)
from ai_venture_studio.operations import (
    OperationsError,
    OperationsPolicy,
    lane_check,
    load_hot_files,
    load_operations_policy,
    shed_check,
    wip_check,
)


def test_operations_policy_defaults_and_overrides(tmp_path):
    policy = load_operations_policy(tmp_path)
    assert policy.wip_limits["coding_lanes_total"] == 4
    (tmp_path / "operations-policy.yaml").write_text(
        "wip_limits: {review_queue: 3}\nhuman_queue_limit: 5\n")
    policy = load_operations_policy(tmp_path)
    assert policy.wip_limits["review_queue"] == 3
    assert policy.wip_limits["discovery"] == 2  # defaults preserved
    (tmp_path / "operations-policy.yaml").write_text("wip_limits: {spec: 0}\n")
    with pytest.raises(OperationsError):
        load_operations_policy(tmp_path)


def test_wip_and_shed_rules():
    policy = OperationsPolicy()
    assert wip_check(policy, "spec", 1).admit
    blocked = wip_check(policy, "spec", 2)
    assert not blocked.admit and "finish before starting" in blocked.reason
    assert shed_check(policy, 8)  # at the limit: still pulling
    assert not shed_check(policy, 9)  # over: intake sheds, gates unchanged


def test_hot_files_lane_check(tmp_path):
    (tmp_path / "hot-files.yaml").write_text(
        "hot_files:\n"
        "  - {pattern: 'src/app/db/*', owner_lane: feature-billing}\n"
        "  - {pattern: 'src/app/shared.py', lanes_max: 1}\n")
    registry = load_hot_files(tmp_path)
    conflicts = lane_check(["src/app/db/schema.py", "src/app/other.py"],
                           registry, lane="sweep")
    assert len(conflicts) == 1 and conflicts[0].owner_lane == "feature-billing"
    assert lane_check(["src/app/db/schema.py"], registry,
                      lane="feature-billing") == []  # the owner may touch it


def test_mp_checks():
    assert mp_size_check({"dist-wx": MAIN_PACKAGE_BUDGET_BYTES - 1}) == []
    over = mp_size_check({"dist-wx": MAIN_PACKAGE_BUDGET_BYTES + 1, "dist-qq": 100})
    assert len(over) == 1 and "dist-wx" in over[0].message

    findings = mp_domain_check(
        {"pages/shop.js": "wx.request({url: 'https://api.evil.example/x'})"},
        whitelist=["api.myshop.example"])
    assert findings[0].rule == "undeclared_domain"

    jank = mp_setdata_lint({"pages/list.js": ".setData(" * 12})
    assert jank[0].rule == "setdata_hot_path"

    privacy = mp_privacy_check(
        {"app.js": "wx.getLocation({})"}, privacy_agreement_declared=False)
    rules = {f.rule for f in privacy}
    assert {"missing_privacy_agreement", "eager_authorization"} == rules
    assert mp_privacy_check({"pages/map.js": "wx.getLocation({})"},
                            privacy_agreement_declared=True) == []
