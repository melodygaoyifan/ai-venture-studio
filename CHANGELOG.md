# Changelog

SemVer over the enumerated contract surface (CONTRIBUTING.md). One entry
per release, newest first; the git tags v0.8.0–v0.27.0 predate this file
and are summarized in the README roadmap and docs/implementation-map.md.

## v0.111.0 — two ways to be green and wrong

Both changes here start from an operational question and end at a ledger that
would have recorded something untrue: *"how do we move a tag safely?"* and
*"is there another way to afford bench run 19?"* In each case the honest
answer required fixing the thing that would have quietly accepted a wrong one.

### ADR-065 — a green run that published nothing

The tag push **is** the publish here (Trusted Publishing over OIDC), so
force-moving a tag starts a second `publish` run while the first is still
uploading. That hazard was known and written down as advice — *cancel the
in-flight run for the old ref first* — which is a thing to remember at the end
of a release, by someone who has just found a defect in what they were about
to ship.

Worse, the symptom that would reveal a lost race had already been removed.
`uv publish --check-url` was added so re-tagging would not produce a red
release, and **re-tagging is exactly the case where red was correct**: "already
on PyPI" is decided by *filename*, and a re-tagged commit builds a different
wheel under the same filename. So the run correcting a release skips its own
upload and reports success — PyPI serving the pre-fix build, the tag pointing
at the fix, the release green. A version can only be yanked, never replaced,
so no later run repairs it.

The publish job now asks the **index** what it is serving and compares the
sha256 of every file against the ones this run built. A skipped upload, a
half-finished upload from a cancelled race, and a correct release become three
distinguishable outcomes, and the failure names the only remedy that exists
(bump the version) rather than the one people reach for (re-run). It does not
depend on how `uv publish` behaves when hashes differ: the guarantee wanted is
a property of the index, not of the uploader.

Two supporting pieces. A `concurrency` group keyed on `github.ref` — the key
is the whole correctness argument, since a constant group would cancel an
unrelated release mid-upload. And `scripts/retag.sh`, which mostly **refuses**:
once PyPI serves the version, moving the tag cannot change what anyone installs
and only makes the tag a lie. It refuses before it cancels, and cancels — with
a wait, because `gh run cancel` returns before the upload stops — before it
pushes.

### ADR-066 — a slice is not the suite

Bench run 19 is owed, and the account cannot afford five hours in one sitting.
The supported way to buy it in pieces already existed (`--limit` + ADR-052
checkpoints + `--resume`), and each piece was corrupting the ledger on its way
past.

`load_cases(cases_dir)[: limit or None]` meant `cases_total` counted the cases
the run was *handed*, not the cases the suite *has*. `--limit 1` wrote a
scoreboard reading **`1 of 1`** into the tracked `benchmarks/results/`. It is
not partial (`cases_measured == cases_total`), not aborted (the environment was
fine), and not simulated (the cases it ran were measured for real, at full
price, against the real provider) — so every guard on that ledger passed it
through as a complete reading of the suite.

The suite contains a case that has built nothing in two consecutive runs. A
slice landing on it reads `build 0% over 1 of 1` — below floor — and
`CONSECUTIVE_RUNS_TO_FIRE = 2`. Two purchases of a bench nobody could afford to
run would have fired a criterion whose only remedy is a human deciding whether
to kill the project.

Third instance of one shape, after ADR-053 and ADR-056: *the cheap substitute
for an expensive measurement can corrupt the ledger the measurement lives in.*

Now the denominator travels. `--limit` bounds what a run **pays for**, never
what the scoreboard **counts**; each unreached case gets an
`error: not run: --limit N` row, which keeps it out of every rate (ADR-035,
never a 0.0 for a case nobody asked) while recording *why* it is unmeasured —
the one thing a list of names cannot carry. A truncated run stays in `.mas/`,
where its scoreboard and checkpoints are useful, and out of the tracked
directory; `bench_criterion._scan` refuses one that arrives anyway and names it
with the next step attached. Every reader closed in one change (ADR-051): the
criterion, the cadence watchdog — which would otherwise report "ran recently,
all clear" about a suite nobody read — and the alert, whose heading now says
`SLICE` for the same reason it says `SIMULATED`.

`truncated` is read off the rows rather than the flag, so `--limit 6` over six
cases is still a complete reading. Refusing it was a live bug in this change's
own first draft — `limited_to` is a model field and `model_dump` put it in the
payload unasked — caught by running the code against the deployed build rather
than reading the diff.

Twelve tests broke on the semantic change, and none of them were in a file this
change touched: `--limit N` had become the standard shorthand for "the suite is
N cases", and tests that need a small denominator now build a small suite. The
targeted runs were green; only the full suite found them.

**After the release, tests and docs only — no version bump** (ADR-067, and
`7646b03` before it). ADR-066's control was generalised from one change to the
whole population: for each of the thirty-two test files an ADR names as its
mechanism, check out the parent of the commit that added it and run today's
test file there. Anything green is a test that would not have caught the defect
it is named for. Forty-four passed; most are the deliberately narrow half of a
guard and must stay that way, but **five were named for behaviour they did not
pin** and have been fixed — two of them by reading the artifact they guard
instead of keeping their own copy of it. Eighteen files fail to *collect*
against their parent commit; rather than record them as unresolved, the control
was escalated through four finer operators — per hunk, per statement, per line
over every artifact the change touched, and condition negation — down to a
hand-picked mutation per test where no mechanical operator applies. That found
**four more** holes (a "test end to end" that asserted a constructor argument
echoed back; a source-text guard whose slice ran past the condition into a
comment restating it verbatim; a durability test that hand-committed the flag
itself and then asserted git could not lose it, which is true on every build
ever written; and an ADR-060 guard that could not fail for any state of the
code, because the document *about* the fix spells the field names and the test's
own tuple of names supplied the readers it then asserted existed), and reading
the survivors found a fifth (a registry guard blind to the five sites that write
`== "mock"` by hand). **Ten in total.** The ledger's final reading is **382
tests, 382 killed, 0 survived, 0 unresolved** — 346 by the mechanical rungs, 36
by a mutation named for each.

Eleven defects were found in the instruments along the way, eight of which
produced an *empty measurement*: six empty controls, one empty baseline, and one
run that never returned at all. An empty measurement reads exactly like a passing
one, and twice the empty one arrived as the fix for an earlier entry on the same
list. The eleventh is the sharpest: the harness scoring ADR-036 used
`subprocess.run(timeout=…)`, which signals the direct child only — so a product
server the mutated tests booted outlived the kill holding the stdout pipe and the
budget was never enforced, which is verbatim the defect ADR-036 exists to fix.
Five mutants on a forty-five-second budget cannot take ten minutes; that
arithmetic was the only tell, because there was no output to read.

**Buying run 19 in slices is now the recommended way** to run an expensive
bench on a constrained account: slice as far as credit allows, then close with
one un-limited `--resume` run, which is the reading. All pieces must share a
build and land inside the 14-day checkpoint window, so `src/` must not change
mid-purchase.

## v0.110.0 — the gate that stopped asking, the timer that kept buying

The other two answers to the same question v0.109.0 opened. That release swept
the source and mined the result files; this one turns to the two **controls**
themselves — the linter that had been asking one question for 54 releases, and
the scheduler that was about to spend $67.88 because a week had gone by. The
widened linter then produced a third change of its own (ADR-064): the one rule
it was not ready to enforce, enforced.

### ADR-062 — an ignore is a decision

ADR-055 added the first linter and drew it narrow on purpose, with a test that
said of itself: *"if a later change widens `select`, this is the test that asks
whether the widening was deliberate."* It was never revisited. Meanwhile
CLAUDE.md carried three invariants stated in prose with no mechanism behind
them at all — no runtime asserts, no silently swallowed exceptions, absolute
executable paths.

`select` is now the fourteen families about code that **misbehaves**
(`F B S BLE W A PIE RET RSE PTH C4 LOG G TID`). The cosmetic ones stay out, and
the scope test pins that boundary rather than a count. It landed at **zero
findings**, not a backlog: a gate that arrives with a list to work through
later is the gate ADR-055 refused.

Three defects, each confirmed live against the **installed** v0.109.0 before a
fix was written:

- **A desync probe that passed a client which had stopped producing.**
  `desync_probe` zipped the two hash streams and read the common prefix — and a
  stream that stops has no divergence in that prefix. Four server hashes
  against two client hashes returned `passed=True, detail='no divergence'`. The
  most complete desync available, green, from the probe built to catch it.
- **An unsubstantiated number leaving through the substantiation check.**
  `zip(draft_numbers, register_numbers)` stopped at the shorter side, so
  `"Teams ship 40% faster across 12000 sessions."` against a register entry
  carrying one figure returned `[]`. The 12000 was checked against nothing.
  Now its own `unsubstantiated_number` rule — drift means correct the figure,
  this means substantiate it or cut it.
- **`silent behavior change: divergence at tick None`** — `cross_build_replay`
  on two streams that agree and then one stops. It now says which build stopped
  and after how many ticks.

`S110`/`S112` found **fifteen** silently swallowed exceptions, the CLAUDE.md
rule that had never had an enforcer. Twelve modules gained a logger and each
handler now names what it lost (`skipping an unreadable spend row — money in it
is not counted: …`), and **`AVS_DEBUG=1`** gives them a reader: fifteen
handlers logging into a logger with no handler would have been ADR-060's own
defect wearing a different hat. Off by default and it stays off — these go to
stderr and the CLI's real output is Rich on stdout.

`S310` found `urlopen` taking `SCHEMA_REGISTRY_URL` at its word, scheme
included; `file:///etc/passwd` there would have been read and handed to
`json.loads`. Operator-set rather than attacker-set, so a guard rather than an
incident, and it now refuses in words an operator can act on.

`S607` was the one deferral, and it did not survive the week — see ADR-064
below. It was ignored **with the deferral written down**, naming the real fix
in the file where the ignore lived; the founder read it there and said fix all.
That is the sequence this ADR argues for, working once: an ignore-with-a-reason
is a decision someone can overrule, and a bare `"S607",` would still be sitting
in the file.

### ADR-063 — the bench is not on a timer

The product-bench costs $67.88 and about five hours of API time, and it was
scheduled exactly like the two loops that cost nothing: seven days since the
last dated result. Run 19 was about to be bought on that basis, over a
framework that had not necessarily changed. Days are a proxy for what this
series actually measures, and the proxy breaks in both directions — it buys
runs over an unchanged system, and it reads `ok, 4d` while the numbers came
from nine releases ago.

Due is now: the reading's own `avs_version` differs from the running build
**and** at least 7 days have passed, **or** 90 days have passed regardless.
The second clause is the load-bearing one. "Run it when the version changes" is
one sentence away from a watchdog that reports all clear forever — the exact
failure `cadence.py` names in its own source — so a test asserts the backstop
is a number and not infinity, because the way it becomes "never" is not a code
change but someone setting it to 3650 "for now". A result with no
`avs_version` counts as **changed**; an unknown build is not evidence of the
same build.

Strictly cheaper and never more expensive: every date the new rule fires, the
old timer would have fired too — walked over 120 days of fixture rather than
asserted in prose. `LoopStatus.due_because` carries the reason into the table,
into `run_due`, and into the alert beside the command, because `DUE (9d)` reads
as a timer and a timer is no longer what raises it.

Found in the same module on the way, by looking rather than by hitting it: an
ADR-061 aftershock where `_bench_rates` required both rates and so would have
blanked the scheduler line entirely for a run with a null probe rate — the
single worst reading the series can produce.

### ADR-064 — one lookup with a name on it

ADR-062's deferral, taken up in the same release. **152 subprocess invocations
handed the kernel a bare name and let `PATH` decide** — 60 in `src/`, 92 in
`tests/`, 44 of them `git`, the rest `gh`, `glab`, `npm`, `node`, `railway`,
`supabase`, `k6`, `uv`, `launchctl`, `tc`, `pgrep`. CLAUDE.md has said
"absolute executable paths, never partial paths" since the first commit, and
half the rule was true everywhere: `shell=True` appears nowhere, every call
passes a list. The other half was true nowhere.

It is not pedantry in this codebase. The system runs `git` inside a workspace
it has just generated from model output, and runs `npm install` in that same
workspace. The list-argv rule stops an *argument* becoming a command; nothing
stopped a *command* becoming a different command.

One module, `ai_venture_studio.executables`, and two functions — because
absence means two different things. `git` missing is a broken environment and
`resolve` raises; `k6` missing is an ordinary Tuesday and `find` returns `None`
so the lane reports `skipped` with the script it would have run in the record.

The load-bearing decision is that **`ExecutableNotFound` subclasses
`FileNotFoundError`** — exactly what `subprocess` raised before this module
existed. `forge._run`, `github._gh` and several lanes were written years
earlier and catch it to degrade to a note; a resolver that turned a handled
degradation into an unhandled crash would have been a worse bug than the one it
fixed. It is deliberately uncached, and honest about its limit: `shutil.which`
still searches `PATH`, so the claim is *one lookup with a name on it*, not
*no `PATH`*.

Three distinctions the conversion had to make. A command **displayed** for a
human stays bare — `avs cadence` prints a `launchctl bootstrap …` line to copy,
and `netem_command()` records the exact `tc` invocation a Linux host would run
— while a command **executed** is resolved. Where a `shutil.which` guard
already existed, its answer is now what runs, instead of being discarded so
`PATH` could choose again three lines later and three more times. And `_run`
resolves `argv[0]` itself, so the "`gh` is not installed" message it prints
does not turn into a machine-specific path.

`S607` is deleted from `ignore` rather than grandfathered, and a test walks
`src/` with `ast` and fails on any bare-name argv head — the rule is held by
the shape of the code, not only by the config.

One consequence worth naming, because the suite caught it and a reviewer would
not have: resolving before `subprocess.run` **moves the test seam**.
`test_forge.py` declares itself hermetic — "nothing here touches a network or
requires either CLI installed" — and it intercepted `subprocess.run`, which is
now one frame too late. `glab` is not installed on the development machine, so
six of its tests silently stopped exercising dispatch and took the "not
installed" branch instead. CI would have gone green: GitHub's runners ship `gh`
but not `glab`, so the GitHub half would have kept passing and the GitLab half
would have been quietly untested. The file now fakes `PATH` too and asserts on
the tool's name rather than a machine-specific path.

## v0.109.0 — finding the issues that are already here

Asked why the system needs endless batch running when the existing issues
could just be found and fixed, this release answers it twice: once by sweeping
the source for a defect class mechanically (ADR-060), and once by mining the
result files already on disk (ADR-061). Seven defects, one afternoon, no API
spend — including two that v0.107.0's own hand-written fix touched and missed.

### ADR-060 — the sweep

ADR-058's six findings were one defect six times: a component established a
fact, put it on the record, and the reader that needed it never got it. Every
one was found by hand, after a $67.88 bench run had already paid to expose the
symptom. That shape is mechanical, so `tests/write_without_reader.py` now asks
it mechanically — an AST walk over every record class under `src/`, against a
deliberately generous notion of a reader, in 1.2 seconds.

Five defects it found that no run had ever surfaced:

- **Built, tests green, nothing imports it.** `BuildResult.wireup_issues` is
  computed only on a **successful** build, so the one outcome the record had no
  way to state was the one that looks best and is worst. Every reader saw
  `status: built` and stopped. It and `modified_existing` — whose own field
  description promised the changes would be "visible, reviewed, never silent" —
  are the two `BuildResult` fields v0.107.0 walked past while adding three of
  their neighbours. Both now ride on `TaskOutcome`, into a deterministic
  `_wireup_block` in the founder report (both languages) and a named line in
  the CLI summary.
- **On whose say-so.** `Decision.policy_path` says which human-authored policy
  file authorised a merge or a deploy, and the automation log — the only
  durable record that the machine performed one — dropped it. Now recorded on
  the refusal path too, because *"why didn't it"* is the question an auditor
  opens that file with.
- **A trigger tuple nothing consulted.** `CascadePolicy.escalate_on` shipped
  with `low_confidence` in its default for its entire life while `cascade_route`
  had no confidence input to judge it by. `escalate_on` now defaults to the
  mandatory triggers, `low_confidence` is opt-in, and opting in without
  supplying a confidence **raises** — neither silently clean nor
  blanket-escalating, which turns the cascade off while looking like it is on.
- **Gate PL3 counts its preflights** instead of asserting zero, and names which
  failures are hard — the ones with no override path — in the refusal.
- **The kill criterion states the floor it judged against**, read off the state
  object rather than from a second copy of the constant, including in the
  no-runs branch where a reader is deciding whether the bar is worth clearing.

The allowlist is where the judgment lives: 24 fields are legitimately written
without an in-repo reader, each with a written reason. `test_every_excuse_is_an
_actual_sentence` fails an entry shorter than four words; it caught five shrugs
on its first run. Deliberately a subset check — a field that gains a reader
must not break an unrelated change, because noise is how a check gets disabled.

### ADR-061 — one failure, one column

The same question aimed at run 18's recorded rows rather than the source. Both
findings had gone unnoticed because each moved a headline number in the
direction of **worse**, which is the direction nobody audits.

- **A case that built nothing has no probe reading.** `03-groupbuy-auto` was
  blocked at planning, built 0 of 0 tasks, and was charged a hard `0.0` in the
  probe column as well — one failure moving two of the three headline rates,
  dragging the run's probe rate to 75% when three cases had been probed and all
  three passed. The correct rule was already written verbatim on
  `clean_review_rate`, one property below the one that got it wrong.
- **A probe that cannot parse is our defect.**  `run_probe` compiles every
  probe before running it and returns `harness_fault=True` when it will not.
  `test_every_probe_compiles` already covers probes written into a case file;
  it cannot reach the ones `probegen` writes during a run, which is the larger
  population — case 03 declares none of its own. Harness faults stay in the row
  and leave the denominator, and a case whose every probe was ours has **no**
  reading rather than 100%.
- **The exclusion is per-case and it is printed.** `BenchSummary.no_probe
  _reading` names the measured cases outside the probe average; the CLI prints
  the narrower denominator under the rates and `bench_alert` carries the same
  sentence, because a qualifier that reaches only the operator's screen is one
  the 3am reader did not get. This is the half of run 16's correction that
  ADR-061 keeps while reversing its other half.
- **The worst run stays visible.** Once a nothing-built case has no probe
  reading, a run where *every* case failed writes a null probe rate — and
  `bench_criterion` skipped any run with one. `BenchRun.probe_pass_rate` is
  optional now and `below_floor` judges the floor it has; a run with no
  `build_rate` at all is still skipped, because that one made no claim.
- **`summarise` is a module-level function.** Every rule about what counts
  toward which rate lived in the tail of `_run_product_bench`, unreachable
  without executing a whole bench run — which is why both defects above were
  found by reading result files instead of by a test.

**No re-scoring.** Run 18's file says `probe_pass_rate: 0.75` and keeps saying
it; run 16's numbers stand as recorded. `HISTORY.md` carries both corrected
readings beside the originals, and a test pins run 18's 0.75 against exactly
that temptation.

## v0.108.0 — a check that cannot fire, and the plan that learned to keep it quiet

The one finding ADR-058 named and deliberately left open. `lane_check`
compares tasks in **different** lanes — same-lane overlap is skipped because
`schedule_waves` admits one task per lane per wave, which is true and was
verified rather than assumed. The consequence is that a single-lane plan
cannot collide however many tasks share a file: the check that exists to
protect parallelism is silent exactly when there is none left to protect, and
a planner can clear a real collision by merging the two lanes.

Run 18 scored both sides of that in one run. Cases 02 and 04 collapsed to a
single lane and passed unexamined — case 04 with three tasks all expecting
`app/candidates.py`. Case 03 kept two honest lanes, could not resolve the
collision in three attempts, and built nothing. Two of the three passing plans
passed by removing the parallelism the check protects, and the honest one
died. v0.107.0's own MERGE remedy made that dodge easier to find.

- **`lane_advisories` describes the arrangement; it never refuses it.**
  `run_planning` computes `status="blocked"` from `dag_issues` alone, so a new
  rule there blocks the plan — and a planner handed a bar it cannot clear is
  exactly what killed case 03. There is also no honest deterministic rule
  separating "these are one surface" from "the planner gave up". So the
  advisory names the wave count, states that `lane collision` *cannot be
  reported for this plan at all*, says the arrangement may well be right, and
  offers the hoist if it is not. A test reads the source of `run_planning` and
  fails if an advisory is ever spliced into `dag_issues`.
- **Three readers, none of which can force a revision.** A `minor` critic issue
  under lens `parallelism` in `plan.yaml`; the lane **count** in `plan.md` (the
  task table has always named each task's lane and never how many lanes there
  are); and revision feedback under `advisories_not_blocking` **only when a
  revision is already happening** — the clean-plan `break` comes first, pinned
  by a test.
- **MERGE admits its price.** It stays on `lane_check`'s list of remedies — it
  is often correct — but now says in the same sentence that the two tasks will
  build one after the other, and that collapsing every task into one lane
  silences the check and builds the plan serially.
- **`CaseResult.lanes` keeps the arrangement after the workspace is gone.**
  Establishing that run 18's cases 02 and 04 had collapsed meant opening
  preserved workspaces by hand, and run 18 had already overwritten run 17's.
  The row now records `1 lane(s) over 3 task(s): core` plus the advisory count,
  read from a `plan.yaml` the bench already had.

Run 18's rates are not re-scored: case 04 passed and still passes. What changes
is that run 19's row will say how. The blind spot was confirmed present in the
shipped 0.106.0 build before any of this was written, so it is a property of
the released system and not an artifact of this change's own refactor.

- Suite: 2307 → 2326 hermetic tests (ledger PC-1 synced).

ADR-059.

## v0.107.0 — the run knew, and the record did not

Six findings from inspecting run 17's credit-exhaustion abort and run 18's
preserved workspaces. They look unrelated and they are one defect six times:
the system established a fact, and the place that needed the fact did not
receive it. Five of the six were sitting in the run's own artifacts.

- **A preserved workspace is filed under the run that made it.** It was keyed
  `workspaces/<case>` and `rmtree`'d first, so run N's opening act — per case —
  was deleting the only copy of run N-1's evidence. Run 18 destroyed run 17's
  four workspaces this way, and run 17 was the abort whose forensics were the
  reason anyone would look. Now `workspaces/<run-stamp>/<case>`, stamped once
  at the start of the run and carried in `BenchSummary.run_stamp` so the result
  file and the directory name the same run. Bounded by dropping whole old runs
  (five kept) — a decision about age, which is reviewable, instead of one about
  name collision, which nobody made and nobody could see.
- **An aborted run says so in its contents, not only in its filename.**
  `bench_criterion` has two guards and run 17's file tripped exactly one; copy
  it under a `result-` name and it re-enters the capability series as a
  build-100% reading over 1 of 5 cases. The `aborted:` key is backfilled,
  quoted from the status the run actually died on, and a test asserts every
  `aborted-*.yaml` on disk carries one.
- **Gate 2's reason reaches the bench row.** The test gate downgrades an
  APPROVE deterministically and writes why into `leader.summary`, which nothing
  read — so the one rejection that knows its exact cause arrived as the
  worst-explained. Worse, where a voter was also blocked the row printed "this
  is what rejected the task" about a voter that had not: that claim is about
  `synthesize`'s trigger order and is false when something downstream decides.
  Run 18's `01-groupbuy-api t3` is that row.
- **A voter that asks for a tool is no longer read as a verdict.**
  `_tool_request` returned `None` both for "not a tool request" and for "a tool
  request that would not parse", so an investigation turn went to the verdict
  parser, raised, and burned every retry re-sending an identical prompt for an
  identical answer — landing as `BLOCKED_TOOL_FAILURE`, and two of those on one
  task is a `REQUEST_CHANGES` nothing objected to. **Twelve of run 18's
  seventeen blocked votes were this**, and the cause is one YAML rule: bare `*`
  opens an alias, so `glob: **/*.py` unquoted is a scanner error. The voter is
  now told what broke (twice, then the old behaviour), and the protocol doc
  states the rule up front. This is the review ceiling HISTORY has attributed
  to severity calibration for runs 14, 16 and 18.
- **A change request is judged as a change, not as a product brief.**
  `run_feature` called `assess_fdr` with no product context, so the first-FDR
  bar read everything the request did not mention as *missing* rather than as
  *unchanged*. All three of run 18's follow-up FDRs came back `needs_answers`
  and returned at intake — **the increment axis's 0% is not a reading of the
  gate**, and the row now says so. The assessor gets the relevant requirement
  slice and a feature-scoped bar; an unreadable ledger falls back to the strict
  one, because degrading toward more questions is the safe direction.
- **A lane collision names a legal arrangement.** The message named the
  forbidden one and stopped. `03-groupbuy-auto` was handed it three times,
  produced a materially identical plan each time, exhausted `MAX_REVISIONS` and
  built nothing — while case 01 had already solved the identical collision by
  hoisting the shared file into its own task. HOIST, MERGE and SPLIT are now
  spelled out with the actual ids and globs, once per colliding pair.

Also closed, by measurement rather than by argument: run 18's `24.97s → 0.31s`
test-suite collapse is real (the preserved suite re-runs in 0.45s), not a
reporting artifact.

- Suite: 2276 → 2307 hermetic tests (ledger PC-1 synced).

ADR-058.

## v0.106.0 — the bench could never say what it cost

Every product-bench result file records `duration_s`. Run 17's row says a case
took 3438 seconds and then died. No file in the series says what those seconds
bought — and that is the number that decides whether to run it again.

The data was never missing. `spend.record()` meters every provider call;
`autopilot` flushes the ledger into the workspace root, so the rows are already
attributed to the case by construction; `run_case` then deletes that tree in a
`finally`. The answer was written to disk and thrown away, once per case, for
the whole life of the bench. ADR-051's shape again: cost metering is read back
by `build`, `autopilot`, `graph`, `studio`, `gepa`, `smoke` and `cli`, and was
not read back by the one path that spends the most.

- **A bench run now reports what it cost.** Per case and summed for the run —
  on the CLI under the rates, in the Discord alert (whose docstring already
  argued this run costs "real money on the founder's own key", and then
  reported only the hours), and as a `cost` block in the result file. Outside
  `rates`, because cost is not a rate and `bench_criterion` reads that block.
- **Unpriced is not zero.** `usd` is `None`, never `0.0`, when no price covered
  the models a case used — ADR-053's rule applied to money. `unpriced_calls`
  travels with it, so a partially-priced total announces itself as a FLOOR.
- **Prices come from the operator's repo.** The token ledger lives in the
  case's throwaway workspace; the price table lives in `.mas/cost-model.yaml`
  where `avs prices --import` put it. A `mkdtemp` directory has never held an
  operator's prices and never will, so pricing a case against its own `.mas`
  would report every call unpriced forever.
- **A crashed case still reports its spend**, via the same channel the
  preserved-workspace path already uses. That is where the question matters
  most: run 17 spent 3438 seconds and died, and the money was as unrecoverable
  as the measurement.
- **A resumed row contributes nothing** to the run total — its cost was paid by
  the run that measured it, and counting it twice would inflate the series in
  proportion to how often a flaky run was resumed.
- **The total counts cases the rates exclude**, deliberately opposite to
  ADR-035: a case that crashed still spent money, and a total that dropped it
  would answer "what will this cost me next time" with a number that has never
  been true.
- No cap and no refusal (ADR-032 stands), and no prices in code.

See [ADR-057](docs/adr/057-the-bench-could-never-say-what-it-cost.md).

## v0.105.0 — a reading that cannot name its instrument

Run 17 is blocked on API credit, so the question was what can be validated
without it. The cheapest substitute is `avs product-bench --provider mock` — a
documented option, free and offline. Before running it, one check, because
ADR-054 had just established what `benchmarks/results/` is for:

> Can a simulated run enter the ledger the capability kill criterion reads?

It could, and it was indistinguishable from a real one once there. Nobody had
run it, so nothing was corrupted; it was safe by accident.

- **A result file now records the provider that produced it.** `provider:`
  sits beside `avs_version` and exists for the same reason — a row that cannot
  name what produced it cannot be compared to the row above it. Recorded on
  every run, not only simulated ones: a field that appears exactly when
  something is wrong is a field nobody thinks to look for.
- **A simulated run is not dual-written into `benchmarks/results/`.** It still
  writes `.mas/product-bench/`, because a mock run *is* a real exercise of the
  harness and the scoreboard is the output of that check.
- **`bench_criterion` refuses to count a simulated result that reaches the
  directory anyway** — hand-copied, restored, written by an older build — and
  *names* it, the rule the aborted-run list already established. Two layers,
  because each is silent about the other's case.
- **A file with no `provider:` key is read as real.** The eleven results
  already in the series predate the field and every one of them was a genuine
  run. Reading absence as "simulated" would have deleted the entire recorded
  series from the criterion's view.
- **The other two readers of the same numbers, closed with it.** `cadence`
  globs that directory itself to answer "has the bench run lately" — a
  simulated file would have made it report *ran today, all clear* about a run
  that read nothing, which is the failure its own `LOOP_NAMES` comment says a
  watchdog must never commit. It now asks `bench_criterion` which files to
  skip rather than re-deriving the rule. And `bench_alert`, which fires even
  on a clean run because somebody is waiting on the weekly number, marks a
  simulated run in the heading — the part a phone notification shows.
- **`SIMULATED_PROVIDERS` lives in the provider registry and nowhere else**,
  pinned by a test. A second copy is a second definition of "real run", and
  the thing the two would drift about is which files decide whether a human is
  asked to consider killing the project (ADR-038, ADR-051).
- **The command says so on screen**, not only in the file: *"provider 'mock'
  is simulated — these rates measure the harness, not the system."*

With that closed, the free path is safe to use: the full product-bench harness
— autopilot, build tasks, independent probes, review passes, checkpointing,
result file — runs end to end against `--provider mock` in well under a minute
per case, with no key and no spend. That is where every recent defect actually
lived (ADR-052, -053, -054, -055; not one needed a provider). What it cannot
substitute for is the capability reading itself: build ≥ 60% / probes ≥ 50%
measured against a mock is a measurement of the mock. Run 17 is still run 17.

ADR-056. No contract removed; `save_summary` gains a keyword-only `provider`
argument, `bench_criterion._scan` a third return value, and
`BenchCriterionState` a `simulated_skipped` field.

## v0.104.0 — a name that resolves nowhere

ADR-054 closed by observing that *running* `avs bench-criterion` found in one
invocation what an audit of the module could not. True, and incomplete. The
more useful question was why neither the audit nor the suite could have found
it, and the answer generalises past that one command:

> A test proves that the code it calls works. It says nothing whatsoever
> about code that no test calls.

The orphaned `streak_state` block was unreachable from every one of 2229
hermetic tests. No coverage target reaches it, because coverage measures the
code you ran. `ruff check` reads every line whether or not anything runs it —
a different instrument, not a better test, and this project had none.

- **A security boundary typed against a class nobody imported.**
  `MCPHost.__init__` annotated `taint: "TaintGuard | None"` and no import of
  `TaintGuard` existed in the module. Lazy under `from __future__ import
  annotations`, so it never raised — it meant the declared type on the
  risk-tier RBAC boundary (doc 11 §17.3) was verified by nothing, and
  `typing.get_type_hints()` raised `NameError`. Confirmed against the
  deployed v0.103.0 before the fix. Imported at runtime rather than under
  `TYPE_CHECKING`: with postponed annotations already in force, the deferred
  import satisfies the linter and leaves `get_type_hints()` raising the
  identical error — the defect preserved and the report silenced.
- **A guard whose failure message could not be printed.**
  `test_every_stage_command_enforces_its_floor` formats `{floor.name}` into
  its assertion message and `floor` is not in scope. That test guards eight
  stages against running below their infrastructure floor (ADR-U15); the
  message is built *only when the assertion fails*, so on the day it caught a
  real regression it would have died with `NameError` instead of naming which
  stage ran where. Now `STAGE_FLOORS[stage].name` — and the first draft of
  that fix rendered `build`, which is the command's argv and not its stage
  key, trading the `NameError` for a `KeyError`; the test now renders every
  stage/rung pair rather than one sample.
- **`ruff check src/ tests/` in CI, `F` rules only.** Pyflakes rules describe
  code that cannot work, not code someone would format differently. Style
  linting stays out: several hundred reformatted files is how a gate becomes
  something people scroll past, and `select = ["F"]` is pinned by a test so a
  later widening has to be deliberate.
- **Both workflows run it, and a test reads both files.** `publish.yml` says
  in a comment that it runs "the same gate as ci.yml" and then assembles it by
  hand a second time — one control with two call paths (ADR-051), aimed at the
  release path.
- **The suite runs it too**, skipping when ruff is absent, the same
  `shutil.which` pattern the git-dependent suites already use. A gate that
  lives only in CI teaches the author to learn about it from a red workflow
  after the push.
- 85 mechanical findings cleared (dead imports, vestigial `f` prefixes, four
  unused locals). `F401` is ignored in `__init__.py`: a package `__init__`
  exists to re-export, and `lanes/__init__.py` computes `__all__` from
  `dir()`, which no linter can follow — its 47 "unused" imports are the lanes'
  public surface.

**Not claimed:** that either undefined name ever caused an incident. Neither
did `streak_state`, until it did. The claim is narrower — all three were
invisible to the full suite and to a careful reading, and visible to a 300ms
mechanical check the project did not have.

## v0.103.0 — the criterion must survive being read

ADR-053 closed a defect class by auditing every aggregate in `src/` that
divides by a count. That audit read code. Asked to close whatever remained,
the check this time was to RUN the project's own reporting commands against
the real repository — and `avs bench-criterion` printed its report and then
crashed with `NameError: name 'streak_state' is not defined`.

Three defects sat in the twenty lines between the bench result files and the
human who reads them at Gate PL5, all of them ADR-051's shape: a writer added
something, and a reader that had documented its assumptions was not updated.

- **The command crashed on the healthy path only.** Ten orphaned lines from
  the implementation `evaluate()` replaced, calling a `streak_state` that
  exists nowhere. Unreachable in exactly one case — a fired criterion raises
  `typer.Exit(3)` above them — so across eleven recorded runs the command that
  reads the launch PRD's only kill criterion had never once completed, and
  would have "worked" only in the case where the project is in trouble.
  Nothing caught it because **no test invoked the command**; `evaluate()` had
  coverage, the CLI path around it had none.
- **An aborted attempt counted as a run.** `save_summary` writes `aborted:`
  above the rates and says why — "four cases failed" and "this run never got
  to ask them" are different findings whose percentages look the same — and
  the one reader where the difference decides something never looked. Run 17
  died on credit exhaustion after one case and sat in the capability ledger at
  build 100% over 1 of 5. Harmless only because it scored well: inverted, an
  exhausted billing account advances a streak that asks a human to consider
  killing the project. Excluded now by two independent guards, the glob and
  the content key, because they catch different mistakes.
- **The ledger's stated ordering was broken by a filename.** `load_runs`
  promised "oldest first, by filename (they are timestamped)", which holds
  only while every name shares a prefix; ADR-052 added `aborted-*.yaml` and
  `a` sorts before `r`, so the newest file was placed at the oldest position.
  The glob was `*.yaml`, so any stray file in that tracked directory would be
  parsed as a capability reading.

Excluded is not invisible: aborts are still walked, reported in
`BenchCriterionState.aborted_skipped`, and named by the CLI with the command
that would finish them. `avs cadence` now prints the build each reading came
from (`· measured on v0.93.0`) — its `state` column measures staleness in days,
and days is a proxy that breaks exactly when releases outpace the cadence: the
bench row read `ok (4d)` while its numbers were nine releases old. Stated, not
judged — no threshold, for the same reason `SchedulerBuild.behind` only counts
older builds.

Floors, streak and `below_floor` untouched for the second release running.

`tests/test_the_criterion_reads_its_own_ledger.py`, 10 tests; 6 of the first 8
fail against the deployed v0.102.0 as a control. No recorded rate changes —
the run-17 abort was above both floors, so removing it from the ledger moves
no streak and no number. What changes is that it cannot move one in future.

## v0.102.0 — a rate over no cases is not a rate

Found while pricing a cheaper alternative to a full five-case bench run:
run only the increment case, pay ~2h instead of ~5h, and get the `gate_rate`
that has never been measured. That run is well-formed — but its build axis is
empty, and `_avg([])` returned `0.0` rather than `None`.

Which meant it would have been recorded as `build_rate: 0.0,
probe_pass_rate: 0.0`, entered the capability ledger `bench_criterion` reads
(`save_summary` dual-writes to `benchmarks/results/` automatically), come out
**below floor**, and left the project one run from firing a criterion whose
consequence is a human decision at Gate PL5 about whether to continue — over
a run that never asked whether anything builds. `cadence._bench_rates` would
have reported it as a flat, unqualified "build 0%, probes 0%", because its
"over N of M cases" qualifier only appears when `measured < total` and `0 < 0`
is false.

This is ADR-035's rule broken one level above where ADR-035 enforced it.
`CaseResult.build_rate` has returned `None` for a case with no denominator
since that record; `BenchSummary` was typed `float` and flattened it back to a
zero. `gate_rate` was already `float | None` for exactly this reason.

- `_avg` returns `None` for an empty set; `BenchSummary`'s three headline
  rates are `float | None`; the saved file writes `null`, not an omitted key
  (an absent key is indistinguishable from a pre-field file, and the tracked
  scoreboard holds reconstructions back to run 4).
- `bench_criterion.load_runs` and `cadence._bench_rates` check for null
  explicitly. Both were already correct — but only because `float(None)`
  raises `TypeError` into a handler written for malformed files. Accidental
  correctness is not protected by anything.
- A rate with no denominator prints as "not measured" in the CLI table and in
  the alert, the idiom `gate_rate` already used four lines away.
- Floors, streak length and `below_floor` are untouched. What changes is which
  runs are eligible to be judged, never the judgement.
- Every bench claim in `claims/platform.yaml` now names the build that
  produced it (run 13 = v0.83.0, 14 = v0.86.0, 15 = v0.88.0, 16 = v0.93.0),
  and PC-17, README and `benchmarks/results/HISTORY.md` record that run 16 is
  the newest reading and is **not** a reading of this build: the 16 → 17 gap
  spans nine releases (v0.94.0 through this one), so ADR-044 through ADR-053
  all move at once and no
  single record can be credited or blamed for the delta.
- Every aggregate in `src/` that divides by a count was audited for the same
  shape, the way ADR-050 followed ADR-048. Nine were already safe and mostly
  safe on purpose; one was not. `product/loop_metrics.py` returned `0.0` from
  `evidence_quality_ratio` for a stage with no claims and from
  `hypothesis_resolution_rate` for a loop with no hypotheses — while
  `kill_rate` and `attention_cost_per_resolved_hypothesis`, in the same file,
  returned `None` and said why. Both now return `None`. Both defects sit one
  layer *above* per-item code that already got it right, which is the finding.

`tests/test_empty_axis_is_not_a_zero.py`, 13 tests in three groups. The
second group is the one that matters — a fix that blinded the criterion would
be worse than the defect: a case that built nothing still scores a real `0.0`,
a genuine 0% still fires the floor, and a mixed run keeps the build rate it
earned. 5 of the first 9 fail against the deployed 0.101.0 as a control; the 4
that pass are that group plus the cadence check this makes explicit. No
historical rate moves — every run 1–17 had at least one build case.

## v0.101.0 — a measurement is bought once

Run 17 measured one bench case over 3438 seconds of real spend, lost its
account mid-case-02, and left nothing a later run could use. ADR-052 fixes
the three reasons why.

**A finished case is banked before the next one starts.** `save_summary` runs
once, at the end, from the CLI — so run 17 kept its case-01 row only because
the loop happened to reach the end. ADR-036 kills the whole process group at
`BENCH_TIMEOUT_S = 8h` and run 16 already used 2.97h of it before a fifth case
was added, so a timeout destroys every finished case with it. Each measured
case now lands in `.mas/product-bench/checkpoints/` immediately,
write-then-rename so a kill mid-write leaves nothing half-read. Crashed cases
are deliberately not banked: that would make a transient 529 permanent.

**`--resume` reuses a banked row only when it is still true** — case name, a
digest of the case file's whole content, `avs_version` and provider must all
match, and every rejection path ends in the case simply running.
`autopilot._todo_and_skipped` keys on `(task_id, title)` rather than the id for
the same reason, and already said why: *"skipping work is only safe when we can
say what work it was."* Cross-build reuse is refused outright rather than
offered behind a flag — reusing a 0.97.0 row inside a 0.100.0 run would average
two machines into one scoreboard, which is the confound ADR-049 narrowed
`cases_total` to prevent, arriving through the optimisation meant to save
money. A resumed row says so in the table, in the row and in the result file's
`rates.resumed`, because every number in it is real enough to hide it. A resume
reaches back at most 14 days, enforced as a **read** rule rather than a cleanup
pass — deleting inside `.mas/` is the one thing this repo does not do, and
refusing to read a stale checkpoint gets the whole benefit while leaving the
file for whoever is diagnosing the run it came from.

**"One case never kills the bench" is narrowed for the first time.** True for
a hung suite or a 529 that outlived its retries; false for an account with no
credit, which killed cases 03, 04 and 05 at 0.3s each after case 02 spent 1541
seconds finding out. A terminal environment failure now aborts the run and the
untried cases are recorded under one shared reason. Two detectors: a table of
provider wording and statuses 401/402/403, pinned against the verbatim string
from the aborted result; and **two consecutive cases failing identically**,
which needs no vocabulary and keeps working after the provider rewords its
errors. 429 stays excluded — it is transient and already retried six times.

**A preflight spends one token before the run spends hours.** It lives in the
CLI, not in `run_product_bench`: the library function is what the hermetic
suite drives, and a network call inside it would need a flag defaulting to off
— the ADR-051 shape, a guard on the path nobody takes.

Verified end to end on the mock provider: a one-case run measured in 48.8s
returned the identical row in 0.62s under `--resume`. 2206 hermetic tests.

## v0.100.0 — a second path is not a weaker one

v0.99.0 found one job written seven times. This one found two controls
written **once**, on the path fewer runs take — the sibling failure, and the
harder one to see, because nothing is duplicated. The second path simply does
less, and its output has the same type as the first's. ADR-051.

**`--parallel` builds were never reviewed.** `_attempt_task` runs spec →
build → `review_and_repair`. `_build_wave_parallel` ran spec → build → merge
and recorded `TaskOutcome(status="built", review_verdict=None)`. So a founder
who passed `--parallel` got modules no reviewer had ever looked at, in the
same report table as sequentially-built ones carrying a real verdict, with
nothing in the row distinguishing them — and without `iterations`,
`files_written` or `test_summary`, the three fields ADR-042 added so a row
carries its own diagnosis. This is the hole `retry-task` shipped with, the one
`review_and_repair` was extracted to close, whose docstring already says *"A
retry is not a lesser build."* It survived because the wave loop is
hand-written rather than routed through `_attempt_task` — the same reason the
retry paths were wrong before they were merged.

Fixing it made a latent bug reachable: `finalize_build_bookkeeping` ran after
the merge commit and left `built: true`, the changelog fragment and the ledger
sync **uncommitted**, and `_fix_iteration`'s rollback runs
`git checkout -- .`. `build.py` had learned this one commit earlier ("BEFORE
the commit, not after"), where the cost was a resumed run re-paying for
modules it had already built. The merge is now `--no-ff --no-commit` with the
bookkeeping written into the merge commit itself, so `HEAD~1..HEAD` is still
the whole merged branch — which is what `_review_head` reviews.

**ADR-U03 taint isolation was switched off on the default transport.**
`build_toolbox` passed `voter` and `risk_ceiling` to `MCPToolBox` and dropped
both for the in-process `ToolBox`, which constructed no `TaintGuard` at all —
and `tool_transport()` returns `in_process` unless
`AUTOPRODUCT_TOOL_TRANSPORT=mcp`. Nothing was exploitable: every tool in
`VOTER_TOOL_REGISTRY` is L0 read-only and repo-scoped, so a tainted session
had no L1+ call to make. **That is the finding.** The guarantee held because
the table was short, not because anything checked it, and the first person to
add an L1 tool would have removed it without touching a line of security code.
`mcp/toolbox.py` already carried a comment recording that this exact pair was
"implemented on both sides and never connected" once before.

`ToolBox` now enforces both at the same two points `MCPHost` does and against
the same tables: the ceiling filters the allowlist at construction, every call
is authorized, every result is watched for a research wrapper. A denial comes
back as data — a voter degrades on one, a raise would take down the review —
and does not spend the budget, because a refused call is not a call.
`build_toolbox` passes the same four arguments to both branches.

Also: ADR-038's `ROLLBACK_SEVERITIES ⊂ ACTIONABLE_SEVERITIES` is checked at
**import**, not only in the suite, and `ACTIONABLE_SEVERITIES` is frozen. A
test catches drift at CI; an edit made and run without the suite is the case a
test cannot reach, and this file is edited by the same machine it drives.

Guarded by two new suites, both run against the previously released build as a
control — 4 of 6 and 7 of 8 fail there and pass here, and the ones that pass
in both are the negative-path tests, which is what they should do.
`test_both_build_paths_route_through_the_same_review` scans both loops' source
so a third build path added later fails a test rather than shipping
unreviewed; `test_every_voter_tool_is_l0_so_the_default_ceiling_admits_them_all`
turns the property the old code was accidentally relying on into an assertion.

2177 hermetic tests.

## v0.99.0 — one tokenizer, imported everywhere

v0.98.0's changelog recorded a gate that was inert in Chinese. That entry
treated it as one bug. It was seven.

Auditing the repo turned up **seven** places that split text into tokens.
Four had independently learned that Chinese has no spaces and an ASCII-letter
rule therefore finds nothing in it — two of them carrying their own `[一-鿿]`
range, written from scratch, neither covering the Unicode extension blocks.
Three had not, and their state was measured rather than guessed:

- `textsim.similarity(x, x)` returned **0.0** for two identical Chinese
  strings where identical English returned 1.0. All three consumers compare
  `>= threshold`, so it failed **open**: opportunity dedup never deduplicated,
  the kill registry's Novelty match never fired, and the marketing
  near-duplicate rule never tripped. A gate reporting success while doing
  nothing is worse than the one ADR-048 found.
- The incident-to-commit correlator scored the empty set on Chinese incidents.
- The claim-register match found no content words, so substantiated Chinese
  copy was reported as unsubstantiated.
- The thin-page check counted a Chinese page as **zero** words, so a batch of
  substantial copy tripped the ratio at 100%.

`ai_venture_studio/lexicon.py` is now the only place in this system a
tokenizer is written, and all seven sites import it. CJK is detected by
Unicode name rather than a range literal. Stopword lists and length floors
stay per-caller, because those are genuinely different jobs and merging them
is ADR-038's scar — but the length floor never applies to CJK, since four
characters of English is a word and four characters of Chinese is two.

Guarded structurally: an AST walk over `src/` fails on any new
`findall`/`finditer`/`split` whose pattern is bare character classes, and —
because a guard that cannot fire is exactly the defect this release is about —
the detector is itself driven against the five patterns that shipped blind and
the four format-parsers it must leave alone.

ADR-050. 2163 hermetic tests.

## v0.98.0 — the refusal is measured, on its own axis

ADR-046 gave this product its only refusal: a new request is read against what
the product already promises, and it is either a duplicate (nothing is built),
a contradiction (a person decides; under `--yes` the build proceeds and the
clash is recorded as an unapproved SCR), or a real addition. **Nothing measured
it.** The product bench asks one question — can it build what was asked — over
four cases that all ask for a product to exist, and in that reading a gate that
never fires scores exactly like a gate that fires correctly.

ADR-048 showed what that costs: the gate shipped in v0.96.0 inert in Chinese,
never firing and never erroring, and run 17 would have recorded it as working.
The one thing this bench could not see was **a refusal that did not happen**.

**The increment axis (ADR-049).** A case now declares `axis: build` or
`axis: increment`. The four real cases that have always produced the headline
series stay on `build` and remain the only contributors to build rate, probe
pass rate and clean review rate; increment cases score a separate **gate rate**
with its own denominator, its own `unmeasured` list, and its own
`rates.increment` block in the saved result. `avs product-bench`, the Discord
bench alert and the cadence one-line read each report it on its own line —
never folded into the three, because a reader shown one number cannot tell
which question it answered.

Separate rather than combined for the reason ADR-035 gives about denominators:
an increment case whose *correct* outcome is `already_satisfied` builds nothing
on purpose, and averaged into the build rate that is a `0.0` — the same score a
case earns for failing outright.

**A case declares its expectation before the run.** `feature_expectations`
pairs by position with `feature_fdrs`, one of `already_satisfied` /
`raises_scr` / `completed`, and a mismatched length or unknown value is refused
at **load** rather than after hours of wall-clock. An expectation written after
the run is not a measurement.

**A raised SCR outranks the status, both ways.** Under `--yes` a contradiction
proceeds to build, so its status reads `completed` — identical to a gate that
never fired. The harness therefore reads `.mas/scr/SCR-*.yaml` for a newly
written `status: proposed` entry instead of trusting the status. And a
follow-up expected to be a clean addition that instead raised a contradiction
scores `raises_scr` and is **wrong**: a gate that fires too often is a different
failure from one that never fires, and both have to be visible.

**The real case is Chinese and asks all three questions.**
`benchmarks/products-real/05-increment-repairs.yaml` builds a small 报修 backend
whose FDR promises on purpose that a submitted repair cannot be deleted, then
sends three follow-ups in the founder's own later vocabulary: the same promise
reworded, deleting one's own repair, and rating a completed repair. Chinese
because that is the language the gate was inert in while every English test
passed; all three because a gate that only ever says no would score 100% on a
case that only ever asks it to say no.

**Changed, and named:** `cases_total` in a saved result is no longer every case
file in the directory — it is the build-axis count. That narrowing is what
stops a fifth case silently redefining every rate in the run-13..17 series.
The gate rate gets **no floor and no kill criterion**: run 18 is its first
reading, and a threshold set against zero observations is a number invented to
look rigorous.

## v0.97.0 — the founder describes the product; the system proposes the increments

v0.96.0 gave the product a memory of what it *promises*. Two things were still
being asked of the founder that the machine can do, and one thing the founder
could not see at all.

**The constitution (ADR-047).** Section 4 of the FDR template — "暂时不要的功能
/ NOT needed for now" — has been in the template since the beginning, the
Studio composes it as its own slot, the template explains that writing things
there stops them being built by mistake, and **nothing has ever read it**. It
reached the first planner mixed into the FDR blob with no rule attached, and
after that it vanished: `avs add` plans against the code, the ledger and the
reconciler, none of which knows that in February the founder wrote "暂时不需要
在线支付". So the tenth feature grows a checkout.

- `product/constitution.yaml` — an append-only `C-001` ledger of what the
  founder ruled out, **derived from a document they already wrote**. Never a
  typing chore, because a file the founder maintains by hand goes stale in
  silence.
- **Extracted deterministically — no model call.** A model asked to summarise
  this section can invent a boundary the founder never drew, and an invented
  invariant is shown to every future plan as something they decided. Bullets
  and numbering stripped, the templates' own parenthetical examples skipped
  (including the English one, which wraps across two lines), "无"/"none" read as
  *there are none*. The section is found by its **number**, because the two
  templates and the Studio composer write three different wordings after it.
- **Reconciled per origin.** A feature FDR can add to or withdraw from its own
  lines and nothing else — a derivation that read silence as repeal would empty
  the constitution on the first feature.
- **Shown whole to every planner**, not retrieved by keyword: a "do not build
  this" list is short and every line applies to every plan, so slicing it would
  hide the invariant the request is about to violate. The cap of 20 is a
  backstop and it announces what it dropped.
- **It gates nothing.** The planner is told what was ruled out *and* told that
  if this request asks for one of those things, the founder has changed their
  mind and the request wins. A boundary that refuses the person who drew it is
  a bug; this product's one refusal path stays ADR-046's.

**The roadmap (ADR-048).** "One FDR = one thing" is the rule this system runs
on, and it has always been enforced on the *founder*: arrive with a paragraph,
then split it into twelve small documents yourself, in the right order, each
one small enough. That is unpaid labour, it is what a non-technical person is
worst at, and it was the last manual step in a pipeline whose premise is that
everything downstream of an FDR is automated.

- `avs roadmap "..."` — a paragraph becomes 3–12 ordered steps, each a request
  `avs add` can take as written, in the founder's own vocabulary.
- **A proposal, not a contract.** `avs roadmap` with no argument re-reads the
  remaining steps against the ledger through the ADR-046 reconciler, so a step
  the product now satisfies is marked done rather than built twice. A stale
  roadmap the system still believes is worse than none — it has an
  authoritative shape with a wrong answer inside it.
- **Done only when something says so.** An unchecked reconciliation leaves the
  step pending and is *named* as unchecked; steps past the re-check cap are
  reported, not counted as pending. Marking work done because a check failed to
  run would silently delete a feature from the founder's plan.
- **Order is repaired, loops are refused.** A prerequisite listed second is a
  wrong order — topologically sorted. A cycle has no order that builds it —
  refused. An edge to a step that does not exist is dropped, or `next_step`
  would wait forever for something nobody proposed.
- The whole loop is two commands and no documents: `avs roadmap`, then
  `avs add FDR-NEXT.md --yes`, repeat.

**The view and the baseline (ADR-048).** At two hundred promises the founder
needs to *see* what they have before adding to it, and nothing showed it.

- `avs requirements` — live promises grouped by the part of the product they
  belong to, each with its status and the test file that checks it; `--all`
  includes retired and superseded ones.
- **Every `tag_checkpoint` freezes a baseline**, so the view ends with "since
  ap-checkpoint-004: +6 promise(s), 1 superseded". The stored value is the
  id→status map, not a hash: a hash answers *did anything change*, and the
  question a founder asks is *what* changed.
- A checkpoint with no baseline reports **nothing**, not "no change" — every
  checkpoint tagged before this release is one of those. A baseline that will
  not write does not cost the checkpoint: the tag is the founder's undo.

**Fixed: v0.96.0's gate was inert in Chinese (ADR-048).** Building the
roadmap's re-derivation on top of the same retrieval is what surfaced it.
`requirements.tokens()` used one regex that requires an ASCII letter; Chinese
has no spaces, so it found **nothing** in a Chinese criterion. Every score was
zero, `relevant` returned an empty slice, and the ADR-046 duplicate /
contradiction gate therefore reported *"no existing requirement matched this
request"* for every request in the language this product's founder actually
writes in — the templates are bilingual, the Studio ships Chinese by default,
and every case in `benchmarks/products` is Chinese. The gate was not wrong, it
was inert, which is the hardest kind of broken to notice: it never fires and
never errors. Fixed with character bigrams unioned into the existing Latin
rule (no segmenter, no new dependency, no dictionary to go stale), and
retrieval is now tested in both languages — ADR-046's tests were English
against an English ledger, which is why they all passed.

## v0.96.0 — a requirement has a name, and a new one is read against the old

The founder asks for one more small thing. Until now `avs add` planned it
against the *code* and never asked the question a person asks first: **do we
already promise this, and does it fight something we promised before?** Run
16 recorded the cost in a reviewer's own words — *"the exact anti-pattern a
prior task in this repo was already dinged for"*. The reviewer remembered
across tasks. The builder had no way to: `run_feature` told the planner the
*names* of the previous feature directories and nothing about what any of
them promised.

Every mechanism for holding a promise steady stopped at one document.
`ears.py` grammar-checks a criterion, the SCR channel freezes a built spec,
`covers` traces a test to a criterion — all inside one spec file. A criterion
was identified by its position in a list, so nothing outside the spec could
refer to it at all.

**The ledger (ADR-045).** `product/requirements.yaml` gives every acceptance
criterion in the product a permanent `R-001`-style id, with its text, spec,
status, originating FDR, and the test files that verify it.

- **Derived, never hand-maintained.** `sync_ledger` runs from
  `finalize_build_bookkeeping` — the one place `spec.built = True` happens —
  so it cannot drift by being forgotten.
- **Keyed on text, never on index.** Matching on position is the exact defect
  this exists to prevent: a spec regenerated under an approved SCR would keep
  `R-007` pointing at slot 7 while slot 7 now holds a different promise.
- **Append-only.** Ids come from `max(ever seen) + 1` counting retired
  entries, so an id is never reused and an old reference always resolves. A
  criterion that returns to its spec is live again under its original id.
- **`retired` ≠ `superseded`.** Derivation may retire (it observed the text is
  gone); only an explicit decision may supersede, because a derivation cannot
  know what replaced something. `superseded_by` names the replacing FDR, not a
  requirement id — a feature that replaces one promise usually adds several.
- **The planner is shown what its request touches.** A capped, retrieval-scored
  slice, which states what it dropped (ADR-039), and a prompt rule saying the
  requirements it was not shown still exist.
- **Provenance is attributed after the fact.** The first sync on an existing
  product backfills hundreds of criteria; stamping the current FDR on them
  would be a record of a decision nobody made.

**The gate (ADR-046).** Between retrieval and planning, one model call
classifies the request against only the retrieved slice — so the check does
not get more expensive or less accurate as the product passes 300 promises.

- **Three relations, no `unclear`.** Uncertainty falls through to `extends`,
  the pre-existing behaviour. A gate that stops on its own uncertainty stops
  constantly, and `ears.py` carries that scar.
- **`checked` is separate from `relations`.** Empty + checked means "nothing
  conflicts"; empty + unchecked means "nobody looked". Truncated, unparseable,
  nothing retrieved, and named-no-shown-requirement are all unchecked — the
  ADR-041 shape, in the one place it would hurt most.
- **A duplicate refuses the build in both modes**, writes
  `FDR-ALREADY-BUILT.md` naming the promise *and the test file that proves
  it*, and **exits 0**: a non-zero code would train every wrapper to read a
  correct answer as a failure.
- **A contradiction stops for a person** (exit 2, `FDR-DECISION.md` showing
  `avs add <fdr> --replace R-0xx --yes`), and under `--yes` proceeds while
  raising an SCR it deliberately never approves. `--yes` authorizes the build,
  not the retirement.
- **Only `--replace` supersedes, and only after every task builds** — mirroring
  `apply_pending_amendment`: old promise retired plus new one unbuilt is a
  product that promises strictly less than before.
- `already_satisfied` is not a bench failure; scoring a correct refusal as one
  would make redundant work the only way to pass.

`--yes` is narrowed for the first time: "do not stop for me about the *plan*".
`product/requirements.yaml` joins the enumerated contract surface, and `avs
add` can now refuse a request it previously always built — minor, not patch.

ADR-045, ADR-046.

## v0.95.0 — a repair lands whole, or lands nothing and says why

Run 16's clean-review rate fell to **31%** from run 15's 55%, and six of its
eleven rejections read *"a fix was attempted and rolled back — it did not
clear the review"*. The sentence was usually false: `_fix_iteration` could
fail six ways and printed that one line for all of them, four of which never
reached a review. So "the reviewer got stricter" and "the repair pass stopped
working" were identical in the record.

It was the second one, and the cause is reproducible in three files. Run 16's
blocking findings are dominated by duplication complaints against test files
(*"test boilerplate duplicated verbatim across six new test files instead of a
shared fixture/helper"*). The repair is always the same — hoist it into a
helper, call it from the sites — and `assertion_delta` judged one file at a
time, so every assert moving into the helper read as `removed_assert`.
`_write_files` dropped all six call sites and kept the new helper. `written`
was non-empty so nothing noticed, the untouched originals still passed the
suite gate, and the half-change was committed. The re-review then saw the
duplication still there **plus an orphan helper nothing called** — *"Unused
alias function diverges from spec's stated call path"* (03-t5). The repair
pass was manufacturing the findings that rejected it.

- **The write-guard judges the batch.** `assertion_delta(..., elsewhere=)`:
  an assert that leaves one file and lands unchanged in another written in the
  same response has moved, not been deleted. An assert that appears nowhere in
  the batch is still a removal, and `added_skip` is never forgiven by
  relocation — the reward-hacking defence is intact.
- **A partly-refused repair is applied to none.** `_write_files`' `kept` list
  was discarded on the line that produced it; it now reverts the batch and
  names the refused files.
- **Seven failure paths, seven reasons.** `_fix_iteration` returns
  `(landed, after, why)`, and an AST walk rejects any `return False` without
  one. It also checks `last_response_truncated()` — every other writer stage
  has since ADR-041; this was the one that never did.
- **A discarded repair no longer sets the verdict.** `04-t3` was recorded
  ESCALATE_SECURITY_RISK for *"input validation removed"*: the repair removed
  the guard, the rollback restored it, and it was in the delivered code the
  whole time the scoreboard said it was gone.
- **A case with a rejected task keeps its workspace.** All six rolled-back
  tasks sat in cases that completed with every probe passing, so not one
  review survived to be read.

Checked and wrong, recorded because it was checked: the repair pass was *not*
truncating. Preserved ledgers from runs 14–15 top out near 8k output tokens
against a 16384 cap.

ADR-044. `ROLLBACK_SEVERITIES` is untouched.

## v0.94.0 — a case is measured or it is not

Bench run 16 reported **build 100% · probes 75% · clean 31% over 3 of 4
cases** and closed with the line the harness prints for every exclusion:
*excluded from the rates above, not scored as zero*. That sentence was true
of two rates out of three, and the third rate was the headline.

`02-shortener-api` planned for 6.4 minutes, revised twice, came back with
no tasks, and was named in `unmeasured`. Its two probes ran anyway —
against a workspace with no product in it — and entered the probe average
as a real `0.0`. Exclusion was decided per *rate* (`unmeasured` came from
`build_rate is None`; each rate averaged whatever it happened to have), so
one summary could exclude a case from two rates and count it in the third.
This is run 12's error, in the same file, after ADR-035 was written to fix
it.

**The larger half is wrong the other way, and it moves the headline.** Case
02 did not crash — it ran, and the machine failed to produce a product.
ADR-035 already said in words what that is worth ("a case that ran and
built nothing still scores a real 0.0"); the code read `tasks_total == 0`
as "no denominator". So `build 100%` was the rate over the cases that got a
plan, not over the cases that were asked for a product. **Read honestly,
run 16 is `build 75% · probes 75% · clean 31%` over 4 of 4.** Its recorded
numbers are not rewritten; `HISTORY.md` and PC-17 carry the corrected
reading beside them, and the build rate's comparability breaks at run 17.

`measured` is now one decision on the case — `not
autopilot_status.startswith("error")`, only the harness dying counts — and
every rate reads it. `clean_review_rate` stays `None` when nothing was
built: unlike the build rate it has no denominator to be a zero *of*, and
entering it as 0.0 would charge the same failure twice.

Two defects follow from the same run, and they are what keep the new `0.0`
from being an unexplained zero:

- **A blocked plan carried no reason.** `run_planning` came back `blocked`
  and the autopilot turned that into `status="failed"` and nothing else —
  six minutes, no product, the single word *failed*. The cause was on disk
  in `product/plan.yaml` the whole time. Worse, the parse branch in
  `plan.py` kept only `type(exc).__name__`: a `ScannerError` carrying
  "line 3, column 9: expected alphabetic or numeric character but found
  '*'" reached the revision prompt as the word `ScannerError`, so the model
  was asked to fix a break it was never shown, and failed the same way
  twice. `AutopilotResult.blocked_reason` is now set at every failed
  return, travels into `CaseResult.failure_reason` and the result YAML, and
  the CLI prints one line per case that produced nothing.
- **Three of eleven rejections were not about the code.** `01-t4`, `03-t3`
  and `04-t6` had nothing but LOW findings — a severity that cannot block.
  They were rejected by `leader.synthesize`'s *other* trigger, two voters
  that returned no verdict, and the row beside them named the low findings
  instead. Every one of those rows also carried an empty
  `blocking_by_voter`, so the evidence was in the record and the sentence a
  person reads said something else. A non-clean detail now names the silent
  voters and states whether they merely contributed to the rejection or
  *are* it.

The mechanisms are two test files that pin invariants rather than run 16's
instance: `tests/test_unmeasured_is_one_decision.py` asserts an excluded
case is excluded from every rate over three crash shapes and that
`unmeasured` equals the cases whose own decision says so;
`tests/test_a_refusal_names_its_own_cause.py` walks `autopilot.py`'s AST
and rejects any `AutopilotResult(status="failed")` without a
`blocked_reason`, so a fourth failure site cannot be added silently, and
checks the revision prompt against the exception the fixture actually
raises rather than a hard-coded message.

ADR-043. No product or case was changed: the case is fair and the failure
was real.

## v0.93.0 — the scoreboard was two runs behind

`benchmarks/results/HISTORY.md` says of itself that "the table below is
authoritative for the headline numbers". It had no row for **run 14 or run
15**. `save_summary` dual-writes every result file there automatically; the
table is written by hand, and a hand-maintained ledger has no way to notice
it is behind.

To be exact about the size of this, because the first draft of this entry
was not: both runs *were* recorded at the time, in three places — the
commit log (`ab7d8c8`, `f81d1ec`), the claim ledger (PC-15, PC-16) and the
README. One of four records was missing them, and it was the one a person
reads as a series. That is worth fixing and is not the same as a run going
unrecorded. Both runs are now in the table, from their result files — run 14
at build 100% · probes 100% · clean 38% over four cases, run 15 at build
83% · probes 100% · clean 55% over three of four — and three older rows
that identified their file as `…0259, reconstructed (full)` now name it in
full, because an abbreviation nobody can resolve back to a file is not a
citation.

`tests/test_bench_history.py` is the mechanism: a result file that lands in
that directory without a row fails the suite, and each row's three rates
are compared against the file it names (within a point — the table's
rounding is not consistent, and pinning a convention retroactively would
mean editing recorded history to satisfy a test).

The table also carries a **comparability break after run 15**. ADR-039
shipped in v0.89.0 and run 15 ran on 0.88.0, so its 55% clean rate still
contains the location-keyed dedupe that turned one finding across nine
files into nine blocking findings against an eight-finding cap. Run 16's
clean rate is that change first and product quality second, and the note
says so where the number is read.

**A loop within cadence now states when it next comes due** —
`ok (1d, next 2026-08-21)` rather than `ok (1d)`. The scheduler wakes daily
and the loops are weekly; a row that showed the last run, the period and
the age held the next date only as a sum the reader had to perform, and the
obvious wrong answer ("it fires every morning, so tomorrow") is six days
off. Only for loops that need no run: a `DUE` row is already an
instruction, and a date beside it would compete with it. Loops that never
ran, or whose recorded date will not parse, state nothing rather than a
guess.

## v0.92.0 — a failure must arrive as a fact

Bench run 15's third finding: case 01 lost a task to a build that failed
three times, and the result file recorded the reason as

    last failure: ==== FAILURES ==== ____ test_huge_id_no_crash ____
    server = ('127.0.0.1', 64131) def test_huge_id_no_crash(server):
    huge = "9" *

— 240 characters, cut mid-expression, containing no assertion and no
verdict. ADR-037 added that clause so the cause would travel into the rows
that read `detail`. It travels. What arrived was pytest's banner art:
`==== FAILURES ====` and `____ test_name ____` are ~160 characters of rule
characters before the run states a fact, so a head-slice spends its budget
on punctuation and stops short of the answer.

Nothing was lost — `test_summary` held the complete run all along. The
defect was in the condensation. `testing.salient_failure()` now selects
from pytest's own short-summary section, then the `E` assertion lines,
keeping both when both exist: pytest elides its summary line to terminal
width, so the summary names the test and the `E` line says what actually
broke. Truncation lands on a word boundary. The same failure now reads

    FAILED tests/test_get_groupbuy_notfound.py::test_huge_id_no_crash -
    assert 40... E assert 400 == 404

It lives in `testing.py` beside the rest of the pytest-output reading, and
a test asserts `build.py` keeps no second copy of the clip.

The underlying failure, recovered by re-running the preserved workspace: a
40-digit id matches the spec's own `^[0-9]+$` valid-format criterion, so it
is a well-formed id that does not exist and owes a 404; the product
range-rejected it as malformed and answered 400. A real product defect,
correctly caught, with the implementer holding the full traceback on all
three attempts. Nothing about the product or the case is changed — the
build rate measuring it is the benchmark working. See ADR-042.

The word-boundary helper is `_clip_words`: `testing.py` already owned a
`_clip` that keeps both ends of a faulthandler dump, and defining a second
one rebound the name module-wide — the hang-dump path called the wrong
helper and five tests failed with no import error naming the cause. A test
now rejects any duplicated top-level definition in that file. 2021 tests.

## v0.91.0 — an empty answer is not a verdict

Bench run 15 blocked two independent cases with the same spec: no criteria,
no test skeletons, no design, and one reason — "no acceptance criteria".
That sentence reads as a judgment about a spec the writer wrote. Nothing
was written, and the stage had no way to say so.

**An empty spec was the quietest failure in the revision loop, not the
loudest.** It passed every quality check by having nothing to check —
`lint_criteria([])` is clean, no criterion can be uncovered among zero
criteria, no skeleton can be in the wrong language among zero skeletons —
so the loop's "good enough" break fired on a spec containing nothing, and
the feedback handed back to the writer never once said its criteria list
was empty. Whether an LLM critic happened to object was luck: one case
drew three majors and looped, the other drew none and broke on the first
attempt. Emptiness is now reported first, under its own name, and gates
the break.

**The spec stage was the only writer stage that never asked whether its
response had been cut off.** `plan.py`, `build.py` and `discover.py` all
check; `spec.py` did not import the check. A response truncated after
`title:` is still valid YAML with zero criteria — a partial answer wearing
the shape of a complete one, which is the hazard `providers/base.py`
documents. It now asks, and a cut-off response gets its own block reason,
because "raise the cap" and "rewrite the prompt" are different next
actions.

**The ledger now records why the model stopped.** Diagnosing run 15 meant
inferring truncation from `output_tokens` landing exactly on a cap, which
cannot tell a capped answer from a complete one of that length. The
adapters knew the stop reason during the run and discarded it at the end.
`stop_reason` is recorded on every call across all three adapters, and is
optional so every result already on disk still loads.

Not changed: the spec writer's 4096-token cap, which the evidence does not
show to be binding, and the 529 retry policy, which run 15's fourth case
exhausted correctly. See ADR-041.

## v0.90.0 — a result is not an exit code

The founder asked why the Discord channel never showed logs, bugs or errors.
Nothing was broken: the channel reported only **whether a loop ran**, never
what it produced, and only for runs launchd itself started. Bench run 12
finished with a crashed case at build 75% / probes 65% and the log records
`bench: ran (exit 0)` then `no alert: nothing needs a person`. Runs 13, 14
and 15 were started by hand and could not have posted anything at all.
ADR-040.

- **A loop's last result is alertable on its own.** `cadence.result_concerns`
  collects a sentence per loop whose *output* needs a look, separate from
  whether the loop got through — so a run that exited 0 can now raise an
  alert. `bench_criterion.concern` supplies it for the bench series.
- **A poor result is a finding, not a failure.** It is reported and fails
  nothing: the scheduler's exit code still answers only "did the machine
  break". Failing on a low rate would report every weak week as a broken
  scheduler, and is refused explicitly so it is not re-proposed as a tidy-up.
- **`avs product-bench --notify`.** Any finished run reports itself, however
  it was started — completed, with cases that never ran (named first, above
  the rates they distort), or crashed. Forced past the 7-day repeat window:
  that window is for a standing condition, and a run is an event.
- **Movement without a threshold.** `bench_criterion.movement` states the
  run-over-run delta (`clean -37pp` — run 14's collapse, which no floor
  covers). Adding a clean-review floor would extend the launch PRD's only
  kill criterion by a constant in a module; that is a human decision.
- **The floors keep one definition.** `BUILD_FLOOR` / `PROBE_FLOOR` stay in
  `bench_criterion`; a test asserts neither `notify` nor `cadence` names a
  floor value. The sent-log is keyed by alert kind (migrating the old flat
  shape), so the second alert cannot erase the first's memory.

## v0.89.0 — one issue is one finding

Run 13's preserved review artifacts, read directly instead of reasoned about:
**9 of the 15 blocking findings examined were one bandit check.** The build
stage copied `tempfile.mktemp(suffix=".db")` into nine test files, bandit
raised B306 at each, and the leader kept all nine. ADR-037 and ADR-038 fixed
instances of "one concept, two definitions"; this fixes the class that
produced the number both were about.

- **The leader folds repeats of one issue.** Its dedupe key was
  `(file_path, line_start, title)` — keyed on *location* — so the same issue
  at a different path was never a duplicate. Repeats of the same
  `(voter, title)` now collapse into one finding carrying `occurrences` and
  `also_in`, keeping the worst severity any site was raised at.
- **The unclearable case is closed.** The repair pass is capped at 8
  findings, so nine copies meant eight repaired, the ninth surviving *by
  construction*, and the re-review rejecting again — no matter how good the
  fix was. The fold brings the count under the cap, and the fix prompt is
  shown every site in `also_in` so it cannot repair one file and leave eight.
- **A bound that drops work says so.** `MAX_REPAIR_FINDINGS` /
  `MAX_REPAIR_FILES` replace an unnamed literal that appeared twice, and an
  over-cap run records `repair pass saw 8 of 11 findings — 3 were never shown
  to it`. A bound nobody can see reads exactly like a fix that was not good
  enough.
- **A static-analysis hit on a test file is a note, not a blocker.** This had
  already happened once: B310 was 30 of 44 findings in run 11 and was skipped
  *by name*, and B306 walked through the same door two runs later. Analyzer
  findings on test scaffolding now report at `low` — visible, never blocking.
  Production paths keep the full audit; credential checks (B105/B106/B107)
  keep full severity everywhere.
- **A rejection names its author.** `TaskOutcome.blocking_by_voter` records
  blocking findings per voter and rides into the bench result file. Diagnosing
  run 13 meant hand-reading preserved YAML to learn that one deterministic
  tool raised 60% of the blocking findings; the row now carries it.
- **One comparative vocabulary for both claim gates.** The platform and
  marketing gates kept two hand-maintained superlative lists that had already
  drifted, and `#1` was in **both** and could never match in **either** —
  `\b#1\b`, where `\b` needs a word/non-word transition that a space and a
  `#` cannot provide. New `ai_venture_studio.superlatives` module, three
  documented carve-outs, and a `#1` boundary that holds.

See `docs/adr/039-one-issue-is-one-finding.md`.

## v0.88.0 — the thresholds that must differ, and the one word "clean"

A pre-run audit of ADR-037's *shape* — one concept defined in two places —
before firing bench run 15. Fixing the instance was not fixing the class:
three more live definitions of "a clean review" were in the tree, and
ADR-037 had left a trap of its own.

- **`ROLLBACK_SEVERITIES`, named and pinned apart.** `_fix_iteration` still
  rolled a fix back on the hard-coded `("critical", "high")` ADR-037 deleted
  one function above. It reads like leftover drift; changing it to match
  would have been a serious regression. Medium is the modal severity a review
  raises, so rolling back on medium would discard nearly every fix — turning
  the repair pass ADR-037 enables back into the no-op it exists to remove,
  while looking like it ran. Named, commented, and held to a strict subset by
  test. New `_should_roll_back` seam makes the threshold testable without
  driving git and a model call.
- **One definition of "clean."** `state.CLEAN_VERDICTS` lives beside the
  `Verdict` enum and is derived from it, so a new approval-shaped verdict
  cannot be added to the taxonomy and silently missed. `product_bench`'s
  literal, `review_and_repair`'s literal, and the constant added in v0.87.0
  now all resolve to it.
- **The founder tally reports three states, not two.** A `REQUEST_CHANGES`
  task printed the same "built, review had notes" as an `APPROVE_WITH_NOTES`
  one, so the founder could not tell work the reviewer signed off on from
  work it refused. Tolerable while those rejections carried no reason;
  not once v0.87.0 made them say what they objected to.
- **`BENCH_TIMEOUT_S` 6h → 8h.** Run 14 used 3.1h of the 6h. v0.87.0 sends
  every medium-only review into a fix iteration plus a re-review, so most
  tasks now spend two model round-trips they did not before; the margin was
  sized against runs that never did that.
- **Deliberately not unified:** `automation.MERGEABLE_VERDICTS`,
  `review_gate._BLOCKING_SEVERITIES`, and the deploy gate have overlapping
  members and answer different questions. ADR-038 records the rule — unify
  definitions of one concept, name and pin definitions of two.

See `docs/adr/038-the-thresholds-that-must-differ.md`.

## v0.87.0 — a severity you block on is a severity you must try to repair

Bench run 14 scored its best build and probe rates ever (100%/100%, against
run 13's 94%/92%) and its **worst** clean-review rate: 38%, down from 75%.
Two of four cases scored zero clean reviews across nine tasks, each rejection
carrying an empty reason.

The reviewer had not become stricter. Two thresholds that must be one number
were written in two files and had drifted: `leader.synthesize` blocks a
verdict on `{CRITICAL, HIGH, MEDIUM}`, while `review_and_repair` selected
findings to repair with its own hard-coded `("critical", "high")`. A task
whose worst finding was MEDIUM was rejected, **never repaired, and could
never be cleared** — unclean by construction, not by quality. Medium is the
modal severity the voters raise (89 of ~187 findings across run 13's
preserved workspaces, vs 17 high and 2 critical), so this was most of the
unclean rows: medium-only rejections went 2→7 between runs while repairable
ones stayed 3→4. Neither release had touched `leader.py` or any voter — the
defect was constant in both runs and only the exposure varied, so 38% was
not a regression, and 75% was never as solid as it read.

- **One threshold.** `ACTIONABLE_SEVERITIES` is public in `leader.py` and
  imported by `review_and_repair`. A parametrized test walks the set itself,
  asserting each severity both blocks a verdict and triggers a repair
  attempt — nothing pinned the two together before, which is how they drifted.
- **Every non-clean verdict says what it objected to**, bounded to 240 chars
  and built from the review that produced the final verdict, so a post-fix
  row never names findings that were just repaired. Previously `detail` was
  written only when a fix iteration ran, so 11 of run 14's 17 outcomes
  reached the series unexplained — ADR-036's evidence-deletion failure one
  stage over.
- **A rejected row keeps its whole reason**; the 200-char clip now applies
  only to clean rows, where there is nothing to explain.
- **Result files carry `avs_version`.** Attributing run 14 to a build meant
  diffing git commit timestamps against the result's filename, which only
  worked because the release landed 9 minutes before the run.

MINOR, not patch: the repair pass now spends model round-trips it did not
spend before, and the bench result gains a field.

## v0.86.0 — the import gate ran for two profiles and should have run for all

v0.85.0 shipped `_blocks_on_import` gated on `web` and `enterprise-web`, on
the reasoning that only those two carry the boot contract — `python
app/main.py` must serve when run directly — and that the contract is the
precondition for the failure.

It isn't. The contract is what makes a web product *prone* to the shape, by
actively instructing the model to make the entry point serve itself; a model
that obeys at module level satisfies the gate and hangs every test. But the
hang needs only one thing: a call that never returns, at module level, in a
Python file the tests import. The `data` profile's own stack hint is "Python
+ the team's existing warehouse/orchestrator", and a module-level
`run_forever()` there hangs its suite in exactly the same way, printing
exactly as little. `_BLOCKING_SERVE` has listed `run_forever`,
`serve_forever`, `main_loop` and `mainloop` since v0.85.0 — the gate could
already name that call and simply never ran over it.

Only `miniprogram` bans `.py` at the write boundary, so `data`, `game` and
`app` can all produce Python that pytest imports. The gate now runs for
every profile; where there is no Python the scan finds nothing and costs
nothing.

The profile now picks one thing only: the fix sentence. A data pipeline told
to append `uvicorn.run(app, port=int(os.environ["PORT"]))` receives advice
it cannot act on, and feedback a model cannot act on is feedback that loops
— so non-web products get the general rule instead, that nothing which
blocks may run at import time.

Minor for the same reason v0.85.0 was: this refuses builds that previously
passed, in three profiles that were not being checked at all.

Generalizing from *the instruction that induces a bug* to *the bug* is the
error worth naming here. Absence of the boot contract was read as immunity
from the failure the boot contract happens to encourage.

## v0.85.0 — a hang must describe itself, and must not outlive its own timeout

Bench run 12's case 04 died on `pytest -q` exceeding 300s. v0.83.0 turned
that into a blocked gate instead of a dead run and left the cause for the
next run to reveal. The next run never could have revealed it: the harness
destroyed the evidence four separate ways, all of them upstream of the
product under test.

`TimeoutExpired` carries `output` and `stderr`. It was holding everything
the suite printed the whole time, and the report threw it away and said
300s had elapsed. Nothing had asked pytest where it was stuck, though pytest
will name the hung test and the line it is blocked on if
`faulthandler_timeout` is set. The kill signalled the direct child only, so
a product whose tests boot a server left that server running, holding its
port against the next case and holding the stdout pipe it had inherited.
And the crashed case was the only one whose workspace was deleted —
preservation ran after the autopilot call, so the exception jumped straight
over it into the `finally` that removes the temp directory. `run_case`
preserved the workspace of every failure except the failures that needed
one.

So run 12's own product cannot be root-caused. It is gone, and this entry
says so rather than implying otherwise. What can be established is the shape
of failure that satisfies every gate the framework had and still hangs
forever: a module-level `uvicorn.run(app)`. The web profile's boot contract
says the entry point must serve when run directly, and a top-level serve
call satisfies it on the first try — the boot gate boots the entry, sees a
listening socket, and passes. But `import main` is what every test does, and
that line never returns. pytest collects, blocks inside the import, prints
nothing, and five minutes later is killed with no output, which is exactly
what run 12's row recorded. Two gates that each pass, whose conjunction is a
permanent hang.

Now: a timeout report carries what the process printed, clipped from both
ends — a faulthandler dump prints most-recent-call-first, so a tail-only
clip keeps the plumbing and drops the answer. Every test command runs with
`faulthandler_timeout=120`, under the 300s kill; it only prints, so a merely
slow suite is unaffected and a stuck one names itself while still alive to
write it. Every runner that boots a product — the test gate, the docker
sandbox, the probe runner, the boot gate, the screenshot server and the
generated probe frame — starts its child in its own session and signals the
whole group. The case that crashed keeps its workspace, and the error row
points at it.

And a module that serves on import is rejected before the suite runs.
`_blocks_on_import` is a parse, not an execution: it fails the build with
feedback naming the file, the line and the fix. Static, because the dynamic
form of this check is the bug it is checking for.

Minor rather than patch: nothing in the enumerated contract surface moves,
but a build that previously passed can now be refused, and a new refusal is
not patch behavior.

## v0.84.1 — the release check that could not tell a failed install from a good one

Three releases running — 0.82.0, 0.83.0, 0.84.0 — the post-publish
verification installed the new version from PyPI, reported success, and
produced no `avs` binary. Three times it was recorded as a PyPI index
propagation hiccup that `--force-reinstall` cleared. Both halves were
fiction.

The install had failed, and the command doing the verifying was
`pip install --quiet ... 2>&1 | tail -3`. `--quiet` suppresses the
`Successfully installed` line; `tail -3` discards the `ERROR:` lines. What
survives either outcome is the two pip-upgrade notices, so a failed install
and a successful one are byte-for-byte identical on screen. pip had printed
exactly what was wrong every time and it was thrown away before anyone read
it. The retry a minute later is what fixed it; `--force-reinstall` took the
credit — which is why the same "hiccup" recurred at the next release, and
the one after that. The diagnosis was never wrong so much as never made.

That is the shape v0.84.0 fixed one directory over: a check that reports on
something it did not observe. There it was a probe calling a product it
never reached; here it is a verification step reading output it had already
discarded.

`scripts/verify-release.sh` replaces the pipeline typed from memory at each
release. It never silences or truncates pip. It retries **only** while the
index is genuinely behind — the one retryable case — and fails on the first
attempt for anything else, with the whole log, because spinning for ten
minutes on an error that will never clear is how a real defect gets filed as
a propagation delay. It treats `Successfully installed` as no evidence at
all: the console script must exist on disk and `avs --version` must print
the version being verified, since pip reporting success says a wheel was
unpacked and nothing about whether the thing the founder types arrived. It
then runs `init` and `replay --demo` with every provider key unset, so a
wheel that only works because the verifying shell happened to hold
credentials does not pass. `--deploy` upgrades whichever interpreter owns
the `avs` on `PATH` and re-checks the version, because published is not
deployed — a step that has been manual, and forgotten, before.

`tests/test_version_consistency.py` — which exists because `__version__`
drifted from `pyproject.toml` for two releases under a checklist — now also
guards this script's invariants: a pip line carrying `-q`/`--quiet`, or
piped through `head`/`tail`, fails the suite, as does dropping the
console-script assertion. The checklist step is a program, and the program
has a test. `publish.yml`'s header points at it as the last step of the
release.

## v0.84.0 — a probe that never reached the product measured nothing

Patch-shaped, released as a minor because the probe frame is contract
surface. Bench run 13 scored **build 94% · probes 92% · clean 75%**, the
best composite the series has produced, with all four cases measured for
the first time. One of its three probe failures was not the product's.

Case 04's `score-validation-and-evidence-downgrade` failed with
`URLError: Connection refused`. The probe never reached the product, so
nothing was learned about its behaviour — and it still cost the case a
probe (3/3 → 2/3). The cause was in the frame the probes run inside:

- **The port was the constant 8646.** Every probe is a separate process
  that boots its own copy of the product, so a probe could boot onto the
  port the previous probe's server had not finished releasing.
  `proc.terminate()` only asks; it does not wait.
- **Readiness was a bare TCP connect.** That succeeds against a socket
  which is already closing, so the check reported "up" about a server on
  its way out, and the first real call then found nobody there.

Now: an ephemeral port per probe; readiness that requires an actual HTTP
answer (a 404 counts, a transport error does not); a liveness check so a
product that dies on import says so instead of timing out for 30s; one
retry before believing a refusal, and then an `AssertionError` in plain
words rather than a raw traceback; and `proc.wait()` after `terminate()`
so the port is genuinely back before the probe process exits.

This is the third instance of one shape — run 7's `{}` failures, run 12's
error bodies, and now this — **the harness charging the product for the
harness's own miss.** Each was first read as a product defect.

## v0.83.0 — an unmeasured case is not a zero, and it is not silent

Minor. v0.82.0 armed the bench loop and it ran the same night — the first
bench run a scheduler ever performed. It reported **build 75% · probes 65%
· clean 48%** and exited 0, so `avs cadence --notify` printed *no alert:
nothing needs a person*. Three of those statements were misleading, for
three separate reasons ([ADR-035](docs/adr/035-an-unmeasured-case-is-not-a-zero.md)).

**Case 04 never ran.** Its `pytest -q` did not return within 300s.
`run_test_gate` catches `TimeoutExpired` on its own path, but four other
callers — build, autopilot, correction, fixpr — reach the runners directly
and had no guard, so the exception raised out of everything above it and
killed the case an hour into the run.

**The dead case then scored zero.** `_avg` averaged `0.0` for a case with
no denominator, which is how "we did not measure this" became "the machine
failed at this". It cost 22 points of probe rate: over the three cases that
actually ran, the run scored **build 100% · probes 87%**. The launch PRD's
only remaining kill criterion reads that probe number against a 50% floor,
so a hung subprocess was two more bad weeks away from firing a
**capability** verdict about the writer.

**And nobody was told.** `product-bench` exits 0 whether or not a case
died, because a case erroring is data rather than a run failure — the same
absence-as-clean-pass shape ADR-033 and ADR-034 each removed one level
further out, now one level further in.

So: a rate averages only over cases that produced its denominator, and a
case that ran and built nothing still scores a real `0.0` — that is a
failure, not an absence, and the test that pins it is what keeps the
exclusion from becoming a way to hide bad runs. The denominator travels
with the number: `cases_measured`, `cases_total` and `unmeasured` are
written into the saved result, and the cadence line, the Discord alert and
`avs bench-criterion` itself carry `(over 3 of 4 cases)` so a later reader of
the series can tell 75%-of-four from 75%-of-three. That last one matters
most: Gate PL5 is a human deciding whether to cut scope on two numbers, and
they should not have to open the YAML to learn one of them was averaged over
three cases. Results predating this carry no denominator and are read as
complete, which is what they were. A run that could not measure a case
**exits 3** — not
because the result is bad, but because the harness broke and nothing else
will say so; a merely poor result stays quiet, because a channel that
alerts on the benchmark doing its job is a channel nobody reads. And a
hanging suite now blocks its gate instead of killing the run:
`_run_and_classify` converts `TimeoutExpired` into
`TestReport(status="error")`, which already blocks APPROVE — an unprovable
suite must not pass, and must not take the run down with it.

**The measurement was also lying about the products.** Run 12's case 03
failed two probes with `AssertionError: no error field: {}`. The product
was correct — booted by hand it answers `400 {"error": "id must be a
base-10 integer: 'abc'"}`. The frame in `probegen.BOOT_FRAME` was
discarding it: `urllib` raises `HTTPError` on every 4xx and puts the body
on the exception, and `call()` returned `e.code, {}` without ever calling
`e.read()`. Run 7 produced the identical `{}` failures in the same case,
and the response then (`2bb4808`, and the `web.yaml` rule beside it) was to
require products to write a human-readable `error` field into 4xx bodies.
That rule is good and stays — but it was written to fix a symptom in the
product that was a defect in the measurement, and it could never have
worked, because the probe could not see the field no matter what the
product wrote. A harness that cannot read the answer will keep reporting
that the answer is wrong, and each round of that produces a plausible fix
one layer too low.

One more thing this exposed, worth stating on its own: the metric definition
in `metrics/product_bench_capability.md` had excluded *"cases that died on
harness noise rather than on the product under test"* since 2026-07-27. The
code never did. **A stated exclusion that nothing enforces is a comment** —
and this one read as a guarantee for two and a half weeks. The runner now
enforces it, and the metric's `changed_at` moves to 2026-08-12 because the
denominator changed, so any comparison straddling that date is flagged
(F-22.1). The floors and the O-L2 baseline stand: runs 1–11 hold no crashed
cases except run 4, already excluded as noise.

Run 12's recorded numbers are **not** rewritten — `benchmarks/results/` is
a record, not a document — with the recomputed reading and the new
denominators' comparability break noted beside them in `HISTORY.md`. Stated
rather than left implied: **why case 04's suite hangs is still unknown.**
It is now a blocked task with a named reason instead of a dead run, which
makes it diagnosable on the next run rather than diagnosed now.

## v0.82.0 — the kill criterion's series is watched, and its schedule leaves cron

Minor. v0.81.0 left the product-bench capability axis as the launch PRD's
**only** kill criterion, on the strength that its series is collected
mechanically and can fire without asking anyone anything. The series had
stopped. Newest result: run 11, `result-2026-07-27-0449.yaml` — sixteen days
and three scheduled Mondays earlier, with no complaint from anything.

Two independent defects, either sufficient. The Monday crontab entry never
fired: cron *skips* a job whose minute passed while the Mac was asleep
rather than deferring it, and the bench log holds exactly one entry ever,
the preflight of the day it was installed. And it could not have
authenticated if it had: the script grepped `^export ANTHROPIC_API_KEY=`
out of `.zshrc`, which the v0.71.1 LaunchAgent hardening had since converted
to the `ANTHROPIC_API_KEY_FILE` form.

Neither is the structural fault. `avs cadence` watched `compound` and
`sweep` and did **not** watch the one series a kill criterion reads. A
criterion whose series has silently stopped reports "not fired" forever, and
reads exactly like a criterion being satisfied — the same absence-as-clean-pass
failure ADR-033 removed, one directory over.

Added (ADR-034):

- **`bench`, a third cadence loop.** Seven-day cadence, read from the newest
  ISO date in `benchmarks/results/result-*.yaml`, carrying the build and
  probe rates that run recorded. Overdue and `never_run` are findings and
  reach Discord.
- **Tracked only where its cases live.** No `benchmarks/products-real/`, no
  bench loop — the bench measures the framework, not a product, and a
  standing false alarm in every product workspace would train the reader to
  swipe the channel away.
- **It runs itself**, invoking `avs product-bench --cases-dir
  benchmarks/products-real` — the cases directory named explicitly, because
  the command's default is the synthetic set and the criterion is defined
  over the real one. ADR-033's rule holds: a paid hour-long run is still a
  run, not a question.
- **Per-loop timeouts** (`LoopStatus.timeout_s`). Run 11 took 74 minutes; one
  shared hour would have killed it at the three-quarter mark and reported the
  timeout as a capability failure.
- **`--only` and `--label`**, so a second workspace gets its own schedule.
  `--only` is validated against the loop names the module knows rather than
  against what is present, and naming an absent loop is an error, not an
  empty report: a filter that quietly selects nothing is a scheduler that
  watches nothing and reports all clear every morning.

Changed outside the package: the crontab entry is removed in favour of a
launchd agent (launchd runs a missed job on wake), and
`weekly-product-bench.sh` keeps its credential fix but says at the top that
it is no longer scheduled.

Honest cost: the scheduled run no longer commits and pushes the result. The
criterion reads the working tree either way, but the series survives losing
the machine only if someone commits the file — now a line in the weekly
rhythm rather than something automatic. The trade is a runner that is
versioned, tested and watched instead of a script in `~/.local/bin` that
nothing pinned and nothing checked.

## v0.81.0 — the weekly attention axis is withdrawn

Minor. The launch PRD's first kill criterion said: if the framework's own
weekly maintenance attention exceeds 4.0 hours for 4 consecutive weeks, cut
scope at Gate PL5. Its only instrument was **a number the operator had to
type in every week**. Three weeks after launch the log held one `not_tracked`
row and zero logged hours. The criterion could not fire, and — as the Gate
PL5 record said plainly at the time — could not be declared safe either.
What the series actually measured was willingness to answer a weekly prompt.

The machinery around it was careful and the care did not help: `avs
attention` refused to invent a number, logged `not_tracked` rather than
estimate, and the watchdog was built so the machine could never answer on
the operator's behalf. Against a founder for whom typing is the most
expensive thing the product can ask, a weekly typed number was never going
to hold.

It also cost something concrete. The `attention` loop was due every 7 days
and **exited non-zero every single morning by design**, because "not yet
answered" is its normal state. That forced a permanent exemption in the
alert path shipped one release earlier: the one loop guaranteed to fail
daily had to be excluded from the error channel, or the channel would cry
wolf every morning. A scheduled job whose non-zero exit means nothing is a
hole in the error reporting, and it existed solely to carry this axis.

Removed (ADR-033):

- `attention.py`, `tests/test_attention.py`, `metrics/attention-log.yaml`,
  `metrics/weekly_maintenance_attention.md`, and the `avs attention` command.
- The `attention` cadence loop, `LoopStatus.human_input_required`, and the
  alert's "no machine can answer this" branch.
- The cycle report's attention axis (`read_attention`, `CycleState.attention`).
- **The exemption in `notify._failures`.** A non-zero exit from any scheduled
  loop is now a failure, full stop, and reaches Discord.

Amended, not quietly trimmed:

- `launch/prd.yaml` drops outcome **O-L1** and its kill criterion, with the
  withdrawal recorded in place; the id is not reused. The capability axis
  (**O-L2**) stands — its series `benchmarks/results/*.yaml` is written by
  the weekly run itself, so it can fire without asking anyone anything.
- `launch/gate-pl5-evaluation.yaml` is **not rewritten**. Its 2026-07-26
  reading stands verbatim with a `superseded_by` block appended, because the
  criterion was *withdrawn, not satisfied*, and the record has to say which.
- The pre-registered launch experiment and the published launch post are
  left exactly as recorded, for the same reason — the pre-registration hash
  guard refused the edit, which is the guard working.

Honest cost, stated rather than papered over: the framework no longer
measures its own maintenance burden at all. That is the correct trade only
because the measurement it replaced was not happening. A future burden
signal has to come from something already timestamped — commits, gate
decisions, loop runs — and not from a prompt.

## v0.80.0 — the alert carries the error, not just the backlog

Minor. v0.79.0 sent the *lateness* to Discord: which loops are overdue,
which kept their cadence over an empty window, whether the scheduler is
behind. It did not send the thing most worth waking up for — a loop that
**ran this morning and crashed**. `run_due` has captured each due loop's
exit code and the last 2000 characters of its stderr since v0.72.0, printed
them, and thrown them away; `build_alert` never saw them. So a workspace
whose `compound` loop died on a traceback at 09:00 produced a green table,
no alert, and a stack trace in the log nobody opens — the exact failure the
previous release was written to end, one layer further in.

`build_alert` now takes the run's `RunOutcome`s and reports:

- **A loop that ran and failed** — `exit N` plus the tail of its output in a
  code fence. The tail, not the head: the traceback's last line is the one
  that names the error, and the first fifty are import frames.
- **A loop that could not be started at all** — a missing binary, a bad
  `--repo-dir`, a timeout. Said differently on purpose, because it is a
  different fix.
- **Nothing at all** for a loop that was *not due*, or that succeeded
  noisily. Both are the design working.
- **Nothing at all** for a loop that needs a human. `attention` exits
  non-zero every single morning by definition; calling that an error would
  have the channel cry wolf daily about the one loop behaving exactly as
  specified. It is already reported, correctly, as overdue.

Failures are written **first** in the message body and take over the
heading (`2 loops FAILED this run: sweep, compound`). Both are load-bearing:
`render` truncates from the end to fit Discord's 2000-character ceiling, so
an error written after a long backlog is an error that can be silently cut;
and the heading is the phone's notification preview — the whole message for
anyone who does not open it. A loop that broke this morning outranks a loop
that is merely late.

Unchanged: the dedupe, the 7-day repeat window, the webhook handling. A
failure whose output changes is new news and goes out immediately.

**Known limitation.** This reports failures the scheduler *survived*. If
`avs cadence` itself dies — a crash before the notify block — nothing is
sent, and the log is still the only record. Guarding that means wrapping the
whole command, which is a bigger change than the failure it has never yet
had.

## v0.79.0 — the alert leaves the machine

Minor. The daily LaunchAgent has been doing its job since v0.72.0: at 09:00
it runs the due loops, exits 3 when one needs a person, and writes the
reason to `~/Library/Logs/ai-venture-studio/loops.log`. Nobody opens that
file. So the machine noticed, told nothing that listens, and the week
passed — the same silent-success shape the loops exist to prevent, one
level up. Weekly maintenance that depends on the operator remembering to go
look is not maintenance; it is a reminder they have to set themselves.

`avs cadence --notify` posts the alert to a Discord webhook. Three rules
shape it, and each one is a way the notifier could be worse than the log:

- **Only when something needs a person.** No daily "all green". A channel
  that speaks every morning is a channel that gets muted, and then the one
  message that mattered is muted with it. `build_alert` returns `None` when
  nothing is stale, vacuous, or behind.
- **Not the same thing every morning.** An overdue weekly loop stays overdue
  until someone acts. The alert is identified by a hash of its **content**,
  not its date, so tomorrow's identical alert is recognised as already said
  and held for `REPEAT_DAYS`; a *changed* alert goes out immediately,
  because delaying new news would make the channel less trustworthy than
  the log it replaces.
- **Carry the command, not the diagnosis.** The reader is on a phone. Each
  line says what is true and gives them something to paste — including the
  `avs attention …` command, marked as the one no scheduler can answer, and
  the v0.78.0 empty-window cause, which has to survive the trip.

Setup is one command: `avs cadence --set-webhook <url>` validates the URL
before it touches disk (an unvalidated one would sit there looking
configured and fail once a week at 09:00, into the log this feature exists
to stop using), writes it `0600`, and never echoes it back — the terminal
scrolls, and whoever reads it can post as this app. `resolve_webhook` then
finds it with no environment at all, which is the case that matters:
launchd hands a job four variables and no login shell, so a feature
configured only by an `export` would be missed by the one run that counts.

The webhook URL is a credential: whoever holds it can post into the channel
as this app. It follows the rule the scheduler already enforces —
`AVS_DISCORD_WEBHOOK_FILE` (a pointer) travels into the plist, which is a
world-readable file in `~/Library/LaunchAgents`; `AVS_DISCORD_WEBHOOK` (the
URL itself) is refused there by name. The missing-credential check moved
from `*_FILE` to `*_KEY_FILE` in the same change: the webhook pointer also
ends in `_FILE`, so without that a workspace that can notify but cannot
authenticate would have installed clean and failed every morning.

A failed delivery is never recorded as sent — otherwise the dedupe
suppresses tomorrow's retry of an alert that never arrived, and silence is
remembered as success. And `--notify` does not change the exit code:
telling someone is not fixing it.

## v0.78.0 — an empty window says which kind of empty

Minor. `avs cadence` reported the compounding loop green over a workspace
where it had read nothing for weeks, and the only sentence it had for that
was `read 0 reviews — nothing to compound`. That names the symptom and no
cause, and the two causes are opposites: a loop pointed at a workspace
nobody ever built is a misconfiguration to fix, and a loop over a workspace
where the work paused is a loop doing its job. The founder cannot tell them
apart by looking. The machine can, and now does.

`collect_signals` counts **every** review on disk and keeps the newest
`written_at`, window or not — the distance between that stamp and the
window is the diagnosis. `render_proposal` writes it onto the artifact
(`Nothing reached this window: 9 review(s) exist, newest 2026-07-31.`, or
`no review has ever been written here.`), `cadence` reads it back into
`LoopStatus.empty_because` (`work_stopped` | `never_any` | `""`), and the
CLI turns that into the line worth acting on: *the loop is almost certainly
pointed at the wrong `--repo-dir`*, or *the loop is fine; the work is what
paused*. The age is measured from the stamp, clamped at zero so a future
date reads `0d old` rather than a negative one.

Also: `scr_raised` left `correction.py` four releases ago and stayed behind
in the results page's colour table and the CLI's — a row nothing could
reach. A dead row is not a visible bug, which is why it survived. Both
tables now key off a single `correction.STATUSES`, with tests asserting the
module constructs exactly those statuses and that neither presenter names
one that does not exist. Read off the source rather than by exercising the
paths, because the failure being guarded is a status no path reaches.

## v0.77.0 — "not that", said with one tap

Minor. A requirement change comes back as a draft: what will be different,
the acceptance criteria it would leave behind, and the assumptions — the
decisions the model had to make because the founder did not say. Those
assumptions were put on the card deliberately, so a wrong one would be
visible *before* it was built.

Then the only control under them built all of them anyway. Seeing that an
assumption is wrong and being able to say so are different things, and the
founder's entire vocabulary was one button meaning yes and a browser back
button meaning "start the complaint over from nothing". For the reader this
product is written for — assumed as lazy and as non-technical as it is
possible to be — that is not a choice, it is a dead end with a list of
reasons in it. The refinement is the part they cannot do; it is also the
part a model is good at.

So every assumption now carries **Not that**. One tap rejects that decision
and the draft is written again, with the rejection sent to the model as a
question it must answer *differently* — a redraft that keeps the same
behaviour under a new sentence is a wrong answer, and the prompt says so.
Words stay available and stay second: a tap is already a complete answer,
and asking for a sentence is the toll this whole path exists to remove.

The rejections travel on the plan, in the same hidden field the plan
already used, because the draft is never written down and the plan is the
only memory between one request and the next — without it a second redraft
could propose the assumption the founder just turned down. The complaint is
*not* re-routed: a second router call is free to classify the same words as
a repair or against another feature, and the founder would be shown a
redraft of a change they never asked for. `ChangePlan` therefore also
carries the router's `instruction`, so a redraft gets exactly the input the
first draft had, and the rejection is the only thing that differs.

Two rounds, the same bound intake puts on clarifying questions — a third is
not a conversation, it is a loop that spends a model call each time round.
The cap is enforced on the route as well as the page, so a form left open
in another tab cannot spend a call the card has stopped offering. **Make
this change** is on the card at every round including after the cap: the
limit ends the redrafting, never the change. A redraft still writes nothing
— same promise the first draft makes, so pressing "not that" can never be
the thing that commits the founder.

Contract surface: `ChangePlan` gains `rejected`, `notes`, and
`instruction`, all defaulting to empty, plus a `redrafts` count;
`draft_change` gains keyword-only `rejected` and `notes`;
`correction.MAX_REDRAFTS` and `POST /correct/redraft` are new. All
additive.

Suite 1829 hermetic tests (as measured by CI at the tag; 1824 locally,
where the five mutation-testing cases skip for want of `mutmut`).

## v0.76.0 — what happened, said to the founder, and takeable back

Minor. Three failures that all landed on the same screen: the card a
founder reaches after asking for a change.

**It was written for the wrong reader.** The card's only line was
`CorrectionResult.detail` — `repaired in 2 attempt(s); files: src/cart.py`,
or `repair still broke the suite after 3 attempt(s) (…); workspace
reverted`. Those are notes for whoever reads the log. The person this
product exists for is assumed non-technical on purpose, and they were
handed the implementer's notes and left to work out the only two things
they actually wanted to know: did my product change, and what do I do now.
A sentence written for a log cannot be translated into founder language
after the fact — so `CorrectionResult` now carries a `reason` from a closed
vocabulary (`REASONS`), one member per way out of `run_correction`, and the
Studio says it in the founder's own language. A test asserts every member
has a string in both languages, and that every outcome which changed
nothing says so out loud — "it failed" and "it failed and your product is
untouched" are very different news to someone who cannot go and look.
`detail` is not deleted: it moves one fold down, under **Technical
detail**, because when a founder does ask someone technical for help it is
exactly what that person needs.

**A repair could not be undone.** Builds and feature additions were tagged
as checkpoints. Repairs were bare commits — so the one kind of change a
founder makes when something is *already* wrong, the change most likely to
need reversing, was the only one the undo list could not see. A repair now
tags a checkpoint like every other change, and the result card offers to go
back beside the thing it is reporting, carrying the same honest note about
how many later changes go with it. The change list at the bottom of the
home page is where a founder looks a week later; the moment they want the
undo is the moment they read what was done, and sending them off to hunt
for the right row is how a reversible change stops being reversible in
practice. One renderer serves both, so the sentence about the cost cannot
quietly go missing from one of them.

**The page you wait on looked exactly like a hung one.** It reloads every
four seconds through the minutes a model call takes, and said the identical
thing each time — the only evidence anything was happening was that nothing
had changed, which is also what a dead worker looks like. It now shows how
long it has been running. A fact, not a progress bar: an omitted clock is
honest, an invented one is not, which is the rule `_elapsed_hms` already
followed for builds.

Contract surface: `CorrectionResult` gains `reason` and `checkpoint`, both
defaulting to empty; `correction.REASONS` is new. All additive.

Suite 1809 hermetic tests (as measured by CI at the tag; 1804 locally,
where the five mutation-testing cases skip for want of `mutmut`).

## v0.75.0 — something to point at, instead of something to remember

Minor. v0.74.0 made "actually, make it X" real. This is about everything a
founder had to do *before* that sentence, all of which was recall.

**The home page listed the wrong features.** Under `Features` it rendered
`product/features/*` — the directories written by post-build *changes* — as
bare directory slugs. Everything the product was actually built from
appeared nowhere on the page. A founder who built six features and had
changed none of them saw an empty section describing a product with six
features, and then had to describe, from memory, a feature the Studio had
never shown them a name for. There is now a card per built feature, read
from `built_specs` — the correction router's own list, deliberately, because
two independent readers of `specs/` would eventually disagree and the
visible failure would be a card offering a change the router then refuses to
route. Each card carries what that feature does, and a `Change this` box.
The old list keeps its information under an honest heading, `Recent
changes`.

**A criterion is shown as a sentence.** EARS is a grammar for writing
requirements that cannot be misread by the machine that builds against
them; it is not a sentence anyone wants to read. `The system shall keep the
cart after the browser is closed` renders as `Keep the cart after the
browser is closed.`, and `When an order is placed, the system shall email a
receipt` as `When an order is placed: Email a receipt.` Display only —
`criteria` is inside `contract_hash` and is what the build is checked
against, so a helper that rewrote it would be an unratified spec edit
dressed as a stylesheet. A line that is not EARS at all is shown exactly as
written rather than guessed at.

**Pressing `Change this` scopes the complaint without deciding it.** The
card sends `spec_slug`, and the router is constrained to that one feature
instead of inferring one from the founder's words. It does *not* skip the
router: which feature is a fact the founder pointed at, but whether the
product is broken or the requirement moved is a judgment they did not
express by pressing a button, and forcing a `kind` here would silently
answer the one question nobody asked. A slug naming something unbuilt is
refused loudly rather than falling back to the full list — falling back
would turn the card's only guarantee back into a guess.

**Marking an acceptance row wrong now needs no typing.** `Wrong` used to
open an empty box and wait for the founder to write out, in prose, the thing
the row already said. One tap now sends it, recorded as *this one is not
right: <the row>* — a report of a failure, which is what the tap means,
rather than the bare row text, which would be filed on the SCR as a
requirement. The box is still there, second, for anyone who wants to add
words. Where a row matches a feature's criterion word for word the tap also
carries that feature's slug; where it matches nothing — the common case,
since the acceptance walkthrough is written by a model in plain language —
it carries none and the router decides as before. `criterion_owners` only
ever returns exact, whitespace-normalised matches owned by exactly one
feature, because a *wrong* pre-scope is worse than none: it skips the router
precisely when the router was needed.

**The composer offers two choices, because it has two forms.** It had three
tabs. `Something wrong?` and `Is it broken?` posted to the same `/correct`
form and the router reads the words, never the tab — so the founder made a
distinction the backend threw away, and paid a decision for it.

Contract surface: `/correct` accepts an optional `spec_slug` field (absent
behaves exactly as before); `route_complaint` gains `only_slug`;
`_built_specs` is now public `built_specs`, and `criterion_owners` is new.
All additive.

Suite 1791 hermetic tests (as measured by CI at the tag; 1786 locally,
where the five mutation-testing cases skip for want of `mutmut`).

## v0.74.0 — "actually, make it X" is a button now, not a sentence

Minor. A founder tells the Studio their requirement has changed. Until this
release the whole path ended at a line of text:

> re-run `avs add`/`spec` for 'cart' to regenerate

printed in a browser, to someone who has never opened a terminal, under a
green tick reporting that their complaint had been handled. Nothing was
built. Nothing was scheduled. The spec still described the product they had
just changed away from.

**The change is now drafted, then built.** `draft_change` reads the spec and
returns a plan — a plain-language summary, the complete post-change
acceptance criteria, and the assumptions made on the founder's behalf. The
assumptions are on the card rather than in the report afterwards, because a
wrong one is cheap to catch before a build and expensive to catch inside a
product. Drafting is pure: it writes nothing and raises nothing, so a
founder who reads it and closes the tab leaves the workspace exactly as they
found it.

That last property is a fix, not a nicety. The old path raised **and
approved** the SCR at classification time — before the founder had agreed to
anything. Somebody who read the plan and decided against it left an
approved, unconsumed grant behind: a banked free pass to edit a frozen spec,
earned by a person who had chosen not to change anything. `apply_change`
now does the raising, and the founder's press is what calls it.

**The amendment is parked, not applied.** The Studio confirms in one process
and builds in a detached subprocess minutes later, so there is no moment at
which "the founder agreed" and "the build succeeded" are both true and in
the same process. The criteria are written to `.mas/pending-amendment.yaml`,
bound to the FDR by a sha256 of its whitespace-normalised text so a record
left by an abandoned change cannot attach itself to whatever gets built
next, and `run_feature` spends it only when **every** task built. A spec
promising behaviour that failed to build would be worse than the stale spec
this whole path exists to fix: it is the one file `avs status` and the
acceptance page treat as the truth.

**Amending re-stamps `approved_hash`.** `criteria` is inside
`contract_hash`, and `_approval_drift` refuses to build any spec whose
contract no longer matches its stamp. An amendment that changed the criteria
without re-stamping would make the founder's own approved change look
exactly like someone editing a frozen spec behind the SCR channel's back,
and §13.35.5 would refuse every later build of that slug — permanently.
Re-stamping is what ratification means. `test_skeletons` are deliberately
untouched: they belong to the build that authored the test files now on
disk, and the feature build has already written its own.

Two smaller rules that the tests pin: the SCR records `plan.words` — the
founder's own sentence — and never `plan.summary`, because a paraphrase
filed as the authorization is a record of a decision nobody made (ADR-U02);
and an empty criteria list is refused rather than ratified, since a spec
that promises nothing passes every gate that follows it.

**The other half: the evidence is refreshed after a change.** This would not
have been noticed until the first half worked, which is why it ships with
it. `_post_build_artifacts` wrote six artifacts; the feature path
hand-inlined two of them. Screenshots and `VERIFICATION.md` came only from
the first build — so a founder would change the cart, press "what does it
look like", and be shown the old cart under a green success message. While a
change was a no-op that staleness was accidentally honest.

`refresh_evidence` is split out and called by both paths. What is *not* in
it is deliberate: `outcomes.yaml` stays the first build's per-module record
(`persist=False`), because rewriting it with a feature's two tasks would
erase the main build's failures and turn the home page green. Evidence is
best-effort by contract — it never fails a build that succeeded.

Capture itself needed three fixes to be worth re-running:

- **It photographs the product, not its front door.** `capture` was called
  with the default `["/"]`, so a five-page product got one picture of the
  home page — and after a change that touched checkout, the one picture was
  of the page that had not moved. `discover_paths` scans the product's own
  route decorators, caps at 8, and skips parameterised routes, because there
  is no id to substitute and a 404 screenshot is worse than a missing one.
- **The port is asked for, not assumed.** 8642 was survivable while capture
  ran once per workspace lifetime. It now runs after every change, where a
  leftover server — or the founder's own `avs preview` — would make every
  subsequent screenshot a picture of the wrong process.
- **Stale pictures are pruned, but never over a failure.** A page the
  founder deleted must lose its picture; an empty capture run means the
  capture failed, and deleting the last evidence of the product over a
  failure is its own bug.

And when there is nothing to photograph, the page says why. `capture` had
always written its reason to `screenshots.yaml` — playwright missing, no
runnable entry, the server never listened — and nothing read it, so the
founder saw an empty space and concluded the Studio was broken.

Contract surface: `POST /correct/change` is a new Studio route;
`.mas/pending-amendment.yaml` is a new internal file; `avs correct` gains
`--yes` and a `change_planned` status; `spec.yaml` is written by a new
caller (the SCR channel is unchanged). All additive.

Suite 1766 hermetic tests (as measured by CI at the tag; 1761 locally,
where the five mutation-testing cases skip for want of `mutmut`).

## v0.73.0 — a founder says three things and gets three answers

Minor. A founder wrote one message listing three problems with their
product. The Studio showed one classification card, repaired one feature,
and returned to the home page. The other two were not refused, not queued,
not logged — they were gone, and nothing on screen ever said so. Reporting
the same problem twice is what a founder does when the first report seems
to have been read, so the failure was also self-concealing.

It was singular in three places at once, which is why nothing caught it:

- `_ROUTER_SYSTEM` said "map the complaint to the ONE responsible feature"
  and gave the model a reply shape — `spec_slug` / `kind` / `instruction` —
  with no field a second issue could occupy. A model asked for one answer
  is not withholding the rest; it has nowhere to put them.
- `route_complaint` validated that the one returned slug exists. Nothing
  compared the routed issue against the message it came from, so leftover
  problems were not a state the system could be in.
- The repair stage collected `_related_sources` for the routed spec only,
  so even a router that had somehow named three features would have handed
  the implementer the files of one.

Now the router splits first. `route_complaint` returns a **list** — one
route per distinct problem, in the founder's order — and each route carries
the founder's own words for *its* issue. That quote is checked the way
intake checks a "said" value (§13.26.7): a whitespace-normalised span of
what they actually wrote, or it is dropped, because a summary shown back
under the heading "Your words" is a lie the founder cannot catch. A slug
the workspace does not have fails the whole plan loudly rather than
routing the rest — keeping the routable issues and discarding the others
would be the original defect wearing a plural.

The classification page shows every issue as its own card, with the whole
original message above the split so the founder can see nothing of theirs
went missing. Each card has a checkbox, ticked, because "fix all three"
and "fix the first, I was thinking aloud about the others" are both
reasonable and only the founder knows which. Confirming runs every ticked
issue as its own repair against its own spec — one correction, one commit,
one log line, unchanged — and the founder lands on a page stating what
happened to each, instead of a redirect home that was fine for one result
and a lie for three.

Details:

- `run_corrections` is the new entry point; `run_correction` still repairs
  exactly one issue, and now **refuses** when handed an unrouted complaint
  that turns out to hold several, naming the plural entry point. Silently
  picking the first is the defect, so it is not a fallback.
- One issue that cannot be repaired does not stop the ones after it. Every
  issue gets a result, whatever it is.
- `avs correct` prints one line per issue and **exits 1 if any failed** — a
  run that repaired two of three and exited 0 reports success for the one
  it could not do.
- The router's `max_tokens` went 512 → 2048. A truncated list parses as
  valid YAML that is quietly short, which is this bug again.
- A single-issue complaint renders exactly the page it always did: one
  decision, no checkbox, no plural. The fix must not tax the common case.

## v0.72.3 — the scheduler tells you when it is running an old build

Patch. Releasing v0.72.2 exposed the gap between *published* and *deployed*:
the LaunchAgent's plist names an absolute path to an `avs` binary — whichever
install was on PATH when the agent was armed — and that is a different install
from the one a release is cut with. `git push`, a green publish and a new
version on PyPI move nothing on the machine. The daily loop went on running
v0.72.1, including the metering fix it did not have, and would have kept doing
so until somebody thought to check by hand.

That is the same shape as every bug `avs cadence` exists to catch: a green
report over a stale reality. So it is a mechanical check now.

`avs cadence` reads the installed plist, asks the scheduled binary's own
interpreter which version it has, and compares. A scheduler running an older
build than the reporting one is a finding: it prints both versions and the
exact `pip install --upgrade` line for that install, and **exits 3** — the
same code as an overdue loop, because a yellow line alone gets scrolled past
and the exit code is what a script reads.

Details that matter:

- The probe asks for **distribution metadata** (`importlib.metadata.version`),
  which is what pip wrote to disk and cannot drift, falling back to
  `__version__` only for an uninstalled checkout. `__version__` is
  hand-maintained and has drifted before — 0.70.1 shipped inside both v0.71.0
  and v0.71.1.
- It probes the **interpreter** named in the console script's shebang rather
  than running `avs --version`, because it has to work against builds older
  than the one asking, and those are precisely the builds that lack any flag
  added later.
- Only *older* counts. Running `avs cadence` from a development checkout while
  the scheduler holds the last release is normal and is not reported.
- An install too broken to import is reported as unreadable, never as current.

Also: `avs --version`, which did not exist.

## v0.72.2 — metering stops being something each command has to remember

Patch. v0.72.1 fixed `compound`'s missing ledger flush by hand. Fixing the
one caller that was caught is how the same bug comes back: the audit that
followed found **2 of 77 commands** flushed the buffer, and `gepa` — a
provider-calling path with no production caller yet — was about to inherit
the gap the moment someone wired it up.

Three changes, smallest to largest:

- `gepa.write_proposal()` flushes. It is the terminal step that knows a
  workspace, and closing it before gepa has a caller means whoever wires it
  up inherits the metering instead of the leak.
- `smoke` needed no fix — it already flushed, including on the
  release-blocking exit-1 path — but nothing pinned that. Now a test does,
  on the failing path specifically, because an early exit is exactly where a
  later edit drops a flush.
- Every command flushes, once, in one place. A wrapper around each
  registered command persists whatever was buffered to the workspace the
  command was already pointed at (`--repo-dir`/`--workspace`). An empty
  buffer flushes to nothing, so this is inert for commands that never call a
  provider, and an unwritable ledger can never mask the command's real
  result.

ADR-032 removed the spending *cap* and kept the metering deliberately. This
makes the kept half structural rather than a rule each new command's author
has to already know.

## v0.72.1 — the compounding loop writes down what it spends

Patch. Found by arming the scheduler and reading the ledger afterwards:
`avs compound` called a provider, produced a real proposal, and the
workspace's `.mas/spend.jsonl` showed **zero calls**.

The provider adapter buffers usage in process state; only a caller that knows
the workspace can flush it to disk. The review graph, `build`, `autopilot`
and the Studio all flush. The compounding loop never did — so every run spent
real money and left no trace of it. Survivable while it was hand-run and
occasional; not survivable once `avs cadence` put it on a daily LaunchAgent,
where it becomes a standing recurring unmetered cost.

`compound` now flushes and prints the workspace cost line. ADR-032 removed
the spending *cap* and deliberately kept the metering; this is the kept half,
and it is now pinned by a test that was verified to fail without the fix.

Audited the rest: `sweep` is deterministic and calls nothing. The other
provider-calling modules (`leader`, `verify`, `studio_chat`) flush through
their callers. `gepa` and `smoke` do not, but neither is on a scheduler.

Suite 1728 hermetic tests (as measured by CI at the tag).

## v0.72.0 — a loop that read nothing no longer reports as a loop that worked

`cadence` decided freshness from the *existence* of a dated artifact. Run
`avs compound` against a workspace whose 7-day window holds no reviews and it
writes a proposal without ever calling a provider — and that file then reads
as a healthy run for a week. The narrow form of the same "looks done" this
feature exists to prevent, and it was demonstrable, not theoretical.

The fix separates two things a date cannot tell apart:

- **Read nothing** (`Window: 0 review(s)`) — the loop ran, so it is *not*
  stale and does not fail a gate, but it is now marked `vacuous`, rendered
  `ok, empty`, and named in the summary: "all 3 loops within cadence, but 1
  loop had nothing to read: compound".
- **Read plenty and concluded nothing** — twelve reviews where no constraint
  cleared the evidence bar is work with a real negative result, and is
  reported as such.

Sweep is deliberately exempt: invariant 14.30 makes a clean pass a finding
that must be recorded rather than pass silently, so an empty sweep is real
work. Its own `note` is carried through instead. An unrecognised proposal
format claims nothing in either direction rather than guessing.

`avs cadence` gains a **"last run produced"** column.

Also fixed: **`__version__` had drifted to 0.70.1** and shipped that way in
both v0.71.0 and v0.71.1 — nothing read it, so nothing caught it. It is now
pinned against `pyproject.toml` by a test, along with the CHANGELOG's leading
entry, because the release checklist that was supposed to catch this is
exactly the kind of habit this release is about not relying on.

Suite 1726 hermetic tests (as measured by CI at the tag).

## v0.71.1 — the trigger gets an environment

Arming v0.71.0's LaunchAgent against a real product workspace surfaced the
one thing that would have made it useless: **launchd does not read a login
shell.** A credential the operator keeps in `.zshrc` — here an
`ANTHROPIC_API_KEY_FILE` pointing at `~/.secrets/` — is simply absent at
09:00. `compound` would have reached its provider with nothing and failed
every morning into a log file nobody opens, which is the silent-success
failure this whole feature exists to prevent, reintroduced by the installer.

The plist now carries an `EnvironmentVariables` block:

- **Pointers travel, secrets never do.** `ENV_POINTERS` (`*_KEY_FILE`,
  `ANTHROPIC_BASE_URL`, `AWS_PROFILE`, …) are copied. `ENV_SECRETS`
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) are **refused by name** — the
  plist is a readable file in `~/Library/LaunchAgents`, and writing a key
  into it would turn the scheduler into a credential leak. The refusal names
  the variable so the operator converts it to its `_FILE` form instead of
  debugging a silent 401.
- **PATH is set explicitly.** launchd's default is a bare
  `/usr/bin:/bin:/usr/sbin:/sbin`, which does not contain the interpreter
  `avs` was installed into.
- **No credential at all is a warning at install time**, not a discovery a
  week later.

Also corrected: `install_agent` now reports `env_keys` and `warnings`, and
`avs cadence --install` prints both.

Suite 1718 hermetic tests, 3 skipped.

## v0.71.0 — the recurring loops get a trigger, and the build loop gets a floor

Three loops in this system were designed to recur — the compounding loop
(§09.8), the Sweep role (doc 29), weekly attention collection (doc 25
§76.4) — and none of them had a trigger. "Weekly" was a habit, and this
repo is the evidence of what a lapsed habit costs: `attention.py` opens by
saying a missed week does not merely lose a week, it **resets the streak
the kill criterion depends on**, and `metrics/attention-log.yaml` records
the discipline starting 2026-W31 and then stops. Nothing reported the
lapse, because nothing was watching.

**`avs cadence`** reads the artifacts the loops already write — a
`proposal-<date>.md`, a `digest-<date>.yaml`, a `logged` attention row —
and says which loop is overdue. It exits 3 when one is, so it can gate a
script. Three rules keep it from lying: a loop that never ran reports
`never_run` rather than fresh (the one failure a watchdog can actually
have); a `not_tracked` attention week is not a run, or the series the kill
criterion is falsifiable by could look maintained while measuring nothing;
and a future-dated artifact clamps to zero instead of inventing a negative
staleness. Seven days is `due`, ten is `overdue` — a weekly loop is seven
days old on the day it is next due, and that is health.

**The trigger is machine-local, and had to be.** The obvious move was a CI
cron, and it would have been wrong: every artifact these loops read lives
under `.mas/`, which is gitignored. A runner checks out an empty `.mas/`,
finds every loop `never_run`, and gets tuned until it reports a clean pass
forever against state it cannot see — the "looks done" failure with a green
check on top. So `avs cadence --install` writes a user LaunchAgent instead.
It fires **daily** and runs only what is due: a weekly timer has one chance
to be missed, a daily due-check has seven, and finding nothing due costs a
file-stat. `--run-due` is idempotent for the same reason.

It is written disarmed. `--install` writes the plist and prints the
`launchctl` line; `--arm` loads it. `RunAtLoad` is false, so installing a
trigger never starts a run. And `avs attention` is only ever run in its
read-only form: it logs a row solely with `--confirm-hours`, that number is
the operator's, and the scheduler surfaces the ask without answering it.
Mechanical recurrence is the machine's job; the judgment is not.

**A termination bound on the build loop.** `MAX_ITERATIONS` bounds attempts
per task and `max_tasks` bounds tasks, but a task whose context grows each
iteration can consume without limit inside those counts, and the ledger
said nothing until the run ended. `avs create` now stops between tasks once
a run passes `--token-ceiling` (default 10M).

This is **not** a spending cap and ADR-032 stands: it counts tokens, never
dollars, no price table can make it fire, and a month of heavy spend still
builds — pinned by a test that says so. Ten million is roughly 3× what a
full 12-task build arithmetically costs; a run that crosses it is looping,
not working. The status is its own word, `halted`, not `failed`, because
nothing failed: it stops at a task boundary where stopping is free, keeps
every built module, takes no undo checkpoint, and a re-run continues from
there. `--token-ceiling 0` disables it.

Also: the two remaining runtime `assert`s in `cli.py` are explicit checks
that raise with a diagnosable message (CLAUDE.md — an `assert` holds only
until someone runs under `-O`, and then the next line raises
`AttributeError` on `None` instead). And three tests were reading the
developer's own shell: the preflight resolves a key through `env_or_file`,
but the enterprise preflight and journey tests cleared only
`ANTHROPIC_API_KEY`, never `ANTHROPIC_API_KEY_FILE` — so a machine that
keeps its key in a file saw `model: ready` where the test's own comment
said "no credential in this test env", and the suite failed there and
nowhere else. The second and third sites were hidden behind the first until
the run went past `-x`.

Suite 1714 hermetic tests, 3 skipped.

## v0.70.1 — a name declared twice renders every importing page blank

A patch release: no command, file format, or route changes. One gate check
and one test repair.

**The 小程序 loadability gate now catches duplicate top-level declarations.**
Found the hard way in a product workspace: a second `pad2` was added to a
utils module, and **106 `node --test` cases stayed green while the app was
entirely broken** — the cart, delivery and profile pages all registered no
`Page()` and rendered pure white. Only the DevTools run found it.

The mechanism was verified rather than assumed, because the obvious
explanation was wrong: `function a(){} function a(){}` is legal in a sloppy
script **and in a strict one**. It is a SyntaxError under ES-*module*
semantics, which is what the 小程序 toolchain compiles with — so the module
never evaluates and everything importing it goes blank. Node's own test
runner loads the same file as a script and sees nothing wrong, which is
exactly why the suite could not catch this.

The gate reports duplicate top-level `function`/`const`/`let`/`class` names
per module. `var` is deliberately excluded: re-declaring a var is legal
everywhere. Validated against the real incident in both directions —
re-introducing the exact second `pad2` on a copy names the file, the
identifier, the count and the consequence; the repaired workspace passes.

This is the third member of the same family, all mechanically detectable
with no DevTools and no LLM: a page file that does not exist, a `require`
that escapes `miniprogramRoot`, and now a module that cannot evaluate.

Also: two README media assertions follow the v0.70.0 screenshot rename
(`studio-en.png` → `studio-en-v070.png` and siblings), which had shipped
without them.

Suite 1689 hermetic tests — and this is the first release whose publish run
enforces PC-1 itself: a claimed test count that disagrees with the count
that just passed now fails the release rather than publishing a number
nobody measured.

## v0.70.0 — what a real run said, and the repair for what it broke

One live English run through the shipped Studio, driven in a browser to
replace the README's pre-redesign screenshots. It found six defects that a
green hermetic suite had not, because each needs a real model, a real price
list, or a real rollback to appear.

**Every artifact a founder reads now follows the FDR's own language.** The
confirmation prompt said "in the SAME LANGUAGE as the FDR" and then
demonstrated its section headings as `会做什么 / What will be built` — and a
demonstration beats an instruction, so the model copied the frame and wrote
the body in Chinese over an entirely English FDR. The one page a founder
must read *before spending money* was not in their language. The headings,
the counted tally and the cost heading now come from a per-language table
keyed by the FDR itself, detected deterministically (any CJK ⇒ zh), because
a model call to decide which language to answer in is a model call that can
get it wrong.

**`built: true` rides inside the task's own commit.** Written afterwards it
was an uncommitted working-tree change, and two recovery paths discard those
wholesale: `git checkout -- .` when a fix iteration is rolled back, and
`_reset_workspace` after a failed build. A run that committed six modules
kept the flag on two. The wrong headline number ("2 of 6" over a finished
product) was the visible half; the expensive half is that `built_task_ids`
is what a resumed run reads to decide what it may skip, so the next
`avs create` would have rebuilt and re-billed four committed modules.

**`avs reconcile [--scan DIR] [--apply]`** repairs that damage where it
already exists, since no code change can fix a workspace built last week.
It restores a flag only where outcomes.yaml AND a commit naming that spec
(or the title its commit subject embeds) already agree; a task they disagree
on is named for a human and left alone. A task is the unit, not a spec file:
planning is not deterministic, so a re-run leaves the same task a second
spec under a slightly different slug, and repairing those leftovers would
resurrect specs a later plan replaced. Keyed on spec files the first version
reported eleven findings across three real workspaces and nine were false.

**The wait stopped claiming a number it did not have.** `"$0.00 so far"` ran
for twenty-four minutes of a build that was spending: the guard covered *no
calls at all* but not calls whose models have no price, and the building page
then stripped the `≥` floor marker off the figure it did print. A founder
reads a zero as "this one is free"; it meant "this workspace has no price
list". No figure now, and the floor marker survives where there is one.

**And one NOW, not three.** Both renderers read "pending + a step" as
in-flight, and every task that had ever narrated anything still carried its
last line — so a sequential build showed three modules building at once, of
which at most one could be true.

Plus the README's founder demo is that run: the flow GIF, the single screen,
and the `--lang zh` still are all the redesigned Studio, and the caption
states the run's real outcome and the defects it exposed. The honesty test
moved with it rather than being relaxed — "partly built" was this demo's
admission against interest and would now be the lie, so the test pins the
unedited claim plus at least one concrete admission, and fails a caption
that admits nothing.

1688 hermetic tests.

## v0.69.0 — the key, the paragraph, the product, and the decision

The four proposals the Studio redesign deliberately left unbuilt, built —
each one where a founder actually meets it.

**The key gate.** The tool is free and the model is not, and that fact used
to reach a founder as a stack trace on their first send. A keyless Studio on
a paying provider now opens on whose-account-pays language, the doors that
need no key typed here (`AVS_ANTHROPIC_MODE=bedrock|vertex|foundry`, a
gateway bearer token, a mounted `*_API_KEY_FILE`), and `/demo` — the
vendored recorded run, rendered through the same timeline the workspace's
own reviews use, readable with no key at all. A pasted key is set for that
process only and never written to disk; persisting it was refused, because a
key in a workspace is a key in somebody's next `git add`. Detection is
boolean through `env_or_file`, so the value is never returned, rendered or
logged. A key that gets REFUSED carries the paste box on the page that names
the problem instead of a trip back through settings. And a **token-gated
(shared) Studio does not offer the box at all**: there the process is
everyone's, so one person's key would pay for every build anybody on that
token starts, while the form's own "this process only" reads as "my session
only".

**One paragraph, then only the gaps.** Six fixed questions asked in order
was a form wearing a chat's clothes. Now: one open prompt, one extraction
pass, SAID/GUESS rows, and questions only about what is genuinely missing.
The charter rule (§13.26.7 — agents never author a user need) is enforced in
Python, not in the prompt: a `said` value that is not a whitespace-normalised
span of the founder's own paragraph is **demoted to a guess**, guesses are
capped, and an unparseable response falls back to asking all six. A guess
never reaches FDR.md until the founder confirms it — verified against a
deliberately lying model, whose fabrication was demoted and kept out of the
document. The paragraph itself is kept verbatim, because a span is not the
framing.

**Try it, beside its own acceptance list.** `/try` puts the criteria the
product was supposed to meet next to it as rows the founder marks Fine or
Wrong — and a complaint made from a row travels WITH that row, so the router
knows which criterion failed instead of receiving a bare sentence. A tick is
the founder's note, never a verdict, and the page says so. The Studio does
not start the product for you: a server spawned by a page load is a process
nobody owns on a port nobody chose, so the page names the real command and
the detected entry point.

**The decision, before the work.** A complaint is classified before it costs
anything — SMALL FIX repaired in place, or NEW REQUIREMENT that gets its own
plan — showing the founder's words, the feature, and what the implementer
will be told, with Reword one click away and "nothing has been changed yet"
on the page. One router, extracted and shared, not a second classifier. And
the change log becomes the undo surface: each entry offers *go back to just
before this change*, stating how many later changes and commits go with it,
because the history is a straight line of checkpoint tags and pretending
otherwise would promise an independence the model does not have.

**Also:** the wire-up gate grew the half it was missing. It proved every
button pointed at a real route and every route was pointed at by some page,
but it rendered pages and never PRESSED anything — so two unvalidated path
segments lived in that gap: `/feature/build`'s `slug` walked the build out
of the workspace entirely, and `/retry`'s unknown `task_id` spawned a worker
that died on arrival and left its pid file behind, so the Studio showed a
build in progress that could never finish. Both now take the segment rule
the review and incident ids already carried, well-formed-but-absent is
answered out loud, and every POST route is pressed with the body its own
forms send.

1655 hermetic tests.

## v0.68.1 — the diagnosis stops lying, and the Studio reads like a product

A patch release: no command, file format, or route changes. Everything below
either corrects a message that pointed somewhere wrong, or restyles a
surface without moving it.

**The 小程序 automation diagnosis was wrong three ways, each caught by using
it.** A timeout was reported as "almost always the service port", which sent
an investigation to re-check a toggle that was already on — the CLI had in
fact printed its success marker, and DevTools was simply compiling a project
it had never opened (measured: first open >300s, second 27s). Then the
corrected message lied differently: it read the *whole* CLI log, which is
appended to across runs on purpose, so a `✔ auto` from an earlier run was
read as this run's success — reporting "first-open cost" for a session that
had exited after four seconds, while quoting the 300s ceiling as if it were
the measurement. The check now snapshots the log size before spawning and
reads only what this run wrote, reports the wait as measured, and treats an
early exit as its own diagnosis (a session already open on the project,
naming `pkill -f 'cli auto'`). The success marker is matched as a whole line,
never as a substring — this workspace's own path contains "auto", so a
substring test would call every failure a success.

**A directory named `miniprogram/` no longer buys silence.** Found by
pointing the new gate at products built before it existed: one workspace
keeps its mini-program at the repo root — `app.json` with three registered
pages, `pages/` beside it — next to a stray `miniprogram/` holding nothing
but a `.DS_Store` and an `api/` folder. `_miniprogram_root` picked the stray
on its *name*, found no `app.json`, and the gate answered "nothing built here
yet" — a vacuous pass over a real product, which is the exact silent no-op
this gate exists to prevent. With nothing declared, evidence now beats
directory names: the candidate that actually holds an `app.json` is the
project. A declared `miniprogramRoot` still wins, because that is the
platform's own answer. Four defects became visible on the affected workspace
the moment the gate could see it.

**The scaffold stops shipping a future AppID leak.** A new workspace's
`.gitignore` now covers WeChat DevTools' per-machine artifacts:
`project.private.config.json`, and the `project.config.json` DevTools writes
*inside* `miniprogramRoot` carrying the logged-in account's real AppID —
where the scaffold's root config deliberately uses `touristappid`, because a
real AppID is somebody's identifier and not ours to commit. That inner file
also shadows the root config for tools that look there first.

Corrected in [the pipeline guide](docs/miniprogram-pipeline.md), because a
wrong note becomes tomorrow's cargo cult: **`libVersion: "latest"` is fine.**
Tested both ways on a cold IDE with identical results — it breaks
`miniprogram-automator`'s `checkVersion` (an undefined `SDKVersion`) but not
the raw driver, which never version-checks. The guide also now records that
the IDE degrades over a long automation session: after many open/relaunch
cycles `captureScreenshot` times out and app calls fail with a bare
`Uncaught [object Object]`, on a project that passes cleanly after a cold
start. A run failing in the driver rather than in the assertions means
restart the IDE, not that the product broke.

**The Studio worked and read as a debug page; now it reads as a product.**
Nothing new is fetched, no client framework, no new source of truth — still
server-rendered HTML over the same workspace files, still bilingual, **every
route unchanged**. A design system replaces the styling accidents: one white
card on a warm ground, one green primary action (the button that spends real
money no longer looks like "Triage it"), a persistent Describe → Plan →
Build → Your product stage rail, and hint text that clears AA instead of
sitting at ~3.5:1 while carrying real instructions. `CONFIRMATION.md` and
`BUILD-REPORT.md` are rendered rather than dumped as one grey `<pre>`, with
the "will NOT build" section lifted into its own callout because that is the
cheapest place to catch a misunderstanding. The build view gains the elapsed
clock and accrued spend that were already on disk and thrown away, plus
DONE/NOW/QUEUED per module — still no percentage and no ETA, because the
system does not know whether attempt 2 is the last one. Modes reorder instead
of appending, and add-only is intact: nothing is removed in any mode. Two
layout defects found by driving the real UI in a browser rather than in
tests: an unbreakable git-identity token pushed the preflight table into the
trust table beside it, and a fixed-width state chip overflowed into the task
title for any state longer than `QUEUED`.

**A task id from a form is never taken on trust.** A well-formed id that is
not in the plan used to spawn a worker that died on arrival — leaving a pid
file, so the Studio showed a build in progress that could never finish. It is
now answered out loud. (That guard made a `/retry` test's planless fixture
stale; the fixture was repaired, and the property it protects — `--provider`
travels, output lands in `.mas/build.log` and never `DEVNULL` — is intact.)

Suite 1572 hermetic tests.

## v0.68.0 — a blank page is no longer a passing page (小程序)

The 小程序 runtime check reported **"all 7 registered pages rendered"** for a
build in which three were pure white. Nothing lied: the check's only signal
was that `reLaunch` did not throw, and it does not throw for a page whose JS
died before `Page()` ran — the page still opens, still sits on the page
stack, and still renders nothing. This release makes the evidence match the
claim, at both the static and the runtime rung.

**Static (free, no DevTools, runs in CI).** The loadability gate now walks
the relative `require()`/`import` chains from `app.js` and every registered
page, failing on a specifier that resolves to no file and on one that
escapes `miniprogramRoot` — DevTools cannot package what is outside the
root, so that import throws at evaluation time and the page goes blank. The
incident that named this: a builder wrote `utils/telemetry.js` at the repo
root and three pages imported it by three different relative paths; every
page *file* existed, so the gate passed 7/7.

**Runtime (`avs mp-runtime`, your machine only).** Every page is now
screenshotted into `.mas/mp-runtime/`, and **a single flat colour is a
`page_blank` finding** — a judge that is cause-agnostic, so an empty WXML
and a dead require fail the same way. PNG decoding is stdlib-only (IHDR,
inflate, the five scanline filters); anything exotic is conservatively
treated as not-flat, so an encoding surprise can never fail a healthy page.

Two driver defects fixed with it. The check no longer drives through
`miniprogram-automator`, whose `launch()` **and** `connect()` hang without
diagnosis against IDE `2.01.2510290` — it speaks the automation protocol
raw over WebSocket and spawns `cli auto` itself, whose own words land in
`.mas/mp-runtime/cli-auto.log`. And it never reuses a listening automation
port: a leftover session serves whatever project *it* opened, which once
verified the wrong app under this project's name, all green. Each run takes
a free port from 9420–9439, spawns its own session, and terminates it in a
`finally`.

Validated against the incident in both directions: a copy of the affected
workspace with one require path re-broken reports `page_blank` on exactly
that page; the repaired workspace reports ok on all seven. The flat-colour
judge separates every real screenshot from that investigation — three blank,
three rendered.

Also in this release:

- **A re-run remembers why it failed.** Recorded failure context reached
  only same-run retries, so pressing "continue the build" re-attempted every
  failed task blind — same inputs, same writer, same wall. Run 2's *first*
  attempt at a task run 1 could not build now carries run 1's diagnosis, on
  both the sequential and the parallel-wave path.
- **Continuing is one click, not one per module.** The Studio's interrupted
  page and failed-modules card lead with a single "Continue the build"
  (locked plan reused, built modules skipped, failures re-attempted with
  context); per-module buttons remain for surgical retries. The `/retry`
  path also regained two previously-fixed properties, now pinned by tests:
  its output goes to `.mas/build.log` rather than `DEVNULL`, and it inherits
  the Studio's `--provider`.
- **The scaffold's index page no longer ships blank by construction** —
  `{{title}}` was bound to an empty data object, and that page is where a
  share-QR scan lands.
- [docs/miniprogram-pipeline.md](docs/miniprogram-pipeline.md): the four
  rungs (unit tests → loadability gate → runtime screenshots → a
  per-product designed-flow script), each with the failure that justified
  it, plus the operational notes that cost a session to learn — zombie
  `cli auto` sessions block later handshakes, cold project boots take
  60–150s, and `libVersion: "latest"` breaks the automation handshake.

Suite 1560 hermetic tests.

## v0.67.0 — spend is measured and reported, never gated (ADR-032)

An operator decision, recorded as
[ADR-032](docs/adr/032-no-framework-spending-cap.md): every model call is
billed to the operator's **own key or subscription**, so budget enforcement
belongs to the provider account that does the billing. Provider-side
spending limits are authoritative — they see all usage on the key, not just
this framework's slice, and cannot be bypassed by a bug here. A
framework-side cap duplicates that control at best and contradicts it at
worst: for subscription billing, tokens do not map to marginal dollars, so
a token-priced cap would pause builds over money that was never being
spent. The gate's own refusal message already had to strain to attribute
the stop to the operator ("the limit YOU set") — a sign the mechanism sat
on the wrong side of the ADR-U20 boundary ("the framework never spends
money").

**Removed:** the monthly cap and everything that refused over it —
`cost_gate` at Gate 1 and per build task, the auto-retry precheck,
`monthly_cap_usd`, `avs prices --cap`, the Studio ceiling form and its
`/cap` route, and the `avs cost` exit-3 path. An old `cost-model.yaml`
carrying the retired key still loads; the key is ignored, never an upgrade
error.

**Kept, deliberately — the answer to "how much will a typical month cost
me?" is visibility, not refusal:** the ledger metering every call at the
adapter, the build report's arithmetic cost line, `avs cost` per model, the
Studio cost card on the confirm page (before the first dollar) and the
report page, and the sourced reference price table as pure estimation —
ranges still resolve upward, operator corrections still survive re-import,
and an unpriced call still keeps the total labelled a FLOOR rather than
counted as zero. The surfaces now point at provider-side limits, where a
ceiling is both effective and complete.

The removal is pinned as firmly as the presence was: a month of heavy spend
must not stop a build or a review, the Studio renders no ceiling form and
no refusal copy, `POST /cap` is gone, and legacy-key compatibility is a
test.

## v0.66.0 — the spend guard: the ceiling reaches the one persona that cannot use a CLI

Built from research rather than intuition. The published record on
non-technical builders using AI app tools converges on a short list of
failure modes: the bug doom loop that burns credits, the comprehension debt
of owning code you cannot read, the prototype-vs-product gap — and, at the
top, **surprise bills**: runaway usage-billed sessions ($607 Replit bills,
credits gone overnight), with no major platform setting a spending cap by
default and every guide's first recommendation being a hard cap on day one.

Cross-validated against this codebase mode by mode, most of that list was
already closed — bounded build iterations with test feedback, the auto-retry
with failure context (v0.65), plain-language reports that never blame the
founder, probes generated from the founder's own requirements. Two gaps
survived the check:

**The cap existed and the founder could not reach it.** `avs prices --import
--cap` shipped in v0.65 — CLI-only. The Studio's cost card showed spend with
no ceiling on it, to exactly the persona the industry data says is most
burned by the missing ceiling and least able to use a CLI. The card is now
the **spend guard**: this month's spend against the cap (honestly labelled a
floor when a call is unpriced), a one-click set-cap form that writes the
same `.mas/cost-model.yaml` the CLI owns (packaged reference prices ride
along so the cap can actually fire; a price the operator corrected is never
overwritten), and — when the cap is reached — "builds are paused between
modules, nothing is lost, raise it to continue", because a cap doing its job
must never read as a failure. It sits on the **confirm page**, next to the
button that starts the spend, not only on the report page where the bill
already exists — and it shows before the first dollar, where the old card
hid itself until money had been spent. Mode-adaptable, add-only: engineer
gains the per-model table and the CLI twins (`avs cost`, `avs prices`);
enterprise gains the governance note; the founder card is complete on its
own, in both languages.

**The verification nobody could see.** Probes generated from the founder's
own FDR run against the built product and write `VERIFICATION.md` — the one
artifact that answers "does it actually work?" without asking a founder to
judge code — and it was never linked anywhere. The Studio now serves it and
links it beside the acceptance walkthrough. The Studio's own wireup gate
caught the first draft of this change (a route rendered by no state), which
is the gate doing precisely what it was built for.

## v0.65.0 — the run presses its own retry button, and the cost cap can fire

Two changes, both against the failure modes that matter most for autonomous
development: a run stopping to ask a human for something mechanical, and the
same error recurring because nothing carried the diagnosis forward.

**A failed task no longer ends with a button.** Every `spec_blocked` /
`build_failed` / `merge_conflict` outcome used to be recorded and left for
the founder's retry button — and the bench record shows that button usually
worked (t1/t2 recovered on the second pass, t5/t9 built on retry). Pressing
it takes patience, not judgment, so the run presses it itself: **one bounded
retry pass** after the first pass over the plan, in dependency order (a task
that failed because its dependency failed retries *after* the dependency
recovered), gated on the cost cap (a run over its cap does not retry itself
deeper into the cap), every attempt and result recorded in the report's
auto-approvals. Applies to `create`, the parallel-wave path (a
`merge_conflict` retried serially is exactly the fix), and `add`. The
judgment gates are untouched: FDR questions, plan confirmation, and review
escalations still wait for the human — those stops are the point.

**A retry is a different attempt, not a replay.** The spec writer and the
implementer had no channel for "the previous attempt failed because X", so
every retry started blind — the writer picked the same rejected phrasing,
the implementer re-invented the same phantom import (run-3 forensics). The
previous attempt's status, detail, and test summary now travel into both
prompts as `<previous_attempt_failed>`; `avs retry-task` reads the recorded
failure out of `outcomes.yaml` and passes it the same way; and a retry that
also fails keeps **both** diagnoses so a later attempt starts from the
accumulated history.

**And the bug that made recovery invisible.** A resumed run that rebuilt a
previously-failed task *appended* a second outcome row, so the tally counted
the ghost: a workspace whose failed task was rebuilt on the next run
reported "3/4 modules built" and status `failed` for a product that was
entirely built — then asked the founder to retry work that was already done.
A run that says failed when it succeeded is a manufactured stop-and-ask.
Outcome rows are now replaced, never shadowed.

The per-task attempt (spec → Gate U3 → build → review → repair) is now one
shared code path for the first pass, the auto-retry, and the feature flow —
hand-copied variants are how `retry-task` once shipped without the review
gate. The feature path gains review parity and progress narration for free.

**The cost cap can fire** (`avs prices`). The gate has been complete since
v0.59.0 and inert ever since: with `prices` empty, every call was UNPRICED,
the month's total was a FLOOR, and a cap compared against a floor never
bites. The package now ships published list prices with a vendor URL and a
retrieval date per provider — the same evidence standard `claim_lint`
applies to every other number in this repo — and `avs prices --import
[--cap <usd>]` writes them into `.mas/cost-model.yaml`, where you own them.
Ranges resolve **upward** (Sonnet 5 carries the standard price, not the
introductory one; Gemini's larger-prompt tier is the one recorded), so the
estimate is a ceiling, not a guess. Your own price always survives a
re-import; a model with no sourced price (grok-4) stays visibly unpriced
rather than invented, and keeps the total labelled a floor. Verified end to
end: a simulated month reported `spend ≥$46.76 of $25.00 cap`, refused new
work naming the file the cap was set in, and the auto-retry pass respects
the same gate.

Also: a test-isolation bug found by that work — spend is recorded into a
process-global buffer, so a test that recorded without flushing leaked rows
into the next test's workspace ledger. The suite now drains the buffer
around every test.

## v0.64.0 — the rules that were only ever sentences

Five things this system said and did not enforce. Every one had the same
shape: a rule written in prose, next to a code path free to ignore it — and
in four of the five cases the code path had already been taking the free
option in real runs.

**Gate U2's scope lock was decoration.** `approve_plan` wrote
`status: locked` and the next `avs create` re-decomposed the brief anyway:
assess, discovery, four charter voters with a verify pass, a leader, then
planning — minutes and real money — to arrive at a *different* plan, because
planning is not deterministic. That is also what made resume unable to
recognize its own work, since ids are positional: run 2's `t4` (购物车与结算UI)
matched run 1's `t4` (结算页与下单记录界面). A locked plan is now the plan,
keyed on a whitespace-insensitive fingerprint of the FDR it came from —
reflowing a paragraph is not a scope change, different words are. After the
lock, different words are refused with the routes named (`avs add`, `avs
scr`, or a deliberate `--replan`) instead of silently re-planned.
Re-running `create` on an unchanged FDR now costs **nothing at all**: the
reuse path makes no model call, not even for the confirmation.

The one place that must never hit that refusal is the Studio's requirements
form. A founder rewriting the FDR there has asked for scope to change, in
the place the product offers for asking, so the lock is released at the
write rather than surfacing as an error two screens later on a page that
cannot explain it.

**A retried module was never reviewed.** `retry-task` ran spec + build and
stopped. In one real run four of the seven modules that reached the founder
came through that path: no reviewer had read them, no fix iteration could
fire, and their rows carried an empty verdict beside modules `create` had
reviewed properly. Gate 3 — review, one bounded repair, re-review, roll back
if it did not clear — is now shared code rather than a copy, so the two
paths cannot drift apart again. The retry path was also the only build path
that never carried the founder's FDR as its `source_contract`, so a retried
module was free to invent field names its siblings had agreed on. And
`setdefault` on a `model_dump` could never fire (the key is always present,
set to `None`), so "keep the verdict from the original run" had been
overwriting it with nothing.

**"A .py file in a 小程序 is always wrong" was a sentence in a prompt.** A
spec came back with `tests/test_catalog_page.py` regardless, which made the
build gate demand a passing pytest run against a project with no Python in
it; the task died three iterations later on "pytest collected no tests" — a
true sentence about the wrong thing. It is now checked at both boundaries:
the spec blocks with the JS alternative spelled out, and the write refuses.
Per-runtime, not a dislike of Python — the web profile *is* Python and is
untouched.

**The mock was the reason that could happen.** It answered with a Python
item-store for every profile, so every hermetic mini-program test was
exercising a product WeChat could never load — 1478 green tests over a
fiction. It now answers in the language the profile actually runs, and a
小程序 autopilot run under the mock builds real JavaScript that
`node --test` executes. The bench case followed: its probe had been
importing a Python module out of a mini-program.

**And the 小程序 "it works" claim is finally checkable.** The build gate's
loadability check is static — it asks whether DevTools *would* open the
project. `avs mp-runtime` opens it for real and visits every registered
page. Three preconditions, each a visible skip naming its remedy: the
DevTools desktop app (macOS/Windows, never CI), `miniprogram-automator`, and
DevTools' service port, which is a one-time human toggle in its security
settings. With the port off the automator swallows the CLI's own
`IDE service port disabled` and simply times out, so that case is reported
as **skipped, not failed** — nothing was checked, and red would read as
"your pages are broken".

### `avs smoke` — and it earned its place on the first run

Twelve real defects were found in one day of running this product against
one real FDR. The suite was green for every one of them, and it was right to
be: they lived where a mock cannot go. The expensive one shipped to PyPI
twice — v0.60.0 and v0.61.0 could not build a single task, because the
implementer asks for 32000 output tokens and the SDK refuses a
non-streaming request that might run past ten minutes, raising before it
sends anything. 1441 tests passed. Every real build died at "attempt 1/3".

Writing more mocks does not fix that; a mock is authored by the same person
holding the same wrong belief about the SDK, so it agrees with the bug.
`avs smoke` makes four real calls per configured provider, costs a fraction
of a cent on your own key, and is now **step 0 of every release**:
`reachable`, `streams_large` (the bug above, as a check),
`truncation_visible` (what stops half a file reaching disk), and
`usage_metered` (so the cost gate is not reading a silent zero). An
unconfigured provider is a loud skip naming the variable — "we did not
check" and "it works" must not look alike.

Its first run found gpt-5 answering empty: a reasoning model at
`max_tokens=16` spends the whole budget reasoning and returns
`content: null` with `finish_reason=length` (512 and up answer normally —
measured, not assumed). Two things were wrong. The check's: no caller in
this system passes 16, so it was testing a configuration that does not
exist. The product's: `choice["message"]["content"]` was returned unguarded
and every caller does `raw.strip()`, so a null reached the voter's generic
retry as an AttributeError about attributes rather than about budget.

Also in this release: `avs create --profile` is optional for a workspace
that already declares one (demanding it on every re-run made resume,
`--yes`-after-confirmation and every later feature an error, and a mistyped
value would have read as a request to change what the product *is*); the
demo scripts left `/tmp`, where one machine's home directory had been baked
into them, for `scripts/demo/` with paths as inputs; and CI moved past the
deprecated Node 20 runtime (`checkout@v7`, `setup-uv@v9.0.0` — that action
publishes no floating major tag, which cost one four-second red build to
learn).

## v0.63.0 — a 小程序 that opens, and a repair that has to prove itself

Seven fixes, all of them found the same way: by running one real founder
FDR — a WeChat mini-program, written in Chinese — three times over and
fixing whatever stopped it. None of them were found by the hermetic suite,
which was green throughout. Every one lived past the point a mock provider
can see: in the assembled output, in the repair loop, in the bookkeeping
between runs.

**Nine modules built, zero of them reachable.** A run produced seven page
directories and no `app.json`, so WeChat DevTools could not open the
project at all. Every module had passed its own tests. The web profile has
had a boot gate since product-bench run 4 — built every task, failed every
probe on "server never listened" — and it was never ported to 小程序,
whose own checks (size, domain allowlist, setData lint) a project with no
entry point passes comfortably. The mini-program gate is static, because
DevTools is a desktop application and cannot be driven headlessly, and it
blocks on exactly what makes a project unopenable: no `app.json`, no
`app.js`, an empty or unparseable `pages` array, a registered page whose
files are missing, and a page directory that exists but is registered
nowhere — a page nobody can navigate to was built for no one.

**A gate can only check a layout somebody guarantees.** Two runs of that
same FDR produced two different trees, and the new gate silently no-opped
on the second because it found no anchor. `avs init --profile miniprogram`
now scaffolds a minimal *loadable* project and commits it, so every task
extends a real project instead of assembling one. The appid is WeChat's
documented `touristappid`, never a plausible-looking one: an implementer
invented `wxb1e7d6736079f6c3` in an earlier run, and that string is either
somebody's identifier or nobody's.

**A repair that made the product worse was committed anyway.** Told that a
cart's `onAdd` handler was never wired to any control, the fix deleted the
handler — the cheapest way to satisfy "this is never used". The next review
called it critical, certain, verified, score 100, "breaking core feature",
and nothing acted on it, because that review ran in the caller *after* the
commit purely to record a verdict. The re-review now runs inside the fix
iteration and can veto: any remaining critical/high resets the workspace to
the commit before the fix, and a rolled-back repair says so in the outcome
rather than looking like no repair was tried. Same number of review calls —
it moved, it did not multiply.

**One unchecked fix iteration cost four modules.** That deleted handler
broke a committed test, and because the build gate runs the whole suite
while existing tests are read-only walls to later implementers, the next
four tasks could not be built by anyone. Not four problems — one, four
times. It went unchecked because the fix iteration validated with bare
pytest, which returns `no_tests` on a 小程序, while the build loop
validates with pytest *and* the JS suite: two standards in one repo, and
the weaker one guarding the path that edits already-committed code.
Separately, and enough on its own to guarantee a cascade, the JS runner
globbed `**/*.test.js`, and `**` walks hidden directories — so it collected
`.mas/failed-builds/<slug>/tests/*.test.js`, the preserved copies of FAILED
attempts. In that workspace it was 31 of 37 matched files: once one task
failed, every later task's gate ran that task's broken snapshot as if it
were the product.

**A reformatted artifact is not a missing one.** A task died on
`grounding violation: required context missing from the implementer's
prompt`, and the writer had not been blind — every acceptance criterion was
there. The probe is the artifact's longest line; here that was a `purpose:`
inside `test_skeletons`, which the prompt renders in a different shape.
Same content, no substring match, and a grounding violation is
unrecoverable rather than retried, so the task was simply lost. Grounding
now tries several probes and grants the receipt if any appears. The bug the
check exists to catch — a prompt assembled without the invariants the
artifact will be judged against — is untouched: an absent artifact scores
zero on every probe.

**Resume skipped work it could not name.** Task ids are positional, and
planning is not deterministic, so `t4` was 结算页与下单记录界面 in one run
and 购物车与结算UI in the next. Matching on the id alone meant the second
run skipped a module that had never been built, while the report announced
"resumed: 1 task(s) already built". Resume now matches on (id, title).

**EARS rejected correct requirements, one clause over from last time.**
v0.62 made the article optional; the writer then phrased its next draft
without the connective and the same task was blocked again by the same
class of pedantry. `then` is now optional too — "If the payload is
malformed, the API shall return 400" is not a worse requirement than the
version with it. A missing `shall` is still rejected. And a successful
`retry-task` now records its result: retries rebuilt and committed two
modules while `product/outcomes.yaml` went on calling them `spec_blocked`,
so the Studio kept offering to retry work that was already done.

Known and unchanged in this release: `approve_plan` sets the plan `locked`
(Gate U2, scope lock) and the next `avs create` regenerates it anyway, so
the expensive upstream is re-paid every run. Reusing a locked plan is a
behaviour change and wants its own release.

## v0.62.0 — the run that could not build, and the page that never said why

Everything here was found by running the product against one real founder
FDR (a WeChat mini-program, written in Chinese) and fixing whatever stopped
it, in order. Four of these are defects that made a whole run impossible.

**`avs create` could not build a single task, on any run, with any FDR.**
The implementer asks for `max_tokens=32000` — it returns whole files, and
16384 truncated real builds — and the SDK refuses a non-streaming request
whose size implies it might run past ten minutes, raising *before sending*:
`ValueError: Streaming is required for operations that may take longer than
10 minutes`. Every build died at "writing the code (attempt 1/3)". Requests
above 8192 output tokens now stream; metering, stop-reason and the
empty-response diagnostics are unchanged and pinned by tests, because a
streaming path that silently dropped usage would break the cost gate and
the truncation guards at once. **v0.60.0 and v0.61.0 on PyPI cannot build;
this release is the fix.**

**A 529 killed runs that were already eleven minutes in.** The transient
retry existed but spent its whole budget in fourteen seconds (four attempts,
2/4/8s). Now six attempts capped at 60s, honouring `retry-after` when the
server sends one, with jitter — the review voters run in a thread pool, and
without jitter they all retry in the same instant.

**A botched final revision threw away a good brief.** Discovery's loop set
`brief = None` on a parse failure, so an attempt that had already passed
schema *and* the four charter voters was discarded when a later polish
attempt came back as malformed YAML. Two runs died holding a usable brief.
It now keeps the last good one and appends a visible note that the final
revision failed, rather than presenting a mid-revision draft as finished.

**EARS rejected correct requirements.** `When fetchFn rejects …,
loadCatalog shall return …` was refused for "does not match any EARS
pattern" because the grammar demanded a literal `the` before the subject —
satisfying it would mean writing "the loadCatalog shall". Three of nine
tasks in one run were blocked by this alone. The article is now optional;
bare subjects must look like identifiers, so `We shall see whether the
founders like it.` is still not a requirement.

**The report blamed the founder for our bug.** Those three blocked tasks
were reported as "三项因为需求描述不够清楚" — three could not start because
the requirements were unclear. False: `spec_blocked` means our spec writer
failed our own checks, and the founder's description is not what is being
checked. The reporter is now told whose failure each status is and told
never to describe a blocked task as unclear requirements. The same report
said "five of nine" for a run that built six, so the tally is now appended
deterministically, the way the cost line already is.

**A conversational way in, and it is now the front door.** One question at
a time, composing FDR.md — the form asked for 4000 characters at once and
then, when the assessor returned five questions, asked you to edit the right
lines inside that textarea. Clarify rounds are capped, every turn offers
"that's enough, go to the plan", and skipping is a recorded answer: a loop
that asks until the model is satisfied is worse than a slightly
under-specified FDR. `--entry form` restores the old landing page.

**The wait before the first module is no longer blank.** Assess, brief, four
charter voters, leader, planning — the longest stretch of a run — narrated
nothing until tasks existed. Still no percentages and no ETA.

Smaller, all found the same way: the FDR form no longer silently overwrites
an FDR that changed under it (a stale tab destroyed five answered clarify
questions, and the assessor then asked them again); a failure names its real
cause instead of asserting a missing API key for everything; failures are
recorded to `.mas/studio-failures.jsonl`; the working page polls instead of
bouncing you to the page you just left; the interrupted page shows why the
worker died instead of only that it did; and `failed_hint` was defined twice
in the string table, so the "modules that did not build" card had been
telling founders their API key was exhausted.

## v0.61.0 — the enterprise you actually work at: GitLab, Bedrock, a proxy

Started by adopting one real enterprise data-pipeline repo (a BigQuery
spend optimizer on self-managed GitLab), then generalized from research
across the enterprise landscape — forge market reality, how enterprises
actually reach model APIs, what security questionnaires ask, and what
breaks behind TLS-inspecting proxies — so the fixes fit the scenario,
not the single example.

- **GitLab is a first-class forge.** Every `gh` side effect — PR comments,
  HITL issues, fix-PRs, head-branch lookup, diff acquisition, the
  policy-gated merge — now routes through a forge seam (`forge.py`) that
  dispatches on the target's URL shape: `/pull/<n>` → `gh` (github.com and
  GitHub Enterprise Server), `/-/merge_requests/<n>` → `glab` (gitlab.com
  and self-managed, subgroups included). The ADR-031 posture carries over
  verbatim: no `--admin`, no force flags, merge reachable only through
  `automation.evaluate_merge`. `avs review <MR-URL>` now works where
  enterprises actually host code.
- **The model door matches enterprise network reality.**
  `AVS_ANTHROPIC_MODE=bedrock|vertex` routes the same Messages API through
  AWS Bedrock or GCP Vertex; an internal LLM gateway authenticates with
  `ANTHROPIC_AUTH_TOKEN` (+ SDK-native `ANTHROPIC_BASE_URL`). Every door
  errors loudly on missing credentials — no silent fallback between doors.
- **`avs map` no longer reports the filesystem as an HTTP surface.** The
  hand-rolled-router heuristics (`path == "/..."`, `startswith("/...")`)
  matched filesystem-path literals all over brownfield code; mapping the
  pilot repo reported `/usr/bin/env` and `/opt/homebrew` as routes — a
  scanner that cannot be trusted on first contact. Heuristic matches whose
  first segment is a Unix/macOS filesystem root are now screened out;
  decorator-declared routes are untouched.
- **The perimeter that cannot expose an endpoint still gets reviews.**
  `avs review --from-ci` derives the target from the pipeline's own
  predefined variables (GitLab CI merge-request pipelines, GitHub Actions
  pull_request events) — the pattern locked-down enterprises actually use
  instead of webhooks. `avs serve` grew a `/webhook/gitlab` route
  (constant-time `X-Gitlab-Token` check — GitLab's design is a shared
  secret, not an HMAC; `update` events trigger only when they carry new
  commits, so metadata edits don't spam the MR). Azure DevOps and
  Bitbucket PR URLs are recognized and refused by name instead of falling
  through to `git diff` on a URL; CodeCommit (closed to new customers
  2024) is deliberately out.
- **The model door matches all four enterprise routes.**
  `AVS_ANTHROPIC_MODE=foundry` adds Microsoft Foundry (Azure) beside
  bedrock/vertex — model IDs stay platform-native and verbatim (ARNs and
  deployment names cannot be derived, so no auto-translation is
  attempted). The cross-family voter seats re-point too:
  `OPENAI_BASE_URL` (also the on-prem vLLM/NIM door), `XAI_BASE_URL`,
  `GEMINI_BASE_URL`.
- **Secrets can live in mounts, not process environments.** Every
  provider key and `secret://` reference accepts the Docker/K8s
  `<VAR>_FILE` convention; a configured mount that cannot be read errors
  loudly instead of running half-armed.
- **Egress is enumerated, and quieter.** The procurement pack gains
  [network-egress.md](editions/enterprise/procurement/network-egress.md)
  — every outbound host with the env var that re-points or disables it.
  The slopsquat check honors an internal PyPI mirror
  (`AVS_PYPI_JSON_BASE`), semgrep runs with `--metrics=off` and a
  pinnable local config (`AVS_SEMGREP_CONFIG`), and playwright moved to
  an opt-in `[screenshots]` extra so the base install never wants a
  browser download a firewall will block.
- **The Studio can be evaluated air-gapped, and its build worker no
  longer dies silently.** `avs studio --provider mock` walks the whole
  founder flow — clarify, plan confirmation, build with per-task
  narration, review, report — offline with a canned product. Found by
  driving the UI in a real browser: the Studio accepted a provider
  internally but the CLI never exposed it, and the spawned build worker
  didn't inherit it — a mock Studio spawned a build that wanted a real
  key and died with its output in DEVNULL, silently returning the
  founder to the confirm page. The worker now inherits the provider and
  writes `.mas/build.log`, so a worker that dies before the report
  leaves forensics.
- **`enterprise-web` joins the profile set** — the web profile plus the
  constraints an IT/security review actually asks about: append-only
  audit records on every state-changing action, `/api/health` for the
  load balancer, env-only configuration with `<VAR>_FILE` secret mounts,
  versioned JSON contracts for named integration consumers, and a
  no-node-assumed frontend stance. It reuses web's block library;
  add-only like every profile (edition_lint posture).
- **The founder's journey no longer ends at "works in this folder" — the
  production loop is in the Studio.** *Take it live* (`/live`): the exact
  boot command every verification used, the persistence story (local DB /
  the SERVICES.md cloud steps, generated on click), the deploy boundary
  stated where the button would be (avs never deploys on its own —
  ADR-031), an is-it-answering-now probe with a remembered last check,
  and the sweep role's housekeeping digest in plain language. *Is it
  broken?* on the product page: a founder sentence becomes a real
  incident — same Incident model, same triage/root-cause MAS, same
  artifacts as `avs triage` — reported back in plain language
  ("A likely cause was found" / "This needs a human"), with a one-click
  fix attempt whose click IS the human approval and whose change
  re-enters review like any PR. Found while wiring it: the probe ran on
  the event loop (a slow URL froze every Studio page — moved to the
  threadpool), and the studio wireup drift test caught two forms that
  rendered in no walked state.
- **`avs preflight` and the enterprise journey as a fixture.** The
  Ready-to-build check exists in the terminal too (`--strict` exits 1 on
  any gap — a pipeline can gate on enterprise readiness before spending
  a token), and the whole enterprise journey — adopt-with-gate-owner,
  readiness starter, substrate declaration, posture transitions,
  preflight truth-telling, every dashboard card — is pinned end-to-end
  in CI against a miniature of the real pilot repo (data-pipeline
  modules, FastAPI surface, filesystem-path traps, GitLab CI, an
  operator-owned CLAUDE.md), so enterprise mode cannot rot against
  exactly the kind of repo it was built for.
- **Enterprise mode opens with "Ready to build?"** — a six-row preflight
  read live from the environment, git config, the forge CLI's own auth
  check, and the workspace: model credential (mock escape hatch named),
  git identity, forge authentication, governance (edition + named gate
  owner), substrate declaration, Studio access posture. Ready rows show
  what was found; gaps show the exact fix command.
- **The Studio can be deployed for a team, fail-closed.**
  `AVS_STUDIO_TOKEN` (env or `_FILE` secret mount) gates every request —
  open `/?token=…` once, a cookie keeps the session; `avs studio --host`
  exists now and **refuses** a non-loopback bind without the token. The
  CSRF origin guard compares against the request's own host instead of
  hardcoded localhost (which would have rejected every form POST the
  moment the Studio served on a corp hostname). A deployment
  `Dockerfile` ships (non-root, git-only, fail-closed default command;
  not yet CI-built) plus the RUNBOOK's run-it-as-a-service section
  (docker + systemd, volume/backup note, OIDC-reverse-proxy as the SSO
  path — the token stays a shared secret by design).
- **The enterprise loop closes in the Studio: incidents from anywhere,
  evidence in one click, Gate 5 on the dashboard, housekeeping on
  demand.** The incident front door now also lives on /live (an adopted
  brownfield repo has no product page — and the substrate ladder's
  refusal renders in place when maintenance is below floor). The review
  timeline gained *Export the Gate-R evidence bundle* — same artifact as
  `avs evidence-bundle`, one click from the review it attests; a human
  still attaches it, the Studio never submits anything. The enterprise
  panel gained a Deploy reviews (Gate 5) card reading the same mirrors
  `avs deploy-review` writes — recommendations, never executions. And
  the housekeeping card gained *Run a housekeeping check*: the identical
  sweep pass as `avs sweep`, report-only at SW0, clean passes recorded.
- **Enterprise mode is a governance dashboard, not four dead ends.**
  Grounded in how mature enterprise consoles actually work (SonarQube's
  verdict-first gates, GitHub security overview's explicit "not enabled"
  state, Renovate's what-we-found onboarding) and tested by adopting a
  real 39k-line enterprise data-pipeline repo: a **posture line** now
  answers first — measured / not-yet-configured / needs-attention, and
  unmeasured never renders green; a **Model door & egress card** answers
  the security reviewer's first questions on screen (which provider mode,
  authenticated how — presence only, never a value — which forge,
  telemetry-sends-nothing, workspace spend from the ledger); a
  **Codebase card** renders the `avs map` comprehension report so a
  brownfield adoption's first screen proves the tool read the repo;
  empty states carry the exact command, what it changes, and the
  feedback loop ("this page re-reads the workspace on every reload");
  the governance card names the gate owner, not just the rule; and
  `init --adopt` now points at readiness/review/studio instead of
  telling a brownfield team to write a spec for a product that already
  exists. The Studio also stopped 404ing its own favicon.
- **Windows can't be killed by a health check anymore.** `os.kill(pid, 0)`
  liveness probes — which on Windows *terminate* the probed process —
  went through a cross-platform `procs.pid_alive`, worker detachment
  gained a Windows path, and the probe venv resolves `Scripts/` as well
  as `bin/`.

## v0.60.0 — a run that says what it is doing, and a failure that says why

Diagnosed from the durable bench scoreboard (`benchmarks/results/`), where
`build_failed` and `error` rows dominate runs 4–11. Every one of them named a
symptom the system already knew the cause of and had discarded.

- **A cut-off response is no longer indistinguishable from a complete one.**
  `stop_reason` was recorded only when the response text came back *empty* —
  the one case where nothing is at stake. A response truncated at the output
  cap returns partial YAML that usually still *parses* (a block scalar simply
  ends), so a half-written source file reached disk and its real failure
  surfaced an iteration or two later as an unrelated test error. All four
  adapters now record why the model stopped (`stop_reason` / `finish_reason` /
  `finishReason`) into a thread-local — thread-local because voters and
  parallel lane builds run concurrently.
- **The implementer refuses a truncated batch instead of writing it.** The
  build loop feeds the truncation back as named feedback ("return FEWER
  files"), and a task truncated on every attempt fails with the cap, the
  bytes received, and the remedy — split the task — rather than a generic
  test error. The implementer's cap also rose 16384 → 32000: the prompt
  permits 12 files of 500 lines and the old cap could not carry a third of
  that, so a task at the top of its own stated envelope was cut off by
  construction. 32000 is the Opus-class ceiling; more would 400 at request
  time. The planner gained the same guard (a truncated plan loses whole tasks
  silently, and `dag_check` cannot see it — a truncated plan is internally
  consistent) plus 4096 → 8192.
- **`build gate still failing after max iterations` now says what failed.**
  `BuildResult.test_summary` always held the reason; `TaskOutcome` dropped it,
  so outcomes.yaml, the bench scoreboard and the founder's report all showed
  the generic half alone. The cause now travels with the sentence, the outcome
  carries `iterations` / `files_written` / `test_summary`, and the bench row
  stops truncating a failure's detail mid-word.
- **`implementer returned no files` distinguishes its three causes** — an
  empty `files:` list, a batch discarded wholesale as weakened skeletons, or a
  model that narrated instead of answering — by keeping the response opening,
  the way the voter seat already does.
- **A task in flight is observable.** New `upstream/progress.py`: an
  append-only step journal (`.mas/progress.jsonl`) written as each step
  actually starts. A task used to sit at `pending` through spec, five charter
  critics, up to three build iterations and six review voters — most of a
  run's wall-clock, indistinguishable from frozen. `avs create` now narrates
  live (it printed nothing at all between start and a report an hour away),
  the Studio's per-task line shows the current step, and a failed module
  prints its cause in the summary. Observed, never predicted: no percentages
  and no ETAs, because the system genuinely does not know whether iteration 2
  of 3 will be the last. The journal is a record and never an input — an
  unwritable one cannot fail a build.

Shipping alongside it, the other half of the same question — the run also says
what it *cost*:

- The founder's `BUILD-REPORT.md` gains a "What this cost" section, appended
  as arithmetic rather than prompted, so the number is never model prose. It
  names the typical and worst run once there is more than one to compare, and
  states plainly that the spend is on the operator's own API key.
- The Studio's product page carries the same line, read from the same ledger
  `avs cost` reads — the Studio stays a veneer, never a second source.
- `avs create` prints it when the run ends.

The cap stays off by default. Transparency is the always-on behaviour; the
limit only exists if somebody wrote a number, and says so when it fires.

## v0.59.1 — cost transparency is the point; the cap is opt-in and secondary

Correcting v0.59.0's emphasis. The founder signal asked to SEE the number —
"how much will a typical month of builds cost me? I'm scared to leave
autopilot running" — not to be stopped, and users spend their own keys.

- `summarize` / `by_model` / `summarize_workspace`: calls, tokens and money
  for a run, a month, or a model.
- `render_plain` is the founder-facing sentence, in the register that asked:
  no token counts, no model names. "at least $X" when unpriced calls make the
  figure a floor, and "unknown, here is how to fix it" when no prices are
  configured — printing "$0.00" would be a lie about someone's money.
- `read_entries(since=…)` answers "what did THIS run cost" without threading
  attribution through every call site.
- `typical_and_projected` reports the MEDIAN run beside the worst run,
  because agentic spend has a fat tail and one runaway loop makes an average
  useless. Runs are inferred from ledger gaps, stated as the heuristic it is.
- The cap message now reads as the operator's own standing decision: it is
  off by default and only exists because somebody wrote a number.

## v0.59.0 — the cost gate: spend is measured, and the cap is real

observability.py could price a call, total a month, and compare against a cap
— and none of it was reachable, because nothing ever *recorded* a call.

- Recording at the provider adapters, where usage exists; `Provider.chat`
  still returns `str`. Recording never raises: a metering failure must not
  take down the work being metered.
- Persisting to append-only `.mas/spend.jsonl`, flushed on every review exit
  path including the Gate 1 bail, and between build tasks. Lock-guarded,
  because voters run in a thread pool. A truncated row is skipped, not fatal.
- Gating before the spend — Gate 1 refuses a review, `run_build` refuses a
  task — with the previous task's spend flushed first, so a cap cannot be
  discovered only at the end of a seven-task run. `avs cost` reports a month.
- Unpriced calls are never counted as zero (the total reports as a floor);
  an unconfigured cap says it checked nothing rather than passing silently.

## v0.58.0 — the MVP contract, and the AI delta on top of it

The system could make a build smaller but never asked whether the slice, on
its own, tells you if the thing is worth building. Doc 13 specified exactly
that rule and it had never been implemented.

- `avs mvp` + `product/mvp.py`: doc 13's rule (a hypothesis the increments
  can actually settle, matched on 4-char stems plus CJK bigrams so
  "progressing" agrees with "progress" and a Chinese FDR is not silently
  exempt), plus the canon requirements the schemas let default to empty and
  a refusal of "build it and see" as a cheapest test. `thin` IS the MVP tier.
- The AI delta, fired by AI-shaped language in the founder's own words:
  a named simpler alternative, a declared cost of being wrong (irreversible
  may not act; expensive may suggest but not take), a wrong-answer fallback,
  >= 20 eval cases authored first, and a quality metric paired to every
  volume metric. These hold at every tier.
- Gate PL2 carries scope_tier into the build (nothing bridged handoff and
  planner before); `test_first` blocks `avs prd` instead of being a string
  nothing read; the thin task cap became deterministic.

## v0.57.1 — a founder never meets "Internal Server Error"

Found by watching a real founder hit it. `/fdr`, `/feature` and `/correct`
run LLM calls for minutes and had neither an in-flight guard nor error
handling, while `/build` and `/retry` had both.

- A failure renders a page: what stopped, that nothing was lost, the likely
  cause in plain language, the real exception one click away.
- A second submit while thinking returns "working on it" rather than racing
  two autopilots over one workspace.

## v0.57.0 — the six gaps from the research/comprehension audit

- `avs init` no longer overwrites an existing CLAUDE.md.
- The Context Manifest reaches the writer instead of only auditing the
  prompt — `render_manifest` was dead code, and its absence is why sibling
  tasks drifted onto different routes.
- `avs map` / `avs init --adopt`: the brownfield entry path. Languages,
  entry points, modules, real import edges, HTTP surface and test locations
  derived from the code; `--write-deps` emits the baseline
  `arch_contract_check` never had.
- Charter voters get the tools they declare (~40 seats ran tool-less);
  the phantom `repo_capability_probe` is gone.
- `--tier thin` reaches the planner.
- `avs probe`: the quarantined fetch the docstrings had promised, plus
  ADR-U03 taint isolation finally wired to the host.

## v0.56.1 — the founder demo is a recorded real run

`docs/media/studio-flow.gif`: seven frames from one real build against a live
provider. That run failed some modules, so the report frame reads "partly
built" and the caption says so; a test pins the honesty phrases. Also fixes
`avs studio <dir>` — the positional form every doc showed was the one form
that could not work — keeping `--repo-dir` behind a deprecation notice.

## v0.56.0 — per-mode UIs: each persona gets its organizing surface

Researched before built: the design canon (doc 24's persona constraints,
the solo weekly-review agenda, invariants 14.21/14.22), the adaptive-UI
literature (Findlater & McGrenere CHI 2004: adaptable beats adaptive;
NN/g on visible modes and progressive disclosure), and an inventory of
every `.mas/` surface the CLI writes that no Studio route rendered.

- **The mode is adaptable per request, never adaptive.** A visible
  switcher on every page — including founder — sets `?mode=` and a
  cookie; `--mode`/edition only supply the default, and the system never
  flips the mode behind the user's back. An unknown `?mode=` is a loud
  400, same policy as an unknown `--mode`.
- **Engineer mode** gains the review machinery: a bounded newest-first
  recent-reviews table (the server's `/reviews` pattern — no unbounded
  scans per page load), a per-review timeline page at `/review/<id>`
  rendering the same `NN-<node>.yaml` mirror `avs replay` reads, and a
  voter-health board (runs · blocked · substituted) from
  `.mas/voters/*/log.yaml` — previously visible only inside the weekly
  compound proposal.
- **Enterprise mode** stops counting and starts verifying: the
  attestation card now recomputes the sha256 chain (`verify_ledger`) and
  renders tampering as BROKEN-at-entry-N; plus the S0–S4 stage-activation
  grid with the exact missing prerequisite per inactive stage, the F-18.3
  gate-dwell/rubber-stamp report with its own notes verbatim, and
  automation policy arming state (disarmed-by-default rendered as the
  good news it is; an armed policy shows who armed it and until when).
  The governance spokes render even without an edition file — a workspace
  still has a ladder, a dwell distribution, and an arming state.
- **Founder mode** stays the plain flow plus the switcher, and gains its
  correction history (`product/CORRECTION-LOG.md` was written on every
  correction and rendered nowhere).
- The wire-up gate grew with the UI: query-string references route by
  path, and the engineer/enterprise states joined the state walk — the
  bidirectional every-button-resolves / every-route-rendered contract
  now covers the mode cards too.
- Every new card is a pure read of files the CLI already writes; the
  Studio stays a veneer. LLM-calling and subprocess paths stay out of
  GET handlers.
- Suite: 1092 → 1112.

## v0.55.0 — Studio modes: different users, different depths of the same UI
- `avs studio --mode founder|engineer|enterprise`. The editions system
  (doc 24, ADR-U26/U27) already encodes who is at the keyboard; the Studio
  now reads it — no flag needed, the mode resolves from the workspace's
  `.mas/edition.yaml` (solo→founder, engineer→engineer,
  enterprise→enterprise), and `--mode` overrides per launch.
- Founder mode is the existing UI unchanged, and stays the default: with no
  edition and no flag, the page is byte-for-byte what it was.
- Engineer mode appends a build-internals card to every page: each task
  with its CLI-usable ID and verbatim recorded state (`spec_blocked`, not a
  euphemism), the workspace profile, and the command equivalent of every
  button (`retry-task`, `preview`, `walkthrough`, `verify`).
- Enterprise mode appends a governance card read from the resolved edition:
  substrate rung, WIP limit, gate-owner rule, never-batched gates, and the
  attestation-ledger entry count — with "no ledger yet" stated rather than
  omitted, because a missing ledger must not read as attested-and-clean.
- The mode contract is the editions' own rule read UI-side (invariant
  14.21): a mode may only ADD visibility. Tested structurally — every form
  action and link the founder page renders must appear in every other mode.
  An unknown `--mode` is a loud startup error, same policy as a missing
  i18n string; a corrupted edition file falls back to founder rather than
  taking the Studio down.
- `__init__.__version__` synced (it had been stranded at 0.12.0 since the
  packaging split; nothing reads it, but a wrong number is a wrong number).
- Suite: 1071 → 1092.

## v0.54.1 — packaging: every runtime resource now ships in the wheel
- **0.54.0 was broken for pip users and is yanked.** Publishing it and then
  installing it FROM PyPI is what found this: `avs init --profile web` — the
  first command any new user runs — failed with `unknown profile 'web';
  available: []`, and `avs replay --demo`, the README's no-API-key headline,
  crashed on a missing directory.
- The cause was systemic, not a one-off: ten data paths resolved through
  `parents[N] / "profiles"`-style repo-root arithmetic, which points at
  `site-packages/../../profiles` once installed. `paths.py` had documented
  exactly this trap when `skills/` was fixed, and the rest of the class was
  never swept.
- `profiles/`, `blocks/`, and the edition presets + offline demo bundle now
  live inside the package (`edition_data/`, renamed to avoid colliding with
  `editions.py`), with repo-root symlinks so humans and docs keep the familiar
  paths. Every consumer resolves through `paths.py`.
- Development-only data (benchmark corpora, test fixtures) is deliberately
  still NOT shipped — a wheel should not carry a benchmark corpus — so
  `repo_data()` returns None and callers can say "needs a checkout" instead of
  reporting an empty corpus as a result.
- Verified the way it should have been the first time: installed from PyPI
  into a clean venv and ran `init --profile web --edition solo` and
  `replay --demo` end to end.
- The lesson recorded, because the local build passed every check: a wheel
  test that only runs `--help` proves the entry point resolves, nothing more.
  Packaging bugs live in the resources, so a smoke test has to touch them.

## v0.54.0 — package and CLI renamed: `ai_venture_studio`, command `avs`
- Release plumbing: `version` in pyproject was 0.33.1 while the CHANGELOG had
  reached v0.54.0 — the two now agree, because publishing a number that
  disagrees with its own release notes is worse than not publishing.
- `.github/workflows/publish.yml`: tag-triggered PyPI release via Trusted
  Publishing (OIDC, no API token anywhere). It runs the full suite on the
  tagged commit, refuses to publish when the tag and pyproject version
  disagree, runs `twine check`, and only then uploads — because a published
  version cannot be replaced, only yanked.
- Artifacts for 0.54.0 are built and verified locally: `twine check` PASSED on
  both, and a clean-venv install of the wheel runs `avs`, the `autoproduct`
  alias, and a real command. The UPLOAD is not done: it needs a credential
  only the account owner holds (see RUNBOOK → Releasing to PyPI).
- Import package `autoproduct` → **`ai_venture_studio`** (874 references
  across 268 files), distribution `autoproduct` → **`ai-venture-studio`**,
  and the CLI command is now **`avs`**. `autoproduct` stays as a console-script
  ALIAS so anything scripted against it — cron entries, CI steps, shell
  history — keeps working; docs use `avs`.
- **Compatibility contracts were deliberately NOT renamed**, each with a
  comment in the code saying why:
  * `AUTOPRODUCT_*` env vars (19 of them) — renaming breaks every existing
    deployment's configuration.
  * `autoproduct_reviews_total` / `autoproduct_errors_total` Prometheus
    series — renaming a metric silently breaks every dashboard, alert and
    recording rule built on it. A real rename needs a dual-emit window.
  * the `autoproduct_version` telemetry field — a wire key consumers parse.
  * `## Learned constraints (autoproduct)` in CLAUDE.md — this string matches
    the header already written into existing workspaces; changing it would
    make the compounding loop append a duplicate section instead of updating
    its own.
- **Two real breakages the rename caused, both found and fixed:** the
  root `skills` symlink still pointed at `src/autoproduct/skills` (21 tests
  failed until it was repointed), and `version("autoproduct")` in the
  telemetry payload raised `PackageNotFoundError` under the new dist name —
  now tries the new name and falls back to the old.
- Honest install line: `ai-venture-studio` is not on PyPI yet, so the README
  still tells you `pip install autoproduct` and says the rename lands with
  the next release rather than instructing an install that would fail.
- Recorded evidence untouched again: the demo review audit trail cites
  `src/autoproduct/*` files because that is what it reviewed.
- Suite: 1071 tests, unchanged and green.

## v0.53.0 — renamed to ai-venture-studio; English is the UI default
- **Repository renamed** melodygaoyifan/ai-product-autopilot →
  **ai-venture-studio**. Live references updated (pyproject URLs, README,
  launch post, design canon links). GitHub redirects the old URLs, so
  existing clones and links keep working.
- **Recorded evidence was NOT rewritten.** The Gate PL5 and experiment-run
  records cite `gh api repos/…/ai-product-autopilot` with a `retrieved_at`:
  those are evidence snapshots, and an evidence snapshot is not edited after
  the fact (the same rule that makes the attention log append-only). Each now
  carries a note that the repo was renamed on 2026-07-27 and that
  re-measurements use the new name.
- **English is now the Studio default** (`DEFAULT_LANGUAGE = "en"`). The UI
  began Chinese-first because its first users were 小程序 founders; the repo
  is public and English-speaking, so the default now matches the audience
  that meets it first. `--lang zh` restores the original bilingual UI
  character for character — the strings were not touched, only the default —
  and a test asserts exactly that so the move is not a quiet degradation.
- The FDR template follows the same default, and the Chinese-founder tests
  now ask for `lang="zh"` explicitly rather than relying on a default, with
  new tests covering the English default path end to end.
- The package and CLI are still named `avs`: renaming those would
  break every existing install, and that is a separate decision.
- Suite: 1068 -> 1071 hermetic tests

## v0.52.0 — the Studio speaks English, and the README demo shows it
- `avs studio --lang en` renders the entire flow in English. Every
  user-facing string moved to `studio_i18n.py` keyed by language, and the
  FDR template gained an English twin asking the same six questions.
- **What made this necessary:** the README's founder demo claimed an
  English-or-Chinese product while the screenshot showed
  `写下你的产品需求 / Describe your product` — the UI was bilingual
  Chinese-first with no way to opt out. A demo that claims English and shows
  Chinese is a false claim about the product, not a cosmetic gap.
- `zh` (the default) keeps the original bilingual strings character for
  character, and a test asserts an unset language renders byte-identically
  to before. Existing users see no change; the flag is additive.
- An unknown language falls back to the default rather than rendering blank
  labels: a working UI in the wrong language beats a broken one in none.
  Codes normalise, so `EN`, `en-US`, `en_GB` all work.
- The README founder section is now English-first — web profile, an English
  FDR shown inline as the actual input, and a REAL screenshot of the real UI
  captured through Playwright at `docs/media/studio-en.png` (not a mockup;
  the Chinese screenshot stays linked for 小程序 founders). A test pins the
  README, the flag, and the image together so the demo cannot drift from the
  product again.
- Also honest now: the profile list in the README names all five profiles
  rather than three, and glosses the WeChat terms in English.
- Suite: 1060 -> 1068 hermetic tests

## v0.51.0 — a second kill-criterion axis, chosen by the human, evaluated by the machine
- The launch PRD now carries TWO axes. The new one (O-L2, capability
  regression) fires if the product-bench build rate falls below 60% OR the
  probe pass rate below 50% for 2 consecutive weekly runs.
- **Why this axis and not another:** its series already exists.
  `benchmarks/results/*.yaml` records build/probe/clean rates per run and the
  cadence is weekly, so this criterion can fire on the NEXT run — while the
  attention axis cannot fire until four consecutive weeks are logged. A
  second axis that also cannot fire would have added no coverage.
- **The floors are read, not chosen.** Runs 4-5 sat at 8-33% build, runs 6-9
  climbed 42-72%, runs 10-11 hold 74-75%. 60/50 sits below the current level
  and above the pre-fix era: crossing it means regressing into territory the
  system already climbed out of once. Two runs rather than one because at
  n=4 cases a single dip is noise, and a criterion that cries wolf gets
  ignored.
- `avs bench-criterion` evaluates it, and `avs loop` now
  reports both axes with their real readings in one line. Either firing
  demands a recorded human decision at Gate PL5 (invariant 14.20); neither
  evaluator decides.
- metrics/product_bench_capability.md defines the series, its exclusions
  (harness-noise runs, corpus changes that reset comparability), and its
  falsifier.
- **The PRD linter caught me:** the outcome instrumented
  `product_bench.run_recorded` while the metric counts
  `product_bench.case_built`, so P4 would have read zero. That is precisely
  the class of bug prd_lint exists for, and it fired on its own author.
- Suite: 1048 -> 1060 hermetic tests

## v0.50.0 — `loop` and `attention` now answer one question together
- `avs loop` reads the attention streak, so the v3.0.0 gate report
  states the real distance to firing ("2/4 consecutive logged weeks over
  4.0h; 2 more would fire it") and the real next action ("log last week:
  `avs attention --week 2026-W31 …`") instead of a static "the
  criteria need data that does not exist yet". Two commands shipped in
  separate releases were leaving the operator to join them by hand.
- A `not_tracked` row is reported as itself: it is a RECORDED decision, not
  a gap, so `loop` says the run "starts from the next week you log" rather
  than claiming last week is logged (which the first cut did) or asking for
  a rewrite of the record.
- When the criterion has fired, the next action becomes the decision —
  and the gate still does not close on it. Only a recorded human
  kill-or-pivot does (invariant 14.20), which the existing tests keep true.
- Absent log: the static wording, unchanged. Unreadable log: reported as
  unreadable rather than as a streak of zero.
- Suite: 1043 -> 1048 hermetic tests

## v0.49.0 — the use-case matrix, and the gap it found
- New `tests/test_use_case_matrix.py` tests the canon's coverage CLAIMS as a
  matrix instead of trusting that each part works because its own unit test
  passes: five domain profiles spec-and-build end to end, three editions
  resolve and lint narrowing-only, and the five-rung substrate ladder
  activates exactly the stages its floors allow.
- **The gap it found:** `STAGE_FLOORS` declared floors for eight stages, but
  only `code_review` and `deploy_review` ever consulted them. So doc 18's
  "stages below their infrastructure floor are inactive-never-degraded"
  (ADR-U15) was unenforced for six stages — an S0 team with no git could run
  `build`, and `triage` ran with no observability configured. discover,
  plan, spec, build and triage now enforce their floors with the same
  exit-code-4 refusal, and both directions are pinned: refused BELOW the
  floor, and never refused AT it (a guard that blocks legitimate work is
  worse than no guard).
- Recorded rather than smoothed over: `deploy_review` is the designed
  exception — above S0 it DEGRADES to config-lint-only instead of going
  inactive, because a config lint still helps without progressive delivery.
  A test pins that too, so the asymmetry stays deliberate.
- An absent `.mas/substrate-profile.yaml` still gates nothing, so no
  existing workspace starts refusing work because this exists.
- Suite: 1012 -> 1043 hermetic tests (+3 skips: stages with an S0 floor have
  no rung below them to be refused at)

## v0.48.0 — upstream resume, grounding at the spec writer, and the plan closed out
- **Upstream resume (gap-plan item 15's second half).** A task is the
  expensive unit upstream — spec + build + review, minutes and real money
  each — so autopilot now persists each outcome AS it completes and a re-run
  skips tasks already built on disk. Honestly labelled: this is
  task-granular, not super-step-granular like the review graph. A task
  interrupted halfway restarts that task; the ones before it stay done.
- `outcomes.yaml` is treated as a record, not an authority: an outcome
  claiming `built` is honored only when `built_task_ids` agrees the spec is
  actually built. A stale record can never make a run skip work that is not
  there.
- **Grounding now gates the SPEC writer too**, not just the build writer —
  the v0.42 asymmetry was arbitrary. Same finding as last time, one stage
  earlier: the spec writer never saw CLAUDE.md or the module invariants, so
  it could author a criterion contradicting an invariant, producing a build
  that cannot satisfy both and a SPEC_DRIFT flag against work nobody could
  have got right. Both now reach the prompt verbatim, and a spec authored
  blind to them raises rather than returning a weak spec.
- docs/gap-closure-plan.md reflects reality: every phase closed, item 15
  marked with both halves and their differing guarantees, and the
  "recorded non-goals" list corrected where later ADRs reversed it.
- Suite: 1008 -> 1012 hermetic tests (one end-to-end resume example, not a
  battery)

## v0.47.0 — the bot fleet: the game profile's last unbuilt check
- `avs botfleet` runs N parallel bot sessions and triages what they
  hit: crashes, softlocks, unreachable-state regressions, out-of-bounds
  positions, and errors. Findings dedupe by signature across sessions and
  each carries a reproduction command — a bug a fleet found that cannot be
  replayed by hand is not actionable.
- **The design decision that made this shippable without an engine:** the
  fleet is defined by a session PROTOCOL (newline-delimited JSON events), not
  by a game. So the detectors are real functions over a real stream, verified
  against real subprocess sessions of a real deterministic simulation
  (`benchmarks/botfleet/toy_sim.py`, which is also the reference emitter an
  engine adapter copies). Wiring Unity or Unreal is now an adapter, not a
  redesign — and that adapter is the honest remaining open item.
- **Bug the first real run found:** one escaping bot produced 44 findings,
  because the out-of-bounds signature included the per-tick state hash. A
  continuing condition is now one finding per session, and the signature
  names which axis and side left the play area rather than how far along it
  the bot got. This is exactly what a stubbed stream would not have shown.
- Honest by construction elsewhere too: a hung session is a crash rather
  than a hang, a non-zero exit with no crash event is still a crash, an
  unconfigured or unrunnable command is a VISIBLE skip ("never counted as a
  clean overnight run"), and an undeclared netem profile is an error naming
  the declared ones.
- Scope, per §45.1: the fleet finds crashes and stuck states. A clean report
  says so explicitly — whether the game is FUN is the human playtest gate's
  question, and no bot replaces it.
- Suite: 982 -> 1008 hermetic tests

## v0.46.0 — the attention collector: making the v3.0.0 criterion able to fire
- `avs attention` measures the OBSERVABLE FLOOR of weekly
  maintenance attention from ledgers `.mas/` already writes — gate dwell
  (escalate→final, the same measurement the rubber-stamp detector uses),
  recorded product-gate decisions, sweep reviews — and prints it with the
  artifacts it came from.
- **The machine never sets `hours`.** A floor is not a total: reading a
  review without touching a gate, thinking, and answering questions all
  count toward attention and leave no timestamp. So `hours` and
  `status: logged` stay human, `--by` is required (a number in this series
  has an author), and the floor is recorded BESIDE the human's number rather
  than instead of it.
- Append-only, enforced: an existing week is never rewritten, the log's
  header comment survives appends, and a malformed log errors rather than
  starting a fresh one.
- The streak reader implements the log's own rule — an untracked week breaks
  a streak without counting either way, and exactly-at-budget is not over
  budget. When four consecutive logged weeks exceed the budget the command
  exits 3 and says Gate PL5 now needs a recorded human decision. It does not
  make one.
- Why this was engineering worth doing: the v3.0.0 blocker was never "wait
  four weeks", it was that logging was a manual habit whose lapse silently
  reset the clock the criterion depends on. The habit is now cheap; the
  decision stays yours.
- Suite: 961 -> 982 hermetic tests

## v0.45.0 — the deploy-side CLI wrappers complete the §17.2 table
- terraform_validate, helm_lint, kubectl_dry_run, argocd_app_diff,
  flagger_inspect, railway_inspect join the L1 `deploy` partition. This is
  the table's other integration shape: BINARIES gated on being installed,
  following the pattern tools/external.py set for the scanners — an absent
  binary is a visible skip with the install hint, never counted as clean.
- `kubectl_dry_run` defaults to `--dry-run=client`, which never contacts a
  cluster. Server-side dry-run is real admission validation and more
  useful, but it talks to whatever cluster the current kubeconfig points
  at, so it is opt-in per call. A deploy review that silently reached into
  production because a context happened to be current is exactly the
  surprise this design spends its budget avoiding.
- Read-only is structural, not documentation: no wrapper names sync,
  rollback, up, redeploy, patch, delete, or destroy, and `apply` appears
  only behind `--dry-run` — asserted against the module's own source.
- Semantics that matter: argocd exits 1 when a diff EXISTS, so that is
  findings rather than an error, while auth failures and missing apps are
  errors; flagger flags unhealthy canaries but never patches one, because
  promoting or aborting a canary is a human's call.
- **Bug the tests found:** terraform with no parseable verdict (typically an
  uninitialized directory) reported "findings: 0 diagnostic(s)", which reads
  like a pass. A non-answer is now an error naming the likely cause.
- migration_dryrun from the §17.2 table was already covered by
  lanes.delivery.migration_rehearsal, so it was not duplicated.
- Honest scope: hermetic via a stubbed subprocess boundary. None has run
  against live infrastructure from this repo (no cluster, no cloud
  credentials); first real invocation per tool stays an open item.
- Suite: 932 -> 961 hermetic tests

## v0.44.0 — all six §17.2 signal readers, over one shared core
- datadog_query_metrics, pagerduty_get_incident, prometheus_query,
  loki_query, jaeger_query_trace join sentry_get_issue in the L1
  `maintenance` partition. Sentry's shape became a shared read-only core
  (gating, `secret://` resolution, GET-with-no-body, summarize, wrap,
  multi-secret scrub, errors-as-data), so each reader is its endpoint and
  its summary and nothing else.
- **Two gating families, deliberately distinguished.** A hosted service is
  gated on its credential; a self-hosted one on its base URL, because there
  is no sensible default address for a Prometheus and defaulting to
  localhost would turn "not configured" into a confusing connection error.
  Either way, unconfigured means a visible skip naming the exact variable.
- Details that are the point rather than decoration: Datadog requires BOTH
  keys and an explicit window (a metric read whose window nobody stated is
  not evidence); PagerDuty is read-only so it cannot ack, resolve, or
  reassign — the on-call human owns those; Loki's limit is bounded and its
  log lines get the same wrapper as everything else, which matters most
  there because log lines are the most user-influenced text in the stack;
  both Datadog keys are scrubbed from one payload.
- Honest scope, unchanged: written against each vendor's documented REST API
  and exercised against a stub transport. None has run against a live
  account from this repo — no credentials exist here — and the map says so
  per tool.
- Suite: 908 -> 932 hermetic tests

## v0.43.0 — the first external-service tool: sentry_get_issue
- maintenance/signals.py: reads one Sentry issue over the documented REST
  API, served by the L1 `maintenance` MCP partition. Adding it needed a row
  in the partition table plus a reader module — no transport, host, or RBAC
  change, which is what v0.40 claimed and this checks.
- House rules, all enforced by test: the credential is `AUTOPRODUCT_SENTRY_TOKEN`
  (raw or a `secret://ENV` ref through the v0.31 layer) and a configured-but-
  unresolvable ref errors rather than going unauthenticated; no token is a
  VISIBLE skip naming the env var, never an empty result, because "never
  asked" must not read like "nothing found"; the reader is read-only (the
  request builder sends no body and names no write verb, asserted on its
  source); the payload arrives `wrap_research`-wrapped, so a hostile issue
  title is data and consuming it taints the run out of L1+ (ADR-U03).
- Wired end to end: the Sentry webhook now passes the issue id through as
  `external_id`, and the maintenance graph gained a `signal` step that
  enriches a Sentry-sourced incident before root-cause analysis and records
  the wrapped payload in the mirror. A manual incident never calls out.
- **Bug the suite found:** substring-scrubbing the token shredded any payload
  containing its characters — with a 1-character token, everything. Scrubbing
  now has a length floor, because mangling a payload is worse than not
  scrubbing a string too short to be a credential.
- Honest scope: exercised hermetically against a stub transport. It has NOT
  been run against a live Sentry org here — no credential exists in this
  repo to do that with, and the module docstring says so instead of implying
  coverage it lacks.
- Suite: 891 -> 908 hermetic tests

## v0.42.0 — grounding enforced on every build, and the gap it found
- The Context Manifest is now wired into the build writer, not just
  available: every build assembles a manifest, records it at
  `.mas/manifests/<slug>.yaml`, and BLOCKS when a required entry's content
  never reached the implementer's prompt. Overflow blocks too, reported as
  a Planning split proposal rather than trimmed.
- Receipts for pushed context: `grounding_receipts` probes the prompt for
  each entry's most distinctive line instead of trusting a model's
  self-report. This checks assembly, not attention — it cannot prove the
  model read what it was handed, and the docstring says so.
- **The gap it found on the first run:** module-spec invariants
  (`.mas/specs/*.spec.yaml`) were never in the implementer's prompt, even
  though Code Review enforces them and flags SPEC_DRIFT_UNDOCUMENTED. The
  implementer was being held to a contract it had not been shown.
  Invariants and forbidden side effects now ship in the prompt, quoted
  VERBATIM — the probe requires the contract text itself, and paraphrasing
  a contract into a prompt is the smell the check exists to catch.
- Modeling correction: `spec.md` renders `spec.yaml` for a reader, so it is
  optional rather than a second obligation; requiring both fired a
  violation over a heading the machine contract never had.
- Suite: 886 -> 891 hermetic tests

## v0.41.0 — the ContextAssembler and research-session taint isolation
- upstream/context_assembler.py (§13.25.2, §13.29.3, §13.35.5): builds a
  task's Context Manifest deterministically under a token cap — spec slice
  first, code neighborhoods last, every entry content-hashed. Three
  mechanisms that only work together:
  * grounding receipts — `verify_sources_read` checks a writer's
    `sources_read` against the manifest; unread required context, a hash
    mismatch, or a claimed read of something unlisted are CONTRACT
    violations (§11.18.3), not quality notes;
  * drift detection — re-hashing catches a human editing a frozen artifact
    mid-flight, and `run_build` now refuses to build an unratified fork,
    naming the retro-SCR path instead of fighting the human (Gate U3
    pins a contract hash at approval; specs approved before v0.41 have no
    receipt and are treated as clean);
  * overflow as a planning defect — a task whose REQUIRED context exceeds
    the cap returns TASK_BLOCKED_CONTEXT_OVERFLOW with a split proposal
    rather than quietly compressing the contract.
- harness/taint_guard.py (§13.31.2, ADR-U03): the session-level enforcement
  the taint classes always assumed. `wrap_research` marks fetched content as
  data (and neutralizes a nested closing tag, so hostile content cannot
  close the wrapper and speak as the host); a run that consumes research is
  tainted one-way and loses L1+ tools for the rest of its life. Enforcement
  sits at the MCP transport where v0.40's risk tiers live, so the denial
  does not depend on anything the model says: L0 reading still works, L1/L2
  and unclassified tools are refused, and the refusal lands in the audit
  ledger. Taint arrives from tool OUTPUT, not declaration.
- Suite: 866 -> 886 hermetic tests

## v0.40.0 — the L1/L2 MCP partitions + the deploy-branch fix
- Three more partitions from doc 11 §17.2, for the tools that exist:
  `deploy` (L1: migration/workflow/canary probes), `maintenance` (L1:
  recent_commits, correlate), `test_exec` (L2: run_tests, which executes
  repo code — §17.2's reason for isolating it hardest). Five real servers
  now; the table's external-service tools (terraform, sentry, datadog)
  stay unbuilt and named as open rather than stubbed.
- Risk-tier RBAC at the transport: each partition declares L0/L1/L2 and
  MCPHost refuses to mount one above the caller's `risk_ceiling`, so a
  read-only voter cannot reach L1/L2 even if a future skill names one of
  their tools — enforced where the connection is made, not in a prompt.
  `MCPToolBox` also intersects with the L0 registry, so a voter allowlist
  cannot grow into stage tools by accident.
- Audit coverage now includes the tools that touch the most: deploy probes
  and test execution were previously unaudited.
- Fix (v0.39 follow-up): the deploy review now records the branch it
  covers (PR head branch, or the checked-out branch for a local range;
  empty on detached HEAD). `deploy-execute` and `automerge` treat an
  unresolvable branch as a REFUSAL — the old `or "main"` fallback would
  have let an armed policy act on work it was never armed for.
- Suite: 856 -> 866 hermetic tests

## v0.39.0 — policy-armed merge and deploy execution (ADR-031)
- `avs automerge <review-id>` and `deploy-execute <id>`: the
  capabilities the README listed as out-of-scope now exist, DISARMED. A
  human arms them per repository in `.mas/automerge-policy.yaml` /
  `.mas/deploy-exec-policy.yaml`; the system's job is to refuse unless
  every declared condition mechanically holds.
- The bounding, which is the actual work: absence is never permission
  (`enabled: false` is the default inside a present file too); branch
  globs are refused so a policy cannot arm what it does not name;
  `armed_by` and `expires_at` are required and an expired policy is a hard
  error; a minimum track record of correct recommendations must exist
  before the first automated action; only APPROVE/APPROVE_WITH_NOTES may
  precede a merge and an escalated review's decision stands; migrations,
  IaC, Dockerfiles, CI workflows, k8s/Helm, CLAUDE.md, `.mas/`, and the
  policy files themselves always demand a human — so automation can never
  widen its own permissions; `deploy-execute` runs only the exact argv a
  human wrote, never one the system composes; no `--admin` escape, so
  branch protection wins.
- `.mas/automation-log.jsonl` records actions AND refusals with reasons:
  "why didn't it merge" deserves the same answer quality as "why did it".
- CLAUDE.md's invariant revised to match the code, and ADR-031 records the
  reversal with its mechanism. Auto-hotfix stays out entirely.
- Suite: 817 -> 856 hermetic tests (36 of the 39 new ones assert refusals)

## v0.38.0 — multi-tenant server mode (ADR-030) + the ADR directory
- One `serve` process may now front several isolated workspaces:
  `.mas/tenants.yaml` maps a tenant id to a SHA-256 token hash and a
  workspace root; `avs tenant add|list` manages it and prints the
  plaintext token exactly once.
- Isolation is the mechanism, not the aspiration: workspaces must be
  disjoint (a root contained in another fails at LOAD time), the token
  picks the workspace and no client-supplied path or id ever does,
  per-tenant GitHub secrets are `secret://ENV` references so one tenant's
  secret cannot verify another's deliveries, read routes (/jobs, /reviews)
  require the token in multi-tenant mode, and unknown/disabled/missing
  tokens answer identically so responses never enumerate tenants.
- Security fix found on the way: `review_id` was interpolated into a
  filesystem path unvalidated. Now `[A-Za-z0-9_-]{1,64}` — in multi-tenant
  mode that was a traversal into a neighbour's workspace.
- Single-tenant mode is byte-for-byte unchanged: no registry, no
  multi-tenancy, shared-secret path and open localhost reads as before.
- docs/adr/: the implementation's own decision records, starting with the
  three that REVERSE a recorded non-goal (029 MCP transport, 030
  multi-tenant, 031 policy-armed automation). A scope reversal that lives
  only in a commit message is indistinguishable from scope creep. Closes
  the map's "ADR docs" open item.
- Still out: SaaS — billing, plans, quotas, a shared database, a hosted
  control plane, per-tenant key management. Tenants bring their own keys.
- Suite: 795 -> 817 hermetic tests

## v0.37.0 — MCP as the internal tool transport (doc 11 §17), first real slice
- autoproduct/mcp/: JSON-RPC 2.0 over stdio (newline-delimited), two real
  partitions from doc 11 §17.2 — read_only (read_file/grep/list_files) and
  code_intel (symbol_refs) — each served by its own subprocess via
  `python -m ai_venture_studio.mcp.server <name>`.
- The triple check made real (§17.3): the skill allowlist decides which
  tools exist, MCPHost mounts only the servers those tools live in (so an
  unlisted tool is unreachable, not merely refused), and the server itself
  refuses anything outside its partition. Any one layer's bug fails closed.
- Subprocess isolation is the property the in-process mapping could not
  give: a path-traversal attempt is now refused inside the child process.
- mcp-audit ledger (.mas/mcp-audit.jsonl): every call, permitted or
  refused, with voter, server, tool, digested args, outcome and duration.
  Arguments are digested rather than copied — the ledger records what was
  asked for without duplicating searched content.
- Transport switch: AUTOPRODUCT_TOOL_TRANSPORT=mcp opts in; in-process
  stays the default because a subprocess spawn per server per invocation
  should be paid deliberately. Both toolboxes present one surface, and the
  caller's budget stays authoritative.
- Still out, by design and named in the map: external MCP servers (doc 11
  §17.1's supply-chain reasoning), and the L1/L2 deploy/maintenance/
  test-exec partitions — two real servers beat eight stubs.
- Suite: 778 -> 795 hermetic tests

## v0.36.0 — the live-loop instrument for the v3.0.0 design gate
- avs loop: reads a cycle's artifacts (stages P0-P5, gates
  PL1/PL2/PL3/PL5) and reports the three v3.0.0 criteria with reasons.
  States, never decides: a cycle where nothing fired is NOT the gate, and
  a recorded 'continue' is not either — the gate is about the loop's
  ability to stop, so only a human kill-or-pivot at PL5 closes it
  (invariant 14.20, ADR-U19). Exit 3 when a fired criterion is waiting on
  a human.
- launch/cycle.yaml: loop-entry declaration. This repo's own cycle entered
  at P2 (the product predated the loop), recorded with its reason instead
  of left as a silent gap; P0/P1 are in scope for cycle 2.
- docs/v3-live-loop.md: what closes the gate, why the system cannot close
  it for itself, and the exact field a human records.
- Current honest state: V3-1 and V3-2 met, V3-3 not — the launch PRD's
  only kill criterion needs four consecutive logged attention weeks and
  the log holds one untracked week.
- Suite: 766 -> 778 hermetic tests

## v0.35.0 — the last small open items: review-voter gates, policy thresholds, the ready-queue fix
- Review voters now register like every other roster (§11.19):
  `avs review-gate` runs each of the six core charters against 8
  fixtures (4 positive / 2 negative / 2 boundary, unified diffs) through
  the REAL Voter seat, ≥87.5% to register, recorded under `review/<voter>`
  in `.mas/voter-registry.yaml`. The vote node fails closed on a FAILED
  voter, reports unregistered ones, and refuses to review at all if the
  whole roster failed. Review voters no longer ride `bench` alone.
- Policy thresholds move into `.mas/project.yaml` (`policy:` block, doc 09
  open item): max_reviewable_lines, report_threshold,
  high_severity_threshold, rootcause_confidence_min. Unknown keys are a
  loud error (a typo silently keeping the default is worse), ranges are
  bounded so a project cannot set a meaningless bar, effective values are
  recorded in the run mirror, and any threshold looser than the shipped
  default is stamped into the leader summary — a lowered bar never hides
  inside a clean-looking verdict.
- Fixed: `next_tasks` matched a `task_id` field Spec never had, so the
  ready queue never advanced past the first task. It now reads the
  `(task:<id>)` marker in the spec request — one shared definition of
  "built", reused by the Studio's per-task progress.
- Suite: 737 → 766 hermetic tests

## v0.34.0 — Studio live progress, interrupted-build recovery UX, the wire-up gate
- Building page shows per-task state (from the same workspace files the
  CLI writes) updating in place via /status polling — signals s1/s3, "it
  looks frozen while it works"; one reload when the worker exits
- Interrupted builds (dead worker, no report) get their own page: kept
  modules shown ✅, per-module 继续 retry buttons through the existing
  retry-task path (a blanket rebuild would trip the SCR freeze on built
  specs — deliberately not offered), reset clears the stale pid marker
- Wire-up gate (tests/test_studio_wireup.py): every form action, fetch,
  link, and image src rendered by any Studio state must resolve to a
  registered route with the right method — and every route must be
  rendered by some state; the /status JSON contract is pinned to what the
  building-page script reads
- README: bring-your-own-keys contract spelled out (no shipped keys, no
  proxy, no metered backend), AUTOPRODUCT_CHECKPOINT_KEY documented,
  roadmap rows v0.31-v0.34, stale per-review-only recovery limit fixed

## v0.33.0 — gap plan D15 + D16 remainder: checkpointed deploy/maintenance, encrypted checkpoints
- Deploy review and maintenance rebuilt as LangGraph graphs on the shared
  `.mas/checkpoints.db` saver (thread ids `deploy:<id>` / `incident:<id>`):
  a crash mid-vote or mid-root-cause resumes from the last completed
  super-step via `avs recover` (now covering all three graphs)
  instead of re-paying the pipeline; mirror step names, verdict taxonomies,
  lint-only degraded mode, and the recommend-only ceiling unchanged
- Encrypted checkpointer serde (doc 09 §3.1): `AUTOPRODUCT_CHECKPOINT_KEY`
  (raw or `secret://ENV`) encrypts checkpoint rows at rest via LangGraph's
  EncryptedSerializer (AES, pycryptodome availability-gated); a key that
  cannot be honored errors loudly — never a silent plaintext fallback;
  encryption state stamped into every run's meta.yaml; the YAML mirror
  stays deliberately plaintext (§09.6 audit asymmetry)
- Suite: 688 → 694 on the D15 branch (727 after merging v0.32; ledger
  PC-1 synced at the merge)

## v0.33.1 — operator pass: live records + installable package
- Live operator records: first Gate PL5 evaluation (nothing fired — 0 of
  4 attention weeks exist; decision explicitly NOT due), the launch
  experiment's power verdict against real traffic (n=1 unique visitor vs
  2,936 required → BLOCKED(INSUFFICIENT_POWER), exactly as pre-registered;
  clone spike recorded as CI-confounded), and the append-only
  weekly-attention log (2026-W30 honestly `not_tracked`; discipline
  starts 2026-W31) — each with a test that re-derives it
- Packaging: voter charters moved into the package
  (`src/autoproduct/skills/`, root symlink kept) so the installed wheel
  runs stage commands; pip-installed builds previously crashed loading
  charters. MIT LICENSE file added; PyPI metadata completed
- Suite: 727 → 732 hermetic tests on the rebased tree (ledger PC-1 synced)

## v0.32.0 — gap plan D13: upstream critique rosters
- Discover/plan/spec critics ported onto the shared stage engine as 14
  registered charter voters (discovery: desirability/feasibility/
  viability/scope-discipline; planning: completeness/dependency-realism/
  risk-sequencing/parallelization-safety/estimate-sanity; spec:
  testability/consistency/completeness/ambiguity/interface-impact — doc
  13 §25.1), each behind the 8-fixture registration gate; the three
  single-panel critic prompts retired
- `run_critique_roster` extracted from the P-stage engine: charter voters
  with no cross-visibility → per-finding fresh verify → leader; failed
  gate runs exclude the voter, unregistered voters are reported
- Suite: 688 → 721 hermetic tests (ledger PC-1 synced)

## v0.31.0 — gap plan D14 + D16: GEPA proposer, secrets layer
- GEPA proposer (`gepa.py`): budget-gated by the v0.27 `gepa.yaml` schema
  (refuses at zero weekly rollouts or unlisted targets), deterministic
  salted-hash holdout split the proposer never sees, old-vs-new charter
  scored by the same fixture gate voters register through; improvements
  emit a `.mas/gepa/` proposal record for human review — nothing
  self-installs
- Secrets layer (`secrets.py`): `secret://ENV` resolution that errors
  loudly on missing values, `Secret` with masked repr and a single
  deliberate `reveal()`, `scrub()` stripping every resolved value from
  outbound text
- Suite: 673 → 688 hermetic tests (ledger PC-1 synced)

## v0.30.0 — audit gap closures, phase C
- Cost/observability ledger: config-priced estimates, unpriced-call
  visibility, monthly cap check, tool-audit + evidence-ledger writers,
  Prometheus /metrics
- Module-spec invariant layer with SPEC_DRIFT_UNDOCUMENTED
- Named signal webhooks (sentry/datadog/pagerduty) with dedupe window

## v0.29.0 — audit gap closures, phase B
- Voter families: voter-gate now serves web/miniprogram/app/data alongside
  the product stages (same skills+fixtures contract)
- Five profile voter charters authored; 8-fixture gates for them and for
  the three data voters (64 new fixture cases)

## v0.28.0 — audit gap closures, phase A
- Web det-tool runners (axe/Lighthouse/size-limit, availability-gated)
- Data NFR grammar + lineage impact check (doc 18 §48.1)
- Upstream verdict vocabulary, typed (doc 13)
- Gate P1 platform-preflight class (doc 17 §41.3)
- Data-classification tags check (doc 18 §49.3)
- This CHANGELOG (doc 10)
