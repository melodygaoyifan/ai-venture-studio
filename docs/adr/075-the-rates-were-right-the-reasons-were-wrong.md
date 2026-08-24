# ADR-075 — the rates were right; the reasons were wrong

**Status:** accepted (2026-08-24) · **Release**: v0.117.0

## Context

Run 19 was bought as the first conclusive reading (ADR-074: every known
run-18 fault fixed before spending), and its *numbers* survived the debug
untouched: build 27/28, probes 13/13, clean 0%, gate 0/3, $81.72. What did
not survive is the run-day account of **why**. Per the standing direction —
debug, push, deploy; no new instruments — this ADR is the debug record for
every issue the run surfaced. There are six, and each was diagnosed from
artifacts the run had already preserved (blocked-voter records,
`progress.jsonl`, failed-build workspaces, the result file), not from a new
measurement.

The run-day HISTORY.md note attributed the eleven no-verdict rejections to
"a degraded provider window", the `Gate 2 blocked` rows to "the product's
own suite red at review time", and increment 2's miss to "the first genuine
gate reading". All three attributions were wrong, and all three were
**committed** before being checked against the preserved records. The
corrected text now stands in `benchmarks/results/HISTORY.md` and the PC-19
note in `claims/platform.yaml`; the recorded rates are not re-scored.

## The six findings

### A — eleven no-verdict rejections were parse/protocol failures, not a bad window

The live observation (the client asleep in its 6-attempt/60s-cap backoff
loop during case 05) was real but causally unrelated. The eleven rejections
trace to six preserved blocked-voter records whose API calls all returned
200 and were paid for:

| shape | count | record |
|---|---|---|
| entire 4096-token output budget spent on a thinking block (`stop_reason: max_tokens`, content `['thinking']`, zero text) — then retried blind ×3 at the same budget | 4 | the voter re-bought the identical failure |
| tool request with the tool name as a YAML key (`read_file: tool: read_file …`) — unparseable | 1 | the run-18 quoting rule's sibling |
| verdict delivered as prose, no YAML mapping | 1 | fail-closed rejected the task |

Three fixes, each at the layer that failed:

1. **Provider budget escalation** (`providers/anthropic_provider.py`): a
   response that is empty *because* it hit `max_tokens` with only thinking
   content is re-bought **once** at 4× budget (which crosses
   `_STREAM_ABOVE`, so the retry streams). Spend is recorded per pass.
   A blind same-budget retry buys the same failure at the same price —
   that is what the four preserved records show, three times each.
2. **Reverdict nudge** (`voters/base.py`): an unparseable verdict gets an
   in-conversation correction naming the exact expected shape, bounded by
   `_MAX_REVERDICT_NUDGES = 2`, instead of a fresh blind conversation.
3. **Requote nudge generalized** (`voters/base.py`): the malformed-request
   reply now names *both* observed breakages (tool-name-as-key, unquoted
   globs), not just the one run 18 saw.

The fail-closed posture is unchanged: a voter that still returns no verdict
after correction rejects the task.

### B — the `Gate 2 blocked` rows were the harness host's, not the products'

16 of 19 review-gate suite runs across cases 01–04 failed with import
errors — the same suites the *build* gate had run green in Docker minutes
earlier. The two gates run in different sandboxes: build prefers Docker,
review standard mode always runs `_pytest_in_subprocess` on the host. On
the host, a **regular** `tests` package installed in the runner's
site-packages shadows any product's init-less `tests/` directory, because
PEP 420 resolves a regular package at *any* `sys.path` entry ahead of every
namespace portion. `from tests.conftest import …` imported miniconda's
`tests`, not the product's.

Fix (`testing.py`): `_pytest_in_subprocess` drops a temporary
`tests/__init__.py` into the product (when none exists) and prepends the
product root to `PYTHONPATH`, both removed in `finally`. Validated against
scratchpad copies of all five preserved case workspaces: case 01 went 10
errors → 30 passed, case 04 15 errors → 61 passed, case 05 45 → 45.

The 0% clean rate is therefore dominated by A and B — two harness-side
defects — not by product quality. The reviewer's three real catches
(`N+1 per-candidate query`, `B310`) stand.

### C — the reconciliation gate was inert, not wrong

Increment 2 (the delete-endpoint follow-up, `expected raises_scr`) did not
"reach the gate and miss". `relevant()` (`upstream/requirements.py`) scores
by deterministic content-word overlap; the ledger is English EARS (the spec
pipeline writes it) and the follow-up is Chinese, so the slice came back
empty, `reconcile()` wrote `checked: false`, and the contradiction built
straight through — with `tests/test_no_delete.py` deleted by the build.
ADR-048's shape one layer up; ADR-050 fixed CJK-vs-CJK tokenization but
not cross-language retrieval.

Fix: zero overlap against a **non-empty** ledger now returns the whole
live ledger (capped at `_FALLBACK_CAP = 200`, drops reported), marked
`fallback=True`, and `render_slice` tells the judge why every requirement
is shown unranked. Degrading toward *more* context is the safe direction;
an empty slice makes the gate silently inert. Retired/superseded
requirements are filtered before the fallback, so it can never resurrect a
dead requirement.

### D — a deliberately empty plan is `already_satisfied`, not `failed`

Increment 1's planner examined the codebase map, recognized the follow-up
as already provided, and produced `tasks: []` — its only channel for
"nothing to build". `run_feature`'s completion check
(`status="completed" if outcomes and built_count == len(outcomes)`) mapped
that to `failed`. The planner did the reconciler's job correctly and was
scored as a build failure for it. Fix: `tasks: []` returns a distinct
`already_satisfied` status with a bilingual REPORT.md explaining that
nothing was built and inviting the founder to say what is different.

### E — a `--yes` run has nobody to answer intake questions

Increment 3 stopped at intake with the assessor's questions recorded
(ADR-058's fix visible) — but the run was `--yes`, unattended, so
`needs_answers` parks the request forever. Fix: on an **established**
product (`run_feature` only), a not-ready assessment under `--yes` records
the questions in `FDR-QUESTIONS.md` under a "we proceeded with sensible
defaults" heading (bilingual) and continues. First-FDR intake
(`run_autopilot`) keeps the strict bar: a product that does not exist yet
has no sensible defaults.

### F — the implementer prompt lied about the skeletons

The system prompt has always said "the test skeletons are the contract —
write them as real tests… Include the test files." The user prompt said
"Your spec's skeleton tests already exist ON DISK: do not resubmit them" —
and **nothing has ever written them**; the spec stores only
path/purpose/covers. Whichever instruction the model obeyed decided the
task: 04-t1 and 04-t2 obeyed the user prompt, submitted source only, and
died on `pytest collected no tests` three attempts straight, with the
feedback naming the symptom while the prompt forbade the fix. Both
preserved failed-build workspaces have no `tests/` directory at all.

Fix (`upstream/build.py`): the skeleton listing now annotates each path
with its disk truth — `[NOT on disk — write this file]` or
`[on disk — read-only wall]` — checked per path at prompt-build time. The
wall semantics (`_write_files`: assert-weakened rewrites discarded,
existing tests read-only) are unchanged; only the claim about what exists
is now true.

## Decision

Ship all six fixes as one release, v0.117.0, with this ADR as the single
debug record — no new instrument, no bench run bought to confirm. New
tests (take-the-fix-away verified, per ADR-066/067): provider escalation
and voter nudges, the shadowing shield, 9 tests in
`test_an_increment_survives_a_language_boundary.py` (fallback semantics
plus the whole increment path: cross-language duplicate refused, zero-task
plan `already_satisfied`, `--yes` intake records-and-proceeds, non-`--yes`
still stops), 3 in `test_skeletons_are_the_implementers_to_write.py`
(prompt truth per path, system contract retained).

Correct the run-day attributions **in place, visibly** in HISTORY.md and
the PC-19 note — the recorded rates stand; only the causal story changes
(ADR-072's precedent: the correction lives in the record itself).

## Consequences

- Run 19's three headline "failures" decompose as: clean 0% ← A + B
  (harness), gate 0/3 ← C + D + E (harness). The gate axis has **still
  never been genuinely asked its question** — three misses, three
  different harness defects. That is a fact about the instrument, not a
  scheduled reason to buy a run; the next paid run, whenever one is
  warranted, inherits these fixes.
- The lesson that names this ADR: a run-day narrative written while the
  process is visibly misbehaving (a backoff loop you can watch) will
  attribute everything to what it can see. Every one of the six causes was
  already sitting in a preserved artifact; none required new
  instrumentation to find. Check the records before committing the story.
- Shape note for future debugging: C, D and F are one shape — a component
  silently accepting an input that means "I cannot see what you meant"
  (empty slice, empty plan, a claim about disk state nobody checked) and
  proceeding as if it had seen. The safe degradations were: show
  everything (C), name the distinct state (D), tell the truth per path (F).
