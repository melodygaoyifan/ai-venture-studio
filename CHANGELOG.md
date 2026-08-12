# Changelog

SemVer over the enumerated contract surface (CONTRIBUTING.md). One entry
per release, newest first; the git tags v0.8.0–v0.27.0 predate this file
and are summarized in the README roadmap and docs/implementation-map.md.

## v0.79.0 — the alert leaves the machine

Minor. The daily LaunchAgent has been doing its job since v0.72.0: at 09:00
it runs the due loops, exits 3 when one needs a person, and writes the
reason to `~/Library/Logs/ai-venture-studio/loops.log`. Nobody opens that
file. So the machine noticed, told nothing that listens, and the week
passed — the same silent-success shape the loops exist to prevent, one
level up. Weekly maintenance that depends on the operator remembering to go
look is not maintenance; it is a reminder they have to set themselves.

`avs cadence --notify` posts the alert to a Discord webhook. Three rules
shape it, and each one is a way the notifier could be worse than the log:

- **Only when something needs a person.** No daily "all green". A channel
  that speaks every morning is a channel that gets muted, and then the one
  message that mattered is muted with it. `build_alert` returns `None` when
  nothing is stale, vacuous, or behind.
- **Not the same thing every morning.** An overdue weekly loop stays overdue
  until someone acts. The alert is identified by a hash of its **content**,
  not its date, so tomorrow's identical alert is recognised as already said
  and held for `REPEAT_DAYS`; a *changed* alert goes out immediately,
  because delaying new news would make the channel less trustworthy than
  the log it replaces.
- **Carry the command, not the diagnosis.** The reader is on a phone. Each
  line says what is true and gives them something to paste — including the
  `avs attention …` command, marked as the one no scheduler can answer, and
  the v0.78.0 empty-window cause, which has to survive the trip.

Setup is one command: `avs cadence --set-webhook <url>` validates the URL
before it touches disk (an unvalidated one would sit there looking
configured and fail once a week at 09:00, into the log this feature exists
to stop using), writes it `0600`, and never echoes it back — the terminal
scrolls, and whoever reads it can post as this app. `resolve_webhook` then
finds it with no environment at all, which is the case that matters:
launchd hands a job four variables and no login shell, so a feature
configured only by an `export` would be missed by the one run that counts.

The webhook URL is a credential: whoever holds it can post into the channel
as this app. It follows the rule the scheduler already enforces —
`AVS_DISCORD_WEBHOOK_FILE` (a pointer) travels into the plist, which is a
world-readable file in `~/Library/LaunchAgents`; `AVS_DISCORD_WEBHOOK` (the
URL itself) is refused there by name. The missing-credential check moved
from `*_FILE` to `*_KEY_FILE` in the same change: the webhook pointer also
ends in `_FILE`, so without that a workspace that can notify but cannot
authenticate would have installed clean and failed every morning.

A failed delivery is never recorded as sent — otherwise the dedupe
suppresses tomorrow's retry of an alert that never arrived, and silence is
remembered as success. And `--notify` does not change the exit code:
telling someone is not fixing it.

## v0.78.0 — an empty window says which kind of empty

Minor. `avs cadence` reported the compounding loop green over a workspace
where it had read nothing for weeks, and the only sentence it had for that
was `read 0 reviews — nothing to compound`. That names the symptom and no
cause, and the two causes are opposites: a loop pointed at a workspace
nobody ever built is a misconfiguration to fix, and a loop over a workspace
where the work paused is a loop doing its job. The founder cannot tell them
apart by looking. The machine can, and now does.

`collect_signals` counts **every** review on disk and keeps the newest
`written_at`, window or not — the distance between that stamp and the
window is the diagnosis. `render_proposal` writes it onto the artifact
(`Nothing reached this window: 9 review(s) exist, newest 2026-07-31.`, or
`no review has ever been written here.`), `cadence` reads it back into
`LoopStatus.empty_because` (`work_stopped` | `never_any` | `""`), and the
CLI turns that into the line worth acting on: *the loop is almost certainly
pointed at the wrong `--repo-dir`*, or *the loop is fine; the work is what
paused*. The age is measured from the stamp, clamped at zero so a future
date reads `0d old` rather than a negative one.

Also: `scr_raised` left `correction.py` four releases ago and stayed behind
in the results page's colour table and the CLI's — a row nothing could
reach. A dead row is not a visible bug, which is why it survived. Both
tables now key off a single `correction.STATUSES`, with tests asserting the
module constructs exactly those statuses and that neither presenter names
one that does not exist. Read off the source rather than by exercising the
paths, because the failure being guarded is a status no path reaches.

## v0.77.0 — "not that", said with one tap

Minor. A requirement change comes back as a draft: what will be different,
the acceptance criteria it would leave behind, and the assumptions — the
decisions the model had to make because the founder did not say. Those
assumptions were put on the card deliberately, so a wrong one would be
visible *before* it was built.

Then the only control under them built all of them anyway. Seeing that an
assumption is wrong and being able to say so are different things, and the
founder's entire vocabulary was one button meaning yes and a browser back
button meaning "start the complaint over from nothing". For the reader this
product is written for — assumed as lazy and as non-technical as it is
possible to be — that is not a choice, it is a dead end with a list of
reasons in it. The refinement is the part they cannot do; it is also the
part a model is good at.

So every assumption now carries **Not that**. One tap rejects that decision
and the draft is written again, with the rejection sent to the model as a
question it must answer *differently* — a redraft that keeps the same
behaviour under a new sentence is a wrong answer, and the prompt says so.
Words stay available and stay second: a tap is already a complete answer,
and asking for a sentence is the toll this whole path exists to remove.

The rejections travel on the plan, in the same hidden field the plan
already used, because the draft is never written down and the plan is the
only memory between one request and the next — without it a second redraft
could propose the assumption the founder just turned down. The complaint is
*not* re-routed: a second router call is free to classify the same words as
a repair or against another feature, and the founder would be shown a
redraft of a change they never asked for. `ChangePlan` therefore also
carries the router's `instruction`, so a redraft gets exactly the input the
first draft had, and the rejection is the only thing that differs.

Two rounds, the same bound intake puts on clarifying questions — a third is
not a conversation, it is a loop that spends a model call each time round.
The cap is enforced on the route as well as the page, so a form left open
in another tab cannot spend a call the card has stopped offering. **Make
this change** is on the card at every round including after the cap: the
limit ends the redrafting, never the change. A redraft still writes nothing
— same promise the first draft makes, so pressing "not that" can never be
the thing that commits the founder.

Contract surface: `ChangePlan` gains `rejected`, `notes`, and
`instruction`, all defaulting to empty, plus a `redrafts` count;
`draft_change` gains keyword-only `rejected` and `notes`;
`correction.MAX_REDRAFTS` and `POST /correct/redraft` are new. All
additive.

Suite 1829 hermetic tests (as measured by CI at the tag; 1824 locally,
where the five mutation-testing cases skip for want of `mutmut`).

## v0.76.0 — what happened, said to the founder, and takeable back

Minor. Three failures that all landed on the same screen: the card a
founder reaches after asking for a change.

**It was written for the wrong reader.** The card's only line was
`CorrectionResult.detail` — `repaired in 2 attempt(s); files: src/cart.py`,
or `repair still broke the suite after 3 attempt(s) (…); workspace
reverted`. Those are notes for whoever reads the log. The person this
product exists for is assumed non-technical on purpose, and they were
handed the implementer's notes and left to work out the only two things
they actually wanted to know: did my product change, and what do I do now.
A sentence written for a log cannot be translated into founder language
after the fact — so `CorrectionResult` now carries a `reason` from a closed
vocabulary (`REASONS`), one member per way out of `run_correction`, and the
Studio says it in the founder's own language. A test asserts every member
has a string in both languages, and that every outcome which changed
nothing says so out loud — "it failed" and "it failed and your product is
untouched" are very different news to someone who cannot go and look.
`detail` is not deleted: it moves one fold down, under **Technical
detail**, because when a founder does ask someone technical for help it is
exactly what that person needs.

**A repair could not be undone.** Builds and feature additions were tagged
as checkpoints. Repairs were bare commits — so the one kind of change a
founder makes when something is *already* wrong, the change most likely to
need reversing, was the only one the undo list could not see. A repair now
tags a checkpoint like every other change, and the result card offers to go
back beside the thing it is reporting, carrying the same honest note about
how many later changes go with it. The change list at the bottom of the
home page is where a founder looks a week later; the moment they want the
undo is the moment they read what was done, and sending them off to hunt
for the right row is how a reversible change stops being reversible in
practice. One renderer serves both, so the sentence about the cost cannot
quietly go missing from one of them.

**The page you wait on looked exactly like a hung one.** It reloads every
four seconds through the minutes a model call takes, and said the identical
thing each time — the only evidence anything was happening was that nothing
had changed, which is also what a dead worker looks like. It now shows how
long it has been running. A fact, not a progress bar: an omitted clock is
honest, an invented one is not, which is the rule `_elapsed_hms` already
followed for builds.

Contract surface: `CorrectionResult` gains `reason` and `checkpoint`, both
defaulting to empty; `correction.REASONS` is new. All additive.

Suite 1809 hermetic tests (as measured by CI at the tag; 1804 locally,
where the five mutation-testing cases skip for want of `mutmut`).

## v0.75.0 — something to point at, instead of something to remember

Minor. v0.74.0 made "actually, make it X" real. This is about everything a
founder had to do *before* that sentence, all of which was recall.

**The home page listed the wrong features.** Under `Features` it rendered
`product/features/*` — the directories written by post-build *changes* — as
bare directory slugs. Everything the product was actually built from
appeared nowhere on the page. A founder who built six features and had
changed none of them saw an empty section describing a product with six
features, and then had to describe, from memory, a feature the Studio had
never shown them a name for. There is now a card per built feature, read
from `built_specs` — the correction router's own list, deliberately, because
two independent readers of `specs/` would eventually disagree and the
visible failure would be a card offering a change the router then refuses to
route. Each card carries what that feature does, and a `Change this` box.
The old list keeps its information under an honest heading, `Recent
changes`.

**A criterion is shown as a sentence.** EARS is a grammar for writing
requirements that cannot be misread by the machine that builds against
them; it is not a sentence anyone wants to read. `The system shall keep the
cart after the browser is closed` renders as `Keep the cart after the
browser is closed.`, and `When an order is placed, the system shall email a
receipt` as `When an order is placed: Email a receipt.` Display only —
`criteria` is inside `contract_hash` and is what the build is checked
against, so a helper that rewrote it would be an unratified spec edit
dressed as a stylesheet. A line that is not EARS at all is shown exactly as
written rather than guessed at.

**Pressing `Change this` scopes the complaint without deciding it.** The
card sends `spec_slug`, and the router is constrained to that one feature
instead of inferring one from the founder's words. It does *not* skip the
router: which feature is a fact the founder pointed at, but whether the
product is broken or the requirement moved is a judgment they did not
express by pressing a button, and forcing a `kind` here would silently
answer the one question nobody asked. A slug naming something unbuilt is
refused loudly rather than falling back to the full list — falling back
would turn the card's only guarantee back into a guess.

**Marking an acceptance row wrong now needs no typing.** `Wrong` used to
open an empty box and wait for the founder to write out, in prose, the thing
the row already said. One tap now sends it, recorded as *this one is not
right: <the row>* — a report of a failure, which is what the tap means,
rather than the bare row text, which would be filed on the SCR as a
requirement. The box is still there, second, for anyone who wants to add
words. Where a row matches a feature's criterion word for word the tap also
carries that feature's slug; where it matches nothing — the common case,
since the acceptance walkthrough is written by a model in plain language —
it carries none and the router decides as before. `criterion_owners` only
ever returns exact, whitespace-normalised matches owned by exactly one
feature, because a *wrong* pre-scope is worse than none: it skips the router
precisely when the router was needed.

**The composer offers two choices, because it has two forms.** It had three
tabs. `Something wrong?` and `Is it broken?` posted to the same `/correct`
form and the router reads the words, never the tab — so the founder made a
distinction the backend threw away, and paid a decision for it.

Contract surface: `/correct` accepts an optional `spec_slug` field (absent
behaves exactly as before); `route_complaint` gains `only_slug`;
`_built_specs` is now public `built_specs`, and `criterion_owners` is new.
All additive.

Suite 1791 hermetic tests (as measured by CI at the tag; 1786 locally,
where the five mutation-testing cases skip for want of `mutmut`).

## v0.74.0 — "actually, make it X" is a button now, not a sentence

Minor. A founder tells the Studio their requirement has changed. Until this
release the whole path ended at a line of text:

> re-run `avs add`/`spec` for 'cart' to regenerate

printed in a browser, to someone who has never opened a terminal, under a
green tick reporting that their complaint had been handled. Nothing was
built. Nothing was scheduled. The spec still described the product they had
just changed away from.

**The change is now drafted, then built.** `draft_change` reads the spec and
returns a plan — a plain-language summary, the complete post-change
acceptance criteria, and the assumptions made on the founder's behalf. The
assumptions are on the card rather than in the report afterwards, because a
wrong one is cheap to catch before a build and expensive to catch inside a
product. Drafting is pure: it writes nothing and raises nothing, so a
founder who reads it and closes the tab leaves the workspace exactly as they
found it.

That last property is a fix, not a nicety. The old path raised **and
approved** the SCR at classification time — before the founder had agreed to
anything. Somebody who read the plan and decided against it left an
approved, unconsumed grant behind: a banked free pass to edit a frozen spec,
earned by a person who had chosen not to change anything. `apply_change`
now does the raising, and the founder's press is what calls it.

**The amendment is parked, not applied.** The Studio confirms in one process
and builds in a detached subprocess minutes later, so there is no moment at
which "the founder agreed" and "the build succeeded" are both true and in
the same process. The criteria are written to `.mas/pending-amendment.yaml`,
bound to the FDR by a sha256 of its whitespace-normalised text so a record
left by an abandoned change cannot attach itself to whatever gets built
next, and `run_feature` spends it only when **every** task built. A spec
promising behaviour that failed to build would be worse than the stale spec
this whole path exists to fix: it is the one file `avs status` and the
acceptance page treat as the truth.

**Amending re-stamps `approved_hash`.** `criteria` is inside
`contract_hash`, and `_approval_drift` refuses to build any spec whose
contract no longer matches its stamp. An amendment that changed the criteria
without re-stamping would make the founder's own approved change look
exactly like someone editing a frozen spec behind the SCR channel's back,
and §13.35.5 would refuse every later build of that slug — permanently.
Re-stamping is what ratification means. `test_skeletons` are deliberately
untouched: they belong to the build that authored the test files now on
disk, and the feature build has already written its own.

Two smaller rules that the tests pin: the SCR records `plan.words` — the
founder's own sentence — and never `plan.summary`, because a paraphrase
filed as the authorization is a record of a decision nobody made (ADR-U02);
and an empty criteria list is refused rather than ratified, since a spec
that promises nothing passes every gate that follows it.

**The other half: the evidence is refreshed after a change.** This would not
have been noticed until the first half worked, which is why it ships with
it. `_post_build_artifacts` wrote six artifacts; the feature path
hand-inlined two of them. Screenshots and `VERIFICATION.md` came only from
the first build — so a founder would change the cart, press "what does it
look like", and be shown the old cart under a green success message. While a
change was a no-op that staleness was accidentally honest.

`refresh_evidence` is split out and called by both paths. What is *not* in
it is deliberate: `outcomes.yaml` stays the first build's per-module record
(`persist=False`), because rewriting it with a feature's two tasks would
erase the main build's failures and turn the home page green. Evidence is
best-effort by contract — it never fails a build that succeeded.

Capture itself needed three fixes to be worth re-running:

- **It photographs the product, not its front door.** `capture` was called
  with the default `["/"]`, so a five-page product got one picture of the
  home page — and after a change that touched checkout, the one picture was
  of the page that had not moved. `discover_paths` scans the product's own
  route decorators, caps at 8, and skips parameterised routes, because there
  is no id to substitute and a 404 screenshot is worse than a missing one.
- **The port is asked for, not assumed.** 8642 was survivable while capture
  ran once per workspace lifetime. It now runs after every change, where a
  leftover server — or the founder's own `avs preview` — would make every
  subsequent screenshot a picture of the wrong process.
- **Stale pictures are pruned, but never over a failure.** A page the
  founder deleted must lose its picture; an empty capture run means the
  capture failed, and deleting the last evidence of the product over a
  failure is its own bug.

And when there is nothing to photograph, the page says why. `capture` had
always written its reason to `screenshots.yaml` — playwright missing, no
runnable entry, the server never listened — and nothing read it, so the
founder saw an empty space and concluded the Studio was broken.

Contract surface: `POST /correct/change` is a new Studio route;
`.mas/pending-amendment.yaml` is a new internal file; `avs correct` gains
`--yes` and a `change_planned` status; `spec.yaml` is written by a new
caller (the SCR channel is unchanged). All additive.

Suite 1766 hermetic tests (as measured by CI at the tag; 1761 locally,
where the five mutation-testing cases skip for want of `mutmut`).

## v0.73.0 — a founder says three things and gets three answers

Minor. A founder wrote one message listing three problems with their
product. The Studio showed one classification card, repaired one feature,
and returned to the home page. The other two were not refused, not queued,
not logged — they were gone, and nothing on screen ever said so. Reporting
the same problem twice is what a founder does when the first report seems
to have been read, so the failure was also self-concealing.

It was singular in three places at once, which is why nothing caught it:

- `_ROUTER_SYSTEM` said "map the complaint to the ONE responsible feature"
  and gave the model a reply shape — `spec_slug` / `kind` / `instruction` —
  with no field a second issue could occupy. A model asked for one answer
  is not withholding the rest; it has nowhere to put them.
- `route_complaint` validated that the one returned slug exists. Nothing
  compared the routed issue against the message it came from, so leftover
  problems were not a state the system could be in.
- The repair stage collected `_related_sources` for the routed spec only,
  so even a router that had somehow named three features would have handed
  the implementer the files of one.

Now the router splits first. `route_complaint` returns a **list** — one
route per distinct problem, in the founder's order — and each route carries
the founder's own words for *its* issue. That quote is checked the way
intake checks a "said" value (§13.26.7): a whitespace-normalised span of
what they actually wrote, or it is dropped, because a summary shown back
under the heading "Your words" is a lie the founder cannot catch. A slug
the workspace does not have fails the whole plan loudly rather than
routing the rest — keeping the routable issues and discarding the others
would be the original defect wearing a plural.

The classification page shows every issue as its own card, with the whole
original message above the split so the founder can see nothing of theirs
went missing. Each card has a checkbox, ticked, because "fix all three"
and "fix the first, I was thinking aloud about the others" are both
reasonable and only the founder knows which. Confirming runs every ticked
issue as its own repair against its own spec — one correction, one commit,
one log line, unchanged — and the founder lands on a page stating what
happened to each, instead of a redirect home that was fine for one result
and a lie for three.

Details:

- `run_corrections` is the new entry point; `run_correction` still repairs
  exactly one issue, and now **refuses** when handed an unrouted complaint
  that turns out to hold several, naming the plural entry point. Silently
  picking the first is the defect, so it is not a fallback.
- One issue that cannot be repaired does not stop the ones after it. Every
  issue gets a result, whatever it is.
- `avs correct` prints one line per issue and **exits 1 if any failed** — a
  run that repaired two of three and exited 0 reports success for the one
  it could not do.
- The router's `max_tokens` went 512 → 2048. A truncated list parses as
  valid YAML that is quietly short, which is this bug again.
- A single-issue complaint renders exactly the page it always did: one
  decision, no checkbox, no plural. The fix must not tax the common case.

## v0.72.3 — the scheduler tells you when it is running an old build

Patch. Releasing v0.72.2 exposed the gap between *published* and *deployed*:
the LaunchAgent's plist names an absolute path to an `avs` binary — whichever
install was on PATH when the agent was armed — and that is a different install
from the one a release is cut with. `git push`, a green publish and a new
version on PyPI move nothing on the machine. The daily loop went on running
v0.72.1, including the metering fix it did not have, and would have kept doing
so until somebody thought to check by hand.

That is the same shape as every bug `avs cadence` exists to catch: a green
report over a stale reality. So it is a mechanical check now.

`avs cadence` reads the installed plist, asks the scheduled binary's own
interpreter which version it has, and compares. A scheduler running an older
build than the reporting one is a finding: it prints both versions and the
exact `pip install --upgrade` line for that install, and **exits 3** — the
same code as an overdue loop, because a yellow line alone gets scrolled past
and the exit code is what a script reads.

Details that matter:

- The probe asks for **distribution metadata** (`importlib.metadata.version`),
  which is what pip wrote to disk and cannot drift, falling back to
  `__version__` only for an uninstalled checkout. `__version__` is
  hand-maintained and has drifted before — 0.70.1 shipped inside both v0.71.0
  and v0.71.1.
- It probes the **interpreter** named in the console script's shebang rather
  than running `avs --version`, because it has to work against builds older
  than the one asking, and those are precisely the builds that lack any flag
  added later.
- Only *older* counts. Running `avs cadence` from a development checkout while
  the scheduler holds the last release is normal and is not reported.
- An install too broken to import is reported as unreadable, never as current.

Also: `avs --version`, which did not exist.

## v0.72.2 — metering stops being something each command has to remember

Patch. v0.72.1 fixed `compound`'s missing ledger flush by hand. Fixing the
one caller that was caught is how the same bug comes back: the audit that
followed found **2 of 77 commands** flushed the buffer, and `gepa` — a
provider-calling path with no production caller yet — was about to inherit
the gap the moment someone wired it up.

Three changes, smallest to largest:

- `gepa.write_proposal()` flushes. It is the terminal step that knows a
  workspace, and closing it before gepa has a caller means whoever wires it
  up inherits the metering instead of the leak.
- `smoke` needed no fix — it already flushed, including on the
  release-blocking exit-1 path — but nothing pinned that. Now a test does,
  on the failing path specifically, because an early exit is exactly where a
  later edit drops a flush.
- Every command flushes, once, in one place. A wrapper around each
  registered command persists whatever was buffered to the workspace the
  command was already pointed at (`--repo-dir`/`--workspace`). An empty
  buffer flushes to nothing, so this is inert for commands that never call a
  provider, and an unwritable ledger can never mask the command's real
  result.

ADR-032 removed the spending *cap* and kept the metering deliberately. This
makes the kept half structural rather than a rule each new command's author
has to already know.

## v0.72.1 — the compounding loop writes down what it spends

Patch. Found by arming the scheduler and reading the ledger afterwards:
`avs compound` called a provider, produced a real proposal, and the
workspace's `.mas/spend.jsonl` showed **zero calls**.

The provider adapter buffers usage in process state; only a caller that knows
the workspace can flush it to disk. The review graph, `build`, `autopilot`
and the Studio all flush. The compounding loop never did — so every run spent
real money and left no trace of it. Survivable while it was hand-run and
occasional; not survivable once `avs cadence` put it on a daily LaunchAgent,
where it becomes a standing recurring unmetered cost.

`compound` now flushes and prints the workspace cost line. ADR-032 removed
the spending *cap* and deliberately kept the metering; this is the kept half,
and it is now pinned by a test that was verified to fail without the fix.

Audited the rest: `sweep` is deterministic and calls nothing. The other
provider-calling modules (`leader`, `verify`, `studio_chat`) flush through
their callers. `gepa` and `smoke` do not, but neither is on a scheduler.

Suite 1728 hermetic tests (as measured by CI at the tag).

## v0.72.0 — a loop that read nothing no longer reports as a loop that worked

`cadence` decided freshness from the *existence* of a dated artifact. Run
`avs compound` against a workspace whose 7-day window holds no reviews and it
writes a proposal without ever calling a provider — and that file then reads
as a healthy run for a week. The narrow form of the same "looks done" this
feature exists to prevent, and it was demonstrable, not theoretical.

The fix separates two things a date cannot tell apart:

- **Read nothing** (`Window: 0 review(s)`) — the loop ran, so it is *not*
  stale and does not fail a gate, but it is now marked `vacuous`, rendered
  `ok, empty`, and named in the summary: "all 3 loops within cadence, but 1
  loop had nothing to read: compound".
- **Read plenty and concluded nothing** — twelve reviews where no constraint
  cleared the evidence bar is work with a real negative result, and is
  reported as such.

Sweep is deliberately exempt: invariant 14.30 makes a clean pass a finding
that must be recorded rather than pass silently, so an empty sweep is real
work. Its own `note` is carried through instead. An unrecognised proposal
format claims nothing in either direction rather than guessing.

`avs cadence` gains a **"last run produced"** column.

Also fixed: **`__version__` had drifted to 0.70.1** and shipped that way in
both v0.71.0 and v0.71.1 — nothing read it, so nothing caught it. It is now
pinned against `pyproject.toml` by a test, along with the CHANGELOG's leading
entry, because the release checklist that was supposed to catch this is
exactly the kind of habit this release is about not relying on.

Suite 1726 hermetic tests (as measured by CI at the tag).

## v0.71.1 — the trigger gets an environment

Arming v0.71.0's LaunchAgent against a real product workspace surfaced the
one thing that would have made it useless: **launchd does not read a login
shell.** A credential the operator keeps in `.zshrc` — here an
`ANTHROPIC_API_KEY_FILE` pointing at `~/.secrets/` — is simply absent at
09:00. `compound` would have reached its provider with nothing and failed
every morning into a log file nobody opens, which is the silent-success
failure this whole feature exists to prevent, reintroduced by the installer.

The plist now carries an `EnvironmentVariables` block:

- **Pointers travel, secrets never do.** `ENV_POINTERS` (`*_KEY_FILE`,
  `ANTHROPIC_BASE_URL`, `AWS_PROFILE`, …) are copied. `ENV_SECRETS`
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) are **refused by name** — the
  plist is a readable file in `~/Library/LaunchAgents`, and writing a key
  into it would turn the scheduler into a credential leak. The refusal names
  the variable so the operator converts it to its `_FILE` form instead of
  debugging a silent 401.
- **PATH is set explicitly.** launchd's default is a bare
  `/usr/bin:/bin:/usr/sbin:/sbin`, which does not contain the interpreter
  `avs` was installed into.
- **No credential at all is a warning at install time**, not a discovery a
  week later.

Also corrected: `install_agent` now reports `env_keys` and `warnings`, and
`avs cadence --install` prints both.

Suite 1718 hermetic tests, 3 skipped.

## v0.71.0 — the recurring loops get a trigger, and the build loop gets a floor

Three loops in this system were designed to recur — the compounding loop
(§09.8), the Sweep role (doc 29), weekly attention collection (doc 25
§76.4) — and none of them had a trigger. "Weekly" was a habit, and this
repo is the evidence of what a lapsed habit costs: `attention.py` opens by
saying a missed week does not merely lose a week, it **resets the streak
the kill criterion depends on**, and `metrics/attention-log.yaml` records
the discipline starting 2026-W31 and then stops. Nothing reported the
lapse, because nothing was watching.

**`avs cadence`** reads the artifacts the loops already write — a
`proposal-<date>.md`, a `digest-<date>.yaml`, a `logged` attention row —
and says which loop is overdue. It exits 3 when one is, so it can gate a
script. Three rules keep it from lying: a loop that never ran reports
`never_run` rather than fresh (the one failure a watchdog can actually
have); a `not_tracked` attention week is not a run, or the series the kill
criterion is falsifiable by could look maintained while measuring nothing;
and a future-dated artifact clamps to zero instead of inventing a negative
staleness. Seven days is `due`, ten is `overdue` — a weekly loop is seven
days old on the day it is next due, and that is health.

**The trigger is machine-local, and had to be.** The obvious move was a CI
cron, and it would have been wrong: every artifact these loops read lives
under `.mas/`, which is gitignored. A runner checks out an empty `.mas/`,
finds every loop `never_run`, and gets tuned until it reports a clean pass
forever against state it cannot see — the "looks done" failure with a green
check on top. So `avs cadence --install` writes a user LaunchAgent instead.
It fires **daily** and runs only what is due: a weekly timer has one chance
to be missed, a daily due-check has seven, and finding nothing due costs a
file-stat. `--run-due` is idempotent for the same reason.

It is written disarmed. `--install` writes the plist and prints the
`launchctl` line; `--arm` loads it. `RunAtLoad` is false, so installing a
trigger never starts a run. And `avs attention` is only ever run in its
read-only form: it logs a row solely with `--confirm-hours`, that number is
the operator's, and the scheduler surfaces the ask without answering it.
Mechanical recurrence is the machine's job; the judgment is not.

**A termination bound on the build loop.** `MAX_ITERATIONS` bounds attempts
per task and `max_tasks` bounds tasks, but a task whose context grows each
iteration can consume without limit inside those counts, and the ledger
said nothing until the run ended. `avs create` now stops between tasks once
a run passes `--token-ceiling` (default 10M).

This is **not** a spending cap and ADR-032 stands: it counts tokens, never
dollars, no price table can make it fire, and a month of heavy spend still
builds — pinned by a test that says so. Ten million is roughly 3× what a
full 12-task build arithmetically costs; a run that crosses it is looping,
not working. The status is its own word, `halted`, not `failed`, because
nothing failed: it stops at a task boundary where stopping is free, keeps
every built module, takes no undo checkpoint, and a re-run continues from
there. `--token-ceiling 0` disables it.

Also: the two remaining runtime `assert`s in `cli.py` are explicit checks
that raise with a diagnosable message (CLAUDE.md — an `assert` holds only
until someone runs under `-O`, and then the next line raises
`AttributeError` on `None` instead). And three tests were reading the
developer's own shell: the preflight resolves a key through `env_or_file`,
but the enterprise preflight and journey tests cleared only
`ANTHROPIC_API_KEY`, never `ANTHROPIC_API_KEY_FILE` — so a machine that
keeps its key in a file saw `model: ready` where the test's own comment
said "no credential in this test env", and the suite failed there and
nowhere else. The second and third sites were hidden behind the first until
the run went past `-x`.

Suite 1714 hermetic tests, 3 skipped.

## v0.70.1 — a name declared twice renders every importing page blank

A patch release: no command, file format, or route changes. One gate check
and one test repair.

**The 小程序 loadability gate now catches duplicate top-level declarations.**
Found the hard way in a product workspace: a second `pad2` was added to a
utils module, and **106 `node --test` cases stayed green while the app was
entirely broken** — the cart, delivery and profile pages all registered no
`Page()` and rendered pure white. Only the DevTools run found it.

The mechanism was verified rather than assumed, because the obvious
explanation was wrong: `function a(){} function a(){}` is legal in a sloppy
script **and in a strict one**. It is a SyntaxError under ES-*module*
semantics, which is what the 小程序 toolchain compiles with — so the module
never evaluates and everything importing it goes blank. Node's own test
runner loads the same file as a script and sees nothing wrong, which is
exactly why the suite could not catch this.

The gate reports duplicate top-level `function`/`const`/`let`/`class` names
per module. `var` is deliberately excluded: re-declaring a var is legal
everywhere. Validated against the real incident in both directions —
re-introducing the exact second `pad2` on a copy names the file, the
identifier, the count and the consequence; the repaired workspace passes.

This is the third member of the same family, all mechanically detectable
with no DevTools and no LLM: a page file that does not exist, a `require`
that escapes `miniprogramRoot`, and now a module that cannot evaluate.

Also: two README media assertions follow the v0.70.0 screenshot rename
(`studio-en.png` → `studio-en-v070.png` and siblings), which had shipped
without them.

Suite 1689 hermetic tests — and this is the first release whose publish run
enforces PC-1 itself: a claimed test count that disagrees with the count
that just passed now fails the release rather than publishing a number
nobody measured.

## v0.70.0 — what a real run said, and the repair for what it broke

One live English run through the shipped Studio, driven in a browser to
replace the README's pre-redesign screenshots. It found six defects that a
green hermetic suite had not, because each needs a real model, a real price
list, or a real rollback to appear.

**Every artifact a founder reads now follows the FDR's own language.** The
confirmation prompt said "in the SAME LANGUAGE as the FDR" and then
demonstrated its section headings as `会做什么 / What will be built` — and a
demonstration beats an instruction, so the model copied the frame and wrote
the body in Chinese over an entirely English FDR. The one page a founder
must read *before spending money* was not in their language. The headings,
the counted tally and the cost heading now come from a per-language table
keyed by the FDR itself, detected deterministically (any CJK ⇒ zh), because
a model call to decide which language to answer in is a model call that can
get it wrong.

**`built: true` rides inside the task's own commit.** Written afterwards it
was an uncommitted working-tree change, and two recovery paths discard those
wholesale: `git checkout -- .` when a fix iteration is rolled back, and
`_reset_workspace` after a failed build. A run that committed six modules
kept the flag on two. The wrong headline number ("2 of 6" over a finished
product) was the visible half; the expensive half is that `built_task_ids`
is what a resumed run reads to decide what it may skip, so the next
`avs create` would have rebuilt and re-billed four committed modules.

**`avs reconcile [--scan DIR] [--apply]`** repairs that damage where it
already exists, since no code change can fix a workspace built last week.
It restores a flag only where outcomes.yaml AND a commit naming that spec
(or the title its commit subject embeds) already agree; a task they disagree
on is named for a human and left alone. A task is the unit, not a spec file:
planning is not deterministic, so a re-run leaves the same task a second
spec under a slightly different slug, and repairing those leftovers would
resurrect specs a later plan replaced. Keyed on spec files the first version
reported eleven findings across three real workspaces and nine were false.

**The wait stopped claiming a number it did not have.** `"$0.00 so far"` ran
for twenty-four minutes of a build that was spending: the guard covered *no
calls at all* but not calls whose models have no price, and the building page
then stripped the `≥` floor marker off the figure it did print. A founder
reads a zero as "this one is free"; it meant "this workspace has no price
list". No figure now, and the floor marker survives where there is one.

**And one NOW, not three.** Both renderers read "pending + a step" as
in-flight, and every task that had ever narrated anything still carried its
last line — so a sequential build showed three modules building at once, of
which at most one could be true.

Plus the README's founder demo is that run: the flow GIF, the single screen,
and the `--lang zh` still are all the redesigned Studio, and the caption
states the run's real outcome and the defects it exposed. The honesty test
moved with it rather than being relaxed — "partly built" was this demo's
admission against interest and would now be the lie, so the test pins the
unedited claim plus at least one concrete admission, and fails a caption
that admits nothing.

1688 hermetic tests.

## v0.69.0 — the key, the paragraph, the product, and the decision

The four proposals the Studio redesign deliberately left unbuilt, built —
each one where a founder actually meets it.

**The key gate.** The tool is free and the model is not, and that fact used
to reach a founder as a stack trace on their first send. A keyless Studio on
a paying provider now opens on whose-account-pays language, the doors that
need no key typed here (`AVS_ANTHROPIC_MODE=bedrock|vertex|foundry`, a
gateway bearer token, a mounted `*_API_KEY_FILE`), and `/demo` — the
vendored recorded run, rendered through the same timeline the workspace's
own reviews use, readable with no key at all. A pasted key is set for that
process only and never written to disk; persisting it was refused, because a
key in a workspace is a key in somebody's next `git add`. Detection is
boolean through `env_or_file`, so the value is never returned, rendered or
logged. A key that gets REFUSED carries the paste box on the page that names
the problem instead of a trip back through settings. And a **token-gated
(shared) Studio does not offer the box at all**: there the process is
everyone's, so one person's key would pay for every build anybody on that
token starts, while the form's own "this process only" reads as "my session
only".

**One paragraph, then only the gaps.** Six fixed questions asked in order
was a form wearing a chat's clothes. Now: one open prompt, one extraction
pass, SAID/GUESS rows, and questions only about what is genuinely missing.
The charter rule (§13.26.7 — agents never author a user need) is enforced in
Python, not in the prompt: a `said` value that is not a whitespace-normalised
span of the founder's own paragraph is **demoted to a guess**, guesses are
capped, and an unparseable response falls back to asking all six. A guess
never reaches FDR.md until the founder confirms it — verified against a
deliberately lying model, whose fabrication was demoted and kept out of the
document. The paragraph itself is kept verbatim, because a span is not the
framing.

**Try it, beside its own acceptance list.** `/try` puts the criteria the
product was supposed to meet next to it as rows the founder marks Fine or
Wrong — and a complaint made from a row travels WITH that row, so the router
knows which criterion failed instead of receiving a bare sentence. A tick is
the founder's note, never a verdict, and the page says so. The Studio does
not start the product for you: a server spawned by a page load is a process
nobody owns on a port nobody chose, so the page names the real command and
the detected entry point.

**The decision, before the work.** A complaint is classified before it costs
anything — SMALL FIX repaired in place, or NEW REQUIREMENT that gets its own
plan — showing the founder's words, the feature, and what the implementer
will be told, with Reword one click away and "nothing has been changed yet"
on the page. One router, extracted and shared, not a second classifier. And
the change log becomes the undo surface: each entry offers *go back to just
before this change*, stating how many later changes and commits go with it,
because the history is a straight line of checkpoint tags and pretending
otherwise would promise an independence the model does not have.

**Also:** the wire-up gate grew the half it was missing. It proved every
button pointed at a real route and every route was pointed at by some page,
but it rendered pages and never PRESSED anything — so two unvalidated path
segments lived in that gap: `/feature/build`'s `slug` walked the build out
of the workspace entirely, and `/retry`'s unknown `task_id` spawned a worker
that died on arrival and left its pid file behind, so the Studio showed a
build in progress that could never finish. Both now take the segment rule
the review and incident ids already carried, well-formed-but-absent is
answered out loud, and every POST route is pressed with the body its own
forms send.

1655 hermetic tests.

## v0.68.1 — the diagnosis stops lying, and the Studio reads like a product

A patch release: no command, file format, or route changes. Everything below
either corrects a message that pointed somewhere wrong, or restyles a
surface without moving it.

**The 小程序 automation diagnosis was wrong three ways, each caught by using
it.** A timeout was reported as "almost always the service port", which sent
an investigation to re-check a toggle that was already on — the CLI had in
fact printed its success marker, and DevTools was simply compiling a project
it had never opened (measured: first open >300s, second 27s). Then the
corrected message lied differently: it read the *whole* CLI log, which is
appended to across runs on purpose, so a `✔ auto` from an earlier run was
read as this run's success — reporting "first-open cost" for a session that
had exited after four seconds, while quoting the 300s ceiling as if it were
the measurement. The check now snapshots the log size before spawning and
reads only what this run wrote, reports the wait as measured, and treats an
early exit as its own diagnosis (a session already open on the project,
naming `pkill -f 'cli auto'`). The success marker is matched as a whole line,
never as a substring — this workspace's own path contains "auto", so a
substring test would call every failure a success.

**A directory named `miniprogram/` no longer buys silence.** Found by
pointing the new gate at products built before it existed: one workspace
keeps its mini-program at the repo root — `app.json` with three registered
pages, `pages/` beside it — next to a stray `miniprogram/` holding nothing
but a `.DS_Store` and an `api/` folder. `_miniprogram_root` picked the stray
on its *name*, found no `app.json`, and the gate answered "nothing built here
yet" — a vacuous pass over a real product, which is the exact silent no-op
this gate exists to prevent. With nothing declared, evidence now beats
directory names: the candidate that actually holds an `app.json` is the
project. A declared `miniprogramRoot` still wins, because that is the
platform's own answer. Four defects became visible on the affected workspace
the moment the gate could see it.

**The scaffold stops shipping a future AppID leak.** A new workspace's
`.gitignore` now covers WeChat DevTools' per-machine artifacts:
`project.private.config.json`, and the `project.config.json` DevTools writes
*inside* `miniprogramRoot` carrying the logged-in account's real AppID —
where the scaffold's root config deliberately uses `touristappid`, because a
real AppID is somebody's identifier and not ours to commit. That inner file
also shadows the root config for tools that look there first.

Corrected in [the pipeline guide](docs/miniprogram-pipeline.md), because a
wrong note becomes tomorrow's cargo cult: **`libVersion: "latest"` is fine.**
Tested both ways on a cold IDE with identical results — it breaks
`miniprogram-automator`'s `checkVersion` (an undefined `SDKVersion`) but not
the raw driver, which never version-checks. The guide also now records that
the IDE degrades over a long automation session: after many open/relaunch
cycles `captureScreenshot` times out and app calls fail with a bare
`Uncaught [object Object]`, on a project that passes cleanly after a cold
start. A run failing in the driver rather than in the assertions means
restart the IDE, not that the product broke.

**The Studio worked and read as a debug page; now it reads as a product.**
Nothing new is fetched, no client framework, no new source of truth — still
server-rendered HTML over the same workspace files, still bilingual, **every
route unchanged**. A design system replaces the styling accidents: one white
card on a warm ground, one green primary action (the button that spends real
money no longer looks like "Triage it"), a persistent Describe → Plan →
Build → Your product stage rail, and hint text that clears AA instead of
sitting at ~3.5:1 while carrying real instructions. `CONFIRMATION.md` and
`BUILD-REPORT.md` are rendered rather than dumped as one grey `<pre>`, with
the "will NOT build" section lifted into its own callout because that is the
cheapest place to catch a misunderstanding. The build view gains the elapsed
clock and accrued spend that were already on disk and thrown away, plus
DONE/NOW/QUEUED per module — still no percentage and no ETA, because the
system does not know whether attempt 2 is the last one. Modes reorder instead
of appending, and add-only is intact: nothing is removed in any mode. Two
layout defects found by driving the real UI in a browser rather than in
tests: an unbreakable git-identity token pushed the preflight table into the
trust table beside it, and a fixed-width state chip overflowed into the task
title for any state longer than `QUEUED`.

**A task id from a form is never taken on trust.** A well-formed id that is
not in the plan used to spawn a worker that died on arrival — leaving a pid
file, so the Studio showed a build in progress that could never finish. It is
now answered out loud. (That guard made a `/retry` test's planless fixture
stale; the fixture was repaired, and the property it protects — `--provider`
travels, output lands in `.mas/build.log` and never `DEVNULL` — is intact.)

Suite 1572 hermetic tests.

## v0.68.0 — a blank page is no longer a passing page (小程序)

The 小程序 runtime check reported **"all 7 registered pages rendered"** for a
build in which three were pure white. Nothing lied: the check's only signal
was that `reLaunch` did not throw, and it does not throw for a page whose JS
died before `Page()` ran — the page still opens, still sits on the page
stack, and still renders nothing. This release makes the evidence match the
claim, at both the static and the runtime rung.

**Static (free, no DevTools, runs in CI).** The loadability gate now walks
the relative `require()`/`import` chains from `app.js` and every registered
page, failing on a specifier that resolves to no file and on one that
escapes `miniprogramRoot` — DevTools cannot package what is outside the
root, so that import throws at evaluation time and the page goes blank. The
incident that named this: a builder wrote `utils/telemetry.js` at the repo
root and three pages imported it by three different relative paths; every
page *file* existed, so the gate passed 7/7.

**Runtime (`avs mp-runtime`, your machine only).** Every page is now
screenshotted into `.mas/mp-runtime/`, and **a single flat colour is a
`page_blank` finding** — a judge that is cause-agnostic, so an empty WXML
and a dead require fail the same way. PNG decoding is stdlib-only (IHDR,
inflate, the five scanline filters); anything exotic is conservatively
treated as not-flat, so an encoding surprise can never fail a healthy page.

Two driver defects fixed with it. The check no longer drives through
`miniprogram-automator`, whose `launch()` **and** `connect()` hang without
diagnosis against IDE `2.01.2510290` — it speaks the automation protocol
raw over WebSocket and spawns `cli auto` itself, whose own words land in
`.mas/mp-runtime/cli-auto.log`. And it never reuses a listening automation
port: a leftover session serves whatever project *it* opened, which once
verified the wrong app under this project's name, all green. Each run takes
a free port from 9420–9439, spawns its own session, and terminates it in a
`finally`.

Validated against the incident in both directions: a copy of the affected
workspace with one require path re-broken reports `page_blank` on exactly
that page; the repaired workspace reports ok on all seven. The flat-colour
judge separates every real screenshot from that investigation — three blank,
three rendered.

Also in this release:

- **A re-run remembers why it failed.** Recorded failure context reached
  only same-run retries, so pressing "continue the build" re-attempted every
  failed task blind — same inputs, same writer, same wall. Run 2's *first*
  attempt at a task run 1 could not build now carries run 1's diagnosis, on
  both the sequential and the parallel-wave path.
- **Continuing is one click, not one per module.** The Studio's interrupted
  page and failed-modules card lead with a single "Continue the build"
  (locked plan reused, built modules skipped, failures re-attempted with
  context); per-module buttons remain for surgical retries. The `/retry`
  path also regained two previously-fixed properties, now pinned by tests:
  its output goes to `.mas/build.log` rather than `DEVNULL`, and it inherits
  the Studio's `--provider`.
- **The scaffold's index page no longer ships blank by construction** —
  `{{title}}` was bound to an empty data object, and that page is where a
  share-QR scan lands.
- [docs/miniprogram-pipeline.md](docs/miniprogram-pipeline.md): the four
  rungs (unit tests → loadability gate → runtime screenshots → a
  per-product designed-flow script), each with the failure that justified
  it, plus the operational notes that cost a session to learn — zombie
  `cli auto` sessions block later handshakes, cold project boots take
  60–150s, and `libVersion: "latest"` breaks the automation handshake.

Suite 1560 hermetic tests.

## v0.67.0 — spend is measured and reported, never gated (ADR-032)

An operator decision, recorded as
[ADR-032](docs/adr/032-no-framework-spending-cap.md): every model call is
billed to the operator's **own key or subscription**, so budget enforcement
belongs to the provider account that does the billing. Provider-side
spending limits are authoritative — they see all usage on the key, not just
this framework's slice, and cannot be bypassed by a bug here. A
framework-side cap duplicates that control at best and contradicts it at
worst: for subscription billing, tokens do not map to marginal dollars, so
a token-priced cap would pause builds over money that was never being
spent. The gate's own refusal message already had to strain to attribute
the stop to the operator ("the limit YOU set") — a sign the mechanism sat
on the wrong side of the ADR-U20 boundary ("the framework never spends
money").

**Removed:** the monthly cap and everything that refused over it —
`cost_gate` at Gate 1 and per build task, the auto-retry precheck,
`monthly_cap_usd`, `avs prices --cap`, the Studio ceiling form and its
`/cap` route, and the `avs cost` exit-3 path. An old `cost-model.yaml`
carrying the retired key still loads; the key is ignored, never an upgrade
error.

**Kept, deliberately — the answer to "how much will a typical month cost
me?" is visibility, not refusal:** the ledger metering every call at the
adapter, the build report's arithmetic cost line, `avs cost` per model, the
Studio cost card on the confirm page (before the first dollar) and the
report page, and the sourced reference price table as pure estimation —
ranges still resolve upward, operator corrections still survive re-import,
and an unpriced call still keeps the total labelled a FLOOR rather than
counted as zero. The surfaces now point at provider-side limits, where a
ceiling is both effective and complete.

The removal is pinned as firmly as the presence was: a month of heavy spend
must not stop a build or a review, the Studio renders no ceiling form and
no refusal copy, `POST /cap` is gone, and legacy-key compatibility is a
test.

## v0.66.0 — the spend guard: the ceiling reaches the one persona that cannot use a CLI

Built from research rather than intuition. The published record on
non-technical builders using AI app tools converges on a short list of
failure modes: the bug doom loop that burns credits, the comprehension debt
of owning code you cannot read, the prototype-vs-product gap — and, at the
top, **surprise bills**: runaway usage-billed sessions ($607 Replit bills,
credits gone overnight), with no major platform setting a spending cap by
default and every guide's first recommendation being a hard cap on day one.

Cross-validated against this codebase mode by mode, most of that list was
already closed — bounded build iterations with test feedback, the auto-retry
with failure context (v0.65), plain-language reports that never blame the
founder, probes generated from the founder's own requirements. Two gaps
survived the check:

**The cap existed and the founder could not reach it.** `avs prices --import
--cap` shipped in v0.65 — CLI-only. The Studio's cost card showed spend with
no ceiling on it, to exactly the persona the industry data says is most
burned by the missing ceiling and least able to use a CLI. The card is now
the **spend guard**: this month's spend against the cap (honestly labelled a
floor when a call is unpriced), a one-click set-cap form that writes the
same `.mas/cost-model.yaml` the CLI owns (packaged reference prices ride
along so the cap can actually fire; a price the operator corrected is never
overwritten), and — when the cap is reached — "builds are paused between
modules, nothing is lost, raise it to continue", because a cap doing its job
must never read as a failure. It sits on the **confirm page**, next to the
button that starts the spend, not only on the report page where the bill
already exists — and it shows before the first dollar, where the old card
hid itself until money had been spent. Mode-adaptable, add-only: engineer
gains the per-model table and the CLI twins (`avs cost`, `avs prices`);
enterprise gains the governance note; the founder card is complete on its
own, in both languages.

**The verification nobody could see.** Probes generated from the founder's
own FDR run against the built product and write `VERIFICATION.md` — the one
artifact that answers "does it actually work?" without asking a founder to
judge code — and it was never linked anywhere. The Studio now serves it and
links it beside the acceptance walkthrough. The Studio's own wireup gate
caught the first draft of this change (a route rendered by no state), which
is the gate doing precisely what it was built for.

## v0.65.0 — the run presses its own retry button, and the cost cap can fire

Two changes, both against the failure modes that matter most for autonomous
development: a run stopping to ask a human for something mechanical, and the
same error recurring because nothing carried the diagnosis forward.

**A failed task no longer ends with a button.** Every `spec_blocked` /
`build_failed` / `merge_conflict` outcome used to be recorded and left for
the founder's retry button — and the bench record shows that button usually
worked (t1/t2 recovered on the second pass, t5/t9 built on retry). Pressing
it takes patience, not judgment, so the run presses it itself: **one bounded
retry pass** after the first pass over the plan, in dependency order (a task
that failed because its dependency failed retries *after* the dependency
recovered), gated on the cost cap (a run over its cap does not retry itself
deeper into the cap), every attempt and result recorded in the report's
auto-approvals. Applies to `create`, the parallel-wave path (a
`merge_conflict` retried serially is exactly the fix), and `add`. The
judgment gates are untouched: FDR questions, plan confirmation, and review
escalations still wait for the human — those stops are the point.

**A retry is a different attempt, not a replay.** The spec writer and the
implementer had no channel for "the previous attempt failed because X", so
every retry started blind — the writer picked the same rejected phrasing,
the implementer re-invented the same phantom import (run-3 forensics). The
previous attempt's status, detail, and test summary now travel into both
prompts as `<previous_attempt_failed>`; `avs retry-task` reads the recorded
failure out of `outcomes.yaml` and passes it the same way; and a retry that
also fails keeps **both** diagnoses so a later attempt starts from the
accumulated history.

**And the bug that made recovery invisible.** A resumed run that rebuilt a
previously-failed task *appended* a second outcome row, so the tally counted
the ghost: a workspace whose failed task was rebuilt on the next run
reported "3/4 modules built" and status `failed` for a product that was
entirely built — then asked the founder to retry work that was already done.
A run that says failed when it succeeded is a manufactured stop-and-ask.
Outcome rows are now replaced, never shadowed.

The per-task attempt (spec → Gate U3 → build → review → repair) is now one
shared code path for the first pass, the auto-retry, and the feature flow —
hand-copied variants are how `retry-task` once shipped without the review
gate. The feature path gains review parity and progress narration for free.

**The cost cap can fire** (`avs prices`). The gate has been complete since
v0.59.0 and inert ever since: with `prices` empty, every call was UNPRICED,
the month's total was a FLOOR, and a cap compared against a floor never
bites. The package now ships published list prices with a vendor URL and a
retrieval date per provider — the same evidence standard `claim_lint`
applies to every other number in this repo — and `avs prices --import
[--cap <usd>]` writes them into `.mas/cost-model.yaml`, where you own them.
Ranges resolve **upward** (Sonnet 5 carries the standard price, not the
introductory one; Gemini's larger-prompt tier is the one recorded), so the
estimate is a ceiling, not a guess. Your own price always survives a
re-import; a model with no sourced price (grok-4) stays visibly unpriced
rather than invented, and keeps the total labelled a floor. Verified end to
end: a simulated month reported `spend ≥$46.76 of $25.00 cap`, refused new
work naming the file the cap was set in, and the auto-retry pass respects
the same gate.

Also: a test-isolation bug found by that work — spend is recorded into a
process-global buffer, so a test that recorded without flushing leaked rows
into the next test's workspace ledger. The suite now drains the buffer
around every test.

## v0.64.0 — the rules that were only ever sentences

Five things this system said and did not enforce. Every one had the same
shape: a rule written in prose, next to a code path free to ignore it — and
in four of the five cases the code path had already been taking the free
option in real runs.

**Gate U2's scope lock was decoration.** `approve_plan` wrote
`status: locked` and the next `avs create` re-decomposed the brief anyway:
assess, discovery, four charter voters with a verify pass, a leader, then
planning — minutes and real money — to arrive at a *different* plan, because
planning is not deterministic. That is also what made resume unable to
recognize its own work, since ids are positional: run 2's `t4` (购物车与结算UI)
matched run 1's `t4` (结算页与下单记录界面). A locked plan is now the plan,
keyed on a whitespace-insensitive fingerprint of the FDR it came from —
reflowing a paragraph is not a scope change, different words are. After the
lock, different words are refused with the routes named (`avs add`, `avs
scr`, or a deliberate `--replan`) instead of silently re-planned.
Re-running `create` on an unchanged FDR now costs **nothing at all**: the
reuse path makes no model call, not even for the confirmation.

The one place that must never hit that refusal is the Studio's requirements
form. A founder rewriting the FDR there has asked for scope to change, in
the place the product offers for asking, so the lock is released at the
write rather than surfacing as an error two screens later on a page that
cannot explain it.

**A retried module was never reviewed.** `retry-task` ran spec + build and
stopped. In one real run four of the seven modules that reached the founder
came through that path: no reviewer had read them, no fix iteration could
fire, and their rows carried an empty verdict beside modules `create` had
reviewed properly. Gate 3 — review, one bounded repair, re-review, roll back
if it did not clear — is now shared code rather than a copy, so the two
paths cannot drift apart again. The retry path was also the only build path
that never carried the founder's FDR as its `source_contract`, so a retried
module was free to invent field names its siblings had agreed on. And
`setdefault` on a `model_dump` could never fire (the key is always present,
set to `None`), so "keep the verdict from the original run" had been
overwriting it with nothing.

**"A .py file in a 小程序 is always wrong" was a sentence in a prompt.** A
spec came back with `tests/test_catalog_page.py` regardless, which made the
build gate demand a passing pytest run against a project with no Python in
it; the task died three iterations later on "pytest collected no tests" — a
true sentence about the wrong thing. It is now checked at both boundaries:
the spec blocks with the JS alternative spelled out, and the write refuses.
Per-runtime, not a dislike of Python — the web profile *is* Python and is
untouched.

**The mock was the reason that could happen.** It answered with a Python
item-store for every profile, so every hermetic mini-program test was
exercising a product WeChat could never load — 1478 green tests over a
fiction. It now answers in the language the profile actually runs, and a
小程序 autopilot run under the mock builds real JavaScript that
`node --test` executes. The bench case followed: its probe had been
importing a Python module out of a mini-program.

**And the 小程序 "it works" claim is finally checkable.** The build gate's
loadability check is static — it asks whether DevTools *would* open the
project. `avs mp-runtime` opens it for real and visits every registered
page. Three preconditions, each a visible skip naming its remedy: the
DevTools desktop app (macOS/Windows, never CI), `miniprogram-automator`, and
DevTools' service port, which is a one-time human toggle in its security
settings. With the port off the automator swallows the CLI's own
`IDE service port disabled` and simply times out, so that case is reported
as **skipped, not failed** — nothing was checked, and red would read as
"your pages are broken".

### `avs smoke` — and it earned its place on the first run

Twelve real defects were found in one day of running this product against
one real FDR. The suite was green for every one of them, and it was right to
be: they lived where a mock cannot go. The expensive one shipped to PyPI
twice — v0.60.0 and v0.61.0 could not build a single task, because the
implementer asks for 32000 output tokens and the SDK refuses a
non-streaming request that might run past ten minutes, raising before it
sends anything. 1441 tests passed. Every real build died at "attempt 1/3".

Writing more mocks does not fix that; a mock is authored by the same person
holding the same wrong belief about the SDK, so it agrees with the bug.
`avs smoke` makes four real calls per configured provider, costs a fraction
of a cent on your own key, and is now **step 0 of every release**:
`reachable`, `streams_large` (the bug above, as a check),
`truncation_visible` (what stops half a file reaching disk), and
`usage_metered` (so the cost gate is not reading a silent zero). An
unconfigured provider is a loud skip naming the variable — "we did not
check" and "it works" must not look alike.

Its first run found gpt-5 answering empty: a reasoning model at
`max_tokens=16` spends the whole budget reasoning and returns
`content: null` with `finish_reason=length` (512 and up answer normally —
measured, not assumed). Two things were wrong. The check's: no caller in
this system passes 16, so it was testing a configuration that does not
exist. The product's: `choice["message"]["content"]` was returned unguarded
and every caller does `raw.strip()`, so a null reached the voter's generic
retry as an AttributeError about attributes rather than about budget.

Also in this release: `avs create --profile` is optional for a workspace
that already declares one (demanding it on every re-run made resume,
`--yes`-after-confirmation and every later feature an error, and a mistyped
value would have read as a request to change what the product *is*); the
demo scripts left `/tmp`, where one machine's home directory had been baked
into them, for `scripts/demo/` with paths as inputs; and CI moved past the
deprecated Node 20 runtime (`checkout@v7`, `setup-uv@v9.0.0` — that action
publishes no floating major tag, which cost one four-second red build to
learn).

## v0.63.0 — a 小程序 that opens, and a repair that has to prove itself

Seven fixes, all of them found the same way: by running one real founder
FDR — a WeChat mini-program, written in Chinese — three times over and
fixing whatever stopped it. None of them were found by the hermetic suite,
which was green throughout. Every one lived past the point a mock provider
can see: in the assembled output, in the repair loop, in the bookkeeping
between runs.

**Nine modules built, zero of them reachable.** A run produced seven page
directories and no `app.json`, so WeChat DevTools could not open the
project at all. Every module had passed its own tests. The web profile has
had a boot gate since product-bench run 4 — built every task, failed every
probe on "server never listened" — and it was never ported to 小程序,
whose own checks (size, domain allowlist, setData lint) a project with no
entry point passes comfortably. The mini-program gate is static, because
DevTools is a desktop application and cannot be driven headlessly, and it
blocks on exactly what makes a project unopenable: no `app.json`, no
`app.js`, an empty or unparseable `pages` array, a registered page whose
files are missing, and a page directory that exists but is registered
nowhere — a page nobody can navigate to was built for no one.

**A gate can only check a layout somebody guarantees.** Two runs of that
same FDR produced two different trees, and the new gate silently no-opped
on the second because it found no anchor. `avs init --profile miniprogram`
now scaffolds a minimal *loadable* project and commits it, so every task
extends a real project instead of assembling one. The appid is WeChat's
documented `touristappid`, never a plausible-looking one: an implementer
invented `wxb1e7d6736079f6c3` in an earlier run, and that string is either
somebody's identifier or nobody's.

**A repair that made the product worse was committed anyway.** Told that a
cart's `onAdd` handler was never wired to any control, the fix deleted the
handler — the cheapest way to satisfy "this is never used". The next review
called it critical, certain, verified, score 100, "breaking core feature",
and nothing acted on it, because that review ran in the caller *after* the
commit purely to record a verdict. The re-review now runs inside the fix
iteration and can veto: any remaining critical/high resets the workspace to
the commit before the fix, and a rolled-back repair says so in the outcome
rather than looking like no repair was tried. Same number of review calls —
it moved, it did not multiply.

**One unchecked fix iteration cost four modules.** That deleted handler
broke a committed test, and because the build gate runs the whole suite
while existing tests are read-only walls to later implementers, the next
four tasks could not be built by anyone. Not four problems — one, four
times. It went unchecked because the fix iteration validated with bare
pytest, which returns `no_tests` on a 小程序, while the build loop
validates with pytest *and* the JS suite: two standards in one repo, and
the weaker one guarding the path that edits already-committed code.
Separately, and enough on its own to guarantee a cascade, the JS runner
globbed `**/*.test.js`, and `**` walks hidden directories — so it collected
`.mas/failed-builds/<slug>/tests/*.test.js`, the preserved copies of FAILED
attempts. In that workspace it was 31 of 37 matched files: once one task
failed, every later task's gate ran that task's broken snapshot as if it
were the product.

**A reformatted artifact is not a missing one.** A task died on
`grounding violation: required context missing from the implementer's
prompt`, and the writer had not been blind — every acceptance criterion was
there. The probe is the artifact's longest line; here that was a `purpose:`
inside `test_skeletons`, which the prompt renders in a different shape.
Same content, no substring match, and a grounding violation is
unrecoverable rather than retried, so the task was simply lost. Grounding
now tries several probes and grants the receipt if any appears. The bug the
check exists to catch — a prompt assembled without the invariants the
artifact will be judged against — is untouched: an absent artifact scores
zero on every probe.

**Resume skipped work it could not name.** Task ids are positional, and
planning is not deterministic, so `t4` was 结算页与下单记录界面 in one run
and 购物车与结算UI in the next. Matching on the id alone meant the second
run skipped a module that had never been built, while the report announced
"resumed: 1 task(s) already built". Resume now matches on (id, title).

**EARS rejected correct requirements, one clause over from last time.**
v0.62 made the article optional; the writer then phrased its next draft
without the connective and the same task was blocked again by the same
class of pedantry. `then` is now optional too — "If the payload is
malformed, the API shall return 400" is not a worse requirement than the
version with it. A missing `shall` is still rejected. And a successful
`retry-task` now records its result: retries rebuilt and committed two
modules while `product/outcomes.yaml` went on calling them `spec_blocked`,
so the Studio kept offering to retry work that was already done.

Known and unchanged in this release: `approve_plan` sets the plan `locked`
(Gate U2, scope lock) and the next `avs create` regenerates it anyway, so
the expensive upstream is re-paid every run. Reusing a locked plan is a
behaviour change and wants its own release.

## v0.62.0 — the run that could not build, and the page that never said why

Everything here was found by running the product against one real founder
FDR (a WeChat mini-program, written in Chinese) and fixing whatever stopped
it, in order. Four of these are defects that made a whole run impossible.

**`avs create` could not build a single task, on any run, with any FDR.**
The implementer asks for `max_tokens=32000` — it returns whole files, and
16384 truncated real builds — and the SDK refuses a non-streaming request
whose size implies it might run past ten minutes, raising *before sending*:
`ValueError: Streaming is required for operations that may take longer than
10 minutes`. Every build died at "writing the code (attempt 1/3)". Requests
above 8192 output tokens now stream; metering, stop-reason and the
empty-response diagnostics are unchanged and pinned by tests, because a
streaming path that silently dropped usage would break the cost gate and
the truncation guards at once. **v0.60.0 and v0.61.0 on PyPI cannot build;
this release is the fix.**

**A 529 killed runs that were already eleven minutes in.** The transient
retry existed but spent its whole budget in fourteen seconds (four attempts,
2/4/8s). Now six attempts capped at 60s, honouring `retry-after` when the
server sends one, with jitter — the review voters run in a thread pool, and
without jitter they all retry in the same instant.

**A botched final revision threw away a good brief.** Discovery's loop set
`brief = None` on a parse failure, so an attempt that had already passed
schema *and* the four charter voters was discarded when a later polish
attempt came back as malformed YAML. Two runs died holding a usable brief.
It now keeps the last good one and appends a visible note that the final
revision failed, rather than presenting a mid-revision draft as finished.

**EARS rejected correct requirements.** `When fetchFn rejects …,
loadCatalog shall return …` was refused for "does not match any EARS
pattern" because the grammar demanded a literal `the` before the subject —
satisfying it would mean writing "the loadCatalog shall". Three of nine
tasks in one run were blocked by this alone. The article is now optional;
bare subjects must look like identifiers, so `We shall see whether the
founders like it.` is still not a requirement.

**The report blamed the founder for our bug.** Those three blocked tasks
were reported as "三项因为需求描述不够清楚" — three could not start because
the requirements were unclear. False: `spec_blocked` means our spec writer
failed our own checks, and the founder's description is not what is being
checked. The reporter is now told whose failure each status is and told
never to describe a blocked task as unclear requirements. The same report
said "five of nine" for a run that built six, so the tally is now appended
deterministically, the way the cost line already is.

**A conversational way in, and it is now the front door.** One question at
a time, composing FDR.md — the form asked for 4000 characters at once and
then, when the assessor returned five questions, asked you to edit the right
lines inside that textarea. Clarify rounds are capped, every turn offers
"that's enough, go to the plan", and skipping is a recorded answer: a loop
that asks until the model is satisfied is worse than a slightly
under-specified FDR. `--entry form` restores the old landing page.

**The wait before the first module is no longer blank.** Assess, brief, four
charter voters, leader, planning — the longest stretch of a run — narrated
nothing until tasks existed. Still no percentages and no ETA.

Smaller, all found the same way: the FDR form no longer silently overwrites
an FDR that changed under it (a stale tab destroyed five answered clarify
questions, and the assessor then asked them again); a failure names its real
cause instead of asserting a missing API key for everything; failures are
recorded to `.mas/studio-failures.jsonl`; the working page polls instead of
bouncing you to the page you just left; the interrupted page shows why the
worker died instead of only that it did; and `failed_hint` was defined twice
in the string table, so the "modules that did not build" card had been
telling founders their API key was exhausted.

## v0.61.0 — the enterprise you actually work at: GitLab, Bedrock, a proxy

Started by adopting one real enterprise data-pipeline repo (a BigQuery
spend optimizer on self-managed GitLab), then generalized from research
across the enterprise landscape — forge market reality, how enterprises
actually reach model APIs, what security questionnaires ask, and what
breaks behind TLS-inspecting proxies — so the fixes fit the scenario,
not the single example.

- **GitLab is a first-class forge.** Every `gh` side effect — PR comments,
  HITL issues, fix-PRs, head-branch lookup, diff acquisition, the
  policy-gated merge — now routes through a forge seam (`forge.py`) that
  dispatches on the target's URL shape: `/pull/<n>` → `gh` (github.com and
  GitHub Enterprise Server), `/-/merge_requests/<n>` → `glab` (gitlab.com
  and self-managed, subgroups included). The ADR-031 posture carries over
  verbatim: no `--admin`, no force flags, merge reachable only through
  `automation.evaluate_merge`. `avs review <MR-URL>` now works where
  enterprises actually host code.
- **The model door matches enterprise network reality.**
  `AVS_ANTHROPIC_MODE=bedrock|vertex` routes the same Messages API through
  AWS Bedrock or GCP Vertex; an internal LLM gateway authenticates with
  `ANTHROPIC_AUTH_TOKEN` (+ SDK-native `ANTHROPIC_BASE_URL`). Every door
  errors loudly on missing credentials — no silent fallback between doors.
- **`avs map` no longer reports the filesystem as an HTTP surface.** The
  hand-rolled-router heuristics (`path == "/..."`, `startswith("/...")`)
  matched filesystem-path literals all over brownfield code; mapping the
  pilot repo reported `/usr/bin/env` and `/opt/homebrew` as routes — a
  scanner that cannot be trusted on first contact. Heuristic matches whose
  first segment is a Unix/macOS filesystem root are now screened out;
  decorator-declared routes are untouched.
- **The perimeter that cannot expose an endpoint still gets reviews.**
  `avs review --from-ci` derives the target from the pipeline's own
  predefined variables (GitLab CI merge-request pipelines, GitHub Actions
  pull_request events) — the pattern locked-down enterprises actually use
  instead of webhooks. `avs serve` grew a `/webhook/gitlab` route
  (constant-time `X-Gitlab-Token` check — GitLab's design is a shared
  secret, not an HMAC; `update` events trigger only when they carry new
  commits, so metadata edits don't spam the MR). Azure DevOps and
  Bitbucket PR URLs are recognized and refused by name instead of falling
  through to `git diff` on a URL; CodeCommit (closed to new customers
  2024) is deliberately out.
- **The model door matches all four enterprise routes.**
  `AVS_ANTHROPIC_MODE=foundry` adds Microsoft Foundry (Azure) beside
  bedrock/vertex — model IDs stay platform-native and verbatim (ARNs and
  deployment names cannot be derived, so no auto-translation is
  attempted). The cross-family voter seats re-point too:
  `OPENAI_BASE_URL` (also the on-prem vLLM/NIM door), `XAI_BASE_URL`,
  `GEMINI_BASE_URL`.
- **Secrets can live in mounts, not process environments.** Every
  provider key and `secret://` reference accepts the Docker/K8s
  `<VAR>_FILE` convention; a configured mount that cannot be read errors
  loudly instead of running half-armed.
- **Egress is enumerated, and quieter.** The procurement pack gains
  [network-egress.md](editions/enterprise/procurement/network-egress.md)
  — every outbound host with the env var that re-points or disables it.
  The slopsquat check honors an internal PyPI mirror
  (`AVS_PYPI_JSON_BASE`), semgrep runs with `--metrics=off` and a
  pinnable local config (`AVS_SEMGREP_CONFIG`), and playwright moved to
  an opt-in `[screenshots]` extra so the base install never wants a
  browser download a firewall will block.
- **The Studio can be evaluated air-gapped, and its build worker no
  longer dies silently.** `avs studio --provider mock` walks the whole
  founder flow — clarify, plan confirmation, build with per-task
  narration, review, report — offline with a canned product. Found by
  driving the UI in a real browser: the Studio accepted a provider
  internally but the CLI never exposed it, and the spawned build worker
  didn't inherit it — a mock Studio spawned a build that wanted a real
  key and died with its output in DEVNULL, silently returning the
  founder to the confirm page. The worker now inherits the provider and
  writes `.mas/build.log`, so a worker that dies before the report
  leaves forensics.
- **`enterprise-web` joins the profile set** — the web profile plus the
  constraints an IT/security review actually asks about: append-only
  audit records on every state-changing action, `/api/health` for the
  load balancer, env-only configuration with `<VAR>_FILE` secret mounts,
  versioned JSON contracts for named integration consumers, and a
  no-node-assumed frontend stance. It reuses web's block library;
  add-only like every profile (edition_lint posture).
- **The founder's journey no longer ends at "works in this folder" — the
  production loop is in the Studio.** *Take it live* (`/live`): the exact
  boot command every verification used, the persistence story (local DB /
  the SERVICES.md cloud steps, generated on click), the deploy boundary
  stated where the button would be (avs never deploys on its own —
  ADR-031), an is-it-answering-now probe with a remembered last check,
  and the sweep role's housekeeping digest in plain language. *Is it
  broken?* on the product page: a founder sentence becomes a real
  incident — same Incident model, same triage/root-cause MAS, same
  artifacts as `avs triage` — reported back in plain language
  ("A likely cause was found" / "This needs a human"), with a one-click
  fix attempt whose click IS the human approval and whose change
  re-enters review like any PR. Found while wiring it: the probe ran on
  the event loop (a slow URL froze every Studio page — moved to the
  threadpool), and the studio wireup drift test caught two forms that
  rendered in no walked state.
- **`avs preflight` and the enterprise journey as a fixture.** The
  Ready-to-build check exists in the terminal too (`--strict` exits 1 on
  any gap — a pipeline can gate on enterprise readiness before spending
  a token), and the whole enterprise journey — adopt-with-gate-owner,
  readiness starter, substrate declaration, posture transitions,
  preflight truth-telling, every dashboard card — is pinned end-to-end
  in CI against a miniature of the real pilot repo (data-pipeline
  modules, FastAPI surface, filesystem-path traps, GitLab CI, an
  operator-owned CLAUDE.md), so enterprise mode cannot rot against
  exactly the kind of repo it was built for.
- **Enterprise mode opens with "Ready to build?"** — a six-row preflight
  read live from the environment, git config, the forge CLI's own auth
  check, and the workspace: model credential (mock escape hatch named),
  git identity, forge authentication, governance (edition + named gate
  owner), substrate declaration, Studio access posture. Ready rows show
  what was found; gaps show the exact fix command.
- **The Studio can be deployed for a team, fail-closed.**
  `AVS_STUDIO_TOKEN` (env or `_FILE` secret mount) gates every request —
  open `/?token=…` once, a cookie keeps the session; `avs studio --host`
  exists now and **refuses** a non-loopback bind without the token. The
  CSRF origin guard compares against the request's own host instead of
  hardcoded localhost (which would have rejected every form POST the
  moment the Studio served on a corp hostname). A deployment
  `Dockerfile` ships (non-root, git-only, fail-closed default command;
  not yet CI-built) plus the RUNBOOK's run-it-as-a-service section
  (docker + systemd, volume/backup note, OIDC-reverse-proxy as the SSO
  path — the token stays a shared secret by design).
- **The enterprise loop closes in the Studio: incidents from anywhere,
  evidence in one click, Gate 5 on the dashboard, housekeeping on
  demand.** The incident front door now also lives on /live (an adopted
  brownfield repo has no product page — and the substrate ladder's
  refusal renders in place when maintenance is below floor). The review
  timeline gained *Export the Gate-R evidence bundle* — same artifact as
  `avs evidence-bundle`, one click from the review it attests; a human
  still attaches it, the Studio never submits anything. The enterprise
  panel gained a Deploy reviews (Gate 5) card reading the same mirrors
  `avs deploy-review` writes — recommendations, never executions. And
  the housekeeping card gained *Run a housekeeping check*: the identical
  sweep pass as `avs sweep`, report-only at SW0, clean passes recorded.
- **Enterprise mode is a governance dashboard, not four dead ends.**
  Grounded in how mature enterprise consoles actually work (SonarQube's
  verdict-first gates, GitHub security overview's explicit "not enabled"
  state, Renovate's what-we-found onboarding) and tested by adopting a
  real 39k-line enterprise data-pipeline repo: a **posture line** now
  answers first — measured / not-yet-configured / needs-attention, and
  unmeasured never renders green; a **Model door & egress card** answers
  the security reviewer's first questions on screen (which provider mode,
  authenticated how — presence only, never a value — which forge,
  telemetry-sends-nothing, workspace spend from the ledger); a
  **Codebase card** renders the `avs map` comprehension report so a
  brownfield adoption's first screen proves the tool read the repo;
  empty states carry the exact command, what it changes, and the
  feedback loop ("this page re-reads the workspace on every reload");
  the governance card names the gate owner, not just the rule; and
  `init --adopt` now points at readiness/review/studio instead of
  telling a brownfield team to write a spec for a product that already
  exists. The Studio also stopped 404ing its own favicon.
- **Windows can't be killed by a health check anymore.** `os.kill(pid, 0)`
  liveness probes — which on Windows *terminate* the probed process —
  went through a cross-platform `procs.pid_alive`, worker detachment
  gained a Windows path, and the probe venv resolves `Scripts/` as well
  as `bin/`.

## v0.60.0 — a run that says what it is doing, and a failure that says why

Diagnosed from the durable bench scoreboard (`benchmarks/results/`), where
`build_failed` and `error` rows dominate runs 4–11. Every one of them named a
symptom the system already knew the cause of and had discarded.

- **A cut-off response is no longer indistinguishable from a complete one.**
  `stop_reason` was recorded only when the response text came back *empty* —
  the one case where nothing is at stake. A response truncated at the output
  cap returns partial YAML that usually still *parses* (a block scalar simply
  ends), so a half-written source file reached disk and its real failure
  surfaced an iteration or two later as an unrelated test error. All four
  adapters now record why the model stopped (`stop_reason` / `finish_reason` /
  `finishReason`) into a thread-local — thread-local because voters and
  parallel lane builds run concurrently.
- **The implementer refuses a truncated batch instead of writing it.** The
  build loop feeds the truncation back as named feedback ("return FEWER
  files"), and a task truncated on every attempt fails with the cap, the
  bytes received, and the remedy — split the task — rather than a generic
  test error. The implementer's cap also rose 16384 → 32000: the prompt
  permits 12 files of 500 lines and the old cap could not carry a third of
  that, so a task at the top of its own stated envelope was cut off by
  construction. 32000 is the Opus-class ceiling; more would 400 at request
  time. The planner gained the same guard (a truncated plan loses whole tasks
  silently, and `dag_check` cannot see it — a truncated plan is internally
  consistent) plus 4096 → 8192.
- **`build gate still failing after max iterations` now says what failed.**
  `BuildResult.test_summary` always held the reason; `TaskOutcome` dropped it,
  so outcomes.yaml, the bench scoreboard and the founder's report all showed
  the generic half alone. The cause now travels with the sentence, the outcome
  carries `iterations` / `files_written` / `test_summary`, and the bench row
  stops truncating a failure's detail mid-word.
- **`implementer returned no files` distinguishes its three causes** — an
  empty `files:` list, a batch discarded wholesale as weakened skeletons, or a
  model that narrated instead of answering — by keeping the response opening,
  the way the voter seat already does.
- **A task in flight is observable.** New `upstream/progress.py`: an
  append-only step journal (`.mas/progress.jsonl`) written as each step
  actually starts. A task used to sit at `pending` through spec, five charter
  critics, up to three build iterations and six review voters — most of a
  run's wall-clock, indistinguishable from frozen. `avs create` now narrates
  live (it printed nothing at all between start and a report an hour away),
  the Studio's per-task line shows the current step, and a failed module
  prints its cause in the summary. Observed, never predicted: no percentages
  and no ETAs, because the system genuinely does not know whether iteration 2
  of 3 will be the last. The journal is a record and never an input — an
  unwritable one cannot fail a build.

Shipping alongside it, the other half of the same question — the run also says
what it *cost*:

- The founder's `BUILD-REPORT.md` gains a "What this cost" section, appended
  as arithmetic rather than prompted, so the number is never model prose. It
  names the typical and worst run once there is more than one to compare, and
  states plainly that the spend is on the operator's own API key.
- The Studio's product page carries the same line, read from the same ledger
  `avs cost` reads — the Studio stays a veneer, never a second source.
- `avs create` prints it when the run ends.

The cap stays off by default. Transparency is the always-on behaviour; the
limit only exists if somebody wrote a number, and says so when it fires.

## v0.59.1 — cost transparency is the point; the cap is opt-in and secondary

Correcting v0.59.0's emphasis. The founder signal asked to SEE the number —
"how much will a typical month of builds cost me? I'm scared to leave
autopilot running" — not to be stopped, and users spend their own keys.

- `summarize` / `by_model` / `summarize_workspace`: calls, tokens and money
  for a run, a month, or a model.
- `render_plain` is the founder-facing sentence, in the register that asked:
  no token counts, no model names. "at least $X" when unpriced calls make the
  figure a floor, and "unknown, here is how to fix it" when no prices are
  configured — printing "$0.00" would be a lie about someone's money.
- `read_entries(since=…)` answers "what did THIS run cost" without threading
  attribution through every call site.
- `typical_and_projected` reports the MEDIAN run beside the worst run,
  because agentic spend has a fat tail and one runaway loop makes an average
  useless. Runs are inferred from ledger gaps, stated as the heuristic it is.
- The cap message now reads as the operator's own standing decision: it is
  off by default and only exists because somebody wrote a number.

## v0.59.0 — the cost gate: spend is measured, and the cap is real

observability.py could price a call, total a month, and compare against a cap
— and none of it was reachable, because nothing ever *recorded* a call.

- Recording at the provider adapters, where usage exists; `Provider.chat`
  still returns `str`. Recording never raises: a metering failure must not
  take down the work being metered.
- Persisting to append-only `.mas/spend.jsonl`, flushed on every review exit
  path including the Gate 1 bail, and between build tasks. Lock-guarded,
  because voters run in a thread pool. A truncated row is skipped, not fatal.
- Gating before the spend — Gate 1 refuses a review, `run_build` refuses a
  task — with the previous task's spend flushed first, so a cap cannot be
  discovered only at the end of a seven-task run. `avs cost` reports a month.
- Unpriced calls are never counted as zero (the total reports as a floor);
  an unconfigured cap says it checked nothing rather than passing silently.

## v0.58.0 — the MVP contract, and the AI delta on top of it

The system could make a build smaller but never asked whether the slice, on
its own, tells you if the thing is worth building. Doc 13 specified exactly
that rule and it had never been implemented.

- `avs mvp` + `product/mvp.py`: doc 13's rule (a hypothesis the increments
  can actually settle, matched on 4-char stems plus CJK bigrams so
  "progressing" agrees with "progress" and a Chinese FDR is not silently
  exempt), plus the canon requirements the schemas let default to empty and
  a refusal of "build it and see" as a cheapest test. `thin` IS the MVP tier.
- The AI delta, fired by AI-shaped language in the founder's own words:
  a named simpler alternative, a declared cost of being wrong (irreversible
  may not act; expensive may suggest but not take), a wrong-answer fallback,
  >= 20 eval cases authored first, and a quality metric paired to every
  volume metric. These hold at every tier.
- Gate PL2 carries scope_tier into the build (nothing bridged handoff and
  planner before); `test_first` blocks `avs prd` instead of being a string
  nothing read; the thin task cap became deterministic.

## v0.57.1 — a founder never meets "Internal Server Error"

Found by watching a real founder hit it. `/fdr`, `/feature` and `/correct`
run LLM calls for minutes and had neither an in-flight guard nor error
handling, while `/build` and `/retry` had both.

- A failure renders a page: what stopped, that nothing was lost, the likely
  cause in plain language, the real exception one click away.
- A second submit while thinking returns "working on it" rather than racing
  two autopilots over one workspace.

## v0.57.0 — the six gaps from the research/comprehension audit

- `avs init` no longer overwrites an existing CLAUDE.md.
- The Context Manifest reaches the writer instead of only auditing the
  prompt — `render_manifest` was dead code, and its absence is why sibling
  tasks drifted onto different routes.
- `avs map` / `avs init --adopt`: the brownfield entry path. Languages,
  entry points, modules, real import edges, HTTP surface and test locations
  derived from the code; `--write-deps` emits the baseline
  `arch_contract_check` never had.
- Charter voters get the tools they declare (~40 seats ran tool-less);
  the phantom `repo_capability_probe` is gone.
- `--tier thin` reaches the planner.
- `avs probe`: the quarantined fetch the docstrings had promised, plus
  ADR-U03 taint isolation finally wired to the host.

## v0.56.1 — the founder demo is a recorded real run

`docs/media/studio-flow.gif`: seven frames from one real build against a live
provider. That run failed some modules, so the report frame reads "partly
built" and the caption says so; a test pins the honesty phrases. Also fixes
`avs studio <dir>` — the positional form every doc showed was the one form
that could not work — keeping `--repo-dir` behind a deprecation notice.

## v0.56.0 — per-mode UIs: each persona gets its organizing surface

Researched before built: the design canon (doc 24's persona constraints,
the solo weekly-review agenda, invariants 14.21/14.22), the adaptive-UI
literature (Findlater & McGrenere CHI 2004: adaptable beats adaptive;
NN/g on visible modes and progressive disclosure), and an inventory of
every `.mas/` surface the CLI writes that no Studio route rendered.

- **The mode is adaptable per request, never adaptive.** A visible
  switcher on every page — including founder — sets `?mode=` and a
  cookie; `--mode`/edition only supply the default, and the system never
  flips the mode behind the user's back. An unknown `?mode=` is a loud
  400, same policy as an unknown `--mode`.
- **Engineer mode** gains the review machinery: a bounded newest-first
  recent-reviews table (the server's `/reviews` pattern — no unbounded
  scans per page load), a per-review timeline page at `/review/<id>`
  rendering the same `NN-<node>.yaml` mirror `avs replay` reads, and a
  voter-health board (runs · blocked · substituted) from
  `.mas/voters/*/log.yaml` — previously visible only inside the weekly
  compound proposal.
- **Enterprise mode** stops counting and starts verifying: the
  attestation card now recomputes the sha256 chain (`verify_ledger`) and
  renders tampering as BROKEN-at-entry-N; plus the S0–S4 stage-activation
  grid with the exact missing prerequisite per inactive stage, the F-18.3
  gate-dwell/rubber-stamp report with its own notes verbatim, and
  automation policy arming state (disarmed-by-default rendered as the
  good news it is; an armed policy shows who armed it and until when).
  The governance spokes render even without an edition file — a workspace
  still has a ladder, a dwell distribution, and an arming state.
- **Founder mode** stays the plain flow plus the switcher, and gains its
  correction history (`product/CORRECTION-LOG.md` was written on every
  correction and rendered nowhere).
- The wire-up gate grew with the UI: query-string references route by
  path, and the engineer/enterprise states joined the state walk — the
  bidirectional every-button-resolves / every-route-rendered contract
  now covers the mode cards too.
- Every new card is a pure read of files the CLI already writes; the
  Studio stays a veneer. LLM-calling and subprocess paths stay out of
  GET handlers.
- Suite: 1092 → 1112.

## v0.55.0 — Studio modes: different users, different depths of the same UI
- `avs studio --mode founder|engineer|enterprise`. The editions system
  (doc 24, ADR-U26/U27) already encodes who is at the keyboard; the Studio
  now reads it — no flag needed, the mode resolves from the workspace's
  `.mas/edition.yaml` (solo→founder, engineer→engineer,
  enterprise→enterprise), and `--mode` overrides per launch.
- Founder mode is the existing UI unchanged, and stays the default: with no
  edition and no flag, the page is byte-for-byte what it was.
- Engineer mode appends a build-internals card to every page: each task
  with its CLI-usable ID and verbatim recorded state (`spec_blocked`, not a
  euphemism), the workspace profile, and the command equivalent of every
  button (`retry-task`, `preview`, `walkthrough`, `verify`).
- Enterprise mode appends a governance card read from the resolved edition:
  substrate rung, WIP limit, gate-owner rule, never-batched gates, and the
  attestation-ledger entry count — with "no ledger yet" stated rather than
  omitted, because a missing ledger must not read as attested-and-clean.
- The mode contract is the editions' own rule read UI-side (invariant
  14.21): a mode may only ADD visibility. Tested structurally — every form
  action and link the founder page renders must appear in every other mode.
  An unknown `--mode` is a loud startup error, same policy as a missing
  i18n string; a corrupted edition file falls back to founder rather than
  taking the Studio down.
- `__init__.__version__` synced (it had been stranded at 0.12.0 since the
  packaging split; nothing reads it, but a wrong number is a wrong number).
- Suite: 1071 → 1092.

## v0.54.1 — packaging: every runtime resource now ships in the wheel
- **0.54.0 was broken for pip users and is yanked.** Publishing it and then
  installing it FROM PyPI is what found this: `avs init --profile web` — the
  first command any new user runs — failed with `unknown profile 'web';
  available: []`, and `avs replay --demo`, the README's no-API-key headline,
  crashed on a missing directory.
- The cause was systemic, not a one-off: ten data paths resolved through
  `parents[N] / "profiles"`-style repo-root arithmetic, which points at
  `site-packages/../../profiles` once installed. `paths.py` had documented
  exactly this trap when `skills/` was fixed, and the rest of the class was
  never swept.
- `profiles/`, `blocks/`, and the edition presets + offline demo bundle now
  live inside the package (`edition_data/`, renamed to avoid colliding with
  `editions.py`), with repo-root symlinks so humans and docs keep the familiar
  paths. Every consumer resolves through `paths.py`.
- Development-only data (benchmark corpora, test fixtures) is deliberately
  still NOT shipped — a wheel should not carry a benchmark corpus — so
  `repo_data()` returns None and callers can say "needs a checkout" instead of
  reporting an empty corpus as a result.
- Verified the way it should have been the first time: installed from PyPI
  into a clean venv and ran `init --profile web --edition solo` and
  `replay --demo` end to end.
- The lesson recorded, because the local build passed every check: a wheel
  test that only runs `--help` proves the entry point resolves, nothing more.
  Packaging bugs live in the resources, so a smoke test has to touch them.

## v0.54.0 — package and CLI renamed: `ai_venture_studio`, command `avs`
- Release plumbing: `version` in pyproject was 0.33.1 while the CHANGELOG had
  reached v0.54.0 — the two now agree, because publishing a number that
  disagrees with its own release notes is worse than not publishing.
- `.github/workflows/publish.yml`: tag-triggered PyPI release via Trusted
  Publishing (OIDC, no API token anywhere). It runs the full suite on the
  tagged commit, refuses to publish when the tag and pyproject version
  disagree, runs `twine check`, and only then uploads — because a published
  version cannot be replaced, only yanked.
- Artifacts for 0.54.0 are built and verified locally: `twine check` PASSED on
  both, and a clean-venv install of the wheel runs `avs`, the `autoproduct`
  alias, and a real command. The UPLOAD is not done: it needs a credential
  only the account owner holds (see RUNBOOK → Releasing to PyPI).
- Import package `autoproduct` → **`ai_venture_studio`** (874 references
  across 268 files), distribution `autoproduct` → **`ai-venture-studio`**,
  and the CLI command is now **`avs`**. `autoproduct` stays as a console-script
  ALIAS so anything scripted against it — cron entries, CI steps, shell
  history — keeps working; docs use `avs`.
- **Compatibility contracts were deliberately NOT renamed**, each with a
  comment in the code saying why:
  * `AUTOPRODUCT_*` env vars (19 of them) — renaming breaks every existing
    deployment's configuration.
  * `autoproduct_reviews_total` / `autoproduct_errors_total` Prometheus
    series — renaming a metric silently breaks every dashboard, alert and
    recording rule built on it. A real rename needs a dual-emit window.
  * the `autoproduct_version` telemetry field — a wire key consumers parse.
  * `## Learned constraints (autoproduct)` in CLAUDE.md — this string matches
    the header already written into existing workspaces; changing it would
    make the compounding loop append a duplicate section instead of updating
    its own.
- **Two real breakages the rename caused, both found and fixed:** the
  root `skills` symlink still pointed at `src/autoproduct/skills` (21 tests
  failed until it was repointed), and `version("autoproduct")` in the
  telemetry payload raised `PackageNotFoundError` under the new dist name —
  now tries the new name and falls back to the old.
- Honest install line: `ai-venture-studio` is not on PyPI yet, so the README
  still tells you `pip install autoproduct` and says the rename lands with
  the next release rather than instructing an install that would fail.
- Recorded evidence untouched again: the demo review audit trail cites
  `src/autoproduct/*` files because that is what it reviewed.
- Suite: 1071 tests, unchanged and green.

## v0.53.0 — renamed to ai-venture-studio; English is the UI default
- **Repository renamed** melodygaoyifan/ai-product-autopilot →
  **ai-venture-studio**. Live references updated (pyproject URLs, README,
  launch post, design canon links). GitHub redirects the old URLs, so
  existing clones and links keep working.
- **Recorded evidence was NOT rewritten.** The Gate PL5 and experiment-run
  records cite `gh api repos/…/ai-product-autopilot` with a `retrieved_at`:
  those are evidence snapshots, and an evidence snapshot is not edited after
  the fact (the same rule that makes the attention log append-only). Each now
  carries a note that the repo was renamed on 2026-07-27 and that
  re-measurements use the new name.
- **English is now the Studio default** (`DEFAULT_LANGUAGE = "en"`). The UI
  began Chinese-first because its first users were 小程序 founders; the repo
  is public and English-speaking, so the default now matches the audience
  that meets it first. `--lang zh` restores the original bilingual UI
  character for character — the strings were not touched, only the default —
  and a test asserts exactly that so the move is not a quiet degradation.
- The FDR template follows the same default, and the Chinese-founder tests
  now ask for `lang="zh"` explicitly rather than relying on a default, with
  new tests covering the English default path end to end.
- The package and CLI are still named `avs`: renaming those would
  break every existing install, and that is a separate decision.
- Suite: 1068 -> 1071 hermetic tests

## v0.52.0 — the Studio speaks English, and the README demo shows it
- `avs studio --lang en` renders the entire flow in English. Every
  user-facing string moved to `studio_i18n.py` keyed by language, and the
  FDR template gained an English twin asking the same six questions.
- **What made this necessary:** the README's founder demo claimed an
  English-or-Chinese product while the screenshot showed
  `写下你的产品需求 / Describe your product` — the UI was bilingual
  Chinese-first with no way to opt out. A demo that claims English and shows
  Chinese is a false claim about the product, not a cosmetic gap.
- `zh` (the default) keeps the original bilingual strings character for
  character, and a test asserts an unset language renders byte-identically
  to before. Existing users see no change; the flag is additive.
- An unknown language falls back to the default rather than rendering blank
  labels: a working UI in the wrong language beats a broken one in none.
  Codes normalise, so `EN`, `en-US`, `en_GB` all work.
- The README founder section is now English-first — web profile, an English
  FDR shown inline as the actual input, and a REAL screenshot of the real UI
  captured through Playwright at `docs/media/studio-en.png` (not a mockup;
  the Chinese screenshot stays linked for 小程序 founders). A test pins the
  README, the flag, and the image together so the demo cannot drift from the
  product again.
- Also honest now: the profile list in the README names all five profiles
  rather than three, and glosses the WeChat terms in English.
- Suite: 1060 -> 1068 hermetic tests

## v0.51.0 — a second kill-criterion axis, chosen by the human, evaluated by the machine
- The launch PRD now carries TWO axes. The new one (O-L2, capability
  regression) fires if the product-bench build rate falls below 60% OR the
  probe pass rate below 50% for 2 consecutive weekly runs.
- **Why this axis and not another:** its series already exists.
  `benchmarks/results/*.yaml` records build/probe/clean rates per run and the
  cadence is weekly, so this criterion can fire on the NEXT run — while the
  attention axis cannot fire until four consecutive weeks are logged. A
  second axis that also cannot fire would have added no coverage.
- **The floors are read, not chosen.** Runs 4-5 sat at 8-33% build, runs 6-9
  climbed 42-72%, runs 10-11 hold 74-75%. 60/50 sits below the current level
  and above the pre-fix era: crossing it means regressing into territory the
  system already climbed out of once. Two runs rather than one because at
  n=4 cases a single dip is noise, and a criterion that cries wolf gets
  ignored.
- `avs bench-criterion` evaluates it, and `avs loop` now
  reports both axes with their real readings in one line. Either firing
  demands a recorded human decision at Gate PL5 (invariant 14.20); neither
  evaluator decides.
- metrics/product_bench_capability.md defines the series, its exclusions
  (harness-noise runs, corpus changes that reset comparability), and its
  falsifier.
- **The PRD linter caught me:** the outcome instrumented
  `product_bench.run_recorded` while the metric counts
  `product_bench.case_built`, so P4 would have read zero. That is precisely
  the class of bug prd_lint exists for, and it fired on its own author.
- Suite: 1048 -> 1060 hermetic tests

## v0.50.0 — `loop` and `attention` now answer one question together
- `avs loop` reads the attention streak, so the v3.0.0 gate report
  states the real distance to firing ("2/4 consecutive logged weeks over
  4.0h; 2 more would fire it") and the real next action ("log last week:
  `avs attention --week 2026-W31 …`") instead of a static "the
  criteria need data that does not exist yet". Two commands shipped in
  separate releases were leaving the operator to join them by hand.
- A `not_tracked` row is reported as itself: it is a RECORDED decision, not
  a gap, so `loop` says the run "starts from the next week you log" rather
  than claiming last week is logged (which the first cut did) or asking for
  a rewrite of the record.
- When the criterion has fired, the next action becomes the decision —
  and the gate still does not close on it. Only a recorded human
  kill-or-pivot does (invariant 14.20), which the existing tests keep true.
- Absent log: the static wording, unchanged. Unreadable log: reported as
  unreadable rather than as a streak of zero.
- Suite: 1043 -> 1048 hermetic tests

## v0.49.0 — the use-case matrix, and the gap it found
- New `tests/test_use_case_matrix.py` tests the canon's coverage CLAIMS as a
  matrix instead of trusting that each part works because its own unit test
  passes: five domain profiles spec-and-build end to end, three editions
  resolve and lint narrowing-only, and the five-rung substrate ladder
  activates exactly the stages its floors allow.
- **The gap it found:** `STAGE_FLOORS` declared floors for eight stages, but
  only `code_review` and `deploy_review` ever consulted them. So doc 18's
  "stages below their infrastructure floor are inactive-never-degraded"
  (ADR-U15) was unenforced for six stages — an S0 team with no git could run
  `build`, and `triage` ran with no observability configured. discover,
  plan, spec, build and triage now enforce their floors with the same
  exit-code-4 refusal, and both directions are pinned: refused BELOW the
  floor, and never refused AT it (a guard that blocks legitimate work is
  worse than no guard).
- Recorded rather than smoothed over: `deploy_review` is the designed
  exception — above S0 it DEGRADES to config-lint-only instead of going
  inactive, because a config lint still helps without progressive delivery.
  A test pins that too, so the asymmetry stays deliberate.
- An absent `.mas/substrate-profile.yaml` still gates nothing, so no
  existing workspace starts refusing work because this exists.
- Suite: 1012 -> 1043 hermetic tests (+3 skips: stages with an S0 floor have
  no rung below them to be refused at)

## v0.48.0 — upstream resume, grounding at the spec writer, and the plan closed out
- **Upstream resume (gap-plan item 15's second half).** A task is the
  expensive unit upstream — spec + build + review, minutes and real money
  each — so autopilot now persists each outcome AS it completes and a re-run
  skips tasks already built on disk. Honestly labelled: this is
  task-granular, not super-step-granular like the review graph. A task
  interrupted halfway restarts that task; the ones before it stay done.
- `outcomes.yaml` is treated as a record, not an authority: an outcome
  claiming `built` is honored only when `built_task_ids` agrees the spec is
  actually built. A stale record can never make a run skip work that is not
  there.
- **Grounding now gates the SPEC writer too**, not just the build writer —
  the v0.42 asymmetry was arbitrary. Same finding as last time, one stage
  earlier: the spec writer never saw CLAUDE.md or the module invariants, so
  it could author a criterion contradicting an invariant, producing a build
  that cannot satisfy both and a SPEC_DRIFT flag against work nobody could
  have got right. Both now reach the prompt verbatim, and a spec authored
  blind to them raises rather than returning a weak spec.
- docs/gap-closure-plan.md reflects reality: every phase closed, item 15
  marked with both halves and their differing guarantees, and the
  "recorded non-goals" list corrected where later ADRs reversed it.
- Suite: 1008 -> 1012 hermetic tests (one end-to-end resume example, not a
  battery)

## v0.47.0 — the bot fleet: the game profile's last unbuilt check
- `avs botfleet` runs N parallel bot sessions and triages what they
  hit: crashes, softlocks, unreachable-state regressions, out-of-bounds
  positions, and errors. Findings dedupe by signature across sessions and
  each carries a reproduction command — a bug a fleet found that cannot be
  replayed by hand is not actionable.
- **The design decision that made this shippable without an engine:** the
  fleet is defined by a session PROTOCOL (newline-delimited JSON events), not
  by a game. So the detectors are real functions over a real stream, verified
  against real subprocess sessions of a real deterministic simulation
  (`benchmarks/botfleet/toy_sim.py`, which is also the reference emitter an
  engine adapter copies). Wiring Unity or Unreal is now an adapter, not a
  redesign — and that adapter is the honest remaining open item.
- **Bug the first real run found:** one escaping bot produced 44 findings,
  because the out-of-bounds signature included the per-tick state hash. A
  continuing condition is now one finding per session, and the signature
  names which axis and side left the play area rather than how far along it
  the bot got. This is exactly what a stubbed stream would not have shown.
- Honest by construction elsewhere too: a hung session is a crash rather
  than a hang, a non-zero exit with no crash event is still a crash, an
  unconfigured or unrunnable command is a VISIBLE skip ("never counted as a
  clean overnight run"), and an undeclared netem profile is an error naming
  the declared ones.
- Scope, per §45.1: the fleet finds crashes and stuck states. A clean report
  says so explicitly — whether the game is FUN is the human playtest gate's
  question, and no bot replaces it.
- Suite: 982 -> 1008 hermetic tests

## v0.46.0 — the attention collector: making the v3.0.0 criterion able to fire
- `avs attention` measures the OBSERVABLE FLOOR of weekly
  maintenance attention from ledgers `.mas/` already writes — gate dwell
  (escalate→final, the same measurement the rubber-stamp detector uses),
  recorded product-gate decisions, sweep reviews — and prints it with the
  artifacts it came from.
- **The machine never sets `hours`.** A floor is not a total: reading a
  review without touching a gate, thinking, and answering questions all
  count toward attention and leave no timestamp. So `hours` and
  `status: logged` stay human, `--by` is required (a number in this series
  has an author), and the floor is recorded BESIDE the human's number rather
  than instead of it.
- Append-only, enforced: an existing week is never rewritten, the log's
  header comment survives appends, and a malformed log errors rather than
  starting a fresh one.
- The streak reader implements the log's own rule — an untracked week breaks
  a streak without counting either way, and exactly-at-budget is not over
  budget. When four consecutive logged weeks exceed the budget the command
  exits 3 and says Gate PL5 now needs a recorded human decision. It does not
  make one.
- Why this was engineering worth doing: the v3.0.0 blocker was never "wait
  four weeks", it was that logging was a manual habit whose lapse silently
  reset the clock the criterion depends on. The habit is now cheap; the
  decision stays yours.
- Suite: 961 -> 982 hermetic tests

## v0.45.0 — the deploy-side CLI wrappers complete the §17.2 table
- terraform_validate, helm_lint, kubectl_dry_run, argocd_app_diff,
  flagger_inspect, railway_inspect join the L1 `deploy` partition. This is
  the table's other integration shape: BINARIES gated on being installed,
  following the pattern tools/external.py set for the scanners — an absent
  binary is a visible skip with the install hint, never counted as clean.
- `kubectl_dry_run` defaults to `--dry-run=client`, which never contacts a
  cluster. Server-side dry-run is real admission validation and more
  useful, but it talks to whatever cluster the current kubeconfig points
  at, so it is opt-in per call. A deploy review that silently reached into
  production because a context happened to be current is exactly the
  surprise this design spends its budget avoiding.
- Read-only is structural, not documentation: no wrapper names sync,
  rollback, up, redeploy, patch, delete, or destroy, and `apply` appears
  only behind `--dry-run` — asserted against the module's own source.
- Semantics that matter: argocd exits 1 when a diff EXISTS, so that is
  findings rather than an error, while auth failures and missing apps are
  errors; flagger flags unhealthy canaries but never patches one, because
  promoting or aborting a canary is a human's call.
- **Bug the tests found:** terraform with no parseable verdict (typically an
  uninitialized directory) reported "findings: 0 diagnostic(s)", which reads
  like a pass. A non-answer is now an error naming the likely cause.
- migration_dryrun from the §17.2 table was already covered by
  lanes.delivery.migration_rehearsal, so it was not duplicated.
- Honest scope: hermetic via a stubbed subprocess boundary. None has run
  against live infrastructure from this repo (no cluster, no cloud
  credentials); first real invocation per tool stays an open item.
- Suite: 932 -> 961 hermetic tests

## v0.44.0 — all six §17.2 signal readers, over one shared core
- datadog_query_metrics, pagerduty_get_incident, prometheus_query,
  loki_query, jaeger_query_trace join sentry_get_issue in the L1
  `maintenance` partition. Sentry's shape became a shared read-only core
  (gating, `secret://` resolution, GET-with-no-body, summarize, wrap,
  multi-secret scrub, errors-as-data), so each reader is its endpoint and
  its summary and nothing else.
- **Two gating families, deliberately distinguished.** A hosted service is
  gated on its credential; a self-hosted one on its base URL, because there
  is no sensible default address for a Prometheus and defaulting to
  localhost would turn "not configured" into a confusing connection error.
  Either way, unconfigured means a visible skip naming the exact variable.
- Details that are the point rather than decoration: Datadog requires BOTH
  keys and an explicit window (a metric read whose window nobody stated is
  not evidence); PagerDuty is read-only so it cannot ack, resolve, or
  reassign — the on-call human owns those; Loki's limit is bounded and its
  log lines get the same wrapper as everything else, which matters most
  there because log lines are the most user-influenced text in the stack;
  both Datadog keys are scrubbed from one payload.
- Honest scope, unchanged: written against each vendor's documented REST API
  and exercised against a stub transport. None has run against a live
  account from this repo — no credentials exist here — and the map says so
  per tool.
- Suite: 908 -> 932 hermetic tests

## v0.43.0 — the first external-service tool: sentry_get_issue
- maintenance/signals.py: reads one Sentry issue over the documented REST
  API, served by the L1 `maintenance` MCP partition. Adding it needed a row
  in the partition table plus a reader module — no transport, host, or RBAC
  change, which is what v0.40 claimed and this checks.
- House rules, all enforced by test: the credential is `AUTOPRODUCT_SENTRY_TOKEN`
  (raw or a `secret://ENV` ref through the v0.31 layer) and a configured-but-
  unresolvable ref errors rather than going unauthenticated; no token is a
  VISIBLE skip naming the env var, never an empty result, because "never
  asked" must not read like "nothing found"; the reader is read-only (the
  request builder sends no body and names no write verb, asserted on its
  source); the payload arrives `wrap_research`-wrapped, so a hostile issue
  title is data and consuming it taints the run out of L1+ (ADR-U03).
- Wired end to end: the Sentry webhook now passes the issue id through as
  `external_id`, and the maintenance graph gained a `signal` step that
  enriches a Sentry-sourced incident before root-cause analysis and records
  the wrapped payload in the mirror. A manual incident never calls out.
- **Bug the suite found:** substring-scrubbing the token shredded any payload
  containing its characters — with a 1-character token, everything. Scrubbing
  now has a length floor, because mangling a payload is worse than not
  scrubbing a string too short to be a credential.
- Honest scope: exercised hermetically against a stub transport. It has NOT
  been run against a live Sentry org here — no credential exists in this
  repo to do that with, and the module docstring says so instead of implying
  coverage it lacks.
- Suite: 891 -> 908 hermetic tests

## v0.42.0 — grounding enforced on every build, and the gap it found
- The Context Manifest is now wired into the build writer, not just
  available: every build assembles a manifest, records it at
  `.mas/manifests/<slug>.yaml`, and BLOCKS when a required entry's content
  never reached the implementer's prompt. Overflow blocks too, reported as
  a Planning split proposal rather than trimmed.
- Receipts for pushed context: `grounding_receipts` probes the prompt for
  each entry's most distinctive line instead of trusting a model's
  self-report. This checks assembly, not attention — it cannot prove the
  model read what it was handed, and the docstring says so.
- **The gap it found on the first run:** module-spec invariants
  (`.mas/specs/*.spec.yaml`) were never in the implementer's prompt, even
  though Code Review enforces them and flags SPEC_DRIFT_UNDOCUMENTED. The
  implementer was being held to a contract it had not been shown.
  Invariants and forbidden side effects now ship in the prompt, quoted
  VERBATIM — the probe requires the contract text itself, and paraphrasing
  a contract into a prompt is the smell the check exists to catch.
- Modeling correction: `spec.md` renders `spec.yaml` for a reader, so it is
  optional rather than a second obligation; requiring both fired a
  violation over a heading the machine contract never had.
- Suite: 886 -> 891 hermetic tests

## v0.41.0 — the ContextAssembler and research-session taint isolation
- upstream/context_assembler.py (§13.25.2, §13.29.3, §13.35.5): builds a
  task's Context Manifest deterministically under a token cap — spec slice
  first, code neighborhoods last, every entry content-hashed. Three
  mechanisms that only work together:
  * grounding receipts — `verify_sources_read` checks a writer's
    `sources_read` against the manifest; unread required context, a hash
    mismatch, or a claimed read of something unlisted are CONTRACT
    violations (§11.18.3), not quality notes;
  * drift detection — re-hashing catches a human editing a frozen artifact
    mid-flight, and `run_build` now refuses to build an unratified fork,
    naming the retro-SCR path instead of fighting the human (Gate U3
    pins a contract hash at approval; specs approved before v0.41 have no
    receipt and are treated as clean);
  * overflow as a planning defect — a task whose REQUIRED context exceeds
    the cap returns TASK_BLOCKED_CONTEXT_OVERFLOW with a split proposal
    rather than quietly compressing the contract.
- harness/taint_guard.py (§13.31.2, ADR-U03): the session-level enforcement
  the taint classes always assumed. `wrap_research` marks fetched content as
  data (and neutralizes a nested closing tag, so hostile content cannot
  close the wrapper and speak as the host); a run that consumes research is
  tainted one-way and loses L1+ tools for the rest of its life. Enforcement
  sits at the MCP transport where v0.40's risk tiers live, so the denial
  does not depend on anything the model says: L0 reading still works, L1/L2
  and unclassified tools are refused, and the refusal lands in the audit
  ledger. Taint arrives from tool OUTPUT, not declaration.
- Suite: 866 -> 886 hermetic tests

## v0.40.0 — the L1/L2 MCP partitions + the deploy-branch fix
- Three more partitions from doc 11 §17.2, for the tools that exist:
  `deploy` (L1: migration/workflow/canary probes), `maintenance` (L1:
  recent_commits, correlate), `test_exec` (L2: run_tests, which executes
  repo code — §17.2's reason for isolating it hardest). Five real servers
  now; the table's external-service tools (terraform, sentry, datadog)
  stay unbuilt and named as open rather than stubbed.
- Risk-tier RBAC at the transport: each partition declares L0/L1/L2 and
  MCPHost refuses to mount one above the caller's `risk_ceiling`, so a
  read-only voter cannot reach L1/L2 even if a future skill names one of
  their tools — enforced where the connection is made, not in a prompt.
  `MCPToolBox` also intersects with the L0 registry, so a voter allowlist
  cannot grow into stage tools by accident.
- Audit coverage now includes the tools that touch the most: deploy probes
  and test execution were previously unaudited.
- Fix (v0.39 follow-up): the deploy review now records the branch it
  covers (PR head branch, or the checked-out branch for a local range;
  empty on detached HEAD). `deploy-execute` and `automerge` treat an
  unresolvable branch as a REFUSAL — the old `or "main"` fallback would
  have let an armed policy act on work it was never armed for.
- Suite: 856 -> 866 hermetic tests

## v0.39.0 — policy-armed merge and deploy execution (ADR-031)
- `avs automerge <review-id>` and `deploy-execute <id>`: the
  capabilities the README listed as out-of-scope now exist, DISARMED. A
  human arms them per repository in `.mas/automerge-policy.yaml` /
  `.mas/deploy-exec-policy.yaml`; the system's job is to refuse unless
  every declared condition mechanically holds.
- The bounding, which is the actual work: absence is never permission
  (`enabled: false` is the default inside a present file too); branch
  globs are refused so a policy cannot arm what it does not name;
  `armed_by` and `expires_at` are required and an expired policy is a hard
  error; a minimum track record of correct recommendations must exist
  before the first automated action; only APPROVE/APPROVE_WITH_NOTES may
  precede a merge and an escalated review's decision stands; migrations,
  IaC, Dockerfiles, CI workflows, k8s/Helm, CLAUDE.md, `.mas/`, and the
  policy files themselves always demand a human — so automation can never
  widen its own permissions; `deploy-execute` runs only the exact argv a
  human wrote, never one the system composes; no `--admin` escape, so
  branch protection wins.
- `.mas/automation-log.jsonl` records actions AND refusals with reasons:
  "why didn't it merge" deserves the same answer quality as "why did it".
- CLAUDE.md's invariant revised to match the code, and ADR-031 records the
  reversal with its mechanism. Auto-hotfix stays out entirely.
- Suite: 817 -> 856 hermetic tests (36 of the 39 new ones assert refusals)

## v0.38.0 — multi-tenant server mode (ADR-030) + the ADR directory
- One `serve` process may now front several isolated workspaces:
  `.mas/tenants.yaml` maps a tenant id to a SHA-256 token hash and a
  workspace root; `avs tenant add|list` manages it and prints the
  plaintext token exactly once.
- Isolation is the mechanism, not the aspiration: workspaces must be
  disjoint (a root contained in another fails at LOAD time), the token
  picks the workspace and no client-supplied path or id ever does,
  per-tenant GitHub secrets are `secret://ENV` references so one tenant's
  secret cannot verify another's deliveries, read routes (/jobs, /reviews)
  require the token in multi-tenant mode, and unknown/disabled/missing
  tokens answer identically so responses never enumerate tenants.
- Security fix found on the way: `review_id` was interpolated into a
  filesystem path unvalidated. Now `[A-Za-z0-9_-]{1,64}` — in multi-tenant
  mode that was a traversal into a neighbour's workspace.
- Single-tenant mode is byte-for-byte unchanged: no registry, no
  multi-tenancy, shared-secret path and open localhost reads as before.
- docs/adr/: the implementation's own decision records, starting with the
  three that REVERSE a recorded non-goal (029 MCP transport, 030
  multi-tenant, 031 policy-armed automation). A scope reversal that lives
  only in a commit message is indistinguishable from scope creep. Closes
  the map's "ADR docs" open item.
- Still out: SaaS — billing, plans, quotas, a shared database, a hosted
  control plane, per-tenant key management. Tenants bring their own keys.
- Suite: 795 -> 817 hermetic tests

## v0.37.0 — MCP as the internal tool transport (doc 11 §17), first real slice
- autoproduct/mcp/: JSON-RPC 2.0 over stdio (newline-delimited), two real
  partitions from doc 11 §17.2 — read_only (read_file/grep/list_files) and
  code_intel (symbol_refs) — each served by its own subprocess via
  `python -m ai_venture_studio.mcp.server <name>`.
- The triple check made real (§17.3): the skill allowlist decides which
  tools exist, MCPHost mounts only the servers those tools live in (so an
  unlisted tool is unreachable, not merely refused), and the server itself
  refuses anything outside its partition. Any one layer's bug fails closed.
- Subprocess isolation is the property the in-process mapping could not
  give: a path-traversal attempt is now refused inside the child process.
- mcp-audit ledger (.mas/mcp-audit.jsonl): every call, permitted or
  refused, with voter, server, tool, digested args, outcome and duration.
  Arguments are digested rather than copied — the ledger records what was
  asked for without duplicating searched content.
- Transport switch: AUTOPRODUCT_TOOL_TRANSPORT=mcp opts in; in-process
  stays the default because a subprocess spawn per server per invocation
  should be paid deliberately. Both toolboxes present one surface, and the
  caller's budget stays authoritative.
- Still out, by design and named in the map: external MCP servers (doc 11
  §17.1's supply-chain reasoning), and the L1/L2 deploy/maintenance/
  test-exec partitions — two real servers beat eight stubs.
- Suite: 778 -> 795 hermetic tests

## v0.36.0 — the live-loop instrument for the v3.0.0 design gate
- avs loop: reads a cycle's artifacts (stages P0-P5, gates
  PL1/PL2/PL3/PL5) and reports the three v3.0.0 criteria with reasons.
  States, never decides: a cycle where nothing fired is NOT the gate, and
  a recorded 'continue' is not either — the gate is about the loop's
  ability to stop, so only a human kill-or-pivot at PL5 closes it
  (invariant 14.20, ADR-U19). Exit 3 when a fired criterion is waiting on
  a human.
- launch/cycle.yaml: loop-entry declaration. This repo's own cycle entered
  at P2 (the product predated the loop), recorded with its reason instead
  of left as a silent gap; P0/P1 are in scope for cycle 2.
- docs/v3-live-loop.md: what closes the gate, why the system cannot close
  it for itself, and the exact field a human records.
- Current honest state: V3-1 and V3-2 met, V3-3 not — the launch PRD's
  only kill criterion needs four consecutive logged attention weeks and
  the log holds one untracked week.
- Suite: 766 -> 778 hermetic tests

## v0.35.0 — the last small open items: review-voter gates, policy thresholds, the ready-queue fix
- Review voters now register like every other roster (§11.19):
  `avs review-gate` runs each of the six core charters against 8
  fixtures (4 positive / 2 negative / 2 boundary, unified diffs) through
  the REAL Voter seat, ≥87.5% to register, recorded under `review/<voter>`
  in `.mas/voter-registry.yaml`. The vote node fails closed on a FAILED
  voter, reports unregistered ones, and refuses to review at all if the
  whole roster failed. Review voters no longer ride `bench` alone.
- Policy thresholds move into `.mas/project.yaml` (`policy:` block, doc 09
  open item): max_reviewable_lines, report_threshold,
  high_severity_threshold, rootcause_confidence_min. Unknown keys are a
  loud error (a typo silently keeping the default is worse), ranges are
  bounded so a project cannot set a meaningless bar, effective values are
  recorded in the run mirror, and any threshold looser than the shipped
  default is stamped into the leader summary — a lowered bar never hides
  inside a clean-looking verdict.
- Fixed: `next_tasks` matched a `task_id` field Spec never had, so the
  ready queue never advanced past the first task. It now reads the
  `(task:<id>)` marker in the spec request — one shared definition of
  "built", reused by the Studio's per-task progress.
- Suite: 737 → 766 hermetic tests

## v0.34.0 — Studio live progress, interrupted-build recovery UX, the wire-up gate
- Building page shows per-task state (from the same workspace files the
  CLI writes) updating in place via /status polling — signals s1/s3, "it
  looks frozen while it works"; one reload when the worker exits
- Interrupted builds (dead worker, no report) get their own page: kept
  modules shown ✅, per-module 继续 retry buttons through the existing
  retry-task path (a blanket rebuild would trip the SCR freeze on built
  specs — deliberately not offered), reset clears the stale pid marker
- Wire-up gate (tests/test_studio_wireup.py): every form action, fetch,
  link, and image src rendered by any Studio state must resolve to a
  registered route with the right method — and every route must be
  rendered by some state; the /status JSON contract is pinned to what the
  building-page script reads
- README: bring-your-own-keys contract spelled out (no shipped keys, no
  proxy, no metered backend), AUTOPRODUCT_CHECKPOINT_KEY documented,
  roadmap rows v0.31-v0.34, stale per-review-only recovery limit fixed

## v0.33.0 — gap plan D15 + D16 remainder: checkpointed deploy/maintenance, encrypted checkpoints
- Deploy review and maintenance rebuilt as LangGraph graphs on the shared
  `.mas/checkpoints.db` saver (thread ids `deploy:<id>` / `incident:<id>`):
  a crash mid-vote or mid-root-cause resumes from the last completed
  super-step via `avs recover` (now covering all three graphs)
  instead of re-paying the pipeline; mirror step names, verdict taxonomies,
  lint-only degraded mode, and the recommend-only ceiling unchanged
- Encrypted checkpointer serde (doc 09 §3.1): `AUTOPRODUCT_CHECKPOINT_KEY`
  (raw or `secret://ENV`) encrypts checkpoint rows at rest via LangGraph's
  EncryptedSerializer (AES, pycryptodome availability-gated); a key that
  cannot be honored errors loudly — never a silent plaintext fallback;
  encryption state stamped into every run's meta.yaml; the YAML mirror
  stays deliberately plaintext (§09.6 audit asymmetry)
- Suite: 688 → 694 on the D15 branch (727 after merging v0.32; ledger
  PC-1 synced at the merge)

## v0.33.1 — operator pass: live records + installable package
- Live operator records: first Gate PL5 evaluation (nothing fired — 0 of
  4 attention weeks exist; decision explicitly NOT due), the launch
  experiment's power verdict against real traffic (n=1 unique visitor vs
  2,936 required → BLOCKED(INSUFFICIENT_POWER), exactly as pre-registered;
  clone spike recorded as CI-confounded), and the append-only
  weekly-attention log (2026-W30 honestly `not_tracked`; discipline
  starts 2026-W31) — each with a test that re-derives it
- Packaging: voter charters moved into the package
  (`src/autoproduct/skills/`, root symlink kept) so the installed wheel
  runs stage commands; pip-installed builds previously crashed loading
  charters. MIT LICENSE file added; PyPI metadata completed
- Suite: 727 → 732 hermetic tests on the rebased tree (ledger PC-1 synced)

## v0.32.0 — gap plan D13: upstream critique rosters
- Discover/plan/spec critics ported onto the shared stage engine as 14
  registered charter voters (discovery: desirability/feasibility/
  viability/scope-discipline; planning: completeness/dependency-realism/
  risk-sequencing/parallelization-safety/estimate-sanity; spec:
  testability/consistency/completeness/ambiguity/interface-impact — doc
  13 §25.1), each behind the 8-fixture registration gate; the three
  single-panel critic prompts retired
- `run_critique_roster` extracted from the P-stage engine: charter voters
  with no cross-visibility → per-finding fresh verify → leader; failed
  gate runs exclude the voter, unregistered voters are reported
- Suite: 688 → 721 hermetic tests (ledger PC-1 synced)

## v0.31.0 — gap plan D14 + D16: GEPA proposer, secrets layer
- GEPA proposer (`gepa.py`): budget-gated by the v0.27 `gepa.yaml` schema
  (refuses at zero weekly rollouts or unlisted targets), deterministic
  salted-hash holdout split the proposer never sees, old-vs-new charter
  scored by the same fixture gate voters register through; improvements
  emit a `.mas/gepa/` proposal record for human review — nothing
  self-installs
- Secrets layer (`secrets.py`): `secret://ENV` resolution that errors
  loudly on missing values, `Secret` with masked repr and a single
  deliberate `reveal()`, `scrub()` stripping every resolved value from
  outbound text
- Suite: 673 → 688 hermetic tests (ledger PC-1 synced)

## v0.30.0 — audit gap closures, phase C
- Cost/observability ledger: config-priced estimates, unpriced-call
  visibility, monthly cap check, tool-audit + evidence-ledger writers,
  Prometheus /metrics
- Module-spec invariant layer with SPEC_DRIFT_UNDOCUMENTED
- Named signal webhooks (sentry/datadog/pagerduty) with dedupe window

## v0.29.0 — audit gap closures, phase B
- Voter families: voter-gate now serves web/miniprogram/app/data alongside
  the product stages (same skills+fixtures contract)
- Five profile voter charters authored; 8-fixture gates for them and for
  the three data voters (64 new fixture cases)

## v0.28.0 — audit gap closures, phase A
- Web det-tool runners (axe/Lighthouse/size-limit, availability-gated)
- Data NFR grammar + lineage impact check (doc 18 §48.1)
- Upstream verdict vocabulary, typed (doc 13)
- Gate P1 platform-preflight class (doc 17 §41.3)
- Data-classification tags check (doc 18 §49.3)
- This CHANGELOG (doc 10)
