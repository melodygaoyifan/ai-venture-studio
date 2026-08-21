"""D14: the GEPA proposer — budgeted, holdout-scored, nothing self-installs."""

import pytest

from ai_venture_studio.cascade import GepaBudget
from ai_venture_studio.gepa import (
    GepaError,
    GepaProposal,
    holdout_split,
    propose_charter,
    write_proposal,
)
from ai_venture_studio.product.voter_gate import VoterFixture


def _fixtures(n=8):
    kinds = ["positive"] * 4 + ["negative"] * 2 + ["boundary"] * 2
    return [
        VoterFixture(label=f"case-{i:02d}", kind=kinds[i % 8],
                     should_find=kinds[i % 8] == "positive",
                     artifact=f"artifact body {i}")
        for i in range(n)
    ]


def _budget(**over):
    base = {"targets": ["product/prd-metrics"], "budget_rollouts_weekly": 3,
                "holdout_fixture_fraction": 0.25}
    base.update(over)
    return GepaBudget(**base)


def test_holdout_split_is_deterministic_and_disjoint():
    fixtures = _fixtures(16)
    train1, hold1 = holdout_split(fixtures, fraction=0.25, salt="s1")
    train2, hold2 = holdout_split(fixtures, fraction=0.25, salt="s1")
    assert [f.label for f in train1] == [f.label for f in train2]
    assert [f.label for f in hold1] == [f.label for f in hold2]
    assert hold1  # never empty
    assert {f.label for f in train1}.isdisjoint({f.label for f in hold1})
    assert len(train1) + len(hold1) == 16


def test_holdout_split_changes_with_salt():
    fixtures = _fixtures(32)
    _, hold_a = holdout_split(fixtures, fraction=0.25, salt="a")
    _, hold_b = holdout_split(fixtures, fraction=0.25, salt="b")
    assert {f.label for f in hold_a} != {f.label for f in hold_b}


def test_zero_budget_refuses():
    with pytest.raises(GepaError, match="disabled"):
        propose_charter(target="product/prd-metrics", current_charter="old",
                        fixtures=_fixtures(), provider="mock",
                        budget=_budget(budget_rollouts_weekly=0),
                        score_fn=lambda c, h: 1.0)


def test_unlisted_target_refuses():
    with pytest.raises(GepaError, match="targets"):
        propose_charter(target="product/rogue-voter", current_charter="old",
                        fixtures=_fixtures(), provider="mock",
                        budget=_budget(), score_fn=lambda c, h: 1.0)


def test_improved_candidate_emits_charter():
    def score(charter, holdout):
        return 1.0 if "mock improved" in charter else 0.5

    proposal = propose_charter(
        target="product/prd-metrics", current_charter="old charter body",
        fixtures=_fixtures(), provider="mock", budget=_budget(),
        score_fn=score)
    assert proposal.improved
    assert proposal.candidate_holdout_rate > proposal.baseline_holdout_rate
    assert "mock improved" in proposal.candidate_charter
    assert "nothing self-installs" in proposal.note


def test_no_improvement_emits_record_only():
    proposal = propose_charter(
        target="product/prd-metrics", current_charter="old charter body",
        fixtures=_fixtures(), provider="mock", budget=_budget(),
        score_fn=lambda c, h: 0.875)  # tie — strict > required
    assert not proposal.improved
    assert proposal.candidate_charter == ""
    assert "inconclusive" in proposal.note


def test_proposer_never_sees_holdout_labels(monkeypatch):
    seen = {}
    from ai_venture_studio.providers import base as provider_base
    real = provider_base.get_provider

    class Spy:
        def complete(self, *, model, system, user, max_tokens=4096):
            seen["user"] = user
            return real("mock").complete(model=model, system=system,
                                         user=user, max_tokens=max_tokens)

    monkeypatch.setattr("ai_venture_studio.gepa.get_provider", lambda name: Spy())
    fixtures = _fixtures(16)
    _, holdout = holdout_split(fixtures, fraction=0.25, salt="gepa")
    propose_charter(target="product/prd-metrics", current_charter="old",
                    fixtures=fixtures, budget=_budget(),
                    score_fn=lambda c, h: 0.0)
    for fixture in holdout:
        assert fixture.label not in seen["user"]


def test_write_proposal_lands_in_mas(tmp_path):
    proposal = GepaProposal(target="product/prd-metrics",
                            baseline_holdout_rate=0.5,
                            candidate_holdout_rate=0.75, improved=True)
    path = write_proposal(tmp_path, proposal, at="2026-07-26")
    assert path.exists()
    assert ".mas" in str(path)
    assert "prd-metrics" in path.name
