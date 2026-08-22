"""Watch what is really handed to the kernel, from inside the running suite.

Four detectors have now asked "does a bare executable name reach the kernel?"
— ruff's `S607`, ADR-064's direct-call ratchet, ADR-069's wrapper scan and
ADR-070's binder/factory scan — and **all four read source text**. ADR-070
closed on the honest limit of that:

    the honest claim stays a floor: at least these nine, and zero remaining of
    the shapes it can see.

The shapes it cannot see are the ones where no list literal exists to find: an
argv assembled in a dataclass field, accumulated in a dict, reshaped by a
second function, or bound through `functools.partial`. No static scan will
ever enumerate those, because enumerating them is interprocedural dataflow.

This is the second detector ADR-052 asks for — one that "shares no mechanism"
with the first. `sys.addaudithook` receives CPython's `subprocess.Popen` audit
event with `(executable, args, cwd, env)` immediately before the spawn, so it
observes the argv **as the kernel will receive it**, whatever shape produced
it. The floor becomes a measurement.

Its blind spot is the exact complement of the static scans': a call the suite
never executes is never observed, and ADR-068 measured that surface at 2,315
unexecuted statements. Neither instrument is sound alone. Together they fail
in opposite directions, which is the entire reason to run both.

Two design choices are load-bearing:

**Scoped by stack frame, never by an allowlist of names.** CPython's own
`platform.architecture()` shells out to a bare `file`, and `platform.uname()`
to a bare `uname` — four real events in a full run of this suite. An allowlist
holding `{"file", "uname"}` would silence those *and* silently permit our own
code to exec them, which is the ledger trap ADR-060 earned and ADR-068 reused.
So the question asked is "which frame called `subprocess`", and the answer has
to be a file inside `src/` or `tests/` before anything is recorded.

**The fast path does no work.** The hook is called for *every* audit event the
interpreter raises — `open`, `import`, `compile` — so the first line rejects
everything else, and the frame walk happens only for a bare head, which in a
clean tree never happens at all. Measured cost of installing it: +0.6% on a
subprocess-heavy subset (medians 19.82s → 19.94s over three alternating pairs).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_OURS = (str(_REPO / "src") + "/", str(_REPO / "tests") + "/")

# Frames that are the plumbing between a caller and the kernel, not the caller.
# `subprocess.run` and `check_output` both land in `Popen.__init__`, so the
# first frame outside these files is the code that chose the argv.
_MACHINERY = frozenset({__file__, subprocess.__file__})

#: How many of our own frames to record with a violation. One is not enough:
#: argv routed through a wrapper makes the nearest frame the wrapper's own
#: `subprocess` line, which is true and useless — the bare name was chosen by
#: the caller. ADR-069 exists because that hop was invisible; an accusation
#: that stops at the wrapper reintroduces the same blindness in the report.
_CHAIN = 4

#: Bare-name execs attributed to our own frames, each as
#: `(head, [(file, line, func), ...])` innermost first. Must stay empty.
VIOLATIONS: list[tuple[str, list[tuple[str, int, str]]]] = []

#: How many times each distinct offender fired, keyed the same way.
REPEATS: dict[tuple, int] = {}

#: Every `subprocess.Popen` event seen, ours or not. A session that observed
#: zero execs measured nothing, and an empty measurement reads exactly like a
#: passing one (ADR-067) — so the count is reported rather than assumed.
OBSERVED = [0]

_INSTALLED = [False]


def _our_frames() -> list[tuple[str, int, str]] | None:
    """Our frames behind this spawn, innermost first — or None if it isn't ours.

    "Ours" is decided by the *nearest* non-plumbing frame only. CPython's
    `platform.architecture()` shells out to a bare `file` from a stack whose
    outer frames are ours; asking "is any frame ours?" would blame us for the
    stdlib. Asking "who called `subprocess`?" gets it right, and needs no
    allowlist of forgiven names — which is the point, because an allowlist
    holding `file` would also forgive *us* for running `file`.
    """
    frame = sys._getframe(1)
    while frame is not None and frame.f_code.co_filename in _MACHINERY:
        frame = frame.f_back
    if frame is None or not frame.f_code.co_filename.startswith(_OURS):
        return None
    chain = []
    while frame is not None and len(chain) < _CHAIN:
        if frame.f_code.co_filename.startswith(_OURS):
            chain.append((frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name))
        frame = frame.f_back
    return chain


def _hook(event: str, args: tuple) -> None:
    if event != "subprocess.Popen":
        return
    OBSERVED[0] += 1
    try:
        argv = args[1]
        head = argv[0] if argv else args[0]
        if isinstance(head, bytes):
            head = head.decode("utf-8", "replace")
        if not isinstance(head, str) or "/" in head:
            return
        chain = _our_frames()
        if chain is None:
            return  # stdlib's own `file`/`uname`, or a third-party library
        _record(head, chain)
    except Exception as exc:  # noqa: BLE001 - an audit hook must never break a run
        # Recorded rather than swallowed: a detector that fails silently reports
        # zero for the same reason a clean tree does.
        _record(f"<audit hook failed: {exc!r}>", [(__file__, 0, "_hook")])


def _record(head: str, chain: list[tuple[str, int, str]]) -> None:
    """Record one distinct offender, counting repeats rather than listing them.

    A single unconverted `git` inside `testing._run` fires 3,788 times in a
    full run. Appending each one buries the other offenders and turns the
    failure message into something nobody reads to the end.
    """
    key = (head, tuple(chain))
    REPEATS[key] = REPEATS.get(key, 0) + 1
    if REPEATS[key] == 1:
        VIOLATIONS.append((head, chain))


def install() -> None:
    """Install the hook. Idempotent; audit hooks cannot be removed once added."""
    if _INSTALLED[0]:
        return
    _INSTALLED[0] = True
    sys.addaudithook(_hook)


def is_installed() -> bool:
    return _INSTALLED[0]


def _where(filename: str) -> str:
    try:
        return str(Path(filename).relative_to(_REPO))
    except ValueError:
        return filename


def report() -> str:
    lines = [
        f"{len(VIOLATIONS)} distinct bare executable name(s) reached the kernel "
        f"from our own frames, out of {OBSERVED[0]} exec(s) observed:"
    ]
    for head, chain in VIOLATIONS:
        times = REPEATS.get((head, tuple(chain)), 1)
        suffix = f"  (×{times})" if times > 1 else ""
        lines.append(f"  {head!r}{suffix}")
        for depth, (filename, lineno, func) in enumerate(chain):
            arrow = "spawned at" if depth == 0 else "  called from"
            lines.append(f"      {arrow} {_where(filename)}:{lineno} in {func}()")
    lines.append(
        "Resolve it through ai_venture_studio.executables — resolve(name) when "
        "the run cannot proceed without it, find(name) when absence is an "
        "expected answer (ADR-064, ADR-069, ADR-070, ADR-071)."
    )
    return "\n".join(lines)
