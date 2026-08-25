"""A parse failure spends a nudge, not a revision (ADR-080).

Run 19b, case 04, third run: attempts 1–2 parsed and were revised on
substance; attempt 3 broke at an unquoted ``{"error": …}`` inside a
description — and because parse failures and plan revisions shared
`MAX_REVISIONS`, the loop was exhausted, so the corrective feedback
(which since ADR-079 finally names the line and column) was composed and
never shown to the model. Same failure shape as ADR-075's voter
no-verdicts, same remedy: protocol failures get their own bounded budget
(`_MAX_PARSE_NUDGES`), so a revision spent arguing about the plan can
never silently pay for a response that failed to arrive as YAML.
"""

import shutil

import pytest

from ai_venture_studio.providers import get_provider

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

BROKEN = "tasks:\n  - id: t1\n   title: *not a yaml anchor\n"


class _ParseFlaky:
    """Fails to parse for the first `failures` planner calls, then defers
    every prompt — planner included — to the real mock provider."""

    def __init__(self, failures: int):
        self.failures = failures
        self.planner_calls = 0
        self._mock = get_provider("mock")

    def complete(self, **kwargs):
        from ai_venture_studio.upstream.plan import PLANNER_MARKER

        if PLANNER_MARKER in kwargs.get("system", ""):
            self.planner_calls += 1
            if self.planner_calls <= self.failures:
                return BROKEN
        return self._mock.complete(**kwargs)


def _workspace(tmp_path):
    from ai_venture_studio.upstream import (
        approve_brief,
        init_workspace,
        run_discovery,
    )

    root = init_workspace(tmp_path / "p", "p", "web")
    run_discovery(root, "a link shortener", provider="mock")
    approve_brief(root)
    return root


def test_a_parse_failure_spends_a_nudge_not_a_revision(monkeypatch, tmp_path):
    from ai_venture_studio.upstream import plan as plan_mod

    root = _workspace(tmp_path)
    stub = _ParseFlaky(failures=plan_mod._MAX_PARSE_NUDGES)
    monkeypatch.setattr(plan_mod, "get_provider", lambda name: stub)

    plan = plan_mod.run_planning(root, provider="mock")

    assert plan.status == "proposed", plan.dag_issues
    assert plan.revisions == 0, (
        "both failures were protocol failures; the plan itself was never "
        "revised — a nudge that counts as a revision is run 19b's bug"
    )
    assert stub.planner_calls == plan_mod._MAX_PARSE_NUDGES + 1
    kept = sorted((root / ".mas" / "failed-plans").glob("attempt-*.txt"))
    assert [p.name for p in kept] == ["attempt-1.txt", "attempt-2.txt"]


def test_the_nudge_budget_is_bounded(monkeypatch, tmp_path):
    """A planner that never parses must still terminate — and the total
    spend is the two budgets summed, not a hang and not a single merged
    pool."""
    from ai_venture_studio.upstream import plan as plan_mod

    root = _workspace(tmp_path)
    stub = _ParseFlaky(failures=10_000)
    monkeypatch.setattr(plan_mod, "get_provider", lambda name: stub)

    plan = plan_mod.run_planning(root, provider="mock")

    assert plan.status == "blocked"
    assert stub.planner_calls == (
        plan_mod.MAX_REVISIONS + 1 + plan_mod._MAX_PARSE_NUDGES
    )
