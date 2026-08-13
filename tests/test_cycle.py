"""v0.36.0 — the live-cycle reader behind the v3.0.0 design gate.

The load-bearing assertions here are the refusals: a cycle where nothing
fired is not the gate, a 'continue' decision is not the gate, and an entry
above P0 must state its reason.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from ai_venture_studio.product.cycle import read_cycle

REPO = pathlib.Path(__file__).resolve().parents[1]


def _cycle(tmp_path, *, entry=None, reason=None, files=(), evaluation=None):
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x: 1\n", encoding="utf-8")
    if entry:
        block = {"entry_stage": entry}
        if reason:
            block["entry_reason"] = reason
        (tmp_path / "cycle.yaml").write_text(yaml.safe_dump({"cycle": block}))
    if evaluation is not None:
        (tmp_path / "gate-pl5-evaluation.yaml").write_text(
            yaml.safe_dump({"evaluation": evaluation})
        )
    return read_cycle(tmp_path)


def test_empty_directory_reports_everything_missing(tmp_path):
    state = _cycle(tmp_path)
    assert [s.id for s in state.stages if s.present] == []
    assert state.design_gate_met is False
    assert "run P0" in state.next_action


def test_quiet_cycle_is_not_the_gate(tmp_path):
    """Nothing fired, no decision due — V3-1/V3-2 can be met and the gate
    still is not. This is the whole point of the instrument."""
    state = _cycle(
        tmp_path,
        files=("signals.yaml", "market.yaml", "prd.yaml", "post.md",
               "evidence-report.yaml"),
        evaluation={"fired": [], "requires_human_decision": False},
    )
    met = {c.id: c.met for c in state.criteria}
    assert met["V3-1"] is True and met["V3-2"] is True
    assert met["V3-3"] is False
    assert state.design_gate_met is False
    detail = next(c.detail for c in state.criteria if c.id == "V3-3")
    assert "not because anyone chose it" in detail


def test_continue_decision_does_not_close_the_gate(tmp_path):
    state = _cycle(
        tmp_path,
        files=("signals.yaml", "market.yaml", "prd.yaml", "post.md",
               "evidence-report.yaml"),
        evaluation={"fired": ["build rate below floor"],
                    "requires_human_decision": True,
                    "human_decision": "continue"},
    )
    assert state.pl5_decision == "continue"
    assert state.design_gate_met is False
    detail = next(c.detail for c in state.criteria if c.id == "V3-3")
    assert "needs a kill or a pivot" in detail


@pytest.mark.parametrize("decision", ["kill", "pivot", "KILL"])
def test_recorded_kill_or_pivot_closes_the_gate(tmp_path, decision):
    state = _cycle(
        tmp_path,
        files=("signals.yaml", "market.yaml", "prd.yaml", "post.md",
               "evidence-report.yaml"),
        evaluation={"fired": ["build rate below floor"],
                    "requires_human_decision": True,
                    "human_decision": decision},
    )
    assert state.pl5_decision == decision.lower()
    assert state.design_gate_met is True
    assert "gate met" in state.next_action


def test_fired_criterion_without_a_decision_demands_one(tmp_path):
    state = _cycle(
        tmp_path,
        files=("signals.yaml", "market.yaml", "prd.yaml", "post.md",
               "evidence-report.yaml"),
        evaluation={"fired": ["build rate below floor"],
                    "requires_human_decision": True},
    )
    assert state.pl5_requires_human_decision is True
    assert state.pl5_decision is None
    assert "invariant 14.20" in state.next_action
    detail = next(c.detail for c in state.criteria if c.id == "V3-3")
    assert "DUE but unrecorded" in detail


def test_entry_above_p0_requires_a_reason(tmp_path):
    with pytest.raises(ValueError, match="entry_reason is required"):
        _cycle(tmp_path, entry="P2")


def test_unknown_entry_stage_errors(tmp_path):
    with pytest.raises(ValueError, match="not one of"):
        _cycle(tmp_path, entry="P9", reason="because")


def test_declared_entry_narrows_the_span(tmp_path):
    state = _cycle(
        tmp_path, entry="P2", reason="the product predates the loop",
        files=("prd.yaml", "post.md", "evidence-report.yaml"),
        evaluation={"fired": [], "requires_human_decision": False},
    )
    v3_1 = next(c for c in state.criteria if c.id == "V3-1")
    assert v3_1.met is True  # P0/P1 out of scope by declaration
    assert "P2-P5" in v3_1.requirement
    assert "the product predates the loop" in v3_1.detail


# --- this repo's own live cycle -------------------------------------------------


def test_this_repos_cycle_is_read_honestly():
    """The launch cycle: entered at P2 with a recorded reason, PL5 evaluated
    mechanically, and the gate NOT met because no decision was due."""
    state = read_cycle(REPO / "launch")
    assert state.entry_stage == "P2" and state.entry_reason
    met = {c.id: c.met for c in state.criteria}
    assert met["V3-1"] is True  # P2-P5 all have artifacts
    assert met["V3-2"] is True  # gate-pl5-evaluation.yaml exists
    assert met["V3-3"] is False  # nothing fired => no decision => not the gate
    assert state.design_gate_met is False


def test_the_runbook_documents_the_human_only_criterion():
    text = (REPO / "docs" / "v3-live-loop.md").read_text(encoding="utf-8")
    assert "14.20" in text  # the invariant that makes V3-3 human-only
    assert "human_decision" in text  # the exact field to record


# --- the withdrawn axis (v0.81.0, ADR-033) -----------------------------------


def test_the_cycle_no_longer_reads_an_attention_log(tmp_path):
    """Pinned as firmly as the presence was. The weekly-hours series was one
    of two kill-criterion axes and is withdrawn; a stray reader would revive
    a number nothing collects — and would report progress toward a criterion
    that can no longer fire."""
    cycle = tmp_path / "launch"
    cycle.mkdir(parents=True)
    for name in ("signals.yaml", "market.yaml", "prd.yaml", "post.md",
                 "evidence-report.yaml"):
        (cycle / name).write_text("x: 1\n", encoding="utf-8")
    (cycle / "gate-pl5-evaluation.yaml").write_text(
        yaml.safe_dump({"evaluation": {"fired": [],
                                       "requires_human_decision": False}}),
        encoding="utf-8",
    )
    log = tmp_path / "metrics" / "attention-log.yaml"
    log.parent.mkdir(parents=True)
    log.write_text(yaml.safe_dump({"log": [
        {"week": f"2026-W{26 + i}", "window": "a..b", "hours": 9.0,
         "status": "logged", "decided_by": "melody"} for i in range(4)
    ]}), encoding="utf-8")

    state = read_cycle(cycle)

    # Four over-budget weeks: this used to fire the criterion outright.
    assert not hasattr(state, "attention")
    assert "attention" not in state.next_action
    assert "HAS FIRED" not in state.next_action


def test_the_reader_module_is_gone(tmp_path):
    with pytest.raises(ModuleNotFoundError):
        import ai_venture_studio.attention  # noqa: F401
