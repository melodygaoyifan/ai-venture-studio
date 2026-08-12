"""Pointing at a feature instead of remembering it exists.

The change path (v0.74) made "actually, make it X" real. This is about
what a founder has to do BEFORE that sentence — and it was all recall:

- The home page's "Features" heading listed `product/features/*`, which is
  post-build additions only, as bare directory slugs. Everything the
  product was originally BUILT from had no representation anywhere on the
  page. There was nothing to point at, so a founder had to describe from
  memory a feature they had never been shown a name for.
- The composer had three tabs over two forms. "Something wrong?" and "Is
  it broken?" posted to the same `/correct` form, and the router reads the
  words, never the tab — a decision the founder made and the backend threw
  away.
- Marking an acceptance row "Wrong" opened an empty box and waited for
  them to write out, in prose, what the row already said.

The through-line is the same in all three: this product's user is assumed
lazy and non-technical on purpose, and typing is the most expensive thing
it can ask of them. What replaces it must never GUESS, though, which is
why a pre-scoped complaint only skips the router's choice of feature when
that choice is a fact — see `criterion_owners`.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

from ai_venture_studio.studio import _plain_criterion, create_studio_app
from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.correction import (
    CorrectionRouteError,
    built_specs,
    criterion_owners,
    route_complaint,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

CART = "The system shall keep the cart after the browser is closed."
ORDERS = "When an order is placed, the system shall email a receipt."


def _spec(root, slug, title, criteria, *, built=True):
    spec_dir = root / "specs" / slug
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.yaml").write_text(yaml.safe_dump({
        "slug": slug, "title": title, "built": built,
        # load_spec wants the whole model; a fixture missing a required
        # field renders a ValidationError page instead of the behaviour
        # under test, and passes for the wrong reason.
        "request": f"a {title}", "design": "one module", "profile": "web",
        "criteria": criteria, "test_skeletons": [],
    }), encoding="utf-8")


def _workspace(tmp_path, lang="en"):
    """Two built features, so "which one" is a real question the page has
    to answer rather than one with a single possible answer."""
    root = init_workspace(tmp_path / "shop", "shop", "web")
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "BUILD-REPORT.md").write_text("# done", encoding="utf-8")
    _spec(root, "cart", "Shopping cart", [CART])
    _spec(root, "orders", "Orders", [ORDERS])
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


# ── F2: a card per feature, built from the router's own list ─────────────


def test_every_built_feature_is_on_the_page_with_what_it_does(tmp_path):
    """The list used to be `product/features/*` — what was added AFTER the
    build. A founder who built five features and changed none of them saw
    an empty Features section describing a product with five features."""
    client, _root = _workspace(tmp_path)

    page = client.get("/").text

    assert "Shopping cart" in page
    assert "Orders" in page
    # …and what each one does, in a sentence rather than in EARS.
    assert "Keep the cart after the browser is closed." in page
    assert "The system shall keep the cart" not in page


def test_the_cards_and_the_router_read_the_same_list(tmp_path):
    """Rendered from `built_specs`, deliberately — the router's own source
    of truth. Two separate readers of specs/ would eventually disagree, and
    the failure would be a card offering a change the router then refuses
    to route."""
    client, root = _workspace(tmp_path)
    _spec(root, "wishlist", "Wishlist", ["The system shall save a wishlist."],
          built=False)

    page = client.get("/").text

    assert {s["slug"] for s in built_specs(root)} == {"cart", "orders"}
    # An unbuilt spec is not a feature of the product yet, so it is on
    # neither list.
    assert "Wishlist" not in page


def test_a_card_carries_the_slug_that_pre_scopes_its_change(tmp_path):
    client, _root = _workspace(tmp_path)

    page = client.get("/").text

    assert "name=spec_slug value='cart'" in page
    assert "name=spec_slug value='orders'" in page
    assert "Change this" in page


def test_pointing_at_a_feature_beats_naming_another_one(tmp_path):
    """The point of the card. The complaint text names `orders`, and the
    unscoped router follows the words — correctly, it has nothing else. A
    complaint sent from the cart's own card must reach `cart` anyway,
    because the founder pressed the button beside it."""
    _client, root = _workspace(tmp_path)

    loose = route_complaint(root, "the orders page is wrong", provider="mock")
    scoped = route_complaint(
        root, "the orders page is wrong", provider="mock", only_slug="cart"
    )

    assert loose[0].spec_slug == "orders"
    assert scoped[0].spec_slug == "cart"


def test_a_pre_scope_still_leaves_fix_or_change_to_the_router(tmp_path):
    """Pressing a button beside a feature says WHICH feature. It does not
    say whether the product is broken or the requirement moved, and
    deciding that here would be the Studio answering a question the founder
    never was asked."""
    _client, root = _workspace(tmp_path)

    fix = route_complaint(root, "the total is wrong", provider="mock",
                          only_slug="cart")
    change = route_complaint(root, "new requirement: remember it for a week",
                             provider="mock", only_slug="cart")

    assert (fix[0].kind, change[0].kind) == ("fix", "scope_change")


def test_a_pre_scope_naming_something_unbuilt_is_refused(tmp_path):
    """Loudly. Falling back to the whole list would silently turn the one
    guarantee the card makes — this complaint is about THIS feature — back
    into a guess."""
    _client, root = _workspace(tmp_path)
    _spec(root, "wishlist", "Wishlist", ["The system shall save a wishlist."],
          built=False)

    with pytest.raises(CorrectionRouteError, match="wishlist"):
        route_complaint(root, "it is wrong", provider="mock",
                        only_slug="wishlist")


def test_a_spec_slug_from_a_form_cannot_walk_out_of_the_workspace(tmp_path):
    client, _root = _workspace(tmp_path)

    walk = client.post("/correct", data={
        "complaint": "it is wrong", "spec_slug": "../../etc",
    }, follow_redirects=False)
    absent = client.post("/correct", data={
        "complaint": "it is wrong", "spec_slug": "no-such-spec",
    }, follow_redirects=True)

    assert walk.status_code == 303
    assert "no-such-spec" in absent.text  # named out loud, not redirected


def test_the_recent_changes_list_survives_as_its_own_thing(tmp_path):
    """`product/features/*` is a change log, not a feature list. It was
    genuinely useful information filed under the wrong heading — so it gets
    its own, rather than being deleted along with the mistake."""
    client, root = _workspace(tmp_path)
    added = root / "product" / "features" / "cancel-an-order"
    added.mkdir(parents=True)
    (added / "REPORT.md").write_text("# done", encoding="utf-8")

    page = client.get("/").text

    assert "Recent changes" in page
    assert "cancel-an-order" in page


# ── the criterion, as a sentence rather than as EARS ─────────────────────


@pytest.mark.parametrize(("stored", "shown"), [
    ("The system shall show a Submit button.", "Show a Submit button."),
    ("the cart shall keep its items.", "Keep its items."),
    ("When an order is placed, the system shall email a receipt.",
     "When an order is placed: Email a receipt."),
    ("If the payload is malformed, then the API shall return 400.",
     "If the payload is malformed: Return 400."),
    # Not EARS at all — shown exactly as written. Guessing at the shape of
    # a sentence nobody promised would be a worse page than a plain one.
    ("Old rows are archived nightly.", "Old rows are archived nightly."),
])
def test_a_criterion_reads_as_a_sentence_and_is_stored_as_ears(stored, shown):
    assert _plain_criterion(stored) == shown


def test_softening_a_criterion_never_edits_the_spec(tmp_path):
    """Display only. `criteria` is inside the contract hash and is what the
    build is checked against; a helper that rewrote it would be an
    unratified spec edit dressed as a stylesheet."""
    client, root = _workspace(tmp_path)

    client.get("/")

    stored = yaml.safe_load(
        (root / "specs" / "cart" / "spec.yaml").read_text(encoding="utf-8")
    )
    assert stored["criteria"] == [CART]


# ── P1: two tabs, because there are two forms ────────────────────────────


def test_the_composer_offers_one_choice_per_thing_it_actually_does(tmp_path):
    client, _root = _workspace(tmp_path)

    page = client.get("/").text

    assert len(re.findall(r"class='?tab\b", page)) == 2
    assert "Change something" in page
    assert "Add something new" in page
    # The tab that was a fork the backend could not see.
    assert "Is it broken?" not in page


def test_both_forms_are_still_in_the_html_for_someone_without_javascript(tmp_path):
    """The tabs only toggle visibility. A composer whose second form exists
    only after a click is a composer that does nothing in a text browser —
    and invisible to the wire-up gate, which is worse."""
    client, _root = _workspace(tmp_path)

    page = client.get("/").text

    assert "action=/correct id=form-correct" in page
    assert "action=/feature id=form-feature" in page


# ── P3: a row marked wrong IS the complaint ──────────────────────────────


def _acceptance(root, *lines):
    path = root / "product" / "ACCEPTANCE.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        "# Acceptance\n\n" + "".join(f"- [ ] {line}\n" for line in lines),
        encoding="utf-8",
    )


def test_marking_a_row_wrong_asks_for_no_typing_at_all(tmp_path):
    client, root = _workspace(tmp_path)
    _acceptance(root, "Close the browser, come back, the cart is still there")

    page = client.get("/try").text

    assert "no typing" in page
    # The complaint the tap sends: the row, said as a report of a failure
    # rather than as the requirement it is written as. That string is what
    # gets recorded as the founder's own words on the SCR.
    assert (
        "name=complaint value='This one is not right: "
        "Close the browser, come back, the cart is still there'"
    ) in page


def test_the_box_for_saying_more_is_still_there_second(tmp_path):
    """One tap is the default, not the only option. A founder who knows
    exactly what is wrong must not be forced through a round trip to say
    it."""
    client, root = _workspace(tmp_path)
    _acceptance(root, "Close the browser and come back")

    page = client.get("/try").text

    assert "Add a few words" in page
    assert "name=complaint style='min-height:70px'" in page


def test_a_row_that_is_a_criterion_word_for_word_is_pre_scoped(tmp_path):
    client, root = _workspace(tmp_path)
    _acceptance(root, CART)

    page = client.get("/try").text

    assert "name=spec_slug value='cart'" in page


def test_a_row_that_matches_no_criterion_is_left_to_the_router(tmp_path):
    """Which is the common case: the acceptance walkthrough is written by a
    model in plain language, so most rows match nothing. A closest-match
    guess would skip the router exactly when it was needed."""
    client, root = _workspace(tmp_path)
    _acceptance(root, "Close the browser, come back, the cart is still there")

    page = client.get("/try").text

    assert "name=spec_slug" not in page


def test_a_criterion_two_features_both_claim_owns_nothing(tmp_path):
    _client, root = _workspace(tmp_path)
    shared = "The system shall show a spinner while loading."
    _spec(root, "cart", "Shopping cart", [CART, shared])
    _spec(root, "orders", "Orders", [ORDERS, shared])

    owners = criterion_owners(root)

    assert owners[" ".join(CART.split())] == "cart"
    assert shared not in owners


def test_one_criterion_repeated_inside_one_spec_still_has_an_owner(tmp_path):
    """A duplicate is untidy, not ambiguous — there is still exactly one
    feature answerable for it."""
    _client, root = _workspace(tmp_path)
    _spec(root, "cart", "Shopping cart", [CART, CART])

    assert criterion_owners(root)[" ".join(CART.split())] == "cart"


def test_a_one_tap_complaint_reaches_the_classification_page(tmp_path):
    """End to end: the tap posts a real form and comes back with the
    decision, the same as a typed complaint. It must not act — a tap is a
    report, and the confirm step is where anything happens."""
    client, root = _workspace(tmp_path)
    _acceptance(root, CART)

    page = client.post("/correct", data={
        "criterion": CART, "spec_slug": "cart",
        "complaint": f"This one is not right: {CART}",
    }, follow_redirects=True).text

    assert "action=/correct/confirm" in page
    assert "value='cart'" in page
    assert not (root / "product" / "CORRECTION-LOG.md").exists()


@pytest.mark.parametrize(("lang", "expected"), [
    ("en", "This one is not right:"),
    ("zh", "这一条不对："),
])
def test_the_tap_speaks_the_founders_language(tmp_path, lang, expected):
    """`lang` is a CONSTRUCTION parameter, not a cookie — a zh check that
    only sets a cookie renders English and passes without proving
    anything. This string is load-bearing twice over: it is on the page AND
    it is what the SCR records as the founder's words."""
    client, root = _workspace(tmp_path, lang=lang)
    _acceptance(root, CART)

    page = client.get("/try").text

    assert expected in page
