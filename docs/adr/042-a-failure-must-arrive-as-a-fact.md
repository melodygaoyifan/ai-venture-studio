# ADR-042 — A failure must arrive as a fact

Status: accepted (v0.92.0)

## Context

Bench run 15's case 01 lost a task to a build that failed three times with
`1 failed, 27 passed`. The result file recorded why like this:

```
build gate still failing after max iterations; nothing committed — last
failure: ==== FAILURES ==== ____ test_huge_id_no_crash ____ server =
('127.0.0.1', 64131) def test_huge_id_no_crash(server): huge = "9" *
```

240 characters, cut off mid-expression, naming no assertion and no
verdict. ADR-037 added that `— last failure:` clause precisely so the
cause would travel with the generic sentence into the rows that read
`detail` — the bench result, the founder's report, `outcomes.yaml`. It
does travel. What arrives is pytest's banner art: `==== FAILURES ====`
and `____ test_name ____` are roughly 160 characters of rule characters
before the run says anything, so a head-slice spends nearly its whole
budget on punctuation and stops before the first fact.

The full output was never lost — `test_summary` on the task carried the
complete pytest run, including
`FAILED tests/test_get_groupbuy_notfound.py::test_huge_id_no_crash -
assert 400 == 404`. The defect is entirely in the condensation.

The underlying failure, recovered by re-running the preserved workspace at
`.mas/failed-builds/get-api-groupbuys-id-fetch-a-group-buy-by-id`
(`1 failed, 27 passed in 7.23s`, reproducing run 15 exactly): a 40-digit
id matches the spec's own `^[0-9]+$` valid-format criterion, so it is a
well-formed id that does not exist and must answer 404; the product
range-rejected it as malformed and answered 400. That is a genuine product
defect, correctly caught, and the implementer had the full traceback as
feedback on all three attempts. The build rate measuring it is the
benchmark working — nothing about the product is fixed here.

## Decision

`testing.salient_failure()` replaces the head-slice. It prefers pytest's
own short-summary section — one condensed line per failure, already the
shape a person wants — then the `E` assertion lines, then the text with
its rules stripped. It keeps **both** the summary line and the `E` line
when both exist: pytest elides its summary to terminal width
(`- assert 40...`), so the summary names the test and the `E` line says
what was actually wrong, and neither alone is enough. Truncation, when it
still happens, lands on a word boundary.

The same run's failure now reads:

```
FAILED tests/test_get_groupbuy_notfound.py::test_huge_id_no_crash -
assert 40... E assert 400 == 404
```

It lives in `testing.py`, which owns reading pytest output, rather than in
`build.py`, so any consumer of a `TestReport` gets it and the 240-char
slice has exactly one definition — a test asserts `build.py` no longer
keeps its own (ADR-038's rule).

## What stays out

- **Raising the character budget.** A bigger head-slice buys more banner.
  The problem was never the size of the window, it was that the window
  looked at the wrong end of the text. 240 characters is enough for the
  answer once the answer is what gets selected.
- **Storing the full traceback in `detail`.** `test_summary` already holds
  it, uncut. `detail` is a one-line row in a table; making it a transcript
  would break the thing it is for.
- **Any change to the product, or to the case.** The failing product is
  regenerated from scratch every run and lives in a temp workspace; a
  hand-fix would be discarded and would corrupt the measurement. The spec
  is self-consistent — both tests derive from its criteria — so the case
  is fair and the failure is real.
- **Any change to the implementer's feedback.** It already receives
  `report.detail`, the full pytest output, on every retry. Three attempts
  with complete information is a capability result, not a plumbing defect.

## Mechanism

`salient_failure()` and `_clip_words()` in `testing.py`; the one call site
in `build.py`'s max-iterations branch. `tests/test_salient_failure.py` pins
it against run 15's verbatim output, including the old behaviour as the
regression it prevents, and the no-second-copy rule.

The word-boundary helper is `_clip_words`, not `_clip`, because
`testing.py` already owned a `_clip(text, head, tail)` that keeps both ends
of a faulthandler dump. The first version of this change defined a second
`def _clip` at the bottom of the module, which rebound the name for the
whole file — the hang-dump path then called the new two-argument helper and
five `test_test_gate` tests failed with no import error to point at the
cause. A test now parses `testing.py` and rejects **any** duplicated
top-level definition, because a shadowed helper is invisible at the call
site and this is not the last time the file will be appended to.
