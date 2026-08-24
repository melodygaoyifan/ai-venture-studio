"""Coding stage (§13, ADR-U01) — single-writer implementer, test-first.

Deliberately NOT a voting stage: generation is single-writer; judgment
lives in the review stage the diff is handed to afterwards. The build
gate is deterministic — the spec's test skeletons (and everything else in
the suite) must pass before the commit exists.

Bounds: ≤12 files, ≤500 lines each, repo-relative paths only, never
.git/.mas/specs. ≤3 implement-run-fix iterations, then BUILD_FAILED.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ai_venture_studio.providers import get_provider, last_response_truncated
from ai_venture_studio.testing import (
    _pytest_in_docker,
    _pytest_in_subprocess,
    _run,
    docker_available,
)
from ai_venture_studio.upstream import progress
from ai_venture_studio.upstream.spec import Spec, load_spec
from ai_venture_studio.upstream.workspace import load_project
from ai_venture_studio.yamlx import extract_mapping
from ai_venture_studio.executables import resolve

#: Where a deliberate degradation says what it degraded. Every handler
#: below that skips a row, a page, or a piece of bookkeeping logs here
#: first: CLAUDE.md forbids swallowing an exception silently, and until
#: ADR-062 nothing enforced it (`S110`/`S112` found 15). DEBUG, so it is
#: silent unless asked for — `AVS_DEBUG=1` is the ask.
_log = logging.getLogger(__name__)

IMPLEMENTER_MARKER = "single-writer implementer in a greenfield product system"

MAX_ITERATIONS = 3
_MAX_FILES = 12
_MAX_FILE_LINES = 500
_FORBIDDEN_PREFIXES = (".git", ".mas", "specs")

# The prompt below permits 12 files of 500 lines — 6000 lines of code — and the
# old 16384-token cap could not carry a third of that, so a task working at the
# top of its own stated envelope was cut off mid-file by construction. 32000 is
# the ceiling an Opus-class model accepts; asking for more is a 400 at request
# time, which would trade a silent failure for a loud one on every single build.
# The guard in the build loop covers whatever still overruns.
_IMPLEMENTER_MAX_TOKENS = 32000


class BuildResult(BaseModel):
    slug: str
    status: str  # built | build_failed | error
    iterations: int = 0
    files_written: list[str] = Field(default_factory=list)
    modified_existing: list[str] = Field(
        default_factory=list, description="scope_check-lite: pre-existing files "
        "the implementer changed — visible, reviewed, never silent"
    )
    wireup_issues: list[str] = Field(default_factory=list)
    test_summary: str = ""
    commit: str | None = None
    detail: str = ""


_SYSTEM = f"""You are the {IMPLEMENTER_MARKER}. Implement the approved spec
below, test-first: the test skeletons are the contract — write them as real
tests that encode the EARS criteria, then the smallest implementation that
passes them.

Rules:
- Return COMPLETE file contents (no diffs). At most {_MAX_FILES} files,
  each under {_MAX_FILE_LINES} lines. Include the test files.
- Respect the project constraints; no new dependencies unless the spec's
  design names them.
- Where a <source_contract> is provided, it is the founder's LITERAL
  interface contract: use its exact paths, methods, field names, and
  enumerated values verbatim (a field the contract calls "item" is never
  "index"). The acceptance probes are written from that contract, not
  from your code — inventing a synonym fails them all.
- Endpoints never crash on user input: invalid JSON, wrong types, missing
  or unknown fields get an explicit 4xx error response whose JSON body
  carries a human-readable message (an "error" field unless the contract
  fixes another shape — an empty {{}} body fails the founder's checks).
  An unhandled exception on malformed input is a defect, not a shortcut.
- tests/conftest.py and tests/helpers*.py are a SHARED vocabulary that
  other tasks' committed tests import. Reuse the existing fixtures and
  helpers as-is; extend them only additively (a rewrite that drops or
  renames an existing name is discarded). Never build a parallel fixture
  set for the same job.
- If the existing product already satisfies every criterion, do NOT
  rewrite source files: submit ONLY your skeleton test files proving the
  criteria — a passing tests-only submission closes the task as built.
- Never touch paths under {_FORBIDDEN_PREFIXES}.

Respond with ONLY YAML:
files:
  - path: ...
    new_content: |
      ...
notes: one line
"""


def _run_tests(repo: Path):
    return (
        _pytest_in_docker(repo) if docker_available() else _pytest_in_subprocess(repo)
    )


_BOOT_GATE_TIMEOUT_S = 15
_BOOT_CONTRACT_HINT = (
    "the entry point must start its own server on the PORT env var when run "
    "directly; for FastAPI append: if __name__ == \"__main__\": import uvicorn; "
    "uvicorn.run(app, host=\"127.0.0.1\", port=int(os.environ.get(\"PORT\", \"8000\")))"
)


def _boot_gate(repo: Path) -> str | None:
    """Web-profile extension of Gate U4: the product must SERVE, not just
    pass its suite — the founder and every probe boot it as
    `python <entry>` with $PORT set (product-bench run 4: every built web
    task failed all probes on 'server never listened' because nothing
    enforced this contract). Returns feedback text on failure; None when
    the entry listens or when there is no entry point yet to boot."""
    import os
    import socket
    import sys

    entry = next(
        (e for e in ("app/main.py", "main.py", "app.py") if (repo / e).exists()), None
    )
    if entry is None:
        return None
    with socket.socket() as picker:
        picker.bind(("127.0.0.1", 0))
        port = picker.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, entry],
        cwd=repo,
        env={**os.environ, "PORT": str(port), "PYTHONPATH": str(repo)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        # A server started here is a process TREE — uvicorn reloaders and
        # worker pools fork. Killing the parent alone leaves the workers
        # serving, so the boot gate would leak a live server into the test
        # run that follows it.
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + _BOOT_GATE_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                err = (proc.stderr.read() or b"").decode(errors="replace")[-400:]
                return (
                    f"BOOT GATE: `python {entry}` exited with rc={proc.returncode} "
                    f"instead of serving. stderr tail: {err.strip() or '(empty)'} — "
                    + _BOOT_CONTRACT_HINT
                )
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close()
                return None
            except OSError:
                time.sleep(0.3)
        return (
            f"BOOT GATE: `python {entry}` ran {_BOOT_GATE_TIMEOUT_S}s without "
            "listening on 127.0.0.1:$PORT — " + _BOOT_CONTRACT_HINT
        )
    finally:
        from ai_venture_studio.testing import _kill_process_group

        _kill_process_group(proc)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


_BLOCKING_SERVE = ("run", "serve_forever", "run_forever", "main_loop", "mainloop")


_BLOCKS_ON_IMPORT_HINT = (
    "nothing that blocks may run at import time — every test imports the "
    "module, so a call that never returns hangs the suite instead of failing "
    "it. Put it under the guard, or inside a function the caller invokes."
)


def _blocks_on_import(repo: Path, profile: str = "") -> str | None:
    """Reject a product whose modules SERVE the moment they are imported.

    The boot contract says `python main.py` must serve — and a module-level
    `uvicorn.run(app)` satisfies it, which is why nothing caught this. But
    `import main` is what every test does, so the same line makes the suite
    hang forever with no output: pytest collects, blocks inside the import,
    and the only signal is that five minutes passed. Bench run 12's case 04
    died exactly that way and could never be explained after the fact.

    Static, because the dynamic version of this check IS the hang. The serve
    call belongs under `if __name__ == "__main__":`, where the contract has
    always put it, and there it is invisible to an import.

    Runs for EVERY profile, which v0.85.0 got wrong. It was gated on
    web/enterprise-web on the reasoning that only they carry the boot
    contract — but the contract is what makes a web product *prone* to this,
    not what makes the hang possible. `data` is "Python + the team's existing
    warehouse/orchestrator", and a module-level `run_forever()` there hangs
    its suite exactly the same way; `_BLOCKING_SERVE` could already name it
    while the gate never ran. Absence of the instruction that induces a bug
    is not immunity from the bug. Profile only picks the fix sentence: a
    non-web product must not be told to append `uvicorn.run`.
    """
    import ast

    for path in sorted(list(repo.glob("*.py")) + list(repo.glob("app/**/*.py"))):
        if path.name.startswith("test_") or ".mas" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue  # the test run will report it far better than we can
        for node in tree.body:
            # Module level ONLY: a `__main__` guard is an ast.If and a
            # function body is a FunctionDef, and neither runs on import.
            if not isinstance(node, (ast.Expr, ast.Assign, ast.With, ast.Try)):
                continue
            for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
                func = call.func
                attr = isinstance(func, ast.Attribute)
                name = func.attr if attr else getattr(func, "id", "")
                # `.run` alone is far too common to flag; only the known
                # server objects block on it.
                blocking = name in _BLOCKING_SERVE and name != "run"
                serves = (
                    name == "run" and attr
                    and getattr(func.value, "id", "") in ("uvicorn", "app", "socketio")
                )
                if blocking or serves:
                    hint = (
                        _BOOT_CONTRACT_HINT
                        if profile in ("web", "enterprise-web")
                        else _BLOCKS_ON_IMPORT_HINT
                    )
                    return (
                        f"IMPORT GATE: {path.relative_to(repo)} line {call.lineno} "
                        f"calls `{name}(...)` at module level, so importing it "
                        "never returns — `python "
                        f"{path.relative_to(repo)}` would run it, but every test "
                        "that imports this module hangs forever instead of "
                        "failing. Move the call under "
                        '`if __name__ == "__main__":` — ' + hint
                    )
    return None


_MP_CONTRACT_HINT = (
    "a mini-program is loaded from app.json: it must exist at the "
    "miniprogram root, list every page in its `pages` array (first entry is "
    "the launch page), and be accompanied by app.js calling App({}). A page "
    "directory that is not in `pages` cannot be reached by anyone."
)


def _miniprogram_root(repo: Path) -> Path:
    """Where the mini-program actually lives.

    A declared `miniprogramRoot` wins — that is the platform's own answer.
    With nothing declared, **evidence beats directory names**: the candidate
    that actually holds an `app.json` is the project, because a directory
    merely *called* `miniprogram/` silently disabled this whole gate once. A
    real workspace kept its mini-program at the repo root (app.json + pages/
    there) beside a stray `miniprogram/` containing nothing but a .DS_Store
    and an `api/` folder; the name heuristic picked the stray, `app.json`
    was absent, and the gate answered "not my business" over a product it
    should have been checking.
    """
    import json

    config = repo / "miniprogram" / "project.config.json"
    if not config.exists():
        config = repo / "project.config.json"
    if config.exists():
        try:
            declared = json.loads(config.read_text(encoding="utf-8")).get(
                "miniprogramRoot"
            )
        except (ValueError, OSError):
            declared = None
        if declared:
            return (config.parent / str(declared)).resolve()
        return config.parent
    for candidate in (repo / "miniprogram", repo):
        if (candidate / "app.json").is_file():
            return candidate
    return repo / "miniprogram" if (repo / "miniprogram").is_dir() else repo


_MP_REQUIRE_RE = re.compile(r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")
_MP_IMPORT_RE = re.compile(r"^\s*import\b[^'\"\n]*['\"]([^'\"]+)['\"]", re.MULTILINE)
_MP_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_MP_LINE_COMMENT_RE = re.compile(r"(?<!:)//[^\n]*")


def _mp_module_specs(source: str) -> list[str]:
    """Relative and root-absolute module specifiers a JS file loads.

    Comments are stripped first (the `(?<!:)` keeps `https://` string
    literals whole). Bare names (`miniprogram-automator`) are npm
    territory — resolved through miniprogram_npm/ by a build step this
    gate cannot see — so they are not judged here.
    """
    text = _MP_BLOCK_COMMENT_RE.sub("", source)
    text = _MP_LINE_COMMENT_RE.sub("", text)
    specs = _MP_REQUIRE_RE.findall(text) + _MP_IMPORT_RE.findall(text)
    return [s for s in specs if s.startswith((".", "/"))]


def _mp_resolve(spec: str, requiring: Path, root: Path) -> Path:
    """The normalized path WeChat's CommonJS loader would look at:
    relative to the requiring file, or from the mini-program root for
    `/`-absolute specs. Existence is the caller's question."""
    import os

    base = root / spec.lstrip("/") if spec.startswith("/") else requiring.parent / spec
    return Path(os.path.normpath(base))


# Lexical declarations that collide under ES-module semantics. `var` is
# deliberately absent: re-declaring a var is legal everywhere.
_MP_TOPLEVEL_DECL = re.compile(
    r"^(?:export\s+)?(?:async\s+)?(?:function\*?|const|let|class)\s+"
    r"([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


def _mp_duplicate_declaration_problems(root: Path, repo: Path) -> list[str]:
    """A name declared twice at the top level of one module.

    Verified rather than assumed: `function a(){} function a(){}` is legal in
    a sloppy script AND in a strict one, but is a SyntaxError under ES-module
    semantics — which is what the 小程序 toolchain compiles with. So the whole
    module fails to evaluate, and every page that requires it registers no
    Page() and renders blank.

    The lesson is avs-studio-3 (2026-08-03): a second `pad2` was added to
    utils/delivery.js. 106 node --test cases stayed green — Node ran the file
    as a script — while the cart, delivery and profile pages were all blank in
    the real app. Node's own runner cannot see this; a regex over the module's
    top level can.
    """
    problems: list[str] = []
    for source_file in sorted(root.rglob("*.js")):
        parts = set(source_file.parts)
        if "node_modules" in parts or "miniprogram_npm" in parts:
            continue
        try:
            text = source_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        text = _MP_BLOCK_COMMENT_RE.sub("", text)
        text = _MP_LINE_COMMENT_RE.sub("", text)
        seen: dict[str, int] = {}
        for match in _MP_TOPLEVEL_DECL.finditer(text):
            name = match.group(1)
            seen[name] = seen.get(name, 0) + 1
        for name, count in seen.items():
            if count > 1:
                problems.append(
                    f"{source_file.relative_to(repo)} declares {name!r} "
                    f"{count} times at the top level — legal in a plain node "
                    "script, a SyntaxError under the module semantics the "
                    "小程序 toolchain compiles with, so the module never "
                    "evaluates and every page importing it renders blank"
                )
    return problems


def _mp_require_problems(root: Path, repo: Path, entries: list[Path]) -> list[str]:
    """Walk relative require()/import chains from every entry file.

    The lesson is avs-studio-3 (2026-08-01): the builder wrote
    utils/telemetry.js OUTSIDE miniprogramRoot and three pages imported
    it via inconsistent relative paths. A module that throws at require
    time means Page() never registers — a blank page — and this gate
    passed 7/7 because every page FILE existed. Both failure shapes are
    mechanically detectable with no DevTools and no LLM: a spec that
    resolves to nothing, and a spec that resolves outside the root
    DevTools packages.
    """
    problems: list[str] = []
    seen: set[Path] = set()
    queue = [entry for entry in entries if entry.is_file()]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            source = current.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = current.relative_to(repo)
        for spec in _mp_module_specs(source):
            target = _mp_resolve(spec, current, root)
            if not target.is_relative_to(root):
                problems.append(
                    f"{rel} requires {spec!r}, which resolves outside the "
                    f"mini-program root — DevTools only packages files under "
                    f"{root.relative_to(repo)}/, so the module is missing at "
                    "runtime and the page goes blank"
                )
                continue
            candidates = (
                [target] if target.suffix == ".js"
                else [Path(str(target) + ".js"), target / "index.js"]
            )
            hit = next((c for c in candidates if c.is_file()), None)
            if hit is None:
                problems.append(
                    f"{rel} requires {spec!r}, which does not resolve to any "
                    "file — the page throws at require time, Page() never "
                    "registers, and the page renders blank"
                )
                continue
            queue.append(hit)
    return problems


def _miniprogram_gate(repo: Path) -> str | None:
    """Gate U4 for 小程序: the product must LOAD, not just pass its suite.

    The web profile has had `_boot_gate` since product-bench run 4, where
    every built task passed its tests and then failed every probe because
    the server never listened. The mini-program profile never got the
    equivalent, and it cost exactly the same way: a run built nine modules
    and seven page directories, all green, with no app.json and no app.js —
    so WeChat DevTools could not open the project at all and not one page
    was reachable.

    Static rather than runtime, because DevTools is a desktop application
    that cannot be driven headlessly in CI. Returns feedback text on
    failure, None when the project would load.
    """
    import json

    # _miniprogram_root resolves a declared miniprogramRoot (symlinks and
    # all — /tmp vs /private/tmp on macOS); repo must match or every
    # relative_to below is a ValueError.
    repo = repo.resolve()
    root = _miniprogram_root(repo)
    pages_dir = root / "pages"
    if not pages_dir.is_dir() and not (root / "app.json").exists():
        return None  # nothing built here yet — not this gate's business

    problems: list[str] = []
    app_json = root / "app.json"
    registered: list[str] = []
    if not app_json.exists():
        problems.append(f"{app_json.relative_to(repo)} is missing entirely")
    else:
        try:
            data = json.loads(app_json.read_text(encoding="utf-8"))
        except ValueError as exc:
            return (
                f"MINI-PROGRAM GATE: {app_json.relative_to(repo)} is not valid "
                f"JSON ({exc}) — DevTools refuses the project. " + _MP_CONTRACT_HINT
            )
        registered = [str(p) for p in (data.get("pages") or [])]
        if not registered:
            problems.append("app.json has no `pages` — nothing would launch")
        for page in registered:
            for suffix in (".js", ".wxml"):
                if not (root / f"{page}{suffix}").exists():
                    problems.append(
                        f"app.json registers {page!r} but {page}{suffix} does not exist"
                    )
    if not (root / "app.js").exists():
        problems.append(f"{(root / 'app.js').relative_to(repo)} is missing")

    # Pages built but never registered: the founder cannot reach them. This
    # is the mini-program's version of the wireup check.
    on_disk = sorted(
        f"pages/{path.parent.name}/{path.stem}"
        for path in pages_dir.glob("*/*.wxml")
    ) if pages_dir.is_dir() else []
    unreachable = [p for p in on_disk if p not in registered]
    if unreachable:
        problems.append(
            "these pages exist but are not in app.json `pages`, so nothing "
            "can open them: " + ", ".join(unreachable[:8])
        )

    # A page whose file exists can still be blank: a require() that does
    # not resolve throws before Page() runs (avs-studio-3 lost 3 of 7
    # pages this way while this gate said 7/7).
    entries = [root / "app.js"] + [root / f"{page}.js" for page in registered]
    problems.extend(_mp_require_problems(root, repo, entries))
    problems.extend(_mp_duplicate_declaration_problems(root, repo))

    if not problems:
        return None
    return (
        "MINI-PROGRAM GATE — the project would not load in WeChat DevTools:\n"
        + "\n".join(f"- {p}" for p in problems[:8])
        + "\n" + _MP_CONTRACT_HINT
    )


def _preserve_failed_attempt(repo: Path, slug: str, source: Path | None = None) -> str:
    """Failure forensics that survives cleanup: copy the dirty tree to
    .mas/failed-builds/<slug>. Before this, 'worktree left for inspection'
    was untrue — worktrees are force-removed, and the run-4 postmortem had
    to reconstruct failed attempts from workspace residue."""
    import shutil

    keep = repo / ".mas" / "failed-builds" / slug
    shutil.rmtree(keep, ignore_errors=True)
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source or repo,
        keep,
        ignore=shutil.ignore_patterns(
            ".git", ".mas", ".venv", "node_modules", "__pycache__", ".pytest_cache"
        ),
    )
    return str(keep.relative_to(repo))


def _reset_workspace(repo: Path, pre_existing: set[str]) -> None:
    """In-place build failed: drop every file the attempt created and
    restore tracked modifications. Uncommitted residue otherwise leaks into
    sibling tasks, later stages, and the probes (run 4 left 724 inserted
    lines dirtying the case-03 workspace the probes then measured)."""
    for path in sorted(repo.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if ".git" in path.parts or ".mas" in path.parts:
            continue
        if path.is_file():
            if str(path.relative_to(repo)) not in pre_existing:
                path.unlink(missing_ok=True)
        else:
            try:
                path.rmdir()  # only succeeds once emptied — intended
            except OSError:
                pass
    subprocess.run(
        [resolve("git"), "checkout", "--", "."], cwd=repo, capture_output=True, timeout=60
    )


def _removed_names(old: str, new: str) -> set[str]:
    """Top-level names a rewrite dropped. Support modules under tests/
    are a shared vocabulary — sibling tasks' committed tests import these
    names, so removal breaks their collection (run 7, case 04: a conftest
    rewrite lost post_json/create_candidate and every iteration died on
    ImportError). Unparseable code returns empty — the suite gate judges it."""
    old_names, new_names = _toplevel_names(old), _toplevel_names(new)
    if old_names is None or new_names is None:
        return set()
    # Privates are not vocabulary: nothing outside the module should import
    # a _name, and guarding them blocks legitimate internal restructuring
    # (run 8, case 01 t3: a helpers rewrite adding the public name its tests
    # needed was droppable purely over reshuffled _check_url/_do).
    return {n for n in (old_names - new_names) if not n.startswith("_")}


def _toplevel_names(src: str) -> set[str] | None:
    import ast

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    return {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    } | {
        t.id for n in tree.body if isinstance(n, ast.Assign)
        for t in n.targets if isinstance(t, ast.Name)
    }


def _stale_import_note(repo: Path) -> str:
    """Cross-iteration drift detector. Files persist between build
    iterations, so a vocabulary change mid-build leaves earlier files
    importing names that never existed — collection dies before any test
    runs (run 8, case 01 t3: half the tests imported http_post, half
    post). A deterministic scan names the exact files and the available
    names; returns '' when clean."""
    import ast

    problems: list[str] = []
    seen: set[Path] = set()
    for path in sorted(set(repo.glob("tests/**/*.py")) | set(repo.glob("tests/*.py"))):
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            if not (node.module.startswith("tests") or node.module in ("conftest", "helpers")):
                continue
            target = repo / (node.module.replace(".", "/") + ".py")
            if not target.is_file():
                continue
            available = _toplevel_names(
                target.read_text(encoding="utf-8", errors="replace")
            )
            if available is None:
                continue
            for alias in node.names:
                if alias.name != "*" and alias.name not in available:
                    public = ", ".join(sorted(n for n in available if not n.startswith("_"))[:8])
                    problems.append(
                        f"- {path.relative_to(repo)}: `from {node.module} import "
                        f"{alias.name}` — {target.relative_to(repo)} defines: {public or '(nothing public)'}"
                    )
    if not problems:
        return ""
    return (
        "STALE IMPORTS — these files (yours, possibly from an earlier "
        "iteration; files persist between iterations) import names that do "
        "not exist:\n" + "\n".join(problems[:5]) + "\n"
        "Resubmit those files to use the existing names, or add the missing "
        "names additively to the module they import from."
    )


def _why_no_files(raw: str, kept: list[str]) -> str:
    """The distinguishing evidence for an empty submission, as a suffix.

    `kept` non-empty means the batch WAS files and every one of them was
    discarded (weakened skeletons, dropped shared names) — a different bug from
    a model that emitted `files: []` or narrated instead. Without this the three
    read identically in the outcome record."""
    if kept:
        return " — every file was discarded: " + "; ".join(kept[:3])
    opening = " ".join(raw.split())[:200]
    return f" — response began: {opening!r}" if opening else " — response was empty"


# Languages a profile cannot run. Prose could not carry this: the
# mini-program stack_hint has said "a .py test file in a 小程序 project is
# always wrong" since v0.63, and a spec still came back with
# tests/test_catalog_page.py — which made the build gate demand a passing
# pytest run and killed the task three iterations later on "pytest
# collected no tests", a message about the symptom and not the mistake.
# WeChat runs JavaScript; there is no Python runtime anywhere in the
# product, so a .py file is not a style preference to argue with.
_PROFILE_FOREIGN_SUFFIXES: dict[str, dict[str, str]] = {
    "miniprogram": {
        ".py": "WeChat 小程序 run JavaScript — nothing in this product can "
               "execute Python. Write the tests as *.test.js under "
               "miniprogram/utils/ (node --test) or with miniprogram-simulate."
    },
}


def foreign_language_issue(profile: str, rel: str) -> str | None:
    """The file's language against the profile's runtime, or None."""
    reason = _PROFILE_FOREIGN_SUFFIXES.get(profile, {}).get(Path(rel).suffix)
    return f"{rel!r} cannot run in a {profile} product: {reason}" if reason else None


def _workspace_profile(repo: Path) -> str:
    try:
        return load_project(repo).profile
    except (FileNotFoundError, KeyError, ValueError):
        return ""  # not an avs workspace — no profile to enforce


def _write_files(
    repo: Path, files: list[dict], *, allowed_test_paths: set[str] | None = None
) -> tuple[list[str], list[str]]:
    """Two-pass: validate EVERY file first, then write — a refusal never
    leaves partial state behind. Returns (written, kept) where kept are
    skeleton files the implementer tried to weaken: the on-disk skeleton
    WINS silently-but-visibly instead of failing the batch — real models
    reword their own skeleton tests every attempt (bench run 3, 5/8 tasks
    dead on this wall), and a refusal loop never converges."""
    validated: list[tuple[str, str]] = []
    kept: list[str] = []
    profile = _workspace_profile(repo)
    batch = list(files[:_MAX_FILES])
    # The weakening guard below judges the BATCH, not one file at a time: an
    # assert hoisted out of a test and into a shared helper written in the
    # same response has moved, not vanished. Keyed by path so a file is never
    # compared against its own new body.
    batch_contents = {
        str(f["path"]).lstrip("/"): str(f["new_content"])
        for f in batch
        if isinstance(f, dict) and f.get("path") and "new_content" in f
    }
    for f in batch:
        # A malformed entry must surface as ValueError — the build loop's
        # feedback channel — not KeyError, which escapes it and kills the
        # whole run (product-bench run 4, case 01: one entry without
        # new_content zeroed the case).
        if not isinstance(f, dict) or not f.get("path") or "new_content" not in f:
            raise ValueError(
                "malformed file entry (every entry needs 'path' and COMPLETE "
                f"'new_content'): {str(f)[:120]!r}"
            )
        rel = str(f["path"]).lstrip("/")
        if any(rel.startswith(p) for p in _FORBIDDEN_PREFIXES) or ".." in rel:
            raise ValueError(f"implementer touched forbidden path {rel!r}")
        foreign = foreign_language_issue(profile, rel)
        if foreign:
            # A wall, not a hint. The profile has said this in prose since
            # v0.63 and a spec still came back with a .py test file, whose
            # only visible consequence three iterations later was "pytest
            # collected no tests" — a message about the symptom.
            raise ValueError(f"wrong language for this product: {foreign}")
        # §13.29.5 write-lock: existing non-skeleton TEST files are
        # read-only to the implementer. A blocking test is either its bug
        # or a spec gap — never a test to edit. (Reward-hacking defense,
        # structural.) Support modules under tests/ (helpers, conftest,
        # __init__) are NOT walled — feature tasks legitimately extend
        # shared fixtures (run 3: t3 died here) — but they pass through
        # the same weakening guard below, so gutting a helper's asserts
        # still gets the file dropped, and sabotaged fixtures fail the
        # other specs' tests in the suite gate.
        is_test = rel.startswith("tests/") or Path(rel).name.startswith("test_")
        if (
            Path(rel).name.startswith("test_")
            and (repo / rel).exists()
            and allowed_test_paths is not None
            and rel not in allowed_test_paths
        ):
            raise ValueError(
                f"implementer tried to modify existing test {rel!r} — existing "
                "tests are read-only (fix the code, or the spec is wrong)"
            )
        content = str(f["new_content"])
        if is_test and (repo / rel).exists():
            # Its own skeleton surface is rewritable, but never WEAKENABLE:
            # assertion_delta rejects removed asserts / added skips citing
            # the exact node (§13.29.5). The rejection drops THIS file only —
            # the skeleton stays the wall; the rest of the batch proceeds.
            from ai_venture_studio.tools.integrity import assertion_delta

            weakened = assertion_delta(
                (repo / rel).read_text(encoding="utf-8", errors="replace"),
                content,
                elsewhere=[c for p, c in batch_contents.items() if p != rel],
            )
            if weakened:
                kept.append(
                    f"{rel} (skeleton kept — your version dropped: "
                    + "; ".join(f"{c.change}: {c.node[:80]}" for c in weakened[:3])
                    + ")"
                )
                continue
            if not Path(rel).name.startswith("test_"):
                # conftest/helpers are additive-only: dropped names break
                # sibling tasks' imports.
                lost = _removed_names(
                    (repo / rel).read_text(encoding="utf-8", errors="replace"),
                    content,
                )
                if lost:
                    kept.append(
                        f"{rel} (existing version kept — your rewrite removed "
                        f"names committed tests import: {', '.join(sorted(lost)[:6])}. "
                        "Extend this file additively; never rename or drop.)"
                    )
                    continue
        if len(content.splitlines()) > _MAX_FILE_LINES:
            raise ValueError(f"{rel} exceeds {_MAX_FILE_LINES} lines")
        validated.append((rel, content))

    written = []
    for rel, content in validated:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    return written, kept


def _append_design_memory(repo: Path, spec: Spec, files: list[str]) -> None:
    """product/design.md — the evolving architecture the Spec stage reads
    back, so feature N+1 extends the design instead of re-deriving it."""
    path = repo / "product" / "design.md"
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        path.write_text("# Architecture (evolving — appended per build)\n", encoding="utf-8")
    entry = (
        f"\n## {spec.title} ({spec.slug})\n\n{spec.design.strip()}\n\n"
        f"files: {', '.join(f for f in files if not f.startswith('tests/'))}\n"
    )
    path.write_text(path.read_text(encoding="utf-8") + entry, encoding="utf-8")


def _write_changelog_fragment(repo: Path, spec: Spec, files: list[str]) -> None:
    directory = repo / "product" / "changelog"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{spec.slug}.md").write_text(
        f"**{spec.title}** — {len(spec.criteria)} acceptance criteria, "
        f"{len(files)} file(s). User-visible: {spec.criteria[0] if spec.criteria else spec.title}\n",
        encoding="utf-8",
    )


def _file_tree(repo: Path, cap: int = 200) -> str:
    lines = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo)
        if any(part in (".git", ".mas", "__pycache__", "node_modules", ".venv") for part in rel.parts):
            continue
        lines.append(str(rel))
        if len(lines) >= cap:
            lines.append("… (truncated)")
            break
    return "\n".join(lines)


def _related_sources(repo: Path, spec: Spec, cap_files: int = 6, cap_lines: int = 200) -> str:
    """Existing files the spec's design mentions — the implementer extends
    the product, it does not recreate it (feature-FDR awareness)."""
    mentioned = re.findall(r"[\w/]+\.(?:py|js|ts|wxml|wxss|json|html)", spec.design)
    blocks = []
    for rel in dict.fromkeys(mentioned):
        path = repo / rel
        if path.is_file():
            text = "\n".join(
                path.read_text(encoding="utf-8", errors="replace").splitlines()[:cap_lines]
            )
            blocks.append(f'<existing_file path="{rel}">\n{text}\n</existing_file>')
        if len(blocks) >= cap_files:
            break
    return "\n\n".join(blocks)


def data_gate_blockers(repo: Path, profile: str) -> list[str]:
    """§18.48.1: the data profile's build gate also runs the workspace's
    declared/detected external checks (dbt, contracts, DAG imports).
    findings/error block the commit; `skipped` (tool not installed, nothing
    configured) is visible in the check output but does not block —
    availability-gating, not silent absence."""
    if profile != "data":
        return []
    from ai_venture_studio.adoption.data_tools import run_data_checks

    return [
        f"{r.slot} ({r.detail})"
        for r in run_data_checks(repo)
        if r.status in ("findings", "error")
    ]


def finalize_build_bookkeeping(repo_dir: str | Path, slug: str, files: list[str]) -> None:
    """Post-build records: spec frozen, design memory, changelog, actuals.
    Split out so parallel worktree builds can run it after their merge."""
    repo = Path(repo_dir).resolve()
    spec = load_spec(repo, slug)
    spec.built = True
    from ai_venture_studio.upstream.spec import _save as _save_spec

    _save_spec(repo, spec)
    _append_design_memory(repo, spec, files)
    _write_changelog_fragment(repo, spec, files)
    # The ledger follows the specs (ADR-045). Derived here rather than at
    # spec-write time because this is the one place a criterion's status
    # actually changes, and a ledger that only ever says "proposed" is a
    # list of intentions rather than of promises the product keeps.
    from ai_venture_studio.upstream.requirements import sync_ledger

    sync_ledger(repo)


def run_build(
    repo_dir: str | Path,
    slug: str,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    in_branch: bool = False,
    task_lane: str = "core",
    task_estimate_hours: float = 0.0,
    source_contract: str = "",
    prior_failure: str = "",
) -> BuildResult:
    """in_branch=True: build in an isolated worktree on branch
    build/<slug> (parallel-lane mode) — the caller merges and then calls
    finalize_build_bookkeeping. Default: build in place, all-inclusive."""
    started = time.monotonic()
    repo = Path(repo_dir).resolve()

    # Persist the previous task's spend before this one starts: run_build is
    # called once per task, so flushing here keeps the ledger current
    # task-by-task instead of only at the end of a seven-task run. Measured
    # and reported, never gated — budget enforcement belongs to the provider
    # account that does the billing (ADR-032).
    from ai_venture_studio import spend

    spend.flush(repo)

    if in_branch:
        import tempfile

        worktree = Path(tempfile.mkdtemp(prefix=f"autoproduct-lane-{slug[:16]}-"))
        added = _run(
            [resolve("git"), "worktree", "add", "-B", f"build/{slug}", str(worktree), "HEAD"], repo
        )
        if added.returncode != 0:
            return BuildResult(slug=slug, status="error", detail=added.stderr[:300])
        # .mas/ is gitignored config, not history — the lane worktree needs
        # the project + services config to build.
        (worktree / ".mas").mkdir(exist_ok=True)
        for config in ("project.yaml", "services.yaml"):
            source = repo / ".mas" / config
            if source.exists():
                (worktree / ".mas" / config).write_text(
                    source.read_text(encoding="utf-8"), encoding="utf-8"
                )
        result: BuildResult | None = None
        try:
            result = _run_build_inner(
                worktree, slug, provider=provider, model=model, started=started,
                bookkeeping=False, task_lane=task_lane,
                task_estimate_hours=task_estimate_hours,
                source_contract=source_contract, prior_failure=prior_failure,
            )
            result.detail = (result.detail + " " if result.detail else "") + f"branch build/{slug}"
            return result
        finally:
            import shutil as _shutil

            if result is not None and result.status != "built":
                preserved = _preserve_failed_attempt(repo, slug, source=worktree)
                result.detail = (
                    (result.detail + " " if result.detail else "")
                    + f"(failed attempt preserved at {preserved})"
                )
            _run([resolve("git"), "worktree", "remove", "--force", str(worktree)], repo)
            _shutil.rmtree(worktree, ignore_errors=True)
    return _run_build_inner(
        repo, slug, provider=provider, model=model, started=started,
        bookkeeping=True, task_lane=task_lane,
        task_estimate_hours=task_estimate_hours,
        source_contract=source_contract, prior_failure=prior_failure,
    )


def _task_manifest(repo: Path, slug: str, spec: Spec):
    """Assemble and record the task's Context Manifest *before* the prompt is
    built, so the code neighborhoods it collects can actually reach the
    writer. Returns (manifest, "") or (None, blocking_detail).

    This ordering is the fix for a real defect: the manifest used to be
    assembled after the prompt purely to audit it, so `files_expected` code
    was hashed, ranked, persisted — and never shown to the implementer, which
    is how sibling tasks drifted apart (one task's route was /tasks while its
    neighbour's spec said /api/tasks).

    Overflow is Planning's problem, not a compression exercise: a task whose
    required context does not fit is reported as such rather than silently
    trimmed (§13.29.3).
    """
    from ai_venture_studio.upstream.context_assembler import (
        ContextOverflow,
        assemble,
        persist_manifest,
    )

    files_expected: list[str] = []
    try:
        from ai_venture_studio.upstream.plan import TASK_MARKER, load_plan

        marker = TASK_MARKER.search(spec.request or "")
        if marker:
            task = next(
                (t for t in load_plan(repo).tasks if t.id == marker.group(1)), None
            )
            files_expected = list(task.files_expected) if task else []
    except (FileNotFoundError, ValueError):
        files_expected = []  # standalone spec (no plan): code stays optional

    try:
        manifest = assemble(
            repo, slug, task_id=slug, files_expected=files_expected
        )
    except ContextOverflow as exc:
        return None, (
            f"{exc.code}: {exc} — split proposal: {'; '.join(exc.split_proposal)}"
        )
    persist_manifest(manifest, repo, slug)
    return manifest, ""


def _grounding_gate(repo: Path, slug: str, manifest, prompt: str) -> str:
    """Verify the required contract reached the prompt. Returns "" when
    clean, else the blocking detail."""
    from ai_venture_studio.upstream.context_assembler import (
        verify_prompt_grounding,
    )

    violations = verify_prompt_grounding(manifest, prompt)
    if not violations:
        return ""
    detail = "; ".join(f"{v.path} ({v.rule})" for v in violations[:4])
    return (
        f"grounding violation: required context missing from the implementer's "
        f"prompt — {detail}. The artifact would be authored blind to a contract "
        f"a later gate enforces; manifest recorded at .mas/manifests/{slug}.yaml"
    )


def _module_spec_context(repo: Path) -> str:
    """The invariants Code Review will hold this change to, verbatim enough
    that the grounding probe finds them."""
    from ai_venture_studio.module_specs import ModuleSpecError, load_module_specs

    try:
        specs = load_module_specs(repo / ".mas")
    except (ModuleSpecError, OSError):
        return ""  # a malformed module spec is the review stage's finding
    blocks = []
    for spec in specs:
        lines = [f"module {spec.module} (owns {', '.join(spec.paths)})"]
        if spec.invariants:
            # Invariants are quoted VERBATIM, one per line, with no inserted
            # label: the grounding probe requires the contract text itself to
            # be present, and paraphrasing a contract into a prompt is the
            # smell that check exists to catch.
            lines.append("invariants (these must hold after your change):")
            lines += [f"- {i}" for i in spec.invariants]
        if spec.forbidden_side_effects:
            lines.append("forbidden side effects (regex, scanned in your diff):")
            lines += [f"- {f}" for f in spec.forbidden_side_effects]
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _approval_drift(repo: Path, slug: str, spec: Spec) -> str:
    """§13.35.5 detector: has the approved contract moved since Gate U3?

    Empty string when clean. Specs approved before v0.41 carry no hash and
    are treated as clean — a missing receipt is not evidence of drift, and
    silently blocking every pre-existing spec would be its own bug.
    """
    from ai_venture_studio.upstream.spec import contract_hash

    if not spec.approved_hash:
        return ""
    current = contract_hash(spec)
    if current == spec.approved_hash:
        return ""
    return f"contract hash {spec.approved_hash[:12]} → {current[:12]}"


def _run_build_inner(
    repo: Path,
    slug: str,
    *,
    provider: str,
    model: str,
    started: float,
    bookkeeping: bool,
    task_lane: str = "core",
    task_estimate_hours: float = 0.0,
    source_contract: str = "",
    prior_failure: str = "",
) -> BuildResult:
    project = load_project(repo)
    if not source_contract:
        # Same fallback as the spec stage: the workspace FDR is the
        # founder's literal contract. The live post-fix test showed drift
        # migrating to the implementer once specs held (scores handler
        # invented "index" for the FDR's "item" — every probe died on it).
        fdr_file = repo / "FDR.md"
        if fdr_file.exists():
            source_contract = fdr_file.read_text(encoding="utf-8")
    spec: Spec = load_spec(repo, slug)
    if spec.status != "approved":
        return BuildResult(
            slug=slug,
            status="error",
            detail=f"spec status is {spec.status!r} — Gate U3 requires "
            f"`avs spec-approve {slug}` first",
        )

    # §13.35.5: the approval covered a specific bundle. If the spec moved
    # since it was approved, a human edited a frozen artifact mid-flight;
    # the run blocks and a retro-SCR ratifies or reverts. It does not fight
    # the human, and it does not build the fork either.
    drift = _approval_drift(repo, slug, spec)
    if drift:
        return BuildResult(
            slug=slug, status="error",
            detail=f"spec changed outside SCR since Gate U3 approval ({drift}) "
            f"— ratify with `avs scr {slug} \"<reason>\"` + "
            "`scr-approve`, or revert the edit; refusing to build an "
            "unratified fork",
        )

    provider_impl = get_provider(provider)
    claude_md = repo / "CLAUDE.md"
    constraints = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""

    # Assemble the manifest FIRST so its code neighborhoods reach the writer.
    # Before this, the manifest was built after the prompt purely to audit it,
    # and the implementer's only view of existing code was a regex over the
    # spec's own design prose — so a task whose design text happened not to
    # name a file was written blind to the product it was extending.
    manifest, manifest_error = _task_manifest(repo, slug, spec)
    if manifest_error:
        return BuildResult(slug=slug, status="error", detail=manifest_error)

    existing = _related_sources(repo, spec)
    from ai_venture_studio.upstream.context_assembler import render_manifest

    # `skip` the paths _related_sources already rendered: the same file under
    # two headings reads as two files to the model.
    already = set(re.findall(r'<existing_file path="([^"]+)"', existing))
    manifest_code, _receipts = render_manifest(
        manifest, repo, kinds={"code"}, tag="existing_file", skip=already
    )
    if manifest_code:
        existing = (existing + "\n\n" if existing else "") + manifest_code
    fixture_blocks = []
    for rel in ["conftest.py", "tests/conftest.py"] + sorted(
        str(q.relative_to(repo)) for q in repo.glob("tests/helpers*.py")
    ):
        path = repo / rel
        if path.is_file():
            body = "\n".join(
                path.read_text(encoding="utf-8", errors="replace").splitlines()[:200]
            )
            fixture_blocks.append(f'<existing_file path="{rel}">\n{body}\n</existing_file>')
    if fixture_blocks:
        # The vocabulary saga's root cause: _related_sources only surfaces
        # files the spec's design text happens to mention, so implementers
        # never saw the committed fixtures and re-invented them blindly
        # (http/http_post/base_url across runs 7-9). Always show them.
        existing = (existing + "\n\n" if existing else "") + (
            '<shared_test_fixtures note="these already exist — USE these '
            'fixture and helper names in your tests; extend additively, '
            'never re-invent">\n' + "\n\n".join(fixture_blocks)
            + "\n</shared_test_fixtures>"
        )
    from ai_venture_studio.upstream.blocks import blocks_context
    from ai_venture_studio.upstream.provisioning import services_context

    services = services_context(repo)
    blocks = blocks_context(
        project.profile, f"{spec.title} {spec.design} {' '.join(spec.criteria)}"
    )
    if blocks:
        existing = (existing + "\n\n" if existing else "") + blocks
    # Module-spec invariants belong in the implementer's prompt, not only in
    # the reviewer's: Code Review enforces `.mas/specs/*.spec.yaml` and flags
    # SPEC_DRIFT_UNDOCUMENTED, so an implementer that never saw the
    # invariants is being asked to satisfy a contract it was not shown. The
    # grounding check below is what surfaced this.
    module_block = _module_spec_context(repo)
    base_user = (
        f"<constraints>\n{constraints}\n</constraints>\n\n"
        + (f"<module_invariants>\n{module_block}\n</module_invariants>\n\n"
           if module_block else "")
        + (f"<services>\n{services}\n</services>\n\n" if services else "")
        + f"<repo_tree>\n{_file_tree(repo)}\n</repo_tree>\n\n"
        + (f"{existing}\n\n" if existing else "")
        + "You are EXTENDING the existing product above — integrate with it, "
        "never recreate it. Existing test files are read-only to you. Each "
        "skeleton below is marked with whether its file exists on disk yet: "
        "a skeleton NOT on disk is YOURS TO WRITE as a real test file — "
        "writing only source files fails the gate with 'collected no "
        "tests'. A test file already on disk is a wall: do not resubmit it "
        "(a version missing any existing assert is silently discarded and "
        "the on-disk file kept) — write the SOURCE files that make it "
        "pass, plus any NEW test files.\n\n"
        + (f"<source_contract>\n{source_contract[:3000]}\n</source_contract>\n\n"
           if source_contract.strip() else "")
        + f"<spec>\n{yaml.safe_dump(spec.model_dump(include={'title', 'design', 'criteria'}), sort_keys=False, allow_unicode=True)}"
        # Truth per path, checked against the disk, because for sixteen
        # versions this prompt asserted the skeletons "already exist ON
        # DISK" while nothing ever wrote them — the spec stores only
        # path/purpose/covers. The system prompt said "write them as real
        # tests"; this line forbade exactly that, and which instruction the
        # model obeyed decided the task: bench run 19's 04-t1/t2 obeyed
        # this one, submitted source only, and died on 'pytest collected
        # no tests' three attempts straight — with the feedback naming the
        # symptom and the prompt still forbidding the fix.
        f"test_skeletons:\n"
        + "\n".join(
            f"- {s.path}: {s.purpose} (covers {s.covers}) "
            + ("[on disk — read-only wall]" if (repo / s.path).exists()
               else "[NOT on disk — write this file]")
            for s in spec.test_skeletons
        )
        + "\n</spec>"
        # A retried task's implementer must know what killed the previous
        # attempt, or it walks into the same wall: the run-3 forensics showed
        # a retry re-inventing the exact phantom import its predecessor died
        # on, because nothing carried the diagnosis across attempts.
        + (
            "\n\n<previous_attempt_failed note=\"an earlier attempt at this "
            "exact task failed for the reason below — diagnose it and take a "
            "different approach; repeating it will fail the same way\">\n"
            + prior_failure.strip()[:1500]
            + "\n</previous_attempt_failed>"
            if prior_failure.strip()
            else ""
        )
    )
    # Grounding gate (§13.25.2): the contract this task will be judged
    # against must actually be in the prompt. A required manifest entry
    # whose content never reached the writer is a contract violation
    # (§11.18.3) — unrecoverable, not a quality note — because the artifact
    # would be authored blind to something a later gate enforces.
    grounding = _grounding_gate(repo, slug, manifest, base_user)
    if grounding:
        return BuildResult(slug=slug, status="error", detail=grounding)

    allowed_tests = {s.path for s in spec.test_skeletons}
    pre_existing = {
        str(p.relative_to(repo))
        for p in repo.rglob("*")
        if p.is_file() and ".git" not in p.parts and ".mas" not in p.parts
    }

    feedback = ""
    written: list[str] = []
    report = None
    for iteration in range(1, MAX_ITERATIONS + 1):
        progress.step(
            repo, slug, "build", f"writing the code (attempt {iteration}/{MAX_ITERATIONS})"
        )
        raw = provider_impl.complete(
            model=model,
            system=_SYSTEM,
            user=base_user
            + (f"\n\n<test_failure>\n{feedback}\n</test_failure>" if feedback else ""),
            max_tokens=_IMPLEMENTER_MAX_TOKENS,
        )
        if last_response_truncated():
            # A response that ran out of output budget is a PARTIAL file set,
            # and partial YAML usually still parses: the last file's block
            # scalar simply ends. Writing it produced a half-finished source
            # file on disk whose real failure showed up two iterations later as
            # an unrelated test error, with nothing anywhere naming the cause.
            # So: never write a truncated batch, and say so.
            progress.step(
                repo, slug, "build",
                f"response truncated at the output cap (attempt {iteration}) — "
                "asking for fewer files",
            )
            if iteration == MAX_ITERATIONS:
                detail = (
                    f"implementer response truncated at the {_IMPLEMENTER_MAX_TOKENS}-token "
                    f"output cap on every attempt ({len(raw)} chars received); nothing "
                    "written. This task asks for more code than one response can carry — "
                    "split it into smaller tasks"
                )
                if bookkeeping and written:
                    preserved = _preserve_failed_attempt(repo, slug)
                    _reset_workspace(repo, pre_existing)
                    detail += f" (failed attempt preserved at {preserved}; workspace reset)"
                return BuildResult(
                    slug=slug, status="error", iterations=iteration, detail=detail
                )
            feedback = (
                "YOUR LAST RESPONSE WAS CUT OFF at the output limit, so none of "
                "it was used. Return FEWER files this time — the smallest set "
                "that makes the skeleton tests pass — and keep every file "
                "complete. Never abbreviate a file body or write a placeholder "
                "comment in place of code."
            )
            continue
        try:
            data = extract_mapping(raw, ("files",))
            written, kept = _write_files(
                repo, data.get("files") or [], allowed_test_paths=allowed_tests
            )
        except ValueError as exc:
            # A refused write (read-only test, weakened assert, unparseable
            # output) is FEEDBACK, not a fatal error — real implementers
            # routinely re-emit existing test files, and the instant-error
            # version collapsed both real bench cases to 1/7 tasks built.
            # Structural walls stay: nothing was written; the model gets
            # the wall's text and a bounded retry.
            if iteration == MAX_ITERATIONS:
                detail = str(exc)
                if bookkeeping and written:
                    # Earlier iterations already wrote into the shared
                    # workspace — same residue rule as the failure returns.
                    preserved = _preserve_failed_attempt(repo, slug)
                    _reset_workspace(repo, pre_existing)
                    detail += f" (failed attempt preserved at {preserved}; workspace reset)"
                return BuildResult(slug=slug, status="error", iterations=iteration, detail=detail)
            feedback = (
                f"WRITE REFUSED: {exc}. Resubmit ALL your files WITHOUT the "
                "refused change — existing tests are read-only walls, not "
                "suggestions."
            )
            continue
        if not written:
            if iteration == MAX_ITERATIONS:
                # "implementer returned no files" was the whole story this
                # failure told, three times across the bench runs, and it
                # names a symptom with at least three different causes: an
                # empty `files:` list, a batch entirely discarded as weakened
                # skeletons, or a model that narrated instead of answering.
                # The voter seat already preserves the response opening for
                # exactly this reason (voters/base.py); the implementer now
                # does too.
                detail = f"implementer returned no files{_why_no_files(raw, kept)}"
                if bookkeeping:
                    preserved = _preserve_failed_attempt(repo, slug)
                    _reset_workspace(repo, pre_existing)
                    detail += f" (failed attempt preserved at {preserved}; workspace reset)"
                return BuildResult(
                    slug=slug, status="error", iterations=iteration,
                    detail=detail,
                )
            feedback = (
                "every file you returned was a weakened skeleton test and was "
                "discarded: " + "; ".join(kept) + ". Return the SOURCE files."
                if kept
                else "you returned no files; return the complete file set"
            )
            continue
        from ai_venture_studio.testing import combine_reports, run_js_tests

        progress.step(
            repo, slug, "build",
            f"running your tests ({len(written)} file(s) written)",
        )
        # Before the suite, not after: an import-time serve call makes the
        # run hang for the full timeout and report nothing useful, and this
        # answers it from a parse.
        # Every profile, not just the two carrying the boot contract: any
        # Python module that blocks at import hangs its own suite, and only
        # miniprogram cannot execute Python at all (where the scan finds
        # nothing and costs nothing).
        import_failure = _blocks_on_import(repo, project.profile)
        if import_failure:
            progress.step(repo, slug, "build", "it would hang on import — fixing")
            feedback = import_failure
            continue
        report = combine_reports(_run_tests(repo), run_js_tests(repo))
        python_skeletons = any(s.path.endswith(".py") for s in spec.test_skeletons)
        if report.status == "passed" or (
            report.status in ("no_tests", "skipped") and not python_skeletons
        ):
            # skipped = JS tests exist but no node runtime; the skip is
            # visible in the report and review still judges the diff.
            if project.profile in ("web", "miniprogram"):
                progress.step(
                    repo, slug, "build",
                    "checking that it actually starts up" if project.profile == "web"
                    else "checking that the mini-program would load",
                )
            if project.profile == "web":
                boot_failure = _boot_gate(repo)
            elif project.profile == "miniprogram":
                # Same lesson as the web boot gate, ported: passing your own
                # tests is not the same as being loadable.
                boot_failure = _miniprogram_gate(repo)
            else:
                boot_failure = None
            if boot_failure is None:
                progress.step(repo, slug, "build", "tests pass — saving the code")
                break
            progress.step(repo, slug, "build", "it would not load — fixing")
            feedback = boot_failure
            continue
        feedback = report.detail or report.summary
        progress.step(
            repo, slug, "build",
            f"tests failed ({' '.join(report.summary.split())[:120]}) — fixing",
        )
        stale = _stale_import_note(repo)
        if stale:
            feedback += "\n\n" + stale
        if kept:
            feedback += (
                "\n\nNOTE — these skeleton test files were NOT replaced (your "
                "version removed existing asserts; the on-disk skeleton wins): "
                + "; ".join(kept)
                + ". Make the CODE pass the skeleton as written."
            )
    else:
        # The cause travels WITH the generic sentence. `test_summary` has always
        # held the real reason, but every consumer that reads only `detail` — the
        # bench result rows, the founder's report, outcomes.yaml — showed the
        # generic half alone, which is why "why does it always fail" had no
        # answer in the record.
        from ai_venture_studio.testing import salient_failure

        cause = salient_failure(feedback or "")
        detail = "build gate still failing after max iterations; nothing committed" + (
            f" — last failure: {cause}" if cause else ""
        )
        if bookkeeping:
            # bookkeeping=True means we built IN the shared workspace (the
            # worktree path preserves + discards in the run_build wrapper).
            preserved = _preserve_failed_attempt(repo, slug)
            _reset_workspace(repo, pre_existing)
            detail += f" (failed attempt preserved at {preserved}; workspace reset)"
        return BuildResult(
            slug=slug,
            status="build_failed",
            iterations=MAX_ITERATIONS,
            files_written=written,
            test_summary=(feedback or report.summary) if report else feedback,
            detail=detail,
        )

    data_blockers = data_gate_blockers(repo, project.profile)
    if data_blockers:
        detail = "data checks failed: " + "; ".join(data_blockers) + " — nothing committed"
        if bookkeeping:
            preserved = _preserve_failed_attempt(repo, slug)
            _reset_workspace(repo, pre_existing)
            detail += f" (failed attempt preserved at {preserved}; workspace reset)"
        return BuildResult(
            slug=slug,
            status="build_failed",
            iterations=iteration,
            files_written=written,
            test_summary=report.summary if report else "",
            detail=detail,
        )

    # BEFORE the commit, not after. `built: true` and the changelog fragment
    # used to be written once the commit already existed, which left them as
    # uncommitted working-tree changes — and two recovery paths discard those
    # wholesale: `git checkout -- .` when a fix iteration is rolled back, and
    # `_reset_workspace` after a failed build. A real run committed six
    # modules and kept the flag on two, so `built_task_ids` under-reported;
    # the founder's report headline read "2 of 6" over a finished product,
    # and — the expensive half — a resumed run would have rebuilt and
    # re-paid for four modules that were already built and committed.
    # Inside the task's own commit, no later rollback can take it.
    if bookkeeping:
        finalize_build_bookkeeping(repo, slug, written)
    subprocess.run([resolve("git"), "add", "-A"], cwd=repo, check=True)
    committed = subprocess.run(
        [resolve("git"), "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
         "commit", "-qm",
         f"feat({slug}): {spec.title}\n\nImplements spec {slug} (Gate U4 build "
         f"gate passed: {report.summary}). Review with: avs review HEAD~1"],
        cwd=repo, capture_output=True, text=True,
    )
    if committed.returncode != 0:
        return BuildResult(
            slug=slug, status="error", iterations=iteration,
            files_written=written, detail=committed.stderr[:300],
        )
    sha = subprocess.run(
        [resolve("git"), "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    try:
        from ai_venture_studio.upstream.plan import record_actual

        record_actual(
            repo, task_lane, task_estimate_hours or 1.0, time.monotonic() - started
        )
    except Exception as exc:  # noqa: BLE001 — bookkeeping never fails a build
        _log.debug("estimate-vs-actual not recorded for this task: %s", exc)

    from ai_venture_studio.tools.wireup import wireup_check

    wireup = wireup_check(repo)
    return BuildResult(
        slug=slug,
        status="built",
        iterations=iteration,
        files_written=written,
        modified_existing=sorted(set(written) & pre_existing),
        wireup_issues=[f.title for f in wireup.findings][:10],
        test_summary=report.summary,
        commit=sha,
    )
