"""Studio modes — the three doors (doc 24) reach the UI.

The editions system (ADR-U26/U27) already encodes who is at the keyboard —
solo founder, engineer, enterprise team — but the Studio rendered the same
page for all three. A mode is the UI-side reading of that choice:

- ``founder``    — the original flow, unchanged, and the default.
- ``engineer``   — adds a build-internals card: task IDs as the CLI takes
  them, verbatim states, and the command equivalent of every button.
- ``enterprise`` — adds a governance card: the resolved edition's substrate
  rung, WIP limit, gate-owner rule, never-batched gates, and the
  attestation-ledger count.

Same rule as editions themselves: a mode may only ADD visibility, never
remove a page, a form, or a required action — the UI analogue of
narrowing-never-widening (invariant 14.21). Every panel is read from the
same workspace files the CLI writes; the Studio stays a veneer, never a
second source of truth.

Resolution: an explicit ``--mode`` wins; otherwise the workspace's
``.mas/edition.yaml`` (solo→founder, engineer→engineer,
enterprise→enterprise); otherwise founder. An unknown explicit mode is a
loud startup error — same policy as a missing i18n key, because a Studio
serving the wrong audience quietly is worse than one that refuses to start.

Since v0.56 the mode is adaptable per request, never adaptive: a visible
switcher on every page (`mode_strip`) sets ``?mode=`` and a cookie; the
resolution above only supplies the default. The system never flips the
mode behind the user's back — auto-adapting UIs lose the user's trust in
where things are (Findlater & McGrenere, CHI 2004), and a mode that isn't
loudly visible invites mode errors.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Callable

import yaml
from ai_venture_studio.executables import resolve

MODES = ("founder", "engineer", "enterprise")
# solo maps to founder rather than sharing a name: the edition is a pipeline
# preset (WIP 1, weekly review), the mode is a reading depth — a solo founder
# wants the plain flow, which is exactly the founder page.
EDITION_TO_MODE = {"solo": "founder", "engineer": "engineer",
                   "enterprise": "enterprise"}


class StudioModeError(ValueError):
    """An explicit mode the Studio does not have. Startup refuses."""


def mode_strip(current: str, t_: Callable[[str], str]) -> str:
    """The always-visible mode switcher, as a segmented control in the
    header. Two redundant cues for the active segment (weight + beige fill,
    and no link) so the current state is never ambiguous, and the other
    modes stay discoverable from every page. Same ?mode= URLs, same cookie."""
    parts = []
    for mode in MODES:
        label = t_(f"mode_{mode}")
        if mode == current:
            parts.append(f"<span class='seg on'>{label}</span>")
        else:
            parts.append(f"<a class=seg href='/?mode={mode}'>{label}</a>")
    return f"<nav class=modeswitch>{''.join(parts)}</nav>"


def resolve_mode(root: Path, explicit: str | None = None) -> str:
    if explicit:
        mode = str(explicit).strip().lower()
        if mode not in MODES:
            raise StudioModeError(
                f"unknown studio mode {explicit!r}; modes: {', '.join(MODES)}"
            )
        return mode
    path = root / ".mas" / "edition.yaml"
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            # A hand-corrupted edition file must not take the Studio down;
            # founder mode still serves, and the enterprise panel is where
            # edition problems are surfaced loudly when asked for.
            return "founder"
        return EDITION_TO_MODE.get(str(raw.get("edition")), "founder")
    return "founder"


def recent_reviews(root: Path, limit: int = 10) -> list[dict]:
    """Bounded, newest-first review listing — the server.py `/reviews`
    pattern; an unbounded scan per page load is the bug it avoids."""
    reviews_dir = root / ".mas" / "reviews"
    if not reviews_dir.is_dir():
        return []
    rows = []
    newest_first = sorted(
        (d for d in reviews_dir.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )[: max(1, min(limit, 50))]
    for review_dir in newest_first:
        final = sorted(review_dir.glob("[0-9]*-final.yaml"))
        verdict = None
        if final:
            try:
                data = yaml.safe_load(final[-1].read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
            verdict = data.get("verdict")
        rows.append({"review_id": review_dir.name, "verdict": verdict})
    return rows


def voter_health(root: Path) -> list[dict]:
    """Per-voter invocation counts from `.mas/voters/*/log.yaml` — the same
    raw material the compounding loop reads, summarized instead of hidden
    inside a weekly proposal."""
    voters_dir = root / ".mas" / "voters"
    if not voters_dir.is_dir():
        return []
    rows = []
    for voter_dir in sorted(d for d in voters_dir.iterdir() if d.is_dir()):
        log_path = voter_dir / "log.yaml"
        if not log_path.exists():
            continue
        try:
            entries = yaml.safe_load(log_path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError:
            continue
        blocked = sum(
            1 for e in entries if str(e.get("status", "")).startswith("BLOCKED")
        )
        substituted = sum(1 for e in entries if e.get("substituted_from"))
        rows.append({
            "voter": voter_dir.name, "total": len(entries),
            "blocked": blocked, "substituted": substituted,
        })
    return rows


def review_timeline_body(root: Path, review_id: str,
                         t_: Callable[[str], str], *,
                         reviews_dir: Path | None = None,
                         evidence: bool = True) -> str:
    """One review's mirror as a step table — `avs replay` in the browser,
    reading the same NN-<node>.yaml files.

    `reviews_dir` overrides where the mirror is read from, which is how the
    vendored demo bundle (`avs replay --demo`) renders here with no
    workspace and no key. `evidence` drops the export form for a review
    this workspace did not run — there is nothing of its own to attest.
    """
    from ai_venture_studio.replay import load_replay, summarize_step

    replay = load_replay(reviews_dir or (root / ".mas" / "reviews"), review_id)
    rows = "".join(
        f"<tr><td>{step.step}</td><td><code>{html.escape(step.node)}</code></td>"
        f"<td>{html.escape(summarize_step(step))}</td></tr>"
        for step in replay.steps
    )
    verdict = replay.verdict or "—"
    duration = f"{replay.duration_s:.1f}s" if replay.duration_s else "—"
    export = (
        f"<form method=post action='/review/{html.escape(review_id)}/evidence'>"
        f"<button class=secondary>{t_('btn_evidence')}</button></form>"
        f"<p class=muted>{t_('evidence_note')}</p>"
        if evidence else ""
    )
    return (
        f"<p>{t_('review_verdict')}: <b>{html.escape(str(verdict))}</b> · "
        f"{t_('review_duration')}: {html.escape(duration)}</p>"
        f"<table>{rows}</table>"
        f"{export}"
        f"<p><a href='/'>{t_('link_back')}</a></p>"
    )


def engineer_panel(root: Path, t_: Callable[[str], str],
                   tasks: list[dict], spend_detail: str = "") -> str:
    """Everything an engineer wants, reordered summary-first. Every datum
    the pre-redesign panel showed is still here; nothing founder-facing is
    removed (add-only, invariant 14.21 read UI-side)."""
    profile = ""
    project = root / ".mas" / "project.yaml"
    if project.exists():
        try:
            data = yaml.safe_load(project.read_text(encoding="utf-8")) or {}
            profile = str(data.get("profile", ""))
        except yaml.YAMLError:
            profile = ""

    # Top summary strip: verdict chip + counts + a jump to the founder page.
    built = sum(1 for task in tasks if task["state"] == "built")
    failed = [t for t in tasks if t["state"] not in ("built", "pending")]
    chip = ""
    if tasks:
        if failed:
            chip = f"<span class='chip a'>{t_('chip_partly')}</span>"
        elif built == len(tasks):
            chip = f"<span class='chip g'>{t_('chip_built')}</span>"
    counts = t_("rep_modules_fmt").format(done=built, total=len(tasks))
    profile_frag = (
        f" · {t_('eng_profile')}: <code>{html.escape(profile)}</code>"
        if profile else ""
    )
    strip = (
        "<div class=estrip><span>"
        f"{chip} <span>{counts}{profile_frag}</span></span>"
        f"<a href='#founder'>{t_('eng_founder_link')}</a></div>"
    )

    if tasks:
        rows = ""
        for task in tasks:
            is_failed = task["state"] not in ("built", "pending")
            retry = (
                "<form method=post action=/retry style='display:inline'>"
                f"<input type=hidden name=task_id value='{html.escape(task['id'])}'>"
                f"<button>{t_('btn_retry')}</button></form>"
                if is_failed else ""
            )
            state_css = " class=warn" if is_failed else ""
            rows += (
                f"<tr{' class=arow' if is_failed else ''}>"
                f"<td><code>{html.escape(task['id'])}</code></td>"
                f"<td{state_css}>{html.escape(task['state'])}</td>"
                f"<td>{html.escape(task['title'])}</td>"
                f"<td>{retry}</td></tr>"
            )
        table = (
            f"<table><tr><th>{t_('eng_col_id')}</th>"
            f"<th>{t_('eng_col_state')}</th>"
            f"<th>{t_('eng_col_title')}</th><th></th></tr>{rows}</table>"
        )
    else:
        table = f"<p class=muted>{t_('eng_no_plan')}</p>"

    reviews = recent_reviews(root)
    if reviews:
        review_rows = "".join(
            f"<tr><td><a href='/review/{html.escape(r['review_id'])}'>"
            f"<code>{html.escape(r['review_id'])}</code></a></td>"
            f"<td>{html.escape(str(r['verdict'] or '…'))}</td></tr>"
            for r in reviews
        )
        reviews_block = (
            f"<div><div class=lbl>{t_('eng_reviews')}</div>"
            f"<table>{review_rows}</table></div>"
        )
    else:
        reviews_block = (
            f"<div><div class=lbl>{t_('eng_reviews')}</div>"
            f"<p class=muted>{t_('eng_reviews_none')}</p></div>"
        )
    voters = voter_health(root)
    if voters:
        voter_rows = "".join(
            f"<tr><td><code>{html.escape(v['voter'])}</code></td>"
            f"<td>{v['total']}</td><td>{v['blocked']}</td>"
            f"<td>{v['substituted']}</td></tr>"
            for v in voters
        )
        voters_block = (
            f"<div><div class=lbl>{t_('eng_voter_health')}</div>"
            f"<p class=muted>{t_('eng_voter_cols')}</p>"
            f"<table>{voter_rows}</table></div>"
        )
    else:
        voters_block = "<div></div>"
    hints = (
        f"<div class=cliblock><div class=lbl>{t_('eng_cli')}</div>"
        f"<pre>{html.escape(t_('eng_cli_body'))}</pre></div>"
    )
    return (
        f"<section class=engpanel>{strip}"
        f"<h1>{t_('h_engineer')}</h1>"
        f"<p class=muted>{t_('mode_note_engineer')}</p>"
        f"{table}"
        f"<div class=twocol>{reviews_block}{voters_block}</div>"
        f"{spend_detail}{hints}</section>"
    )


def enterprise_panel(root: Path, t_: Callable[[str], str]) -> str:
    """The governance spokes render independently: a workspace without an
    edition file still has a stage ladder, a dwell distribution, and an
    automation arming state worth seeing.

    Order follows the enterprise-dashboard convention (posture verdict →
    readiness + trust/procurement facts → the evidence as drill-downs): the
    one-line answer first, the security reviewer's questions second, the
    evidence below — a card that needs attention arrives already open.
    Every pre-redesign card is still present; only the order and the
    default-open state changed (add-only, never remove)."""
    posture = governance_posture(root)
    attention = set(posture["attention"])

    def _evidence(label: str, spokes: tuple[str, ...],
                  card_html: str) -> str:
        opened = " open" if attention.intersection(spokes) else ""
        return (
            f"<details class=evd{opened}><summary>{label}</summary>"
            f"{card_html}</details>"
        )

    evidence = (
        f"<div class=lbl>{t_('gov_evidence')}</div>"
        + _evidence(t_("code_head"), (), _codebase_html(root, t_))
        + _evidence(t_("h_governance"), ("edition", "attestation"),
                    _edition_card(root, t_))
        + _evidence(t_("gov_deploys"), (), _deploy_reviews_html(root, t_))
        + _evidence(t_("gov_dwell"), ("gate dwell",), _dwell_html(root, t_))
        + _evidence(t_("gov_stages"), ("substrate",),
                    _stage_grid_html(root, t_))
        + _evidence(t_("gov_automation"), ("automation policies",),
                    _automation_html(root, t_))
    )
    footer = (
        f"<div class=panelfoot>{t_('gov_deploys_note')}</div>"
    )
    return (
        "<section class=govpanel>"
        + _posture_html(root, t_, posture)
        + f"<div class=twocol>{_preflight_html(root, t_)}"
        + f"{_trust_html(root, t_)}</div>"
        + evidence + footer + "</section>"
    )


def _deploy_reviews_html(root: Path, t_: Callable[[str], str]) -> str:
    """Gate 5 history where the gate owner already is: the last deploy
    recommendations, newest first, from the same mirrors `avs
    deploy-review` writes. Grey with the command when none have run —
    absence of deploy review must never read as reviewed-and-fine."""
    head = f"<b>{t_('gov_deploys')}</b>"
    base = root / ".mas" / "deploy-reviews"
    runs = sorted(
        (d for d in base.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime, reverse=True,
    )[:5] if base.is_dir() else []
    if not runs:
        return (
            f"<div class=card>{head}"
            f"<p class=muted>{t_('gov_no_deploys')} "
            f"<code>avs deploy-review main...HEAD</code></p></div>"
        )
    rows = ""
    for run_dir in runs:
        final = sorted(run_dir.glob("[0-9]*-final.yaml"))
        verdict, branch = "…", ""
        if final:
            try:
                data = yaml.safe_load(final[-1].read_text(encoding="utf-8")) or {}
                verdict = str(data.get("verdict", "…"))
                branch = str(data.get("branch", ""))
            except yaml.YAMLError:
                verdict = "?"
        rows += (
            f"<tr><td><code>{html.escape(run_dir.name)}</code></td>"
            f"<td>{html.escape(verdict)}</td>"
            f"<td class=muted>{html.escape(branch)}</td></tr>"
        )
    # The recommendations-never-executions note moved to the panel footer,
    # where it is visible without opening this card.
    return f"<div class=card>{head}<table>{rows}</table></div>"


def build_preflight(root: Path) -> list[dict]:
    """Can this workspace's team actually build software TODAY? One row
    per prerequisite: state ('ready' | 'todo'), what was found, and the
    exact fix. Everything is read live — env, git config, the forge CLI's
    own auth check — never cached, never guessed."""
    import os
    import subprocess

    from ai_venture_studio import forge as forge_mod
    from ai_venture_studio.secrets import env_or_file

    rows: list[dict] = []

    # 1 · model credential (any door counts; mock counts for evaluation).
    mode = (os.environ.get("AVS_ANTHROPIC_MODE", "").strip().lower() or "direct")
    has_credential = bool(
        env_or_file("ANTHROPIC_API_KEY") or env_or_file("ANTHROPIC_AUTH_TOKEN")
        or mode in ("bedrock", "vertex", "foundry")
    )
    rows.append({
        "item": "model", "state": "ready" if has_credential else "todo",
        "found": f"door: {mode}" if has_credential else "no credential",
        "fix": "export ANTHROPIC_API_KEY(_FILE) — or "
               "AVS_ANTHROPIC_MODE=bedrock|vertex|foundry; "
               "--provider mock evaluates with no key",
    })

    # 2 · git identity — commits carry the team's name, not nothing.
    try:
        email = subprocess.run(
            [resolve("git"), "config", "user.email"], cwd=root,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        email = ""
    rows.append({
        "item": "git identity", "state": "ready" if email else "todo",
        "found": email or "user.email unset",
        "fix": 'git config user.email "you@company.com"',
    })

    # 3 · forge CLI authenticated — reviews post nowhere without it.
    remote = forge_mod._remote_forge(str(root))
    if remote is None:
        rows.append({
            "item": "forge", "state": "todo",
            "found": "no GitHub/GitLab origin remote",
            "fix": "git remote add origin <your forge URL>",
        })
    else:
        status, note = forge_mod.auth_status(remote)
        cli = {"github": "gh", "gitlab": "glab"}[remote]
        rows.append({
            "item": "forge", "state": "ready" if status == "ready" else "todo",
            "found": note,
            "fix": f"{cli} auth login" if status == "unauthenticated"
                   else f"install {cli}, then {cli} auth login",
        })

    # 4 · workspace governance — profile, edition, a named gate owner.
    project = root / ".mas" / "project.yaml"
    edition_path = root / ".mas" / "edition.yaml"
    owner = ""
    if edition_path.exists():
        try:
            raw = yaml.safe_load(edition_path.read_text(encoding="utf-8")) or {}
            owner = str((raw.get("gate_policy") or {}).get("gate_owner") or "")
        except yaml.YAMLError:
            owner = ""
    governed = project.exists() and edition_path.exists() and bool(owner)
    rows.append({
        "item": "governance", "state": "ready" if governed else "todo",
        "found": f"gate owner: {owner}" if governed else (
            "no gate owner" if edition_path.exists()
            else "no edition declared"
        ),
        "fix": 'avs init . --profile enterprise-web --edition enterprise '
               '--gate-owner "<name>"',
    })

    # 5 · substrate declared — otherwise every stage runs ungated.
    rows.append({
        "item": "substrate",
        "state": "ready" if (root / ".mas" / "substrate-profile.yaml").exists()
        else "todo",
        "found": "declared"
        if (root / ".mas" / "substrate-profile.yaml").exists()
        else "not declared (stages ungated, effective S4)",
        "fix": "avs readiness prints a detected starter profile to save",
    })

    # 6 · Studio access posture — never a blocker (localhost-only is a
    # valid way to build), so the guidance rides in the found text.
    token_set = bool(env_or_file("AVS_STUDIO_TOKEN"))
    rows.append({
        "item": "studio access", "state": "ready",
        "found": "token-gated (AVS_STUDIO_TOKEN set)" if token_set
        else "localhost-only — set AVS_STUDIO_TOKEN(_FILE) to serve a team",
        "fix": "",
    })
    return rows


def _preflight_html(root: Path, t_: Callable[[str], str]) -> str:
    rows = build_preflight(root)
    ready = sum(1 for r in rows if r["state"] == "ready")
    cells = ""
    for r in rows:
        icon = "✅" if r["state"] == "ready" else "◻️"
        fix = (
            f"<br><code>{html.escape(r['fix'])}</code>"
            if r["state"] != "ready" else ""
        )
        cells += (
            f"<tr><td>{icon} {html.escape(r['item'])}</td>"
            f"<td>{html.escape(r['found'])}{fix}</td></tr>"
        )
    return (
        f"<div class=card><b>{t_('pre_head')}</b> "
        f"<span class=muted>{ready}/{len(rows)}</span>"
        f"<p class=muted>{t_('pre_note')}</p>"
        f"<table>{cells}</table></div>"
    )


def governance_posture(root: Path) -> dict[str, list[str]]:
    """Machine-readable spoke states: measured / unconfigured / attention.

    'Unconfigured' is its own state on purpose — a grey answer. A dashboard
    that renders green over an unmeasured workspace teaches its readers to
    ignore green (the GitHub-security-overview lesson: 'not enabled' is
    never 'healthy')."""
    from ai_venture_studio.adoption.attestation import verify_ledger
    from ai_venture_studio.adoption.dwell import gate_dwell_report
    from ai_venture_studio.adoption.substrate import load_substrate_profile
    from ai_venture_studio.automation import (
        AUTOMERGE_POLICY,
        DEPLOY_EXEC_POLICY,
        PolicyError,
        load_policy,
    )
    from ai_venture_studio.editions import EditionError, load_workspace_edition

    posture: dict[str, list[str]] = {
        "measured": [], "unconfigured": [], "attention": [],
    }

    try:
        edition = load_workspace_edition(root)
        if edition is None:
            posture["unconfigured"].append("edition")
        else:
            posture["measured"].append("edition")
    except EditionError:
        posture["attention"].append("edition")

    try:
        substrate = load_substrate_profile(root)
    except ValueError:
        posture["attention"].append("substrate")
    else:
        posture[
            "unconfigured" if substrate is None else "measured"
        ].append("substrate")

    dwell = gate_dwell_report(root)
    if dwell.median_s is None:
        posture["unconfigured"].append("gate dwell")
    elif dwell.rubber_stamp:
        posture["attention"].append("gate dwell")
    else:
        posture["measured"].append("gate dwell")

    if not (root / ".mas" / "attestation" / "ledger.jsonl").exists():
        posture["unconfigured"].append("attestation")
    else:
        try:
            posture[
                "measured" if verify_ledger(root).ok else "attention"
            ].append("attestation")
        except ValueError:
            posture["attention"].append("attestation")

    # Policies always have a knowable state (absent file = disarmed default),
    # so they count as measured unless the file itself is invalid.
    policy_state = "measured"
    for filename in (AUTOMERGE_POLICY, DEPLOY_EXEC_POLICY):
        try:
            load_policy(root, filename)
        except PolicyError:
            policy_state = "attention"
    posture[policy_state].append("automation policies")
    return posture


def _posture_html(root: Path, t_: Callable[[str], str],
                  posture: dict[str, list[str]] | None = None) -> str:
    """The one-line answer, first, as a three-cell strip. Unconfigured is
    grey — its own dot color, never green (not enabled is not healthy)."""
    posture = posture or governance_posture(root)
    cells = ""
    for state, dot, css, label_key in (
        ("attention", "red", "bad", "gov_posture_attention"),
        ("measured", "green", "ok", "gov_posture_measured"),
        ("unconfigured", "grey", "muted", "gov_posture_unmeasured"),
    ):
        items = posture[state]
        cells += (
            "<div class=pcell>"
            f"<div class=stateline><span class='sdot {dot}'></span>"
            f"<span class='slabel {css}'>{t_(label_key)} "
            f"{len(items)}</span></div>"
            f"<div>{html.escape(', '.join(items)) or '—'}</div>"
            "</div>"
        )
    return (
        f"<h1>{t_('gov_posture')}</h1>"
        f"<div class=posture>{cells}</div>"
        f"<p class=muted>{t_('gov_posture_note')}</p>"
    )


def _trust_html(root: Path, t_: Callable[[str], str]) -> str:
    """The procurement answers, on screen: which model door, authenticated
    how (presence only — never a value), which forge, what leaves the
    machine, and what this workspace has spent. A security reviewer should
    read this without a meeting."""
    import os

    from ai_venture_studio import spend as spend_mod
    from ai_venture_studio.forge import _remote_forge

    mode = (os.environ.get("AVS_ANTHROPIC_MODE", "").strip().lower()
            or "direct")
    if os.environ.get("ANTHROPIC_API_KEY"):
        auth = t_("trust_auth_env")
    elif os.environ.get("ANTHROPIC_API_KEY_FILE") or os.environ.get(
        "ANTHROPIC_AUTH_TOKEN_FILE"
    ):
        auth = t_("trust_auth_file")
    elif os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        auth = t_("trust_auth_gateway")
    else:
        auth = t_("trust_auth_none")
    auth_css = "muted" if auth == t_("trust_auth_none") else "ok"

    forge = _remote_forge(str(root))
    forge_cell = (
        f"<code>{html.escape(forge)}</code>" if forge
        else f"<span class=muted>{t_('trust_forge_none')}</span>"
    )

    try:
        summary = spend_mod.summarize(spend_mod.read_entries(root))
    except Exception:  # noqa: BLE001 — a corrupt ledger must not kill the page
        summary = None
    if summary is None or summary.calls == 0:
        spend_cell = f"<span class=muted>{t_('trust_spend_none')}</span>"
    else:
        floor = f"{t_('trust_spend_floor')} " if summary.is_floor else ""
        spend_cell = (
            f"{floor}${summary.usd:.2f} · {summary.calls} calls · "
            f"{summary.total_tokens:,} tokens"
        )

    rows = (
        f"<tr><td>{t_('trust_provider')}</td>"
        f"<td><code>{html.escape(mode)}</code> · "
        f"<span class={auth_css}>{auth}</span></td></tr>"
        f"<tr><td>{t_('trust_forge')}</td><td>{forge_cell}</td></tr>"
        f"<tr><td>{t_('trust_egress')}</td>"
        f"<td class=muted>{t_('trust_egress_note')}</td></tr>"
        f"<tr><td>{t_('trust_spend')}</td><td>{spend_cell}</td></tr>"
    )
    return (
        f"<div class=card><b>{t_('trust_head')}</b>"
        f"<p class=muted>{t_('trust_note')}</p><table>{rows}</table></div>"
    )


def _codebase_html(root: Path, t_: Callable[[str], str]) -> str:
    """The brownfield what-we-found report (the Renovate-onboarding lesson:
    prove comprehension before asking for configuration). Reads the map
    `avs map` / `init --adopt` wrote; absent map is a grey state with the
    one command that fills it."""
    path = root / ".mas" / "codebase-map.yaml"
    head = f"<b>{t_('code_head')}</b>"
    if not path.exists():
        return (
            f"<div class=card>{head}"
            f"<p class=muted>{t_('code_none')} "
            f"<code>avs map .</code></p>"
            f"<p class=muted>{t_('gov_action_reload')}</p></div>"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return (
            f"<div class=card>{head}"
            f"<p class=bad>{t_('code_unreadable')}</p></div>"
        )
    langs = ", ".join(
        f"{lang} ({count})" for lang, count in (data.get("languages") or {}).items()
    )
    modules = sorted(
        data.get("modules") or [],
        key=lambda m: m.get("lines", 0), reverse=True,
    )
    entries = ", ".join(data.get("entry_points") or []) or "—"
    routes = len(data.get("routes") or [])
    top = "".join(
        f"<tr><td><code>{html.escape(str(m.get('name', '')))}</code></td>"
        f"<td>{m.get('files', 0)} files · {m.get('lines', 0):,} lines</td></tr>"
        for m in modules[:6]
    )
    more = (
        f"<p class=muted>+{len(modules) - 6} more modules</p>"
        if len(modules) > 6 else ""
    )
    return (
        f"<div class=card>{head}"
        f"<p>{html.escape(langs)} · {data.get('total_files', 0)} files · "
        f"{data.get('total_lines', 0):,} lines · "
        f"{t_('code_http')}: {routes}</p>"
        f"<p class=muted>{t_('code_entries')}: <code>{html.escape(entries)}"
        f"</code></p><table>{top}</table>{more}"
        f"<p class=muted>{t_('code_note')}</p></div>"
    )


def _edition_card(root: Path, t_: Callable[[str], str]) -> str:
    from ai_venture_studio.editions import EditionError, load_workspace_edition

    head = (
        f"<b>{t_('h_governance')}</b>"
        f"<p class=muted>{t_('mode_note_enterprise')}</p>"
    )
    try:
        edition = load_workspace_edition(root)
    except EditionError as exc:
        return (
            f"<div class=card>{head}<p class=bad>{t_('gov_edition_error')}: "
            f"{html.escape(str(exc))}</p></div>"
        )
    if edition is None:
        # Grey state, not a dead end: the exact command, what it changes,
        # and the feedback loop (this page re-reads the file every load).
        return (
            f"<div class=card>{head}<p class=warn>{t_('gov_no_edition')}</p>"
            f"<p><code>avs init . --profile enterprise-web --edition "
            f"enterprise --gate-owner \"&lt;name&gt;\"</code></p>"
            f"<p class=muted>{t_('gov_edition_effect')} "
            f"{t_('gov_action_reload')}</p></div>"
        )

    defaults = edition.defaults or {}
    policy = edition.gate_policy or {}
    never = ", ".join(sorted(str(g) for g in policy.get("never_consolidate", [])))
    rows = "".join(
        f"<tr><td>{label}</td><td>{value}</td></tr>"
        for label, value in (
            (t_("gov_edition"), f"<code>{html.escape(edition.edition)}</code>"),
            (t_("gov_rung"),
             html.escape(str(defaults.get("substrate_rung", "S0")))),
            (t_("gov_wip"), html.escape(str(defaults.get("wip_limit", "")))),
            (t_("gov_weekly"),
             html.escape(str((edition.attention or {}).get(
                 "weekly_review_minutes", "")))),
            (t_("gov_never"), html.escape(never)),
        )
    )
    owner = (
        t_("gov_gate_owner_yes") if policy.get("require_gate_owner")
        else t_("gov_gate_owner_no")
    )
    if policy.get("gate_owner"):
        # The named human, not just the rule — who is accountable is the
        # first thing a CAB asks.
        owner += f" <b>{html.escape(str(policy['gate_owner']))}</b>"
    return (
        f"<div class=card>{head}<table>{rows}</table>"
        f"<p>{owner}</p><p>{_attestation_html(root, t_)}</p></div>"
    )


def _attestation_html(root: Path, t_: Callable[[str], str]) -> str:
    """Chain verification, not a line count: the ledger's whole point is
    that tampering is detectable, so the panel detects it (recomputing the
    sha256 chain is O(entries), fine on a page load)."""
    from ai_venture_studio.adoption.attestation import verify_ledger

    ledger = root / ".mas" / "attestation" / "ledger.jsonl"
    if not ledger.exists():
        # Absence is stated, never omitted — a missing ledger must not read
        # as attested-and-clean.
        return f"<span class=muted>{t_('gov_no_ledger')}</span>"
    try:
        verification = verify_ledger(root)
    except ValueError as exc:
        # An unparseable line is tampering too — render it as broken, don't
        # take the page down.
        return (
            f"<span class=bad>{t_('gov_ledger_broken')} ?</span> — "
            f"{html.escape(str(exc))}"
        )
    if verification.ok:
        return (
            f"{t_('gov_attestations')}: <b>{verification.entries}</b> "
            f"<span class=ok>{t_('gov_ledger_ok')}</span>"
        )
    problems = "; ".join(verification.problems[:3])
    return (
        f"<span class=bad>{t_('gov_ledger_broken')} "
        f"{verification.first_bad_seq}</span> — {html.escape(problems)}"
    )


def _stage_grid_html(root: Path, t_: Callable[[str], str]) -> str:
    from ai_venture_studio.adoption.substrate import (
        STAGE_FLOORS,
        load_substrate_profile,
        stage_activation,
    )

    head = f"<b>{t_('gov_stages')}</b>"
    try:
        profile = load_substrate_profile(root)
    except ValueError as exc:
        # A malformed profile must not take the page down — render it as
        # broken with the loader's own message (it names the field).
        return (
            f"<div class=card>{head}"
            f"<p class=bad>{html.escape(str(exc))}</p></div>"
        )
    if profile is None:
        return (
            f"<div class=card>{head}"
            f"<p class=muted>{t_('gov_no_substrate')}</p>"
            f"<p><code>avs readiness</code></p>"
            f"<p class=muted>{t_('gov_substrate_effect')} "
            f"{t_('gov_action_reload')}</p></div>"
        )
    icons = {"ACTIVE": "✅", "DEGRADED": "⚠️", "STAGE_INACTIVE": "⛔"}
    rows = ""
    for stage in STAGE_FLOORS:
        activation = stage_activation(profile, stage)
        status = str(activation.status.value if hasattr(activation.status, "value")
                     else activation.status)
        note = f" <span class=muted>{html.escape(activation.note)}</span>" \
            if activation.note else ""
        rows += (
            f"<tr><td><code>{html.escape(stage)}</code></td>"
            f"<td>{icons.get(status, '')} {html.escape(status)}</td>"
            f"<td>{html.escape(activation.rung_present)} / "
            f"{html.escape(activation.rung_required)}{note}</td></tr>"
        )
    return (
        f"<div class=card>{head} <span class=muted>"
        f"({html.escape(profile.rung().label)})</span><table>{rows}</table></div>"
    )


def _dwell_html(root: Path, t_: Callable[[str], str]) -> str:
    """The F-18.3 rubber-stamp detector, on the page where the gate owner
    already is. Notes render verbatim — the report's own words, including
    'nothing to measure', are the honest rendering."""
    from ai_venture_studio.adoption.dwell import gate_dwell_report

    report = gate_dwell_report(root)
    stats = ""
    if report.median_s is not None:
        stats = (
            f"<p>{t_('gov_dwell_median')}: <b>{report.median_s:.0f}s</b> · "
            f"p90 {report.p90_s:.0f}s · "
            f"{t_('gov_override_rate')}: {report.override_rate:.0%} · "
            f"n={len(report.samples)}</p>"
        )
    css = "bad" if report.rubber_stamp else "muted"
    notes = "".join(
        f"<p class={css}>{html.escape(note)}</p>" for note in report.notes
    )
    return f"<div class=card><b>{t_('gov_dwell')}</b>{stats}{notes}</div>"


def _automation_html(root: Path, t_: Callable[[str], str]) -> str:
    from ai_venture_studio.automation import (
        AUTOMERGE_POLICY,
        DEPLOY_EXEC_POLICY,
        PolicyError,
        load_policy,
    )

    rows = ""
    for filename in (AUTOMERGE_POLICY, DEPLOY_EXEC_POLICY):
        name = html.escape(filename)
        try:
            policy = load_policy(root, filename)
        except PolicyError as exc:
            rows += (
                f"<tr><td><code>{name}</code></td>"
                f"<td class=bad>{t_('gov_policy_error')}: "
                f"{html.escape(str(exc))}</td></tr>"
            )
            continue
        if policy.enabled:
            rows += (
                f"<tr><td><code>{name}</code></td>"
                f"<td class=warn>{t_('gov_armed')} "
                f"{html.escape(policy.armed_by)} · {t_('gov_expires')} "
                f"{html.escape(policy.expires_at)}</td></tr>"
            )
        else:
            rows += (
                f"<tr><td><code>{name}</code></td>"
                f"<td class=ok>{t_('gov_disarmed')}</td></tr>"
            )
    return (
        f"<div class=card><b>{t_('gov_automation')}</b><table>{rows}</table></div>"
    )
