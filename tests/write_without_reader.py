"""Find a fact written into a field that nothing anywhere reads.

ADR-058 fixed six of these one at a time, each found by hand after an
expensive bench run had already paid to expose it. They had one shape:

    a component established a fact, put it on the record, and the reader
    that needed it never got it.

That shape is mechanical, so this asks it mechanically. For every field
declared on a model class under `src/`, it looks for ANY reader in the repo
and reports the ones with none. ADR-060 ran it once and it returned five
defects the runs had never surfaced, including two fields on `BuildResult`
that ADR-058's own fix had walked past.

A "read" is deliberately generous — the question is whether the fact reaches
anyone, not whether it reaches them elegantly:

  * `x.field` in Load context
  * `d["field"]`, `d.get("field")`, `d.pop("field")`
  * the name inside any string literal, in any .py/.yaml/.md/.json in the
    repo (rendered into a report, matched out of a document, named in a
    prompt — all readers)

A write is not a read: `x.field = v`, `Model(field=v)`, and the declaring
annotation itself.

This is a helper, like `claim_count`. `test_write_without_reader.py` is what
runs it, and the allowlist there is where the judgment lives.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

#: Bases whose annotated class body is a record of facts, not code.
MODEL_BASES = frozenset({"BaseModel", "TypedDict", "NamedTuple"})

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TEXT_SUFFIXES = (".yaml", ".yml", ".md", ".json", ".txt", ".j2", ".toml")
_SKIP_DIRS = frozenset({".venv", ".git", "node_modules", "__pycache__", ".mas"})


def _py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*.py")
        if not _SKIP_DIRS & set(p.relative_to(root).parts)
    )


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None


def _is_record(node: ast.ClassDef) -> bool:
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if name in MODEL_BASES:
            return True
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = (
            target.attr if isinstance(target, ast.Attribute)
            else getattr(target, "id", "")
        )
        if name == "dataclass":
            return True
    return False


def declared_fields(src: Path, repo: Path) -> dict[str, list[str]]:
    """field name -> ["module.py:ClassName", ...] for every record class."""
    found: dict[str, list[str]] = defaultdict(list)
    for path in _py_files(src):
        tree = _parse(path)
        if tree is None:
            continue
        rel = str(path.relative_to(repo))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_record(node):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name = stmt.target.id
                    # Private and SCREAMING_CASE are not the record's surface.
                    if name.startswith("_") or name.isupper():
                        continue
                    found[name].append(f"{rel}:{node.name}")
    return dict(found)


def _read_names(tree: ast.Module, wanted: frozenset[str]) -> set[str]:
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            if node.attr in wanted:
                seen.add(node.attr)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in wanted:
                seen.add(key.value)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {"get", "pop",
                                                                 "setdefault"}:
                if node.args and isinstance(node.args[0], ast.Constant):
                    value = node.args[0].value
                    if isinstance(value, str) and value in wanted:
                        seen.add(value)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            seen |= wanted & set(_WORD.findall(node.value))
    return seen


def unread_fields(repo: Path) -> dict[str, list[str]]:
    """Declared fields that nothing in the repo reads.

    Returns the same mapping shape as `declared_fields`, filtered down.
    """
    repo = Path(repo)
    declared = declared_fields(repo / "src", repo)
    wanted = frozenset(declared)
    read: set[str] = set()

    for scan in ("src", "tests", "scripts", "benchmarks"):
        for path in _py_files(repo / scan):
            tree = _parse(path)
            if tree is not None:
                read |= _read_names(tree, wanted)

    # Non-Python readers: a field rendered into a report, keyed in a fixture,
    # or named in a doc is read by a person, which is the only reader some of
    # these records have ever had.
    for path in repo.rglob("*"):
        if path.suffix not in _TEXT_SUFFIXES or not path.is_file():
            continue
        if _SKIP_DIRS & set(path.relative_to(repo).parts):
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        read |= wanted & set(_WORD.findall(body))

    return {f: sites for f, sites in sorted(declared.items()) if f not in read}


if __name__ == "__main__":  # pragma: no cover - operator entry point
    import sys

    where = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    unread = unread_fields(where)
    print(f"{len(unread)} field(s) written and never read:\n")
    for field, sites in unread.items():
        print(f"  {field:34s} {'; '.join(sites)}")
