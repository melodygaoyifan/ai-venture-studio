"""assertion_delta (§13.29.5) — the anti-test-weakening AST diff.

When the implementer is allowed to rewrite a test file it authored (its
skeleton surface), the rewrite must not weaken it: removed assert
statements, added skip/xfail markers, or widened numeric tolerances are
build-gate failures citing the exact node. Pure `ast` — no new deps.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable

from pydantic import BaseModel

_SKIP_MARKERS = ("skip", "skipif", "xfail")


class AssertionChange(BaseModel):
    change: str  # removed_assert | added_skip
    node: str


def _collect(source: str) -> tuple[list[str], list[str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []
    asserts, skips = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            asserts.append(ast.unparse(node))
        elif isinstance(node, (ast.Call, ast.Attribute)):
            code = ast.unparse(node)
            if any(f"pytest.{m}" in code or f"mark.{m}" in code for m in _SKIP_MARKERS):
                skips.append(code)
    return asserts, skips


def assertion_delta(
    before: str, after: str, *, elsewhere: "Iterable[str]" = ()
) -> list[AssertionChange]:
    """Weakening changes between two versions of one test file.

    `elsewhere` is the OTHER files written in the same batch. An assert that
    leaves this file and lands, unchanged, in one of them has MOVED, and a
    move is not a weakening — the suite still asserts it.

    Judging one file alone made the most-requested repair in the system
    impossible to perform. Bench run 16's reviewers asked, over and over, for
    duplicated test boilerplate to be hoisted into a shared helper ("across
    six new test files", "across four new test files", "instead of a shared
    fixture/helper"); every such repair moves asserts out of the call sites
    and into the helper, this function read each departure as a deletion, and
    `_write_files` dropped exactly the files that carried the fix while
    keeping the new helper. The repair pass then committed a HALF-applied
    change: the duplication still there, plus an orphan helper nothing
    called — which the re-review duly flagged ("Unused alias function
    diverges from spec's stated call path"). The pass was manufacturing the
    findings that rejected it, and the row blamed the product.

    The reward-hacking defence is untouched: an assert that appears NOWHERE
    in the batch is still a removal, and `added_skip` is never forgiven by
    relocation — you cannot move a skip into existence.
    """
    before_asserts, before_skips = _collect(before)
    after_asserts, after_skips = _collect(after)
    moved: set[str] = set()
    for source in elsewhere:
        moved.update(_collect(source)[0])
    changes = [
        AssertionChange(change="removed_assert", node=node)
        for node in before_asserts
        if node not in after_asserts and node not in moved
    ]
    changes += [
        AssertionChange(change="added_skip", node=node)
        for node in after_skips
        if node not in before_skips
    ]
    return changes
