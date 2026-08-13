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

## Format

Each record states: context, the decision, what it reverses and why that
reversal is defensible, what stays out, and the mechanism that keeps the
new capability bounded. A record without a *mechanism* section is an
opinion, not a decision.
