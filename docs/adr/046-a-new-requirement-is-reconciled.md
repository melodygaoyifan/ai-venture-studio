# ADR-046 — A new requirement is reconciled against the old ones

Status: accepted (v0.96.0)

## Context

ADR-045 gives the product a list of its promises. This record is about the only
question that list makes answerable, and the reason it was worth building:
**when the founder asks for one more thing, is it new?**

`avs add` has always planned a feature against the *code*. Three outcomes it
could not tell apart:

- The product already does this. The correct action is to say so, and build
  nothing. Instead the planner planned it, the builder built it, and the
  reviewer — sometimes — noticed the overlap afterwards, at full cost.
- The request cannot be true at the same time as something the product already
  promises. The correct action is not a build at all; it is a *change*, which
  this system already has a channel for (ADR-U02's SCR). Instead a second rule
  was added beside the first, and the product then promised both.
- The request genuinely adds something. This is the common case and it was
  already handled correctly.

The founder this system is built for is as non-technical and as lazy as
possible. They will ask for things they already have, in different words, and
they will ask for things that quietly contradict what they asked for a month
ago. Neither is a mistake on their part. Noticing is the machine's job.

## Decision

**Between retrieval and planning, one model call classifies the request against
the requirements it plausibly touches.** Two stages, and the split is the
point: `requirements.relevant` narrows the corpus with no model and no cost,
and the reconciler judges only that slice — so its answer does not degrade as
the product grows past 300 promises.

**Three relations, deliberately not four.** `duplicate`, `contradicts`,
`extends`. There is no `unclear`: a judgment the reconciler cannot make falls
through to `extends`, which is the behaviour that existed before this record —
build it, and let the review stage catch the overlap. A gate that stops on its
own uncertainty stops constantly, and `ears.py` carries the scar from a lint
that rejected correct work.

**`checked` is separate from `relations`.** Empty relations with `checked=True`
says "nothing conflicts". Empty relations with `checked=False` says "nobody
looked". Reading the second as the first is the defect ADR-041 removed from the
spec stage, and it would be worse here, because this gate exists to say no. A
truncated answer, an unparseable answer, an empty slice, and an answer that
names no requirement it was shown are all `checked=False`. A verdict about an
id that was never shown is dropped — acting on it would supersede a promise
chosen at random.

**A duplicate stops the build in both modes.** `--yes` means "do not ask me
about the plan". Building what the product already does is waste in either
mode, and the evidence is mechanical: a passing test already covers the
promise. The founder gets `FDR-ALREADY-BUILT.md` naming the requirement, its
text, and the test file — an id alone is not something a non-technical person
can act on. This is `status="already_satisfied"` and it **exits 0**. A non-zero
code would train every wrapper to treat a correct answer as a failure.

**A contradiction stops for a person, then proceeds under `--yes` without
retiring anything.** Interactively it is `status="needs_decision"`, exit 2, and
`FDR-DECISION.md` shows the one command that resolves it —
`avs add <fdr> --replace R-0xx --yes`. Under `--yes` the build proceeds and an
SCR is *raised and not approved*: `--yes` authorizes the build, not the
retirement. Approving it here would forge the authorization ADR-U02 exists to
require.

**Only an explicit `--replace` supersedes a promise, and only after the build
succeeds.** The supersession waits for every task to reach `built`, mirroring
`apply_pending_amendment`: a product with the old promise retired and the new
one unbuilt promises strictly less than it did before.

**`already_satisfied` is not a bench failure.** Scoring a correct refusal as a
failure would mean the only way to pass the bench is to do the redundant work.

## What this reverses

Nothing in ADR-U02 — it routes *into* the SCR channel rather than around it.
It does change `avs add`'s contract: a request that previously always produced
a build can now produce a refusal or a decision, with two new statuses and a
new exit code. That is why this ships as a minor version and not a patch.

It also narrows what `--yes` means, for the first time. `--yes` has been "do
not stop for me". It is now "do not stop for me about the *plan*" — it does not
authorize retiring a promise, and it does not authorize building something the
product already has.

## What stays out

- **An `unclear` relation.** Covered above; uncertainty is `extends`.
- **Approving the conflict SCR automatically under `--yes`.** The SCR is
  proposed. A machine that grants its own authorization has no authorization.
- **Blocking on `extends`.** Related-but-compatible is the normal case and must
  cost nothing.
- **Reconciling against retired or superseded promises.** They are kept for
  reference, not for judgment; `relevant` offers only live ones.
- **A fifth case in the measured bench series.** `06-already-promised` — a
  base product that promises cancellation, a follow-up asking for it again in
  a founder's later words, and a second follow-up that is a genuine addition
  and must *not* be refused — goes in `benchmarks/products`, not
  `benchmarks/products-real`. The nightly series is what run 17 uses to
  measure ADR-044; changing its denominator in the same release would put two
  changes in one run and make neither readable. The real-series case is worth
  authoring, after run 17 reports. **Authored in ADR-049** —
  `05-increment-repairs` carries `axis: increment`, so it does not change this
  series' denominator at all: the four build-axis cases stay the whole of it,
  and the gate is scored separately. 0.98.0 is deployed after run 17 for the
  same one-change-per-run reason this paragraph gives.
- **A second model call to double-check the first.** The stakes are asymmetric
  and already handled: a false `extends` costs one redundant feature that the
  reviewer may catch, while a false `duplicate` blocks real work — which is
  why `duplicate` requires "genuinely the same promise" in the prompt and why
  the founder's refusal file names the test they can go read.

## Mechanism

`upstream/reconcile.py` — `Reconciliation.checked`, the three-relation prompt,
the unknown-id and unknown-relation drops, `render_for_planner`, and `save`
(the verdict is written to `product/features/NN/reconciliation.yaml`); the
gate, `_write_already_built`, `_write_decision`, `_propose_conflict_scrs` and
the deferred `supersede` in `run_feature` (`upstream/autopilot.py`); the
`<conflicts_to_plan_around>` block and its planner rule ("change it in place,
never add a second rule beside it"); `--replace` and the two exit codes in
`cli.py`; the `already_satisfied` allowance in `product_bench.py`.

`tests/test_a_new_requirement_is_reconciled.py` — nineteen tests pinning the
rule: four ways to be unreadable and none of them clean, verdicts about unshown
ids dropped, no `unclear` relation, supersession touching only live
requirements and only after a successful build, and — by AST walk — that the
duplicate refusal does not read `yes`, that the conflict path raises an SCR it
never approves, and that reconciliation happens before planning rather than
after, since after planning it is a report and before planning it is a gate.
The last one is the whole path rather than its parts: a workspace whose ledger
already holds the promise, driven through the real `run_feature`, has to come
back `already_satisfied` with a file the founder can read, no outcomes, the
matched promise untouched, and the verdict saved beside the feature.
