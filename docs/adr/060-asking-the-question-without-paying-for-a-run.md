# ADR-060 — asking ADR-058's question mechanically, instead of paying for a run to ask it

**Status:** accepted (2026-08-21)

**Answers:** the standing question this series has been answering one instance
at a time — *is there a fact this system establishes and then drops before it
reaches the reader that needed it?* ADR-058 found six of those. Every one was
found by hand, after a $67.88 bench run had already paid to expose the symptom.

**Reverses:** nothing.

## Context

ADR-058's six findings had one shape:

> a component established a fact, put it on the record, and the reader that
> needed it never got it.

That shape is mechanical, and nothing in this repo was asking it mechanically.
The cost of the manual method is not the labour — it is the **latency**: the
question only gets asked about the parts of the system a run happened to
exercise, and only after the run is paid for. Runs 12, 13, 16 and 18 each
bought one or two answers to it.

The prompt for this record was a fair question from the founder: *why do we
need endless batch running — can't we just find the existing issues and tackle
all of them?* For this class of defect, the answer is that we can.

## Decision

### The sweep

`tests/write_without_reader.py` walks every record class under `src/` —
anything deriving from `BaseModel`, `TypedDict` or `NamedTuple`, or decorated
`@dataclass` — collects its annotated fields, and looks for **any** reader
anywhere in the repository.

A read is deliberately generous, because the question is whether the fact
reaches anyone, not whether it reaches them elegantly:

- `x.field` in Load context
- `d["field"]`, `d.get("field")`, `d.pop("field")`, `d.setdefault("field")`
- the name inside any string literal, in any `.py`/`.yaml`/`.md`/`.json`/
  `.toml`/`.j2` file in the repo — rendered into a report, matched out of a
  document, named in a prompt, all readers

A write is not a read: `x.field = v`, `Model(field=v)`, and the declaring
annotation itself. The whole sweep runs in 1.2 seconds.

### What it found on the first run

Five defects, none of which any bench run had ever surfaced. Two of them are
fields on `BuildResult` that **ADR-058's own fix walked straight past while
adding three of their neighbours** — the comment it left behind says
"`BuildResult` has always carried these", and it was made true of three of the
five.

| # | fact | who needed it |
|---|---|---|
| F1 | `wireup_issues`, `modified_existing` | the founder reading the report |
| F2 | `policy_path` | whoever audits a merge or a deploy the machine performed |
| F3 | `escalate_on` | the cascade, which never consulted its own trigger list |
| F4 | `hard_fails` | the human deciding what at Gate PL3 can be overridden |
| F5 | `build_floor`, `probe_floor` | anyone reading a kill-criterion verdict |

**F1 is the sharp one.** `wireup_issues` is computed only on a *successful*
build, so the one outcome the record had no way to state was the one that looks
best and is worst: **built, tests green, nothing imports it.** Every reader saw
`status: built` and stopped. `modified_existing`'s own field description
promised the changes would be "visible, reviewed, never silent", and it had no
reader to be visible to.

**F3 is the shape that generalises.** `CascadePolicy.escalate_on` shipped with
`low_confidence` in its default for its entire life, and `cascade_route` had no
confidence input to judge it against. A knob that reads like it controls
something and does not is worse than a missing knob: it is a control surface
that answers a question nobody re-asks.

### The fixes, and one design choice inside them

F1 gets a deterministic `_wireup_block` in the report — deterministic for the
same reason `_outcome_tally` is: the reporter is an LLM and this is a list of
facts. The founder summary in the CLI names the silent case in one line. F2
writes `policy_path` into the automation log, on the refusal path as well as
the approval path, because *"why didn't it"* is the question an auditor
actually opens that file with. F4 derives real counts instead of asserting
zero. F5 reads the floors off the state object, so the sentence and the record
cannot drift.

F3 needed a decision rather than a wire-up. The first attempt treated an absent
confidence as grounds to escalate, which made the default policy escalate
everything. So: `escalate_on` defaults to the two mandatory triggers,
`low_confidence` is opt-in, and **opting in without supplying a confidence
raises**. Neither silently clean — the original defect — nor blanket-escalate,
which turns the cascade off while looking like it is on.

### The allowlist is where the judgment lives

`test_write_without_reader.py` runs the sweep on every suite run. 24 fields are
legitimately written without an in-repo reader, and each carries a written
reason, in four categories: public library surface (the payload *is* the
export); the subject of a finding (an id saying *which* thing, that nothing
dispatches on); read by a person out of a YAML file; and declared-but-not-yet-
enforced, kept and named rather than quietly deleted because the schema is a
published contract.

`test_every_excuse_is_an_actual_sentence` fails an entry shorter than four
words. It caught five of my own shrugs on first run.

## What stays out

- **Not an equality check.** A field that *gains* a reader must not break an
  unrelated change; it drops out of the sweep and leaves a harmless stale
  entry. Only the direction that loses information fails. Noise is how a check
  gets disabled.
- **No auto-deletion of unread fields.** Several are read by a human with the
  file open, which no AST walk can see. The sweep asks; the allowlist answers.
- **Not a replacement for the bench.** This finds facts with no reader. It
  cannot find a fact that is wrong, a gate that is inert, or a plan that gave
  up — those still need a run. It removes one class from the run's job.

## What keeps this honest

- `test_the_audit_finds_a_planted_defect` builds a two-field module in a temp
  tree, one field read and one not, and asserts the sweep separates them. A
  test that only ever passes proves nothing about whether it *could* fail —
  ADR-059's lesson, applied to ADR-059's own successor.
- `test_the_allowlist_has_not_rotted` fails on an excuse for a field that no
  longer exists. An allowlist that stops describing the code is worse than none.
- `test_the_five_defects_adr_060_fixed_stay_fixed` names each field
  individually, because each was a distinct reader that had to be built.
- All five defects were confirmed live in the **installed** 0.108.0 before
  anything was written, so they are properties of the shipped system and not
  artifacts of this change's own refactor.

## The lesson worth keeping

A bench run is how you find out whether the system is any good. It is an
expensive and slow way to find out whether the system is *coherent*.

When a defect class has a mechanical shape, write the sweep. ADR-058 found six
instances by hand across four paid runs; the sweep found five more in an
afternoon for nothing, including two the hand-written fix had touched and
missed. The question worth asking of any recurring finding is not "how do I fix
this one" but **"what is the shape of it, and can I ask a machine?"**
