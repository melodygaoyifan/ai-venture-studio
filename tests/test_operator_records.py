"""The live operator-side records (P20 remainder): the first Gate PL5
evaluation and the launch experiment's power verdict — each mechanically
consistent with the machinery it claims to have run.
"""

from __future__ import annotations

import pathlib

import yaml

from ai_venture_studio.evidence.cohort import required_n_two_proportions
from ai_venture_studio.experiment.design import verify_at_analysis

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load(rel: str) -> dict:
    return yaml.safe_load((REPO / rel).read_text())


def test_experiment_run_pin_still_verifies():
    """The run record's claim 'pin verified' must be re-derivable — a
    drifted design voids the analysis, not just the assertion."""
    run = _load("launch/experiment-run.yaml")["run"]
    text = (REPO / "launch" / "experiment.yaml").read_text()
    verify_at_analysis(text, run["preregistration_hash"])  # raises on drift
    assert run["preregistration_verified"] is True


def test_experiment_run_power_numbers_are_rederivable():
    run = _load("launch/experiment-run.yaml")["run"]
    design = _load("launch/experiment.yaml")["experiment"]["power"]
    n = required_n_two_proportions(
        design["baseline"], design["mde_relative"],
        alpha=design["alpha"], power=design["power"],
    )
    assert run["power_check"]["required_n_per_arm"] == n
    assert run["power_check"]["required_n_total"] == 2 * n
    available = run["power_check"]["available_traffic"]["unique_visitors_14d"]
    # The verdict must match the arithmetic, whichever way it points.
    blocked = run["power_check"]["verdict"] == "BLOCKED(INSUFFICIENT_POWER)"
    assert blocked == (available < 2 * n)


def test_experiment_run_evidence_is_typed():
    run = _load("launch/experiment-run.yaml")["run"]
    evidence = run["power_check"]["available_traffic"]["evidence"]
    for key in ("method", "locator", "retrieved_at"):
        assert str(evidence.get(key, "")).strip(), key
    fallback = run["qualitative_fallback"]
    assert fallback["asked"] == len(fallback["answers"])  # n recorded, not inferred


def test_pl5_evaluation_stays_internally_consistent():
    evaluation = _load("launch/gate-pl5-evaluation.yaml")["evaluation"]
    assert evaluation["requires_human_decision"] == bool(
        evaluation["fired"] or evaluation["loop_budget_exhausted"]
    )
    assert evaluation["loop_index"] <= evaluation["max_loops"]


def test_a_withdrawn_criterion_is_superseded_never_erased():
    """The attention axis this record evaluated was withdrawn in v0.81.0
    (ADR-033). The reading it recorded on 2026-07-26 must survive verbatim —
    an evidence snapshot is not edited after the fact — with the withdrawal
    appended beside it, pointing at the decision."""
    evaluation = _load("launch/gate-pl5-evaluation.yaml")["evaluation"]
    recorded = evaluation["criteria"][0]
    assert "weekly maintenance attention" in recorded["text"]
    assert recorded["fired"] is False
    assert "0 of the 4 required consecutive weeks exist" in recorded["reading"]

    superseded = evaluation["superseded_by"]
    assert (REPO / superseded["ref"]).exists()
    assert "WITHDRAWN, not satisfied" in superseded["note"]
