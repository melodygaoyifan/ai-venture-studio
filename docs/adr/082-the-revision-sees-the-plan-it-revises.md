# ADR-082: the revision sees the plan it revises

Date: 2026-08-26
Status: accepted
Release: v0.124.0

## Context

Run 19b, case 04, verification run on v0.123.0 ($1.49): the case blocked
in planning — "lane collision: t1 (api) and t5 (elimination) both expect
'app/models.py'" — and ADR-081's repair retry was never reached. The
preserved artifacts (ADR-079) tell the whole story: attempt 3 was a
collision-free arrangement lost to a trailing prose paragraph that broke
the YAML parse; the nudge (ADR-080) fired live for the first time and
worked exactly as built; and the regenerated plan had a *new* collision
that two dag revisions then failed to clear.

The structural defect: provider calls are stateless, and none of the
three corrective-feedback paths included the thing being corrected. The
dag/critic revision sent `{dag_issues, critic_majors}` — the issues,
never the plan they were issues with. The parse nudge named the parser's
objection (ADR-079) but not the response that provoked it, so a
99%-correct response was regenerated instead of repaired. The truncation
path said "Return the SAME plan with shorter descriptions" — of a plan
the model could no longer see. Every revision was a blind re-roll, which
is why case 04's planning success rate across recent runs was ~2 in 5:
the loop was sampling arrangements, not converging on one.

A sharper corner of the same defect: `blast_radius` derives
`files_expected` for tasks that omit them, so a lane collision can be
between globs the planner never wrote. Told "you listed app/models.py in
two tasks," a planner that had listed it in neither cannot narrow what it
cannot see.

## Decision

`_shown_back(previous, instruction)` appends a bounded
(`_PREV_RESPONSE_CHARS = 12_000`) `<your_previous_response>` block plus a
path-specific editing instruction to every corrective feedback:

- **dag/critic revisions** carry the parsed plan serialized *as the
  checker read it* — after the blast_radius fallback, so derived
  files_expected are visible — with "keep every task the issues do not
  name, change only what the issues require."
- **parse nudges** carry the raw failed response with "fix ONLY the
  parse problem … drop anything that is not the YAML mapping" — the
  exact cure for the trailing-prose break that cost attempt 3.
- **truncation** carries the cut-off response: keep its tasks, shorten
  the words.

## Consequences

- A revision is an edit, not a re-roll. The known-good parts of a plan
  survive the correction of its known-bad parts.
- Revision prompts grow by up to ~12KB — input tokens on a path already
  paying for a full re-plan; the re-roll it replaces cost more in failed
  runs than the paste costs in tokens.
- `tests/test_the_revision_sees_the_plan_it_revises.py` pins all three
  paths (stash-controlled) plus the bound.
- ADR-079 → ADR-080 → ADR-081 → ADR-082 close as one lesson: make the
  failure legible, make the loop able to deliver the feedback, show the
  feedback to the actor who can act on it — and show that actor the
  artifact it is acting *on*.
