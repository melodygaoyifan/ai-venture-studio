# autoproduct — operations runbook

Day-to-day operation of all four stages. Assumes `uv` and an
`ANTHROPIC_API_KEY` in the environment (other provider keys optional —
voters fall back visibly without them).

## Commands

| Command | What it does |
|---|---|
| `avs review <PR-URL \| git-range>` | Code Review + Test stages (Gates 1–3) |
| `avs resume <review-id> --decision ack\|override:<VERDICT>` | Continue a review paused at Gate 3 |
| `avs deploy-review <target>` | Gate 5 — deploy recommendation (never deploys) |
| `avs deploy-outcome <review-id> --outcome correct\|incorrect` | Record the human verdict; builds the trust-tier track record |
| `avs triage <incident-file> [--fix]` | Gate 6 — triage + root cause; `--fix` approves a fix-PR attempt |
| `avs replay [<review-id>]` | Audit trail of any past review |
| `avs bench` | Regression benchmark (bars: recall ≥40%, precision ≥50%) |
| `avs compound [--pr]` | Weekly signal aggregation → CLAUDE.md proposal |
| `avs serve` | Webhook mode (needs `AUTOPRODUCT_WEBHOOK_SECRET`) |
| `avs readiness` | Substrate-ladder report (docs 18–19): active stages at the declared rung, what each missing rung unlocks |
| `avs preflight [--strict]` | Ready to build? — the six live checks the enterprise Studio card renders (model credential, git identity, forge auth, governance, substrate, Studio access) + the posture line; `--strict` exits 1 on any gap, so a pipeline can gate on readiness |
| `avs evidence-bundle <review-id>` | Export the Gate-R evidence bundle (unsigned v0) for CAB/change-control submission |
| `avs toolchain <language> [--manifest seeded.yaml]` | Run a language lane's det_tools slots (skipped = loud, never clean); with a seeded-defect manifest, measure catch-rate and register (or label PROVISIONAL) |
| `avs calibrate <language>` | Calibrate seeded-lane patterns against real scanners; per-defect report with actual slot output for each miss (run via `make calibrate`) |
| `avs eval-gate <scores.yaml> [--pin]` | Eval-set regression gate vs the pinned baseline; `--pin` re-baselines (commit the diff via PR) |
| `avs idempotency <run_a> <run_b>` | Backfill idempotency: the fixture-slice re-run must be byte-identical |
| `avs data-checks` | Run the workspace's external data checks (dbt auto-detected; others in `.mas/data-checks.yaml`) |
| `avs attest [<review-id>]` | Chain a review's gate/verdict records into the hash-chained attestation ledger, then verify the chain |
| `avs dwell` | Approval-dwell-time report (F-18.3): flags the rubber-stamp pattern (fast acks + zero overrides) |
| `avs cab-package <review-id>` | Assemble a CAB change package (evidence bundle + prefill) and run the Gate-R preflight; humans complete rollback/approver and submit |
| `avs mp-runtime` | 小程序 only: open the project in WeChat DevTools and visit every registered page — see below |
| `avs cost` | What this workspace spent this month, per model — a statement, never a verdict |
| `avs prices [--import]` | Published list prices with a source and a date; `--import` writes them into `.mas/cost-model.yaml` — see below |

### Cost visibility (`avs cost`, `avs prices`)

Spend is **measured and reported, never gated** (ADR-032): every call is
billed to your own key or subscription, so spending limits belong to the
provider account that does the billing — set them there. What the framework
owes you is the number: the build report ends with what the run cost, `avs
cost` prints the month per model, and the Studio shows spend on the confirm
page before the first dollar.

With no prices configured, every call is **UNPRICED** and the month's total
is a **floor** — honest, but not a dollar figure. `avs prices --import`
writes sourced list prices into `.mas/cost-model.yaml` so the surfaces
above can answer in dollars:

```
avs prices              # the table, with its sources and age
avs prices --import     # write them into the workspace
```

Three properties, because a price is a claim like any other here:

- **Nothing is invented.** Every entry cites the vendor pricing page and the
  date it was read — the standard `claim_lint` applies to every other number
  in this repo. These are **list** prices, not necessarily yours: enterprise
  agreements, credits, subscriptions, Bedrock/Vertex rates and batch
  discounts all differ. Correct them in `.mas/cost-model.yaml`; a price
  already there is never overwritten by a later import unless you pass
  `--overwrite`.
- **A range resolves upward.** Under-counting is the failure that matters
  for a figure someone budgets around. Sonnet 5's introductory rate is lower
  than the standard one it carries; Gemini's larger-prompt tier is the one
  recorded. The estimate is a ceiling, and each such entry says why.
- **A model with no sourced price stays unpriced and is named.** The total is
  labelled a floor rather than quietly counting that call as zero.

### The run retries its own failures (auto-retry)

A failed task used to end the story with a retry button the founder had to
press — and the bench record shows that button usually worked (t1/t2
recovered on the second pass, t5/t9 built on retry). Pressing it takes no
judgment, only patience, so the run presses it itself:

- **One bounded pass**, after the first pass over the plan, in dependency
  order — a task that failed because its dependency failed retries *after*
  the dependency recovered. Never recursive.
- **The retry knows why the last attempt died.** The previous attempt's
  status, detail, and test summary travel into both the spec writer's and
  the implementer's prompts as `<previous_attempt_failed>` — a retry is a
  different attempt, not a replay. `avs retry-task` passes the recorded
  failure from `outcomes.yaml` the same way.
- **Mechanical failures only** (`spec_blocked`, `build_failed`, `error`,
  `merge_conflict` — exactly the statuses the report already calls ours).
  Human judgment gates are untouched: the FDR questions, the plan
  confirmation, and review escalations still wait for you.
- **Everything is recorded** in the report's auto-approvals: which tasks
  were retried, with what context, and which recovered.

### 小程序 runtime verification (`avs mp-runtime`)

The build gate's loadability check is **static**: it reads `app.json` and
asks whether DevTools *would* open the project. Whether the pages then
render is a different question, and it needs the real thing.

Three preconditions, each a visible skip when missing:

1. **WeChat DevTools**, the desktop app — macOS or Windows. This can never
   run in CI; `ubuntu-latest` cannot run it at all.
2. **`miniprogram-automator`** in the workspace: `npm i -D miniprogram-automator`.
3. **DevTools' service port**, switched on once by you: 设置 → 安全设置 →
   服务端口 (Settings → Security → Service Port). The framework will not
   flip a security setting on your machine.

With the port off, the automator just times out — the CLI's own
`IDE service port disabled` message never reaches it. That case is reported
as **skipped**, not failed: nothing was checked, and a red result would
read as "your pages are broken".

## Substrate ladder (traditional-industry adoption, docs 18–19)

Opt-in: declare `.mas/substrate-profile.yaml` (schema in §18.47.1) and
stages below their infrastructure floor refuse with `STAGE_INACTIVE`
(exit code 4) instead of running vacuously — `deploy-review` degrades to
config-lint-only from S1 and says so. No profile file = no gating
(effective S4, unchanged behavior). Gate R rejections are recorded with
`ai_venture_studio.adoption.record_rejection` — mechanizable reasons become
preflight fixtures in `.mas/cab-preflight.yaml`, the rest land in
`.mas/cab-rejections.yaml` for the compounding loop. CAB submission
itself is human-only, always.

**Toolchain calibration (§19 G7).** The seeded-lane manifest patterns in
`tests/toolchains/seeded/{java,dotnet}/seeded.yaml` are hand-labels until a
real scanner run confirms them. `make calibrate` builds the
`Dockerfile.calibrate` image (Checkstyle, PIT, Semgrep, OWASP
Dependency-Check, Stryker, dotnet SDK) and runs `avs calibrate` per
lane, writing per-defect reports to `.mas/calibration/<lang>.yaml` and a
`calibration-summary.md` roll-up. A **miss on a slot that ran** means the
pattern is wrong — fix it in the manifest using the slot output the report
captured; a **skipped slot** means the scanner is absent. Re-run on every
scanner version bump (R-G3). `make calibrate-local` runs it on the host if
the scanners are already installed.

## Weekly rhythm

Start with `avs cadence --repo-dir <workspace>`. It reads the artifacts the
loops already write and reports which of `compound`, `sweep` and `bench` is
overdue. Exit 3 means something needs doing, so it can gate a script.

```bash
avs cadence --repo-dir ~/work/my-product          # what is overdue
avs cadence --repo-dir ~/work/my-product --run-due # run the due ones now
avs cadence --repo-dir ~/work/my-product --install --arm  # daily, 09:00
```

Point it at the **workspace**, not this repo — `.mas/` is where the loops'
state accumulates, and a scheduler aimed at a checkout reports loops
that have never run, correctly and uselessly.

`bench` is the exception, and it appears only in a checkout of this
framework: it watches `benchmarks/results/*.yaml`, the series the launch
PRD's only remaining kill criterion reads (O-L2, `avs bench-criterion`). A
workspace with no `benchmarks/products-real/` is not told it owes a bench.
That checkout therefore wants its **own** agent, filtered to that one loop
and labelled so it does not overwrite the product workspace's:

```bash
avs cadence --repo-dir ~/src/ai-venture-studio --only bench \
  --label ai.venture.studio.bench --install --notify --arm --at 09:07
```

One label is one scheduled job. Installing a second workspace under the
shared label silently retargets the first, and you would find out only by
noticing that nothing had run.

What it refuses to do quietly:

- A loop that never ran reads as **never run**, not as fresh.
- A loop that ran on time over an empty window is reported as *ok, empty* —
  keeping a cadence over nothing is not the same as doing the work.
- A scheduler running an **older build than the one you released** is a
  finding, with the exact `pip install --upgrade` line for that install. A
  green publish moves PyPI and moves nothing on your machine; the plist
  names an absolute path to whichever install was on PATH when you armed it.

On `--install`: launchd does not read a login shell, so credentials exported
from `.zshrc` are absent at 09:00. The plist carries `*_KEY_FILE` pointers
and non-secret settings; raw keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)
are **refused by name**, because `~/Library/LaunchAgents` is readable and a
key written there turns the scheduler into a credential leak. Convert the
variable to its `_FILE` form. Installing never starts a run — `--arm` loads
it, and it fires on its own schedule after that. Logs:
`~/Library/Logs/ai-venture-studio/loops.log`, or `<label-suffix>.log` for a
labelled agent. On Linux, run `avs cadence --run-due` from a systemd timer;
the check is portable and only the LaunchAgent is macOS-only.

**Not cron, on macOS.** cron *skips* a job whose minute passed while the
machine was asleep; launchd *runs* it on wake. The bench was on a Monday
09:07 crontab entry from 2026-07-27 and fired zero times in sixteen days,
which is how the only kill criterion left came to be reading a dead series
(ADR-034).

**Do not plan to read that log.** Nobody does, and a machine that notices
correctly and tells nothing that listens has failed in the same way the
loops exist to prevent. Send the alert to where you already are:

```bash
avs cadence --set-webhook '<Discord webhook URL>'   # stored 0600, never echoed
avs cadence --install --notify --arm
avs cadence --force-notify                          # test message, now
```

The webhook URL is a credential — whoever holds it can post into that
channel as this app — so it is stored outside the plist and found by the
scheduled run with no environment setup at all. What arrives is only what
needs a person: no daily all-green (a channel that speaks every morning is
one you mute, along with the message that mattered), the same unchanged
alert at most once a week, and each line carrying the command to paste
rather than the diagnosis to interpret. `--notify` does not change the exit
code — telling someone is not fixing it.

Two kinds of thing arrive. A loop that is **late** comes with the command
that closes it. A loop that **broke this morning** comes with its exit code
and the tail of its output, named first and in the heading, because that is
the one worth interrupting a day for. A loop that was not due, or that
succeeded loudly, says nothing. Every loop here can close itself, so a
non-zero exit is a failure with no exceptions — the one loop that used to
exit non-zero by design was withdrawn in v0.81.0 (ADR-033).
What this cannot tell you is that `avs cadence` itself crashed before it
got as far as sending — for that, and only that, the log is still the
record.

Then, weekly:

1. `avs compound --pr` — review and merge (or close) the proposal.
2. `avs bench` — must PASS; a regression after merging a compound
   PR means Gate 4: revert the CLAUDE.md change.
3. Skim `.mas/voters/*/log.yaml` block rates; a voter blocking repeatedly
   is a prompt/tool problem, not noise.
4. Approve or delete any `status: proposed` files in
   `.mas/learned-skills/`.
5. In this repo only: commit the week's `benchmarks/results/result-*.yaml`.
   The bench agent runs the bench and writes the file; it does not push. The
   criterion reads your working tree either way — committing is what makes
   the series survive losing the machine, which it has done once before.

## When a review escalates (Gate 3)

A GitHub Issue opens with the findings and a resume command. Decide:
- `--decision ack` — the verdict stands (it will block merge).
- `--decision override:<VERDICT>` — your call is recorded in the summary
  and `final.yaml`; the audit trail keeps both verdicts.

## Deploy trust tiers

Stage starts at `insight` (recommend only). After the configured streak of
correct PROMOTE marks (`promotion_track_record`, default 10), the summary
reports assistive-tier eligibility — graduating is your edit to
`.mas/deploy-policy.yaml`. Production deploys are never autonomous,
regardless of streak.

## Webhook mode

```
export AUTOPRODUCT_WEBHOOK_SECRET=<random>
avs serve --port 8422
```

Point a GitHub webhook (pull_request events, JSON, the same secret) at
`/webhook/github`; POST incidents to `/incidents`. Workers run detached;
`GET /reviews` lists results. Multi-instance operation wants the Celery
supervisor from the design docs — not included yet.

## Crash recovery and checkpoint encryption

All three graphs — code review, deploy review, maintenance — checkpoint
every super-step to `.mas/checkpoints.db`. `avs recover` (also
run automatically at `serve` startup) continues any run that has a
`meta.yaml` but no final mirror step from its last completed super-step;
a review paused at Gate 3 stays `awaiting_human`.

Set `AUTOPRODUCT_CHECKPOINT_KEY` (a raw passphrase or `secret://ENV_NAME`)
to encrypt checkpoint rows at rest (AES via pycryptodome). A key that
cannot be honored is a startup error, never a silent plaintext fallback;
each run's `meta.yaml` records `checkpoint_encryption: aes|off`. The YAML
mirrors stay plaintext on purpose — they are the human-readable audit
trail (doc 09 §6).

## Releasing to PyPI

The distribution is `ai-venture-studio`; the commands it installs are `avs`
(documented) and `autoproduct` (alias, so older scripts keep working).

Publishing is done by CI through **Trusted Publishing** — there is no API
token in the repo, in a secret, or on anyone's laptop. A one-time setup at
<https://pypi.org/manage/account/publishing/> registers this repository and
`publish.yml` as the publisher; after that a release is:

**Run `avs smoke` first. Every time.** v0.60.0 and v0.61.0 went to PyPI
unable to build a single task — the implementer's `max_tokens` made the SDK
refuse the request *before sending*, and 1441 hermetic tests were green
throughout, because a mock is written by the same person holding the same
wrong belief about the SDK. The smoke makes four real calls per configured
provider, costs a fraction of a cent on your own key, and checks the things
only a real call can: that a call returns text, that a large `max_tokens`
streams instead of raising, that a capped response is detectable as
truncated, and that the spend ledger saw it. A provider with no key is a
loud skip — and a skip is not a pass.

```
# 0. the live boundary, before anything else
avs smoke

# 1. bump the version and land it
#    pyproject.toml: version = "0.55.0"   (must match the CHANGELOG entry)
git commit -am "release: v0.55.0" && git push

# 2. tag it — this is what triggers the publish
git tag v0.55.0 && git push origin v0.55.0

# 3. published is not deployed — upgrade anything running on a schedule
avs cadence --repo-dir <workspace>   # exits 3 if the LaunchAgent is behind
```

Step 3 is not bookkeeping. The LaunchAgent's plist names an absolute path to
whichever `avs` install was on PATH when it was armed, which is not the
install you release from. v0.72.2 shipped a metering fix and the daily loop
went on running v0.72.1, silently, because nothing connected the two. The
cadence check now names both versions and prints the exact upgrade line for
that specific install.

The workflow runs the full suite on the tagged commit, checks the tag against
`pyproject.toml` (a mistyped tag fails instead of publishing a wrong number),
runs `twine check`, and only then uploads. The `pypi` environment can require
a manual approval if you want a human click before every release.

**A published version cannot be replaced, only yanked.** That is why the gate
is the whole suite rather than a smoke test, and why the version/tag check
exists.

One-off local publish (if you ever need it without CI): build, verify, then
upload with a token supplied by the environment — never pasted into a shell
that records history.

```
uv build && uvx twine check dist/*
UV_PUBLISH_TOKEN=pypi-... uv publish     # prefer: read it from a password manager
```

### The old distribution

`autoproduct` remains on PyPI at its last released version. PyPI has no
rename, so it stays there; `pip install autoproduct` keeps working and keeps
resolving to the old code. Two honest options, both deliberate rather than
accidental:

- **Leave it frozen** (current state) and point new users at the new name.
- **Publish one final `autoproduct` release** whose only change is a
  deprecation notice in the description pointing at `ai-venture-studio`.
  That requires temporarily setting `name = "autoproduct"` in a release
  branch, so it is a considered act, not a side effect.

## Safety boundaries (structural, not configurable)

- No auto-merge, no production deploys, no L3/L4 tools for any voter.
- Fix-PRs and compound PRs are proposals; humans merge.
- Deep-mode test runs use the docker T3 sandbox when available; the
  `sandbox` field in every test report says which path ran. Subprocess
  fallback = trusted repos only.

## Key hygiene

Provider keys live in the environment only. If a key may have leaked,
rotate it at the provider console and update `~/.zshrc` (or your secret
store); nothing under `.mas/` or git should ever contain one.

## Enterprise environments (GitLab, Bedrock/Vertex/Foundry, gateways, air-gap)

**Forge.** Review targets can be GitHub PR URLs (github.com or GitHub
Enterprise Server, via `gh`) or GitLab MR URLs (gitlab.com or self-managed,
via `glab`) — `.../-/merge_requests/<n>` URLs dispatch to `glab`
automatically, subgroups included. Comments, HITL issues, fix-MRs, merges
(still policy-gated per ADR-031), and diff acquisition all follow the
target's forge; authenticate the matching CLI (`gh auth login` /
`glab auth login --hostname <your-host>`) first. Azure DevOps and
Bitbucket PR URLs are *recognized and refused by name* — supporting them
is open work, and the refusal names the workaround (review the local
range). AWS CodeCommit closed to new customers in 2024 and is
deliberately not on the roadmap.

**Three entry points, most-locked-down first:**

1. **CI job (no webhook, no inbound surface)** — `avs review --from-ci`
   inside a GitLab CI merge-request pipeline (`rules: if:
   $CI_PIPELINE_SOURCE == "merge_request_event"`) or a GitHub Actions
   `pull_request` job derives the target from the CI's own predefined
   variables. This is the pattern for perimeters that cannot expose an
   endpoint at all.
2. **Webhooks** — `avs serve` accepts GitHub `pull_request` events at
   `/webhook/github` (HMAC `X-Hub-Signature-256`) and GitLab
   `merge_request` events at `/webhook/gitlab` (constant-time
   `X-Gitlab-Token` check; `open`/`reopen`/`update`-with-new-commits
   trigger, metadata edits do not). Both share
   `AUTOPRODUCT_WEBHOOK_SECRET` or per-tenant secrets.
3. **CLI** — `avs review <PR-or-MR-URL | git-range>` from any machine
   with the forge CLI authenticated.

**Model door.** Direct API is the default; `AVS_ANTHROPIC_MODE` selects
the enterprise routes. Model IDs are platform-native, passed verbatim
(Bedrock inference profiles/ARNs, Vertex `@`-versioned IDs, Foundry
deployment names) — put the platform's ID in your profile's model fields.

| Env | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | direct API (default) |
| `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_BASE_URL` | enterprise LLM gateway (LiteLLM-style Anthropic passthrough), bearer auth |
| `AVS_ANTHROPIC_MODE=bedrock` | AWS Bedrock (`pip install 'anthropic[bedrock]'`, AWS credential chain; `ANTHROPIC_BEDROCK_BASE_URL` honored by the SDK) |
| `AVS_ANTHROPIC_MODE=vertex` + `ANTHROPIC_VERTEX_PROJECT_ID` + `CLOUD_ML_REGION` | GCP Vertex (`pip install 'anthropic[vertex]'`, ADC; `ANTHROPIC_VERTEX_BASE_URL` honored) |
| `AVS_ANTHROPIC_MODE=foundry` + `ANTHROPIC_FOUNDRY_API_KEY` + `ANTHROPIC_FOUNDRY_RESOURCE` | Microsoft Foundry (Azure); model = your deployment name |

The optional cross-family voter seats re-point the same way:
`OPENAI_BASE_URL` (also the door to on-prem vLLM/NIM OpenAI-compatible
serving), `XAI_BASE_URL`, `GEMINI_BASE_URL`. Every mode errors loudly on
missing credentials; there is no silent fallback between doors.

**Secrets.** Every provider key and `secret://` reference also accepts
the Docker/K8s mounted-file convention: `ANTHROPIC_API_KEY_FILE=/run/secrets/key`
reads the mounted file instead of requiring the value in the process
environment. A configured `*_FILE` that cannot be read errors loudly.

**Network.** All HTTP clients honor `HTTPS_PROXY`/`NO_PROXY` and
`SSL_CERT_FILE` (TLS-inspection CAs) — nothing sets `verify=False`. The
complete outbound-host list, with the env var that re-points or disables
each, is the procurement pack's
[network-egress.md](editions/enterprise/procurement/network-egress.md):
internal PyPI mirror via `AVS_PYPI_JSON_BASE`, pinned local semgrep
rules via `AVS_SEMGREP_CONFIG` (metrics always off), screenshots as an
opt-in extra (`pip install 'ai-venture-studio[screenshots]'`) so the
base install never wants a browser download. `--provider mock` exercises
the full pipeline with zero model egress.

**Run it as a service.** The repo ships a `Dockerfile` (Studio by
default, `avs serve` for webhook mode). Non-loopback Studio binds are
fail-closed: `--host 0.0.0.0` refuses to start without
`AVS_STUDIO_TOKEN` (env or `AVS_STUDIO_TOKEN_FILE` secret mount), and
with it every request needs the token — open `/?token=<value>` once and
a cookie keeps the session. The token is a shared secret by design; for
SSO, put an OIDC reverse proxy (oauth2-proxy-class) in front and keep
the token as the proxy-to-studio hop. State is the workspace directory
(`.mas/` inside it) — mount it as a volume and back it up; one Studio
process per workspace (single-instance supervision; the Celery
multi-instance upgrade path is documented in server.py). Bare-metal
equivalent:

```ini
# /etc/systemd/system/avs-studio.service
[Service]
User=avs
WorkingDirectory=/srv/team-workspace
Environment=AVS_STUDIO_TOKEN_FILE=/etc/avs/studio-token
Environment=ANTHROPIC_API_KEY_FILE=/etc/avs/anthropic-key
ExecStart=/usr/local/bin/avs studio . --host 0.0.0.0 --port 8433
Restart=on-failure
```

**Windows.** Process-liveness probes and worker detachment are
cross-platform (`procs.pid_alive`; no bare `os.kill(pid, 0)` — on
Windows that terminates the probed process). A dev *clone* uses repo
symlinks and wants Developer Mode; installed wheels have no such
requirement. Windows CI is not yet part of the matrix — treat Windows
server mode as supported-by-construction, verified-on-request.

## Quick-tunnel webhook (dogfood setup)

For laptop-grade operation: `cloudflared tunnel --url http://localhost:8422`
gives an ephemeral public URL; register it as the repo webhook (pull_request
events, JSON, the AUTOPRODUCT_WEBHOOK_SECRET value). The tunnel URL changes
on every restart — update the webhook config when it does.
