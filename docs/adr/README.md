# Architecture Decision Records

The design canon (docs 08–29) carries ADR-001…ADR-U37 inline. This
directory holds the records made *in the implementation* after the canon
was written — most importantly the ones that **reverse a previously
recorded non-goal**, because a scope reversal that lives only in a commit
message is indistinguishable from scope creep.

The change-control protocol (§10 Part 11) applies: the newest accepted
decision wins and must be recorded. These files are that record.

| ADR | Decision | Reverses |
|---|---|---|
| [029](029-mcp-transport-partial.md) | MCP is the real transport for the L0 read-only tool surface; L1/L2 stay in-process | narrows the "in-process by ADR'd mapping" compromise |
| [030](030-multi-tenant-server.md) | One `serve` process may front several isolated workspaces | "Multi-tenant SaaS" (README out-of-scope) — the server half only |
| [031](031-policy-armed-automation.md) | Merge and deploy execution become possible, but only when a human arms a policy file | "Auto-merge to main. Auto-deploy to production." (README out-of-scope, §08.1.8) |
| [032](032-no-framework-spending-cap.md) | Spend is measured and reported, never gated — budget limits live at the provider that does the billing | the monthly spending cap (v0.65.0–v0.66.0) |
| [033](033-withdraw-weekly-attention-axis.md) | The weekly maintenance-hours kill axis is withdrawn; no scheduled loop asks a human for a number, and a non-zero exit is now always a failure | PRD outcome O-L1 and its kill criterion (v0.50.0–v0.80.0), and the alert path's `attention` exemption |
| [034](034-the-bench-is-a-watched-loop.md) | The product-bench series becomes a third cadence loop with a per-loop timeout, and leaves cron for launchd | "the series is already collected weekly" — a claim about a scheduler nobody watched |
| [035](035-an-unmeasured-case-is-not-a-zero.md) | A rate averages only over cases that produced its denominator, the denominator travels into the series and the alert, and a run that could not measure a case exits 3 | averaging a crashed case in as `0.0`; the probe frame's discarded 4xx body; and part of `2bb4808`'s reasoning |
| [036](036-a-hang-must-describe-itself.md) | A timeout carries what the process printed, kills the whole process group, and keeps the crashed case's workspace; a module that blocks on import is rejected from a parse, in every profile (scope corrected in v0.86.0) | nothing — it answers ADR-035's open bullet |
| [038](038-the-thresholds-that-must-differ.md) | `ROLLBACK_SEVERITIES` is named and pinned as a strict subset of the set that prompts a fix, "clean" gets one definition derived from the `Verdict` enum, and the founder tally stops reading a rejection as an approval | nothing — it completes ADR-037 by fixing the *class* rather than the instance |
| [039](039-one-issue-is-one-finding.md) | The leader folds repeats of one issue across files into one finding (carrying every site), the repair cap is named and reports what it dropped, a static-analysis hit on a test file is a note rather than a blocker, a rejection records which voter made it, and both claim gates compile one shared superlative vocabulary | nothing — it fixes the class behind run 13's clean-review rate, where ADR-037 and ADR-038 fixed instances |
| [037](037-block-and-repair-are-one-threshold.md) | The repair pass filters by the leader's own `ACTIONABLE_SEVERITIES` instead of a second hard-coded list, so MEDIUM can no longer block a verdict that nothing will ever try to fix; every non-clean verdict records what it objected to, and a result file names the build that produced it | nothing — it explains run 14's 38% clean-review rate |
| [040](040-a-result-is-not-an-exit-code.md) | A loop's last *result* is alertable separately from whether it ran, a poor-but-complete run is a finding that fails nothing, the floors keep one definition, run-over-run movement is stated without inventing a threshold, and `product-bench --notify` lets any run — however started — report itself through the one delivery path | nothing — it fixes the two reasons the alert channel was silent about runs 12–15 |
| [041](041-an-empty-answer-is-not-a-verdict.md) | An empty spec is the loudest complaint in the revision loop instead of the quietest, the spec stage asks whether its response was cut off like every other writer stage does, a truncated response gets its own block reason, and the ledger records `stop_reason` on every call | nothing — it fixes the two blocked specs of run 15 and makes the next occurrence answerable from the ledger |
| [042](042-a-failure-must-arrive-as-a-fact.md) | A build failure's one-line cause is selected from pytest's own summary and assertion lines instead of head-sliced off the front of its banner art, keeping the test's name and the real comparison, with one definition of the clip | nothing — it makes run 15's third finding readable without re-running the preserved workspace |
| [043](043-a-case-is-measured-or-it-is-not.md) | Whether a bench case was measured is one per-case decision that every rate reads, a case that ran and produced nothing scores a real 0.0 instead of vanishing from the denominator, a blocked plan carries its reason into the result file and its parser's message into the revision prompt, and a rejection caused by voters that never answered says so | ADR-035's implementation, not its principle — "a case that ran and built nothing still scores a real 0.0" was already its text; the build rate's comparability breaks at run 17 |

## Format

Each record states: context, the decision, what it reverses and why that
reversal is defensible, what stays out, and the mechanism that keeps the
new capability bounded. A record without a *mechanism* section is an
opinion, not a decision.
