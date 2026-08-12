"""What the founder is told happened, and whether they can take it back.

Three things went wrong at the same place — the card a founder lands on
after asking for a change:

- **It spoke to the wrong reader.** `repaired in 2 attempt(s); files:
  src/cart.py` is a line written for whoever reads the log. It was the only
  thing on the card, so the person this product is built for — assumed
  non-technical, on purpose — was handed the implementer's notes and left
  to work out for themselves whether their product had changed. A sentence
  cannot be translated after the fact, so the way out is a closed
  vocabulary of REASONS the UI can say in either language.
- **A repair could not be undone.** Builds and feature additions were
  tagged as checkpoints; repairs were bare commits. So the one kind of
  change a founder makes when something is *already* wrong — the change
  most likely to be wrong again — was the only one the undo list could not
  see.
- **The page you wait on looked identical to a hung one.** It reloads every
  four seconds and said exactly the same thing each time, for the several
  minutes a model call takes.
"""

from __future__ import annotations

import shutil
import subprocess
import threading

import pytest
import yaml
from fastapi.testclient import TestClient

from ai_venture_studio import testing as testing_mod
from ai_venture_studio.studio import create_studio_app
from ai_venture_studio.studio_i18n import STRINGS
from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.autopilot import checkpoint_log, checkpoints, run_autopilot
from ai_venture_studio.upstream.correction import (
    REASONS,
    CorrectionResult,
    CorrectionRoute,
    run_correction,
    run_corrections,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

GOOD_FDR = "团长发起接龙，住户下单，团长看汇总。必须有：发起、下单、汇总。"


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import ai_venture_studio.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def _light(tmp_path, lang="en", *, specs=(("cart", "Shopping cart"),)):
    """A workspace with built specs and a commit, and no build behind it.

    Enough for everything that only needs the router and the pages; the
    tests that need a real repair pay for a real build below.
    """
    root = init_workspace(tmp_path / "shop", "shop", "web")
    for slug, title in specs:
        spec_dir = root / "specs" / slug
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "spec.yaml").write_text(yaml.safe_dump({
            "slug": slug, "title": title, "built": True,
            "request": title, "design": "one module", "profile": "web",
            "criteria": [f"The system shall provide {title}."],
            "test_skeletons": [],
        }), encoding="utf-8")
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "built"],
        cwd=root, check=True, capture_output=True,
    )
    client = TestClient(
        create_studio_app(root, spawn=lambda r: 1, provider="mock", lang=lang),
        raise_server_exceptions=False,
    )
    return client, root


def _result_page(client, monkeypatch, result: CorrectionResult) -> str:
    """The card for one fabricated outcome.

    Monkeypatching the single-issue entry point is how the existing suite
    drives this page: the alternative is making a real repair fail in five
    different ways, which tests the repairer rather than the card.
    """
    from ai_venture_studio.upstream import correction as correction_mod

    monkeypatch.setattr(
        correction_mod, "run_correction", lambda *a, **k: result
    )
    return client.post("/correct/confirm", data={
        "complaint": "the total is wrong",
        # The confirm route checks the slug against the workspace before it
        # runs anything, so the fabricated result has to claim a feature the
        # workspace really has.
        "spec_slug": [result.spec_slug or "cart"],
        "kind": ["fix"], "quote": ["the total is wrong"],
        "instruction": ["fix the total"],
    }, follow_redirects=True).text


# ── P2: every way out has a sentence, in both languages ──────────────────


def test_every_reason_can_be_said_in_both_languages():
    """The gate that keeps the vocabulary closed. A new way out of
    `run_correction` that nobody wrote a sentence for would otherwise ship
    as a card that says the status and then nothing at all."""
    for reason in REASONS:
        strings = STRINGS[f"res_why_{reason}"]
        assert strings["zh"].strip() and strings["en"].strip()


def test_a_reason_says_whether_the_product_changed(tmp_path):
    """Which is the question the founder is reading the card to answer.
    Every sentence for an outcome that changed nothing has to say so —
    'it failed' and 'it failed and your product is untouched' are very
    different pieces of news to someone who cannot go and look."""
    untouched = ("tests_failed", "unrouted", "unknown_spec", "crashed")
    for reason in untouched:
        english = STRINGS[f"res_why_{reason}"]["en"].lower()
        assert "nothing" in english or "not touched" in english
        assert any(
            said in STRINGS[f"res_why_{reason}"]["zh"]
            for said in ("没有", "没动", "什么都没")
        )


def test_a_complaint_with_nowhere_to_go_is_unrouted(tmp_path):
    root = init_workspace(tmp_path / "empty", "empty", "web")

    result = run_correction(root, "the cart is wrong", provider="mock")

    assert (result.status, result.reason) == ("error", "unrouted")


def test_a_route_naming_a_feature_that_is_not_built_is_unknown_spec(tmp_path):
    _client, root = _light(tmp_path)

    result = run_correction(
        root, "the cart is wrong", provider="mock",
        route=CorrectionRoute(spec_slug="ghost", kind="fix", instruction="x"),
    )

    assert (result.status, result.reason) == ("error", "unknown_spec")


def test_several_issues_at_the_single_issue_door_say_so(tmp_path):
    """`run_correction` refuses rather than picking one — a decision v0.73
    made and this only labels, so the Studio can explain the refusal
    instead of showing the founder the word `run_corrections`."""
    _client, root = _light(
        tmp_path, specs=(("cart", "Cart"), ("orders", "Orders")),
    )

    result = run_correction(
        root, "the cart total is wrong\nthe orders page is wrong",
        provider="mock",
    )

    assert (result.status, result.reason) == ("error", "many_issues")


def test_an_issue_that_raises_is_reported_as_crashed_not_lost(tmp_path):
    _client, root = _light(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("provider exploded")

    from ai_venture_studio.upstream import correction as correction_mod

    original = correction_mod.run_correction
    correction_mod.run_correction = boom
    try:
        results = run_corrections(
            root, "the total is wrong", provider="mock",
            routes=[CorrectionRoute(spec_slug="cart", kind="fix",
                                    instruction="fix it")],
        )
    finally:
        correction_mod.run_correction = original

    assert [r.reason for r in results] == ["crashed"]


def test_a_scope_change_is_planned_not_failed(tmp_path):
    _client, root = _light(tmp_path)

    result = run_correction(root, "新增：住户可以取消订单", provider="mock")

    assert (result.status, result.reason) == ("change_planned", "planned")


# ── P2, on the page ──────────────────────────────────────────────────────


def test_the_card_speaks_to_the_founder_and_keeps_the_log_line(tmp_path, monkeypatch):
    client, _root = _light(tmp_path)

    page = _result_page(client, monkeypatch, CorrectionResult(
        status="fixed", spec_slug="cart", kind="fix", reason="repaired",
        detail="repaired in 1 attempt(s); files: src/app.py",
    ))

    assert "Fixed and saved." in page
    # The log line is not deleted — when a founder does ask someone
    # technical for help, it is the thing they need — but it is one fold
    # down instead of being the whole message.
    assert "Technical detail" in page
    assert "<details" in page
    assert "repaired in 1 attempt(s); files: src/app.py" in page


def test_a_failed_repair_says_the_product_was_not_touched(tmp_path, monkeypatch):
    """The single most important fact on the card, and the one `repair
    still broke the suite after 3 attempt(s) (...); workspace reverted`
    buries in its last two words."""
    client, _root = _light(tmp_path)

    page = _result_page(client, monkeypatch, CorrectionResult(
        status="error", spec_slug="cart", kind="fix", reason="tests_failed",
        detail="repair still broke the suite after 3 attempt(s); reverted",
    ))

    assert "your product is exactly as it was" in page


def test_an_outcome_with_no_reason_still_says_the_only_thing_it_has(
    tmp_path, monkeypatch
):
    """A card that folds its one sentence away and shows nothing is worse
    than the log line it replaced."""
    client, _root = _light(tmp_path)

    page = _result_page(client, monkeypatch, CorrectionResult(
        status="error", spec_slug="cart", detail="something unforeseen",
    ))

    assert "something unforeseen" in page
    assert "Technical detail" not in page


@pytest.mark.parametrize(("lang", "expected"), [
    ("en", "Fixed and saved."),
    ("zh", "已经改好并保存"),
])
def test_the_card_speaks_the_founders_language(tmp_path, monkeypatch, lang, expected):
    """`lang` is a CONSTRUCTION parameter — a zh check that only sets a
    cookie renders English and passes while proving nothing."""
    client, _root = _light(tmp_path, lang=lang)

    page = _result_page(client, monkeypatch, CorrectionResult(
        status="fixed", spec_slug="cart", reason="repaired", detail="d",
    ))

    assert expected in page


# ── P4: a repair is a change, so it can be taken back ────────────────────


@pytest.fixture
def repaired(tmp_path):
    """A real build, then a real repair — the only way to check that the
    repair leaves a checkpoint behind, since the tag is written by the
    commit path itself."""
    root = init_workspace(tmp_path / "p", "p", "web")
    (root / "FDR.md").write_text(GOOD_FDR, encoding="utf-8")
    assert run_autopilot(root, root / "FDR.md", provider="mock",
                         yes=True).status == "completed"
    before = checkpoints(root)
    result = run_correction(root, "按钮文字不对，应该是「参加接龙」", provider="mock")
    assert result.status == "fixed", result.detail
    return root, before, result


def test_a_repair_is_a_checkpoint_like_every_other_change(repaired):
    root, before, result = repaired

    after = checkpoints(root)

    assert len(after) == len(before) + 1
    assert result.checkpoint == after[-1]
    # …and it is therefore in the change list on the home page, which is
    # where a founder looks a week later rather than a minute later.
    assert checkpoint_log(root)[0]["tag"] == result.checkpoint


def test_the_card_offers_to_undo_the_change_it_just_reported(
    repaired, monkeypatch
):
    root, _before, result = repaired
    client = TestClient(
        create_studio_app(root, spawn=lambda r: 1, provider="mock", lang="en"),
        raise_server_exceptions=False,
    )

    page = _result_page(client, monkeypatch, result)

    assert "action=/undo/to" in page
    assert f"name=tag value='{result.checkpoint}'" in page
    # The cost of going back is on the button's own line, the same sentence
    # the change list uses — a second, quieter copy of this button is how
    # the honest part stops being said.
    assert "1 commit" in page or "commits" in page


def test_pressing_it_puts_the_product_back(repaired, monkeypatch):
    root, before, result = repaired
    client = TestClient(
        create_studio_app(root, spawn=lambda r: 1, provider="mock", lang="en"),
        raise_server_exceptions=False,
    )
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.strip()

    client.post("/undo/to", data={"tag": result.checkpoint},
                follow_redirects=False)

    now = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                         capture_output=True, text=True).stdout.strip()
    assert now != head
    assert checkpoints(root) == before  # the repair's tag went with it


def test_no_undo_is_offered_when_there_is_nothing_to_go_back_to(
    tmp_path, monkeypatch
):
    """The first checkpoint has no earlier one, and `undo_to_before` would
    refuse. A button that refuses when pressed is worse than no button."""
    client, root = _light(tmp_path)
    subprocess.run(["git", "tag", "ap-checkpoint-001"], cwd=root, check=True)

    page = _result_page(client, monkeypatch, CorrectionResult(
        status="fixed", spec_slug="cart", reason="repaired", detail="d",
        checkpoint="ap-checkpoint-001",
    ))

    assert "action=/undo/to" not in page
    assert "Fixed and saved." in page


# ── P5: proof that the wait is a wait, not a hang ────────────────────────


def test_the_working_page_shows_how_long_it_has_been_going(tmp_path):
    """The page reloads every four seconds. Without a clock, the only
    evidence that anything is happening is that nothing has changed —
    which is also exactly what a dead worker looks like."""
    client, root = _light(tmp_path)
    from ai_venture_studio.upstream import correction as correction_mod

    holding, seen = threading.Event(), []
    original = correction_mod.route_complaint

    def slow(*a, **k):
        holding.wait(10)
        return original(*a, **k)

    correction_mod.route_complaint = slow
    poster = threading.Thread(target=lambda: client.post(
        "/correct", data={"complaint": "the total is wrong"},
    ))
    poster.start()
    try:
        for _ in range(60):
            page = client.get("/").text
            if "Already working on this" in page:
                seen.append(page)
                break
    finally:
        holding.set()
        poster.join(15)
        correction_mod.route_complaint = original

    assert seen, "the working page never appeared"
    assert "Running for 00:0" in seen[0]


def test_an_idle_home_page_shows_no_clock(tmp_path):
    """An omitted clock is honest; a clock counting something that is not
    running is the invented progress bar this replaces."""
    client, _root = _light(tmp_path)

    assert "Running for" not in client.get("/").text
