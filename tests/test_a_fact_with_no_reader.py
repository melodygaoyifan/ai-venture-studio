"""ADR-060: five facts the system established and then dropped on the floor.

The audit in `test_write_without_reader.py` proves the fields have readers
now. These prove the readers say the right thing — which is the half an
audit cannot check, and the half that matters to whoever is reading.
"""

from __future__ import annotations

import json

import pytest
import yaml

from ai_venture_studio import automation, bench_criterion
from ai_venture_studio.cascade import (
    KNOWN_TRIGGERS,
    MANDATORY_TRIGGERS,
    CascadePolicy,
    cascade_route,
)
from ai_venture_studio.marketing.artifacts import Draft
from ai_venture_studio.marketing.gate_pl3 import (
    GateBlockedError,
    assemble_gate_packet,
)
from ai_venture_studio.marketing.register import ReleaseContract
from ai_venture_studio.upstream.autopilot import TaskOutcome, _wireup_block


# --------------------------------------------------------------------------
# F1: built, tests green, nothing imports it
# --------------------------------------------------------------------------
def _built(task_id="t1", *, wireup=(), modified=()):
    return TaskOutcome(
        task_id=task_id, title="Orders API", status="built",
        review_verdict="APPROVE",
        wireup_issues=list(wireup), modified_existing=list(modified),
    )


def test_the_report_says_when_nothing_imports_what_was_built():
    block = _wireup_block([_built(wireup=["app/orders.py is imported by nothing"])],
                          "en")
    assert "Built, but not wired up" in block
    assert "app/orders.py is imported by nothing" in block
    # The sentence a founder needs, not just the finding: `built` and
    # `reachable` are different claims and the report used to make only one.
    assert "green test suite is not the same as being reachable" in block


def test_a_clean_build_adds_no_block_at_all():
    """The control. A section that always renders is decoration; this one
    has to be absent when there is nothing to say."""
    assert _wireup_block([_built()], "en") == ""


def test_pre_existing_files_are_named_not_summarised():
    block = _wireup_block([_built(modified=["app/models.py"])], "en")
    assert "Pre-existing files changed" in block
    assert "app/models.py" in block


def test_both_report_languages_carry_the_wireup_words():
    """The founder-facing report is bilingual. A block added to one language
    only is a fact that exists for half the readers."""
    for lang in ("zh", "en"):
        block = _wireup_block([_built(wireup=["nothing imports it"])], lang)
        assert "nothing imports it" in block, lang


def test_the_outcome_record_carries_what_the_build_result_knew():
    """The specific regression ADR-058 fixed for three fields and not five."""
    outcome = _built(wireup=["x"], modified=["y"])
    dumped = outcome.model_dump()
    assert dumped["wireup_issues"] == ["x"]
    assert dumped["modified_existing"] == ["y"]


def test_an_older_outcomes_file_still_loads():
    """Optional, like every field added to this record before it: a workspace
    written by an older version must not become unreadable."""
    old = {"task_id": "t1", "title": "T", "status": "built"}
    assert TaskOutcome(**old).wireup_issues == []


# --------------------------------------------------------------------------
# F2: on whose say-so
# --------------------------------------------------------------------------
def test_the_automation_log_records_which_policy_authorised_it(tmp_path):
    decision = automation.Decision(
        action="merge", allowed=True, reasons=[],
        policy_path=str(tmp_path / ".mas" / "automerge-policy.yaml"),
    )
    automation.record(tmp_path, decision, detail="PR #7")
    entry = json.loads(
        (tmp_path / ".mas" / "automation-log.jsonl").read_text().strip()
    )
    assert entry["policy_path"].endswith("automerge-policy.yaml")


def test_a_refusal_records_its_policy_too(tmp_path):
    """"Why didn't it" gets the same answer quality as "why did it" — the
    module's own words, and the refusal path is the one an auditor reads."""
    decision = automation.Decision(
        action="deploy", allowed=False, reasons=["policy expired"],
        policy_path=str(tmp_path / ".mas" / "deploy-exec-policy.yaml"),
    )
    automation.record(tmp_path, decision)
    entry = json.loads(
        (tmp_path / ".mas" / "automation-log.jsonl").read_text().strip()
    )
    assert entry["allowed"] is False
    assert entry["policy_path"].endswith("deploy-exec-policy.yaml")


# --------------------------------------------------------------------------
# F3: a trigger tuple nothing consulted
# --------------------------------------------------------------------------
def test_the_default_policy_names_only_triggers_that_run():
    """The defect itself: `low_confidence` shipped in this default for its
    whole life while `cascade_route` had no confidence input to judge it by."""
    assert set(CascadePolicy().escalate_on) == set(MANDATORY_TRIGGERS)
    assert "low_confidence" in KNOWN_TRIGGERS


def test_a_policy_cannot_be_disarmed_one_trigger_at_a_time():
    with pytest.raises(ValueError, match="must include"):
        CascadePolicy(screening_enabled=True, escalate_on=("finding",))


def test_a_trigger_nothing_can_act_on_is_refused():
    with pytest.raises(ValueError, match="cannot act on"):
        CascadePolicy(escalate_on=("finding", "blocked", "vibes"))


def test_the_mandatory_triggers_still_escalate():
    policy = CascadePolicy(screening_enabled=True)
    assert cascade_route(policy, screening_findings=1,
                         screening_blocked=False).trigger == "finding"
    assert cascade_route(policy, screening_findings=0,
                         screening_blocked=True).trigger == "blocked"
    assert not cascade_route(policy, screening_findings=0,
                             screening_blocked=False).escalate


def test_low_confidence_now_actually_fires():
    policy = CascadePolicy(screening_enabled=True,
                           escalate_on=(*MANDATORY_TRIGGERS, "low_confidence"))
    decision = cascade_route(policy, screening_findings=0, screening_blocked=False,
                             screening_confidence=0.4)
    assert decision.escalate and decision.trigger == "low_confidence"
    assert not cascade_route(policy, screening_findings=0, screening_blocked=False,
                             screening_confidence=0.95).escalate


def test_asking_for_a_check_nothing_feeds_is_loud():
    """Neither silently clean (the original defect) nor blanket-escalate
    (which turns the cascade off while looking like it is on)."""
    policy = CascadePolicy(screening_enabled=True,
                           escalate_on=(*MANDATORY_TRIGGERS, "low_confidence"))
    with pytest.raises(ValueError, match="no screening_confidence"):
        cascade_route(policy, screening_findings=0, screening_blocked=False)


# --------------------------------------------------------------------------
# F4: which failures have no override path
# --------------------------------------------------------------------------
class _Finding:
    def __init__(self, hard_fail=False):
        self.hard_fail = hard_fail


def _draft():
    return Draft(id="d1", channel="email", text="Hello.")


def _register():
    return ReleaseContract(prd_ref="prd-1")


def test_a_hard_failure_is_named_as_one_in_the_refusal():
    with pytest.raises(GateBlockedError) as exc:
        assemble_gate_packet(
            _draft(), _register(), True,
            {"deliverability": [_Finding(hard_fail=True)], "brand": [_Finding()]},
        )
    message = str(exc.value)
    assert "deliverability" in message
    assert "no override path" in message
    # And the soft one is NOT promoted into that list — the whole point is
    # that the human can tell the two kinds of work apart.
    assert "'brand'" not in message.split("HARD failures")[-1]


def test_a_soft_only_block_says_nothing_about_hard_failures():
    with pytest.raises(GateBlockedError) as exc:
        assemble_gate_packet(_draft(), _register(), True, {"brand": [_Finding()]})
    assert "HARD" not in str(exc.value)


def test_the_packet_counts_its_preflights_instead_of_asserting_zero():
    packet = assemble_gate_packet(
        _draft(), _register(), True, {"brand": [], "deliverability": []}
    )
    assert [p.check for p in packet.preflight] == ["brand", "deliverability"]
    assert all(p.findings == 0 and p.hard_fails == 0 for p in packet.preflight)


# --------------------------------------------------------------------------
# F5: the bar it judged against
# --------------------------------------------------------------------------
def _result(tmp_path, name, build, probe):
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / name).write_text(
        yaml.safe_dump({"build_rate": build, "probe_pass_rate": probe}),
        encoding="utf-8",
    )


def test_the_criterion_states_the_floor_it_used(tmp_path):
    _result(tmp_path, "result-2026-01-01-0000.yaml", 0.9, 0.9)
    state = bench_criterion.evaluate(tmp_path)
    assert f"build {state.build_floor:.0%}" in state.detail
    assert f"probes {state.probe_floor:.0%}" in state.detail


def test_an_empty_ledger_still_states_the_floor(tmp_path):
    """The branch that had no floor in it at all. "No runs" is exactly when a
    reader is deciding whether the bar is worth clearing."""
    (tmp_path / "benchmarks" / "results").mkdir(parents=True)
    state = bench_criterion.evaluate(tmp_path)
    assert "no recorded bench runs" in state.detail
    assert f"build {state.build_floor:.0%}" in state.detail


def test_the_message_follows_the_state_not_a_constant(tmp_path):
    """The drift this closes: two copies of the same number, one rendered and
    one recorded, agreeing only by coincidence."""
    _result(tmp_path, "result-2026-01-01-0000.yaml", 0.9, 0.9)
    state = bench_criterion.evaluate(tmp_path)
    assert state.build_floor == bench_criterion.BUILD_FLOOR
    assert state.probe_floor == bench_criterion.PROBE_FLOOR
    assert state.needed == bench_criterion.CONSECUTIVE_RUNS_TO_FIRE
    assert f"{state.streak}/{state.needed}" in state.detail
