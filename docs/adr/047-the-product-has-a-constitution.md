# ADR-047 — The product has a constitution

Status: accepted (v0.97.0)

## Context

Section 4 of the FDR template is "暂时不要的功能 / NOT needed for now". It has
been in the template since the beginning, both the Chinese and English versions
carry it, the Studio's composer writes it as its own slot (`not_needed` in
`studio_chat._HEADINGS`), and the founder fills it in. The template even
explains why it is there: *"想到了但第一版不做的，写在这里防止误做"* — write it
here so it does not get built by mistake.

Nothing has ever read it.

It reached the first planner as part of the FDR blob, mixed in with five other
sections and no rule telling the planner what to do with it, and after that it
vanished entirely. `avs add` plans a feature against the code, the requirement
ledger (ADR-045) and the reconciler (ADR-046). None of those three knows that
in February the founder wrote "暂时不需要在线支付". So the tenth feature grows a
checkout, and the founder — who wrote the sentence that would have prevented
it — has no idea why.

This is the same failure ADR-045 and ADR-046 addressed, one layer up. Those
records gave the system a memory of what the product *promises*. This one is
about what the product has decided *not* to do, which is a different kind of
fact and cannot be stored in the same place.

An invariant is not a requirement. A requirement is a promise the product keeps
and a test proves it. An invariant is a boundary the product does not cross,
and **no test can prove a thing was not built.** Filing "no online payments"
into `product/requirements.yaml` would put an entry in the ledger that
`sync_ledger` retires on its next run, because no spec contains it and no spec
ever will.

## Decision

**`product/constitution.yaml` — an append-only ledger of what the founder has
ruled out, derived from a document they already wrote, shown to every planner,
and gating nothing.**

**Derived, never typed.** `sync_constitution` reads section 4 of an FDR. The
plan this comes from said "never a separate typing chore", and the founder
persona is as lazy and as non-technical as possible: a constitution they have
to maintain by hand is the chore, with the added cost that it goes stale in
silence. The section is found by its *number* (`## 4.`), because the template,
the English template and the Studio composer write three different wordings
after it, and keying on the prose would silently empty the constitution the
next time someone rewords a heading.

**Extracted deterministically. No model call.** A model asked to summarise this
section can invent a boundary the founder never drew, and an invented invariant
is worse than a missing one: it is presented to every future plan as something
the founder decided. `compose_fdr` already establishes that the founder's words
enter the document as they typed them, and this reads them back out the same
way — bullets and numbering stripped (those are the document's, not theirs),
the parenthetical template examples skipped (including `TEMPLATE_EN`'s, which
wraps across two lines), and "无"/"none" recognised as *there are none* rather
than as an invariant named "none".

**Reconciled per origin.** A feature FDR's section 4 can add to or withdraw
from its *own* lines and nothing else. The founding document's "no online
payments" survives a hundred feature FDRs that never mention payments, because
a derivation that reads silence as repeal would empty the constitution on the
first feature. Deleting the line from the FDR that holds it is what withdraws
it — the founder changing their mind, in the place they said it.

**Append-only ids.** `C-001`, allocated from `max(ever seen) + 1` including
withdrawn entries, for the reason ADR-045 gives: an id handed to a second thing
is a record that lies about the first.

**Shown whole, and it says when it is not.** Unlike the requirement ledger
there is no retrieval step. A "do not build this" list is short by construction
and every line applies to every plan, so slicing it by keyword overlap would
hide the invariant the request is about to violate — precisely the one that
mattered. There is still a cap of 20, because "short by construction" is an
expectation rather than a guarantee, and a cap that drops work says so
(ADR-039).

**It gates nothing, and the newest request wins.** The planner is told what was
ruled out and told explicitly that if *this* request asks for one of those
things, the founder has changed their mind and the request wins. That is the
one rule that keeps a boundary from refusing the person who drew it.

## What this reverses

Nothing. It reads a section that was already being written and was already
being ignored. `avs add`'s contract is unchanged: no new status, no new exit
code, no new refusal — the version bump is minor only because
`product/constitution.yaml` becomes a file the system writes and other things
read.

## What stays out

- **A gate.** The obvious next step, and the wrong one. This product already
  has exactly one refusal path (ADR-046), it is measured, and it is built on a
  typed model verdict over a retrieved slice. A *second* stop-the-build path
  built on prose lines with no model call to judge what they meant would refuse
  correct work far more often than it caught a real overreach — the founder
  writes "no logins" in February and is blocked in June for a feature that
  merely mentions a user's name. Telling the planner is the whole win.
- **The other two sources the plan named.** "Resolved review findings" and
  "confirmed FDR answers" were listed as accumulating into the constitution.
  Neither is honest yet. A resolved review finding is a *fixed defect*, not a
  boundary — "do not use a bare except" is a code rule the reviewer already
  enforces, and promoting it to a product invariant shown to every planner
  would fill the file with lint. A confirmed FDR answer (`studio_chat`'s GUESS
  turns) is a statement of what the product *is*, which is a requirement and
  already has a home. Section 4 is the one source that is unambiguously "do not
  build this", so it is the one source that ships. Stating this is better than
  faking the other two.
- **Withdrawing an invariant from the CLI.** There is no `avs constitution
  --withdraw C-003`. The founder withdraws it by deleting the line from their
  FDR, which is a document they already know how to edit, and which keeps the
  document and the derived file from disagreeing.
- **Rendering it to the reconciler.** The reconciler judges a request against
  *promises*, and an invariant is not a promise. A request that violates one is
  not a duplicate and not a contradiction; it is the founder changing their
  mind, which is allowed.

## Mechanism

`upstream/constitution.py` — `not_needed_lines` (the deterministic section-4
parse, keyed on the number, skipping multi-line template guidance),
`sync_constitution` (per-origin, idempotent, append-only), `live`, and
`render_for_planner` with its announced cap. `product/constitution.yaml` is
written beside `product/requirements.yaml`.

Wiring: `run_planning` (`upstream/plan.py`) syncs against `FDR.md` and renders
`<ruled_out>` into the initial planner's prompt; `run_feature`
(`upstream/autopilot.py`) syncs against the archived feature FDR and renders
the same block, with the "the request wins" rule in
`_FEATURE_PLANNER_SYSTEM`.

`tests/test_the_founder_describes_the_product.py` pins the rules that would
otherwise rot quietly: an unfilled template rules nothing out (both languages),
a feature that says nothing does not repeal the founding document, an id is
never handed to a different invariant, writing a line again brings it back,
syncing twice changes nothing, the render names what it dropped, and — by
source inspection — that *both* planners are actually shown the file, since a
constitution nothing reads is the state section 4 was already in.
