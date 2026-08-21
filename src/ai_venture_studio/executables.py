"""Where an external command actually lives — one lookup, not sixty.

CLAUDE.md has said this since the beginning:

    Invoke subprocesses with absolute executable paths and explicit argument
    lists, never with partial paths or `shell=True`.

Half of it was true. Every `subprocess.run` in this codebase passes a list —
`shell=True` appears nowhere and `S602`/`S604`/`S605` confirm it. The other
half was not: **sixty** invocations in `src/` and ninety-two in `tests/`
handed the kernel a bare name and let `PATH` decide. ADR-062 turned `S607` on,
found them, and ignored the rule with the deferral written down — naming, in
the ignore itself, the resolver that would close it. This module is that
resolver (ADR-064), written before the deferral shipped, so `S607` is enforced
rather than grandfathered.

**Why the rule exists.** `subprocess.run(["git", …])` runs whatever `git`
`PATH` resolves to at that instant. This system runs `git` in a workspace it
just built from model output, and it runs `npm install` in that same
workspace — an entry earlier in `PATH` than `/usr/bin` turns every one of
those into a call the run never intended. The list-argv rule stops an argument
becoming a command; this stops a *command* becoming a different command.

**Absolute, not merely resolved.** `shutil.which` searches `PATH` exactly as
the kernel would, so this does not remove the `PATH` dependency — it moves it
to one place, where it happens once, is testable, and produces a path a run
record can quote. That is the honest claim: not "no `PATH`", but "one lookup
with a name on it, instead of sixty invisible ones".

**Two functions, because absence means two different things.** `git` missing
is a broken environment and the caller should hear about it; `k6` missing is
an ordinary Tuesday and the lane reports `skipped` (tools/base.py's standing
rule: an absent external is visible in the record, never silently missing).
`resolve` raises for the first; `find` returns `None` for the second.

**`ExecutableNotFound` is a `FileNotFoundError` on purpose.** That is exactly
what `subprocess` raised before this module existed, so every handler already
catching `FileNotFoundError` or `OSError` keeps catching it and keeps
degrading the way it always did. A resolver that turned a handled degradation
into a crash would have been a worse bug than the one it fixed.

**Deliberately not cached.** A `PATH` scan is microseconds against a process
spawn, and a cache would mean this module answering from a `PATH` that no
longer exists — including in the tests that patch `shutil.which` to prove a
lane skips when its binary is absent.
"""

from __future__ import annotations

import shutil

#: What a missing executable is called here. Installed via a package manager
#: in every case, so the message says which one rather than making the reader
#: guess: an operator reading "git not found" already knew that much.
_HOW_TO_GET = {
    "git": "install git (xcode-select --install, or your package manager)",
    "gh": "install the GitHub CLI (brew install gh)",
    "glab": "install the GitLab CLI (brew install glab)",
    "npm": "install Node.js, which provides npm (brew install node)",
    "node": "install Node.js (brew install node)",
    "uv": "install uv (curl -LsSf https://astral.sh/uv/install.sh | sh)",
    "railway": "install the Railway CLI (npm i -g @railway/cli)",
    "supabase": "install the Supabase CLI (brew install supabase/tap/supabase)",
    "k6": "install k6 (brew install k6)",
    "launchctl": "launchctl ships with macOS — this is not a Linux host",
}


class ExecutableNotFound(FileNotFoundError):
    """A required external command is not on `PATH`.

    Subclasses `FileNotFoundError` — see the module docstring. Callers written
    before this module existed catch that, and they must keep working.
    """


def find(name: str) -> str | None:
    """The absolute path to `name`, or None when it is not installed.

    For the availability-gated callers: a lane that reports `skipped` when its
    external is absent asks this and reports absence itself, because absence
    there is an expected answer and not an error.
    """
    return shutil.which(name)


def resolve(name: str) -> str:
    """The absolute path to `name`, or raise.

    For the callers that cannot proceed without it. The raise is the point:
    the alternative is `PATH` silently choosing, which is the whole reason
    this module exists.
    """
    found = find(name)
    if found is None:
        raise ExecutableNotFound(
            f"{name!r} is not on PATH, and this run needs it — "
            f"{_HOW_TO_GET.get(name, f'install {name} and re-run')}"
        )
    return found
