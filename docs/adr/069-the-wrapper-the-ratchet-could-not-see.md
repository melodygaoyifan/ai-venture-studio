# ADR-069 — the wrapper the ratchet could not see

**Status:** accepted (2026-08-22) · **Release**: v0.112.0

## Context

ADR-062 turned ruff's `S607` on, found 152 invocations handing the kernel a
bare executable name, and deferred the fix with the reason written into the
ignore. ADR-064 wrote the resolver the deferral named, converted all 152, and
deleted the ignore. It left two guards behind:

* `S607` itself, no longer ignorable — `test_s607_is_enforced_and_not_ignored_anywhere`;
* a static ratchet over `src/`, `test_every_subprocess_head_in_src_resolves_through_one_place`.

Both ask the same question of the same shape. The ratchet matches an
`ast.Call` whose `func` is an `ast.Attribute` named `run` / `Popen` /
`check_output` / `check_call`, whose first argument is a list literal with a
bare string head. That is `subprocess.run([...])` written out in full, and
nothing else. `S607` matches the same thing, because a linter cannot do
better: inside a helper the argv head is a *parameter*, and nothing connects
it back to the literal at the call site.

    _run(["git", "worktree", "add", "--detach", str(worktree), "HEAD"], repo)

is an `ast.Name` call. Neither guard looks at it. **They were blind in the same
place, which is why they agreed.**

## Decision

**Ask the question the ratchet meant to ask, then fix every answer.**

`tests/subprocess_wrappers.py` runs two passes: find the functions that
forward one of their own parameters to `subprocess.*` as argv, then find the
calls to those functions whose argument in that position is a list literal
with a bare-name head.

### What it found

| | |
|---|---|
| wrappers in `src/` | 9 |
| call sites handing them a bare name | **35** |
| files | 6 |
| distinct executables | **19** — `argocd axe bandit docker git helm jscpd kubectl lighthouse node npm pip-audit radon railway semgrep size-limit terraform trufflehog vulture` |

ADR-064 reported 152 converted. The population was 187.

The site that says the most is `testing.py`'s `npm test`. `executables.py`
opens by naming that exact call as the reason the module exists:

> This system runs `git` in a workspace it just built from model output, and
> it runs `npm install` in that same workspace — an entry earlier in `PATH`
> than `/usr/bin` turns every one of those into a call the run never intended.

ADR-064's own ratchet repeats it: *"in a workspace built from model output,
next to an `npm install` that just ran in it."* That `npm` reached
`subprocess` through `_run_and_classify` and then `_run` — two hops — and was
invisible to both instruments written to catch it. The motivating example was
the unconverted case.

To be exact about the exposure, because the docstring's phrasing invites an
overstatement: the risk is **`PATH` order**, not the worktree's `cwd`. POSIX
`execvp` does not search `.`. It is the same exposure as the 152 sites ADR-064
did convert, which is the point — these were not a lesser class, only an
unseen one.

### Twelve of the 35 had already computed the answer

`web_tools.py`, `tools/debt.py`, `tools/external.py` and `deploy/externals.py`
all gate on availability first, because CLAUDE.md requires an absent external
to report `skipped` visibly. Every one of them did it like this:

```python
if shutil.which("semgrep") is None:
    return _skipped(...)
_run_json(["semgrep", ...], repo_dir)      # ← and then let PATH decide again
```

The gate found the absolute path, discarded it, and the exec repeated the
lookup independently. So the binary that was checked and the binary that runs
were never guaranteed to be the same one. The fix is smaller than the finding:
**the gate's answer becomes the executable.**

```python
binary = find("semgrep")
if not binary:
    return _skipped(...)
_run_json([binary, ...], repo_dir)
```

That is one lookup instead of two, and it is the one that runs — which is what
`executables.py` claimed the codebase did ("one lookup with a name on it,
instead of sixty invisible ones") and what, at these twelve sites, it did not.

### The remaining 23

`git` and `docker` in `testing.py`, `maintenance/fixpr.py` and
`upstream/build.py` go through `testing._run` with no gate at all, `git` being
a hard requirement. Those take `resolve`, which raises. `build.py` is the
sharpest illustration of the blind spot: it **already imported `resolve`** in
ADR-064 and used it for its direct calls, while its two `_run(["git", ...])`
calls sat two hundred lines away, untouched and unreported.

## Consequences

`tests/test_a_wrapper_is_not_an_escape_hatch.py` is the ratchet, and it has no
ledger and no allowlist, because the count is zero.

The instrument needed three guards, and the second one is the file's whole
argument for existing.

**It refuses to report from no measurement.** A moved source tree raises
`FileNotFoundError`; a tree with no Python in it raises. ADR-067's finding —
eight instrument defects that produced an empty measurement, and *an empty
measurement reads exactly like a passing one* — applies with particular force
to a scan whose passing output is the number zero.

**It asserts that it still recognises its own wrappers.** Every other
assertion in the file passes just as well when wrapper detection breaks: zero
wrappers found means zero call sites checked means zero offenders reported,
byte-identical to a clean tree. So `test_the_scan_still_recognises_the_wrappers_it_depends_on`
fails if the set is empty, and fails if `testing.py` in particular drops out.

**It runs the wrapper pass to a fixed point**, because wrappers nest, and a
single pass finds only the inner one and reports a smaller number with nothing
to indicate it is smaller. The first version of this scan was single-pass and
said **32**; the fixed point said **35**, and the three it had missed included
the `npm` call above.

### The instrument's own defect, which is ADR-064's finding again

The first draft keyed the wrapper table by **function name alone**. `_run` is
defined in three modules here, with the argv in parameter #0 in two of them
and #1 in the third. So `tools/debt.py::_run` silently overwrote
`testing.py::_run`, and the scan then checked every `_run(["git", ...], repo)`
call against index #1, found `repo`, and reported nothing — a clean sweep it
had not performed, over the exact sites it was written to find.

ADR-064's lesson is that **a name is a weak key**. It is a weak key inside the
instrument built to enforce ADR-064. `test_the_audit_does_not_confuse_two_wrappers_of_the_same_name`
pins it with two same-named wrappers whose argv sits in different positions,
so the fix is asserted rather than quietly applied — the discipline ADR-060
earned and ADR-068 reused.

The scan is deliberately conservative in both directions: a wrapper counts
only if it forwards a literal parameter, a call site only if the head is a
plain string with no `/`. Both under-report, which is the correct direction
for a detector whose output is an accusation. `resolve("git")` in argv is
excluded for free, being an `ast.Call` rather than an `ast.Constant`.

### Control

A detector that prints zero is indistinguishable from one that prints nothing.
Run against the pre-fix tree materialised from `HEAD` with `git archive`, it
reports **35**; against the fixed tree, **0**. Reverting a single converted
site — `testing.py`'s `npm` — fails the ratchet naming that line. The
measurement is real in both directions.

### Test seams moved, and that is an improvement

Five tests patched `shutil.which` on the *module under test*
(`web_tools.shutil.which`, `debt.shutil.which`, `externals.shutil.which`).
Those attributes are gone, and the patches now target
`ai_venture_studio.executables.shutil.which` — the seam `test_lane_runners`
and `test_forge` already used after ADR-064. One PATH lookup means one place
to stub, which was the resolver's argument all along.

`test_deploy_externals`'s recorded argv now asserts `/usr/bin/terraform`
rather than `terraform`, which is the behaviour change stated as an assertion.
And `test_a_hang_inside_the_docker_sandbox_blocks_the_gate_too` gained a
`which` stub it did not need before: with `resolve("docker")` in the T3 path,
the test would otherwise have started depending on a docker daemon being
installed — a hermeticity regression introduced by the fix, caught by the
suite, fixed in the same change.

PC-1 2451 → **2457**. Version 0.111.0 → **0.112.0**.

## What stays out

**Extending the scan to `tests/`.** ADR-064 converted 92 test-side sites and
`S607` holds them. A bare name in a test runs on the developer's PATH, in a
tree the developer controls; the threat model that motivates the rule is the
*product* workspace, and that is `src/`. Recorded as a scope decision, not an
oversight.

**Following wrappers through aliases, attributes, or `*args`.** The scan
resolves a call by same-file definition or a single unambiguous
`from ... import`, and gives up otherwise. Widening it means guessing, and a
guessing detector produces false accusations, which is how a ratchet gets
turned off. The honest claim is a floor: **at least** these 35, and zero
remaining of the shape it can see.

**Merging this into the ADR-064 ratchet.** They stay two tests because they
fail for different reasons and a reader should be able to tell which shape
broke. The direct-call one guards the literal; this one guards the hop.
