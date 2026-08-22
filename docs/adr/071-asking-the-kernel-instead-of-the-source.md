# ADR-071 — asking the kernel instead of the source

**Status:** accepted (2026-08-22) · **Release**: v0.114.0

## Context

ADR-064, ADR-069 and ADR-070 each closed the same rule — *no bare executable
name in argv* — and each closed it on a floor rather than a measurement:

> The honest claim is a floor: **at least** these 35, and zero remaining of the
> shape it can see. — ADR-069

> the honest claim stays a floor: **at least** these nine, and zero remaining
> of the shapes it can see. — ADR-070

The floor keeps moving because all four detectors — ruff's `S607`, ADR-064's
direct-call ratchet, ADR-069's wrapper scan, ADR-070's binder and factory scans
— **read source text**, and each new ADR found a shape the previous ones could
not read. ADR-070's own summary of the remainder names the wall:

> Following a list through a dataclass field, a dict, or a second function that
> reshapes it means interprocedural dataflow.

That is not a gap to be closed by a fifth static scan. It is the limit of
static scanning. Three consecutive ADRs discovering an unread shape is the
evidence that the *mechanism* is the problem, not any particular scan.

ADR-052 already wrote down what to do about a detector whose failure is
structural: get a second one that **shares no mechanism** with the first,
"because the first stops firing the day the provider rewords its errors".

## Decision

**Watch what the kernel is actually handed, and fail the suite on it.**

CPython raises a `subprocess.Popen` audit event carrying
`(executable, args, cwd, env)` immediately before every spawn.
`tests/exec_audit.py` installs a `sys.addaudithook` from the **rootdir**
`conftest.py`, so the whole session is covered, and `pytest_sessionfinish`
fails the run if any bare head was handed over by one of our own frames.

This observes argv **as the kernel receives it**, whatever built it — a
dataclass field, a dict, `functools.partial`, a reshaping function. There is
nothing to parse and no shape to miss, because the shape has already been
resolved by the time the event fires. The floor becomes a measurement.

### What it found

Nothing, which is the result the four static scans had earned but could not
prove. Over a full run: **4,295 exec events from 7 interpreters, 16 distinct
heads — 13 absolute (4,290 calls), 1 relative, 2 bare.**

The two bare ones are the finding worth keeping. They are `file` and `uname`,
and they are CPython's: `platform.architecture()` shells out to
`subprocess.check_output(['file', '-b', ...])` on every call, and
`platform.uname()` to a bare `uname`. **Zero bare heads were attributable to
our own frames.**

### Two design choices, both load-bearing

**Scoped by stack frame, never by an allowlist of names.** The obvious way to
handle `file` and `uname` is an allowlist. It is also the ADR-060/ADR-068
ledger trap: `{"file", "uname"}` silences the stdlib *and* silently forgives
our own code for running `file` — the detector would then be quietest about
exactly the case it exists to catch. So the question asked is "which frame
called `subprocess`", and the answer must be a file under `src/` or `tests/`
before anything is recorded. `exec_audit.py` contains no allowlist.

The nearest frame is the one that decides, not any frame. `platform.architecture()`
is reached from our code, so "is any frame ours?" would blame us for the
stdlib. `test_the_stdlibs_own_bare_execs_are_not_attributed_to_us` pins the
distinction against the real `platform` call rather than a mock of it.

**The accusation names the caller, not just the wrapper.** The first version
reported the nearest frame only, which for argv routed through a helper is the
helper's own `subprocess` line:

    'git'  at src/ai_venture_studio/testing.py:115 in _run()

True, unactionable, and pointing at code that is not wrong — **ADR-069's blind
spot reproduced inside the instrument built after it**. The report now carries
up to four of our frames:

    'git'  (×3788)
        spawned at src/ai_venture_studio/testing.py:115 in _run()
          called from src/ai_venture_studio/testing.py:178 in _run_gate_in_worktree()
          called from src/ai_venture_studio/testing.py:164 in run_test_gate()
          called from src/ai_venture_studio/orchestrator/graph.py:262 in test_gate_node()

`testing.py:178` is the site ADR-069's static scan names. The two instruments
now agree on the location as well as the verdict, which is what makes
disagreement between them informative.

Repeats are counted rather than listed, because one unconverted `git` inside
`testing._run` fires 3,788 times in a full run and would bury every other
offender.

### Control

Reverting one converted site — `testing.py`'s `git = resolve("git")` back to
`git = "git"` — and running `tests/test_test_gate.py`:

| | |
|---|---|
| reported | `'git'` at `testing.py:178` and `:188`, with the full chain |
| pytest's own result | **16 passed** |
| exit status | **1** |

The middle row is why the exit status is checked and not assumed. Every test
passed; the run failed anyway, on evidence no test asserted. Reverting
ADR-070's `tests/` fix as well produces the second site,
`test_test_gate.py:29`, so both trees are really covered.

### The blind spot, measured rather than described

A call the suite never executes is never observed. This is the exact
complement of the static scans' failure, and ADR-068 already put a number on
it: **2,315 statements this suite never executes.**

It has a concrete instance right here. ADR-070's headline defect was
`pytest_cmd` returning `["uv", "run", ..., "pytest"]` — and `uv` appears
**zero times** in the 4,295 observed execs, because no test worktree carries a
`uv.lock`. **This instrument could not have found ADR-070's finding.** Stated
plainly because the temptation with a new detector is to imply it supersedes
the old ones, and this one does not: neither is sound alone, and they fail in
opposite directions, which is the whole reason both run.

### Cost

Measured, because a permanent instrument that slows every run needs a number
and not an impression. Three alternating pairs on a subprocess-heavy subset:
median **19.82s** without, **19.94s** with — **+0.6%**.

An earlier full-suite comparison suggested +4.8% and was run-to-run noise. The
hook does no I/O, and rejects every non-spawn event on its first line — it is
called for `open`, `import` and `compile` too — so the frame walk runs only for
a bare head, which in a clean tree never happens at all.

## Consequences

`tests/test_the_kernel_sees_what_we_think_it_sees.py` holds the ratchet and
its controls. Every assertion in it passes just as well when the hook is
broken: no hook means no events means no violations, byte-identical to a clean
tree. ADR-067's finding — *an empty measurement reads exactly like a passing
one* — is now load-bearing in four consecutive ADRs, and here the passing
output is literally the number zero. So the hook is exercised in both
directions before its silence is accepted: a planted bare name must be caught,
an absolute path must not be, a relative path must not be, and the session must
have observed a real spawn at all.

**The control's argv is built by concatenation on purpose.** `S607` and all
three static scans need a string literal to match, and there is none in
`test_the_audit_catches_a_bare_name_handed_over_by_one_of_our_own_frames`.
The class this instrument exists to cover is demonstrated rather than
described, and the control cannot be "fixed" by the scans it complements.

**A failure inside the hook is recorded, not swallowed.** If the audit event's
shape changes under us, the ledger says so; a detector that fails silently
reports zero for the same reason a clean tree does.

**The verdict is delivered from `pytest_sessionfinish`, not raised from the
hook.** An exception thrown inside `subprocess.Popen` surfaces at the call
site, and this codebase has `try`/`except` around plenty of external tools —
the accusation could be swallowed by the very code it accuses. It is also why
`test_no_bare_executable_name_reached_the_kernel` is not the authority: it
sees only the execs that happened before it ran. It exists so an early
offender gets a named failure with a location, and `sessionfinish` is what
actually fails the run.

**`tests/` has its first subprocess wrapper**, `_spawn`, and ADR-070's
`test_a_tree_with_no_wrappers_at_all_is_still_really_scanned` asserted there
were none. Its premise is now false and it is corrected rather than deleted:
the real-tree assertion became a deliberate one-entry ledger, and the case it
was pinning — the direct-call half working with an empty wrapper closure — is
kept on the synthetic tree, where it was already pinned properly. Incidentally
this makes the wrapper half of ADR-069's and ADR-070's scans non-empty over
`tests/` for the first time, so their clean result there is now stronger.

### A reason that was wrong attached to a rule that was right

The hook went into `tests/conftest.py` first, and the suite failed on an
existing assertion that session hooks belong in the **rootdir** conftest. The
reason recorded there, in the file whose entire purpose is to end quiet
non-enforcement, was:

> `pytest_sessionfinish` is one of the hooks pytest invokes **only** from the
> rootdir conftest (or a plugin), never from a subdirectory one.

That is false. The control run had already exited 1 from a hook in
`tests/conftest.py`, so it was measured directly on a throwaway tree: pytest
9.1.1 calls a subdirectory conftest's `pytest_sessionfinish`. Whatever
silently swallowed the original PC-1 check — the suite passing 1667 against a
claim of 1655 and saying nothing — it was not this.

The rule is kept, on the reason that actually holds: **a subdirectory conftest
is loaded only if collection reaches that directory**, so a session-wide check
living there is conditional on how the run was invoked, and a check the
invocation can switch off is not a check. Corrected in the docstring and in
the assertion's message rather than left standing — a false reason attached to
a correct rule is exactly what ADR-069's `tests/` scope note turned out to be,
and ADR-070 then had to go and count the population it had been declined on.

The hook now lives beside the PC-1 check, and is evaluated first: unlike PC-1
it is not a whole-suite-only question, so a subset that spawns an offender
fails on the subset.

PC-1 2469 → **2485**. Version 0.113.0 → **0.114.0**.

## What stays out

**Child interpreters.** The suite spawns 7, and the hook installs only in the
parent, so a `pytest` running inside a workspace built from model output
records nothing of its own. That is deliberate: those execs belong to the
generated product, not to this codebase, and this rule is about our argv. The
`uv` that *launches* that child is observed by the parent, which is the part
we are responsible for.

**Making it a production guard.** It lives in `tests/`, not `src/`. A shipped
audit hook would be a security control, and a security control that a caller
can defeat by catching an exception is worse than none — the honest version of
that is `executables.resolve`, which already exists and is what these four
ADRs have been enforcing. This instrument's job is to prove the enforcement
worked.

**Auditing anything other than `subprocess.Popen`.** CPython raises audit
events for `os.system`, `os.exec*` and `ctypes` calls too. None appears in this
codebase, and adding detectors for absent shapes is how a ratchet accumulates
assertions nobody can evaluate — ADR-067's condemnation of tests named for
behaviour they never pinned. If one appears, the event name is a one-line
change.

**A fifth static scan.** This is the point of the ADR. Three consecutive
attempts to read the remaining shapes out of source text each found a shape the
last one could not read; the answer was a different mechanism, not a better
parser.
