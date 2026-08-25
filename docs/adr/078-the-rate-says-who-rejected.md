# ADR-078: the rate says who rejected

Date: 2026-08-25
Status: accepted
Release: v0.120.0

## Context

Run 19's headline read **clean 0%**, and finding out what that meant took a
debugging session: reading every unclean row's prose `detail`, classifying
each by hand, and discovering that *every* rejection was machine-caused —
Gate 2 blocked by the host's site-packages shadow (ADR-075), voters with no
verdict from provider parse failures (ADR-075), findings whose subject was
the autopilot's own artifact (ADR-076 #2, ADR-077). Not one row said "the
reviewer read working product code and found a defect in it."

The attribution was knowable at scoring time. `review_and_repair` holds the
final review object — findings with severities, blocked voters, the Gate 2
note — at the exact moment it composes the prose `why`. It threw the
structure away and kept only the prose, so the scoreboard could say *how
many* rows were unclean but never *who rejected them*, and the next person
to read a 0% starts the same archaeology over.

A second, related gap: re-measuring the three affected cases after the
fixes meant paying for the whole suite. `--limit N` (ADR-066) slices a
prefix of the sorted case list — it cannot name cases 03/04/05, so the
cheapest honest reading of "did the fixes land" was the full batch. The
founder's question — *"do we really need to run batch 20?"* — is a question
about exactly this gap.

## Decision

Two changes, one release. No recorded rate is re-scored; the rows gain
structure they always could have carried.

**1. Rejection-cause attribution.** `_rejection_causes(review, *,
gate2_blocked)` names every trigger that made `leader.synthesize` reject:
`gate2`, `voters_no_verdict` (≥2 blocked voters), `findings:<severity>` for
each actionable severity present (critical/high/medium — LOW cannot reject
alone and is not a cause), `no_review` when there is no review object at
all, `other` when a rejection matches no known trigger. The list travels:
`review_and_repair` returns it, `TaskOutcome.rejection_causes` persists it,
the bench outcome row carries it, `summarise` tallies rows into
`BenchSummary.unclean_causes` (rows judged, built, and not clean; rows from
before this release tally as `unrecorded`), and the CLI prints the tally
under the clean-rate line. A future 0% announces its own causes.

**2. `--only CASE[,CASE]` — the named-case slice.** Same honesty contract
as `--limit` (ADR-066): the denominator is the whole suite, skipped cases
get named `error: not run: --only` rows, the summary is `truncated`, the
result file never lands in tracked `benchmarks/results/` (save_summary
refuses), and `bench_criterion` refuses a hand-copied file by its
`only_cases` marker. An unknown name is a `RuntimeError` naming the typo
and the suite — a typo that silently ran zero cases would score as a
suite of skip rows. `--only` naming every case is a complete reading
(`truncated` reads the rows, not the flag). One payload subtlety:
`save_summary` previously wrote `limited_to` unconditionally when
truncated, which for an `--only` run would have written `limited_to: None`
— a value the criterion's `is not None` check does not catch. Each marker
is now written only when it is real.

## Consequences

- The kill-criterion ledger's protections extend to named slices; buying
  only the cases a fix touches is now a supported, honest purchase.
- `tests/test_the_rate_says_who_rejected.py` pins the cause vocabulary and
  the tally; `tests/test_a_named_slice_is_still_a_slice.py` mirrors
  ADR-066's harness for the named slice, including the layer-2 refusal.
- Pre-v0.120.0 result files tally `unrecorded` — described, never
  re-scored, per the standing attribution rule.
