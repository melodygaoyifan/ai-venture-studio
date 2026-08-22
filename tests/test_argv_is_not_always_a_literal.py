"""A bare executable name that never appears at a `subprocess` call.

Four detectors have now asked "does a bare name reach the kernel?" and the
first three all required the same thing: a list literal sitting AT the call.

    subprocess.run(["git", ...])          S607, ADR-064's ratchet
    _run(["git", ...], repo)              ADR-069's ratchet
    argv = ["git", ...]; _run(argv, repo)      <- nobody
    for cmd in (["git", "init"], ...):         <- nobody
    return ["uv", "run", ..., "pytest"]        <- nobody

ADR-070 measured the last two. `testing.py:pytest_cmd` was in the third
group: it gated on `shutil.which("uv")`, discarded the path, returned
`["uv", "run", "--project", ..., "pytest"]`, and three callers ran it. That is
the product's own test suite executing in a workspace built from model output,
which is the sentence `executables.py` opens with.

`tests/` is in scope here, unlike ADR-069. Its scope note claimed "`S607`
holds them", and that is wrong for exactly the reason this file exists — the
three `_git_repo` helpers looped over `(["git", "init", "-q"], ...)`, which
`S607` cannot see. The threat-model half of that note still stands; the
enforcement half did not, so it is enforced here instead of argued.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from subprocess_wrappers import (
    SRC_REL,
    _bindings,
    factory_argv_heads,
    subprocess_wrappers,
    variable_argv_heads,
)

REPO = Path(__file__).resolve().parents[1]

#: Both trees. ADR-069 scanned only `src/`; see the module docstring.
SCANNED = (SRC_REL, "tests")


def _describe(offenders: list[dict]) -> str:
    return "\n".join(
        f"  {o['file']}:{o['line']}  {o['head']!r}  via {o['via']}"
        + (f"  (bound at line {o['bound']})" if "bound" in o else "")
        + (f"  (returned by {o['factory']} line {o['returns']})" if "factory" in o else "")
        for o in offenders
    )


@pytest.mark.parametrize("src_rel", SCANNED)
def test_no_bare_executable_reaches_argv_through_a_local_name(src_rel):
    offenders = variable_argv_heads(REPO, src_rel=src_rel)
    assert not offenders, (
        f"{len(offenders)} bare executable name(s) reach argv through a local "
        f"variable in {src_rel}. Resolve through ai_venture_studio.executables "
        f"— `resolve(name)` when the run cannot proceed without it, "
        f"`find(name)` when absence is an expected answer:\n" + _describe(offenders)
    )


@pytest.mark.parametrize("src_rel", SCANNED)
def test_no_bare_executable_is_built_by_a_factory_and_then_run(src_rel):
    offenders = factory_argv_heads(REPO, src_rel=src_rel)
    assert not offenders, (
        f"{len(offenders)} argv factories in {src_rel} return a bare executable "
        f"name that a caller then executes. The gate's answer must BE the "
        f"executable:\n" + _describe(offenders)
    )


# --- the instrument, which is the part that can lie -------------------------
#
# ADR-067 found eight instrument defects that produced an EMPTY measurement,
# and an empty measurement reads exactly like a passing one. That applies with
# particular force here, where passing IS the number zero: every assertion
# above passes just as well if the scan silently stops parsing.


def _tree(tmp_path: Path, rel: str, body: str) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def test_the_scan_refuses_to_report_from_no_measurement(tmp_path):
    """A moved or empty tree must raise, not report a clean sweep."""
    for scan in (variable_argv_heads, factory_argv_heads):
        with pytest.raises(FileNotFoundError):
            scan(tmp_path, src_rel="src/gone")
        (tmp_path / "empty").mkdir(exist_ok=True)
        with pytest.raises(RuntimeError, match="no Python files"):
            scan(tmp_path, src_rel="empty")


def test_the_scan_finds_argv_bound_by_an_assignment(tmp_path):
    root = _tree(tmp_path, "src/x/m.py", """
        import subprocess

        def go(repo):
            argv = ["docker", "exec", repo]
            return subprocess.run(argv, cwd=repo)
    """)
    (found,) = variable_argv_heads(root, src_rel="src/x")
    assert found["head"] == "docker" and found["var"] == "argv"


def test_the_scan_finds_argv_bound_by_a_for_loop(tmp_path):
    """The shape all three `_git_repo` helpers used."""
    root = _tree(tmp_path, "src/x/m.py", """
        import subprocess

        def setup(repo):
            for cmd in (["git", "init"], ["git", "add", "-A"]):
                subprocess.run(cmd, cwd=repo)
    """)
    heads = {o["head"] for o in variable_argv_heads(root, src_rel="src/x")}
    assert heads == {"git"}


def test_the_scan_follows_a_factory_through_a_wrapper_and_a_splat(tmp_path):
    """`_run([*pytest_cmd(w), extra], w)` is how `fixpr.py` consumed it."""
    root = _tree(tmp_path, "src/x/m.py", """
        import subprocess

        def _run(cmd, cwd):
            return subprocess.run(cmd, cwd=cwd)

        def pytest_cmd(w):
            return ["uv", "run", "pytest"]

        def go(w):
            return _run([*pytest_cmd(w), "-k", "smoke"], w)
    """)
    (found,) = factory_argv_heads(root, src_rel="src/x")
    assert found["head"] == "uv" and found["via"] == "_run"


def test_a_factory_nothing_executes_is_not_an_accusation(tmp_path):
    """`netem_command` returns a bare `["tc", ...]` on purpose, as the record
    of what a Linux host would run, and resolves the binary at the exec."""
    root = _tree(tmp_path, "src/x/m.py", """
        def netem_command(iface):
            return ["tc", "qdisc", "add", "dev", iface]

        def report(iface):
            return {"command": netem_command(iface)}
    """)
    assert factory_argv_heads(root, src_rel="src/x") == []


def test_a_name_bound_twice_is_dropped_rather_than_guessed_at(tmp_path):
    """Resolving to whichever branch the walk saw last would be a guess, and a
    guessing detector makes false accusations, which is how a ratchet gets
    turned off."""
    root = _tree(tmp_path, "src/x/m.py", """
        import subprocess

        def go(repo, resolved):
            argv = ["git", "status"]
            argv = [resolved, "status"]
            return subprocess.run(argv, cwd=repo)
    """)
    assert variable_argv_heads(root, src_rel="src/x") == []
    fn = next(
        n for n in ast.walk(ast.parse((root / "src/x/m.py").read_text()))
        if isinstance(n, ast.FunctionDef)
    )
    assert "argv" not in _bindings(fn)


def test_the_scan_still_recognises_the_wrappers_it_composes_with():
    """Both scans reach through the ADR-069 wrapper closure. If that closure
    comes back empty they still report zero — byte-identical to a clean tree,
    and wrong."""
    wrappers = subprocess_wrappers(REPO, src_rel=SRC_REL)
    assert wrappers, "the wrapper closure is empty; these scans measured nothing"
    assert any(f.endswith("/testing.py") for f, _n in wrappers)


def test_a_tree_with_no_wrappers_at_all_is_still_really_scanned(tmp_path):
    """The wrapper half of a scan can be empty, and an empty half proves nothing.

    When this was written `tests/` had no subprocess wrappers at all, so the
    clean result over `tests/` rested entirely on the direct `subprocess.*`
    half continuing to work with an empty wrapper closure — which is
    indistinguishable from a broken scan unless something pins it. ADR-071
    then added `_spawn`, so the real tree no longer exercises that case and
    the synthetic one below is now the only place it is pinned.
    """
    assert subprocess_wrappers(REPO, src_rel="tests") == {
        ("tests/test_the_kernel_sees_what_we_think_it_sees.py", "_spawn"): 0
    }, (
        "tests/ gained or lost a subprocess wrapper. This is a one-entry ledger "
        "on purpose: `_spawn` exists to hand the runtime audit a deliberately "
        "bad argv (ADR-071), and a second wrapper in tests/ should be a "
        "decision someone made, not a thing that happened."
    )
    root = _tree(tmp_path, "src/x/m.py", """
        import subprocess

        def setup(repo):
            for cmd in (["git", "init"],):
                subprocess.run(cmd, cwd=repo)
    """)
    assert subprocess_wrappers(root, src_rel="src/x") == {}
    (found,) = variable_argv_heads(root, src_rel="src/x")
    assert found["head"] == "git"
