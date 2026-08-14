# ADR-040 — A result is not an exit code, and a run is not a scheduler

**Status:** accepted (v0.90.0)
**Reverses:** nothing — it fixes the two reasons the alert channel was silent
about work the machine had actually done.

## Context

The founder asked why the Discord channel never showed logs, bugs or errors.
It was not a configuration fault, a delivery failure or a rate limit. The
channel was working exactly as built, and what it was built to report is
narrower than anyone reading it assumed.

Two independent gaps, both verified against the real artifacts:

1. **The alert reported liveness, never results.** `build_alert` fires on
   four conditions: a loop that failed to run, a loop that is overdue, a loop
   that ran over an empty window, and a scheduler behind on version. Its own
   helper says so: *"a run that succeeded says nothing here even if its
   output was noisy."* Bench run 12 finished with a crashed case, build 75%
   and probes 65%, and `bench.log` records `bench: ran (exit 0)` followed by
   `no alert: nothing needs a person`. Run 14 took clean reviews from 75% to
   38% while builds and probes went to 100%, and said nothing either — every
   number involved was above its floor, and no floor covers clean review.
2. **Only launchd could reach the channel.** The single caller of `notify`
   in the whole codebase was `avs cadence --notify`. Runs 13, 14 and 15 were
   started by hand under `nohup`; each cost hours of wall clock and real
   money on the founder's own key; none of them could have posted anything at
   all, whatever they found. A four-hour run finished and the only record was
   a log file nobody opens.

The two halves are one rule: **anything that can need a person reaches the
person, whatever produced it and whatever started it.**

## Decision

**A loop's last result is a question the alert asks separately from whether
the loop ran.** `cadence.result_concerns` collects a sentence per loop whose
*output* needs a look, and `build_alert` takes it as `concerns` — distinct
from `_failures`, which answers whether the loop got through at all. A loop
that exited 0 can now produce an alert.

**A poor result is a FINDING, not a failure.** The concern is reported and
fails nothing: the scheduler's exit code still answers only "did the machine
break", and `product-bench` still exits 0 on a complete run whatever it
scored (ADR-035). The tempting consistency fix — failing on a low rate —
would report every weak week as a broken scheduler, and is refused here
explicitly so it is not re-proposed as a tidy-up.

**The floors have exactly one definition.** `BUILD_FLOOR` / `PROBE_FLOOR`
stay in `bench_criterion`, which grows `concern()` so a caller never needs to
compare a rate against a literal. A test asserts neither `notify` nor
`cadence` names a floor value. This is ADR-038's pattern applied before the
second copy exists rather than after it drifts.

**Movement is stated; no threshold is invented.** `bench_criterion.movement`
reports the run-over-run delta in points (`clean -37pp`) with no verdict.
There is no floor on the clean-review rate, and adding one here would be
extending the launch PRD's only kill criterion by a constant in a module —
that axis is a recorded human decision (doc 25 §76.4), not a convenience.

**Any finished bench run reports itself.** `avs product-bench --notify` posts
the result however the run ends: a completed run, a run with unmeasured cases
(named first, above the rates they distort), or a crash (`bench_failed_alert`
carrying the traceback's tail). The scheduler is no longer the only door.

**A bench run is an event, not a standing condition.** It is forced past the
7-day repeat window. That window exists so a condition that stays true is not
re-sent daily; two crashed runs in one week are two things that happened, and
dropping the second is silence of exactly the kind this record fixes.

**A clean bench run still posts.** The one deviation from "only when someone
is needed", argued rather than assumed: that rule protects against a *daily*
all-green teaching the reader to swipe. A weekly result somebody is waiting
on is the thing they need, and silence on success is the original complaint.

## What stays out

No floor on clean review. No new kill-criterion axis. No spend gate. No exit
code that reports a bad number as a broken machine. No second delivery path:
every alert goes through `notify.send`, because the second sender is where a
notifier grows a second de-duplication rule, a second webhook lookup and a
second silent failure mode.

## Mechanism

The sent-log (`.mas/alerts-sent.yaml`) is keyed by alert kind, with a
migration reading the pre-v0.90.0 flat shape as the cadence record. Without
that, the second kind would have overwritten the first's memory and each
would have re-sent the other's suppressed news — the de-duplication record is
the only thing holding the channel back from repeating itself.

`tests/test_result_alerts.py` pins the rule from both ends: a healthy loop
with an alarming result produces an alert; an empty concern produces none; a
poor-but-complete run alerts and fails nothing; the floors appear in one
module; the two kinds remember separately and the old file shape migrates;
and the crash path, the manual `--notify` flag and the forced repeat window
are each read where they live.
