# Contributing

Contribution is gated the way everything else here is gated (doc 25 §75.2)
— the same bar for the maintainer and a stranger.

## What a contribution looks like

- **A voter skill** = frontmatter + charter + an 8-fixture set
  (4 positive / 2 negative / 2 boundary) under
  `tests/integration/voters/fixtures/`. The suite enforces the
  charter↔fixture bijection; `avs voter-gate` is the registration
  bar (≥87.5%). **No fixture, no merge** — the public skills ecosystem's
  median quality is what happens when nothing stops registration.
- **A language lane** lands **PROVISIONAL** until its seeded-defect
  manifest is calibrated (design doc 19's rule, applied to everyone).
- **A domain/channel profile or edition** is a delta: it may add checks
  and lower ceilings; the loaders refuse anything that widens.
- **A deterministic tool** is a pure function plus fixture cases; the
  hermetic suite (`uv run pytest`, no network, no keys) is the gate.
- **A README or benchmark-page edit** must resolve against
  `claims/platform.yaml` — asserting beyond the ledger fails the suite
  (ADR-U29). Add the typed claim or drop the number.

## Engineering rules

`CLAUDE.md` is binding for humans too: deterministic control flow, typed
envelopes, no runtime asserts, `yamlx.extract_mapping` for all LLM output,
hermetic tests, no new runtime dependency without a one-line justification.
Telemetry schema changes are a major-version review with a
`telemetry show` diff in the PR (F-25.3).

## Versioning (the contract surface, §74.2)

SemVer over an enumerated surface: FDR schema · `.mas/*` schemas ·
`product/requirements.yaml` (the requirement ledger, ADR-045 — ids are
permanent and referenced from outside the file) · CLI commands and exit
codes · gate names and record schemas · skill frontmatter · the telemetry
payload schema. Breaking any of these bumps
major with a migration note; deprecations live ≥1 minor with a loud
runtime warning. Design docs change via errata, never silent edits.

## Governance, honestly

Single-maintainer project; **bus factor = 1** is a fact, not a flaw to
hide. Decision rights: the maintainer. If that ever changes, the
escalation path is an ADR — not a constitution written for a community
that doesn't exist yet.

## Watch items (each with an owner, a date, and a falsifier)

| Item | Review by | What would change our mind |
|---|---|---|
| Agent Skills spec convergence | 2026-10-01 | the spec stabilizes AND ≥2 tools we care about consume it → converge our frontmatter |
| MCP Server Cards / statelessness | 2026-10-01 | spec adopted by the servers we'd wrap → declare tiers via Server Cards |
| A2A / peer messaging | on demand | a concrete need to expose an external agent surface → formally revisit the doc-16 ADR, never quietly erode it |
| PyPI publication | on demand | first external adopter asks for `uvx` without a checkout → publish `avs` with attested artifacts (§73.2) |

Owner for all of the above: the maintainer. A watch item without a
falsifier is a vibe; these have theirs.
