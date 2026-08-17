# ADR-045 — A requirement has a name

Status: accepted (v0.96.0)

## Context

Every mechanism this system has for holding a promise steady is scoped to one
document. `ears.py` grammar-checks each acceptance criterion. `raise_scr` /
`approve_scr` freeze a built spec and make the SCR the only legal drift channel
(ADR-U02). `TestSkeleton.covers` traces a test back to the criterion it
verifies. All three are real, all three work, and all three stop at the edge of
the spec file they live in.

So the product as a whole has no promises — only a pile of documents that each
have some. Three consequences, in the order they bite:

**A criterion cannot be referred to.** It is identified by its 0-based position
in a list. `covers: [2]` means "the third bullet of *this* spec, today". There
is no way to say "the thing we promised in the orders spec" from anywhere else,
so nothing outside a spec can be about a criterion.

**A planner is never told what the product already does.** `run_feature` passes
`prior` — a list of directory *names* (`autopilot.py:1549`). The planner
building feature 07 is told that features 01 through 06 exist. It is not told
what any of them promised. It plans against the code and its own reading of it.

**The reviewer remembers and the system does not.** Bench run 16 recorded a
blocking finding in the reviewer's own words: *"the exact anti-pattern a prior
task in this repo was already dinged for"*. That is the reviewer holding
across-task memory the builder had no access to. The builder was not ignoring
the earlier decision; nothing ever told it there was one.

## Decision

**Every acceptance criterion in the product gets a stable id and appears in one
list.** `product/requirements.yaml` — a ledger of `R-001`-style ids, each with
its text, its spec, its status, the FDR that introduced it, and the test files
that verify it.

Three properties, and each exists because its absence is a specific defect:

**Derived, never hand-maintained.** `sync_ledger(repo_dir)` reads the specs and
rewrites the ledger. It runs from `finalize_build_bookkeeping` — the one place
`spec.built = True` happens — so the ledger cannot drift from the specs by
being forgotten. A file a person has to remember to update is a file that is
wrong by the second week.

**Keyed on text, never on index.** A requirement is matched by
`(spec_slug, criterion text)`. Matching on position is exactly the defect this
record exists to remove: a spec regenerated under an approved SCR would keep
`R-007` pointing at slot 7 while slot 7 now holds a different promise. The fix
would commit the failure it was built to prevent.

**Append-only.** Ids come from `max(ever seen) + 1`, counting retired and
superseded entries, so an id is never reused. A criterion that leaves its spec
goes `retired` and stays in the file; one that comes back is live again under
its original id. A reference from outside can therefore always be resolved,
including references to things that are gone.

**`retired` and `superseded` are held apart.** Derivation may set `retired` —
it observed that the text is no longer in the spec. Only an explicit decision
may set `superseded`, because a derivation cannot know what replaced something.
`superseded_by` holds the *origin* of the replacement (an FDR path), not
another requirement id: a feature that replaces one promise usually adds
several, and naming one of them would be a guess presented as a record.

**The planner is shown the requirements that its request touches.**
`relevant(repo_dir, text, radius=)` scores live requirements by token overlap
with the request, biased toward the blast radius already computed, and returns
a capped slice. `render_slice` states how many matched and how many were
dropped (ADR-039), and the planner prompt says in words that requirements it
was not shown still exist. A bound that silently truncates would teach the
planner that the list it sees is the whole product.

**Provenance is attributed after the fact, never at sync time.**
`attribute_origin(repo_dir, known_ids, origin)` stamps the FDR only onto
requirements that did not exist before that feature ran. The first sync on an
existing product backfills hundreds of criteria; stamping the current FDR onto
them would write a record of a decision nobody made. The origin recorded is the
*archived* `product/features/NN/fdr.md`, not the caller's input path — a
correction arrives as `.mas/pending-change.md`, which the next correction
overwrites.

## What this reverses

Nothing. It adds a level the system did not have: mechanisms that operate on
the product's whole set of promises rather than on one document at a time.
ADR-U02's SCR channel is untouched and remains the only way a built spec
changes; the ledger observes specs, it does not edit them.

`product/requirements.yaml` becomes a contract surface, alongside the `.mas/*`
schemas and the FDR schema that CONTRIBUTING enumerates.

## What stays out

- **A hand-written requirements document.** The ledger is derived output. If it
  ever needs to be edited by hand, the spec is what should have been edited.
- **Global renumbering.** Ids are permanent. A ledger whose ids mean different
  things at different times is worse than no ledger, because it invites
  references it will then break.
- **Sharing the correlator's tokenizer.** `correlate._tokens` and
  `requirements.tokens` stop different word sets on purpose — the ledger must
  stop the EARS grammar words (`shall`, `when`, `while`, `system`, `user`)
  that appear in *every* criterion and therefore match everything. ADR-038's
  rule applies: unify one concept's definitions, name and pin two.
- **Showing the planner the whole ledger.** Cost and answer quality both
  degrade with corpus size. Retrieval first, then a bounded slice.

## Mechanism

`upstream/requirements.py` — `sync_ledger`, `attribute_origin`, `supersede`,
`relevant`, `render_slice`, and the `Requirement` model; the `sync_ledger` call
in `finalize_build_bookkeeping` (`upstream/build.py`); the
`<existing_requirements>` block and its planner rule in `upstream/autopilot.py`.

`tests/test_a_requirement_has_a_name.py` — eighteen tests pinning the
properties rather than an instance: an id survives reordering, is never reused,
comes back live if its criterion returns, an unreadable spec retires nothing,
duplicate text is one requirement, the slice reports what it dropped and never
offers a retired promise, provenance is written once, the two tokenizers differ
in the direction claimed, and — by AST walk over the call sites — the ledger is
synced by the build rather than by a person, and the feature path syncs before
it reads and attributes after it builds.
