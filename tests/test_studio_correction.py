"""Classification preview, and the change log as the undo surface.

Two problems with the old correction loop, both about a founder finding
out too late what their sentence did:

1. Submitting a complaint ran the whole thing. "The button should say Add
   task" could be read as a SCOPE CHANGE — raising an SCR and approving it
   in the founder's name — and they learned that afterwards, from a log
   line. Now the router's decision is shown first, with Confirm / Reword.
2. Undo was one button for the last change only. The change list now
   offers a go-back per entry — and states, on the button's own line, how
   many later changes that also undoes, because the history is a straight
   line of checkpoints and pretending otherwise would promise an
   independence git does not have here.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

from ai_venture_studio.studio import create_studio_app
from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.autopilot import (
    checkpoint_log,
    checkpoints,
    tag_checkpoint,
    undo_to_before,
)
from ai_venture_studio.upstream.correction import (
    CorrectionRoute,
    CorrectionRouteError,
    route_complaint,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


def _commit(root, message):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", message],
        cwd=root, check=True, capture_output=True,
    )


@pytest.fixture
def built(tmp_path):
    """A workspace with one built spec and a report — the state the
    correction loop is reachable from."""
    root = init_workspace(tmp_path / "corr", "corr", "web")
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "BUILD-REPORT.md").write_text("# done", encoding="utf-8")
    spec_dir = root / "specs" / "tasks"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.yaml").write_text(yaml.safe_dump({
        "slug": "tasks", "title": "Task list", "built": True,
        "request": "a task list", "design": "one module",
        "criteria": ["The system shall show a button labelled Submit."],
        "test_skeletons": [],
    }), encoding="utf-8")
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _commit(root, "feat(tasks): the first build")
    client = TestClient(
        create_studio_app(root, spawn=lambda r: 1, provider="mock"),
        raise_server_exceptions=False,
    )
    return client, root


# ── the router is shown, not a second one written ────────────────────────


def test_the_preview_reuses_the_one_router(built):
    _client, root = built
    routes = route_complaint(root, "the button should say Add task",
                             provider="mock")
    assert [type(r) for r in routes] == [CorrectionRoute]
    assert routes[0].spec_slug == "tasks"
    assert routes[0].kind == "fix"


def test_a_complaint_is_classified_before_anything_happens(built):
    client, root = built
    page = client.post("/correct", data={
        "complaint": "the button should say Add task",
    }, follow_redirects=True).text

    assert "SMALL FIX" in page
    assert "repaired directly" in page
    assert "Nothing has been changed yet" in page
    assert "action=/correct/confirm" in page   # confirm
    assert "action=/correct" in page           # …and reword, same route
    # …and it really did nothing: no log line, no SCR.
    assert not (root / "product" / "CORRECTION-LOG.md").exists()
    assert not list((root / ".mas" / "scr").glob("*")) if (
        root / ".mas" / "scr"
    ).is_dir() else True


def test_a_new_requirement_says_it_is_a_new_requirement(built):
    """The dangerous case: a sentence read as a scope change raises an SCR
    and approves it in the founder's name. They see that first now."""
    client, _root = built
    page = client.post("/correct", data={
        "complaint": "this is a new requirement: let people cancel an order",
    }, follow_redirects=True).text

    assert "NEW REQUIREMENT" in page
    assert "its own small build" in page
    assert "Yes, build it as a new requirement" in page


def test_rewording_routes_again_and_still_does_not_act(built):
    client, root = built
    page = client.post("/correct", data={
        "complaint": "this is a new requirement: cancel an order",
    }, follow_redirects=True).text
    assert "NEW REQUIREMENT" in page

    page = client.post("/correct", data={
        "complaint": "the button should say Add task",
    }, follow_redirects=True).text
    assert "SMALL FIX" in page
    assert not (root / "product" / "CORRECTION-LOG.md").exists()


def test_the_preview_carries_the_criterion_from_the_try_page(built):
    client, _root = built
    page = client.post("/correct", data={
        "complaint": "it never moved",
        "criterion": "Mark it done and see it move",
    }, follow_redirects=True).text

    assert "The criterion this came from" in page
    assert "Mark it done and see it move" in page
    # …and it travels on to the confirm form, so the executing call sees it
    assert "name=criterion value='Mark it done and see it move'" in page


def test_confirming_executes_the_classification_that_was_shown(built, monkeypatch):
    """What the founder agreed to is what runs. Re-routing at confirm time
    could land somewhere else, and then the page was a fiction."""
    import ai_venture_studio.upstream.correction as correction

    seen = {}

    def fake_run(root, complaint, *, provider="mock", model="", criterion="",
                 route=None):
        seen.update(complaint=complaint, criterion=criterion, route=route)
        return correction.CorrectionResult(
            status="fixed", spec_slug=route.spec_slug, kind=route.kind,
            detail="repaired in 1 attempt(s)",
        )

    monkeypatch.setattr(correction, "run_correction", fake_run)
    client, root = built
    client.post("/correct/confirm", data={
        "complaint": "the button should say Add task",
        "criterion": "Add a task and see it listed",
        "spec_slug": "tasks",
        "kind": "fix",
        "instruction": "rename the submit button",
    }, follow_redirects=False)

    assert seen["route"].spec_slug == "tasks"
    assert seen["route"].kind == "fix"
    assert seen["route"].instruction == "rename the submit button"
    assert seen["criterion"] == "Add a task and see it listed"
    log = (root / "product" / "CORRECTION-LOG.md").read_text(encoding="utf-8")
    assert "fixed" in log and "Add task" in log


def test_a_confirmed_scope_change_really_raises_the_scr(built):
    """End to end on the mock: the confirmed classification is the one that
    executes, and the founder's words are the recorded authorization."""
    client, root = built
    client.post("/correct/confirm", data={
        "complaint": "this is a new requirement: let people cancel an order",
        "spec_slug": "tasks",
        "kind": "scope_change",
        "instruction": "add order cancellation",
    }, follow_redirects=False)

    scrs = sorted((root / ".mas" / "scr").glob("*.yaml"))
    assert scrs, "no SCR was raised"
    body = scrs[0].read_text(encoding="utf-8")
    assert "let people cancel an order" in body
    log = (root / "product" / "CORRECTION-LOG.md").read_text(encoding="utf-8")
    assert "scr_raised" in log


def test_a_slug_from_the_form_is_never_taken_on_trust(built):
    client, root = built
    for evil in ("../../etc/passwd", "a b; rm -rf /", "x" * 200):
        response = client.post("/correct/confirm", data={
            "complaint": "x", "spec_slug": evil, "kind": "fix",
        }, follow_redirects=False)
        assert response.status_code < 500
    page = client.post("/correct/confirm", data={
        "complaint": "x", "spec_slug": "no-such-spec", "kind": "fix",
    }).text
    assert "no-such-spec" in page
    assert not (root / "product" / "CORRECTION-LOG.md").exists()


def test_an_unknown_kind_is_refused_rather_than_defaulted(built):
    """`kind` picks between repairing code and rewriting a requirement.
    Defaulting an unrecognised value to either one is a guess about which
    of two very different things the founder wanted."""
    client, root = built
    client.post("/correct/confirm", data={
        "complaint": "x", "spec_slug": "tasks", "kind": "whatever",
    }, follow_redirects=False)
    assert not (root / "product" / "CORRECTION-LOG.md").exists()


def test_a_workspace_with_nothing_built_says_so_instead_of_crashing(tmp_path):
    root = init_workspace(tmp_path / "empty", "empty", "web")
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "BUILD-REPORT.md").write_text("# done", encoding="utf-8")
    client = TestClient(create_studio_app(root, provider="mock"))

    page = client.post("/correct", data={"complaint": "it is wrong"},
                       follow_redirects=True).text
    assert "could not be matched to a feature" in page
    assert "nothing built yet" in page


def test_the_classification_is_guarded_against_a_double_submit(built):
    import threading

    import ai_venture_studio.upstream.correction as correction

    client, _root = built
    started, release = threading.Event(), threading.Event()
    calls = []

    original = correction.route_complaint

    def slow(*a, **k):
        calls.append(1)
        started.set()
        release.wait(timeout=10)
        return [CorrectionRoute(spec_slug="tasks", kind="fix", instruction="x")]

    correction.route_complaint = slow
    try:
        first = threading.Thread(
            target=lambda: client.post("/correct", data={"complaint": "a"})
        )
        first.start()
        assert started.wait(timeout=10)
        second = client.post("/correct", data={"complaint": "a"},
                             follow_redirects=True)
        assert "Working on it" in second.text
        release.set()
        first.join(timeout=10)
    finally:
        release.set()
        correction.route_complaint = original
    assert len(calls) == 1


def test_route_complaint_raises_rather_than_returning_a_non_decision(tmp_path):
    root = init_workspace(tmp_path / "none", "none", "web")
    with pytest.raises(CorrectionRouteError, match="nothing built yet"):
        route_complaint(root, "it is wrong", provider="mock")


# ── several problems in one message ──────────────────────────────────────
#
# A founder does not write one complaint per feature. They write what is
# wrong with their product, and that is routinely three things at once —
# which used to be routed to the ONE most responsible feature, repaired,
# and reported as done, with the other two gone from the system entirely.


@pytest.fixture
def three_built(built):
    """Three built features, so three problems have three real homes."""
    _client, root = built
    for slug, title in (
        ("cart", "Cart and checkout"), ("reviews", "Product reviews"),
    ):
        spec_dir = root / "specs" / slug
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "spec.yaml").write_text(yaml.safe_dump({
            "slug": slug, "title": title, "built": True,
            "request": title, "design": "one module",
            "criteria": [f"The system shall provide {title}."],
            "test_skeletons": [],
        }), encoding="utf-8")
    _commit(root, "feat: two more features")
    return built


THREE = "the tasks button is wrong\nthe cart total is wrong\nreviews show no stars"


def test_three_problems_route_to_three_features(three_built):
    _client, root = three_built
    routes = route_complaint(root, THREE, provider="mock")
    assert [r.spec_slug for r in routes] == ["tasks", "cart", "reviews"]
    # …and each carries the founder's own words for ITS issue, so the
    # repair prompt and the SCR are not handed two unrelated problems.
    assert [r.quote for r in routes] == THREE.split("\n")


def test_the_page_shows_every_issue_and_hides_none(three_built):
    client, _root = three_built
    page = client.post("/correct", data={"complaint": THREE},
                       follow_redirects=True).text

    assert "You raised 3 separate things" in page
    for slug in ("tasks", "cart", "reviews"):
        assert slug in page
    # Every issue is individually selectable, and all start ticked: the
    # founder said all three, so the default is to believe them.
    assert page.count("name=include") == 3
    assert page.count("checked") == 3
    assert "Yes, do all 3" in page
    assert "Nothing has been changed yet" in page


def test_confirming_runs_every_issue_not_just_the_first(three_built, monkeypatch):
    import ai_venture_studio.upstream.correction as correction

    ran = []

    def fake_run(root, complaint, *, provider="mock", model="", criterion="",
                 route=None):
        ran.append(route.spec_slug)
        return correction.CorrectionResult(
            status="fixed", spec_slug=route.spec_slug, kind=route.kind,
            detail="repaired in 1 attempt(s)",
        )

    monkeypatch.setattr(correction, "run_correction", fake_run)
    client, root = three_built
    page = client.post("/correct/confirm", data={
        "complaint": THREE,
        "include": ["0", "1", "2"],
        "spec_slug": ["tasks", "cart", "reviews"],
        "kind": ["fix", "fix", "fix"],
        "quote": THREE.split("\n"),
        "instruction": ["a", "b", "c"],
    }, follow_redirects=True).text

    assert ran == ["tasks", "cart", "reviews"]
    # …and the founder is told what happened to each, rather than being
    # bounced home to infer it from a log file.
    assert "What happened to each of the 3" in page
    assert page.count("FIXED") == 3
    log = (root / "product" / "CORRECTION-LOG.md").read_text(encoding="utf-8")
    assert log.count("fixed") == 3


def test_unticking_an_issue_leaves_it_undone(three_built, monkeypatch):
    """The checkbox is the founder's, and it has to actually mean no."""
    import ai_venture_studio.upstream.correction as correction

    ran = []

    def fake_run(root, complaint, *, provider="mock", model="", criterion="",
                 route=None):
        ran.append(route.spec_slug)
        return correction.CorrectionResult(status="fixed",
                                           spec_slug=route.spec_slug,
                                           kind=route.kind, detail="ok")

    monkeypatch.setattr(correction, "run_correction", fake_run)
    client, _root = three_built
    client.post("/correct/confirm", data={
        "complaint": THREE,
        "include": ["0", "2"],                       # issue 2 unticked
        "spec_slug": ["tasks", "cart", "reviews"],
        "kind": ["fix", "fix", "fix"],
        "quote": THREE.split("\n"),
        "instruction": ["a", "b", "c"],
    }, follow_redirects=True)

    assert ran == ["tasks", "reviews"]


def test_one_issue_still_reads_as_one_thing(three_built):
    """The plural must not tax the common case: one problem gets the same
    single-decision page it always had, with no checkbox to consider."""
    client, _root = three_built
    page = client.post("/correct", data={
        "complaint": "the tasks button is wrong",
    }, follow_redirects=True).text

    assert "SMALL FIX" in page
    assert "repaired directly" in page
    assert "Yes, fix it" in page
    assert "name=include" not in page
    assert "separate things" not in page


def test_a_quote_that_is_not_the_founders_words_is_refused(three_built):
    """A router asked to quote will sometimes summarise. A summary shown
    back under "Your words" is a lie the founder cannot catch, so a quote
    that is not a real span of what they wrote is dropped."""
    import ai_venture_studio.upstream.correction as correction

    routes = correction.route_complaint(three_built[1], THREE, provider="mock")
    assert all(r.quote for r in routes)          # the mock quotes honestly

    assert correction._is_verbatim("the cart total is wrong", THREE)
    assert correction._is_verbatim("the  cart\ntotal is wrong", THREE)  # rewrapped
    assert not correction._is_verbatim("the cart is broken somehow", THREE)
    assert not correction._is_verbatim("", THREE)


def test_run_correction_refuses_to_pick_one_of_several(three_built):
    """The singular entry point must not quietly answer the first issue —
    that IS the defect. It refuses and names the plural entry point."""
    from ai_venture_studio.upstream.correction import run_correction

    _client, root = three_built
    result = run_correction(root, THREE, provider="mock")
    assert result.status == "error"
    assert "3 separate issues" in result.detail
    assert "run_corrections" in result.detail


def test_an_unroutable_issue_fails_the_whole_plan_loudly(three_built, monkeypatch):
    """Keeping the routable issues and discarding the rest would be the
    original defect wearing a plural."""
    import ai_venture_studio.upstream.correction as correction

    monkeypatch.setattr(
        correction, "get_provider",
        lambda _p: type("P", (), {"complete": staticmethod(lambda **k: yaml.safe_dump(
            {"issues": [
                {"quote": "a", "spec_slug": "tasks", "kind": "fix", "instruction": "x"},
                {"quote": "b", "spec_slug": "ghost", "kind": "fix", "instruction": "y"},
            ]}
        ))})(),
    )
    _client, root = three_built
    with pytest.raises(CorrectionRouteError, match="ghost"):
        correction.route_complaint(root, THREE, provider="mock")


# ── the change list, and the honesty of its label ────────────────────────


@pytest.fixture
def history(built):
    """Three checkpoints, plus one untagged commit after the last one —
    the shape a correction leaves behind."""
    client, root = built
    tag_checkpoint(root)                       # 001 — the first build
    for number in (2, 3):
        (root / f"f{number}.txt").write_text("x", encoding="utf-8")
        _commit(root, f"feat(f{number}): change {number}")
        tag_checkpoint(root)                   # 002, 003
    (root / "fix.txt").write_text("x", encoding="utf-8")
    _commit(root, "fix(tasks): founder correction — say Add task")
    return client, root


def test_the_change_list_is_newest_first_with_one_entry_per_change(history):
    _client, root = history
    entries = checkpoint_log(root)
    assert [e["tag"] for e in entries] == [
        "ap-checkpoint-003", "ap-checkpoint-002", "ap-checkpoint-001",
    ]
    assert entries[0]["subject"] == "feat(f3): change 3"


def test_each_entry_states_how_many_later_changes_it_also_undoes(history):
    """The honesty requirement. The history is linear: going back to just
    before change 2 also drops change 3 and the correction after it. The
    button must SAY that — labelling it as undoing one change in isolation
    would promise an independence this model does not have."""
    client, root = history
    page = client.get("/").text

    assert "Changes, newest first" in page
    assert "you cannot lift one change out of the middle" in page
    # Entry 002 (one later checkpoint, and 3 commits go with it: change 2,
    # change 3, and the untagged correction).
    assert "This also undoes the 1 later change(s) — 3 commits in total." in page
    # The newest entry has nothing later recorded, and says so with the
    # real commit count rather than implying it is alone.
    assert "Nothing later has been recorded as a change; this undoes 2 commit(s)" in page
    # The oldest has nowhere earlier to go, and offers no button.
    assert "nothing earlier to return to" in page


def test_the_label_never_claims_one_change_is_undone_in_isolation(history):
    client, _root = history
    page = client.get("/").text
    assert "only this change" not in page.lower()
    assert "just this one" not in page.lower()


def test_going_back_to_an_entry_takes_everything_after_it(history):
    client, root = history
    assert (root / "f3.txt").exists()

    client.post("/undo/to", data={"tag": "ap-checkpoint-002"},
                follow_redirects=False)

    assert not (root / "f3.txt").exists(), "the later change survived"
    assert not (root / "fix.txt").exists()
    assert (root / "f2.txt").exists() is False  # 002 IS the change undone
    assert checkpoints(root) == ["ap-checkpoint-001"]


def test_a_rescue_branch_is_made_before_anything_is_reset(history):
    _client, root = history
    result = undo_to_before(root, "ap-checkpoint-002")
    assert result["status"] == "undone"
    branches = subprocess.run(
        ["git", "branch", "--list", "rescue/*"], cwd=root,
        capture_output=True, text=True, timeout=60,
    ).stdout
    assert "rescue/" in branches
    # …and the rescue branch still holds the work that was dropped.
    files = subprocess.run(
        ["git", "show", "--name-only", "--format=", result["rescue_branch"]],
        cwd=root, capture_output=True, text=True, timeout=60,
    ).stdout
    assert "fix.txt" in files


def test_the_oldest_checkpoint_offers_no_go_back(history):
    _client, root = history
    result = undo_to_before(root, "ap-checkpoint-001")
    assert result["status"] == "nothing_to_undo"


def test_a_checkpoint_name_from_a_form_is_never_taken_on_trust(history):
    client, root = history
    before = checkpoints(root)
    for evil in ("../../etc/passwd", "a b; rm -rf /", "x" * 200):
        response = client.post("/undo/to", data={"tag": evil},
                               follow_redirects=False)
        assert response.status_code < 500
    page = client.post("/undo/to", data={"tag": "ap-checkpoint-404"}).text
    assert "ap-checkpoint-404" in page
    assert checkpoints(root) == before


def test_the_whole_change_list_button_still_exists_for_the_last_change(history):
    """The one-click undo the footer already had is not removed by the
    per-entry list — it is the same operation on the newest entry."""
    client, root = history
    assert "action=/undo" in client.get("/").text
    client.post("/undo", follow_redirects=False)
    assert checkpoints(root) == ["ap-checkpoint-001", "ap-checkpoint-002"]
