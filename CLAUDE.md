# autoproduct — project constraints

Hard constraints for anyone (human or agent) changing this codebase. The
Context voter enforces these as findings; the compounding loop appends its
learned section below.

## Architecture invariants

- Deterministic control flow, probabilistic analysis: LLMs never decide
  which node runs next, when to escalate, or whether to retry. Those
  decisions live in Python (`orchestrator/graph.py`).
- Agents communicate only through typed envelopes (`state.py`). Never add
  a free-form text channel between two LLM invocations.
- Voter tools are read-only, risk L0–L2, allowlisted in skill frontmatter,
  and budget-enforced at the `ToolBox` boundary. L3/L4 tools (secrets,
  migrations, deploys, auth) must not exist in any voter-reachable registry.
- The system never pushes to main on its own, and the compounding loop
  only ever proposes CLAUDE.md changes via PR. Merging and deploy
  execution exist only behind a human-authored, attributed, expiring
  policy file (`.mas/automerge-policy.yaml`, `.mas/deploy-exec-policy.yaml`
  — ADR-031): disarmed by default, exact branch names only, earned by
  track record, and never for a diff touching migrations, CI, IaC,
  `CLAUDE.md`, `.mas/`, or the policy files themselves. No agent may arm
  or widen a policy. Auto-hotfix remains out entirely.
- A voter that cannot judge returns a `BLOCKED_*` status — never an empty
  findings list, never a guess. Findings require verbatim `evidence`.

## Engineering rules

- Python 3.12+, `uv` for everything (`uv run pytest`, `uv add`). No new
  runtime dependency without a one-line justification in the PR body.
- All LLM-response parsing goes through `yamlx.extract_mapping` — models
  narrate; never `yaml.safe_load` a raw response.
- Tests are hermetic: no network, no API keys, mock provider only.
  Anything touching a real provider is manual/live, not in `tests/`.
- External tool wrappers must be availability-gated and report `skipped`
  visibly — silent absence of a scanner reads as "scanned and clean".
- Never commit `.mas/` artifacts, checkpoints, or API keys. Secrets stay
  in the environment.
- **Never delete `.mas/`** (no `rm -rf .mas`, no `git clean` with `-x`/`-X`
  — plain `git clean -fd` spares ignored files and is fine). It holds
  unrecoverable run history and failure forensics; it was wiped once
  (2026-07-26) and runs 1–8's originals were lost. Scoreboard yamls are
  dual-written to the tracked `benchmarks/results/`, but preserved
  workspaces and failed-build snapshots exist nowhere else.
- Subprocess calls: list argv (no `shell=True`), explicit `timeout`,
  `capture_output=True`.

## Known accepted risks

- Gate 2's T3 container sandbox (network-disconnected docker) runs in deep
  mode when a docker daemon is available. Standard mode and docker-less
  hosts fall back to an unsandboxed subprocess worktree — visible in the
  report's `sandbox` field. Only review trusted repos on the fallback path.

## Learned constraints (autoproduct)

- Replace all runtime assert statements with explicit conditional checks that raise appropriate exceptions. <!-- 2026-07-22: B101 assert_used appeared 13 times, the most frequent recurring finding. -->
- Invoke subprocesses with absolute executable paths and explicit argument lists, never with partial paths or shell=True. Resolve the executable through `ai_venture_studio.executables` — `resolve(name)` when the run cannot proceed without it, `find(name)` when absence is an expected answer and the caller reports `skipped`. Never a bare name in argv[0]; ruff `S607` enforces it. A command *displayed* for a human to copy is text and stays bare (ADR-064). <!-- 2026-07-22: B603 (8) and B607 (7) subprocess findings recur across reviews and drove the security escalations. 2026-08-21: enforced at all 152 sites, ADR-064. -->
- Never silently swallow exceptions with try/except/continue; log the error or handle it explicitly. <!-- 2026-07-22: B112 try_except_continue recurred 2 times, hiding failures. -->
