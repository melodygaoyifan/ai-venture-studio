# ADR-067 — a test that could not have failed

**Status:** accepted (2026-08-21) · **Release**: none (tests and docs only)

## Context

ADR-066 shipped, and then its own control was run: restore the one line the
change had replaced, run the suite, read the tests that did *not* fail. One did
not — `test_a_skipped_case_is_not_scored_as_a_zero`, named for a row type, green
on the build where that row type did not exist. It was fixed in `7646b03`.

Asked to close the class rather than the instance, the question became: **how
many other regression tests in this repo are green against the build they were
written to condemn?** Thirty-two test files in `tests/` are named by an ADR as
its mechanism — the set is derived by scanning `docs/adr/*.md` for test
filenames, not by reading. Every one of them is a claim, written down in a test
name, about a defect someone had already seen. None of them had been checked as
a population.

Reading them would not answer it. ADR-054's lesson — running the thing beats
reading it — applies to tests too, and more sharply: a test's whole purpose is
to fail on some build, and the only way to know which build is to run it there.

## Decision

**A regression test's claim is falsifiable or it is decoration, and the repo now
knows which of its own are which.**

The control is mechanical and needs no judgement per ADR. For each forensic test
file, find the commit that added it, check out that commit's **parent**, and run
today's version of the test file against it. Anything that passes is a test that
would not have caught the defect it is named for.

    git worktree add <scratch>/ctl <parent-of-adding-commit>
    cp tests/<file> conftest.py <scratch>/ctl/...
    cd <scratch>/ctl && PYTHONPATH=$PWD/src <repo>/.venv/bin/python -m pytest tests/<file>

**Roll back the whole tree, not just `src/`.** The first pass swapped only the
Python source via `PYTHONPATH` and ran from the current checkout, and it was
wrong in both directions. `test_a_green_run_that_published_nothing.py` reported
**7 passed** — a total hole, apparently — because ADR-065's subject is a GitHub
workflow and a shell script, and those were being read from the *current* tree
no matter which `src/` was on the path. Under the honest control it fails, as it
should. `test_every_probe_compiles.py` moved the other way for the same reason:
its parametrization is over probe *data*. A control that leaves half the
artifact in place measures nothing reliably; it just happens to agree sometimes.

## Consequences

Thirty-two files ran. Fourteen produced a verdict at this rung; **forty-four
tests passed against the tree that predates them.**

Most of that is correct and must stay correct. A guard that is narrow on purpose
has a half that passes on both builds by construction — `..._is_still_...`,
`..._is_left_alone`, `..._is_not_a_collision`. Those are not weak tests; they are
the half that stops an over-broad fix, and their control is a *different*
mutation. `test_s607_is_enforced_and_not_ignored_anywhere` passes on a tree from
before the ignore was ever added, which is exactly right — it guards the entry
coming back. `test_a_line_continuation_cannot_survive_the_folded_scalar` records
a fact about YAML and Python, not about this repo, and would pass on any build
ever written.

**Five were real** at this rung, and three more turned up when the control was
sharpened — see *Three more real holes* below. Each was named for behaviour it
did not pin:

| Test | Why it was green on the broken build |
|---|---|
| `test_the_newest_run_is_last_even_beside_an_abort` (ADR-054) | asserted only `runs[-1]`. `aborted-` sorts before `result-`, so the misplaced file went to the FRONT and the newest *result* stayed last either way — the defect was invisible at the position the test looked. |
| `test_the_cadence_reports_nothing_rather_than_zero` (ADR-053) | hand-wrote `build_rate: null` into a file and asserted the reader said nothing. Pre-fix, `float(None)` raised `TypeError` and the `except` returned `""` — the right answer by accident. The defect was in the WRITER, and no reader can decline to print a zero it was handed. |
| `test_folding_keeps_the_worst_severity_any_site_was_raised_at` (ADR-039) | with no folding at all, nine findings come back and the HIGH one is first anyway, because the list is ordered by severity. `findings[0]` was green on a build with nothing to soften. |
| `test_the_floor_diagnostic_can_actually_be_printed` (ADR-055) | rendered its **own copy** of the message and asserted the copy came out. The shipping message lives in `test_use_case_matrix.py`; a `NameError` put back there would not have failed anything. |
| `test_the_remedy_the_message_recommends_actually_clears_the_check` (ADR-041) | proved three arrangements clear the check, and never read the message. The link to what the message *recommends* was a comment: `# (1) HOIST`. |

All five now fail against their own pre-fix tree, verified individually.

Two of the five deserve to be read together, because they are one shape and it
is this repo's most persistent one. ADR-055's guard kept a second copy of the
message it was guarding; ADR-041's kept the remedy names in a comment beside the
message that names them. **A test that restates a fact instead of reading it is
ADR-051's defect wearing a regression test's clothes** — two copies, and the
guard watching the one it owns. Both now read the shipping artifact: one lifts
the f-string out of the source with `ast` and evaluates it for every stage and
rung, the other asserts the collision message still offers all three remedies
the cases below prove out.

**Eighteen files are unresolved by this control, and are not counted as
passing.** They fail to *collect* against their parent commit — the ADR added a
symbol the test imports, so the whole file errors and per-test discrimination
cannot be read off it. That is a genuine limit of this control, not a clean
bill: `test_a_slice_is_not_the_suite.py` was in that group, and the one hole
ADR-066 found by hand was inside it.

*(This paragraph first said **eleven**, over a population of twenty-six, both
counted by reading the ADRs. The set is now derived mechanically — every test
file named in any ADR, thirty-two of them, each run against the parent of its
own adding commit — and eighteen error. The undercount is recorded rather than
quietly corrected: the number of files this control could not read was itself
produced by the reading it says not to trust.)*

## Closing them

The passage above ended "closing them needs the per-ADR mutation, which is
judgement, not mechanism." That was half wrong, and the half that was wrong was
the expensive half. Four more mechanical rungs reach **346 of the 382 tests**,
and each rung exists because the one above it was blind to something. Judgement
is the last thirty-six, not the whole population — and knowing WHICH thirty-six
is the part the mechanism buys.

| # | Operator | Blind to |
|---|---|---|
| 1 | **parent tree** — run today's file at the parent of the commit that added it | files that will not collect there |
| 2 | **per hunk** — at the ADR's own commit, revert one hunk of its non-test diff | a mechanism that is a NEW function. The only hunk that removes it also removes the name the test imports, so that hunk is excluded as uninformative and every test of the function survives vacuously |
| 3 | **per statement** — delete one statement the change added | changes that are not statements |
| 4 | **per line**, over *every* artifact the change touched | nothing a deletion can express |
| 5 | **condition negation** — negate one added `if`/`while` test | anything that is not a condition |
| 6 | **hand-picked**, one mutation per test, named | nothing — and it is not mechanism |

Two rules make the ladder honest.

**The unit is the TEST, not the file.** A forensic file accumulates tests across
several ADRs. Each test's introducing commit is found by pickaxe —
`git log --format=%H -S "def <name>(" -- <file>`, oldest entry — and the control
runs per (file, commit).

**Uninformative mutants are excluded, never counted.** A mutant that breaks
collection fails every test at once, and counting it would credit each of them
with evidence none earned. Rung 3 exists because rung 2 could not do this
finely enough: ADR-059 added `lane_check` as one contiguous block, and nine of
its tests "survived" a control that never once removed a line of the thing they
test.

**And one ledger, derived rather than remembered.** Six operators over
thirty-one result files is not a thing anyone holds in their head, and this
record has
already had to correct two figures in public — *eleven* files, *twenty-six*
files — that were produced by trying to. So the verdicts are folded by a script
that reads every rung's output and reports KILLED / SURVIVED / UNRESOLVED per
test, later rungs overriding earlier ones, and never downgrading a real verdict
to "could not tell". The first time it ran it found **sixty-one tests still
sitting at rung 2**: their escalation had gone down with the crash in defect 8
below, and nothing had been counting them. The crash was in the log. The
sixty-one were not, and a list of survivors nobody is escalating is
indistinguishable from a list of tests that passed their control.

Its final reading, with every rung run to the bottom: **382 tests, 382 killed,
0 survived, 0 unresolved** — 346 by the mechanical rungs and 36 by a mutation
named for each. "Unresolved" reaching zero is the part worth naming, because
unresolved was where the first pass put everything it could not read, and every
one of the ten holes in this record was sitting inside a row that looked fine.

### Eleven defects in the instruments, and they are all one defect

1. **Whole-tree rollback** (rung 1) — swapping only `src/` left the workflow and
   the shell script being read from the current tree. Recorded above.
2. **Batching two test files into one pytest run** (rung 2) — a hunk whose
   revert stops file A collecting still lets file B collect, so the run never
   says `collected 0 items`, the hunk is scored *informative*, and every test in
   A is recorded as having failed under it. Commit `be39a66` reported 0
   survivors batched and 13 unbatched.
3. **Parsing only Python** (rung 4) — the parseability check ran `ast.parse`
   over every candidate file, so all 27 changed lines of `publish.yml` and all
   95 of `retag.sh` were skipped, and ADR-065's three workflow tests were
   reported as survivors of a control that had never touched their subject.
4. **Reading "error" from anywhere in the output** — collection failure is a
   fact about pytest's final summary line, not about the word appearing in a
   traceback.
5. **A flat hang budget** (rung 4) — 300 seconds, aimed at a file that runs in
   **0.6**. Every mutant that hung cost five idle minutes, and a nine-hundred
   line plan projected past sixty hours. A control that does not finish reports
   nothing at all. The budget now comes from the file's own baseline run.
6. **The control tree was not on the path at all** — the worst of the nine.
   This package was renamed `autoproduct` → `ai_venture_studio` on
   2026-07-27. Today's test files import the new name; a tree from before the
   rename does not contain it; and the venv's editable `.pth` appends the *live*
   repo's `src` to `sys.path`. So the import succeeded — against the current
   build — and every hunk the control reverted was reverted in a package nothing
   had imported. The received wisdom that "`PYTHONPATH` beats the `.pth`" is
   true only for a name that exists in **both**; for a name that exists in only
   one of them there is no contest to win. **Nine (file, commit) units across
   four files were affected, and the tell was in the numbers the whole time:
   survivors *exactly equal* to baseline, which is what a control that changes
   nothing must report.** Fixed by symlinking the old package under the new name
   inside the control tree, and — because a fix that is itself unverified is how
   this list got as long as it did — by resolving `ai_venture_studio.__file__`
   once per checkout and *raising* unless it lies under the control tree.
7. **The fix for the sixth broke the seventh.** The shim is a symlink, a symlink
   is untracked, and `git checkout` leaves untracked things alone — so checking
   out a *post*-rename commit underneath it made git write the real package's
   files straight **through** the link into `src/autoproduct/`. Every unit after
   the first pre-rename one came back `baseline: 0`, which reads exactly like
   "none of these tests pins anything" and was nothing of the kind. The symlink
   is now removed **before** the checkout rather than after it.
8. **A deleted file's line numbers credited to the file above it in the diff.**
   The `+++` line of a deletion says `/dev/null` and names no path, so the
   parser kept the previous file's path and attributed the deleted file's hunks
   to it, at line numbers that file does not have. This is the only one of the
   nine that **crashed**, and the crash is the only reason it was noticed — it
   took the entire fourteen-unit rung-4 pass down with it, loudly, on the first
   unit.
9. **Blame mode planned over paths that predate the rename.** The commit names
   `src/autoproduct/product_bench.py`; the tree being mutated has
   `src/ai_venture_studio/product_bench.py`; the planner drops paths that do not
   exist. Zero mutants, and four units reporting every one of their tests as a
   survivor. Fixed by carrying the name across — `git blame` had been following
   the rename correctly all along; only the name handed to it needed mapping —
   and, more usefully, by making an empty plan return the word **`unresolved`**
   instead of the word `survivors`.
10. **Parametrised tests could not appear in a baseline.** pytest reports one as
    `test_every_profile_specs_and_builds[web]`; every rung intersected that
    against bare `def` names read out of the source; the intersection was empty.
    A unit made only of parametrised tests therefore returned `baseline: 0` —
    the same row the rename bug produced, and read the same way: *this file
    cannot be measured*. `test_use_case_matrix.py@58d6e6f` spent six minutes on
    forty mutants to report nothing about five tests that pass in four seconds.
    Folding the id is not enough on its own: a function counts as passing only
    if **every** one of its cases passed, or the one red parametrisation that is
    the mutant being caught gets folded into a survival.
11. **The timeout did not time anything out** — and it was the harness measuring
    ADR-036, whose whole subject is that "the kill signalled the direct child
    only, so a server the tests booted outlived the timeout holding its port and
    the inherited stdout pipe." `subprocess.run(timeout=…)` signals the direct
    child; the mutated tests boot a product server that survives it and holds the
    pipe, and the `communicate()` after the kill blocks forever. Four shards of
    `test_product_bench.py` sat past a ten-minute wall on five mutants each with
    a forty-five-second budget, which is arithmetic saying the budget was not
    being applied — the last unit on the ledger, held up by the defect the unit
    was written to check. It is `start_new_session=True` and `os.killpg` now,
    with a bounded second wait, because a hang in the harness must not look like
    a slow mutant either.

The sixth had a trap nested inside it. `git checkout` does not remove untracked
directories, so an old tree keeps a `src/ai_venture_studio/` holding nothing but
`__pycache__` — and a directory with no `__init__.py` is a *namespace* package,
which loses to a regular package anywhere later on the path. The stale husk
reproduces the bug it looks like it should have fixed.

Six of the eleven produced an **empty control** — no mutants at all — and an
empty control reads exactly like a passing one. The tenth produced an empty
*baseline*, which is the same lie told from the other end: nothing to score
rather than nothing to score it with. The eleventh produced no row at all, which
is the third face of it: a measurement that never returns cannot be wrong, and
it cannot be right either. That is the defect this record is named after,
committed eight more times in the act of writing it, twice *by the fix
for a previous entry on this same list*. Batching fails the other way — it
credits tests with evidence none of them earned — which is the same error with
the sign flipped: in both directions the number came out of a measurement that
never happened.

This is the reason every rung above reports its own denominator — mutants
generated, live, unusable, hung — and why a survivor count with no denominator
beside it is not a result. `candidates: 0` was printed in the output for the
ninth, in the same object as the survivor list, and was read past. A denominator
only works if something refuses when it is zero, so the harness now *refuses*
rather than reports: nine of these eleven had their honest output available and
nobody was asking for it.

The eleventh had no output to read, so the arithmetic had to be the tell —
five mutants times a forty-five-second budget cannot take ten minutes. That is
worth stating as a rule, because it is the cheapest check on this list and the
only one that works when the instrument has produced nothing at all: **a run
that exceeds its own declared budget times its own mutant count is not slow, it
is not enforcing the budget.**

That same unit is also where ADR-066 walked back in wearing the instrument's
clothes. Even with the kill fixed, 304 mutants over a file whose subject is a
timeout does not finish inside one sitting, so it was run in slices — `SHARD=i/n`,
splitting again whichever slice ran out of wall clock, eighteen slices in the
end. A bench bought in pieces is exactly ADR-066, and it gets ADR-066's answer:
the denominator travels. Each slice records the `i/n` it ran, and the merge
**refuses** unless the residue classes tile `[0, total)` with nothing covered
twice and nothing missed — then sums the mutant counts, sums `killed_by`, and
takes survivors as the *intersection*, because a kill anywhere is a kill and a
survival has to hold everywhere. The first merge attempt was refused by its own
check: it had a hand-typed 289 from `git show -- 'src/*.py'` against a plan of
304 blame-owned lines over every artifact the commit touched. The typed number
was the smaller measurement wearing the bigger one's label, which is the whole
subject of this record, caught this time by a script that would not proceed.

The tenth is why there is now a second script beside the ledger, asking the
question from the other side: which suspects were **named in a unit and got no
row back from it**? Not survivors, not killed, not unresolved — absent. It reads
every result file and prints the difference between the tests a unit said it was
examining and the tests it said anything about. It found exactly the five, and it
is cheap enough to keep. A denominator you never subtract the numerator from is
just another number in the output.

### Six shapes no deletion can reach

The residue is not weak tests. Deletion is simply the wrong operator for these,
and naming which one is right is the whole of the judgement:

| Shape | Example | Its operator |
|---|---|---|
| **A. absence over source** — `assert not offenders`, `"approve_scr" not in source` | `test_no_module_rebuilds_the_tokenizer` | **insertion**: add the offending call site |
| **B. presence over source, token older than the commit** | `test_a_case_with_a_rejected_task_keeps_its_workspace` | edit the older line the guard reads |
| **C. regression guard on behaviour the commit deliberately did NOT change** | `test_the_collapsed_plan_is_invisible_to_lane_check` | mutate the older code it guards |
| **D. subject is not code** — a recorded bench reading in the repo | `test_run_18s_recorded_rates_are_left_alone` | edit the result file |
| **E. guard and calibration both live in `tests/`** | `test_the_guard_fires_on_the_patterns_that_actually_shipped` | mutate the guard |
| **F. redundantly guarded** — two independent mechanisms produce the outcome | `test_an_empty_build_axis_is_still_skipped` | a **two-point** mutant, declared as one |

Shape F is worth its own sentence. Removing the explicit `if data.get("build_rate") is None: continue` leaves `float(None)` raising into the handler below and the run is skipped anyway — the "accidental correctness" that code's own comment says was replaced by a rule. No single-point mutant can falsify the test, and that is a fact about the code, not a fault in the test. A two-point mutant kills it.

Thirty-six residue tests were given a hand-picked mutation each, applied at HEAD,
run alone, and required to fail. All thirty-six died. Three needed a mutant
declared as more than one point: shape F above; the ADR-060 guard fixed in this
change, whose fields are also read by a second real guard in `tests/`; and
`test_the_old_truncation_produced_neither`, whose subject is a recorded pytest
run and which reads three separate banners out of it, so one conceptual edit —
"take the banners away" — lands in three places.

## Five more real holes

**Ten, not five.** The escalated control found four more, and reading one of the
survivors it had cleared found a fifth.

| Test | Why it could not have failed |
|---|---|
| `test_a_collapsed_plan_still_passes_planning` (ADR-059) | its docstring says "end to end" and its body constructs `Plan(status="proposed", …)` and asserts `status == "proposed"` — a constructor echo. It never called `run_planning`, so it would have passed on a build where the lane advisory blocks the plan, which is the one outcome ADR-059 exists to prevent. |
| `test_a_case_with_a_rejected_task_keeps_its_workspace` (ADR-036 family) | took the source of `run_case`, sliced everything after the last `if ` before the `_preserve_workspace(` call, and asserted `len(clean) < len(built)` was in it. That span runs past the condition and into the fourteen-line comment below it, **and the comment restates the condition verbatim in backticks**. The assertion was satisfied by the prose explaining the rule while the rule itself said `<=`. Its predecessor had already failed once, by splitting on the argument list; twice is the pattern, and it now parses the `if` with `ast` instead of slicing text. |
| `test_the_registry_is_the_only_place_that_names_simulated_providers` (ADR-056) | asserted that `SIMULATED_PROVIDERS` is spelled in exactly one file. True, and not what the name claims: the way a second definition of "not a measuring instrument" actually arrives is `provider == "mock"` written out by hand, and five such sites exist. The guard was blind to every one of them for its whole life. |
| `test_the_five_defects_adr_060_fixed_stay_fixed` (ADR-060) | it asserts six named fields are not in the "written and never read" audit — and it could not fail, twice over. The audit counts a field name appearing in any `.md` as read, on the rule that a human with the file open is a reader; `docs/adr/060-*.md` spells all six. Deleting the only code reader of `build_floor` and `probe_floor` left it green: the document *about* the fix was standing in for the fix. Under that, the tuple of names in the test's own body is six string constants in a file the audit scans, and a bare string literal counts as a read — so the test was supplying every reader it then asserted existed, for any state of the code whatsoever. |
| `test_a_discarded_working_tree_cannot_lose_the_flag` (`7656f51`) | its docstring says "the mechanism itself". Its body hand-wrote `built: true` into a spec, hand-committed it, and asserted that `git checkout -- .` could not lose it — which is git's behaviour, true of any committed file on any build ever written, and true whether this repo writes the flag before the commit or after it. The fix it is named for, `finalize_build_bookkeeping` running *before* the staging, never executed inside it. It survived all 24 live mutants of its own commit. |

The first is fixed by driving `run_planning` with a stub planner that returns
run 18's case 04 and asserting the verdict, the advisory's delivery as a minor
finding, and `dag_issues == []`. Each fails against a build where the advisory
is appended to `dag_issues`, verified.

The fourth is scored against code that is not itself. The audit grew two knobs,
both off by default and both for the same kind of caller — a guard that names
specific fields and must be able to fail: `prose_counts=False` drops the
non-Python sweep, so a document describing a fix stops standing in for it, and
`exclude` drops named paths from both sweeps, so a guard listing the names it
guards stops being their reader. Neither changes the audit itself; the sweep
that finds *new* unread fields is deliberately generous, and being generous is
right when the question is "does this fact reach anyone" and wrong when the
question is "is this specific reader still there". With both applied, the six
fields still have code readers — and they have two each: dropping the `src` one
alone fails `test_a_fact_with_no_reader.py` instead, which is a real second
guard, so the mutant that speaks through *this* test is a declared two-point one
(shape F). Before the fix no mutant of any kind could reach it.

The fifth is fixed by driving the mechanism: it now writes a `built: false` spec, calls
`finalize_build_bookkeeping` for real, asserts the flag appeared, *then* stages
and commits in the order the builder uses, and *then* runs both recovery paths —
the working-tree discard and `_reset_workspace`, the second of which its own
docstring named and its body never exercised. Deleting `spec.built = True` from
the bookkeeping now fails it.

The third could not be fixed by making the guard true — the five sites are
*routing* decisions (pick the stub provider, pick the cheap model), not readings
of whether a bench result measured anything, and rewriting them is a `src/`
change this release is not allowed to make (see Mechanism). So the debt is
pinned at its true size instead: the guard now counts the hand-written sites per
file and fails on a sixth, with the remedy named in the failure message. **A
follow-up for the next release that may touch `src/`: route those five through
`is_simulated`, then delete the count.**

### What a green control does and does not prove

A test that **survives** every mutation of its own change is definitely not
pinning that change. That direction is sound.

The other direction is weaker and is not claimed here. A test that **dies** under
some mutant is only *probably* pinning the behaviour it is named for — a mutant
can break it collaterally, through a shared fixture or an import it happens to
touch. The hand-picked rung is the only one that closes this, because it names
the mutation the test is written against and requires that specific one to kill
it. Rungs 1–5 answer "is this test attached to anything at all"; only rung 6
answers "is it attached to what its name says."

## Mechanism

No `src/` change, no version bump. v0.111.0 stays the build every banked run-19
checkpoint is keyed to (ADR-052 keys on `avs_version`, and a release invalidates
the lot mid-purchase). This constraint is why the eighth finding — the
`is_simulated` routing sites — is pinned rather than fixed, and it is named as a
follow-up here rather than left in a scratchpad.

The suite count does not move: ten existing tests were strengthened — five in
the first commit of this ADR, five more once the ladder was run to the bottom —
and none added.

## References

- ADR-066 — the instance this generalises, and its own control section
- ADR-064 — the moved test seam: the other way a test stops watching its subject
- ADR-051 — one fact, one reader; the shape two of these five turned out to be
- ADR-054 — running the thing beats reading it
