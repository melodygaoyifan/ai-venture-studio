# ADR-077: one trailer per section

Date: 2026-08-25
Status: accepted
Release: v0.119.0

## Context

The last unmapped run-19 finding. Case 03 (小区团购接龙), task t2: the
reviewer flagged, as a low finding, *"Duplicated/divergent 'files:' trailer
appended to design.md"*. In the preserved workspace, commit `8266346` (t2's
build commit) appends one design-memory section that ends:

```
files: app/__init__.py, app/db.py, app/groupbuys.py, app/main.py

files: app/main.py
```

Two consecutive `files:` trailers, different contents, one section — and the
first one is wrong: t2 modified only `app/main.py`.

## Finding

`product/design.md` is the evolving architecture memory: each build appends
`## title (slug)`, the spec's design text, and a `files:` trailer listing
what was actually written. The Spec stage reads the whole file back so
feature N+1 extends the design instead of re-deriving it.

That read-back is the cause. The t2 spec model was shown design.md with t1's
section — which ends in t1's trailer `files: app/__init__.py, app/db.py,
app/groupbuys.py, app/main.py` — and imitated the format: its design text
ends with that exact line (verified in the committed
`specs/get-groupbuys-id-view-a-group-buy-backend/spec.yaml`, whose `design`
field ends `...concerns them.\n\nfiles: app/__init__.py, app/db.py,
app/groupbuys.py, app/main.py\n`). The model's list is a guess copied from
t1, not disk truth. `_append_design_memory` then appended its own — correct —
trailer after it.

So the defect is the appender's: it composes model text + machine trailer
without guarding against the model text already ending in trailer format.
The machine cannot stop a model from imitating what it is shown; it can
refuse to publish two records where its format promises one.

## Fix

`_append_design_memory` strips trailing `files:` lines (and the blank lines
above them) from the design text before writing its own trailer. Only
*trailing* trailer-format lines are touched — a `files:` mention mid-prose is
the model's own text and stays. The machine trailer, derived from the files
actually written, is the one record.

## Control

`tests/test_one_trailer_per_section.py` — 3 tests. With `src/` stashed to
v0.118.0, the run-19-shape test fails (both trailers survive, the wrong one
first) and the two over-stripping guards pass, pinning that the fix removes
exactly the imitated trailer and nothing else.

## Consequences

- Each design-memory section carries exactly one `files:` line, from disk
  truth. Later spec stages read an unambiguous memory; the reviewer finding
  class is closed.
- The imitation itself is not suppressed — the model may keep ending designs
  with a `files:` line, and the appender will keep discarding it. That is
  the right division: prompt-shaping the habit away would be an instrument;
  this is a one-line invariant at the only seam that publishes the record.
- With this, every run-19 outcome shape maps to a shipped fix (ADR-075
  A–F, ADR-076 1–4, ADR-077) or to the reviewer doing its job on genuine
  product-code findings.
