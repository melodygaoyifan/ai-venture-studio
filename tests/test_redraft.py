"""Disagreeing with a draft, without writing anything.

A requirement change comes back as a draft: a summary, the acceptance
criteria it would leave behind, and — the interesting part — the list of
decisions the model had to make because the founder did not say. Those
assumptions were put on the card so a wrong one would be visible *before*
it was built.

Then the only control under them built all of them anyway. Seeing that an
assumption was wrong and being able to say so are different things: the
founder's whole vocabulary was one button that meant yes, and one browser
back button that meant "start the complaint again from nothing". For the
person this product is written for — assumed as lazy and as non-technical
as it is possible to be — that is not a choice, it is a dead end with a
list of reasons in it.

So: one tap per assumption, and the model does the rewriting. Words stay
available and stay second, because a tap is already a complete answer.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import threading

import pytest
import yaml
from fastapi.testclient import TestClient

from ai_venture_studio import testing as testing_mod
from ai_venture_studio.studio import create_studio_app
from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.correction import (
    CHANGE_MARKER,
    CORRECTION_MARKER,
    MAX_REDRAFTS,
    ChangePlan,
    CorrectionRoute,
    draft_change,
)
from ai_venture_studio.executables import resolve

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import ai_venture_studio.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def _workspace(tmp_path, lang="en"):
    """One built feature, committed. Drafting reads a spec and writes
    nothing, so this is everything the whole path needs."""
    root = init_workspace(tmp_path / "shop", "shop", "web")
    spec_dir = root / "specs" / "cart"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.yaml").write_text(yaml.safe_dump({
        "slug": "cart", "title": "Shopping cart", "built": True,
        "request": "a cart", "design": "one module", "profile": "web",
        "criteria": ["The system shall keep items in a cart."],
        "test_skeletons": [],
    }), encoding="utf-8")
    subprocess.run([resolve("git"), "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [resolve("git"), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "built"],
        cwd=root, check=True, capture_output=True,
    )
    client = TestClient(
        create_studio_app(root, spawn=lambda r: 1, provider="mock", lang=lang),
        raise_server_exceptions=False,
    )
    return client, root


def _plan(root, **over) -> ChangePlan:
    """The draft a founder is looking at when they press "not that"."""
    fields = {
        "spec_slug": "cart",
        "summary": "The cart will remember what you put in it.",
        "criteria": ["The system shall keep items in a cart.",
                     "The system shall keep the cart after a browser restart."],
        "assumptions": ["the cart is kept for thirty days",
                        "a logged-out visitor keeps their own cart"],
        "words": "the cart should remember things",
        "instruction": "persist the cart across sessions",
    }
    fields.update(over)
    return ChangePlan(**fields)


def _card(client, plan: ChangePlan) -> str:
    """The change card as the founder sees it, straight from the renderer
    the correction path uses — reached by pressing something on it, which
    is the only way the plan ever gets rendered."""
    from ai_venture_studio.upstream.correction import CorrectionResult

    result = CorrectionResult(
        status="change_planned", spec_slug=plan.spec_slug,
        kind="scope_change", reason="planned", plan=plan,
    )
    import ai_venture_studio.upstream.correction as correction_mod

    original = correction_mod.run_correction
    correction_mod.run_correction = lambda *a, **k: result
    try:
        return client.post("/correct/confirm", data={
            "complaint": plan.words, "spec_slug": [plan.spec_slug],
            "kind": ["scope_change"], "quote": [plan.words],
            "instruction": [plan.instruction],
        }, follow_redirects=True).text
    finally:
        correction_mod.run_correction = original


def _tap(client, plan: ChangePlan, index: int) -> str:
    return client.post("/correct/redraft", data={
        "plan": json.dumps(plan.model_dump()), "index": str(index),
    }, follow_redirects=True).text


def _carried(page: str) -> ChangePlan:
    """The plan the page is carrying in its hidden field — the only place
    a draft exists between one request and the next."""
    found = re.findall(r'name=plan value="([^"]*)"', page)
    assert found, "the page carried no plan"
    return ChangePlan.model_validate(json.loads(html.unescape(found[-1])))


def _prompts(monkeypatch) -> list[tuple[str, str]]:
    """Every (system, user) pair the page sends to the model."""
    from ai_venture_studio.providers import mock as mock_mod

    seen: list[tuple[str, str]] = []
    original = mock_mod.MockProvider.complete

    def spy(self, *, model, system, user, **kw):
        seen.append((system, user))
        return original(self, model=model, system=system, user=user, **kw)

    monkeypatch.setattr(mock_mod.MockProvider, "complete", spy)
    return seen


# ── the tap ──────────────────────────────────────────────────────────────


def test_every_assumption_can_be_rejected_with_one_tap(tmp_path):
    """The founder's entire required vocabulary. Before this, an assumption
    was a statement they could read and not answer."""
    client, root = _workspace(tmp_path)
    plan = _plan(root)
    page = _card(client, plan)

    for i, assumption in enumerate(plan.assumptions):
        assert html.escape(assumption) in page
        assert f"name=index value='{i}'" in page
    assert page.count("action='/correct/redraft'") == len(plan.assumptions) + 1


def test_rejecting_an_assumption_redrafts_it(tmp_path):
    """"Not that" has to produce a different draft. A redraft that comes
    back with the same decision reworded is the failure this exists to
    prevent — it looks like it listened and changed nothing."""
    client, root = _workspace(tmp_path)
    plan = _plan(root)

    after = _carried(_tap(client, plan, 0))

    assert after.assumptions != plan.assumptions
    assert plan.assumptions[0] not in after.assumptions


def test_the_rejection_is_carried_so_it_cannot_come_back(tmp_path):
    """The draft is never written down — the plan in the hidden field is
    the only memory this path has. Without the rejection on it, a second
    redraft could propose the assumption the founder already turned
    down."""
    client, root = _workspace(tmp_path)
    plan = _plan(root)

    after = _carried(_tap(client, plan, 0))

    assert after.rejected == [plan.assumptions[0]]


def test_the_model_is_told_the_decision_is_off_limits(tmp_path, monkeypatch):
    """Carrying the rejection is only half of it — it has to reach the
    prompt, and the prompt has to forbid re-proposing it rather than
    merely mentioning it."""
    client, root = _workspace(tmp_path)
    seen = _prompts(monkeypatch)
    plan = _plan(root)

    _tap(client, plan, 1)

    system, user = seen[-1]
    assert CHANGE_MARKER in system
    assert "Never propose any of them again" in system
    assert f"<rejected_assumptions>\n- {plan.assumptions[1]}" in user


def test_a_redraft_does_not_re_route_the_complaint(tmp_path, monkeypatch):
    """The founder is disagreeing with a detail, not re-reporting the
    problem. A second router call is free to classify the same words as a
    repair, or against another feature, and they would be shown a redraft
    of a change they never asked for."""
    client, root = _workspace(tmp_path)
    seen = _prompts(monkeypatch)

    _tap(client, _plan(root), 0)

    assert [s for s, _u in seen if CORRECTION_MARKER in s] == []
    assert len([s for s, _u in seen if CHANGE_MARKER in s]) == 1


def test_the_redraft_gets_the_same_instruction_the_first_draft_had(tmp_path, monkeypatch):
    """Which is why the plan carries it. The only difference between the
    draft and the redraft has to be the rejection."""
    client, root = _workspace(tmp_path)
    seen = _prompts(monkeypatch)
    plan = _plan(root)

    _tap(client, plan, 0)

    _system, user = seen[-1]
    assert f"<instruction>\n{plan.instruction}\n</instruction>" in user
    assert f"<founder_words>\n{plan.words}\n</founder_words>" in user


def test_draft_change_records_the_instruction_it_was_given(tmp_path):
    """The carrying only works if the first draft wrote it down."""
    _client, root = _workspace(tmp_path)
    route = CorrectionRoute(
        quote="the cart should remember things", spec_slug="cart",
        kind="scope_change", instruction="persist the cart across sessions",
    )

    plan = draft_change(root, route, route.quote, provider="mock")

    assert plan.instruction == route.instruction
    assert plan.rejected == [] and plan.notes == []


# ── words: available, optional, second ───────────────────────────────────


def test_words_are_offered_after_the_taps_never_instead_of_them(tmp_path):
    """Same order the complaint form uses. A textarea above the buttons
    reads as "explain yourself", which is the toll this path removes."""
    client, root = _workspace(tmp_path)
    page = _card(client, _plan(root))

    first_tap = page.index("name=index value='0'")
    assert first_tap < page.index("<textarea name=note>")


def test_a_few_words_redraft_without_rejecting_anything(tmp_path, monkeypatch):
    """When none of the assumptions is the wrong part. The words are the
    founder's own, so they reach the model as words and not as a
    rejection."""
    client, root = _workspace(tmp_path)
    seen = _prompts(monkeypatch)
    plan = _plan(root)

    page = client.post("/correct/redraft", data={
        "plan": json.dumps(plan.model_dump()),
        "note": "it is about guests, not the thirty days",
    }, follow_redirects=True).text

    after = _carried(page)
    assert after.notes == ["it is about guests, not the thirty days"]
    assert after.rejected == []
    assert "<founder_notes>\n- it is about guests" in seen[-1][1]


def test_the_words_box_starts_empty_and_asks_for_nothing(tmp_path):
    """A pre-filled or required box is a form. This is an afterthought,
    and it has to look like one."""
    client, root = _workspace(tmp_path)
    page = _card(client, _plan(root))

    assert "<textarea name=note></textarea>" in page
    assert "required" not in page


# ── the cap ──────────────────────────────────────────────────────────────


def test_the_studio_stops_offering_after_two_rounds(tmp_path):
    """Two, the same bound intake puts on clarifying questions. A third
    round is not a conversation, it is a loop that spends a model call
    each time round."""
    client, root = _workspace(tmp_path)
    plan = _plan(root)

    for _round in range(MAX_REDRAFTS):
        plan = _carried(_tap(client, plan, 0))

    page = _card(client, plan)
    assert plan.redrafts == MAX_REDRAFTS
    assert "name=index" not in page
    assert "<textarea name=note>" not in page
    assert "I have thought this over twice" in page


def test_the_cap_ends_the_redrafting_never_the_change(tmp_path):
    """The founder who has sent it back twice still wanted the change.
    Taking the build button away would leave them with a draft and no
    way to accept it."""
    client, root = _workspace(tmp_path)
    plan = _plan(root, rejected=["a", "b"])

    page = _card(client, plan)

    assert "Make this change" in page
    assert "action='/correct/change'" in page


def test_a_capped_plan_is_not_redrafted_even_if_asked(tmp_path, monkeypatch):
    """The cap lives on the route, not only on the page — a form kept open
    in another tab must not spend a call the page has stopped offering."""
    client, root = _workspace(tmp_path)
    seen = _prompts(monkeypatch)
    plan = _plan(root, rejected=["a", "b"])

    page = _tap(client, plan, 0)

    assert seen == []
    assert _carried(page).rejected == ["a", "b"]


def test_a_tap_with_nothing_to_reject_costs_nothing(tmp_path, monkeypatch):
    """An index the plan does not have — a stale page, a hand-edited form.
    Re-rendering keeps the draft on screen; redirecting home would throw
    away the very thing they were reading."""
    client, root = _workspace(tmp_path)
    seen = _prompts(monkeypatch)
    plan = _plan(root)

    page = _tap(client, plan, 99)

    assert seen == []
    assert html.escape(plan.summary) in page


# ── it is still only a page ──────────────────────────────────────────────


def test_a_redraft_writes_nothing_to_the_workspace(tmp_path):
    """Same promise the first draft makes. A founder who reads a redraft
    and closes the tab has changed nothing, so pressing "not that" can
    never be the thing that commits them."""
    client, root = _workspace(tmp_path)
    before = (root / "specs" / "cart" / "spec.yaml").read_text(encoding="utf-8")

    _tap(client, _plan(root), 0)

    assert (root / "specs" / "cart" / "spec.yaml").read_text(encoding="utf-8") == before
    assert subprocess.run(
        [resolve("git"), "status", "--porcelain"], cwd=root,
        capture_output=True, text=True, check=True,
    ).stdout.strip() == ""


def test_a_hand_edited_plan_cannot_walk_out_of_the_workspace(tmp_path, monkeypatch):
    """`spec_slug` reaches the filesystem here exactly as it does on the
    build route, so it is checked here exactly as it is there."""
    client, root = _workspace(tmp_path)
    seen = _prompts(monkeypatch)

    page = client.post("/correct/redraft", data={
        "plan": json.dumps(_plan(root, spec_slug="../../etc").model_dump()),
        "index": "0",
    }, follow_redirects=True).text

    assert seen == []
    assert "action='/correct/redraft'" not in page


def test_a_feature_that_does_not_exist_is_refused_by_name(tmp_path):
    """Redirecting home would read as "my tap did nothing" — the failure
    every refusal on this path is written to avoid."""
    client, root = _workspace(tmp_path)

    page = client.post("/correct/redraft", data={
        "plan": json.dumps(_plan(root, spec_slug="no-such-thing").model_dump()),
        "index": "0",
    }, follow_redirects=True).text

    assert "no-such-thing" in page


def test_a_model_failure_while_redrafting_is_a_page_not_a_500(tmp_path, monkeypatch):
    """The founder pressed a small button on a card. Whatever happens
    behind it, what comes back has to be readable."""
    client, root = _workspace(tmp_path)
    import ai_venture_studio.upstream.correction as correction_mod

    monkeypatch.setattr(correction_mod, "draft_change", _boom)

    page = _tap(client, _plan(root), 0)

    assert "Internal Server Error" not in page
    assert page.strip()


def _boom(*_a, **_k):
    raise RuntimeError("the model is down")


def test_the_page_speaks_the_studio_language(tmp_path):
    """`lang` is settled when the app is constructed. A tap that came back
    in the other language would be a change the founder cannot read."""
    zh_client, zh_root = _workspace(tmp_path / "zh", lang="zh")
    en_client, en_root = _workspace(tmp_path / "en", lang="en")

    zh_page = _card(zh_client, _plan(zh_root))
    en_page = _card(en_client, _plan(en_root))

    assert "不是这样" in zh_page
    assert "不是这样" not in en_page
    assert "Not that" in en_page


def test_the_working_page_says_what_it_is_thinking_about(tmp_path):
    """A redraft is a model call like any other, and the page it leaves
    the founder on has to name the thing it is doing — "working" over a
    tap that produced no visible change is indistinguishable from a tap
    that did nothing at all."""
    client, root = _workspace(tmp_path)
    import ai_venture_studio.upstream.correction as correction_mod

    holding, seen = threading.Event(), []
    original = correction_mod.draft_change

    def slow(*a, **k):
        holding.wait(10)
        return original(*a, **k)

    correction_mod.draft_change = slow
    plan = _plan(root)
    tapper = threading.Thread(target=lambda: _tap(client, plan, 0))
    tapper.start()
    try:
        for _attempt in range(60):
            page = client.get("/").text
            if "Thinking about this change again" in page:
                seen.append(page)
                break
    finally:
        holding.set()
        tapper.join(15)
        correction_mod.draft_change = original

    assert seen, "the working page never named the redraft"
