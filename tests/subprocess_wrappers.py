"""Bare-name argv heads that reach `subprocess` through a helper.

ADR-064 converted 152 call sites and left a static ratchet,
`test_every_subprocess_head_in_src_resolves_through_one_place`. It flags a
list literal whose head is a bare string, passed to a call whose `func` is an
`ast.Attribute` named run / Popen / check_output / check_call — that is
`subprocess.run([...])`, and nothing else. Ruff's `S607` sees the same shape
and no more.

    _run(["git", "worktree", "add", "--detach", str(worktree), "HEAD"], repo)

is an `ast.Name` call, so neither one looks at it, and inside the helper the
head is a *parameter*, which no linter can trace back to the literal. Both
detectors were blind in the same place, which is why they agreed. ADR-069
measured the gap at **35 sites across 6 files and 19 executables**, including
the `npm` invocation that ADR-064's own module docstring names as the reason
the resolver exists.

This asks the question the ratchet meant to ask, in two passes:

  1. find the WRAPPERS — functions that hand a parameter of their own to
     `subprocess.<run|Popen|check_output|check_call>` as argv;
  2. find the CALLS to those wrappers whose argument in that position is a
     list literal with a bare-name string head.

Pass 1 runs **to a fixed point**, because wrappers nest: `_run_and_classify`
passes its argv to `_run`, which passes it to `Popen`. A single pass sees only
the inner one, reports a smaller number, and gives no sign that it is smaller.

Deliberately conservative in both directions. A wrapper counts only if the
argv it forwards is literally one of its own parameters; a call site counts
only if the head is a plain string constant with no `/`. Both under-report,
which is the right way round for a detector whose output is an accusation.
`resolve("git")` in argv is excluded for free — it is an `ast.Call`, not an
`ast.Constant`, so it never reaches the head test.

ADR-070 added the two shapes that survived all of the above, because every
detector so far — `S607`, ADR-064's ratchet, and `wrapped_bare_heads` itself —
requires the list literal to sit AT the call site:

* `variable_argv_heads` — argv reaches the call through a local name, either
  `argv = [...]` or `for cmd in ([...], [...])`. One of these was live in
  `testing.py` and was fixed during ADR-069 only because that file happened to
  be under a hand sweep. A defect fixed by luck is an open class.
* `factory_argv_heads` — argv is built by a function and RETURNED, so no
  `subprocess` call appears anywhere near the literal. `pytest_cmd` returned
  `["uv", "run", ..., "pytest"]` and three callers executed it.

All four detectors compose with the wrapper closure rather than duplicating
it, so a new hop is taught to the scan once.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_REL = "src/ai_venture_studio"
SUBPROCESS_CALLS = frozenset({"run", "Popen", "check_output", "check_call"})

#: Wrapper closure rounds before the scan declares itself broken. Nesting is
#: two deep in this repo; ten is a runaway guard, not a budget.
_MAX_ROUNDS = 10


def _param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = fn.args
    return [p.arg for p in (*args.posonlyargs, *args.args)]


def _imported_names(tree: ast.AST) -> dict[str, str]:
    """local name -> module it came from, for `from x.y import name`."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                out[alias.asname or alias.name] = node.module
    return out


def _owner_of(
    name: str,
    rel: str,
    wrappers: dict[tuple[str, str], int],
    imported: dict[str, str],
) -> str | None:
    """Which file's wrapper a call refers to, or None if it is not one.

    Same file first, then a `from ... import name` whose module holds exactly
    one wrapper by that name. Never "some file somewhere defines this name":
    `_run` is defined in three modules here with the argv in a DIFFERENT
    parameter position in each, so a name-keyed table lets one silently
    overwrite another and then checks every call site against the wrong index.
    That is ADR-064's own lesson — a name is a weak key — and the first draft
    of this scan tripped on it, reporting a clean sweep it had not performed.
    """
    if (rel, name) in wrappers:
        return rel
    if name in imported:
        tail = imported[name].rsplit(".", 1)[-1]
        hits = [f for (f, n) in wrappers if n == name and f.endswith(f"/{tail}.py")]
        if len(hits) == 1:
            return hits[0]
    return None


def _sources(root: Path, src_rel: str) -> dict[str, ast.AST]:
    src = root / src_rel
    if not src.is_dir():
        raise FileNotFoundError(
            f"{src_rel} is not a directory under {root} — refusing to report a "
            "clean sweep over a tree that is not there"
        )
    trees = {
        p.relative_to(root).as_posix(): ast.parse(p.read_text(encoding="utf-8"))
        for p in sorted(src.rglob("*.py"))
    }
    if not trees:
        raise RuntimeError(
            f"no Python files under {src_rel} — an empty scan reports zero "
            "offenders, which is exactly what a passing one reports"
        )
    return trees


def subprocess_wrappers(
    root: Path, *, src_rel: str = SRC_REL
) -> dict[tuple[str, str], int]:
    """(file, function) -> index of the parameter it forwards as argv."""
    trees = _sources(root, src_rel)
    imports = {rel: _imported_names(tree) for rel, tree in trees.items()}
    wrappers: dict[tuple[str, str], int] = {}

    for _round in range(_MAX_ROUNDS):
        before = len(wrappers)
        for rel, tree in trees.items():
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                params = _param_names(fn)
                if not params:
                    continue
                for node in ast.walk(fn):
                    if not (isinstance(node, ast.Call) and node.args):
                        continue
                    idx = None
                    if (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr in SUBPROCESS_CALLS
                    ):
                        idx = 0
                    elif isinstance(node.func, ast.Name):
                        owner = _owner_of(node.func.id, rel, wrappers, imports[rel])
                        if owner is not None:
                            idx = wrappers[(owner, node.func.id)]
                    if idx is None or len(node.args) <= idx:
                        continue
                    argv = node.args[idx]
                    if isinstance(argv, ast.Name) and argv.id in params:
                        wrappers[(rel, fn.name)] = params.index(argv.id)
        if len(wrappers) == before:
            return wrappers
    raise RuntimeError(
        f"the wrapper closure did not settle in {_MAX_ROUNDS} rounds; the scan "
        "is not measuring what it claims to"
    )


def _bare_head(node: ast.AST | None) -> str | None:
    """The bare-name head of a list literal, or None if there isn't one."""
    if not (isinstance(node, ast.List) and node.elts):
        return None
    head = node.elts[0]
    if (
        isinstance(head, ast.Constant)
        and isinstance(head.value, str)
        and "/" not in head.value
    ):
        return head.value
    return None


def _argv_position(
    node: ast.Call,
    rel: str,
    wrappers: dict[tuple[str, str], int],
    imported: dict[str, str],
) -> tuple[int | None, str | None]:
    """Where argv sits in this call, and what to call the callee — or (None, None).

    A direct `subprocess.*` call, or a call to one of the ADR-069 wrappers.
    """
    if isinstance(node.func, ast.Attribute) and node.func.attr in SUBPROCESS_CALLS:
        return 0, f"subprocess.{node.func.attr}"
    if isinstance(node.func, ast.Name):
        owner = _owner_of(node.func.id, rel, wrappers, imported)
        if owner is not None:
            return wrappers[(owner, node.func.id)], node.func.id
    return None, None


def _bindings(fn: ast.AST) -> dict[str, list[ast.List]]:
    """`name -> list literals it can hold`, within one function body.

    Two forms, both of which occur here and neither of which any of the three
    earlier detectors can see:

        argv = ["docker", "exec", ...]      ->  _run(argv, worktree)
        for cmd in (["git", "init"], ...):  ->  subprocess.run(cmd, cwd=repo)

    A name bound more than once, or bound to anything that is not a list
    literal, is DROPPED. Resolving it to whichever branch the walk saw last
    would be a guess, and a guessing detector makes false accusations.
    """
    seen: dict[str, list[ast.List] | None] = {}

    def bind(name: str, values: list[ast.List] | None) -> None:
        seen[name] = None if name in seen or values is None else values

    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                bind(
                    target.id,
                    [node.value] if isinstance(node.value, ast.List) else None,
                )
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if not isinstance(node.target, ast.Name):
                continue
            it = node.iter
            elts = it.elts if isinstance(it, (ast.Tuple, ast.List)) else []
            lists = [e for e in elts if isinstance(e, ast.List)]
            bind(node.target.id, lists if elts and len(lists) == len(elts) else None)

    return {name: lists for name, lists in seen.items() if lists}


def variable_argv_heads(root: Path, *, src_rel: str = SRC_REL) -> list[dict]:
    """Bare heads that reach argv through a local variable, not a literal."""
    trees = _sources(root, src_rel)
    wrappers = subprocess_wrappers(root, src_rel=src_rel)
    out: list[dict] = []
    seen: set[tuple] = set()
    for rel, tree in trees.items():
        imported = _imported_names(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            bound = _bindings(fn)
            if not bound:
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call) and node.args):
                    continue
                idx, via = _argv_position(node, rel, wrappers, imported)
                if idx is None or len(node.args) <= idx:
                    continue
                argv = node.args[idx]
                if not isinstance(argv, ast.Name):
                    continue
                for literal in bound.get(argv.id, []):
                    head = _bare_head(literal)
                    key = (rel, node.lineno, argv.id, head)
                    if head and key not in seen:
                        seen.add(key)
                        out.append(
                            {
                                "file": rel,
                                "line": node.lineno,
                                "via": via,
                                "var": argv.id,
                                "head": head,
                                "bound": literal.lineno,
                            }
                        )
    return sorted(out, key=lambda o: (o["file"], o["line"], o["head"]))


def _argv_factories(trees: dict[str, ast.AST]) -> dict[tuple[str, str], list[tuple[int, str]]]:
    """(file, function) -> the (line, bare head) pairs it can return."""
    out: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for rel, tree in trees.items():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            heads = [
                (node.value.lineno, head)
                for node in ast.walk(fn)
                if isinstance(node, ast.Return)
                and (head := _bare_head(node.value)) is not None
            ]
            if heads:
                out[(rel, fn.name)] = heads
    return out


def factory_argv_heads(root: Path, *, src_rel: str = SRC_REL) -> list[dict]:
    """Bare heads built by an argv factory and executed by one of its callers.

    `pytest_cmd` returned `["uv", "run", ...]` and `_run_and_classify` ran it.
    No `subprocess` call is anywhere near the literal, so `S607` and ADR-064's
    ratchet see nothing; the literal is not an argument at a wrapper call
    site, so ADR-069's scan sees nothing; it is a `return` rather than an
    assignment, so `variable_argv_heads` sees nothing either.

    A factory is reported only when a caller is found that actually executes
    what it returns. A list that merely looks like argv and is never run is
    not an accusation — `netem_command` deliberately returns a bare
    `["tc", ...]` as the RECORD of what a Linux host would run, and resolves
    the binary separately at the exec.
    """
    trees = _sources(root, src_rel)
    wrappers = subprocess_wrappers(root, src_rel=src_rel)
    factories = _argv_factories(trees)
    by_name: dict[str, list[tuple[str, str]]] = {}
    for key in factories:
        by_name.setdefault(key[1], []).append(key)

    out: list[dict] = []
    for rel, tree in trees.items():
        imported = _imported_names(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            idx, via = _argv_position(node, rel, wrappers, imported)
            if idx is None or len(node.args) <= idx:
                continue
            argv = node.args[idx]
            # `factory(...)` passed straight through, or splatted into a
            # longer argv as `[*factory(...), extra]`.
            calls = (
                [argv]
                if isinstance(argv, ast.Call)
                else [
                    e.value
                    for e in getattr(argv, "elts", [])
                    if isinstance(e, ast.Starred) and isinstance(e.value, ast.Call)
                ]
            )
            for call in calls:
                name = (
                    call.func.id
                    if isinstance(call.func, ast.Name)
                    else getattr(call.func, "attr", None)
                )
                for key in by_name.get(name, ()):
                    for lineno, head in factories[key]:
                        out.append(
                            {
                                "file": rel,
                                "line": node.lineno,
                                "via": via,
                                "factory": f"{key[0]}::{key[1]}",
                                "returns": lineno,
                                "head": head,
                            }
                        )
    return sorted(out, key=lambda o: (o["file"], o["line"], o["returns"]))


def wrapped_bare_heads(root: Path, *, src_rel: str = SRC_REL) -> list[dict]:
    """Every call site handing a bare executable name to one of them."""
    trees = _sources(root, src_rel)
    wrappers = subprocess_wrappers(root, src_rel=src_rel)
    out: list[dict] = []
    for rel, tree in trees.items():
        imported = _imported_names(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            owner = _owner_of(node.func.id, rel, wrappers, imported)
            if owner is None:
                continue
            idx = wrappers[(owner, node.func.id)]
            if len(node.args) <= idx:
                continue
            argv = node.args[idx]
            head = _bare_head(argv)
            if head is not None:
                out.append(
                    {
                        "file": rel,
                        "line": argv.elts[0].lineno,
                        "wrapper": f"{owner}::{node.func.id}",
                        "head": head,
                    }
                )
    return sorted(out, key=lambda o: (o["file"], o["line"]))
