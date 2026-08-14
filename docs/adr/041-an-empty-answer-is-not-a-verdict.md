# ADR-041 — An empty answer is not a verdict

Status: accepted (v0.91.0)

## Context

Bench run 15 blocked two independent cases — `01-groupbuy-api` and
`04-direction-workbench` — with byte-for-byte the same spec on disk:

```yaml
status: blocked
design: ''
criteria: []
test_skeletons: []
block_reasons:
- no acceptance criteria
revisions: 2
```

"No acceptance criteria" reads as a judgment about a spec the writer
wrote. It was not. Nothing was written. The stage had no way to say that,
and three separate gaps kept it from finding out.

**Emptiness passed every quality check by having nothing to check.**
`ears.lint_criteria([])` is clean, `_coverage_gaps` finds no uncovered
criterion among zero criteria, and `_foreign_skeletons` finds no
wrong-language skeleton among zero skeletons. All three came back empty,
so the revision loop's break —

```python
if not lint and not gaps and not majors and not foreign:
    break
```

— fired on a spec containing nothing, declaring it good enough. Only an
LLM critic could object, and whether one did was luck: case 01 drew three
completeness majors and looped; case 04 drew none and broke early. Same
defect, two different-looking symptoms. And when case 01 did loop, the
feedback blob handed back to the writer carried four keys, all of which
are silent on an empty spec — so it was revised twice and never once told
that its criteria list was empty.

**The spec stage never asked whether its response had been cut off.**
`plan.py`, `build.py` and `discover.py` all check
`last_response_truncated()`; `spec.py` did not import it. `providers/base.py`
names this exact hazard: a response that hit the output cap is *"a PARTIAL
answer wearing the shape of a complete one… truncated YAML usually still
parses"*. A response cut off after `title:` is a valid mapping with zero
criteria — indistinguishable, downstream, from a writer that considered
the request and produced nothing.

**The ledger could not settle which of the two it was.** Diagnosing run 15
meant reading `spend.jsonl` and inferring truncation from `output_tokens`
sitting exactly on a cap — which cannot distinguish a capped answer from a
complete one that happened to be that long. The evidence needed to tell
the two apart was recorded during the run (`record_stop_reason`, thread-local)
and discarded at the end of it.

## Decision

1. **Emptiness is the loudest complaint, not the quietest.** `_undelivered()`
   reports what the writer failed to deliver at all. It gates the "good
   enough" break, and it leads the revision feedback under the key
   `you_returned_nothing` — first, because every other key in that blob is
   empty exactly when this one is not.
2. **The spec stage asks whether its response was cut off**, using the same
   idiom as every other writer stage, and discards a truncated response
   rather than parsing it.
3. **A cut-off response gets its own block reason.** "Spec writer response
   cut off at the output cap" and "no acceptance criteria" are different
   diagnoses that lead a person to different next actions — raise the cap,
   or rewrite the prompt. The plain wording survives for the ordinary case.
4. **The ledger records `stop_reason` on every call**, across all three
   adapters. Optional, so every result already on disk still loads.

## What stays out

- **No raise of the spec writer's `max_tokens`.** The 4096 cap is not shown
  to be binding: case 04's largest output across all 112 calls was 2048, and
  case 01's twelve capped calls all carried ~30k input, which is the
  implementer, not the spec writer. Raising a cap on the strength of a
  hypothesis is how an unfalsifiable "fix" gets shipped. Decision 4 makes
  the next occurrence answerable from the ledger; if it is truncation,
  raising the cap will then be an evidence-backed change.
- **No retry added for case 04's 529.** The adapter already retries six
  times with jittered backoff to a 60s cap, honouring `retry-after`. That
  case exhausted a correct retry policy against a sustained overload. ADR-035
  already governs the outcome: it is excluded from the rates, not scored
  zero, and named in the report. Nothing here is a defect.
- **No new failure mode for the bench.** An empty spec still blocks. This
  changes what the machine *says* about it and whether the revision loop
  gets a chance to fix it — not whether it counts.

## Mechanism

`_undelivered()` in `upstream/spec.py`, the truncation guard at the top of
the revision loop, the `truncated` flag reset per attempt so one cut-off
response cannot mislabel a later block reason, `SpendEntry.stop_reason`, and
the `stop_reason=` argument threaded through the anthropic, google and
openai-compatible adapters.

`tests/test_spec_empty_response.py` pins all of it, including the premise
(an empty spec passes all three quality checks) and the invariant rather
than the instance (*every* writer stage asks whether it was cut off, so the
next one added without the check fails there).
