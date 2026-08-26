"""A revision is an edit, not a re-roll (ADR-082).

Run 19b, case 04, on v0.123.0: the planner produced a collision-free
arrangement on attempt 3 and lost it to a trailing prose paragraph that
broke the YAML parse. The nudge (ADR-080) fired — for the first time live
— but the corrective feedback named only the error, not the response, so
the model regenerated the whole plan from scratch and introduced a NEW
lane collision, which the dag revisions then failed to clear for the same
reason: the feedback listed the issues and never the plan they were
issues *with*. Provider calls are stateless; a model asked to "fix that
exact problem" in a plan it cannot see can only re-roll.

Every corrective path now shows the planner the thing it is revising:
the parsed plan as the checker read it (dag/critic revisions — including
files_expected the blast_radius fallback derived, which the planner never
wrote and so could never narrow), the raw response (parse nudges), and
the cut-off response (truncation).
"""

import shutil

import pytest

from ai_venture_studio.providers import get_provider

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

BROKEN = "tasks:\n  - id: t1\n   title: *not a yaml anchor\n"

# Legal everywhere except lane_check: two lanes, one file.
COLLIDING = """tasks:
  - id: "t1"
    title: "api endpoints"
    description: "serve the shortener api"
    depends_on: []
    lane: "api"
    estimate_hours: 2
    files_expected: ["app/models.py"]
  - id: "t2"
    title: "web pages"
    description: "render the pages"
    depends_on: []
    lane: "web"
    estimate_hours: 2
    files_expected: ["app/models.py"]
"""


class _Scripted:
    """Plays `responses` to the planner (recording each planner prompt),
    then defers every prompt — planner included — to the real mock."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.planner_prompts: list[str] = []
        self._mock = get_provider("mock")

    def complete(self, **kwargs):
        from ai_venture_studio.upstream.plan import PLANNER_MARKER

        if PLANNER_MARKER in kwargs.get("system", ""):
            self.planner_prompts.append(kwargs.get("user", ""))
            if self.responses:
                return self.responses.pop(0)
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


def _plan_with(monkeypatch, tmp_path, stub):
    from ai_venture_studio.upstream import plan as plan_mod

    root = _workspace(tmp_path)
    monkeypatch.setattr(plan_mod, "get_provider", lambda name: stub)
    return plan_mod.run_planning(root, provider="mock")


def test_a_dag_revision_shows_the_plan_being_revised(monkeypatch, tmp_path):
    """The run-19b failure mode: told about a lane collision, never shown
    the plan that contains it."""
    stub = _Scripted([COLLIDING])
    plan = _plan_with(monkeypatch, tmp_path, stub)

    assert plan.status == "proposed", plan.dag_issues
    assert plan.revisions == 1
    first, second = stub.planner_prompts[0], stub.planner_prompts[1]
    assert "<your_previous_response>" not in first
    assert "<your_previous_response>" in second
    # The collision AND the plan it lives in, in the same prompt.
    assert "lane collision" in second
    assert "app/models.py" in second
    assert "t1" in second and "t2" in second
    assert "keep every task the issues do not name" in second


def test_a_parse_nudge_shows_the_response_that_broke(monkeypatch, tmp_path):
    """ADR-079 named the break, ADR-080 delivered the feedback, and the
    model still could not see what it was fixing — case 04's collision-free
    attempt 3 was regenerated instead of repaired."""
    stub = _Scripted([BROKEN])
    plan = _plan_with(monkeypatch, tmp_path, stub)

    assert plan.status == "proposed", plan.dag_issues
    assert plan.revisions == 0, "a nudge is still not a revision (ADR-080)"
    second = stub.planner_prompts[1]
    assert "<your_previous_response>" in second
    assert "*not a yaml anchor" in second, (
        "the broken response itself must ride in the nudge"
    )
    assert "Fix ONLY the parse problem" in second


def test_a_truncation_retry_shows_the_cut_off_response(monkeypatch, tmp_path):
    """'Return the SAME plan with shorter descriptions' — of a plan the
    model was never shown."""
    from ai_venture_studio.upstream import plan as plan_mod

    truncated_once = iter([True])
    monkeypatch.setattr(
        plan_mod, "last_response_truncated",
        lambda: next(truncated_once, False),
    )
    stub = _Scripted(['tasks:\n  - id: "t9"\n    title: "half a plan cut mid'])
    plan = _plan_with(monkeypatch, tmp_path, stub)

    assert plan.status == "proposed", plan.dag_issues
    second = stub.planner_prompts[1]
    assert "CUT OFF" in second
    assert "<your_previous_response>" in second
    assert "half a plan cut mid" in second


def test_the_shown_response_is_bounded():
    """The block rides inside the next prompt; an unbounded paste of a
    pathological response must not blow the input budget."""
    from ai_venture_studio.upstream import plan as plan_mod

    out = plan_mod._shown_back("x" * 50_000, "edit it")
    assert "x" * plan_mod._PREV_RESPONSE_CHARS in out
    assert "x" * (plan_mod._PREV_RESPONSE_CHARS + 1) not in out
    assert out.rstrip().endswith("edit it")
