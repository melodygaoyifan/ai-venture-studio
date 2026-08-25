# AI Venture Studio (`avs`)

**AI Venture Studio is an open-source multi-agent system that takes one
plain-language requirements document and plans, builds, tests, reviews, and
ships the product — with a human approving every irreversible step.** It also
runs the loop around the loop: it finds what to build from your real user
signals, sizes it honestly, writes the PRD with kill criteria, measures
whether it worked — and forces the kill decision when it didn't.

[![CI](https://github.com/melodygaoyifan/ai-venture-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/melodygaoyifan/ai-venture-studio/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ai-venture-studio)](https://pypi.org/project/ai-venture-studio/)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Try it in two minutes, no API key:

```bash
uvx --from ai-venture-studio avs replay --demo
```

That replays a real review of this repo's own code from its vendored audit
trail, offline — including the run where the pipeline escalated to a human
instead of guessing.

> **This README cannot overclaim.** Every quantitative claim in it is
> machine-checked in CI against [claims/platform.yaml](claims/platform.yaml)
> — a number that was not measured, or a superlative about anyone else,
> fails the build (ADR-U29).

## Pick your door

One spine, three editions — narrowing presets over the same unchanged
pipeline, never forks, never fewer checks (`edition_lint` refuses anything
that widens):

- **[Solo founder / OPC 一人公司](editions/solo/START-HERE.md)** — built
  around your attention: one product bet, a 45-minute weekly review with the
  kill criteria first, safe publishing defaults. FDRs in English or Chinese.
- **[Engineer](editions/engineer/START-HERE.md)** — fixture-gated extension
  points; nothing unfixtured registers, and you're invited to try to break
  that.
- **[Enterprise / traditional industry](editions/enterprise/START-HERE.md)**
  — ships the procurement pack and the pilot-to-production contract; init
  refuses without a named gate owner.

## For founders (no technical background needed)

Three steps to the browser UI:

```bash
pip install ai-venture-studio      # 1. install — `avs` appears on your PATH
export ANTHROPIC_API_KEY=...       # 2. the key that powers the build
avs studio myteam --profile web    # 3. serve the Studio for a new workspace
```

Then open **http://127.0.0.1:8433**. The Studio is localhost-only: no
account, no signup, nothing leaves your machine except the model API calls.
Profiles: `web` | `miniprogram` | `app`. Returning later is just
`avs studio` from inside the folder. (Wrong Python environment? `uvx --from
ai-venture-studio avs studio myteam --profile web` sidesteps it.)

<img src="docs/media/studio-flow-v070.gif" alt="The founder flow, recorded from one real run: the single open prompt, the SAID and GUESS rows the system took from one paragraph, the plan returned for confirmation with its NOT-building list, live per-module build state with an elapsed clock, and the plain-language report of the finished product" width="760">

*One real run, unedited — a live provider, no mock, nothing composited. One
paragraph in; the system shows what it took from it and asks only about the
gaps; a plan comes back for confirmation with what it will **not** build
called out; modules build with real per-module state and an honest clock and
no invented percentage; the report says in your own words what works. That
run finished 6 of 6 — one module failed its tests and the run's own retry
pass rebuilt it, which is why the report notes where the reviewer still
wants a closer look rather than claiming a clean sweep. Every screen here is
the product as shipped, and driving this one run found six defects that a
green hermetic suite had not — the plan came back in Chinese for an
English founder, the wait claimed "$0.00" while it was spending, three
modules claimed to be building at once, and the finished product was
reported as "2 of 6". Fixed, each with the test that would have caught it
([single screen](docs/media/studio-en-v070.png) · [Chinese UI](docs/media/studio-zh-v070.png)).*

English is the default (`--lang en`); `--lang zh` gives the original bilingual UI for
微信小程序 founders. The page
header switches the view — **Founder**, **Engineer**, **Enterprise** — and
deeper views only add read-only detail.

Your entire input is an **FDR** — and you do not have to write one. Say what
you want in a paragraph and the system reads it back to you: what it took
verbatim, and what it is guessing. A guess never enters the document until
you confirm it. This is the whole of what produced the run above:

```text
A shared task list for the two of us running a small studio. Right now we
track work in chat messages and keep losing track of what is actually
finished. Anyone should be able to add a task with a title and who it is
for, mark a task done, and look at the open tasks and the finished ones
separately, newest first. No logins, it is just us.
```

The six sections it filled from that — who it is for, what people do,
must-haves, not-in-v1, constraints, what success looks like — are still the
document that gets built, and [`/?form=1`](RUNBOOK.md) still opens it to
write or edit by hand.

- **If your FDR is unclear, the system asks — it never guesses** (at most
  5 questions a non-technical person can answer, in your language).
- **One FDR = one thing.** The first FDR is the smallest usable product;
  every later feature is its own small FDR. Small builds are more accurate
  and fail more debuggably.
- **You do not have to do that splitting yourself.** Describe the product in
  one paragraph and `avs roadmap` proposes the small steps in the order to
  build them; each run hands you the next one as a file, and re-checks the
  rest against what your product already promises — so a step you have
  since got another way is marked done instead of being built twice.
- **What you said NOT to build is remembered.** The "not needed for now"
  section you already fill in becomes a list every plan is shown, so the
  tenth feature does not quietly grow the checkout you ruled out in the
  first one. Change your mind by asking for it — the newest request wins.
- **`avs requirements` shows what your product promises**, grouped, each with
  the test that checks it, and what changed since the last checkpoint.
- **If you ask for something your product already does, it tells you instead
  of building it again** — naming the promise and the test that already
  proves it. If your new request contradicts something you asked for
  earlier, it stops and shows you the one command that replaces the old
  rule, rather than quietly leaving your product promising both.
- **You confirm intent in plain language before anything is built**, and get
  a build report in your language after — including every automated approval
  the machine made on your behalf.
- **You can see what it is doing while it does it.** Each module narrates its
  own steps as they start — working out how to build it, writing the code
  (attempt 2 of 3), running your tests, checking that it actually starts up —
  in the terminal and in the Studio. No percentages and no ETA: the system
  does not know whether attempt 2 will be the last one, and a made-up number
  is worse than an honest step.
- **A module that fails says why.** Not "the build gate failed" but the test
  that failed, or that the model's answer was cut off and the task needs
  splitting. Every attempt is kept for inspection.
- Real persistence out of the box (local SQLite; Supabase and WeChat Cloud
  微信云开发 are guided options with credentials in a vault that never
  enters prompts).

The same flow runs in the terminal: `avs create` → `avs preview` →
`avs add` → `avs ship` ([full founder walkthrough](RUNBOOK.md)).

## What happens when you press build?

Fourteen gated stages across two loops, and every generative stage runs the
same template: one writer, deterministic tools first, independent charter
voters (each fixture-gated at 8 cases, ≥87.5% to register), a fresh verify
pass per finding, a leader synthesis, and a gate — human wherever judgment
is the point. Nothing auto-merges, nothing deploys autonomously, nothing
publishes, nothing spends.

The full design is public — fourteen documents, cross-referenced to shipped
code in the [implementation map](docs/implementation-map.md) (open items are
named, never silent): [autoproduct-design](https://github.com/melodygaoyifan/autoproduct-design).

## A real run (unedited)

Real support tickets in, an evidence-gated product decision out. Signals,
verbatim from the tracker:

```text
s1  "I started a build and stared at the terminal for 40 minutes with
     no idea whether it was progressing or stuck"
s6  "how much will a typical month of builds cost me? I'm scared to
     leave autopilot running"
```

`avs opportunity` → `market` → `prd` → `evidence`. Every artifact below is
unedited pipeline output from one real-provider run:

![The outer loop, one real run — condensed transcript; every number and quote is unedited pipeline output](docs/media/opportunity-run.svg)

1. **P0 turns the signals into grounded candidates** (Gate PL0) — every
   claim cites its ticket verbatim, each carries a falsifiable hypothesis
   and a *named cheapest test* ("ship a clickable mockup to the 3
   reporters", not "build an MVP").
2. **P1's own voters attack the market case** — the Sizing seat caught an
   ungrounded 0.15 affected-fraction inference; the dedicated
   Disconfirmation seat argues the other side of the same evidence. The
   deterministic gate had already blocked an earlier draft outright: 75% of
   its claims were `model_inference` against the 30% market ceiling —
   reasoning dressed as research doesn't pass.
3. **P2 writes a PRD with its own death spelled out** (Gate PL2) — kill
   criteria authored before anyone is attached, sibling candidates listed
   as non-goals by name, and a Planning task auto-generated for the metric
   nobody had instrumented yet.
4. **P4 reads the cohort and refuses to flatter it:**

```text
build_progress_view_rate: 0.240   n=250   CI [0.191, 0.297]   window complete
verdict H-1: insufficient_evidence — the interval brushes the 30% kill
threshold; the honest output is the n it would take to know, not a win.
```

## What has been measured?

Numbers below link to their evidence; the CI-enforced ledger entry for each
is in [claims/platform.yaml](claims/platform.yaml).

- **Review benchmark** (`avs bench`): recall 100%, precision 67% on 13
  labeled cases against bars of 40% and 50%
  ([cases](benchmarks/cases), [method](docs/benchmark.md)).
- **Product benchmark** (`avs product-bench`): full FDR→product runs scored
  by *independent* behavioral probes executed against the built product
  ([WebGen-Bench](https://arxiv.org/abs/2505.03733) pattern), reported
  unaveraged across synthetic and real case sets. Synthetic cases
  (2026-07-23, n=1, claude-opus-4-8 writer): build 100%, probe pass 83.3%,
  clean review 100%. Real cases, run 5 (2026-07-26, n=1, pre-fix baseline):
  build 33%, probe pass 0%, clean review 17% — **published because a
  benchmark you can only pass is marketing; one you can fail in public is
  evidence** ([full run history](benchmarks/results/HISTORY.md)).
- **Perf-lane calibration**: 5 of 5 seeded defects caught (catch rate 100%)
  at the 3x relative-detection factor, loopback low-parity environment,
  2026-07-26 ([manifest](benchmarks/perf_seeded/calibration.yaml)).
- **2556 hermetic tests** (`uv run pytest`, no network, no keys); every PR
  in this repo was reviewed by avs itself, and five of those reviews caught
  real bugs.
- **A hermetic suite is not enough, and this repo says so.** Twelve real
  defects were found in one day of running the product against one real
  requirements document, with every test green — two of them at the model-SDK
  boundary, where a mock is authored by the same person holding the same
  wrong belief. `avs smoke` makes four real calls per configured provider
  and is step 0 of every release ([runbook](RUNBOOK.md#releasing-to-pypi)).

## How does it compare?

Orchestration SDKs and app builders are complements at a different layer —
you can keep their mental models and still adopt this repo's lifecycle,
gates, and evidence discipline. No scores, no adjectives; just the layer
split:

| | Layer | Opinion held | Opinion delegated |
|---|---|---|---|
| Orchestration SDKs (LangGraph, CrewAI, agent SDKs) | build agents | how agents compose and communicate | what your team's lifecycle, gates, and evidence standards are |
| Chat-to-app builders | generate an app from a conversation | speed from prompt to running code | what happens after: review depth, launch honesty, the kill decision |
| **AI Venture Studio** | run a product lifecycle | which decisions need a human, which claims need evidence, and when a product must die | which model families do the judging (swappable seats) |

## Limitations

- The outer loop runs end-to-end and survived its first real-provider
  smoke, but its release bar is honest: it is unproven until a real Gate
  PL5 records a real kill or pivot on a live cycle.
- Cloud services are guided, not auto-provisioned; deploys generate
  artifacts + instructions, and the button stays yours until you arm a
  policy that says otherwise ([ADR-031](docs/adr/031-policy-armed-automation.md)
  — disarmed by default, attributed, expiring).
- 小程序 runtime verification (`avs mp-runtime`) needs the WeChat DevTools
  desktop app plus a one-time human toggle (Settings → Security → Service
  Port), so it runs on macOS/Windows and **never in CI**; every missing
  precondition is a visible skip naming its remedy, never a silent pass.
  Page-level unit testing still needs `miniprogram-simulate`; pure-logic
  modules are gated via `node --test`.
- Single-machine operation; crash recovery resumes reviews, deploy reviews,
  and incidents from their checkpoints, but multi-instance supervision
  remains the documented upgrade path.
- Multi-tenant isolation is filesystem-and-routing level, not OS-level
  ([ADR-030](docs/adr/030-multi-tenant-server.md)); if that is in your
  threat model, run one process per tenant.

## For developers

```bash
pip install ai-venture-studio   # the command is `avs` (`autoproduct` kept as an alias)
```

The repo ships no keys, no proxy, and no metered backend: every provider
call bills the keys in *your* environment, and every provider errors loudly
if its key is missing rather than running half-armed. `OPENAI_API_KEY` is
optional but recommended — it puts a different model family in the security
voter seat, breaking same-family self-preference when Claude reviews
Claude-written code. Setup, env vars, and operations: [RUNBOOK.md](RUNBOOK.md).

<details>
<summary><b>The CLI at a glance</b> (every stage is one command; full table in the RUNBOOK)</summary>

| | |
|---|---|
| `opportunity` · `market` · `prd` · `evidence` (+ `*-approve`) | the outer loop as one-command stages; human decisions recorded at gates PL1/PL2 |
| `discover / plan / spec / build` (+ `*-approve`) | inner-loop upstream stages, gates U1–U4; `scr` is the only legal way to change a built spec |
| `review` · `resume` · `replay` · `recover` | the review pipeline, HITL, audit trail, crash recovery |
| `deploy-review` · `triage [--fix]` | deploy gate and production maintenance |
| `bench` · `product-bench` · `voter-gate` · `compound --pr` | the benchmarks, voter registration gates, and the weekly compounding loop |
| `automerge` · `deploy-execute` | exist but stay disarmed until a human writes an attributed, expiring policy ([ADR-031](docs/adr/031-policy-armed-automation.md)) |
| `readiness` · `attest` · `cab-package` · `sweep` | the enterprise adoption surface: substrate ladder, attestation ledger, change control, the janitor |
| `cadence [--install --arm] [--notify] [--only L] [--label X]` | the recurring loops' watchdog and trigger. Reports which of `compound`/`sweep`/`bench` is overdue, and `--install` writes a daily macOS LaunchAgent that runs the due ones. Exits 3 when something needs doing, so it can gate a script. A loop that never ran reads as *never run*, never as fresh; a loop that ran on time over an empty window is reported as such rather than counted green; and a scheduler still running an older build than the one you released is a finding with the exact upgrade line, because publishing does not reach the machine. `--notify` posts the alert to a Discord webhook instead of leaving it in a log nobody opens: only when a person is actually needed, at most once per week for the same unchanged alert, and carrying the command to paste rather than the diagnosis. `--set-webhook <url>` is the whole setup — stored `0600`, found by the daily run with no environment at all (the URL is a credential, so it is never echoed and never written into the world-readable plist). `bench` watches the series the launch PRD's only kill criterion reads, and is tracked only in a checkout that has the cases to run it; `--only`/`--label` give that checkout its own agent without retargeting the product workspace's |
| `mp-runtime` | opens a built 小程序 in WeChat DevTools, visits every registered page and screenshots it — the blank-page check the static gate cannot make ([pipeline guide](docs/miniprogram-pipeline.md)) |
| `reconcile [--scan DIR] [--apply]` | one-time repair for workspaces built before v0.70: restores `built` flags lost to a rollback, so a resumed run does not rebuild and re-bill committed modules. Reports by default; repairs only where outcomes.yaml and the commit log agree, and leaves a superseded spec from a re-plan alone |

</details>

## FAQ

### Can it build an app from a plain-English description?

Yes — that is the founder flow: one FDR (six questions in your own words,
English or Chinese) goes in; a planned, built, tested, and reviewed product
comes out, with your plain-language confirmation gate before any build
starts. The product benchmark exercises exactly this path end to end.

### Do I need to know how to code?

No. The Studio UI asks for your product description in your own words,
asks clarifying questions when it is unclear, and reports back in plain
language — including an acceptance walkthrough you can click through. You
never have to read the code it writes (it's yours, though).

### What does it refuse to do autonomously?

Merge to main, deploy to production, publish, send, spend money, fabricate
user evidence, or close a fired kill criterion. Each of these requires a
recorded human decision; merge/deploy automation exists but is disarmed
until a human writes an attributed, expiring policy naming exact branches.

### Does my data leave my machine?

Only model API calls, billed to your own keys. There is no account, no
proxy, and no metered backend; telemetry is off by default and aggregate-only
if you opt in (`avs telemetry show` prints the exact payload before anything
sends). Credentials live in a vault layer that never enters prompts.

### What happens when a build fails?

Passing its own tests does not get a module past the build gate: a web
product must actually start and listen on its port, and a 小程序 must be
openable in WeChat DevTools — app.json present, every page registered, and
every relative `require` chain resolving to a file inside the mini-program
root, because a module that throws at require time is a page that renders
blank. Both checks exist because real runs produced green suites over
products that could not load.

For 小程序 there is a rung past that, on your own machine: `avs mp-runtime`
opens the built product in WeChat DevTools, visits every registered page and
**screenshots it**. A page counts as broken when its screenshot is a single
flat colour — because "the page opened without throwing" is not evidence: a
page whose JS died before `Page()` still opens, still sits on the page
stack, and still renders pure white. That check exists because a build was
reported as seven pages rendered while three of them were blank. The
[pipeline guide](docs/miniprogram-pipeline.md) has the four rungs and the
failure that justified each.

The run retries its own mechanical failures first: one bounded pass, in
dependency order, with the previous attempt's diagnosis handed to the writer
— so a retry is a different attempt, not a replay. What still fails is
preserved for post-mortem, the rest of the product keeps working, and the
Studio offers per-module retry — an interrupted build resumes from what is
already built instead of re-paying it. Review verdicts and gate records stay
on disk as YAML you can replay.

### Can it run up a surprise bill?

Signal s6, verbatim: *"how much will a typical month of builds cost me? I'm
scared to leave autopilot running."* The answer is visibility plus your
provider's own controls. Every call is billed to **your** key or
subscription — the framework holds nobody's keys and never spends money on
your behalf — so spending limits belong where the billing actually happens:
your provider account, whose limits see all usage on the key and cannot be
bypassed by anything here. What the framework owes you is the number, and
it delivers it everywhere money is decided: every build report ends with
what the run cost, as arithmetic; `avs cost` prints the month per model;
the Studio shows spend on the confirm page, before the first dollar.
Every command records what it spent to the workspace ledger — *every*
command, in one place rather than as a rule each new one's author has to
already know. That is not decoration: `compound` reached a provider daily
on a scheduler while writing nothing to the ledger, and the run that costs
money without appearing in the number is the one that produces the
surprise this question is about.
Prices are published list prices with a source and a date (`avs prices`),
ranges resolved upward so the estimate is a ceiling, and a model with no
sourced price keeps the total honestly labelled a floor — never counted as
zero. There is deliberately no framework-side spending cap
([ADR-032](docs/adr/032-no-framework-spending-cap.md)): it would duplicate
your provider's control and mislead subscription users whose tokens don't
map to marginal dollars.

### How is this different from an orchestration SDK like LangGraph or CrewAI?

Different layer: SDKs give you primitives to build agents; this is an
opinionated product lifecycle that happens to be run by agents — with the
opinions (gates, evidence rules, kill criteria) enforced by deterministic
code, and the judging seats swappable across model families. See
[How does it compare?](#how-does-it-compare) above.

## Status

Current release: see the [PyPI badge](https://pypi.org/project/ai-venture-studio/)
and [CHANGELOG.md](CHANGELOG.md); the release-by-release record back to
v0.8 is in [docs/release-history.md](docs/release-history.md). Next
milestone: the v3.0.0 design gate — the launch PRD's kill criterion fires
on the product-bench series, and a human records the decision
([runbook](docs/v3-live-loop.md)). The second axis, which needed four
consecutive weeks of hand-logged maintenance hours, was **withdrawn** in
v0.81.0 ([ADR-033](docs/adr/033-withdraw-weekly-attention-axis.md)): three
weeks after launch its log held zero logged hours, so what it measured was
willingness to answer a weekly prompt. The axis that remains reads a series
the weekly run already writes, and can fire without asking anyone anything —
provided the run keeps happening. It had stopped for sixteen days without a
word, so in v0.82.0 that series became a watched loop of its own
([ADR-034](docs/adr/034-the-bench-is-a-watched-loop.md)). `avs cadence`
keeps that clock honest, every loop it drives can close itself, and a
criterion reading a dead series is now a Discord message rather than a
standing "not fired". The first scheduled run then showed the other half of
the same problem: a case killed by a hung subprocess was averaged into the
rates as `0.0`, so an infrastructure crash was two bad weeks from firing a
*capability* verdict. Since v0.83.0 a rate averages only over cases that
produced its denominator, the denominator travels with the number, and a
run that could not measure a case exits 3
([ADR-035](docs/adr/035-an-unmeasured-case-is-not-a-zero.md)). Run 13, the
first run under those rules, scored **build 94% · probes 92% · clean 75%**
with all four cases measured — and turned up the same defect one level
smaller: a probe that got `Connection refused` had reached nothing at all,
and was scored against the product anyway. v0.84.0 gives every probe its
own port and makes readiness require an answer rather than an open socket.
Run 14 then scored **build 100% · probes 100% · clean 38%** — both rates up
from run 13's 94% and 92%, and clean review down from its 75%, with two of
four cases scoring zero clean reviews and every rejection carrying an empty
reason. That reads as a quality regression and was not one. The leader
blocked a verdict on `{critical, high, medium}` while the repair pass
selected fixes with its own hard-coded `("critical", "high")`, so a task
whose worst finding was medium — the modal severity the voters raise — was
rejected, never repaired, and could never clear: **unclean by construction**.
Neither release had touched the leader or any voter, so the defect was
constant across both runs and only the exposure varied. 38% was not a
regression to hunt, and **75% was never as solid as it read**
([ADR-037](docs/adr/037-block-and-repair-are-one-threshold.md)). Four times
now the harness has been charged to the product, and all four times it was
first read as a product defect — the fourth one level up again, charged to
the reviewer rather than the code it was reviewing. Reading run 13's
preserved review artifacts rather than reasoning about them turned up the
rest: the build stage had copied one line of test boilerplate into nine
files, a static analyzer raised the same finding at each, and the leader —
whose dedupe key was keyed on *location* — kept all nine. One issue, nine
blocking findings, and more of them than the repair pass's own cap, so eight
were fixed, the ninth survived **by construction**, and the re-review
rejected the task again — unclearable no matter how good the fix was. Two
runs earlier the same shape had appeared under a different analyzer check
and been patched by naming that check. Since v0.89.0 one issue is one
finding however many files it appears in, a bound that drops work says so in
the row, an analyzer finding on a test file is a note rather than a blocker,
and a rejection records which voter made it
([ADR-039](docs/adr/039-one-issue-is-one-finding.md)). Run 15, the first run
with the thresholds joined, is the number that supersedes both.

Run 16 recorded **build 100% · probes 75% · clean 31% over 3 of 4** — and
that headline was the harness charged to the product for a fifth time,
this time in the arithmetic. One case ran for six minutes, came back with
no tasks, and was dropped from the build rate while its two probes, run
against a workspace with no product in it, were averaged in as zeros:
exclusion was decided per *rate* instead of per *case*, so one summary
excluded a case from two rates and counted it in the third. The exclusion
itself was the error — ADR-035 already said "a case that ran and built
nothing still scores a real 0.0" and the code read zero tasks as no
denominator. **Read honestly the run is build 75% · probes 75% · clean 31%
over 4 of 4**, and 100% was the rate over the cases that got a plan, not
over the cases that were asked for a product. Since v0.94.0 measured is one
decision per case that every rate reads, a blocked plan carries its
reason — the failing case's was `unparseable planner output`, sitting
unread in `product/plan.yaml`, and the parser's message had been dropped
from the revision prompt so both retries were asked to fix a break they
were never shown — and a rejection caused by reviewers that returned no
verdict says so instead of naming the low-severity findings that did not
cause it
([ADR-043](docs/adr/043-a-case-is-measured-or-it-is-not.md)). The build
rate's comparability breaks here; run 17 is the first number on the new
denominator.

**Run 16 is the newest reading, and it is not a reading of this build.** It
ran on v0.93.0. Everything since — ADR-044 through ADR-053 — is unmeasured
by this series: run 17 fired early on 2026-08-17, measured one case, and
lost the account to credit exhaustion mid-way through the second
([ADR-052](docs/adr/052-a-measurement-is-bought-once.md) is what came of
that). So the rates above are true and they are stale, and the two are not
the same complaint. Every bench claim in `claims/platform.yaml` now names
the build that produced it, so the gap is visible from the claim rather
than only from the result file.

---

MIT · design docs: [autoproduct-design](https://github.com/melodygaoyifan/autoproduct-design) · operations: [RUNBOOK.md](RUNBOOK.md) · security: [SECURITY.md](SECURITY.md)
