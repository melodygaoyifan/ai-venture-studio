"""A helper around `subprocess` must not re-admit the bare executable name.

ADR-064 closed CLAUDE.md's oldest half-kept rule — "never a bare name in
argv[0]" — at 152 sites, and left two guards: ruff's `S607`, and
`test_every_subprocess_head_in_src_resolves_through_one_place` in
`test_the_gate_is_wider_now.py`. Both inspect a literal argv at a direct
`subprocess.*` call. Neither follows a helper, and inside a helper the head is
a parameter, which a linter cannot trace to the literal at the call site.

So 35 sites survived the conversion with both guards reporting clean, and the
one that says the most is `testing.py`'s `npm test`: `executables.py` opens by
naming that exact call as the reason it exists —

    This system runs `git` in a workspace it just built from model output, and
    it runs `npm install` in that same workspace — an entry earlier in `PATH`
    than `/usr/bin` turns every one of those into a call the run never
    intended.

— and the direct-call ratchet's own docstring names it too. It reached
`subprocess` through `_run_and_classify` and then `_run`, two hops, and was
invisible to the instrument written to catch it.

This is the guard for the shape the other two cannot see. It is a ratchet with
no ledger and no allowlist, because ADR-069 took the count to zero: any
non-empty result is a new one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from subprocess_wrappers import subprocess_wrappers, wrapped_bare_heads

REPO = Path(__file__).resolve().parent.parent


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = root / "src" / "ai_venture_studio" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def test_no_bare_executable_reaches_subprocess_through_a_wrapper():
    offenders = wrapped_bare_heads(REPO)
    assert not offenders, (
        "these call sites hand a bare executable name to a helper that passes "
        "it straight to subprocess, so PATH picks the binary:\n"
        + "\n".join(
            f"  {o['file']}:{o['line']}  {o['wrapper']}([{o['head']!r}, ...])"
            for o in offenders
        )
        + "\n\nRoute it through `ai_venture_studio.executables` — `resolve` "
        "when the run cannot proceed without it, `find` when absence is an "
        "expected answer and the caller reports `skipped`. Neither S607 nor "
        "the direct-call ratchet can see this shape; that is why it is here."
    )


def test_the_scan_still_recognises_the_wrappers_it_depends_on():
    """The anti-empty-measurement guard, and the one that actually earns its
    keep here.

    Every other assertion in this file passes just as well when the wrapper
    detection silently stops working — zero wrappers found means zero call
    sites checked means zero offenders reported, which is byte-identical to a
    clean tree. ADR-067 spent eleven instrument defects learning that, eight
    of which produced an empty measurement wearing a green tick.
    """
    wrappers = subprocess_wrappers(REPO)
    assert wrappers, (
        "the scan found no subprocess wrappers in src/ at all. Either every "
        "helper was inlined, or the detection broke — and a broken detection "
        "reports a clean sweep."
    )
    files = {rel for rel, _name in wrappers}
    assert any(f.endswith("/testing.py") for f in files), (
        "testing.py's `_run` is the wrapper ADR-069 was found through; if it "
        "is no longer recognised, this file is guarding nothing"
    )


def test_the_audit_finds_a_wrapped_bare_head(tmp_path):
    """The instrument, on a tree where the answer is known."""
    root = _tree(
        tmp_path,
        {
            "runner.py": (
                "import subprocess\n"
                "def _run(cmd, cwd):\n"
                "    return subprocess.run(cmd, cwd=cwd)\n"
                "def bad():\n"
                '    return _run(["git", "status"], ".")\n'
                "def good():\n"
                '    return _run(["/usr/bin/git", "status"], ".")\n'
                "def also_good(resolve):\n"
                '    return _run([resolve("git"), "status"], ".")\n'
            )
        },
    )
    found = wrapped_bare_heads(root)
    assert [(o["head"], o["line"]) for o in found] == [("git", 5)], (
        "an absolute path and a resolved call must both pass, and the bare "
        f"name must not: {found}"
    )


def test_the_audit_follows_a_wrapper_around_a_wrapper(tmp_path):
    """The fixed point. `_run_and_classify` -> `_run` -> `Popen` is two hops,
    and it is where the `npm` call hid. A single-pass scan sees only the
    inner wrapper and reports a smaller number with nothing to say it is
    smaller."""
    root = _tree(
        tmp_path,
        {
            "runner.py": (
                "import subprocess\n"
                "def _run(cmd, cwd):\n"
                "    return subprocess.Popen(cmd, cwd=cwd)\n"
                "def _run_and_classify(cmd, cwd):\n"
                "    return _run(cmd, cwd)\n"
                "def outer():\n"
                '    return _run_and_classify(["npm", "test"], ".")\n'
            )
        },
    )
    found = wrapped_bare_heads(root)
    assert [o["head"] for o in found] == ["npm"], (
        f"a wrapper around a wrapper must still be followed: {found}"
    )


def test_the_audit_does_not_confuse_two_wrappers_of_the_same_name(tmp_path):
    """The defect this scan was written with, asserted rather than fixed
    quietly.

    `_run` exists in three modules in this repo, and the argv is parameter #0
    in two of them and #1 in the third. Keyed by name alone, one definition
    overwrites another; the scan then checks `_run(["git", ...], repo)`
    against index #1, finds `repo`, and reports nothing. ADR-064's finding was
    that a name is a weak key, and it is a weak key inside the instrument
    written to enforce ADR-064 too.
    """
    root = _tree(
        tmp_path,
        {
            "a.py": (
                "import subprocess\n"
                "def _run(cmd, cwd):\n"
                "    return subprocess.run(cmd, cwd=cwd)\n"
                "def call_a():\n"
                '    return _run(["git", "status"], ".")\n'
            ),
            "b.py": (
                "import subprocess\n"
                "def _run(label, argv):\n"          # argv is parameter #1
                "    return subprocess.run(argv)\n"
                "def call_b():\n"
                '    return _run("clone", ["docker", "ps"])\n'
            ),
        },
    )
    found = {o["head"] for o in wrapped_bare_heads(root)}
    assert found == {"git", "docker"}, (
        "both must be caught: two `_run`s with the argv in different "
        f"positions, and neither may mask the other — got {found}"
    )


def test_the_audit_refuses_to_report_from_no_measurement(tmp_path):
    """Two ways this returns a clean answer from nothing: a moved source
    tree, and a tree with no Python in it."""
    with pytest.raises(FileNotFoundError):
        wrapped_bare_heads(tmp_path)

    (tmp_path / "src" / "ai_venture_studio").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="no Python files"):
        wrapped_bare_heads(tmp_path)
