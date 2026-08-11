"""Changing a requirement: the amendment, and the evidence after it.

Two halves of one promise. A founder says "actually, sort them oldest
first"; what has to come back is a product that does that, an acceptance
list that says so, and pictures of the thing they just changed.

Both halves used to be missing in the same way — quietly:

- The SPEC was never amended. A change built new code against the old
  contract, so `avs status` and the acceptance page still described the
  product the founder had just changed away from. Amending it is not a
  free edit either: `criteria` is inside `contract_hash`, so an amendment
  that does not RE-STAMP `approved_hash` leaves an unratified fork that
  §13.35.5 refuses to build ever again.
- The EVIDENCE was never refreshed. Screenshots and VERIFICATION.md came
  only from the first build, so after a change the Studio showed the old
  cart under a green success message.

The ordering rules here are the whole design, so each has a test:
nothing is authorized until the founder presses go, the amendment is
parked until the build actually succeeds, and it is spent exactly once.
"""

from __future__ import annotations

import shutil

import pytest
import yaml

from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.screenshots import (
    MAX_SHOTS,
    capture_web,
    discover_paths,
)
from ai_venture_studio.upstream.spec import (
    amend_spec_criteria,
    apply_pending_amendment,
    approve_scr,
    contract_hash,
    load_spec,
    raise_scr,
    write_pending_amendment,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

FDR = "# sort them oldest first\n\nput the oldest at the top\n"


def _workspace(tmp_path, *, built: bool = True):
    """A workspace with one spec whose approval hash is deliberately stale —
    the state a spec is in between builds."""
    root = init_workspace(tmp_path / "prod", "prod", "web")
    spec_dir = root / "specs" / "tasks"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.yaml").write_text(yaml.safe_dump({
        "slug": "tasks", "title": "Task list", "request": "a task list",
        "profile": "web", "design": "one module", "built": built,
        "criteria": ["The system shall list items newest first."],
        "test_skeletons": [], "approved_hash": "stale-hash-from-gate-u3",
    }), encoding="utf-8")
    return root


def _grant(root):
    approve_scr(root, int(raise_scr(root, "tasks", "founder said so").stem.split("-")[1]))


# --- the amendment ------------------------------------------------------------

def test_amending_criteria_restamps_the_approval_hash(tmp_path):
    """Otherwise the amendment is a spec-killer.

    `criteria` is inside `contract_hash`, and `_approval_drift` refuses to
    build any spec whose contract no longer matches `approved_hash`. An
    amendment that changed the criteria without re-stamping would leave
    the founder's own change looking exactly like someone editing a frozen
    spec behind the SCR channel's back — and every later build would
    refuse it.
    """
    root = _workspace(tmp_path)
    _grant(root)

    spec = amend_spec_criteria(root, "tasks", ["The system shall list oldest first."])

    assert spec.criteria == ["The system shall list oldest first."]
    assert spec.approved_hash == contract_hash(spec)
    assert spec.revisions == 1
    # …and it survived the round trip to disk, which is what the build reads.
    assert load_spec(root, "tasks").approved_hash == contract_hash(spec)


def test_amending_a_built_spec_without_a_grant_is_refused(tmp_path):
    """ADR-U02 has exactly one drift channel. A change path that could
    amend without a grant would be a second one."""
    root = _workspace(tmp_path)

    with pytest.raises(PermissionError, match="approved SCR"):
        amend_spec_criteria(root, "tasks", ["The system shall do something else."])

    assert load_spec(root, "tasks").criteria == [
        "The system shall list items newest first."
    ]


def test_an_empty_criteria_list_is_never_ratified(tmp_path):
    """A spec that promises nothing passes every gate that follows."""
    root = _workspace(tmp_path)
    _grant(root)

    with pytest.raises(ValueError):
        amend_spec_criteria(root, "tasks", ["", "   "])


# --- parking it until the build succeeds --------------------------------------

def test_a_parked_amendment_is_bound_to_its_own_fdr(tmp_path):
    """The Studio confirms in one process and builds in a detached one
    minutes later, so the amendment has to wait on disk. Bound by digest so
    a record left by an abandoned change cannot attach itself to whatever
    the founder builds next."""
    root = _workspace(tmp_path)
    _grant(root)
    write_pending_amendment(root, "tasks", ["The system shall list oldest first."], FDR)

    assert apply_pending_amendment(root, "# a completely different feature\n") is None
    assert (root / ".mas" / "pending-amendment.yaml").exists(), "record was spent"
    assert load_spec(root, "tasks").criteria == [
        "The system shall list items newest first."
    ]


def test_a_parked_amendment_is_spent_exactly_once(tmp_path):
    root = _workspace(tmp_path)
    _grant(root)
    write_pending_amendment(root, "tasks", ["The system shall list oldest first."], FDR)

    spec = apply_pending_amendment(root, FDR)

    assert spec is not None
    assert spec.criteria == ["The system shall list oldest first."]
    assert not (root / ".mas" / "pending-amendment.yaml").exists()
    scr = yaml.safe_load(
        next((root / ".mas" / "scr").glob("SCR-*.yaml")).read_text(encoding="utf-8")
    )
    assert scr["status"] == "consumed"
    # A second call has nothing left to spend — and no grant left either.
    assert apply_pending_amendment(root, FDR) is None


def test_a_failed_build_leaves_the_spec_saying_what_is_actually_built(tmp_path):
    """`run_feature` spends the amendment only when every task built. A spec
    promising behaviour that failed to build would be worse than the stale
    spec this whole path exists to fix: the founder would be told their
    change had landed by the one file the CLI treats as the truth."""
    root = _workspace(tmp_path)
    _grant(root)
    write_pending_amendment(root, "tasks", ["The system shall list oldest first."], FDR)

    # The build failed, so nothing calls apply_pending_amendment…
    assert load_spec(root, "tasks").criteria == [
        "The system shall list items newest first."
    ]
    # …and the parked record is still there for the retry.
    assert (root / ".mas" / "pending-amendment.yaml").exists()


def test_a_change_to_a_spec_that_vanished_does_not_crash_the_build(tmp_path):
    """Evidence and amendments are tails on a build that already succeeded.
    Neither may turn a built product into a failed one."""
    root = _workspace(tmp_path)
    _grant(root)
    write_pending_amendment(root, "tasks", ["The system shall list oldest first."], FDR)
    shutil.rmtree(root / "specs" / "tasks")

    assert apply_pending_amendment(root, FDR) is None


# --- the evidence -------------------------------------------------------------

def _product(root, body: str) -> None:
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / "main.py").write_text(body, encoding="utf-8")


def test_discover_paths_photographs_the_whole_product(tmp_path):
    """Capture was called with the default `["/"]`, so a founder who built
    five pages got one picture of the home page — and after a change that
    touched checkout, the one picture was of the page that had not moved."""
    _product(tmp_path, (
        '@app.get("/")\ndef home(): ...\n'
        '@app.get("/cart")\ndef cart(): ...\n'
        '@app.post("/checkout")\ndef checkout(): ...\n'
    ))

    assert discover_paths(tmp_path) == ["/", "/cart", "/checkout"]


def test_discover_paths_skips_routes_it_cannot_visit(tmp_path):
    """There is no id to substitute into `/items/{item_id}`, and a 404
    screenshot is worse than a missing one."""
    _product(tmp_path, (
        '@app.get("/items/{item_id}")\ndef item(): ...\n'
        '@app.get("/orders/<oid>")\ndef order(): ...\n'
    ))

    assert discover_paths(tmp_path) == ["/"]


def test_discover_paths_stays_a_tail_on_the_build(tmp_path):
    routes = "".join(
        f'@app.get("/p{i}")\ndef p{i}(): ...\n' for i in range(MAX_SHOTS + 10)
    )
    _product(tmp_path, routes)

    paths = discover_paths(tmp_path)

    assert len(paths) == MAX_SHOTS
    assert paths[0] == "/", "the front door is never the one dropped"


def test_discover_paths_on_a_product_with_no_routes(tmp_path):
    _product(tmp_path, "# nothing yet\n")

    assert discover_paths(tmp_path) == ["/"]


def test_a_product_that_cannot_be_photographed_says_why(tmp_path):
    """The note is the whole point: an empty gallery with no explanation
    read as "the Studio is broken", and a gallery of the PREVIOUS build's
    pictures read as "your change did nothing"."""
    result = capture_web(tmp_path)

    assert result.captured == []
    assert result.note, "silent skip"


def test_stale_pictures_are_never_deleted_over_a_failed_capture(tmp_path, monkeypatch):
    """Pruning exists so a page the founder deleted loses its picture. An
    empty run means the capture failed, and deleting the last evidence of
    the product over a failure is its own bug."""
    import ai_venture_studio.upstream.screenshots as shots_mod

    _product(tmp_path, '@app.get("/")\ndef home(): ...\n')
    out = tmp_path / "product" / "screenshots"
    out.mkdir(parents=True)
    (out / "cart.png").write_bytes(b"\x89PNG\r\n")
    # Playwright is "installed", but the product never listens.
    monkeypatch.setattr(shots_mod, "_playwright_available", lambda: True)

    result = capture_web(tmp_path, port=1)

    assert result.captured == []
    assert (out / "cart.png").exists(), "last evidence deleted over a failure"
