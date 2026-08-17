# ADR-043 — A case is measured or it is not

Status: accepted (v0.94.0)

## Context

Bench run 16 reported `build 100% · probes 75% · clean 31%` over "3 of 4
cases measured", and closed with the line the harness prints for every
exclusion: *these are excluded from the rates above, not scored as zero*.

That sentence was true of two rates out of three.

`02-shortener-api` planned for 6.4 minutes, revised twice, came back with
no tasks, and was named in `unmeasured`. Its two probes ran anyway —
against a workspace with no product in it — failed `AssertionError: no
runnable entry point`, and entered the probe average as a real `0.0`. The
cause is that exclusion was decided **per rate**, not per case:
`unmeasured` was derived from `build_rate is None` (`product_bench.py:472`)
while each of the three rates averaged whatever it happened to have
(`_avg`, `:461–471`). A case's probe list produces a denominator whether or
not there is anything to run the probes against, so the probe rate never
saw the exclusion at all.

This is **run 12's error, in the same file, after the ADR written to fix
it**. ADR-035 asked "did this case produce a denominator?" — one rate at a
time.

The second half is wrong in the other direction, and it is the larger
number. Case 02 did not crash. It ran, and the machine failed to produce a
product. ADR-035 already says what that is worth in words — *"a case that
ran and built nothing still scores a real 0.0"* — and the code read
`tasks_total == 0` as "no denominator" instead of as the most complete
build failure available. Its two probes failing were **correct**: there was
no product, and that is a fact about the run.

So `build 100%` was the rate over the cases that got a plan, not over the
cases that were asked for a product. The honest reading of run 16 is
**`build 75% · probes 75% · clean 31%` over 4 of 4**.

Two more defects follow from the same run, and they are what make the new
`0.0` legible rather than merely lower:

**A blocked plan carried no reason.** `run_planning` came back `blocked`,
and `autopilot.py:302` turned that into `status="failed"` and nothing else
— a run that spent six minutes and produced no product reported the single
word *failed*. The reason was on disk in `product/plan.yaml` the whole
time: `unparseable planner output (ValueError)`, twice. And upstream of
that, the parse branch in `plan.py` kept only `type(exc).__name__`. A yaml
`ScannerError` carries *"line 3, column 9: expected alphabetic or numeric
character but found '*'"*; the revision prompt reduced that to the word
`ScannerError` and asked the model to fix a break it was never shown. Both
revisions failed the same way. That is ADR-041's failure (the writer never
told what was wrong with its answer) and ADR-042's (a cause recorded as
decoration instead of a fact) at the planning stage.

**Three rejections were not about the code.** `01-t4`, `03-t3` and `04-t6`
were rejected with nothing but LOW findings — a severity that cannot block.
`leader.synthesize` rejects on two triggers, not one: an actionable
severity, **or** `len(blocked) == 2`, two voters whose status came back
anything but OK. Nothing downstream knew about the second trigger, so those
rows reached the scoreboard as `REQUEST_CHANGES [review: 1 low — B310:
blacklist]`, naming the finding that did not reject them and omitting the
two reviewers that did. The tell was already in the record — all three rows
carry an empty `blocking_by_voter` — but the sentence a person actually
reads said something else. Three of eleven rejections, charged to the
products.

## Decision

**Measured is one decision, made by the case, read by every rate.**
`CaseResult.measured` is `not autopilot_status.startswith("error")` — only
the harness dying makes a case unmeasured. Each per-case rate returns
`None` when `not measured` and a number otherwise, `summary.unmeasured` is
read from `measured` rather than re-derived from one rate, and a case that
ran and produced no tasks scores `build_rate == 0.0`.

`clean_review_rate` deliberately still returns `None` when nothing was
built: unlike the build rate it has no denominator to be a zero *of* — a
product with no tasks has no reviews to be unclean, and entering it as 0.0
would punish the same failure twice.

**A failure carries its reason to the durable record.**
`AutopilotResult.blocked_reason` is set at every `status="failed"` return,
`CaseResult.failure_reason` copies it, and it is written into the result
YAML — the workspace it would otherwise have to be read from is gitignored
and has been lost before. The CLI prints one line per case that produced
nothing and says why, so the new `0.0` is never an unexplained zero. The
planner's parse branch keeps `str(exc)` in both the revision feedback and
the recorded issue.

**A rejection by silence says so.** `_blocked_voter_note` prepends the
blocked voters to a non-clean `detail`, and states whether they merely
contributed or *are* the rejection — the latter when no finding sits at an
actionable severity.

## What this reverses

ADR-035's implementation, not its principle. "A crashed case is dropped
from the rates, not scored 0.0" stands and is now pinned by a test over
three crash shapes. What is reversed is the reading of `tasks_total == 0`
as a crash: ADR-035's own text already excluded it, so this is that ADR
enforced rather than amended.

The comparability of the build rate breaks here. Run 16 and everything
before it measured cases that got a plan; run 17 measures cases that were
asked for a product. `metrics/product_bench_capability.md` records the
break, and PC-17's note carries the honest reading of run 16 beside its
recorded numbers — the recorded numbers are not rewritten.

## What stays out

- **Dropping the probes of an unbuilt case.** That was the first fix I
  reached for and it is the wrong one: it makes the number prettier by
  deleting the observation that the product was not there.
- **Scoring a crashed case 0.0.** A provider 529 says nothing about whether
  the machine can build software, and entering it as a zero lets an outage
  fire a capability verdict. This is ADR-035's actual holding.
- **Retrying an unparseable plan more than the existing two revisions.**
  The revisions were not too few; they were uninformed. Whether two
  informed revisions are enough is a question for run 17.
- **Any change to case 02, or to the planner's model.** The case is fair
  and the failure was real.
- **Deriving `unmeasured` from any rate, including the build rate.**
  Re-deriving it is exactly how the list came to describe averages it did
  not match; a test asserts the list equals the set of cases whose own
  `measured` is false.

## Mechanism

`CaseResult.measured` / `.build_rate` / `.probe_pass_rate` /
`.clean_review_rate` and `run_product_bench`'s `unmeasured=` in
`product_bench.py`; `blocked_reason` on `AutopilotResult` and
`_blocked_voter_note` in `upstream/autopilot.py`; the parse branch in
`upstream/plan.py`; the per-case reason line in `cli.py` and the same
reason appended to the case's line in `notify.bench_alert` — the alert
would otherwise have announced run 16's case 02 as "`02-shortener-api` —
failed", which is the category, not the fact.

Two test files, each pinning the invariant rather than run 16's instance:

- `tests/test_unmeasured_is_one_decision.py` — an excluded case is excluded
  from *every* rate, over three crash shapes; a case that ran and produced
  nothing is measured; `unmeasured` equals the cases whose own decision
  says so. It also recomputes run 16 from its own recorded result file
  rather than from a retyped number, so the corrected reading asserted
  here, in `HISTORY.md`, in the CHANGELOG and in PC-17 fails the suite if
  the rule that produces it ever stops producing it.
- `tests/test_a_refusal_names_its_own_cause.py` — an AST walk over
  `autopilot.py` that rejects **any** `AutopilotResult(status="failed")`
  without `blocked_reason`, so a fourth failure site cannot be added
  silently; the revision prompt is checked against the exception the
  fixture actually raises, not a hard-coded message, so a yaml or pydantic
  upgrade cannot quietly empty it.
