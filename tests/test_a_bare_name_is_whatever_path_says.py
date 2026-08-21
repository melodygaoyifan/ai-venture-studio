"""ADR-064 — one lookup with a name on it, instead of 152 invisible ones.

`subprocess.run(["git", …])` runs whatever `PATH` resolves `git` to at that
instant. This system runs `git` inside a workspace it just generated from model
output, and runs `npm install` in that same workspace; CLAUDE.md has said
"absolute executable paths, never partial paths" since the beginning, and 152
call sites did not.

The interesting tests here are not "does `resolve` return a path" — they are
the two ways this change could have made things WORSE:

  - by turning a handled degradation ("gh is not installed" → a note in the
    report) into an uncaught crash, and
  - by caching, so a lane that is supposed to notice its binary is missing
    answers from a `PATH` that no longer exists.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from ai_venture_studio.executables import ExecutableNotFound, find, resolve


def test_resolve_returns_an_absolute_path_for_something_that_exists():
    resolved = resolve("git") if shutil.which("git") else resolve("sh")
    assert resolved.startswith("/"), (
        "a relative answer would leave PATH deciding, which is the whole "
        "thing this module exists to stop"
    )


def test_absence_has_two_shapes_because_it_means_two_things():
    """`git` missing is a broken environment; `k6` missing is a Tuesday."""
    assert find("no-such-binary-anywhere-ap") is None
    with pytest.raises(ExecutableNotFound) as raised:
        resolve("no-such-binary-anywhere-ap")
    message = str(raised.value)
    assert "no-such-binary-anywhere-ap" in message
    assert "install" in message, (
        "an operator reading 'X not found' already knew that much; the "
        "message has to say what to do"
    )


def test_the_error_is_the_error_subprocess_already_raised():
    """The compatibility hinge of the whole change.

    `forge._run`, `github._gh` and several lanes were written years before
    this module and catch `FileNotFoundError` to degrade politely. If
    `ExecutableNotFound` were a plain `Exception`, every one of those would
    have become a crash — a worse bug than the one being fixed.
    """
    assert issubclass(ExecutableNotFound, FileNotFoundError)
    assert issubclass(ExecutableNotFound, OSError)
    with pytest.raises(FileNotFoundError):
        resolve("no-such-binary-anywhere-ap")


def test_the_lookup_is_not_cached():
    """Tests patch `shutil.which` to prove a lane skips when its binary is
    absent. A cache would answer those from the real PATH — and, in a long
    run, from a PATH that has since changed."""
    calls = []
    real = shutil.which
    try:
        shutil.which = lambda name, *a, **k: (calls.append(name), real(name))[1]
        find("git")
        find("git")
    finally:
        shutil.which = real
    assert calls == ["git", "git"], (
        "the second lookup was served from somewhere other than PATH"
    )


def test_a_missing_forge_cli_still_degrades_to_a_note(monkeypatch):
    """End-to-end on the hinge above: `gh` absent must produce the same
    "not installed" note it produced before ADR-064, not a traceback."""
    from ai_venture_studio import forge

    monkeypatch.setattr("ai_venture_studio.executables.shutil.which", lambda _: None)
    note = forge.post_comment("https://github.com/o/r/pull/1", "hello")
    assert note is not None
    assert "not installed" in note


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_a_resolved_head_still_runs_the_command(tmp_path):
    """The conversion is worthless if it broke the calls it rewrote."""
    subprocess.run(
        [resolve("git"), "init", "-q"], cwd=tmp_path, check=True, timeout=60
    )
    assert (tmp_path / ".git").is_dir()
