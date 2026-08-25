"""A parse failure must say what broke and keep what broke (ADR-079).

Run 19b, case 04: the planner's response failed to parse three times, the
revision feedback each time said only "no YAML mapping with any of
('tasks',) found in response (2457 chars)" — a message that names no
problem — and after the case died, nothing of the responses survived but
a 160-char whitespace-collapsed opening. $10 of planner attempts, zero
evidence. Two pins:

1. `extract_mapping`'s ValueError carries the parser's own objection
   ("closest attempt: …"), so the revision prompt shows the model the
   actual break — the upgrade the comment in plan.py already argued for
   (run 16's case 02, ADR-041) but only delivered for non-yamlx errors.
2. A failed planner attempt preserves its full raw response at
   `.mas/failed-plans/attempt-N.txt`, the way failed builds keep their
   worktree, and the blocked reason names the path.
"""

import shutil

import pytest

from ai_venture_studio.yamlx import extract_mapping

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


def test_the_error_carries_the_parsers_own_objection():
    broken = "tasks:\n  - id: t1\n   title: *not a yaml anchor\n"
    with pytest.raises(ValueError, match="closest attempt:") as excinfo:
        extract_mapping(broken, ("tasks",))
    # Whatever yaml said, the location survives — that is the part the
    # model can act on.
    assert "line" in str(excinfo.value)


def test_prose_is_named_as_prose_not_as_absence():
    with pytest.raises(ValueError, match="parsed to str, not a mapping"):
        extract_mapping("just prose, no yaml at all", ("tasks",))


def test_a_mapping_with_the_wrong_keys_names_the_keys_it_has():
    with pytest.raises(ValueError, match="its keys are"):
        extract_mapping("plan:\n  - id: t1\n", ("tasks",))


class _Unparseable:
    """A planner that never parses — three attempts, three failures."""

    BROKEN = "tasks:\n  - id: t1\n   title: *not a yaml anchor\n"

    def complete(self, **kwargs):
        from ai_venture_studio.product.stage_engine import (
            PRODUCT_LEADER_MARKER,
            PRODUCT_VERIFIER_MARKER,
            PRODUCT_VOTER_MARKER,
        )

        system = kwargs.get("system", "")
        if PRODUCT_VOTER_MARKER in system:
            return "findings: []"
        if PRODUCT_VERIFIER_MARKER in system:
            return "verdict: refuted\nreason: stub"
        if PRODUCT_LEADER_MARKER in system:
            return "summary: stub leader"
        return self.BROKEN


def test_every_failed_attempt_leaves_its_response_on_disk(
    monkeypatch, tmp_path
):
    from ai_venture_studio.upstream import (
        approve_brief,
        init_workspace,
        run_discovery,
    )
    from ai_venture_studio.upstream import plan as plan_mod

    root = init_workspace(tmp_path / "p", "p", "web")
    run_discovery(root, "a link shortener", provider="mock")
    approve_brief(root)
    monkeypatch.setattr(plan_mod, "get_provider", lambda name: _Unparseable())

    plan = plan_mod.run_planning(root, provider="mock")

    assert plan.status == "blocked"
    kept = sorted((root / ".mas" / "failed-plans").glob("attempt-*.txt"))
    assert [p.name for p in kept] == [
        f"attempt-{n}.txt" for n in range(1, plan_mod.MAX_REVISIONS + 2)
    ], "one preserved response per attempt, all attempts"
    assert kept[0].read_text(encoding="utf-8") == _Unparseable.BROKEN, (
        "the preserved response is the raw text, not a collapsed opening"
    )
    issues = " | ".join(plan.dag_issues)
    assert ".mas/failed-plans/" in issues, (
        "the blocked reason must say where the evidence is"
    )
