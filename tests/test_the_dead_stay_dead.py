"""What ADR-068 measured as dead, deleted — and kept deleted.

ADR-068's coverage run found 93 functions with zero executed statements and
triaged them down to **six genuinely dead**: no reference in `src/`, `tests/`,
`scripts/`, no string mention, no re-export, no dynamic dispatch. It then left
them in place on purpose, saying so in writing:

> Deleting the six dead functions stays out — that is a `src/` change and a
> visible decision, not a tidy-up to fold into a test-only commit.

This is that decision, taken. Five of the six are gone (the sixth,
`record_calibration`, is the opposite case — it is being *wired up*, because
it is the only writer of a committed artifact).

**Why a test for an absence.** Dead code does not come back by itself; it comes
back because someone needs a thing, greps for a plausible name, finds one, and
calls it. Two of these five are actively misleading to such a reader:

  * `verdicts.is_terminal` returned `verdict in ALL_VERDICTS` — `True` for
    every verdict there is. Anyone reaching for "has this stage ended?" would
    have found a function with exactly the right name and a body that answers
    a different question.
  * `wireup.wireup_diff_gate` was `return wireup_check(repo_dir)` under a name
    promising a build-gate step. `wireup_check` is the real one, wired at
    `upstream/build.py`.

So the assertion is not "these lines were removed" — a diff shows that. It is
that the *names* do not resolve, anywhere, by any route: not as a module
attribute, not as a re-export, not as a file on disk. That is the property a
future grep depends on.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "ai_venture_studio"

# (module path, attribute) — each measured dead by ADR-068's coverage run.
DELETED = [
    ("ai_venture_studio.upstream.verdicts", "is_terminal"),
    ("ai_venture_studio.tools.wireup", "wireup_diff_gate"),
    ("ai_venture_studio.profile_schema", "load_structured_profile"),
]

# The whole module, not two of its functions. ADR-068 named `post_pr_comment`
# and `pr_head_branch`; the wider fact is that NOTHING in `src/` imported
# `github` at all — `forge.py` replaced it with a forge-aware superset
# (`post_comment`, `merge`, `head_branch`, `create_issue`), and `cli.py` and
# `orchestrator/graph.py` call forge. Deleting two functions from a module
# nothing imports would have left the other three exactly as dead.
DELETED_MODULES = ["ai_venture_studio.github"]


@pytest.mark.parametrize(("module", "name"), DELETED)
def test_the_name_does_not_resolve(module, name):
    mod = importlib.import_module(module)
    assert not hasattr(mod, name), (
        f"{module}.{name} is back. ADR-068 measured it as executed by no test "
        f"and called by nothing; if it is needed now, it needs a caller and a "
        f"test in the same change, not a re-add."
    )


@pytest.mark.parametrize("module", DELETED_MODULES)
def test_the_module_does_not_import(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_nothing_in_the_tree_still_names_them():
    """`hasattr` cannot see a re-add under a different module, and a stale
    caller left behind by a partial revert would raise only on the path that
    reaches it — which, for code this cold, may be no path at all.

    Read the SYNTAX, not the text. The first draft of this test grepped for
    the strings and failed on the tombstone comment in `verdicts.py` that
    explains the deletion — the explanation supplying the mention the test
    asserts is absent, which is ADR-060's defect at small scale. An `ast`
    walk answers the question actually being asked (does any code reference
    this name?) and is blind to comments and prose by construction, so the
    record can stay next to the hole it describes.
    """
    names = {name for _, name in DELETED} | {
        "merge_pr", "post_pr_comment", "pr_head_branch",
    }
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            referenced = (
                node.id if isinstance(node, ast.Name)
                else node.attr if isinstance(node, ast.Attribute)
                else node.name if isinstance(node, ast.FunctionDef)
                else None
            )
            if referenced in names:
                offenders.append(
                    f"{path.relative_to(REPO)}:{node.lineno}: {referenced}"
                )
    assert not offenders, (
        "deleted names reappeared in src/ as code: " + ", ".join(offenders)
    )


def test_the_forge_superset_really_covers_what_github_did():
    """The deletion's premise, asserted rather than asserted-in-prose: every
    side effect `github.py` offered exists on `forge`. If a later change
    narrows forge, this fails here instead of at the next merge attempt."""
    from ai_venture_studio import forge

    for name in ("post_comment", "merge", "head_branch", "create_issue"):
        assert callable(getattr(forge, name, None)), (
            f"forge.{name} is missing — `github.py` was deleted on the "
            f"grounds that forge supersedes it, and that is no longer true"
        )
