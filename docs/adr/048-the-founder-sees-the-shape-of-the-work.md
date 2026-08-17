# ADR-048 — The founder sees the shape of the work

Status: accepted (v0.97.0)

## Context

This system runs on a granularity rule: **one FDR = one thing.** Every document
in this repo says so, `avs add`'s own help string says so, and it is correct —
a feature request that contains an "and" produces a plan that contains a guess.

The rule has always been enforced on the *founder*. They arrive with a
paragraph — "I want the thing my building's group-buy runs on" — and the
product's answer is: split that into twelve small documents yourself, in the
right order, each one small enough, and hand them to me one at a time. That is
unpaid labour. It is the part a non-technical person is worst at. And it is the
one step in the entire pipeline that is still manual, in a system whose whole
premise is that everything downstream of an FDR is automated.

The mirror of that problem sits at the other end. After twenty features the
product promises perhaps two hundred things, `product/requirements.yaml`
records every one of them with an id (ADR-045), and there is no way to *look*
at them. A founder about to ask for one more thing cannot see what they already
have. `avs status` does not exist; the closest thing, `avs digest`, reports
telemetry.

These are one record because they are the same decision seen from both ends:
**a view over the requirement ledger, re-derived rather than believed.** One
looks forward at work not yet done, one looks back at promises already made,
and both refuse to report a state they did not verify.

## Decision

### `avs roadmap` — paragraph in, ordered increments out

One model call turns the founder's description into 3–12 steps, each one a
request `avs add` can take as written, in the founder's own vocabulary, with
dependency edges. The founder approves by tapping a sequence rather than typing
twelve documents.

**The roadmap is a proposal, not a contract.** A twelve-item roadmap written on
day one is stale by item three, because the build teaches things the paragraph
did not know. `rederive` re-reads the remaining steps against the ledger after
each increment lands, reusing the ADR-046 reconciler: a step the product now
satisfies is marked done instead of being built a second time. A stale roadmap
the system still believes is worse than no roadmap — it has an authoritative
shape with a wrong answer inside it.

**A step is done only when something says so.** The reconciler returns
`checked=False` rather than a clean verdict when it could not judge, and an
unchecked step stays pending and is *named* as unchecked. Marking work done
because a check failed to run is the defect ADR-041 removed from the spec
stage; here it would silently delete a feature from the founder's plan. The
same applies to the re-check cap: steps past it are reported unchecked, not
counted as pending (ADR-039).

**Order is repaired; loops are refused.** A prerequisite listed second is a
wrong *order*, not a wrong plan, and order is the one part of a proposal that
code can fix without guessing — so the steps are topologically sorted. A
dependency cycle has no order that builds it, and picking one would hand the
founder a sequence that cannot be executed, so it is `checked=False`. An edge
to a step that does not exist is dropped, because keeping it would make
`next_step` wait forever for something nobody proposed.

**The founder's loop is two commands, neither of them a document.** `avs
roadmap` writes the next step to `FDR-NEXT.md`; `avs add FDR-NEXT.md --yes`
builds it; repeat. The file is overwritten each time on purpose — a directory
of `FDR-NEXT-3.md` files is a decision the founder then has to make.

### `avs requirements` — what your product promises

The live promises, grouped by the part of the product they belong to, each with
its status and the test file that checks it. `--all` includes retired and
superseded ones. The ledger is synced before it is read, because it is derived
and never hand-maintained (ADR-045).

**Every `tag_checkpoint` freezes a baseline**, so the view can end with "since
ap-checkpoint-004: +6 promise(s), 1 superseded". The stored value is the
id→status **map**, not a hash. A hash answers exactly one question — did
anything change — and the question a founder asks is the other one: *what*
changed, and is anything I used to have gone. Two hundred ids and a status each
is a few hundred bytes.

**A checkpoint with no baseline reports nothing, not "no change".** Every
checkpoint tagged before this record existed is one of those, and "+0 since
checkpoint 2" would be a measurement of a missing file rather than of the
product. **A baseline that will not write does not cost the checkpoint**: the
tag is the founder's undo, and losing it over a bookkeeping file trades the
important guarantee for the small one.

### The retrieval was inert in Chinese, and that made the gate inert too

Building `rederive` on top of ADR-046's retrieval is what surfaced this. A
step written in the founder's own words retrieved *nothing*, on a ledger that
plainly contained the same promise.

`requirements.tokens()` used a single regex that requires an ASCII letter.
Chinese has no spaces, so it found **nothing** in a Chinese criterion: every
score was zero, `relevant` returned an empty slice, and the ADR-046 gate
therefore reported "no existing requirement matched this request" for every
request in the language this product's founder actually writes in. The
templates are bilingual, the Studio ships Chinese by default, and every case
in `benchmarks/products` is Chinese.

The gate was not *wrong*, it was **inert** — the hardest kind of broken to
notice, because it never fires and never errors. It shipped that way in
v0.96.0 and would have been measured as a working gate by run 17.

The fix is character bigrams unioned with the existing Latin rule, plus a
small set of Chinese function characters as stopwords. Not a segmenter: no new
runtime dependency, no dictionary to go stale, and the failure mode is
symmetric noise — a bigram straddling two words scores equally against every
candidate — rather than a missed match. Ranking here is comparative and
capped, so noise costs a little precision where the alternative cost
everything.

**The lesson this records is about the test, not the tokenizer.** ADR-046's
tests were written in English against an English ledger, so they all passed
against a function that could not read the product's own benchmark corpus. A
retrieval test now exists in both languages.

## What this reverses

Nothing in this record's own scope. Both commands are new, `tag_checkpoint`
keeps its signature and its behaviour, and no existing command changes what it
does. The granularity rule is unchanged — the roadmap *enforces* it rather
than relaxing it; what changes is who does the work of satisfying it.

The tokenizer fix reverses no decision either; ADR-046 intended retrieval to
work and it did not.

## What stays out

- **Building the roadmap automatically.** `avs roadmap` proposes and hands over
  one step. It does not loop into `avs add`. An unattended run that builds
  twelve features off one paragraph is the failure mode this whole system is
  organised against, and the founder tapping through a sequence is the point,
  not an inconvenience to be removed.
- **Marking a step done because its words look built.** Lexical similarity
  between a step and a requirement is not evidence. The reconciler is a typed
  verdict over a retrieved slice and it can say it does not know; a token-
  overlap score cannot.
- **Re-proposing the remainder with a second model call.** `rederive` marks
  what is done and leaves the rest as written. Re-generating the pending steps
  after every build would make the roadmap unstable — the founder would see a
  different plan each time they looked, with no way to tell a real re-think
  from model noise.
- **A checkpoint baseline for the constitution.** ADR-047's invariants are not
  promises, and "since checkpoint 4: +2 things you ruled out" is not a sentence
  a founder needs. It can be added when someone wants it.
- **A bench case.** Neither command is on the measured path: `product-bench`
  drives `avs create` and `avs add` with FDRs the case file supplies, and
  neither of those calls the roadmap. Adding a case that exercises `avs
  roadmap` would measure a different thing under the same rates and make the
  series unreadable — the same reason ADR-046's case went to the synthetic set.
  The obligation this creates is named in the plan and stands: after run 17
  reports, a real-series case covering the increment path (ADR-046) and its own
  metric file.

## Mechanism

`upstream/roadmap.py` — `propose` (with `checked=False` on truncation, parse
failure, no buildable step and dependency cycles), `_ordered` (stable
topological sort), `next_step`, `rederive` and `RederiveReport.unchecked`,
`save`/`load` into `product/roadmap.yaml`. `MAX_STEPS = 12`, `RECHECK_CAP = 8`.

`upstream/requirements.py` — `write_baseline`, `load_baseline`, and `since`
returning a typed `Since` or `None`; `tag_checkpoint` (`upstream/autopilot.py`)
writes the baseline after tagging and swallows only `OSError`. `_CJK`,
`_CJK_FUNCTION` and `_cjk_tokens`, unioned into `tokens()`.

`cli.py` — `avs roadmap` (propose / re-derive / hand over `FDR-NEXT.md`) and
`avs requirements` (grouped view, `--all`, the since line). The mock provider
grows a `ROADMAP_MARKER` branch that splits on the founder's own sentence
breaks, so the CLI path is exercisable end to end without a model.

`tests/test_the_founder_describes_the_product.py` pins the refusals rather than
the happy path: an unparseable roadmap is not an empty plan, a truncated one is
not a plan, a cycle is refused while a mis-ordered prerequisite is repaired, an
edge to a nonexistent step cannot deadlock `next_step`, an unchecked step stays
pending and says so, steps past the cap are unchecked rather than pending, a
step nothing matched costs no model call, a checkpoint with no baseline returns
`None`, and a baseline that raises `OSError` still leaves the founder their
undo.
