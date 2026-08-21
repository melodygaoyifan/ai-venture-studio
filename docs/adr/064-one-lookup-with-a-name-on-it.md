# ADR-064 — One lookup with a name on it

**Status**: accepted · **Date**: 2026-08-21 · **Release**: v0.110.0

## Context

CLAUDE.md has carried this rule since the first commit:

> Invoke subprocesses with absolute executable paths and explicit argument
> lists, never with partial paths or `shell=True`.

Half of it was true. Every `subprocess.run` in the repo passes a list;
`shell=True` appears nowhere, and `S602`/`S604`/`S605` — selected in ADR-062 —
confirm it by finding nothing. The other half was not true anywhere:
**152 invocations** handed the kernel a bare name and let `PATH` decide, 60 in
`src/` and 92 in `tests/`. Forty-four were `git`; the rest were `railway`,
`supabase`, `gh`, `glab`, `npm`, `node`, `k6`, `uv`, `launchctl`, `tc`,
`pgrep`, `python3`.

ADR-062 found them by turning `S607` on, and deferred the fix *in writing* —
the `ignore` entry named the resolver that would close it. The founder read the
deferral and said fix all. This is that change.

**Why the rule is not pedantry here.** `subprocess.run(["git", …])` runs
whatever `PATH` resolves `git` to at that instant. This system runs `git`
inside a workspace it has just generated from model output, and runs
`npm install` in that same workspace. An entry earlier in `PATH` than
`/usr/bin` turns every one of those into a call the run never intended. The
list-argv rule stops an *argument* becoming a command; this stops a *command*
becoming a different command.

## Decision

One module — `ai_venture_studio/executables.py` — with two functions, and
every call site routed through it.

```python
def find(name: str) -> str | None: ...   # absence is an expected answer
def resolve(name: str) -> str: ...       # absence is a broken environment
class ExecutableNotFound(FileNotFoundError): ...
```

**Two functions, because absence means two different things.** `git` missing is
a broken environment and the caller should hear about it. `k6` missing is an
ordinary Tuesday: the lane reports `skipped` with the script it *would* have
run recorded, per tools/base.py's standing rule that an absent external is
visible in the record and never silently missing. `resolve` raises for the
first, `find` returns `None` for the second.

**`ExecutableNotFound` subclasses `FileNotFoundError`, and that is the load-
bearing decision in the whole change.** `FileNotFoundError` is exactly what
`subprocess` raised before this module existed. `forge._run`, `github._gh` and
several lanes were written years earlier and catch it to degrade politely — a
note in the report instead of a traceback. Had the new exception been a plain
`Exception`, every one of those handlers would have become a crash. *A
resolver that turned a handled degradation into an unhandled one would have
been a worse bug than the one it fixed.*

**Deliberately not cached.** A `PATH` scan is microseconds against a process
spawn, and a cache would mean this module answering from a `PATH` that no
longer exists — including in the tests that patch `shutil.which` to prove a
lane skips when its binary is absent.

**Honest about what it does not do.** `shutil.which` searches `PATH` exactly as
the kernel would, so this does not remove the `PATH` dependency. It moves it to
one place, where it happens once, is testable, and produces a path a run record
can quote. The claim is not "no `PATH`" — it is *one lookup with a name on it,
instead of 152 invisible ones*. Hard-coding `/usr/bin/git` would be the literal
reading of the rule and would break every host where git lives elsewhere.

### Three distinctions the conversion had to make

**A displayed command is text; an executed command is an invocation.** `avs
cadence` returns a `launchctl bootstrap …` string for a human to copy, and
`runners.netem_command()` returns the exact `tc` invocation a Linux host would
run, recorded in skip reports. Both stay bare — resolving them would print a
machine-specific path to a reader who cannot use it, and would make
`test_cadence.py` pass on ubuntu CI while failing on the developer's mac. What
goes to the kernel is resolved; what goes to a person is not.

**Where a guard already existed, the guard's answer is what runs.** Several
sites did `if shutil.which("railway") is None: return unavailable` and then
called `["railway", …]` — throwing the answer away so `PATH` could choose
again, three lines later and three more times. Those became
`railway = find("railway")`, one lookup used at every call.

**Resolve where the error message is written.** `forge._run` interpolates
`argv[0]` into "`gh` is not installed — install it and authenticate…".
Resolving at the six call sites would have made that message read
"`/opt/homebrew/bin/gh` is not installed", which is nonsense, and unreachable
besides. So `_run` resolves `argv[0]` itself and the call sites keep passing
the bare name they print.

## Consequences

- `S607` is removed from `ignore` and enforced across `src/` and `tests/`.
  Ruff is clean at 0 findings with the full ADR-062 selection.
- `uninstall_agent` now treats a host with no `launchctl` as success — an
  uninstall was already idempotent, and this extends that to "a machine that
  never could have had one".
- The lane-skip test that patched `ai_venture_studio.lanes.runners.shutil.which`
  now patches `ai_venture_studio.executables.shutil.which`. There is one place
  to patch, which is the point.
- **The test seam moved, and one file had been sitting on the old one.**
  Resolution happens *before* `subprocess.run` is reached, so a test that
  intercepts `subprocess.run` no longer intercepts everything. `test_forge.py`
  opens by declaring itself hermetic — "nothing here touches a network or
  requires either CLI installed" — and six of its tests began failing on this
  machine for exactly the reason it had ruled out: `glab` is not installed
  here, so `resolve` raised, `_run` degraded to its "not installed" note, and
  the dispatch under test never ran. **CI would have gone green on it**, since
  GitHub's runners ship `gh` but not `glab`: the GitHub half would have kept
  passing and the GitLab half would have been silently untested. The file now
  fakes `PATH` as well and compares the tool's *name* rather than whichever
  absolute path a given machine produces, which is the assertion it always
  meant to make. A test whose coverage depends on what is installed on the
  machine running it is not hermetic, and this change was one release away from
  making that true quietly.

## Mechanism

`tests/test_a_bare_name_is_whatever_path_says.py` tests the two ways this
change could have made things worse rather than the way it obviously works:

1. **The degradation survives.** `ExecutableNotFound` is asserted to be a
   `FileNotFoundError` *and* an `OSError`, and an end-to-end test patches
   `gh` out of existence and asserts `forge.post_comment` returns the same
   "not installed" note it returned before.
2. **The lookup is not cached** — two `find` calls produce two `PATH` scans.

`tests/test_the_gate_is_wider_now.py` holds the rule from both ends: `S607` is
in neither `ignore` nor any per-file exemption, and an `ast` walk over `src/`
fails on any `subprocess` call whose argv head is a bare string constant. The
second is the one that matters — config can be edited back, but that test
describes the code.

## References

- ADR-062 — the linter widening that found these, and the deferral this closes
- ADR-055 — the first linter, and the scope line both ADRs hold
- CLAUDE.md §invariants — "absolute executable paths, never partial paths"
