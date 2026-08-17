"""ADR-045: every acceptance criterion has an id that outlives its position.

These pin the RULES the ledger has to keep, not the shape of any one
workspace: ids are never reused, never renumbered, derived rather than
maintained, and a slice shown to a planner says what it left out.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import yaml

from ai_venture_studio.upstream import requirements as req


def _write_spec(root: Path, slug: str, criteria: list[str], *, built: bool = False,
                status: str = "proposed", skeletons: list[dict] | None = None) -> None:
    directory = root / "specs" / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spec.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": slug,
                "title": slug,
                "status": status,
                "request": slug,
                "profile": "web",
                "design": "",
                "criteria": criteria,
                "test_skeletons": skeletons
                or [{"path": f"tests/test_{slug}.py", "purpose": "p",
                     "covers": list(range(len(criteria)))}],
                "built": built,
            },
            sort_keys=False, allow_unicode=True,
        ),
        encoding="utf-8",
    )


def test_a_criterion_gets_an_id_and_keeps_it(tmp_path: Path) -> None:
    _write_spec(tmp_path, "orders", ["The API shall accept an order."])
    req.sync_ledger(tmp_path)
    first = req.load_ledger(tmp_path)
    assert [r.id for r in first] == ["R-001"]

    # A second, unrelated spec must not disturb the first one's id.
    _write_spec(tmp_path, "accounts", ["The API shall create an account."])
    req.sync_ledger(tmp_path)
    after = {r.spec_slug: r.id for r in req.load_ledger(tmp_path)}
    assert after["orders"] == "R-001"
    assert after["accounts"] == "R-002"


def test_reordering_a_spec_does_not_repoint_an_id(tmp_path: Path) -> None:
    """The defect this file exists to prevent: matching on position would
    silently move R-001 onto a different promise."""
    a = "The API shall accept an order."
    b = "The API shall reject an empty order."
    _write_spec(tmp_path, "orders", [a, b])
    req.sync_ledger(tmp_path)
    before = {r.text: r.id for r in req.load_ledger(tmp_path)}

    _write_spec(tmp_path, "orders", [b, a])
    req.sync_ledger(tmp_path)
    after = {r.text: r.id for r in req.load_ledger(tmp_path)}
    assert after == before


def test_an_id_is_never_reused_after_the_criterion_leaves(tmp_path: Path) -> None:
    _write_spec(tmp_path, "orders", ["The API shall accept an order."])
    req.sync_ledger(tmp_path)

    _write_spec(tmp_path, "orders", ["The API shall accept a refund."])
    req.sync_ledger(tmp_path)
    ledger = {r.id: r for r in req.load_ledger(tmp_path)}
    assert ledger["R-001"].status == "retired"
    assert ledger["R-001"].text == "The API shall accept an order."
    assert ledger["R-002"].text == "The API shall accept a refund."


def test_a_retired_criterion_that_comes_back_is_live_again(tmp_path: Path) -> None:
    text = "The API shall accept an order."
    _write_spec(tmp_path, "orders", [text])
    req.sync_ledger(tmp_path)
    _write_spec(tmp_path, "orders", ["Something else shall happen."])
    req.sync_ledger(tmp_path)
    assert {r.id: r.status for r in req.load_ledger(tmp_path)}["R-001"] == "retired"

    _write_spec(tmp_path, "orders", [text])
    req.sync_ledger(tmp_path)
    revived = {r.id: r for r in req.load_ledger(tmp_path)}["R-001"]
    assert revived.status == "proposed"
    assert revived.text == text


def test_the_sync_is_idempotent(tmp_path: Path) -> None:
    """It runs on every build. A sync that rewrites the file each time turns
    the ledger into commit noise nobody reads."""
    _write_spec(tmp_path, "orders", ["The API shall accept an order."])
    req.sync_ledger(tmp_path)
    content = req.ledger_path(tmp_path).read_text(encoding="utf-8")
    second = req.sync_ledger(tmp_path)
    assert not second.changed
    assert req.ledger_path(tmp_path).read_text(encoding="utf-8") == content


def test_status_follows_the_spec(tmp_path: Path) -> None:
    _write_spec(tmp_path, "orders", ["The API shall accept an order."])
    req.sync_ledger(tmp_path)
    assert req.load_ledger(tmp_path)[0].status == "proposed"

    _write_spec(tmp_path, "orders", ["The API shall accept an order."],
                status="approved")
    req.sync_ledger(tmp_path)
    assert req.load_ledger(tmp_path)[0].status == "approved"

    _write_spec(tmp_path, "orders", ["The API shall accept an order."],
                status="approved", built=True)
    req.sync_ledger(tmp_path)
    assert req.load_ledger(tmp_path)[0].status == "built"


def test_a_criterion_carries_the_test_that_covers_it(tmp_path: Path) -> None:
    _write_spec(
        tmp_path, "orders",
        ["The API shall accept an order.", "The API shall reject an empty order."],
        skeletons=[
            {"path": "tests/test_accept.py", "purpose": "p", "covers": [0]},
            {"path": "tests/test_reject.py", "purpose": "p", "covers": [1]},
        ],
    )
    req.sync_ledger(tmp_path)
    by_text = {r.text: r.verified_by for r in req.load_ledger(tmp_path)}
    assert by_text["The API shall accept an order."] == ["tests/test_accept.py"]
    assert by_text["The API shall reject an empty order."] == ["tests/test_reject.py"]


def test_an_unreadable_spec_does_not_retire_its_requirements(tmp_path: Path) -> None:
    """Unreadable is not gone. Retiring on a parse error would record a
    decision nobody made, and the next sync would un-retire it — churn that
    reads as a product changing its mind."""
    _write_spec(tmp_path, "orders", ["The API shall accept an order."])
    req.sync_ledger(tmp_path)
    (tmp_path / "specs" / "orders" / "spec.yaml").write_text(
        "criteria: [unclosed\n", encoding="utf-8"
    )
    req.sync_ledger(tmp_path)
    assert req.load_ledger(tmp_path)[0].status == "proposed"


def test_the_same_criterion_written_twice_is_one_requirement(tmp_path: Path) -> None:
    text = "The API shall accept an order."
    _write_spec(tmp_path, "orders", [text, text])
    req.sync_ledger(tmp_path)
    assert len(req.load_ledger(tmp_path)) == 1


def test_the_slice_says_what_it_dropped(tmp_path: Path) -> None:
    _write_spec(
        tmp_path, "orders",
        [f"The checkout shall handle payment method {i}." for i in range(10)],
    )
    req.sync_ledger(tmp_path)
    sl = req.relevant(tmp_path, "checkout payment", cap=3)
    assert len(sl.shown) == 3
    assert sl.matched == 10
    assert sl.dropped == 7
    rendered = req.render_slice(sl)
    assert "3 of 10" in rendered and "7" in rendered


def test_an_empty_slice_says_so_rather_than_rendering_nothing(tmp_path: Path) -> None:
    """A blank block in a prompt reads as 'this product promises nothing',
    which is a different claim from 'nothing matched'."""
    assert req.render_slice(req.RequirementSlice()).strip()
    assert "no existing requirement" in req.render_slice(req.RequirementSlice())


def test_retired_requirements_are_not_offered_to_a_planner(tmp_path: Path) -> None:
    _write_spec(tmp_path, "orders", ["The checkout shall accept an order."])
    req.sync_ledger(tmp_path)
    _write_spec(tmp_path, "orders", ["Something unrelated shall happen."])
    req.sync_ledger(tmp_path)
    assert not req.relevant(tmp_path, "checkout order").shown


def test_the_slice_is_stable_for_the_same_corpus_and_request(tmp_path: Path) -> None:
    _write_spec(
        tmp_path, "orders",
        [f"The checkout shall handle payment method {i}." for i in range(8)],
    )
    req.sync_ledger(tmp_path)
    once = [r.id for r in req.relevant(tmp_path, "checkout payment", cap=4).shown]
    twice = [r.id for r in req.relevant(tmp_path, "checkout payment", cap=4).shown]
    assert once == twice


def test_provenance_is_recorded_once_and_never_overwritten(tmp_path: Path) -> None:
    _write_spec(tmp_path, "orders", ["The API shall accept an order."])
    req.sync_ledger(tmp_path)
    req.attribute_origin(tmp_path, set(), "product/features/01-orders/fdr.md")
    assert req.load_ledger(tmp_path)[0].origin == "product/features/01-orders/fdr.md"

    known = {r.id for r in req.load_ledger(tmp_path)}
    _write_spec(tmp_path, "orders", ["The API shall accept an order.",
                                     "The API shall list orders."])
    req.sync_ledger(tmp_path)
    req.attribute_origin(tmp_path, known, "product/features/02-listing/fdr.md")
    origins = {r.text: r.origin for r in req.load_ledger(tmp_path)}
    assert origins["The API shall accept an order."] == \
        "product/features/01-orders/fdr.md"
    assert origins["The API shall list orders."] == \
        "product/features/02-listing/fdr.md"


def test_the_ledger_tokenizer_stops_the_words_ears_puts_in_every_criterion() -> None:
    """A similarity score built on 'shall' and 'the' ranks nothing, and the
    correlator's stopword list — written for incident text — stops neither."""
    from ai_venture_studio.maintenance.correlate import _tokens as incident_tokens

    grammar = "The system shall respond."
    assert not req.tokens(grammar) & {"shall", "the", "system"}
    assert incident_tokens(grammar) & {"shall", "system"}


def test_the_planner_is_shown_the_requirements_it_is_told_it_has() -> None:
    """The prompt names <existing_requirements>; the assembly must actually
    build that block. A rule about a section that is never sent is a rule
    the model cannot follow."""
    from ai_venture_studio.upstream import autopilot

    assert "<existing_requirements>" in autopilot._FEATURE_PLANNER_SYSTEM
    source = inspect.getsource(autopilot.run_feature)
    assert "<existing_requirements>" in source
    assert "render_slice(req_slice)" in source


def _calls(func) -> set[str]:
    tree = ast.parse(inspect.getsource(func).lstrip())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def test_the_ledger_is_kept_current_by_the_build_not_by_a_person() -> None:
    """`sync_ledger` must run from the one place a criterion's status
    actually changes. A ledger updated only when someone remembers is the
    stale-authoritative-file failure this whole design avoids."""
    from ai_venture_studio.upstream import build

    assert "sync_ledger" in _calls(build.finalize_build_bookkeeping)


def test_the_feature_path_syncs_before_it_reads_and_attributes_after() -> None:
    """Order is the whole contract: a slice read before the sync would miss a
    spec built moments earlier, and provenance stamped before the build would
    name an FDR for requirements it never asked for."""
    from ai_venture_studio.upstream import autopilot

    source = inspect.getsource(autopilot.run_feature)
    assert _calls(autopilot.run_feature) >= {
        "sync_ledger", "relevant", "render_slice", "attribute_origin",
    }
    first_sync = source.index("sync_ledger(root)")
    assert first_sync < source.index("relevant(root, fdr_text")
    assert source.index("attribute_origin(") > source.index("_retry_failed_tasks(")
