# ADR-062 — an ignore is a decision: widening the gate that was drawn narrow

**Status:** accepted (2026-08-21)

**Answers:** the third strand of the founder's question behind ADR-060 and
ADR-061 — *can't we just find the existing issues?* ADR-060 swept the source
for facts with no reader. ADR-061 mined the result files. This one asks the
same question of the **linter configuration**: which classes of defect is this
codebase not looking for at all, and who decided that?

**Reverses:** ADR-055's `select = ["F"]`, and the test that pinned it.

## Context

ADR-055 added this project's first linter and drew it deliberately narrow:

> Scope is load-bearing. Selecting style rules here would bury F821 in
> hundreds of formatting findings, and a gate people learn to scroll past is
> not a gate. **If a later change widens `select`, this is the test that asks
> whether the widening was deliberate.**

That was right, and it was never revisited. Fifty-four releases later the gate
still asked exactly one question — *does every name resolve?* — while CLAUDE.md
carried at least three invariants stated in prose with no mechanism behind them
at all:

- "Replace all runtime `assert` statements with explicit conditional checks."
- "Never silently swallow exceptions with try/except/continue; log the error or
  handle it explicitly."
- "Invoke subprocesses with absolute executable paths and explicit argument
  lists, never with partial paths or `shell=True`."

A rule with no enforcement is a rule that is true until someone is in a hurry.

The general shape is ADR-058's, aimed one level up. There, a component
established a fact and the reader that needed it never got it. Here, a *rule*
was established and the mechanism that would ask it never existed — and the way
a gate like this dies is not by being wrong. It is by being turned off one
`ignore` at a time, each with a good reason nobody wrote down.

## Decision

**Widen `select` to the families about code that misbehaves, and treat every
`ignore` as a decision that has to be argued in writing.**

Selected: `F`, `B`, `S`, `BLE`, `W`, `A`, `PIE`, `RET`, `RSE`, `PTH`, `C4`,
`LOG`, `G`, `TID`. All of them are about behaviour. The cosmetic families —
`E` line length, `D` docstrings, `Q` quotes, `ANN`, `COM`, `EM`, `FBT`, `ERA` —
stay out, and a test now pins that boundary explicitly rather than pinning a
count. ADR-055's scope argument survives the widening intact; what it loses is
the assumption that "about behaviour" meant only `F`.

The widening left the codebase at **zero findings**, not at a backlog. That is
the condition on the decision: a gate that lands on a list people are told to
work through later is a gate people learn to scroll past, which is exactly what
ADR-055 refused.

### What it found, live, in the released package

Three defects, each confirmed against the *installed* v0.109.0 before the fix
was written — not against the working tree, because a defect that only exists
in an editor is a defect that might not exist.

**A comparison that stops at the shorter sequence is a check that quietly does
not check.** `B905` (`zip` without `strict=`) found the family:

1. `lanes/realtime.py::desync_probe` — a desync probe that zipped the server
   and client hash streams and read the common prefix. A client that *stopped
   producing* has no divergence in that prefix, so:

   ```
   desync_probe(["a","b","c","d"], ["a","b"], hash_every_n_ticks=10)
   → passed=True detail='no divergence'
   ```

   The most complete desync available, reported green, by the probe that
   exists to catch it.

2. `marketing/substantiation.py` — `zip(draft_numbers, register_numbers)`, so
   every figure past the register entry's last was checked against nothing:

   ```
   check_substantiation("Teams ship 40% faster across 12000 sessions.", …)
   → []          # the 12000 was never checked against anything
   ```

   An unsubstantiated number leaving through the check built to catch
   unsubstantiated numbers. Fixed as its own rule (`unsubstantiated_number`)
   rather than folded into `number_drift`, because the responses differ: drift
   means correct the figure, this means substantiate it or cut it.

3. `lanes/realtime.py::cross_build_replay` — the same prefix read, which
   rendered a truncated stream as `silent behavior change: divergence at tick
   None`. Now it says which build stopped and where.

`S110`/`S112` found **fifteen** silent swallows — the CLAUDE.md rule that had
never been enforced. Each handler now names what it lost:

```
DEBUG ai_venture_studio.spend: skipping an unreadable spend row —
money in it is not counted: Expecting value: line 1 column 1 (char 0)
```

Twelve modules gained a logger, and — this is the part that matters —
`AVS_DEBUG=1` gives them a **reader**. Fifteen handlers logging to a logger
with no handler is ADR-060's defect wearing a different hat, and shipping that
would have been this ADR committing the thing the previous one was about. Off
by default and it must stay off: these go to stderr and the CLI's real output
is Rich on stdout.

`S310` found `urlopen` handed a URL straight out of `SCHEMA_REGISTRY_URL`,
which honours whatever scheme it is given — `file:///etc/passwd` there would
have been read and its bytes handed to `json.loads`. Operator-set rather than
attacker-set, so it is a guard and not an incident, and it now says no in words
a misconfigured operator can act on.

Also: `B006` a mutable default, `B023` a late-bound closure variable (harmless
today and harmless only as long as nobody defers the call), `RET504`, `C416`,
`C408`, `PIE804`, `PIE810`, `RSE102`, `PTH123`.

## What stays out, and the one deferral that did not survive the week

**`S607` — 152 partial-path subprocess invocations — was deferred here and
then fixed before this release shipped (ADR-064).** CLAUDE.md says "invoke
subprocesses with absolute executable paths"; 60 sites in `src/` and 92 in
`tests/` said otherwise (44 of them `git`, plus `railway`, `supabase`, `gh`,
`npm`, `node`, `k6`, `glab`, `uv`, `launchctl`). There were three options and
each had a cost:

- fix all 152 now — a large mechanical change riding along in a release about
  something else, touching subprocess invocation across the codebase;
- ignore it silently — which makes a written rule quietly false;
- ignore it *with the deferral written down*, naming what the real fix is.

This record originally chose the third and surfaced the choice as the
founder's. The founder chose the first, and ADR-064 is the result: one
`shutil.which` resolver in `ai_venture_studio.executables`, every call site
routed through it, and the `ignore` entry deleted rather than grandfathered.

That is worth keeping in the record rather than editing away, because it is
the shape this ADR argues for working exactly once. The ignore named its own
fix in the file where it lived; a reader — the founder, in this case — could
see the cost of the deferral without reading a diff, and priced it. An ignore
that had said only `"S607",` would have been invisible and would still be
there. **An ignore-with-a-reason is a decision someone can overrule; a bare
ignore is a decision nobody knows was made.**

The test that guarded the deferral now guards its absence: it asserts `S607`
is in neither `ignore` nor any per-file exemption, and a second test walks
`src/` with `ast` and fails on any bare-name argv head — so the rule is held
by the code's shape, not only by the config.

`S105`/`S106` are ignored because every hit is an environment variable *name*
(`SENTRY_TOKEN_ENV`), and `S603` because the list-argv rule it checks is
already met everywhere and its stricter siblings `S602`/`S604`/`S605` are
selected and find nothing.

`S101` is ignored **in `tests/` only**. `src/` measured zero when the family
was adopted, and a test asserts the ignore never widens past `tests/` — outside
a test, an assert is a check that vanishes under `python -O`.

## Mechanism

`tests/test_the_gate_is_wider_now.py`, 25 tests, in three parts:

1. **The families that were adopted stay adopted.** A regression here is
   silent — the suite goes green, the lint job goes green, and the rules simply
   stop being asked.
2. **No rule is ignored without a written reason.** Every entry in `ignore`
   must carry a comment of at least twelve words immediately above it. This is
   ADR-060's allowlist-as-judgment shape applied to the linter: the mechanism
   does not decide whether an ignore is right, it only refuses to let one be
   added without an argument.
3. **The defects stay fixed by behaviour, not by asking ruff again.** A test
   that re-runs the linter proves the linter is configured; only a test that
   calls `desync_probe` with a client that stopped proves the probe now sees it.

`tests/test_a_name_that_resolves_nowhere.py` keeps ADR-055's scope test, now
pinning the boundary (`E`, `D`, `Q`, `ANN`, … stay out) instead of the literal
`["F"]` it can no longer pin. Both CI and the publish workflow run the gate,
and that duplication is itself tested (ADR-051).
