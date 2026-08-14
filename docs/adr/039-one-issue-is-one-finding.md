# ADR-039 — One issue is one finding, and a bound that hides is a bug

**Status:** accepted (v0.89.0)
**Reverses:** nothing — it fixes the *class* of failure ADR-037 and ADR-038
found instances of, in the stage that produces the number both were about.

## Context

Bench runs 13 and 14 scored build and probe rates at or near 100% while the
clean-review rate sat at 75% and 38%. ADR-037 and ADR-038 removed two real
causes (a repair pass filtering on a narrower severity set than the leader
blocked on; three definitions of "clean"). Neither explained the whole gap,
so run 13's *preserved review artifacts* were read directly.

The finding: **9 of the 15 blocking findings in the reviews examined were one
bandit check.** The build stage had copied `tempfile.mktemp(suffix=".db")`
into nine test files; bandit raised B306 at each; the leader kept all nine.

Three separate defects produced that, and each one alone is survivable:

1. **The leader's dedupe key is `(file_path, line_start, title)`** — keyed on
   *location*. The same issue at a different path was never a duplicate, so
   one issue in nine files was nine blocking findings.
2. **The repair pass is capped at 8 findings.** Nine copies means eight are
   repaired, the ninth survives *by construction*, and the re-review rejects
   the task again — **unclearable no matter how good the fix was.** The cap
   was an unnamed literal appearing twice, and nothing recorded that it had
   dropped anything, so the row read like an ordinary rejection.
3. **The analyzer judged test scaffolding against production rules.** This
   had already happened once: in run 11, B310 (a urllib audit firing on the
   suite's own localhost client) was 30 of 44 review findings, and the fix
   was to skip B310 *by name*. B306 then walked through the same door two
   runs later. Naming the next `test_id` after each run is not a fix.

And the scoreboard could not have told anyone this. A row recorded *that* the
review rejected the work, never *who* rejected it — so a strict reviewer and
a miscalibrated one produced indistinguishable records, which is ADR-036's
evidence-deletion failure one stage over.

A fourth, unrelated instance of the same "patched the instance, not the
class" shape was found while investigating a rejected README draft: the two
claim gates (`product.platform_claims`, `marketing.substantiation`) carried
two hand-maintained superlative word lists that had already drifted, and
`#1` was in **both** lists and could never match in **either** — written
`\b#1\b`, where `\b` requires a word/non-word transition that a space and a
`#` cannot provide. It ships here because it is one release and one lesson.

## Decision

**One issue is one finding, however many files it appears in.** The leader
folds repeats of the same `(voter, title)` across files into a single
finding carrying `occurrences` and `also_in`; the worst severity any site was
raised at wins, and the summary reports the fold.

**A folded finding loses a row, never a fix target.** The repair prompt is
shown every site in `also_in`, so a fix cannot repair one file and leave
eight for the re-review to find.

**A bound that drops work says so.** The repair cap is the named
`MAX_REPAIR_FINDINGS` (with `MAX_REPAIR_FILES` beside it), and a run that
exceeds it records `repair pass saw 8 of 11 findings — 3 were never shown to
it` in the row. A bound nobody can see is indistinguishable from a fix that
was not good enough.

**A static-analysis hit on a test file is a note, not a blocker.** Analyzer
findings on test scaffolding report at `low` — visible, never blocking —
instead of being skipped one `test_id` at a time. Production paths keep the
full audit at full severity, and credential checks (B105/B106/B107) keep full
severity everywhere, because a hardcoded password in a fixture is a real leak.

**A rejection names its author.** `TaskOutcome.blocking_by_voter` records
blocking findings per voter and travels into the bench result file.

**One comparative vocabulary.** `ai_venture_studio.superlatives` holds the
shared list both claim gates compile from, with three documented carve-outs
(`cheapest test`, marketing-only `most <word>`, and `worst case/finding/
severity`) — each an ordering over our own data rather than a claim about a
product we do not control. `#1` is matched by a boundary that works.

## What stays out

- **No semantic folding in the deterministic half.** The fold is exact on
  `(voter, title)`. Clustering paraphrases stays in the LLM `semantic_merge`
  pass, which can only improve the report and never gate it.
- **The same issue from two different voters stays two findings.** Two
  independent reviewers agreeing is signal, not duplication.
- **Test-file findings are downgraded, not dropped.** A real issue in a
  fixture stays in the report.
- **The repair cap stays.** The fix prompt must fit one completion; the
  change is that the cap is named and its overflow is reported.

## Mechanism

`tests/test_one_issue_one_finding.py` reconstructs run 13's exact shape — the
same finding at nine paths — and pins that it produces one blocking finding,
that the count lands inside `MAX_REPAIR_FINDINGS`, that folding keeps the
worst severity, that two issues in one file and one issue from two voters
both stay two findings, that the repair prompt contains all nine paths, that
an over-cap run says what it dropped, and that a rejection names its voters.

`tests/test_tools.py` pins the class rule with B310 *and* B306 together —
the check that recurred under a new id after the first was named — plus the
production-severity and credential carve-outs.

`tests/test_editions_platform.py` pins `#1` as a live alternative in both
gates, the shared vocabulary across both, and the three carve-outs with
their ranking senses still failing.
