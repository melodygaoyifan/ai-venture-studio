# ADR-070 — the argv that was never a literal

**Status:** accepted (2026-08-22) · **Release**: v0.113.0

## Context

ADR-069 closed on a claim about its own completeness:

> The honest claim is a floor: **at least** these 35, and zero remaining of
> the shape it can see.

The floor was right and the shape was narrower than it looked. Four detectors
have now asked "does a bare executable name reach the kernel?", and the first
three all required the same thing — a **list literal sitting at the call**:

| written as | `S607` | ADR-064 ratchet | ADR-069 ratchet |
|---|---|---|---|
| `subprocess.run(["git", ...])` | ✓ | ✓ | — |
| `_run(["git", ...], repo)` | — | — | ✓ |
| `argv = ["git", ...]` … `_run(argv, repo)` | — | — | — |
| `for cmd in (["git", "init"], ...): subprocess.run(cmd)` | — | — | — |
| `return ["uv", "run", ..., "pytest"]` | — | — | — |

The bottom three rows had no detector at all.

What forced the question was not a hunch. ADR-069 fixed
`testing.py`'s `sync_cmd_argv = ["docker", "exec", ...]` — and it fixed it
because that file was under a hand sweep for the string `"docker"` at the
time, not because any instrument reported it. **A defect fixed by luck is an
open class**, and the honest response to noticing the luck is to go and
measure what else it was covering for.

## Decision

**Bind the names, follow the returns, and scan `tests/` too.**

`tests/subprocess_wrappers.py` gains two detectors that compose with the
ADR-069 wrapper closure rather than duplicating it, so a new hop is taught to
the scan once:

* `variable_argv_heads` — argv reaches the call through a local name, bound
  either by `argv = [...]` or by `for cmd in ([...], [...])`.
* `factory_argv_heads` — argv is built by a function and **returned**, so no
  `subprocess` call appears anywhere near the literal.

### What it found

| | src/ | tests/ |
|---|---|---|
| argv through a local name | 0 | **3** |
| argv from a factory, then executed | **6 call sites / 3 return sites** | 0 |

All six `src/` sites are `testing.py`, and the headline is `pytest_cmd`:

```python
def pytest_cmd(worktree: Path) -> list[str]:
    if (worktree / "uv.lock").exists() and shutil.which("uv"):
        return ["uv", "run", "--project", str(worktree), "pytest", *pytest_flags()]
```

That is **ADR-069's own finding, one hop further out**. The gate computed the
absolute path, threw it away, and handed the kernel a bare `uv` — so the `uv`
that was checked and the `uv` that ran were two independent `PATH` lookups.
ADR-069 found that at twelve sites and called the fix "the gate's answer
becomes the executable"; here the same gate and the same discard survived,
because the literal was on a `return` statement instead of an argument list.

It is also the worst-placed instance of it. `pytest_cmd` is consumed at
`testing.py:316` and `maintenance/fixpr.py:151`, and what it runs is **the
product's own test suite, inside the workspace built from model output** —
the sentence `executables.py` opens with, and the same sentence ADR-069 found
sitting over an unconverted `npm`. The same module docstring has now named the
exposure twice while the exposure sat inside the module it was written for.

`_mutmut_cmd` is the same defect twice more: `["uv", "run", ..., "mutmut", ...]`
gated by a `_mutmut_in_env` that discarded `shutil.which("uv")`, and
`["mutmut", subcommand]` gated by a `shutil.which("mutmut")` on the line above
that discarded its own answer.

The fix replaces the predicate with the path, because a predicate answers the
wrong question:

```python
def _uv_for(worktree: Path) -> str | None:
    """The `uv` that will run this project's suite, as a path, or None."""
    return find("uv") if (worktree / "uv.lock").exists() else None
```

### `tests/`, which ADR-069 declined on a claim that was false

ADR-069's scope note read:

> **Extending the scan to `tests/`.** ADR-064 converted 92 test-side sites and
> `S607` holds them. […] Recorded as a scope decision, not an oversight.

The threat-model half of that stands: a bare name in a test runs on the
developer's `PATH`, in a tree the developer controls. **The enforcement half
was wrong.** `S607` does not hold them, because `S607` cannot see a loop-bound
argv, and three `_git_repo` helpers — `test_test_gate.py`, `test_fixpr.py`,
`test_mutation.py` — each looped over `(["git", "init", "-q"], ...)`. The
decline was made on a population that had never been counted, which is the
subject of ADR-066, ADR-067 and ADR-068 in that order.

Three sites is cheaper to fix than to argue about, so they take `resolve("git")`
and `tests/` is now scanned rather than reasoned about. ADR-069's note is
corrected in place rather than quietly left standing.

## Consequences

`tests/test_argv_is_not_always_a_literal.py` is the ratchet, over **both**
trees, with no ledger and no allowlist because the count is zero.

**A factory nothing executes is not an accusation.** `lanes/runners.py:netem_command`
returns a bare `["tc", "qdisc", ...]` deliberately — it is the *record* of what
a Linux host would run, shown in skip reports and asserted by
`test_lane_runners`, while `apply_netem` resolves `find("tc")` and execs
`[tc, *argv[1:]]`. It is the ADR-064 display carve-out applied correctly, and
the scan reports a factory only when a caller is found that runs what it
returns. That is why the `src/` factory count is 3 return sites and not 4.

**A name bound twice is dropped, not guessed at.** Resolving it to whichever
branch the walk saw last would be a guess, and a guessing detector produces
false accusations, which is how a ratchet gets turned off.

**The empty-half problem is asserted, not assumed.** `tests/` contains no
subprocess wrappers at all, so the wrapper-dependent half of both scans is
legitimately empty there and proves nothing — the clean result over `tests/`
rests entirely on the direct `subprocess.*` half continuing to work with an
empty wrapper closure. `test_a_tree_with_no_wrappers_at_all_is_still_really_scanned`
pins exactly that, on a synthetic tree where the answer is known. ADR-067's
finding — *an empty measurement reads exactly like a passing one* — is now
load-bearing in three consecutive ADRs.

### Control, which also settles the luck

Run with the current instrument against trees materialised by `git archive`:

| tree | variable | factory |
|---|---|---|
| pre-ADR-069 `src/` (`HEAD~1`) | **1** — `testing.py:402 docker` | 6 |
| pre-ADR-070 (`HEAD` 7990ba5) | 0 in `src/`, **3** in `tests/` | 6 |
| fixed (this change) | **0** | **0** |

The first row is the point. `testing.py:402` is `sync_cmd_argv`, present
before ADR-069 and absent after it — so that site really was live, really was
fixed by a hand sweep rather than by any instrument, and the detector written
here would have caught it. The luck is confirmed rather than asserted, and it
is now covered.

PC-1 2457 → **2469**. Version 0.112.0 → **0.113.0**.

## What stays out

**Chasing argv across function boundaries in general.** The two new detectors
are one hop each: one binding, or one return. Following a list through a
dataclass field, a dict, or a second function that reshapes it means
interprocedural dataflow, and the honest claim stays a floor — **at least**
these nine, and zero remaining of the shapes it can see. The unresolved
denominator was checked by hand rather than left implied: of the 9 argv
positions in `src/` holding a name the binder cannot resolve, 7 are wrapper
bodies where the parameter *is* the argv, 1 is `cadence.py`'s caller-supplied
`executable=`, and 1 is a false positive.

**That false positive is worth naming.** `orchestrator/graph.py:169` is
`v.run(diff_raw, ...)` — a *voter's* `.run` method, matched because the scan
keys on the attribute name `run` alone. ADR-064's ratchet has the identical
surface and only escapes it by also demanding a bare-string literal head. A
name is a weak key, for the third ADR running.

**Converting the seven remaining `shutil.which` calls outside `executables.py`.**
Measured, not skipped: `adoption/toolchains.py`, `adoption/gate_r.py` and
`cadence.py` (×3) already use the answer they compute, and `product_bench.py`
falls back to `sys.executable` before reaching its separate `find("uv")`.
None is a defect. They are a *seam* inconsistency — a test stubbing
`ai_venture_studio.executables.shutil.which` does not reach them — and
`lanes/botfleet.py:303` deliberately accepts a `cwd`-relative command, so a
blanket conversion would break documented behaviour. Recorded as measured
debt, with the count, rather than declined on an estimate.
