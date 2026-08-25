# ADR-079: the feedback names the problem, and the evidence stays

Date: 2026-08-25
Status: accepted
Release: v0.121.0

## Context

Run 19b (the ADR-078 named slice), case 04-direction-workbench, second
attempt: planning died after all `MAX_REVISIONS + 1 = 3` planner attempts
with *"unparseable planner output (ValueError: no YAML mapping with any of
('tasks',) found in response (2457 chars))"* — and left nothing to debug
with. Two defects, both already half-diagnosed in the code's own comments:

1. **The revision feedback named no problem.** `run_planning`'s except
   block includes `str(exc)` in the retry prompt — the fix run 16's case 02
   forced (the comment cites ADR-041: the writer never told what was wrong
   with its answer). But when `extract_mapping` is what raises, its
   ValueError says only "no YAML mapping … found": the underlying yaml
   ScannerError — "line 3, column 9: expected alphabetic or numeric
   character but found '*'" — was swallowed in the candidate loop's
   `continue`. So the model was told *"Fix that exact problem"* about a
   message that states no problem, three times, and all three failed. The
   run-16 fix moved the gap one call deeper instead of closing it.

2. **The failed responses were discarded.** All that survives a failed
   attempt was a 160-char *whitespace-collapsed* opening
   (`" ".join(raw.split())[:160]`) — I could not even determine whether
   case 04's response lacked newlines (one plausible cause given its
   opening `'tasks: - id: t1 title: …'`) because the collapse destroys
   exactly that evidence. Failed builds keep their whole worktree at
   `.mas/failed-builds/<slug>`; failed plans kept nothing.

## Decision

- `yamlx.extract_mapping` records what the parser objected to per
  candidate — the yaml error text, or "parsed to str, not a mapping", or
  "a mapping parsed but its keys are […]" — and appends the last one (the
  key-anchored candidate: the model's actual envelope) to its ValueError as
  `— closest attempt: …`. Every consumer of the run-16 fix now gets a
  message with the location and character in it.
- `run_planning` writes each failed attempt's full raw response to
  `.mas/failed-plans/attempt-N.txt` before revising, and the blocked
  reason names the path. Same rationale as `.mas/failed-builds/`.

## Consequences

- If case 04 fails again, the three responses are on disk and the debug is
  a read, not a re-run. If it stops failing, the better feedback is a
  plausible reason why.
- `tests/test_the_feedback_names_the_problem.py` pins both: the objection
  in the error (three shapes) and one preserved file per attempt with the
  raw — not collapsed — text.
- The class to remember: an error message built at one layer can undo a
  fix made at the layer above it. Run 16 fixed "the exception class alone
  reached the model"; this instance is "an exception whose *message* is
  itself contentless reached the model", through the same corridor.
