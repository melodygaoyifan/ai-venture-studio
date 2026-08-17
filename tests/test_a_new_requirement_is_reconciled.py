"""ADR-046: a new request is read against the promises the product keeps.

The invariants, not the instance: an unreadable reconciliation never reads
as a clean one, a duplicate is refused in both modes, a contradiction
reaches a person before it reaches a planner, and nothing retires a promise
without the founder saying so.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
import yaml

from ai_venture_studio.upstream import reconcile as rec
from ai_venture_studio.upstream import requirements as req
from ai_venture_studio.upstream.requirements import Requirement, RequirementSlice


def _slice(*texts: str) -> RequirementSlice:
    return RequirementSlice(
        shown=[
            Requirement(id=f"R-{i + 1:03d}", text=t, spec_slug="orders",
                        status="built", verified_by=["tests/test_orders.py"])
            for i, t in enumerate(texts)
        ],
        matched=len(texts),
        cap=12,
    )


class _Provider:
    def __init__(self, answer: str, *, truncated: bool = False):
        self.answer = answer
        self.truncated = truncated

    def complete(self, **_kwargs) -> str:
        return self.answer


@pytest.fixture
def provider(monkeypatch):
    def install(answer: str, *, truncated: bool = False):
        monkeypatch.setattr(rec, "get_provider", lambda _name: _Provider(answer))
        monkeypatch.setattr(rec, "last_response_truncated", lambda: truncated)
    return install


def test_an_unreadable_answer_is_not_a_finding_of_no_conflict(provider) -> None:
    """The ADR-041 shape, in the one place it would hurt most: this gate
    exists to say no, so 'nobody looked' must never present as 'nothing
    conflicts'."""
    provider("the model wrote prose instead of yaml")
    result = rec.reconcile("add cancellation", _slice("The API shall place orders."))
    assert result.checked is False
    assert not result.relations
    assert result.note


def test_a_truncated_answer_is_not_a_finding_of_no_conflict(provider) -> None:
    provider(yaml.safe_dump({"relations": [
        {"requirement_id": "R-001", "relation": "extends", "reason": "r"}]}),
        truncated=True)
    result = rec.reconcile("add cancellation", _slice("The API shall place orders."))
    assert result.checked is False


def test_nothing_retrieved_is_not_a_clean_check(provider) -> None:
    provider("unused")
    result = rec.reconcile("add cancellation", RequirementSlice())
    assert result.checked is False


def test_a_verdict_about_an_unshown_requirement_is_dropped(provider) -> None:
    """An id the model invented names no promise; acting on it would
    supersede one chosen at random."""
    provider(yaml.safe_dump({"relations": [
        {"requirement_id": "R-999", "relation": "contradicts", "reason": "r"},
        {"requirement_id": "R-001", "relation": "extends", "reason": "r"},
    ]}))
    result = rec.reconcile("x", _slice("The API shall place orders."))
    assert [r.requirement_id for r in result.relations] == ["R-001"]


def test_an_answer_naming_only_unknown_ids_is_unchecked(provider) -> None:
    provider(yaml.safe_dump({"relations": [
        {"requirement_id": "R-999", "relation": "extends", "reason": "r"}]}))
    assert rec.reconcile("x", _slice("The API shall place orders.")).checked is False


def test_an_unknown_relation_word_is_dropped_not_guessed(provider) -> None:
    provider(yaml.safe_dump({"relations": [
        {"requirement_id": "R-001", "relation": "sort-of", "reason": "r"}]}))
    assert rec.reconcile("x", _slice("The API shall place orders.")).checked is False


def test_there_is_no_unclear_relation() -> None:
    """A gate that stops on its own uncertainty stops constantly. `ears.py`
    carries the scar; this pins the decision so it is not 'fixed' later."""
    assert set(rec.RELATIONS) == {"duplicate", "contradicts", "extends"}
    assert "unclear" not in rec._SYSTEM.lower().split()


def test_the_planner_view_hides_extends_and_names_the_rest(provider) -> None:
    provider(yaml.safe_dump({"relations": [
        {"requirement_id": "R-001", "relation": "extends", "reason": "fine"},
        {"requirement_id": "R-002", "relation": "contradicts", "reason": "clash"},
    ]}))
    result = rec.reconcile("x", _slice("a shall b", "c shall d"))
    rendered = rec.render_for_planner(result)
    assert "R-002" in rendered and "clash" in rendered
    assert "R-001" not in rendered


def test_an_unchecked_reconciliation_says_so_to_the_planner() -> None:
    unchecked = rec.Reconciliation(checked=False, note="did not parse")
    assert "not reconciled" in rec.render_for_planner(unchecked)
    assert "did not parse" in rec.render_for_planner(unchecked)


def test_supersede_only_moves_live_requirements(tmp_path: Path) -> None:
    req.save_ledger(tmp_path, [
        Requirement(id="R-001", text="a", spec_slug="s", status="built"),
        Requirement(id="R-002", text="b", spec_slug="s", status="retired"),
    ])
    moved = req.supersede(tmp_path, ["R-001", "R-002"], "product/features/03/fdr.md")
    assert moved == ["R-001"]
    after = {r.id: r for r in req.load_ledger(tmp_path)}
    assert after["R-001"].status == "superseded"
    assert after["R-001"].superseded_by == "product/features/03/fdr.md"
    assert after["R-002"].status == "retired"
    assert after["R-002"].superseded_by is None


def test_a_superseded_requirement_is_not_shown_to_the_next_planner(tmp_path: Path) -> None:
    req.save_ledger(tmp_path, [
        Requirement(id="R-001", text="The checkout shall charge on order.",
                    spec_slug="s", status="built"),
    ])
    assert req.relevant(tmp_path, "checkout charge").shown
    req.supersede(tmp_path, ["R-001"], "product/features/03/fdr.md")
    assert not req.relevant(tmp_path, "checkout charge").shown


# --- the wiring, which is where a gate stops being a gate --------------------


def _source(func) -> str:
    return inspect.getsource(func)


def test_reconciliation_runs_before_planning() -> None:
    """After planning it is a report; before planning it is a gate."""
    from ai_venture_studio.upstream import autopilot

    source = _source(autopilot.run_feature)
    assert source.index("_rec.reconcile(") < source.index("_FEATURE_PLANNER_SYSTEM")


def test_a_duplicate_stops_the_build_in_both_modes() -> None:
    """`yes` means 'do not ask me about the plan'. Building something the
    product already does is waste in either mode, and the evidence for the
    refusal is a test that already passes — a fact, not a judgment."""
    from ai_venture_studio.upstream import autopilot

    tree = ast.parse(_source(autopilot.run_feature).lstrip())
    guards = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If) and "duplicates" in ast.unparse(node.test)
    ]
    assert guards, "no branch acts on duplicates"
    for guard in guards:
        assert "yes" not in ast.unparse(guard.test), (
            "a duplicate refusal must not depend on --yes"
        )


def test_a_contradiction_never_silently_retires_a_promise() -> None:
    """Under --yes the build proceeds, but the SCR is RAISED and not
    APPROVED: --yes authorizes the build, not the retirement."""
    from ai_venture_studio.upstream import autopilot

    source = _source(autopilot._propose_conflict_scrs)
    assert "raise_scr" in source
    assert "approve_scr" not in source


def test_only_an_explicit_replace_supersedes_anything() -> None:
    from ai_venture_studio.upstream import autopilot

    source = _source(autopilot.run_feature)
    assert "settled = set(replace or [])" in source
    # And the supersession waits for the build, like the amendment does.
    assert source.index("supersede(root, to_supersede") > source.index(
        "_retry_failed_tasks("
    )


def test_a_failed_build_does_not_retire_the_old_promise() -> None:
    from ai_venture_studio.upstream import autopilot

    source = _source(autopilot.run_feature)
    guard = source[source.index("if to_supersede"):source.index("supersede(root")]
    assert 'o.status == "built"' in guard


def test_the_bench_does_not_score_a_correct_refusal_as_a_failure() -> None:
    from ai_venture_studio import product_bench

    source = inspect.getsource(product_bench)
    assert '"completed", "already_satisfied"' in source


def test_the_whole_path_refuses_a_duplicate_and_says_so_to_the_founder(
    tmp_path: Path, monkeypatch
) -> None:
    """Not the parts — the path. `avs add` on a request the product already
    promises must reach the founder as a file they can read, having built
    nothing."""
    import ai_venture_studio.upstream.build as build_mod
    from ai_venture_studio import testing as testing_mod
    from ai_venture_studio.upstream.autopilot import run_feature
    from ai_venture_studio.upstream.workspace import init_workspace

    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    monkeypatch.setattr(build_mod, "docker_available", lambda: False)

    root = init_workspace(tmp_path / "prod", "prod", "web")
    spec_dir = root / "specs" / "orders"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.yaml").write_text(
        yaml.safe_dump({
            "slug": "orders", "title": "orders", "status": "built",
            "request": "orders", "profile": "web", "design": "",
            "criteria": ["The system shall let a resident cancel an order."],
            "test_skeletons": [{"path": "tests/test_orders.py", "purpose": "p",
                                "covers": [0]}],
            "built": True,
        }, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    req.sync_ledger(root)
    assert [r.id for r in req.load_ledger(root)] == ["R-001"]

    fdr = root / "feature.md"
    fdr.write_text(
        "MOCK_DUPLICATE let a resident cancel an order\n"
        "住户可以取消自己的订单。必须有：取消入口。",
        encoding="utf-8",
    )
    result = run_feature(root, fdr, provider="mock", yes=True)

    assert result.status == "already_satisfied"
    assert "R-001" in (result.blocked_reason or "")
    told = (root / "FDR-ALREADY-BUILT.md").read_text(encoding="utf-8")
    assert "R-001" in told and "tests/test_orders.py" in told
    # Nothing was built, and the promise it matched is untouched.
    assert not result.outcomes
    assert req.load_ledger(root)[0].status == "built"
    # And the verdict is on disk beside the feature, not only in the return.
    feature_dir = sorted((root / "product" / "features").iterdir())[-1]
    saved = yaml.safe_load((feature_dir / "reconciliation.yaml").read_text())
    assert saved["checked"] is True


def test_the_founder_is_told_which_promise_and_which_test(tmp_path: Path) -> None:
    """An id alone is not something a non-technical person can act on."""
    from ai_venture_studio.upstream import autopilot

    req.save_ledger(tmp_path, [
        Requirement(id="R-001", text="The API shall cancel an order.",
                    spec_slug="orders", status="built",
                    verified_by=["tests/test_cancel.py"]),
    ])
    lines = autopilot._requirement_lines(
        tmp_path, [rec.Relation(requirement_id="R-001", relation="duplicate",
                                reason="already cancels orders")]
    )
    assert "R-001" in lines
    assert "The API shall cancel an order." in lines
    assert "tests/test_cancel.py" in lines
    assert "already cancels orders" in lines
