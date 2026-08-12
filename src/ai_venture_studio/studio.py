"""Founder Studio — the browser UI for the FDR flow.

`avs studio --repo-dir <workspace>` serves a single-page flow on
localhost: edit the FDR, get questions or the plain-language confirmation,
press the build button instead of typing --yes, watch progress, read the
build report. All state lives in the same workspace files the CLI writes —
the Studio is a veneer, never a second source of truth.

Local-first: binds 127.0.0.1, no external assets, no accounts. The build
runs as the same detached worker the CLI uses.

Every user-facing string comes from `studio_i18n` so `--lang en` renders the
whole flow in English. The default stays the original bilingual Chinese-first
text, so nothing changes for existing users.

Different users get different modes (`studio_modes`): founder is the
original UI unchanged; engineer and enterprise append read-only cards. The
mode resolves from the workspace's edition, `--mode` overrides, and a mode
may only ADD visibility — never remove a form or a required action.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ai_venture_studio import studio_chat
from ai_venture_studio.studio_i18n import DEFAULT_LANGUAGE, normalize, t
from ai_venture_studio.studio_modes import (
    MODES,
    StudioModeError,
    engineer_panel,
    enterprise_panel,
    mode_strip,
    recent_reviews,
    resolve_mode,
    review_timeline_body,
)

# Design tokens: warm paper ground with a 4px dot grid, content on a white
# card; ink #191813, hairlines #eae4d8; green #07c160 for accents and
# #0d7a45 for the one primary action per page; amber for warnings and
# partial builds; #6b6456 replaces the old #888 hint text (5.4:1, AA).
# Faces are system stacks only — nothing is fetched: serif display via
# Georgia with a 'Noto Serif SC' fallback, mono via ui-monospace, sans via
# -apple-system/'PingFang SC'.
_SANS = "-apple-system,'PingFang SC','Helvetica Neue',sans-serif"
_SERIF = "Georgia,'Times New Roman','Noto Serif SC',serif"
_MONO = "ui-monospace,SFMono-Regular,Menlo,monospace"

_STYLE = f"""
*{{box-sizing:border-box}}
body{{margin:0;padding:28px 16px 72px;background:#eae6dc;
background-image:radial-gradient(rgba(25,24,19,.045) 1px,transparent 1px);
background-size:4px 4px;color:#454037;-webkit-font-smoothing:antialiased;
font:15px/1.6 {_SANS}}}
a{{color:#0d7a45}}a:hover{{color:#07c160}}
h1{{font-family:{_SERIF};font-weight:500;font-size:27px;line-height:1.25;
letter-spacing:-.01em;color:#191813;margin:0 0 10px}}
h2{{font-family:{_SERIF};font-weight:500;font-size:20px;line-height:1.3;
letter-spacing:-.01em;color:#191813;margin:1.6em 0 .5em}}
h3{{font-family:{_SERIF};font-weight:500;font-size:17px;color:#191813;
margin:1.4em 0 .4em}}
h4{{font-size:15px;color:#191813;margin:1.2em 0 .3em}}
.shell{{max-width:900px;margin:0 auto;background:#fff;
border:1px solid #ded9cd;border-radius:12px;
box-shadow:0 1px 2px rgba(25,24,19,.05),0 12px 32px -16px rgba(25,24,19,.18);
overflow:hidden}}
.hdr{{display:flex;align-items:center;gap:10px;padding:14px 24px;
border-bottom:1px solid #eae4d8}}
.brand{{display:flex;align-items:center;gap:8px}}
.mark{{width:11px;height:11px;background:#07c160;transform:rotate(45deg);
border-radius:2px}}
.marklabel{{font:600 12px/1 {_MONO};letter-spacing:.1em;color:#6f6858}}
.hdrdiv{{width:1px;height:16px;background:#e5dfd0}}
.wsname{{font-weight:500;font-size:15px;color:#191813}}
.pchip{{font:12px/1 {_MONO};color:#6b6456;background:#f1ede2;
border-radius:4px;padding:4px 7px}}
.modeswitch{{display:flex;border:1px solid #ddd6c5;border-radius:7px;
overflow:hidden;margin-left:auto}}
.modeswitch .seg{{font-size:12px;line-height:1;padding:7px 12px;
text-decoration:none;color:#6b6456;border-left:1px solid #ddd6c5}}
.modeswitch .seg:first-child{{border-left:0}}
.modeswitch .seg.on{{font-weight:600;color:#191813;background:#f1ede2}}
.modenote{{display:flex;justify-content:space-between;align-items:center;
gap:16px;padding:10px 24px;border-bottom:1px solid #eae4d8;
background:#faf7ef;font-size:13px;color:#6b6456}}
.rail{{display:flex;align-items:center;gap:10px;padding:12px 24px;
border-bottom:1px solid #eae4d8;font-size:13px}}
.rline{{flex:1;height:1px;background:#e5dfd0}}.rline.on{{background:#07c160}}
.rdone{{color:#6b6456}}.rtodo{{color:#7d7666}}
.rcur{{display:flex;align-items:center;gap:7px;font-weight:600;
color:#191813}}
.pagebody{{padding:26px 28px}}
button{{min-height:44px;background:#fff;color:#191813;
border:1px solid #d3ccbb;border-radius:9px;padding:11px 18px;
font:600 14px/1 {_SANS};cursor:pointer}}
button.secondary{{background:#fff;color:#191813;border:1px solid #d3ccbb}}
button.primary{{background:#0d7a45;color:#fff;border:0;
box-shadow:0 1px 0 rgba(25,24,19,.06),0 6px 14px -8px rgba(13,122,69,.6);
padding:12px 22px;font-size:15px}}
button.linkish{{border:0;background:none;color:#6b6456;font-weight:400;
min-height:0;padding:0;box-shadow:none}}
button.linkish:hover{{color:#07c160}}
textarea{{width:100%;min-height:96px;font:15px/1.6 {_SANS};
padding:12px 14px;border:1px solid #d3ccbb;border-radius:9px;
color:#191813;resize:vertical;display:block}}
textarea.fdrbox{{min-height:340px;font-size:14px}}
pre{{white-space:pre-wrap;background:#faf7ef;border:1px solid #eae4d8;
padding:12px 14px;border-radius:8px;font:12px/1.7 {_MONO};color:#454037}}
code{{font:12px/1.5 {_MONO};color:#191813}}
.card{{border:1px solid #ded9cd;border-radius:11px;padding:14px 18px;
margin:14px 0}}
.callout{{background:#fcf6e9;border:1px solid #eddec4;border-radius:9px;
padding:12px 16px;margin:14px 0}}
.muted{{color:#6b6456;font-size:13px}}
.ok{{color:#0d7a45}}.warn{{color:#8a5a12}}.bad{{color:#b5352c}}
.lbl{{font:600 12px/1 {_MONO};letter-spacing:.09em;text-transform:uppercase;
color:#6f6858;margin:18px 0 8px}}
table{{border-collapse:collapse;width:100%}}
td,th{{padding:.45rem .6rem;border-bottom:1px solid #efe9dd;font-size:13px;
text-align:left;vertical-align:top;overflow-wrap:anywhere}}
/* A git identity or a paste-me command is one unbreakable token, and its
   min-content width is what pushed the preflight table over its column and
   into the trust table beside it. Value cells and inline code wrap
   anywhere; the label column keeps whole words, or auto table layout
   shrinks it to one character per line and reads as "governan/ce". */
td:first-child{{overflow-wrap:normal}}
code{{overflow-wrap:anywhere}}
th{{font:600 11px/1 {_MONO};letter-spacing:.06em;text-transform:uppercase;
color:#6f6858;background:#faf7ef}}
tr.arow td{{background:#fcf6e9}}
summary{{cursor:pointer}}
.chip{{display:inline-block;font:600 11px/1 {_SANS};letter-spacing:.05em;
text-transform:uppercase;border-radius:4px;padding:5px 8px}}
.chip.g{{color:#0d7a45;background:#e2f4ea}}
.chip.a{{color:#8a5a12;background:#fbecd3}}
.chip.q{{color:#6f6858;background:#f1ede2}}
.dot7{{width:7px;height:7px;border-radius:50%;background:#07c160;
display:inline-block;flex-shrink:0}}
.sdot{{width:9px;height:9px;border-radius:50%;display:inline-block;
flex-shrink:0}}
.sdot.red{{background:#b5352c}}.sdot.amber{{background:#c08a1a}}
.sdot.grey{{background:#a49d8b}}.sdot.green{{background:#07c160}}
.stateline{{display:flex;align-items:center;gap:9px;margin-bottom:10px}}
.slabel{{font-weight:600;font-size:11px;letter-spacing:.06em;
text-transform:uppercase}}
.footbar{{display:flex;align-items:center;justify-content:space-between;
gap:20px;background:#faf7ef;border-top:1px solid #eae4d8;padding:16px 28px;
margin:26px -28px -26px}}
.footbar .actions{{display:flex;align-items:center;gap:12px;flex-shrink:0}}
.twocol{{display:flex;gap:16px;align-items:flex-start;margin:14px 0}}
.twocol>*{{flex:1;min-width:0}}
.colcard{{border:1px solid #eae4d8;border-radius:9px;padding:16px 18px}}
.colcard.amber{{border-color:#eddec4;background:#fcf6e9}}
.collab{{font-weight:600;font-size:13px;letter-spacing:.05em;
text-transform:uppercase;margin-bottom:10px}}
.chiprow{{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0}}
.achip{{display:inline-flex;align-items:center;min-height:44px;
padding:0 16px;border:1px solid #d3ccbb;border-radius:8px;color:#191813;
text-decoration:none;font-size:14px}}
.trow{{display:flex;gap:14px;align-items:flex-start;padding:12px 0;
border-bottom:1px solid #efe9dd}}
/* min-width, not width: DONE/NOW/QUEUED align on a 72px column, but a
   longer real state (test_failed, truncated) grows its chip instead of
   overflowing it into the title beside it. */
.trow .chip{{min-width:72px;text-align:center;flex-shrink:0}}
.trow .ttl{{flex:1}}
/* Per-row actions on their own line. Inline forms sat flush against the
   last word of the criterion, so a row read "…who it is for✓ Fine". */
.tryacts{{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;
margin-top:6px}}
.tryacts form{{display:inline}}
.tryacts details{{width:100%}}
.tstep{{display:block;font-size:14px;color:#575145;font-weight:400}}
.bhead{{display:flex;align-items:flex-start;justify-content:space-between;
gap:24px}}
.bclock{{text-align:right;flex-shrink:0}}
.bclock b{{font:600 22px/1.2 {_MONO};color:#191813}}
.vhead{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
.chatwrap{{display:flex;align-items:stretch;margin:-26px -28px}}
.chatmain{{flex:1;min-width:0;padding:24px 24px 18px;display:flex;
flex-direction:column;gap:18px}}
.chatside{{width:300px;flex-shrink:0;background:#faf7ef;
border-left:1px solid #eae4d8;display:flex;flex-direction:column}}
.sidehead{{padding:20px 20px 12px;border-bottom:1px solid #eae4d8}}
.sidebody{{flex:1;padding:16px 20px;display:flex;flex-direction:column;
gap:14px}}
.sidefoot{{border-top:1px solid #eae4d8;padding:14px 20px 16px;
display:flex;flex-direction:column;gap:8px;text-align:center}}
.slot .slothead{{display:flex;align-items:center;gap:7px;font-size:13px;
font-weight:600;color:#191813}}
.slot.empty .slothead{{color:#575145;font-weight:600}}
.slot .val{{font-size:13px;color:#575145;padding-left:16px}}
.msg-a{{display:flex;gap:12px}}
.msg-a .dot7{{margin-top:9px}}
.msg-a p{{margin:0;font-family:{_SERIF};font-size:17px;line-height:1.6;
color:#191813;max-width:520px}}
.msg-u{{align-self:flex-end;max-width:440px;background:#f1ede2;
border-radius:14px 14px 4px 14px;padding:12px 16px}}
.msg-u p{{margin:0;font-size:16px;color:#191813}}
.composer{{border:1px solid #d3ccbb;border-radius:12px;
padding:12px 14px 10px;box-shadow:0 1px 2px rgba(0,0,0,.03)}}
.composer textarea{{border:0;padding:0;min-height:56px;outline:none;
background:none;border-radius:0}}
.comprow{{display:flex;align-items:center;justify-content:space-between;
gap:12px;margin-top:8px}}
.composerbox{{border:1px solid #d3ccbb;border-radius:10px;overflow:hidden;
margin:12px 0}}
.tabs{{display:flex;border-bottom:1px solid #eae4d8;background:#faf7ef}}
.tab{{min-height:0;border:0;border-radius:0;
border-right:1px solid #eae4d8;background:none;color:#6b6456;
padding:13px 18px;font-weight:400;font-size:14px;box-shadow:none}}
.tab.on{{background:#fff;color:#191813;font-weight:600;
border-bottom:2px solid #07c160}}
.composerbox textarea{{border:0;border-radius:0}}
.composerbox .comprow{{border-top:1px solid #eae4d8;background:#faf7ef;
padding:12px 16px;margin:0}}
.shot{{max-width:100%;border:1px solid #e5dfd0;border-radius:8px;
margin:.4rem 0}}
.estrip{{display:flex;align-items:center;justify-content:space-between;
gap:16px;background:#faf7ef;border-bottom:1px solid #eae4d8;
padding:12px 24px;font-size:14px;margin:-26px -28px 22px}}
.cliblock{{background:#faf7ef;border:1px solid #eae4d8;border-radius:9px;
padding:14px 16px;margin:18px 0}}
.cliblock .lbl{{margin-top:0}}
.cliblock pre{{border:0;background:none;padding:0;margin:0}}
.engpanel,.govpanel{{padding-bottom:22px;margin-bottom:22px;
border-bottom:1px solid #eae4d8}}
.posture{{display:flex;border:1px solid #ded9cd;border-radius:10px;
overflow:hidden;margin:14px 0}}
.pcell{{flex:1;padding:14px 16px;border-left:1px solid #efe9dd;
font-size:14px}}
.pcell:first-child{{border-left:0}}
.pcell .stateline{{margin-bottom:7px}}
.evd{{border:1px solid #eae4d8;border-radius:9px;margin:10px 0;
padding:0 14px}}
.evd summary{{padding:12px 0;font-size:14px;color:#191813}}
.evd .card{{border:0;margin:0;padding:0 0 12px}}
.panelfoot{{background:#faf7ef;border:1px solid #eae4d8;border-radius:9px;
padding:12px 16px;margin:18px 0;font-size:13px;color:#6b6456}}
.mdoc p{{margin:.5em 0 1em}}
"""


_KEY_SIGNALS = (
    "api_key", "api key", "authentication", "unauthorized", "permission denied",
    "credit balance", "insufficient_quota", "billing", " 401", "401 ", " 403", "403 ",
)
_BUSY_SIGNALS = (
    "overloaded", "rate limit", "rate_limit", "temporarily unavailable",
    "timeout", "timed out", "connection", "econnreset", "bad gateway",
    " 429", "429 ", " 502", "502 ", " 503", "503 ", " 529", "529 ",
)


def failure_cause(exc: BaseException) -> str:
    """Which plain-language cause the failure page should show: 'key',
    'busy', or 'unknown'.

    Deliberately returns 'unknown' rather than guessing. A confident wrong
    cause is more expensive than an honest "look at the detail below": the
    page used to assert a missing-or-exhausted API key for every failure, so
    a 529 overload on a valid key read as a billing problem and the real
    signal — retry in a minute — was nowhere on the page.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    # Key problems are checked first: an auth failure is often *reported*
    # through a status code that also appears in the busy list.
    if any(signal in text for signal in _KEY_SIGNALS):
        return "key"
    if any(signal in text for signal in _BUSY_SIGNALS):
        return "busy"
    return "unknown"


#: Which environment variable each provider authenticates with — the same
#: names providers/ already resolves through `secrets.env_or_file`. Listed
#: here so the Studio can ASK for one without ever reading a value: every
#: function below answers "is there a key?" and never "which key".
_PROVIDER_KEY_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "openai": ("OPENAI_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "mock": (),  # the recorded provider bills nobody
}

#: The doors an enterprise already has, per provider: nothing is typed into
#: the Studio for any of them. Rendered as names only — a door is named, an
#: existing configuration is never read back onto the page.
_PROVIDER_DOORS: dict[str, tuple[str, ...]] = {
    "anthropic": (
        "AVS_ANTHROPIC_MODE=bedrock",
        "AVS_ANTHROPIC_MODE=vertex",
        "AVS_ANTHROPIC_MODE=foundry",
        "ANTHROPIC_AUTH_TOKEN (gateway bearer token)",
        "ANTHROPIC_API_KEY_FILE=/run/secrets/… (mounted secret)",
    ),
    "openai": (
        "OPENAI_BASE_URL=https://your-gateway/v1",
        "OPENAI_API_KEY_FILE=/run/secrets/… (mounted secret)",
    ),
    "xai": (
        "XAI_BASE_URL=https://your-gateway/v1",
        "XAI_API_KEY_FILE=/run/secrets/… (mounted secret)",
    ),
    "google": (
        "GEMINI_BASE_URL=https://your-gateway",
        "GEMINI_API_KEY_FILE=/run/secrets/… (mounted secret)",
    ),
}


def provider_key_present(provider: str) -> bool:
    """Can this provider authenticate at all?

    Read-only, and deliberately boolean: the resolved value is never
    returned, rendered or logged anywhere in this module. `mock` bills
    nobody, an unknown provider is not ours to gate, and the gateway modes
    (bedrock / vertex / foundry) authenticate through the cloud's own
    credentials with no key to type here.
    """
    from ai_venture_studio.secrets import SecretError, env_or_file

    if provider == "mock":
        return True
    if provider == "anthropic":
        mode = os.environ.get("AVS_ANTHROPIC_MODE", "direct").strip().lower()
        if mode and mode != "direct":
            return True
    names = _PROVIDER_KEY_VARS.get(provider)
    if not names:
        return True
    try:
        return any(env_or_file(name) for name in names)
    except SecretError:
        # A configured *_FILE mount that cannot be read is a loud failure at
        # the provider, with its own message. Standing in front of it with a
        # paste box would hide the real problem.
        return True


def set_provider_key(provider: str, value: str) -> str | None:
    """Set the provider's key FOR THIS PROCESS ONLY. Returns the variable
    name that was set (never the value), or None when there was nothing
    usable to set.

    Deliberately not persisted: writing it into .mas/ would make the Studio
    a second home for a secret the operator's environment already owns, and
    a key on disk in a workspace is a key in somebody's next `git add`.
    """
    names = _PROVIDER_KEY_VARS.get(provider) or ()
    value = value.strip()
    if not names or not value or any(char.isspace() for char in value):
        return None
    os.environ[names[0]] = value
    return names[0]


def record_failure(root: Path, exc: BaseException) -> None:
    """Append the failure to .mas/studio-failures.jsonl, with the traceback.

    The Studio rendered its exceptions to the browser and nowhere else, so an
    operator who closed the tab had no record that anything had failed. This
    keeps forensics where the rest of them live (.mas/, gitignored) rather
    than introducing a logging stack the codebase does not otherwise use.

    Never raises: a workspace that cannot be written to must still get the
    error page it was about to render.
    """
    import datetime as _dt
    import json
    import traceback

    try:
        path = Path(root) / ".mas" / "studio-failures.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": _dt.datetime.now(_dt.UTC).isoformat(),
            "error": f"{type(exc).__name__}: {exc}"[:500],
            "cause": failure_cause(exc),
            "traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[-4000:],
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return  # forensics are best-effort; the founder's page is not


def _fdr_fingerprint(text: str) -> str:
    """Identifies the FDR revision a form was rendered from.

    The textarea POSTs whatever it was loaded with, so anything that changed
    FDR.md in the meantime — the CLI, a second tab, the conversation, an
    agent editing the file — was silently overwritten on submit. That is a
    lost update, and it cost a founder five answered clarify questions: the
    stale tab put the pre-answer document back and the assessor, seeing no
    answers, asked the same five questions again.
    """
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _md(path: Path) -> str:
    return html.escape(path.read_text(encoding="utf-8")) if path.exists() else ""


_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_HEAD = re.compile(r"(#{1,3})\s+(.*)")
#: Section headings that mean "deliberately out of scope" — the cheapest
#: place to catch a misunderstanding, so they get the amber callout.
_MD_NOT_BUILDING = re.compile(r"not\s+build|non[- ]?goal|不做|非目标", re.I)


def _render_markdown(text: str) -> str:
    """The escaped-first markdown renderer for CONFIRMATION.md / REPORT.md.

    The plan and the report are the most important text in the product and
    both used to render as one grey <pre> blob. The markdown already has
    structure, so render it — but ONLY after escaping everything: this is
    model output headed for a browser. Deliberately tiny: #/##/### become
    h2/h3/h4, "- " runs become lists, **bold** becomes <b>, blank lines
    split paragraphs, and nothing else is interpreted. A section whose
    heading says it will NOT be built is wrapped in the amber callout.
    """
    def _inline(escaped: str) -> str:
        return _MD_BOLD.sub(r"<b>\1</b>", escaped)

    sections: list[tuple[str, list[str]]] = [("", [])]
    para: list[str] = []
    items: list[str] = []

    def _flush() -> None:
        blocks = sections[-1][1]
        if para:
            blocks.append("<p>" + _inline("<br>".join(para)) + "</p>")
            para.clear()
        if items:
            blocks.append(
                "<ul>"
                + "".join(f"<li>{_inline(item)}</li>" for item in items)
                + "</ul>"
            )
            items.clear()

    for line in html.escape(text).splitlines():
        stripped = line.strip()
        heading = _MD_HEAD.match(stripped)
        if heading:
            _flush()
            level = len(heading.group(1)) + 1  # → h2/h3/h4
            sections.append((
                heading.group(2),
                [f"<h{level}>{_inline(heading.group(2))}</h{level}>"],
            ))
            continue
        if stripped.startswith("- "):
            if para:
                _flush()
            items.append(stripped[2:])
            continue
        if not stripped:
            _flush()
            continue
        if items:
            _flush()
        para.append(stripped)
    _flush()

    out = []
    for head, blocks in sections:
        chunk = "".join(blocks)
        if head and _MD_NOT_BUILDING.search(head):
            chunk = f"<div class=callout>{chunk}</div>"
        out.append(chunk)
    return f"<div class=mdoc>{''.join(out)}</div>"


def _elapsed_hms(root: Path) -> str:
    """Wall-clock since the build worker was spawned, from the pid marker's
    own mtime — a fact already on disk, read-only. Empty when unknowable:
    an omitted clock is honest, an invented one is not."""
    import time as _time

    try:
        seconds = int(_time.time() - (root / ".mas" / "build.pid").stat().st_mtime)
    except OSError:
        return ""
    if seconds < 0:
        return ""
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


# Inline SVG favicon: kills the /favicon.ico 404 in every console (the
# first thing a browser-driven evaluation sees) without adding a route or
# an asset file.
_FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
    "%3Ctext y='13' font-size='13'%3E%F0%9F%8F%97%3C/text%3E%3C/svg%3E"
)


def _page(title: str, body: str) -> HTMLResponse:
    """The document wrapper. `body` is the full page including chrome —
    the header, rail and card are assembled per request in `_render`,
    which knows the workspace and the mode; this knows neither."""
    return HTMLResponse(
        f"<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        f"<link rel='icon' href=\"{_FAVICON}\">"
        f"<style>{_STYLE}</style><body>{body}"
    )


def _failed_tasks(root: Path) -> list[str]:
    path = root / "product" / "outcomes.yaml"
    if not path.exists():
        return []
    outcomes = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [o["task_id"] for o in outcomes if o.get("status") != "built"]


def _pending_feature(root: Path) -> Path | None:
    features_dir = root / "product" / "features"
    if not features_dir.is_dir():
        return None
    for d in sorted(features_dir.iterdir(), reverse=True):
        if (d / "CONFIRMATION.md").exists() and not (d / "REPORT.md").exists():
            return d
    return None


def _build_running(root: Path) -> bool:
    marker = root / ".mas" / "build.pid"
    if not marker.exists():
        return False
    try:
        pid = int(marker.read_text().strip())
    except ValueError:
        return False
    from ai_venture_studio.procs import pid_alive

    return pid_alive(pid)


#: How long a foreground failure is still worth showing. Long enough for the
#: working page's own reload to catch it, short enough that it cannot
#: ambush someone who comes back after a successful build.
_FAILURE_TTL_S = 120.0

# Same shape the server's review routes enforce — a review id is a path
# segment, so anything else is a traversal attempt, not a typo.
_REVIEW_ID = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")

#: EARS, split into its optional condition and its response. The grammar is
#: `[When|While|If|Where <condition>, [then] ][the ]<subject> shall
#: <response>` — see upstream/ears.py, which is what enforces it.
_EARS_SPLIT = re.compile(
    r"\A(?:(When|While|If|Where)\s+(.+?),\s*(?:then\s+)?)?"
    r"(?:the\s+)?\S.*?\s+shall\s+(.+)\Z",
    re.IGNORECASE | re.DOTALL,
)


def _plain_criterion(text: str) -> str:
    """An EARS criterion as a sentence a founder reads, not one they parse.

    "The system shall list items newest first." is the right thing to STORE
    — it is testable, and the spec is a contract. It is the wrong thing to
    show on the one page written for someone non-technical, where a column
    of "The system shall" reads as machine output about someone else's
    product. Only the preamble goes; the stored text is never touched, and
    anything that is not EARS is shown exactly as written rather than
    guessed at.
    """
    match = _EARS_SPLIT.match(" ".join(str(text).split()))
    if not match:
        return str(text).strip()
    condition, clause, response = match.groups()
    response = response.strip()
    response = response[:1].upper() + response[1:]
    return f"{condition} {clause}: {response}" if condition else response


#: The four founder-flow stages, in order, keyed to their i18n labels.
_STAGES = (
    ("describe", "rail_describe"), ("plan", "rail_plan"),
    ("build", "rail_build"), ("product", "rail_product"),
)


def _task_chip(task: dict, t_) -> tuple[str, str, str]:
    """(chip label, chip css class, step narration) for one task row —
    the same mapping the building page's poll JS applies client-side."""
    state = task["state"]
    if state == "built":
        return t_("chip_done"), "g", ""
    if state == "pending" and task.get("step"):
        return t_("chip_now"), "a", task["step"]
    if state == "pending":
        return t_("chip_queued"), "q", ""
    # A failed state keeps its verbatim name, amber — never a euphemism.
    return state, "a", ""


def _task_rows_html(tasks: list[dict], t_) -> str:
    rows = []
    for task in tasks:
        chip, css, step = _task_chip(task, t_)
        step_html = (
            f"<span class=tstep>{html.escape(step)}</span>" if step else ""
        )
        rows.append(
            f"<div class=trow id='task-{html.escape(task['id'])}'>"
            f"<span class='chip {css}'>{html.escape(chip)}</span>"
            f"<span class=ttl>{html.escape(task['title'])}{step_html}</span>"
            "</div>"
        )
    return "".join(rows)


def _task_states(root: Path) -> list[dict]:
    """Per-task build state from the workspace files the CLI writes (the
    Studio is a veneer, never a second source of truth): a spec gains
    `built: true` as the run progresses — its `(task:<id>)` request marker
    links it back to the plan — and outcomes.yaml records failures when a
    run finishes."""
    plan_path = root / "product" / "plan.yaml"
    if not plan_path.exists():
        return []
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
    from ai_venture_studio.upstream.plan import built_task_ids

    built_ids = built_task_ids(root)  # one definition of "built", shared
    failed: dict[str, str] = {}
    outcomes_path = root / "product" / "outcomes.yaml"
    if outcomes_path.exists():
        for o in yaml.safe_load(outcomes_path.read_text(encoding="utf-8")) or []:
            if o.get("status") != "built" and o.get("task_id"):
                failed[o["task_id"]] = str(o.get("status", "failed"))
    # `pending` covers everything from "not started" to "on its third build
    # attempt", which is most of a run's wall-clock. The step journal is the
    # difference between the two — still a read of a file the CLI writes.
    from ai_venture_studio.upstream import progress as progress_journal

    steps = progress_journal.latest_by_task(root)
    # A step belongs to the ONE task the run is on. Handing every started
    # task its last narration made a sequential build claim three modules
    # were in flight at once — "NOW" on each, because both renderers read
    # "pending + a step" as in-flight. Seen in a real run at 1:25 elapsed:
    # two DONE, one BUILD_FAILED, and three NOWs of which at most one could
    # be true. A step that is not happening now is stale narration, which
    # this file already refuses to print on a built task; the same rule just
    # has to hold for a task the run has moved past. Keeping the payload's
    # shape (rather than adding an is_current flag) keeps the server and the
    # poll JS honest by construction, since neither can drift from the other.
    live = (progress_journal.current(root) or {}).get("task_id")
    return [
        {
            "id": t["id"],
            "title": t.get("title", t["id"]),
            "state": "built" if t["id"] in built_ids
            else failed.get(t["id"], "pending"),
            "step": str(steps.get(t["id"], {}).get("detail", ""))
            if t["id"] == live else "",
        }
        for t in plan.get("tasks", [])
    ]


def _progress(root: Path) -> dict:
    plan_path = root / "product" / "plan.yaml"
    total = built = 0
    if plan_path.exists():
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
        total = len(plan.get("tasks", []))
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=root, capture_output=True, timeout=60, text=True
    ).stdout
    built = log.count("feat(")
    from ai_venture_studio.upstream import progress as _progress_journal

    current = _progress_journal.current(root)
    return {
        "total": total,
        "built": built,
        "running": _build_running(root),
        "tasks": _task_states(root),
        # What it is doing RIGHT NOW. Before any task exists this is the only
        # honest thing the Building page can show, and it used to show
        # nothing but a static "planning…" for the several minutes the
        # assess/brief/roster/plan stretch takes.
        "step": (current or {}).get("detail", ""),
    }




def create_studio_app(
    repo_dir: str | Path, *, spawn=None, provider: str = "anthropic",
    lang: str = DEFAULT_LANGUAGE, mode: str | None = None,
    entry: str = "chat",
) -> FastAPI:
    """`entry` picks which door the describe-state opens with.

    Default 'chat': answering one question at a time is what a
    non-technical founder can actually do, and the 4000-character textarea
    was where they stopped. The form is never removed — it stays one click
    away at /?form=1, and anyone who already has an FDR is offered it
    rather than dropped into a conversation about a document they wrote.
    """
    if entry not in ("chat", "form"):
        raise ValueError(f"unknown entry {entry!r} — expected chat or form")
    root = Path(repo_dir).resolve()
    lang = normalize(lang)
    # --mode / edition only set the DEFAULT; the user switches per request
    # (adaptable, never adaptive — the UI must not flip modes on its own).
    default_mode = resolve_mode(root, mode)

    def _(key: str) -> str:
        """This page's string in the chosen language (studio_i18n)."""
        return t(lang, key)

    def _req_mode(request: Request) -> str:
        """Query beats cookie beats the startup default. An unknown ?mode=
        is a loud 400, same policy as an unknown --mode."""
        query = request.query_params.get("mode")
        if query:
            try:
                return resolve_mode(root, query)
            except StudioModeError as exc:
                raise HTTPException(400, str(exc)) from exc
        cookie = request.cookies.get("studio_mode")
        if cookie in MODES:
            return cookie
        return default_mode

    # The three handlers below run LLM calls for minutes. `/build` and
    # `/retry` already refused to start a second worker; these did not, and a
    # button that looks dead for six minutes is a button people press twice —
    # two autopilots on one workspace race on git and on the same files. The
    # flag is in-process because the Studio is one localhost process.
    thinking: dict[str, str] = {}

    def _thinking_page(request: Request, what: str) -> HTMLResponse:
        return _render(
            request, _("title_working"),
            f"<div class=card><b class=warn>{_('working_lead')}</b>"
            f"<p>{html.escape(what)}</p>"
            f"<p class=muted>{_('working_hint')}</p></div>"
            # A POLL, not a bounce. This used to jump to / after 15 seconds
            # while the step was still running, and / had no idea anything
            # was in flight — so it rendered the page the founder had just
            # left and the whole thing read as "my click did nothing". Home
            # now returns this same page while work is in flight, so the
            # reload is a refresh that ends by itself when the step lands.
            "<script>setTimeout(()=>location.href='/',4000)</script>",
        )

    #: The last foreground failure, kept until a page shows it. The failure
    #: page is returned to the POST that raised — but the working page has
    #: usually navigated away from that request by then, so without this the
    #: founder watches a spinner and then lands on an ordinary page with no
    #: sign that anything went wrong. Which is precisely what happened.
    last_failure: dict[str, object] = {}

    def _stash_failure(exc: Exception) -> None:
        import time as _time

        last_failure.clear()
        last_failure["exc"] = exc
        last_failure["at"] = _time.monotonic()

    def _take_fresh_failure() -> Exception | None:
        """The pending failure, if it is still about what the founder is
        looking at.

        Without an expiry this was a landmine: a failure nobody happened to
        load the page for sat in memory, and the next visitor got "That step
        did not finish" ahead of the product a later run had successfully
        built. An hour-old error is not news, it is a scare.
        """
        import time as _time

        if not last_failure:
            return None
        age = _time.monotonic() - float(last_failure.get("at", 0))
        exc = last_failure.pop("exc", None)
        last_failure.clear()
        if age > _FAILURE_TTL_S:
            return None
        return exc if isinstance(exc, Exception) else None

    def _failure_page(
        request: Request, exc: Exception, *, record: bool = True,
        retry_action: str | None = None,
    ) -> HTMLResponse:
        """A founder should never meet a stack trace, and should never be
        told nothing either: reassurance outranks the error — plain
        language first, the real error one click away, and the workspace
        left where they can retry.

        The cause line is derived from the exception, never assumed. It used
        to be one hardcoded sentence naming a missing API key, which is how a
        transient provider overload — on a valid, funded key — sent someone
        looking for a key problem that did not exist.

        `retry_action` puts the retry ON the page that names the failure —
        but only where a bodyless re-POST is safe; everything else keeps
        the plain link home.
        """
        if record:
            record_failure(root, exc)
            _stash_failure(exc)
        if retry_action:
            retry = (
                f"<form method=post action={retry_action}>"
                f"<button class=primary>{_('btn_retry_step')}</button></form>"
            )
        else:
            retry = f"<p><a href='/'>{_('link_back')}</a></p>"
        # A refused key is the one cause whose fix is a single field. Put it
        # HERE, where the problem is named, instead of sending someone back
        # through the flow to find a settings page that does not exist.
        cause = failure_cause(exc)
        if cause == "key" and _can_paste_key():
            retry += _key_form(heading=_("key_fail_head"))
        return _render(
            request, _("title_failed"),
            "<div class=stateline><span class='sdot red'></span>"
            f"<span class='slabel bad'>{_('fail_chip')}</span></div>"
            f"<h1>{_('failed_lead')}</h1>"
            f"<p>{_('failed_safe')}</p>"
            f"<p>{_('failed_cause_' + cause)}</p>"
            f"{retry}"
            f"<details><summary class=muted>{_('failed_detail')}</summary>"
            f"<pre>{html.escape(f'{type(exc).__name__}: {exc}')}</pre>"
            "</details>",
            h1="",
        )

    def _header(req_mode: str) -> str:
        """The top bar every page shares: the AVS mark, the workspace name,
        the profile chip, and the mode switcher — the answer to "where am
        I, as whom" before any content."""
        try:
            profile = _profile(root)
        except Exception:  # noqa: BLE001 — chrome must never take a page down
            profile = ""
        chip = f"<span class=pchip>{html.escape(profile)}</span>" if profile else ""
        return (
            "<header class=hdr>"
            "<span class=brand><span class=mark></span>"
            "<span class=marklabel>AVS</span></span>"
            "<span class=hdrdiv></span>"
            f"<span class=wsname>{html.escape(root.name)}</span>{chip}"
            + mode_strip(req_mode, _)
            + "</header>"
        )

    def _stage_rail(current: str) -> str:
        """Describe → Plan → Build → Your product: four spans and three
        hairlines that answer "did my click do anything?" on every
        founder-flow page. ✓ on what is behind you, a green dot on where
        you are."""
        index = [key for key, _label in _STAGES].index(current)
        parts = []
        for position, (_key, label_key) in enumerate(_STAGES):
            label = _(label_key)
            if position:
                on = " on" if position == index else ""
                parts.append(f"<span class='rline{on}'></span>")
            if position < index:
                parts.append(f"<span class=rdone>✓ {label}</span>")
            elif position == index:
                parts.append(
                    f"<span class=rcur><span class=dot7></span>{label}</span>"
                )
            else:
                parts.append(f"<span class=rtodo>{label}</span>")
        return f"<div class=rail>{''.join(parts)}</div>"

    def _render(
        request: Request, title: str, body: str, *,
        rail: str | None = None, h1: str | None = None,
    ) -> HTMLResponse:
        """Every page: shared chrome (header, optional stage rail), then
        the mode's read-only panel, then the founder body — the panel goes
        FIRST because the person who switched modes switched for it, and
        the founder content stays one in-page anchor away. Founder mode
        adds no panel, so the founder flow stays the plain flow plus the
        switcher. Panels are built per request — they reflect the workspace
        files as of this page load, never a cached copy.

        `rail` names the current founder-flow stage; failure pages and
        non-flow pages pass none. `h1` overrides the page heading ("" for
        states that compose their own)."""
        req_mode = _req_mode(request)
        chrome = [_header(req_mode)]
        if req_mode != "founder":
            chrome.append(
                f"<div class=modenote><span>{_('mode_addonly_note')}</span>"
                f"<a href='/?mode=founder'>{_('mode_back_founder')}</a></div>"
            )
        if rail:
            chrome.append(_stage_rail(rail))
        panel = ""
        if req_mode == "engineer":
            panel = engineer_panel(
                root, _, _task_states(root),
                spend_detail=_engineer_spend_detail(),
            )
        elif req_mode == "enterprise":
            panel = enterprise_panel(root, _)
        heading = f"<h1>{html.escape(title)}</h1>" if h1 is None else (
            f"<h1>{h1}</h1>" if h1 else ""
        )
        document = (
            "<main class=shell>" + "".join(chrome)
            + f"<div class=pagebody>{panel}"
            f"<div id=founder>{heading}{body}</div></div></main>"
        )
        response = _page(title, document)
        if request.query_params.get("mode"):
            # An explicit switch persists across the POST→redirect cycle.
            response.set_cookie("studio_mode", req_mode)
        return response

    app = FastAPI(
        title="avs studio", docs_url=None, redoc_url=None, openapi_url=None
    )

    # Shared-machine / corp deployment: AVS_STUDIO_TOKEN (env or *_FILE
    # mount) gates every request. Absent token keeps the original
    # localhost-only posture; the CLI refuses to bind non-loopback without
    # one. This is deliberately a shared secret, not SSO — the documented
    # upgrade path is OIDC in front (reverse proxy), not a home-grown login.
    from ai_venture_studio.secrets import env_or_file

    studio_token = env_or_file("AVS_STUDIO_TOKEN")

    @app.middleware("http")
    async def token_gate(request: Request, call_next):
        if not studio_token:
            return await call_next(request)
        import hmac as _hmac

        auth = request.headers.get("Authorization", "")
        supplied = (
            request.cookies.get("studio_token")
            or request.query_params.get("token")
            or (auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else "")
        )
        if not (supplied and _hmac.compare_digest(supplied, studio_token)):
            from fastapi.responses import PlainTextResponse

            return PlainTextResponse(
                "This Studio requires its access token. Open "
                "/?token=<AVS_STUDIO_TOKEN> once — a cookie keeps you "
                "signed in after that.",
                status_code=401,
            )
        response = await call_next(request)
        if request.query_params.get("token"):
            response.set_cookie(
                "studio_token", studio_token, httponly=True, samesite="lax"
            )
        return response

    @app.middleware("http")
    async def same_origin_guard(request: Request, call_next):
        """Localhost is not a security boundary against the browser: a
        malicious page can form-POST to 127.0.0.1 (sweep finding). POSTs
        must come from the Studio itself — compared against the request's
        OWN host, so a Studio served on a corp hostname keeps working
        (hardcoding localhost here broke every POST behind a real name)."""
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if origin:
                from urllib.parse import urlsplit

                origin_host = urlsplit(origin).hostname or ""
                own_host = request.url.hostname or ""
                if origin_host not in (own_host, "127.0.0.1", "localhost"):
                    from fastapi.responses import PlainTextResponse

                    return PlainTextResponse(
                        "cross-origin POST rejected", status_code=403
                    )
        return await call_next(request)

    def _spawn_build() -> int:
        if spawn is not None:
            return spawn(root)
        # The worker inherits the Studio's provider — without this, a Studio
        # started with --provider mock spawned a build that wanted a real
        # key and died silently. Its output goes to .mas/build.log, not
        # DEVNULL: a worker that dies before writing the report must leave
        # forensics behind.
        (root / ".mas").mkdir(exist_ok=True)
        log = (root / ".mas" / "build.log").open("ab")
        proc = subprocess.Popen(  # noqa: S603 — fixed argv
            [sys.executable, "-m", "ai_venture_studio.cli", "create", str(root),
             "--profile", _profile(root), "--provider", provider, "--yes"],
            cwd=root, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        (root / ".mas" / "build.pid").write_text(str(proc.pid), encoding="utf-8")
        return proc.pid

    def _profile(workspace: Path) -> str:
        data = yaml.safe_load(
            (workspace / ".mas" / "project.yaml").read_text(encoding="utf-8")
        )
        return data["profile"]

    def _spend_lines() -> str:
        """What the workspace has spent, where money is decided — a
        statement, never a gate.

        Rendered inside the footer bar of the confirm page (before the
        first dollar) and the report page. The founder signal asked to SEE
        the number ("how much will a typical month of builds cost me?"),
        and a figure you must go looking for does not answer it.
        Deliberately no cap and no ceiling form: every call is billed to
        the founder's own key or subscription, so spending limits belong to
        the provider account that does the billing (ADR-032) — a
        framework-side dollar cap would duplicate the provider's job and
        mislead subscription users whose tokens do not map to marginal
        dollars. The per-model table lives on the engineer panel.
        """
        from ai_venture_studio import spend

        spend.flush(root)
        report = spend.month_report(root)
        prefix = "≥" if report.is_floor else ""
        if report.calls:
            summary = spend.summarize_workspace(root)
            line = html.escape(
                spend.render_plain(summary, what=_("cost_what"))
            ) + f" ({report.month}: {prefix}${report.spent_usd:.2f})"
        else:
            line = _("cost_no_spend")
        parts = [f"<div><b>{_('h_cost')}</b> — {line}</div>"]
        if report.is_floor:
            parts.append(f"<div class=muted>{_('cost_floor_note')}</div>")
        parts.append(
            f"<div class=muted>{_('cost_own_key')} "
            f"{_('cost_provider_limits')}</div>"
        )
        return f"<div>{''.join(parts)}</div>"

    def _spend_month_fragment() -> str:
        """Just the month's dollar figure ("≥$3.87"), for muted meta lines —
        empty when there is no figure to give, because an omitted figure is
        honest and a made-up zero is not.

        "No figure" covers two cases, and the second one used to slip
        through: no calls at all, AND calls whose models are all unpriced.
        A real live build spent 24 minutes showing "$0.00 so far" on the
        building page — a founder reads that as "this one is free", when it
        means "this workspace has no price list". The full spend card says
        so in words; a four-character meta line cannot, so it says nothing.
        """
        from ai_venture_studio import spend

        report = spend.month_report(root)
        if not report.calls:
            return ""
        if report.is_floor and report.spent_usd == 0:
            return ""  # priced nothing: a zero here would be a claim
        prefix = "≥" if report.is_floor else ""
        return f"{prefix}${report.spent_usd:.2f}"

    def _engineer_spend_detail() -> str:
        """The per-model breakdown `avs cost` prints, plus the CLI spelling —
        engineer mode's contract is that every card names its CLI twin."""
        from ai_venture_studio import spend
        from ai_venture_studio.observability import load_cost_model

        entries = spend.read_entries(root, month=spend.current_month())
        if not entries:
            return ""
        records = spend.priced(entries, load_cost_model(root / ".mas"))
        by_model: dict[str, tuple[int, int, float, int]] = {}
        for entry, record in zip(entries, records):
            calls, tokens, usd, unpriced = by_model.get(entry.model, (0, 0, 0.0, 0))
            by_model[entry.model] = (
                calls + 1,
                tokens + entry.input_tokens + entry.output_tokens,
                usd + (record.cost_usd or 0.0),
                unpriced + (1 if record.cost_usd is None else 0),
            )
        rows = "".join(
            f"<tr><td><code>{html.escape(model)}</code></td><td>{calls}</td>"
            f"<td>{tokens:,}</td><td>{usd:.2f}{'+' if unpriced else ''}</td></tr>"
            for model, (calls, tokens, usd, unpriced) in sorted(by_model.items())
        )
        return (
            f"<details><summary class=muted>{_('eng_cost_detail')}</summary>"
            "<table><tr><th>model</th><th>calls</th><th>tokens</th><th>usd</th></tr>"
            f"{rows}</table>"
            "<pre>avs cost\navs prices --import</pre></details>"
        )

    # ── The key gate ─────────────────────────────────────────────────────
    # A founder who has never held an API key used to meet that fact as a
    # traceback on their first send. It is asked for here instead, in
    # whose-account-pays language, once, before the describe state.

    #: Set once a key was pasted into THIS process. Only ever a boolean —
    #: the value lives in os.environ and is never copied here.
    key_pasted: dict[str, bool] = {}

    def _needs_key() -> bool:
        return not provider_key_present(provider)

    def _can_paste_key() -> bool:
        """Whether pasting a key here could help at all.

        False for the mock (nothing to pay) and for a provider we do not know
        the variable of — a paste box that sets nothing is a dead button.

        False as well whenever AVS_STUDIO_TOKEN gates this Studio, which is
        the shared-machine deployment: the key would be set on ONE process
        that everyone holding the token then spends through, while the form
        says "this process only" — wording a reader hears as "my session
        only". One person's card silently paying for everyone else's builds
        is not a thing to let a founder do by accident, so a shared Studio
        takes its key from the environment or a mounted file (the doors
        panel) and the box is not offered at all.
        """
        return bool(_PROVIDER_KEY_VARS.get(provider)) and not studio_token

    def _key_form(*, heading: str) -> str:
        """The paste-a-key form. `type=password` so a shoulder or a
        screenshot does not carry the key, `autocomplete=off` so the browser
        does not keep it, and no value attribute EVER — the field renders
        empty even when a key is set, because the page must not be able to
        show one back."""
        name = (_PROVIDER_KEY_VARS.get(provider) or ("",))[0]
        return (
            f"<div class=card><b>{heading}</b>"
            "<form method=post action=/key>"
            "<p><input type=password name=key required autocomplete=off "
            "spellcheck=false style='width:100%;min-height:44px;padding:11px "
            "14px;border:1px solid #d3ccbb;border-radius:9px' "
            f"placeholder='{html.escape(name)}'></p>"
            f"<p><button class=primary>{_('btn_key_save')}</button></p></form>"
            f"<p class=muted>{_('key_paste_hint_fmt').format(name=html.escape(name))}</p>"
            f"<p class=muted>{_('key_process_only')}</p></div>"
        )

    def _key_cost_line() -> str:
        """What it typically costs — the workspace's own ledger or no figure
        at all. An invented number on the page that asks for a payment
        method is the worst place in the product to guess."""
        spent = _spend_month_fragment()
        line = (
            _("key_cost_spent_fmt").format(amount=html.escape(spent))
            if spent else _("key_cost_no_figure")
        )
        return (
            f"<div class=panelfoot><b>{_('key_cost_head')}</b> — {line} "
            f"{_('cost_provider_limits')}</div>"
        )

    def _key_gate_page(request: Request) -> HTMLResponse:
        doors = "".join(
            f"<div><code>{html.escape(door)}</code></div>"
            for door in _PROVIDER_DOORS.get(provider, ())
        )
        return _render(
            request, _("title_key_gate"),
            f"<p>{_('key_lead')}</p>"
            + (
                _key_form(heading=_("key_paste_head")) if _can_paste_key()
                # A shared Studio says why the box is missing. Silence would
                # read as a broken page on the one deployment where the
                # answer ("set it in the environment") is the whole point.
                else f"<div class=card><b>{_('key_shared_head')}</b>"
                f"<p>{_('key_shared_note')}</p></div>" if studio_token else ""
            )
            + (
                f"<div class=card><b>{_('key_doors_head')}</b>"
                f"<p class=muted>{_('key_doors_note')}</p>{doors}</div>"
                if doors else ""
            )
            # Nothing to type at all: the vendored demo review is a real
            # recorded run of this repo's own code, and it renders through
            # the same timeline the workspace's own reviews use.
            + f"<div class=card><b>{_('key_demo_head')}</b>"
            f"<p class=muted>{_('key_demo_note')}</p>"
            f"<p><a class=achip href='/demo'>{_('link_demo')}</a></p></div>"
            + _key_cost_line(),
            rail="describe",
        )

    def _key_strip() -> str:
        """One line, on the page that was already coming. State B adds no
        click and no page: a key that was already there says nothing at
        all, and a key pasted here says so once, in place."""
        if not key_pasted:
            return ""
        return f"<p class='muted ok'>{_('key_strip_set')}</p>"

    @app.post("/key")
    async def set_key(request: Request):
        """Sets the provider key for THIS PROCESS ONLY (os.environ) — never
        written to disk, never echoed back, never logged.

        Refused outright on a token-gated (shared) Studio: see
        `_can_paste_key`. The form is not rendered there, so reaching this is
        either a stale tab or someone posting by hand — and in both cases
        accepting would charge one person for everybody.
        """
        if studio_token:
            return _render(
                request, _("title_key_gate"),
                f"<div class=card><b class=warn>{_('key_shared_head')}</b>"
                f"<p>{_('key_shared_note')}</p></div>"
                f"<p><a href='/'>{_('link_back')}</a></p>",
            )
        form = await request.form()
        name = set_provider_key(provider, str(form.get("key", "")))
        if name is None:
            # Nothing usable arrived (empty, whitespace, or a provider with
            # no key to set). Say so rather than redirecting into the same
            # page with no explanation.
            return _render(
                request, _("title_key_gate"),
                f"<div class=card><b class=warn>{_('key_refused')}</b></div>"
                + (_key_form(heading=_("key_paste_head")) if _can_paste_key() else "")
                + f"<p><a href='/'>{_('link_back')}</a></p>",
            )
        key_pasted["set"] = True
        return RedirectResponse("/", status_code=303)

    def _spec_cards() -> str:
        """One card per feature the product actually HAS, each with a way
        to change it.

        Rendered from `built_specs` — the correction router's own source of
        truth — so a card can never offer to change something the router
        would then refuse to route to. The alternative (listing
        product/features/*) showed only what had been added AFTER the
        build, as bare directory slugs, which is both the smaller half and
        the less recognisable one.
        """
        from ai_venture_studio.upstream.correction import built_specs

        try:
            specs = built_specs(root)
        except Exception:  # noqa: BLE001 — a malformed spec must not 500 the home page
            specs = []
        cards = ""
        for spec in specs:
            slug = str(spec["slug"])
            does = "".join(
                f"<div class=tstep>{html.escape(_plain_criterion(str(c)))}</div>"
                for c in (spec.get("criteria") or [])
            )
            cards += (
                "<div class=card>"
                f"<b>{html.escape(str(spec.get('title') or slug))}</b>"
                f"<span class='chip g'>{_('state_done')}</span>"
                + (f"<div class=lbl>{_('spec_card_does')}</div>{does}" if does else "")
                # <details>, so the box is in the HTML for the wireup gate
                # and for anyone without JavaScript, and closed by default
                # so twelve features do not become twelve open text areas.
                + f"<details><summary class=linkish>{_('btn_change_this')}"
                "</summary>"
                "<form method=post action=/correct>"
                f"<input type=hidden name=spec_slug value='{html.escape(slug)}'>"
                "<textarea name=complaint "
                f"placeholder='{_('change_this_placeholder')}'></textarea>"
                f"<p><button class=secondary>{_('btn_change_this_go')}</button></p>"
                "</form></details>"
                "</div>"
            )
        return cards

    def _change_list() -> str:
        """The log, as the undo surface — with the truth about what going
        back costs.

        The history model is a straight line of checkpoint tags: going back
        means resetting to one, so everything after it goes too. Reverting
        one middle change on its own would be `git revert` plus a conflict
        resolution nobody should hand a founder. So each row offers "go back
        to just before THIS change" and states, on the button's own line,
        how many later changes that also undoes. A rescue branch is made
        first, every time.
        """
        from ai_venture_studio.upstream.autopilot import checkpoint_log

        try:
            entries = checkpoint_log(root)
        except Exception:  # noqa: BLE001 — a workspace without git still renders
            entries = []
        if not entries:
            return ""
        rows = ""
        for entry in entries:
            if entry["previous"]:
                later = entry["later_changes"]
                note = (
                    _("undo_to_note_fmt") if later
                    else _("undo_to_note_last_fmt")
                ).format(later=later, commits=entry["undoes_commits"])
                action = (
                    "<form method=post action=/undo/to>"
                    f"<input type=hidden name=tag value='{html.escape(entry['tag'])}'>"
                    f"<button class=linkish>{_('btn_undo_to')}</button></form>"
                    f"<span class=tstep>{note}</span>"
                )
            else:
                action = f"<span class=tstep>{_('undo_to_first')}</span>"
            rows += (
                "<div class=trow><span class='chip q'>"
                f"{html.escape(entry['tag'].removeprefix('ap-checkpoint-'))}"
                "</span><span class=ttl>"
                f"<b>{html.escape(entry['subject'] or entry['tag'])}</b>"
                f"<span class=tstep>{html.escape(entry['when'])}</span>"
                f"{action}</span></div>"
            )
        return (
            f"<h2>{_('h_changes')}</h2>"
            f"<p class=muted>{_('changes_linear_note')}</p>{rows}"
        )

    @app.post("/undo/to")
    async def undo_to(request: Request):
        """Go back to just before one recorded change — and, with it,
        everything that came after. The page said so before this ran."""
        from ai_venture_studio.upstream.autopilot import checkpoints, undo_to_before

        form = await request.form()
        tag = str(form.get("tag", ""))
        # A tag reaches an argv, so the same segment rule as every other
        # identifier arriving from a form, then existence.
        if not _REVIEW_ID.match(tag):
            return RedirectResponse("/", status_code=303)
        if tag not in checkpoints(root):
            return _no_such_page(request, "title_no_checkpoint", tag)
        undo_to_before(root, tag)
        return RedirectResponse("/", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        # A foreground step (assess / feature / correct) is mid-flight in
        # another request. Say so, rather than rendering the page they just
        # left as though nothing had happened — these steps run for minutes
        # and the thinking page reloads here on a timer.
        if thinking:
            return _thinking_page(request, next(iter(thinking.values())))
        stale = _take_fresh_failure()
        if stale is not None:
            # Shown once, to whoever gets here first — usually the working
            # page's own reload, which is the only thing still watching.
            return _failure_page(request, stale, record=False)
        fdr = root / "FDR.md"
        report = root / "product" / "BUILD-REPORT.md"
        confirmation = root / "product" / "CONFIRMATION.md"
        questions = root / "FDR-QUESTIONS.md"
        progress = _progress(root)

        if progress["running"]:
            tasks = progress["tasks"]
            done = sum(1 for t in tasks if t["state"] == "built") or progress["built"]
            total = progress["total"] or "?"
            checklist = (
                f"<div id=tasks>{_task_rows_html(tasks, _)}</div>"
                if tasks
                else f"<p class=muted id=tasks>{_('planning')}</p>"
            )
            step_line = (
                f"<p id=step><b>{html.escape(progress['step'])}</b></p>"
                if progress.get("step")
                else "<p id=step></p>"
            )
            headline = _("building_headline").format(
                done=f"<b id=done>{done}</b>", total=f"<b id=total>{total}</b>"
            )
            # Elapsed and spend: both already on disk (the pid marker's
            # mtime, the spend ledger). Known, honest texture for the wait —
            # still no percentage and no ETA, which stays deliberate.
            clock = _elapsed_hms(root)
            spent = _spend_month_fragment()
            meta_bits = [_("building_elapsed")] if clock else []
            if spent:
                meta_bits.append(
                    # The whole figure, "≥" included: stripping the floor
                    # marker turned a lower bound into a total on the one
                    # page that watches money accrue.
                    _("building_spent_fmt").format(amount=spent)
                )
            clock_html = ""
            if clock or spent:
                big = f"<b>{html.escape(clock)}</b>" if clock else ""
                meta = " · ".join(meta_bits)
                clock_html = (
                    f"<div class=bclock>{big}"
                    f"<div class=muted>{html.escape(meta)}</div></div>"
                )
            # Live per-task progress (signal s3: "it looks frozen while it
            # works") — poll /status, update in place, one full reload when
            # the worker exits so the report page takes over. The chip
            # mapping mirrors _task_chip exactly.
            chips_js = (
                "const CHIP={built:['%s','g'],now:['%s','a'],queued:['%s','q']};"
                % (_("chip_done"), _("chip_now"), _("chip_queued"))
            )
            return _render(
                request, _("title_building"),
                f"<div class=bhead><div><h1>{headline}</h1>"
                f"<p class=muted>{_('building_note')}</p></div>"
                f"{clock_html}</div>"
                f"{step_line}{checklist}"
                f"<div class=footbar><span class=muted>{_('building_honesty')}"
                "</span></div>"
                "<script>\n"
                f"{chips_js}\n"
                "const esc=x=>String(x).replace(/[<&]/g,'');\n"
                "async function poll(){try{\n"
                "  const s=await (await fetch('/status')).json();\n"
                "  if(!s.running){location.reload();return}\n"
                "  const built=s.tasks.filter(t=>t.state==='built').length;\n"
                "  document.getElementById('done').textContent=built||s.built;\n"
                "  if(s.total)document.getElementById('total').textContent=s.total;\n"
                "  const st=document.getElementById('step');\n"
                "  if(st)st.innerHTML=s.step?'<b>'+esc(s.step)+'</b>':'';\n"
                "  for(const t of s.tasks){\n"
                "    const li=document.getElementById('task-'+t.id);\n"
                "    if(!li)continue;\n"
                "    let c,cls,step='';\n"
                "    if(t.state==='built'){c=CHIP.built[0];cls='g'}\n"
                "    else if(t.state==='pending'&&t.step){c=CHIP.now[0];cls='a';step=t.step}\n"
                "    else if(t.state==='pending'){c=CHIP.queued[0];cls='q'}\n"
                "    else{c=t.state;cls='a'}\n"
                "    li.innerHTML='<span class=\"chip '+cls+'\">'+esc(c)+'</span>'\n"
                "      +'<span class=ttl>'+esc(t.title)\n"
                "      +(step?'<span class=tstep>'+esc(step)+'</span>':'')+'</span>';\n"
                "  }\n"
                "}catch(e){}setTimeout(poll,5000)}\n"
                "poll();\n"
                "setTimeout(()=>location.reload(),120000)\n"
                "</script>",
                rail="build", h1="",
            )
        interrupted = (
            (root / ".mas" / "build.pid").exists()
            and not report.exists()
            and progress["tasks"]
        )
        if interrupted:
            tasks = progress["tasks"]
            unbuilt = [t for t in tasks if t["state"] != "built"]
            built_ids = [t["id"] for t in tasks if t["state"] == "built"]
            headline = _("int_headline").format(
                done=len(built_ids), total=len(tasks)
            )
            id_rows = ""
            for chip, css, ids in (
                (_("chip_done"), "g", built_ids),
                (_("chip_left"), "q", [t["id"] for t in unbuilt]),
            ):
                if ids:
                    id_rows += (
                        "<div class=trow>"
                        f"<span class='chip {css}'>{html.escape(chip)}</span>"
                        f"<span class=ttl><code>"
                        f"{html.escape(' · '.join(ids))}</code></span></div>"
                    )
            # One click, not one per module. The interrupted page used to
            # offer only per-module resume buttons — N mechanical clicks for
            # something the resume machinery does whole: re-running the build
            # reuses the locked plan (no model calls), skips what is built,
            # attempts the rest with each task's recorded failure as context,
            # and auto-retries what still fails. The per-module buttons stay
            # for the founder who wants exactly one module back.
            continue_all = (
                "<form method=post action=/build style='display:inline'>"
                f"<button class=primary>{_('btn_continue_build')}"
                "</button></form> "
            )
            retries = "".join(
                f"<form method=post action=/retry style='display:inline'>"
                f"<input type=hidden name=task_id value='{html.escape(task['id'])}'>"
                f"<button class=secondary>{_('btn_resume')} "
                f"{html.escape(task['title'])}</button></form> "
                for task in unbuilt
            )
            done_note = (
                f"<p class=ok>{_('interrupted_all_done')}</p>"
                if not unbuilt
                else f"<p>{_('interrupted_resume')}</p>"
                f"<p>{continue_all}{retries}</p>"
            )
            return _render(
                request, _("title_interrupted"),
                "<div class=stateline><span class='sdot amber'></span>"
                f"<span class='slabel warn'>{_('int_chip')}</span></div>"
                f"<h1>{headline}</h1>"
                f"<p class=muted>{_('interrupted_lead')}</p>"
                f"{id_rows}{done_note}"
                f"{_worker_error_block()}"
                "<form method=post action=/reset style='margin-top:1rem'>"
                f"<button class=secondary>{_('btn_edit_and_restart')}"
                "</button></form>",
                h1="",
            )
        if report.exists():
            # Two different lists that used to be one. The features are what
            # the product HAS; product/features/* is a log of what was added
            # to it afterwards. Showing only the second under the heading
            # "Features" left the originally-built ones with no representation
            # anywhere on the page.
            feature_cards = _spec_cards()
            features_dir = root / "product" / "features"
            added_rows = ""
            if features_dir.is_dir():
                for d in sorted(features_dir.iterdir()):
                    state = (
                        _("state_done") if (d / "REPORT.md").exists()
                        else (_("state_pending_confirm")
                              if (d / "CONFIRMATION.md").exists() else "…")
                    )
                    added_rows += (
                        f"<div class=card>{html.escape(d.name)} — {state}</div>"
                    )
            added = (
                f"<h2>{_('h_recent_changes')}</h2>{added_rows}" if added_rows else ""
            )
            pending = _pending_feature(root)
            if pending:
                raw_confirmation = (pending / "CONFIRMATION.md").read_text(
                    encoding="utf-8"
                )
                return _render(
                    request, _("title_confirm_feature"),
                    _render_markdown(raw_confirmation)
                    + f"<details><summary class=muted>{_('md_original')}"
                    f"</summary><pre>{html.escape(raw_confirmation)}</pre>"
                    "</details>"
                    f"<form method=post action=/feature/build>"
                    f"<input type=hidden name=slug value='{html.escape(pending.name)}'>"
                    f"<button class=primary>{_('btn_build_feature')}"
                    "</button></form>",
                )

            # ── Verdict first. On a partly-built product the failed
            # modules are the only thing that needs the founder; they used
            # to sit under a screenshot gallery.
            tasks = _task_states(root)
            failed = _failed_tasks(root)
            failed_status: dict[str, str] = {}
            outcomes_path = root / "product" / "outcomes.yaml"
            if outcomes_path.exists():
                for o in yaml.safe_load(
                    outcomes_path.read_text(encoding="utf-8")
                ) or []:
                    if o.get("status") != "built" and o.get("task_id"):
                        failed_status[o["task_id"]] = str(
                            o.get("status", "failed")
                        )
            verdict_chip = (
                f"<span class='chip a'>{_('chip_partly')}</span>" if failed
                else f"<span class='chip g'>{_('chip_built')}</span>"
            )
            meta_bits = []
            if tasks:
                built_count = sum(1 for t in tasks if t["state"] == "built")
                meta_bits.append(_("rep_modules_fmt").format(
                    done=built_count, total=len(tasks)
                ))
            spent = _spend_month_fragment()
            if spent:
                meta_bits.append(spent)
            meta = (
                f"<span class=muted>{html.escape(' · '.join(meta_bits))}</span>"
                if meta_bits else ""
            )
            vhead = f"<div class=vhead>{verdict_chip}{meta}</div>"

            summary = _render_markdown(
                report.read_text(encoding="utf-8")
            )

            # ── Working now / Did not build, side by side. The primary
            # action — continue the build, which re-attempts every failed
            # module with its recorded failure as context — lives in the
            # amber column and only exists when failures do.
            columns = ""
            built_titles = [
                t["title"] for t in tasks if t["state"] == "built"
            ]
            if built_titles or failed:
                left = ""
                if built_titles:
                    left = (
                        f"<div class=colcard><div class='collab ok'>"
                        f"{_('rep_working')}</div>"
                        + "".join(
                            f"<div>{html.escape(title)}</div>"
                            for title in built_titles
                        )
                        + "</div>"
                    )
                right = ""
                if failed:
                    titles = {t["id"]: t["title"] for t in tasks}
                    failed_list = "".join(
                        f"<div><b>{html.escape(titles.get(fid, fid))}</b> "
                        f"<span class=warn>"
                        f"{html.escape(failed_status.get(fid, ''))}</span></div>"
                        for fid in failed
                    )
                    continue_all = (
                        "<form method=post action=/build style='display:inline'>"
                        f"<button class=primary>{_('btn_continue_build')}"
                        "</button></form> "
                    )
                    retries = "".join(
                        f"<form method=post action=/retry style='display:inline'>"
                        f"<input type=hidden name=task_id value='{html.escape(fid)}'>"
                        f"<button class=secondary>{_('btn_retry')} "
                        f"{html.escape(fid)}</button></form> "
                        for fid in failed
                    )
                    right = (
                        f"<div class='colcard amber'><div class='collab warn'>"
                        f"{_('failed_modules')}</div>{failed_list}"
                        f"<p class=muted>{_('failed_modules_hint')}</p>"
                        f"<p>{continue_all}{retries}</p></div>"
                    )
                columns = f"<div class=twocol>{left}{right}</div>"

            # ── The action chips: only routes that really exist right now.
            # "Try it" leads, because using the thing is the only way a
            # founder can answer the question they actually have.
            chips = [
                f"<a class=achip href='/try'>▶ {_('link_try')}</a>",
                f"<a class=achip href='/live'>🚀 {_('link_live')}</a>",
            ]
            if (root / "product" / "ACCEPTANCE.md").exists():
                chips.append(
                    f"<a class=achip href='/acceptance'>{_('link_acceptance')}</a>"
                )
            # The probes are the founder's own requirements, run against
            # what was built — the one artifact that answers "does it
            # actually work?" without asking them to judge code. It was
            # written to disk and never linked; a verification nobody can
            # see persuades nobody.
            if (root / "product" / "VERIFICATION.md").exists():
                chips.append(
                    f"<a class=achip href='/verification'>"
                    f"{_('link_verification')}</a>"
                )
            reviews = recent_reviews(root, limit=1)
            if reviews:
                review_id = html.escape(reviews[0]["review_id"])
                chips.append(
                    f"<a class=achip href='/review/{review_id}'>"
                    f"{_('title_review')}</a>"
                )
            chiprow = f"<div class=chiprow>{''.join(chips)}</div>"

            shots_dir = root / "product" / "screenshots"
            gallery = ""
            if shots_dir.is_dir():
                images = "".join(
                    f"<img class=shot src='/shots/{p.name}'>"
                    for p in sorted(shots_dir.glob("*.png"))
                )
                if images:
                    gallery = f"<h2>{_('h_screenshots')}</h2>{images}"
            if not gallery:
                from ai_venture_studio.studio_try import screenshot_note

                shot_note = screenshot_note(root)
                if shot_note:
                    gallery = (
                        f"<h2>{_('h_screenshots')}</h2>"
                        f"<p class=muted>{html.escape(shot_note)}</p>"
                    )

            no_features = f"<p class=muted>{_('first_version')}</p>"

            # ── ONE composer, TWO intents — because there are two forms and
            # there were three tabs. "Something wrong?" and "Is it broken?"
            # posted to the same /correct form and the router, which reads
            # the words and not the tab, could not tell them apart: a
            # decision the founder had to make and the backend then threw
            # away. What is genuinely two things is changing the product
            # (/correct) and adding to it (/feature). Both are real forms,
            # so both actions are in the HTML for the wireup gate and for
            # anyone without JavaScript; the tabs only toggle visibility,
            # they never hide a form from no-JS users.
            # The change list and the correction log are HISTORY: they sit
            # after the composer, not between its heading and its box.
            history = _change_list() + (
                f"<details><summary class=muted>{_('correction_log')}"
                f"</summary><pre>"
                f"{_md(root / 'product' / 'CORRECTION-LOG.md')}"
                "</pre></details>"
                if (root / "product" / "CORRECTION-LOG.md").exists()
                else ""
            )
            composer = (
                f"<h2>{_('composer_head')}</h2>"
                f"<p class=muted>{_('correction_hint')}</p>"
                "<div class=composerbox><div class=tabs id=fixtabs>"
                f"<button type=button class='tab on' data-form=form-correct>"
                f"{_('tab_change')}</button>"
                f"<button type=button class=tab data-form=form-feature>"
                f"{_('tab_add')}</button>"
                "</div>"
                "<form method=post action=/correct id=form-correct>"
                "<textarea name=complaint "
                f"placeholder='{_('correction_placeholder')}'></textarea>"
                "<div class=comprow><span class=muted></span>"
                f"<button class=primary>{_('btn_correct')}</button></div></form>"
                "<form method=post action=/feature id=form-feature>"
                f"<div style='padding:12px 16px 0'><b>{_('h_add_feature')}</b>"
                f"<p class=muted>{_('feature_hint')}</p></div>"
                f"<textarea name=fdr placeholder='{_('feature_placeholder')}'>"
                "</textarea>"
                "<div class=comprow><span class=muted></span>"
                f"<button class=primary>{_('btn_check_feature')}</button>"
                "</div></form></div>"
                "<script>\n"
                "(function(){\n"
                "const tabs=document.querySelectorAll('#fixtabs .tab');\n"
                "function pick(tab){\n"
                "  tabs.forEach(b=>b.classList.toggle('on',b===tab));\n"
                "  ['form-correct','form-feature'].forEach(id=>{\n"
                "    document.getElementById(id).style.display=\n"
                "      id===tab.dataset.form?'':'none';\n"
                "  });\n"
                "}\n"
                "tabs.forEach(b=>b.addEventListener('click',()=>pick(b)));\n"
                "pick(tabs[0]);\n"
                "})();\n"
                "</script>"
            )

            footer = (
                f"<div class=footbar>{_spend_lines()}"
                "<form method=post action=/undo class=actions>"
                f"<button class=linkish>{_('btn_undo')}</button></form></div>"
            )
            return _render(
                request, _("title_product"),
                vhead + f"<h1>{_('title_product')}</h1>" + summary + columns
                + chiprow + gallery
                + f"<h2>{_('h_features')}</h2>"
                + (feature_cards or no_features)
                + added
                + composer + history + footer,
                rail="product", h1="",
            )
        if confirmation.exists():
            # The confirm page is where the founder decides to spend money —
            # the running total and the ceiling's true home sit in the same
            # footer bar as the button that starts the spend. The
            # confirmation markdown is rendered, not dumped: the NOT-building
            # section gets the amber callout because this page is the
            # cheapest place to catch a misunderstanding.
            raw_confirmation = confirmation.read_text(encoding="utf-8")
            footer = (
                f"<div class=footbar>{_spend_lines()}"
                "<div class=actions>"
                "<form method=post action=/reset>"
                f"<button class=secondary>{_('btn_edit_fdr')}</button></form>"
                "<form method=post action=/build>"
                f"<button class=primary>{_('btn_start_building')}</button>"
                "</form></div></div>"
            )
            return _render(
                request, _("title_confirm_plan"),
                f"<p class=muted>{_('confirm_hint')}</p>"
                + _render_markdown(raw_confirmation)
                + f"<details><summary class=muted>{_('md_original')}"
                f"</summary><pre>{html.escape(raw_confirmation)}</pre></details>"
                + footer,
                rail="plan",
            )
        # Nothing above this line spends a token: a running build, a report,
        # a plan to confirm are all reads of the workspace. The describe
        # state is the first thing that will call the model, so it is where
        # "whose account pays" belongs.
        if _needs_key():
            return _key_gate_page(request)
        # The describe state, and only this state, honours `entry`: the
        # build/report/confirmation pages above are the same in both doors.
        if entry == "chat" and not request.query_params.get("form"):
            return _chat_page(request)
        guide = _md(root / "FDR-GUIDE.md")
        question_block = (
            f"<div class=callout><b class=warn>{_('answer_first')}"
            f"</b><pre>{_md(questions)}</pre></div>"
            if questions.exists()
            else ""
        )
        from ai_venture_studio.upstream.fdr import template_for

        current = (
            fdr.read_text(encoding="utf-8") if fdr.exists() else template_for(lang)
        )
        return _render(
            request, _("title_describe"),
            f"{_key_strip()}{question_block}"
            f"<form method=post action=/fdr>"
            f"<input type=hidden name=base value='{_fdr_fingerprint(current)}'>"
            f"<textarea name=fdr class=fdrbox>{html.escape(current)}</textarea>"
            f"<p><button class=primary>{_('btn_check_and_plan')}</button></p>"
            f"</form>"
            f"<p><a href='/chat'>{_('chat_switch_to_chat')}</a></p>"
            f"<details><summary class=muted>{_('guide_summary')}"
            f"</summary><pre>{guide}</pre></details>",
            rail="describe",
        )

    def _conflict_page(
        request: Request, submitted: str, on_disk: str
    ) -> HTMLResponse:
        """Both versions, and the founder picks. Never a silent merge and
        never a silent overwrite — the one thing this page must not do is
        decide on its own which set of words to throw away."""
        return _render(
            request, _("title_conflict"),
            f"<div class=card><b class=warn>{_('conflict_lead')}</b>"
            f"<p>{_('conflict_hint')}</p></div>"
            f"<div class=card><b>{_('conflict_on_disk')}</b>"
            f"<pre>{html.escape(on_disk)}</pre>"
            "<form method=post action=/fdr>"
            f"<input type=hidden name=base value='{_fdr_fingerprint(on_disk)}'>"
            f"<input type=hidden name=fdr value='{html.escape(on_disk)}'>"
            f"<button>{_('btn_use_on_disk')}</button></form></div>"
            f"<div class=card><b>{_('conflict_yours')}</b>"
            f"<pre>{html.escape(submitted)}</pre>"
            "<form method=post action=/fdr>"
            "<input type=hidden name=force value=1>"
            f"<input type=hidden name=fdr value='{html.escape(submitted)}'>"
            f"<button class=secondary>{_('btn_use_mine')}</button></form></div>",
        )

    # ── The conversational intake ────────────────────────────────────────
    # An alternative door to the SAME FDR, never a second source of truth:
    # every answer is composed into FDR.md and the existing flow takes over
    # from there. The form stays exactly as it was for anyone who prefers it.

    def _written_fdr() -> str:
        """The founder's existing FDR, or "" if there is nothing real yet.

        A blank template is not content — in either language, since a
        workspace initialised as `zh` can be served with `--lang en`.
        """
        path = root / "FDR.md"
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        from ai_venture_studio.upstream.fdr import TEMPLATE, TEMPLATE_EN

        if not text.strip() or text.strip() in (
            TEMPLATE.strip(), TEMPLATE_EN.strip()
        ):
            return ""
        return text

    def _compose(turns) -> str:
        """The FDR this conversation produces.

        A conversation that answered the six intake questions AUTHORS the
        document. One that only answered follow-ups is a clarify pass over
        an FDR written elsewhere (the form, the CLI, by hand) and must
        extend it, never replace it with six blank sections.
        """
        base = "" if studio_chat.has_intake(turns) else _written_fdr()
        return studio_chat.compose_fdr(turns, lang, base_fdr=base)

    def _open_questions() -> list[str]:
        """Questions the assessor already left on disk (FDR-QUESTIONS.md).

        The form writes them there; with the conversation as the front door
        they would otherwise be invisible — the founder would land on a page
        that says nothing about the five things blocking their build.
        """
        path = root / "FDR-QUESTIONS.md"
        if not path.exists():
            return []
        found = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if re.match(r"^\d+\.\s+\S", stripped):
                found.append(re.sub(r"^\d+\.\s+", "", stripped))
        return found

    def _worker_error_block() -> str:
        """Why the build stopped, if the detached worker left a reason.

        The worker's traceback goes to .mas/build.log and nowhere the
        founder looks, so "The build was interrupted" was the whole story
        for a run that had actually died on a hard, repeatable provider
        error. Same rule as everywhere else here: plain language first, the
        real thing one click away.
        """
        log = root / ".mas" / "build.log"
        if not log.exists():
            return ""
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        # The last exception line is the one that ended the run; rich draws
        # a box around the traceback, so prefer a bare `Error: message`.
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        headline = next(
            (
                line for line in reversed(lines)
                if re.match(r"^[A-Za-z_.]*(Error|Exception)\b", line)
            ),
            "",
        )
        if not headline and not lines:
            return ""
        tail = "\n".join(lines[-40:])
        return (
            f"<p class=bad>{html.escape(headline[:300])}</p>" if headline else ""
        ) + (
            f"<details><summary class=muted>{_('int_why')}</summary>"
            f"<pre>{html.escape(tail)}</pre></details>"
        )

    def _sidebar_values(turns) -> dict[str, str]:
        """What the draft FDR already holds, per intake slot — parsed from
        the SAME document `_compose` writes at handoff, so the sidebar can
        never show something the file would not contain. Read-only: nothing
        here is a second source of truth."""
        composed = _compose(turns)
        values: dict[str, str] = {}
        current: str | None = None
        headings = {
            heads[slot].strip(): slot
            for heads in studio_chat._HEADINGS.values()
            for slot in studio_chat.INTAKE_SLOTS
        }
        for line in composed.splitlines():
            stripped = line.strip()
            if stripped in headings:
                current = headings[stripped]
                continue
            if stripped.startswith("#"):
                current = None
                continue
            if (
                current and stripped
                and "not answered" not in stripped and "未回答" not in stripped
            ):
                values.setdefault(current, stripped)
        return values

    def _chat_sidebar(turns) -> str:
        """The document being written, beside the conversation: the six
        intake sections with a filled/empty dot each, the escape hatch to
        the plan, and the door to the raw file."""
        values = _sidebar_values(turns)
        slots = ""
        for slot in studio_chat.INTAKE_SLOTS:
            value = values.get(slot, "")
            if value:
                slots += (
                    "<div class=slot><div class=slothead>"
                    f"<span class=ok>●</span>"
                    f"<span>{_(f'chat_slot_{slot}')}</span></div>"
                    f"<div class=val>{html.escape(value)}</div></div>"
                )
            else:
                slots += (
                    "<div class='slot empty'><div class=slothead>"
                    f"<span class=muted>○</span>"
                    f"<span>{_(f'chat_slot_{slot}')}</span></div></div>"
                )
        return (
            "<aside class=chatside>"
            "<div class=sidehead>"
            f"<div class=lbl style='margin:0 0 6px'>{_('chat_sidebar_head')}</div>"
            f"<div class=muted>{_('chat_sidebar_note')}</div></div>"
            f"<div class=sidebody>{slots}</div>"
            "<div class=sidefoot>"
            "<form method=post action=/chat/enough>"
            f"<button style='width:100%'>{_('btn_chat_enough')}</button></form>"
            f"<a class=muted href='/?form=1'>{_('chat_read_file')}</a>"
            "</div></aside>"
        )

    def _thread_html(turns) -> str:
        """The conversation as it happened — plus, where the extraction pass
        ran, what was TAKEN from the founder's paragraph.

        The synthetic question a SAID pair carries is bookkeeping, not
        dialogue: rendering it as an assistant bubble would show the founder
        a conversation that never took place. So the pairs render as rows
        instead — SAID for their own words, GUESS for a proposal — and the
        two are visibly different things on the page, because they are
        different things in the document.
        """
        out: list[str] = []
        rows: list[str] = []

        def flush() -> None:
            if rows:
                out.append(
                    f"<div class=card><div class=lbl style='margin-top:0'>"
                    f"{_('chat_extract_head')}</div>" + "".join(rows) + "</div>"
                )
                rows.clear()

        for turn in turns:
            label = (
                _(f"chat_slot_{turn.slot}")
                if turn.slot in studio_chat.INTAKE_SLOTS else turn.slot
            )
            if turn.kind == studio_chat.SAID:
                if turn.role == "assistant":
                    continue  # the synthetic question is not a thing anyone said
                rows.append(
                    "<div class=trow><span class='chip g'>"
                    f"{_('chat_chip_said')}</span><span class=ttl>"
                    f"<b>{label}</b> — {html.escape(turn.text)}</span></div>"
                )
                continue
            if turn.kind == studio_chat.GUESS:
                why = (
                    f"<span class=tstep>{html.escape(turn.text)}</span>"
                    if turn.text and turn.text != turn.value else ""
                )
                rows.append(
                    "<div class=trow><span class='chip a'>"
                    f"{_('chat_chip_guess')}</span><span class=ttl>"
                    f"<b>{label}</b> — {html.escape(turn.value)}{why}"
                    "</span></div>"
                )
                continue
            flush()
            out.append(
                "<div class=msg-a><span class=dot7></span>"
                f"<p>{html.escape(turn.text)}</p></div>"
                if turn.role == "assistant"
                else f"<div class=msg-u><p>{html.escape(turn.text)}</p></div>"
            )
        flush()
        return "".join(out)

    def _guess_composer(guess) -> str:
        """A guess is settled by confirming or correcting it — two plain
        forms, no JavaScript. Confirming is the ONLY thing that lets
        proposed words into FDR.md; correcting replaces them with the
        founder's own."""
        label = (
            _(f"chat_slot_{guess.slot}")
            if guess.slot in studio_chat.INTAKE_SLOTS else guess.slot
        )
        return (
            "<div class=card>"
            f"<b>{_('chat_guess_head')}</b>"
            f"<p><span class='chip a'>{_('chat_chip_guess')}</span> "
            f"<b>{html.escape(label)}</b> — {html.escape(guess.value)}</p>"
            f"<p class=muted>{_('chat_guess_note')}</p>"
            "<form method=post action=/chat/guess style='display:inline'>"
            f"<button class=primary name=accept value=1>"
            f"{_('btn_guess_yes')}</button></form>"
            "<form method=post action=/chat/guess>"
            f"<p class=muted style='margin:12px 0 4px'>{_('chat_guess_fix')}</p>"
            "<div class=composer><textarea name=answer></textarea>"
            "<div class=comprow><span class=muted></span>"
            f"<button class=secondary>{_('btn_guess_mine')}</button>"
            "</div></div></form></div>"
        )

    def _chat_page(request: Request, note: str = "") -> HTMLResponse:
        turns = studio_chat.load_thread(root)
        existing = _written_fdr()
        # "Started" means an ANSWER exists. A thread holding only a question
        # nobody replied to is a page that was opened once, and it must not
        # suppress the offer below — merely looking at the conversation
        # should not commit you to it.
        started = bool(studio_chat.pairs(turns))
        if not started and existing:
            studio_chat.reset_thread(root)
            turns = []
        pending = _open_questions() if existing else []
        if not started and pending and not request.query_params.get("start"):
            # Straight into answering them, one at a time — this is exactly
            # the loop the conversation exists to fix.
            studio_chat.append_turn(
                root, "assistant", pending[0], slot=studio_chat.CLARIFY
            )
            turns = studio_chat.load_thread(root)
        # Only when there is nothing outstanding: unanswered assessor
        # questions are the more urgent thing to show, and the branch above
        # has already put the first one in the thread.
        if (
            not started
            and existing
            and not pending
            and not request.query_params.get("start")
        ):
            # Never start interviewing someone about a document they already
            # wrote. Offer it back first; the conversation is the other button.
            return _render(
                request, _("title_chat"),
                f"<div class=card><b>{_('chat_have_fdr')}</b>"
                f"<p class=muted>{_('chat_have_fdr_hint')}</p></div>"
                f"<div class=card><pre>{html.escape(existing)}</pre></div>"
                "<form method=post action=/fdr style='display:inline'>"
                f"<input type=hidden name=base value='{_fdr_fingerprint(existing)}'>"
                f"<input type=hidden name=fdr value='{html.escape(existing)}'>"
                f"<button class=primary>{_('btn_check_and_plan')}</button></form> "
                f"<a href='/?form=1'><button type=button class=secondary>"
                f"{_('btn_edit_fdr')}</button></a>"
                f"<p><a href='/chat?start=1'>{_('chat_start_over')}</a></p>",
                rail="describe",
            )
        thread = _thread_html(turns)
        question = studio_chat.open_question(turns)
        guess = studio_chat.pending_guess(turns) if question is None else None
        if question is None and guess is None:
            # Nothing pending: the open prompt on a fresh thread, then only
            # the slots the extraction did not fill — or hand over.
            slot = studio_chat.next_question_slot(turns)
            if slot is None:
                return _chat_assess(request)
            question = studio_chat.append_turn(
                root, "assistant", _(f"chat_q_{slot}"), slot=slot
            )
            thread += (
                "<div class=msg-a><span class=dot7></span>"
                f"<p>{html.escape(question.text)}</p></div>"
            )
        if guess is not None:
            composer = _guess_composer(guess)
        else:
            answered = len({
                q.slot for q, _a in studio_chat.pairs(turns)
                if q.slot in studio_chat.INTAKE_SLOTS
            })
            total = len(studio_chat.INTAKE_SLOTS)
            if question.slot == studio_chat.OPEN:
                counter = _("chat_open_lead")
            elif question.slot in studio_chat.INTAKE_SLOTS:
                counter = f"{min(answered + 1, total)} / {total}"
            else:
                counter = _("chat_clarify_lead")
            composer = (
                f"<form method=post action=/chat>"
                f"<p class=muted style='margin:0'>{counter}</p>"
                "<div class=composer>"
                "<textarea name=answer autofocus></textarea>"
                "<div class=comprow>"
                f"<span class=muted>{_('chat_composer_note')}</span>"
                f"<span style='flex-shrink:0'>"
                f"<button class=secondary name=skip value=1>{_('btn_chat_skip')}"
                "</button> "
                f"<button class=primary>{_('btn_chat_send')}</button></span>"
                "</div></div></form>"
            )
        main = (
            "<div class=chatmain>"
            f"{_key_strip()}"
            f"<p class=muted style='margin:0'>{_('chat_intro')}</p>"
            + (f"<div class=callout><b class=warn>{html.escape(note)}</b></div>"
               if note else "")
            + thread
            + composer
            + "<form method=post action=/chat/restart style='text-align:center'>"
            f"<button class=linkish>{_('btn_chat_restart')}</button></form>"
            "</div>"
        )
        return _render(
            request, _("title_chat"),
            f"<div class=chatwrap>{main}{_chat_sidebar(turns)}</div>",
            rail="describe", h1="",
        )

    @app.get("/chat", response_class=HTMLResponse)
    def chat(request: Request):
        if "fdr" in thinking:
            return _thinking_page(request, thinking["fdr"])
        # Same gate as the home door: /chat is the describe state, and the
        # first message here is the first model call.
        if _needs_key():
            return _key_gate_page(request)
        return _chat_page(request)

    @app.post("/chat")
    async def chat_answer(request: Request):
        if "fdr" in thinking:
            return _thinking_page(request, thinking["fdr"])
        form = await request.form()
        turns = studio_chat.load_thread(root)
        question = studio_chat.open_question(turns)
        if question is None:
            return RedirectResponse("/chat", status_code=303)
        answer = str(form.get("answer", "")).strip()
        skipped = bool(form.get("skip")) or not answer
        if skipped:
            answer = _("chat_skipped")
        studio_chat.append_turn(root, "user", answer, slot=question.slot)
        if question.slot == studio_chat.OPEN and not skipped:
            # ONE pass over what they wrote, before anything else is asked.
            # Same in-flight guard as every other model call on this surface:
            # a second submit lands on the working page instead of starting a
            # second extraction over the same paragraph.
            from starlette.concurrency import run_in_threadpool

            thinking["fdr"] = _("chat_reading")
            try:
                extraction = await run_in_threadpool(
                    studio_chat.extract_intake, answer, provider=provider
                )
            except Exception as exc:  # noqa: BLE001 — a page, never a 500
                # Their paragraph is already in the thread, so the next page
                # load simply falls back to asking the six.
                return _failure_page(request, exc)
            finally:
                thinking.pop("fdr", None)
            studio_chat.apply_extraction(
                root, extraction,
                {slot: _(f"chat_q_{slot}") for slot in studio_chat.INTAKE_SLOTS},
            )
        # POST-redirect-GET, always: the GET decides whether the next step is
        # another question or the assessment. Keeping that decision in one
        # place is also why the assessment is not its own route — it is a
        # transition, and an endpoint no rendered page links to is an orphan
        # (caught by tests/test_studio_wireup.py).
        return RedirectResponse("/chat", status_code=303)

    @app.post("/chat/guess")
    async def chat_guess(request: Request):
        """Confirm or correct ONE proposal.

        The charter rule lives here: proposed words become an answer only on
        the confirm branch, because that is the branch where the founder
        said them. The pending guess is resolved from the thread, never from
        the form — a posted slot would let a page decide which slot it was
        answering.
        """
        if "fdr" in thinking:
            return _thinking_page(request, thinking["fdr"])
        form = await request.form()
        turns = studio_chat.load_thread(root)
        guess = studio_chat.pending_guess(turns)
        if guess is None:
            return RedirectResponse("/chat", status_code=303)
        typed = str(form.get("answer", "")).strip()
        if form.get("accept"):
            answer = guess.value
        elif typed:
            answer = typed
        else:
            # Declined without a replacement: recorded as skipped, exactly
            # like the skip button. The one thing it must not do is leave
            # the proposal pending forever.
            answer = _("chat_skipped")
        studio_chat.resolve_guess(
            root, guess, answer,
            _(f"chat_q_{guess.slot}") if guess.slot in studio_chat.INTAKE_SLOTS
            else guess.slot,
        )
        return RedirectResponse("/chat", status_code=303)

    def _chat_assess(request: Request) -> HTMLResponse:
        """Compose the FDR from the conversation, then ask the assessor
        whether it is buildable. Bounded: after MAX_CLARIFY_ROUNDS the
        conversation stops asking and goes to the plan."""
        if "fdr" in thinking:
            return _thinking_page(request, thinking["fdr"])
        turns = studio_chat.load_thread(root)
        # Composed in memory, NOT written yet: FDR.md is only replaced at
        # handoff, and only after the existing one is preserved. Writing here
        # would destroy a hand-written FDR the moment somebody tried the
        # conversation out of curiosity.
        composed = _compose(turns)
        if studio_chat.clarify_rounds_used(turns) >= studio_chat.MAX_CLARIFY_ROUNDS:
            return _chat_handoff(request, note=_("chat_rounds_done"))

        from ai_venture_studio.upstream.fdr import assess_fdr

        thinking["fdr"] = _("chat_checking")
        try:
            assessment = assess_fdr(composed, provider=provider)
        except Exception as exc:  # noqa: BLE001 — a founder gets a page, not a 500
            return _failure_page(request, exc)
        finally:
            thinking.pop("fdr", None)

        if assessment.ready or not assessment.questions:
            return _chat_handoff(request)
        # One at a time — the whole point of this surface. The remaining
        # questions are re-derived on the next round against the fuller FDR,
        # which is usually a shorter list than the one we just got.
        studio_chat.append_turn(
            root, "assistant", assessment.questions[0], slot=studio_chat.CLARIFY
        )
        return _chat_page(request)

    def _chat_handoff(request: Request, note: str = "") -> HTMLResponse:
        """The conversation is done: FDR.md is written, so the normal flow
        (confirmation → build) takes over from the home page.

        An FDR that already existed is copied to FDR-before-chat.md first.
        Someone with a hand-written FDR who clicks the conversation link to
        see what it does must not lose it — losing the founder's own words
        is the worst thing this surface could do.
        """
        turns = studio_chat.load_thread(root)
        composed = _compose(turns)
        existing = root / "FDR.md"
        preserved = ""
        if existing.exists():
            previous = existing.read_text(encoding="utf-8")
            backup = root / "FDR-before-chat.md"
            if previous.strip() and previous != composed and not backup.exists():
                backup.write_text(previous, encoding="utf-8")
                preserved = backup.name
        existing.write_text(composed, encoding="utf-8")
        (root / "FDR-QUESTIONS.md").unlink(missing_ok=True)
        if preserved:
            note = (note + " " if note else "") + _("chat_prior_fdr_saved").format(
                name=preserved
            )
        return _render(
            request, _("title_chat"),
            (f"<div class=card><b>{html.escape(note)}</b></div>" if note else "")
            + f"<div class=card><pre>{html.escape((root / 'FDR.md').read_text(encoding='utf-8'))}</pre></div>"
            f"<form method=post action=/fdr>"
            f"<input type=hidden name=fdr value='{html.escape((root / 'FDR.md').read_text(encoding='utf-8'))}'>"
            f"<button class=primary>{_('btn_check_and_plan')}</button></form>"
            f"<p><a href='/chat'>{_('btn_chat_restart')}</a></p>",
            rail="describe",
        )

    @app.post("/chat/enough")
    def chat_enough(request: Request):
        """The escape hatch. An under-specified FDR the founder chose is
        better than a question loop they cannot leave."""
        return _chat_handoff(request, note=_("chat_rounds_done"))

    @app.post("/chat/restart")
    def chat_restart(request: Request):
        studio_chat.reset_thread(root)
        return RedirectResponse("/chat", status_code=303)

    @app.post("/fdr")
    async def save_fdr(request: Request):
        if "fdr" in thinking:
            return _thinking_page(request, thinking["fdr"])
        form = await request.form()
        submitted = str(form.get("fdr", ""))
        fdr_path = root / "FDR.md"
        # Optimistic concurrency: refuse to overwrite an FDR that changed
        # after this page was rendered. `force` is the founder's explicit
        # "yes, use mine" from the conflict page.
        base = str(form.get("base", ""))
        if base and not form.get("force") and fdr_path.exists():
            on_disk = fdr_path.read_text(encoding="utf-8")
            if _fdr_fingerprint(on_disk) != base and on_disk != submitted:
                return _conflict_page(request, submitted, on_disk)
        # Submitting the requirements form is the founder deciding scope
        # again, which is exactly what Gate U2's lock is not allowed to
        # block. Release it here, where the new document is written, rather
        # than letting the build refuse two screens later.
        from ai_venture_studio.upstream.plan import release_lock_if_fdr_changed

        release_lock_if_fdr_changed(root, submitted)
        fdr_path.write_text(submitted, encoding="utf-8")
        for stale in ("FDR-QUESTIONS.md",):
            (root / stale).unlink(missing_ok=True)
        from starlette.concurrency import run_in_threadpool

        from ai_venture_studio.upstream.autopilot import run_autopilot

        thinking["fdr"] = _("working_fdr")
        try:
            # LLM calls block for minutes — off the event loop (sweep
            # finding), or the progress page can't even poll while the
            # assessor runs.
            await run_in_threadpool(
                run_autopilot, root, root / "FDR.md", yes=False, provider=provider
            )
        except Exception as exc:  # noqa: BLE001 — a founder gets a page, not a 500
            return _failure_page(request, exc)
        finally:
            thinking.pop("fdr", None)
        return RedirectResponse("/", status_code=303)

    @app.get("/verification", response_class=HTMLResponse)
    def verification(request: Request):
        return _render(
            request, _("title_verification"),
            f"<pre>{_md(root / 'product' / 'VERIFICATION.md')}</pre>"
            f"<p><a href='/'>{_('link_back')}</a></p>",
        )

    @app.get("/acceptance", response_class=HTMLResponse)
    def acceptance(request: Request):
        return _render(
            request, _("title_acceptance"),
            f"<pre>{_md(root / 'product' / 'ACCEPTANCE.md')}</pre>"
            f"<p><a href='/try'>{_('link_try')}</a></p>"
            f"<p><a href='/'>{_('link_back')}</a></p>",
        )

    @app.get("/try", response_class=HTMLResponse)
    def try_it(request: Request):
        """The product on the left, its own acceptance list on the right."""
        from ai_venture_studio.studio_try import try_body

        try:
            profile = _profile(root)
        except Exception:  # noqa: BLE001 — a workspace without a profile
            profile = ""
        return _render(request, _("title_try"), try_body(root, _, profile),
                       rail="product")

    @app.post("/try/tick")
    async def try_tick(request: Request):
        """One founder tick. Explicitly not a verdict about the product:
        nothing here changes what was built, and the page says so."""
        from ai_venture_studio.studio_try import acceptance_rows, set_tick

        form = await request.form()
        row_id = str(form.get("row", ""))
        # Same rule as every other identifier arriving from a form: shape
        # first, then existence. This one becomes a key in a workspace file.
        if not _REVIEW_ID.match(row_id):
            return RedirectResponse("/try", status_code=303)
        row = next((r for r in acceptance_rows(root) if r.id == row_id), None)
        if row is None:
            return _no_such_page(request, "title_no_row", row_id)
        set_tick(root, row, on=not form.get("off"))
        return RedirectResponse("/try", status_code=303)

    @app.get("/review/{review_id}", response_class=HTMLResponse)
    def review_detail(request: Request, review_id: str):
        """One review's mirror as a timeline — `avs replay` in the browser.
        Linked from the engineer card; reachable in every mode (modes add
        visibility, they never own a page)."""
        if not _REVIEW_ID.match(review_id):
            raise HTTPException(404)
        try:
            body = review_timeline_body(root, review_id, _)
        except FileNotFoundError:
            raise HTTPException(404) from None
        return _render(request, _("title_review"), body)

    @app.get("/demo", response_class=HTMLResponse)
    def demo_review(request: Request):
        """`avs replay --demo` in the browser: the vendored demo bundle —
        a real, redacted review of this repo's own code — rendered by the
        same timeline the workspace's own reviews use. No key, no
        workspace, and no second source of truth: the YAML mirror shipped
        with the package is the whole input."""
        from ai_venture_studio.editions import EDITIONS_ROOT

        reviews_dir = EDITIONS_ROOT / "demo" / "reviews"
        names = sorted(p.name for p in reviews_dir.iterdir() if p.is_dir())
        if not names:
            return _no_such_page(request, "title_no_demo", "demo")
        body = review_timeline_body(
            root, names[0], _, reviews_dir=reviews_dir, evidence=False
        )
        return _render(
            request, _("title_demo"),
            f"<p class=muted>{_('demo_note')}</p>{body}",
        )

    @app.get("/shots/{name}")
    def shot(name: str):
        from fastapi.responses import FileResponse

        path = (root / "product" / "screenshots" / name).resolve()
        if not path.is_file() or path.parent != (root / "product" / "screenshots").resolve():
            raise HTTPException(404)
        return FileResponse(path)

    def _issue_card(index: int, route, many: bool) -> str:
        """One routed issue, with its own promise stated on its own card.

        Each carries a checkbox when there are several, because "fix all
        three" and "fix the first one, I was only thinking aloud about the
        others" are both reasonable and only the founder knows which.
        """
        scope = route.kind == "scope_change"
        chip = _("cls_scope_chip") if scope else _("cls_fix_chip")
        # The founder's own words for THIS issue when the router quoted
        # them verbatim; the check that they ARE verbatim already happened
        # in the router, so anything here is safe to show under "Your words".
        quoted = (
            f"<div class=lbl style='margin-top:0'>{_('cls_your_words')}</div>"
            f"<p>{html.escape(route.quote)}</p>" if route.quote else ""
        )
        box = (
            f"<label class=lbl style='margin-top:0'>"
            f"<input type=checkbox name=include value='{index}' checked> "
            f"{_('cls_issue_n').format(n=index + 1)}</label>" if many else ""
        )
        return (
            "<div class=card>"
            + box
            + f"<div class=stateline style='margin:6px 0 10px'>"
            f"<span class='sdot {'amber' if scope else 'green'}'></span>"
            f"<span class='slabel {'warn' if scope else 'ok'}'>{chip}</span></div>"
            + quoted
            + f"<div class=lbl>{_('cls_feature')}</div>"
            f"<p><code>{html.escape(route.spec_slug)}</code></p>"
            f"<div class=lbl>{_('cls_instruction')}</div>"
            f"<p class=muted>{html.escape(route.instruction)}</p>"
            # Aligned with `include` by position, so unticking issue 2
            # cannot shift what issue 3 means.
            f"<input type=hidden name=spec_slug value='{html.escape(route.spec_slug)}'>"
            f"<input type=hidden name=kind value='{html.escape(route.kind)}'>"
            f"<input type=hidden name=quote value='{html.escape(route.quote)}'>"
            f"<input type=hidden name=instruction value='{html.escape(route.instruction)}'>"
            "</div>"
        )

    def _classification_page(
        request: Request, complaint: str, criterion: str, routes: list
    ) -> HTMLResponse:
        """What the router decided, before it is acted on.

        The two outcomes are different promises — a small fix is repaired
        directly, a new requirement becomes its own small build — and the
        founder is the only one who knows which they meant. Two plain
        forms: confirm what it says, or reword it and route again.

        A founder listing three problems in one message gets three cards,
        not the first one silently answered. That was worth the plural:
        the page is where the split becomes visible, and a split the
        founder cannot see is the same as a split that did not happen.
        """
        many = len(routes) > 1
        scope = any(r.kind == "scope_change" for r in routes)
        if many:
            head = f"<h1>{_('cls_many_head').format(n=len(routes))}</h1>"
            lead = f"<p>{_('cls_many_what')}</p>"
            # The whole message stays on the page above the split, so the
            # founder can check nothing of theirs went missing.
            said = (
                "<div class=card>"
                f"<div class=lbl style='margin-top:0'>{_('cls_your_words')}</div>"
                f"<p>{html.escape(complaint)}</p></div>"
            )
        else:
            one = routes[0]
            one_scope = one.kind == "scope_change"
            head = (
                "<div class=stateline>"
                f"<span class='sdot {'amber' if one_scope else 'green'}'></span>"
                f"<span class='slabel {'warn' if one_scope else 'ok'}'>"
                f"{_('cls_scope_chip') if one_scope else _('cls_fix_chip')}"
                "</span></div>"
                f"<h1>{_('cls_scope_head') if one_scope else _('cls_fix_head')}</h1>"
            )
            lead = f"<p>{_('cls_scope_what') if one_scope else _('cls_fix_what')}</p>"
            said = (
                "<div class=card>"
                f"<div class=lbl style='margin-top:0'>{_('cls_your_words')}</div>"
                f"<p>{html.escape(complaint)}</p>"
                + (f"<div class=lbl>{_('cls_criterion')}</div>"
                   f"<p class=muted>{html.escape(criterion)}</p>" if criterion else "")
                + f"<div class=lbl>{_('cls_feature')}</div>"
                f"<p><code>{html.escape(routes[0].spec_slug)}</code></p>"
                f"<div class=lbl>{_('cls_instruction')}</div>"
                f"<p class=muted>{html.escape(routes[0].instruction)}</p></div>"
            )
        if many:
            crit = (
                "<div class=card>"
                f"<div class=lbl style='margin-top:0'>{_('cls_criterion')}</div>"
                f"<p class=muted>{html.escape(criterion)}</p></div>"
                if criterion else ""
            )
            cards = crit + "".join(
                _issue_card(i, r, True) for i, r in enumerate(routes)
            )
        else:
            # The single card already said everything; repeating it would
            # only add a checkbox with nothing to choose between.
            cards = ""
        if many:
            button = _("btn_cls_confirm_all").format(n=len(routes))
        else:
            button = (
                _("btn_cls_confirm_scope") if scope else _("btn_cls_confirm_fix")
            )
        # Confirm: the classification the founder just read is the one that
        # executes — the routes travel with the form rather than being
        # decided a second time.
        hidden = "" if many else (
            f"<input type=hidden name=spec_slug value='{html.escape(routes[0].spec_slug)}'>"
            f"<input type=hidden name=kind value='{html.escape(routes[0].kind)}'>"
            f"<input type=hidden name=quote value='{html.escape(routes[0].quote)}'>"
            f"<input type=hidden name=instruction value='{html.escape(routes[0].instruction)}'>"
        )
        return _render(
            request, _("title_classify"),
            head + lead + said
            + "<form method=post action=/correct/confirm>"
            + f"<input type=hidden name=complaint value='{html.escape(complaint)}'>"
            + f"<input type=hidden name=criterion value='{html.escape(criterion)}'>"
            + cards + hidden
            + f"<button class=primary>{button}</button></form>"
            + "<form method=post action=/correct style='margin-top:18px'>"
            + f"<div class=lbl>{_('cls_reword')}</div>"
            + f"<input type=hidden name=criterion value='{html.escape(criterion)}'>"
            + f"<textarea name=complaint>{html.escape(complaint)}</textarea>"
            + f"<p><button class=secondary>{_('btn_cls_reword')}</button></p>"
            + "</form>"
            + f"<p class=muted>{_('cls_nothing_yet')}</p>",
            h1="",
        )

    def _change_card(result) -> str:
        """What will change, and one button that does it.

        This is what replaced `re-run \\`avs add\\`/\\`spec\\` for 'cart' to
        regenerate` — a terminal instruction rendered to someone in a
        browser, under a message saying it had worked.

        The founder is spending their own token, so there is no cost to
        confirm. What is worth confirming is whether the change was
        understood, which is why the assumptions are on the card: they are
        the decisions made on the founder's behalf, stated flat so a wrong
        one is visible before it is built rather than after.
        """
        plan = result.plan
        if plan is None:  # pragma: no cover — status implies a plan
            return f"<p class=muted>{html.escape(result.detail)}</p>"
        assumptions = "".join(
            f"<li>{html.escape(a)}</li>" for a in plan.assumptions
        )
        criteria = "".join(
            f"<li>{html.escape(c)}</li>" for c in plan.criteria
        )
        payload = json.dumps(plan.model_dump(), ensure_ascii=False)
        return (
            f"<p><b>{html.escape(plan.summary)}</b></p>"
            + (f"<div class=lbl>{_('chg_assumptions')}</div>"
               f"<ul class=muted>{assumptions}</ul>" if assumptions else "")
            + (f"<div class=lbl>{_('chg_criteria')}</div>"
               f"<ul class=muted>{criteria}</ul>" if criteria else "")
            + "<form method=post action='/correct/change'>"
            # The whole plan travels in the form, including the founder's own
            # words: the confirmation arrives in a SECOND request, and by then
            # the draft is gone unless the page carried it.
            f"<input type=hidden name=plan value=\"{html.escape(payload, quote=True)}\">"
            f"<button class=primary type=submit>{_('btn_chg_go')}</button>"
            "</form>"
        )

    def _correction_result_page(
        request: Request, results: list
    ) -> HTMLResponse:
        """What each issue actually got. A redirect home was fine for one
        repair and a lie for three — it left the founder to infer from a
        log file whether the second and third had happened."""
        # dot colour, label class, string key — one row per status the
        # correction path can end in, so an unrecognised one still renders
        # as a plain red row rather than a KeyError on the results page.
        look = {
            "fixed": ("green", "ok", "cls_res_fixed"),
            "change_planned": ("amber", "warn", "cls_res_change_planned"),
            "scr_raised": ("amber", "warn", "cls_res_scr_raised"),
            "error": ("red", "warn", "cls_res_error"),
        }
        rows = ""
        for r in results:
            dot, klass, key = look.get(r.status, ("red", "warn", "cls_res_error"))
            rows += (
                "<div class=card>"
                "<div class=stateline style='margin-top:0'>"
                f"<span class='sdot {dot}'></span>"
                f"<span class='slabel {klass}'>{_(key)}</span></div>"
                + (f"<p><code>{html.escape(r.spec_slug)}</code></p>"
                   if r.spec_slug else "")
                + (_change_card(r) if r.status == "change_planned"
                   else f"<p class=muted>{html.escape(r.detail)}</p>")
                + "</div>"
            )
        return _render(
            request, _("title_cls_result"),
            f"<h1>{_('cls_result_head').format(n=len(results))}</h1>"
            + rows
            + f"<p><a href='/'>{_('link_back')}</a></p>",
            h1="",
        )

    def _spawn_add(fdr_path: Path) -> None:
        """Start `avs add <fdr> --yes` detached, the one way a feature-sized
        build is run.

        Shared by "add a feature" and "change a requirement" on purpose: a
        change IS a feature build, and giving it its own spawn would give it
        its own pid handling, its own log, and eventually its own bugs. The
        caller checks `_build_running` — two of these against one workspace
        corrupts it, which is the real reason for the guard now that cost is
        not one.
        """
        if spawn is not None:
            spawn(root)
            return
        (root / ".mas").mkdir(parents=True, exist_ok=True)
        log = (root / ".mas" / "build.log").open("ab")
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "ai_venture_studio.cli", "add", str(fdr_path),
             "--repo-dir", str(root), "--provider", provider, "--yes"],
            cwd=root, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        (root / ".mas" / "build.pid").write_text(str(proc.pid), encoding="utf-8")

    def _log_correction(complaint: str, result) -> None:
        path = root / "product" / "CORRECTION-LOG.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"- {result.status}: {complaint[:120]} → {result.detail}\n"
            )

    @app.post("/correct")
    async def correct(request: Request):
        """Classify FIRST, act only once the founder has confirmed.

        This used to run the whole correction on submit, so the founder
        learned that their bug report had been read as a scope change —
        their own SCR, approved in their name — only afterwards, from a log
        line.

        `spec_slug` arrives when they pressed "Change this" on a feature
        card instead of typing into the composer. It scopes the router to
        that one feature — the founder pointed at it, so which feature is
        no longer a guess.
        """
        if "correct" in thinking:
            return _thinking_page(request, thinking["correct"])
        form = await request.form()
        complaint = str(form.get("complaint", "")).strip()
        criterion = str(form.get("criterion", "")).strip()
        slug = str(form.get("spec_slug", "")).strip()
        if not complaint:
            return RedirectResponse("/", status_code=303)
        if slug:
            # Same segment rule as everywhere else a form supplies a name
            # that becomes a path under specs/.
            if not _REVIEW_ID.match(slug):
                return RedirectResponse("/", status_code=303)
            if not (root / "specs" / slug / "spec.yaml").is_file():
                return _no_such_page(request, "title_no_spec", slug)
        from starlette.concurrency import run_in_threadpool

        from ai_venture_studio.upstream import correction as correction_mod

        thinking["correct"] = _("working_correct")
        try:
            routes = await run_in_threadpool(
                lambda: correction_mod.route_complaint(
                    root, complaint, provider=provider, criterion=criterion,
                    only_slug=slug,
                )
            )
        except correction_mod.CorrectionRouteError as exc:
            # Not a crash: the workspace has nothing built, or the router
            # would not commit. Say which, rather than a stack trace.
            return _render(
                request, _("title_classify"),
                f"<div class=card><b class=warn>{_('cls_cannot_route')}</b>"
                f"<p class=muted>{html.escape(str(exc))}</p></div>"
                f"<p><a href='/'>{_('link_back')}</a></p>",
            )
        except Exception as exc:  # noqa: BLE001 — a page, never a 500
            return _failure_page(request, exc)
        finally:
            thinking.pop("correct", None)
        return _classification_page(request, complaint, criterion, routes)

    @app.post("/correct/confirm")
    async def correct_confirm(request: Request):
        """Execute the classification the founder just read."""
        if "correct" in thinking:
            return _thinking_page(request, thinking["correct"])
        from ai_venture_studio.upstream.correction import (
            CorrectionRoute,
            run_corrections,
        )

        form = await request.form()
        complaint = str(form.get("complaint", "")).strip()
        criterion = str(form.get("criterion", "")).strip()
        # The classification page sends one hidden field per issue, in the
        # order it showed them, so these lists are parallel by position.
        slugs = [str(s) for s in form.getlist("spec_slug")]
        kinds = [str(k) for k in form.getlist("kind")]
        quotes = [str(q) for q in form.getlist("quote")]
        instructions = [str(i) for i in form.getlist("instruction")]
        if not complaint or not slugs or len(kinds) != len(slugs):
            return RedirectResponse("/", status_code=303)
        # Which issues the founder left ticked. A page with a single issue
        # renders no checkbox at all, so "no boxes sent" means all of them
        # — but an explicit tick of none is an explicit nothing to do.
        if "include" in form:
            chosen = {
                int(v) for v in form.getlist("include")
                if str(v).isdigit() and int(v) < len(slugs)
            }
        else:
            chosen = set(range(len(slugs)))
        routes = []
        for i, slug in enumerate(slugs):
            if i not in chosen:
                continue
            kind = kinds[i]
            # `spec_slug` becomes a directory name under specs/, so it gets
            # the same segment rule as every other identifier arriving from
            # a form — and `kind` decides between two very different
            # actions, so it is checked against the two it is allowed to be.
            if not _REVIEW_ID.match(slug) or kind not in ("fix", "scope_change"):
                return RedirectResponse("/", status_code=303)
            if not (root / "specs" / slug / "spec.yaml").is_file():
                return _no_such_page(request, "title_no_spec", slug)
            routes.append(CorrectionRoute(
                spec_slug=slug, kind=kind,
                quote=quotes[i] if i < len(quotes) else "",
                instruction=(
                    instructions[i] if i < len(instructions) else ""
                ) or complaint,
            ))
        if not routes:
            return RedirectResponse("/", status_code=303)
        from starlette.concurrency import run_in_threadpool

        thinking["correct"] = _("working_correct")
        try:
            results = await run_in_threadpool(
                lambda: run_corrections(
                    root, complaint, provider=provider,
                    criterion=criterion, routes=routes,
                )
            )
        except Exception as exc:  # noqa: BLE001 — a page, never a 500
            return _failure_page(request, exc)
        finally:
            thinking.pop("correct", None)
        for route, result in zip(routes, results):
            _log_correction(route.words(complaint), result)
        return _correction_result_page(request, results)

    @app.post("/correct/change")
    async def correct_change(request: Request):
        """The founder read the change and pressed go. Build it.

        This is the button that used to be a sentence: `re-run \\`avs add\\`
        for 'cart' to regenerate`, printed to someone who had never opened a
        terminal, under a green tick claiming their change was handled.

        The plan travels back in the form rather than being re-drafted here,
        for the same reason `/correct/confirm` carries its routes: what the
        founder read is what runs. A second model call could quietly plan
        something else and build that instead.
        """
        from pydantic import ValidationError

        from ai_venture_studio.upstream.correction import ChangePlan, apply_change

        form = await request.form()
        try:
            plan = ChangePlan.model_validate(json.loads(str(form.get("plan", ""))))
        except (ValueError, ValidationError):
            return RedirectResponse("/", status_code=303)
        # `spec_slug` reaches the filesystem, and `criteria` becomes the
        # acceptance contract — an empty one would ratify a spec that
        # promises nothing, which every later gate would happily pass.
        if not _REVIEW_ID.match(plan.spec_slug) or not plan.criteria:
            return RedirectResponse("/", status_code=303)
        if not (root / "specs" / plan.spec_slug / "spec.yaml").is_file():
            return _no_such_page(request, "title_no_spec", plan.spec_slug)
        # Refuse BEFORE authorizing anything. Applying now would approve an
        # SCR and park an amendment for a build that is not going to start,
        # leaving a grant banked against a change nobody built.
        if _build_running(root):
            return _render(
                request, _("title_chg"),
                f"<div class=card><b class=warn>{_('chg_busy')}</b>"
                f"<p class=muted>{_('chg_busy_hint')}</p></div>"
                f"<p><a href='/'>{_('link_back')}</a></p>",
                h1="",
            )
        _spawn_add(apply_change(root, plan))
        return RedirectResponse("/", status_code=303)

    def _no_such_page(request: Request, title_key: str, what: str) -> HTMLResponse:
        """A named thing the workspace does not have. Redirecting home would
        read as "my click did nothing" — the exact failure the in-flight
        guard exists to prevent — so the refusal says which name missed."""
        return _render(
            request, _(title_key),
            f"<div class=card><b class=warn>{_('no_such_lead')}</b>"
            f"<p>{_(title_key + '_missing').format(name=html.escape(what))}</p>"
            f"</div><p><a href='/'>{_('link_back')}</a></p>",
        )

    @app.post("/retry")
    async def retry(request: Request):
        form = await request.form()
        task_id = str(form.get("task_id", ""))
        # Same rule the review and incident ids already follow: a task id is
        # a path segment and an argv word, so anything else is an attempt,
        # not a typo. And an id that is well-formed but not in the plan used
        # to spawn a worker that died on arrival — leaving a pid file, so the
        # Studio showed a build in progress that could never finish.
        if task_id and not _REVIEW_ID.match(task_id):
            return RedirectResponse("/", status_code=303)
        if task_id and task_id not in {t["id"] for t in _task_states(root)}:
            return _no_such_page(request, "title_no_task", task_id)
        if task_id and not _build_running(root):
            # Same rules as the build worker, which this path had quietly
            # regressed on: the retry inherits the Studio's provider (a mock
            # Studio used to spawn a retry that wanted a real key and died),
            # and its output lands in .mas/build.log rather than DEVNULL — a
            # worker that dies before the report must leave forensics.
            (root / ".mas").mkdir(exist_ok=True)
            log = (root / ".mas" / "build.log").open("ab")
            proc = subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "ai_venture_studio.cli", "retry-task", task_id,
                 "--repo-dir", str(root), "--provider", provider],
                cwd=root, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            (root / ".mas" / "build.pid").write_text(str(proc.pid), encoding="utf-8")
        return RedirectResponse("/", status_code=303)

    @app.post("/undo")
    def undo():
        from ai_venture_studio.upstream.autopilot import undo_last

        undo_last(root)
        return RedirectResponse("/", status_code=303)

    @app.post("/feature")
    async def feature(request: Request):
        if "feature" in thinking:
            return _thinking_page(request, thinking["feature"])
        form = await request.form()
        fdr_text = str(form.get("fdr", "")).strip()
        if fdr_text:
            fdr_path = root / ".mas" / "pending-feature.md"
            fdr_path.write_text(fdr_text, encoding="utf-8")
            from starlette.concurrency import run_in_threadpool

            from ai_venture_studio.upstream.autopilot import run_feature

            thinking["feature"] = _("working_feature")
            try:
                await run_in_threadpool(
                    run_feature, root, fdr_path, provider=provider, yes=False
                )
            except Exception as exc:  # noqa: BLE001 — a page, never a 500
                return _failure_page(request, exc)
            finally:
                thinking.pop("feature", None)
        return RedirectResponse("/", status_code=303)

    @app.post("/feature/build")
    async def feature_build(request: Request):
        form = await request.form()
        slug = str(form.get("slug", ""))
        # `slug` reaches the filesystem AND an argv, so it gets the same
        # segment rule as the review and incident ids: without it,
        # `../../..` walked the build out of the workspace entirely.
        if not _REVIEW_ID.match(slug):
            return RedirectResponse("/", status_code=303)
        feature_dir = root / "product" / "features" / slug
        if not feature_dir.is_dir():
            return _no_such_page(request, "title_no_feature", slug)
        if not _build_running(root):
            _spawn_add(feature_dir / "fdr.md")
        return RedirectResponse("/", status_code=303)

    @app.post("/build")
    def build():
        if not _build_running(root):
            _spawn_build()
        return RedirectResponse("/", status_code=303)

    @app.get("/live", response_class=HTMLResponse)
    def live(request: Request):
        from ai_venture_studio.studio_live import live_body

        return _render(request, _("title_live"), live_body(root, _, _profile(root)))

    @app.post("/live/guide")
    def live_guide():
        from ai_venture_studio.upstream.provisioning import write_cloud_guide

        write_cloud_guide(root, _profile(root))
        return RedirectResponse("/live", status_code=303)

    @app.post("/live/sweep")
    async def live_sweep(request: Request):
        from starlette.concurrency import run_in_threadpool

        from ai_venture_studio.studio_live import run_housekeeping

        try:
            await run_in_threadpool(run_housekeeping, root)
        except Exception as exc:  # noqa: BLE001 — a page, never a 500
            # A bodyless POST is safe to re-issue, so the retry button
            # belongs on the page that names the failure.
            return _failure_page(request, exc, retry_action="/live/sweep")
        return RedirectResponse("/live", status_code=303)

    @app.post("/review/{review_id}/evidence")
    def review_evidence(request: Request, review_id: str):
        """The Gate-R artifact, one click from the review it attests. Same
        export as `avs evidence-bundle`; a human still attaches it to the
        CAB submission — the Studio never submits anything anywhere."""
        if not _REVIEW_ID.match(review_id):
            return RedirectResponse("/", status_code=303)
        from ai_venture_studio.adoption import write_evidence_bundle

        try:
            path = write_evidence_bundle(str(root), review_id)
        except FileNotFoundError as exc:
            return _render(
                request, _("title_evidence"),
                f"<div class=card><p class=bad>{html.escape(str(exc))}</p>"
                f"</div><p><a href='/'>{_('link_back')}</a></p>",
            )
        return _render(
            request, _("title_evidence"),
            f"<div class=card><b>{_('evidence_written')}</b>"
            f"<p><code>{html.escape(str(path))}</code></p>"
            f"<p class=muted>{_('evidence_note')}</p></div>"
            f"<p><a href='/review/{html.escape(review_id)}'>"
            f"{_('link_back')}</a></p>",
        )

    @app.post("/live/probe")
    async def live_probe(request: Request):
        # In the threadpool, not the event loop: a slow (or self-referential)
        # URL must never freeze every other Studio page for 8 seconds.
        from starlette.concurrency import run_in_threadpool

        from ai_venture_studio.studio_live import probe_live

        form = await request.form()
        await run_in_threadpool(probe_live, root, str(form.get("url", "")))
        return RedirectResponse("/live", status_code=303)

    @app.post("/incident")
    async def incident(request: Request):
        """It's broken → the real triage MAS. Same Incident model and
        artifacts as `avs triage`; only the front door is a textarea."""
        from starlette.concurrency import run_in_threadpool

        from ai_venture_studio.adoption import StageInactiveError, check_stage
        from ai_venture_studio.studio_live import incident_body, incident_intake

        form = await request.form()
        description = str(form.get("description", "")).strip()
        if not description:
            return RedirectResponse("/", status_code=303)
        try:
            check_stage(str(root), "maintenance")
        except StageInactiveError as exc:
            return _render(
                request, _("title_incident"),
                f"<div class=card><p class=bad>{html.escape(str(exc))}</p>"
                f"</div><p><a href='/'>{_('link_back')}</a></p>",
            )
        try:
            incident_obj, result = await run_in_threadpool(
                incident_intake, root, description, provider
            )
        except Exception as exc:  # noqa: BLE001 — a page, never a 500
            return _failure_page(request, exc)
        return _render(
            request, _("title_incident"), incident_body(_, incident_obj.id, result)
        )

    @app.post("/incident/fix")
    async def incident_fix(request: Request):
        from starlette.concurrency import run_in_threadpool

        from ai_venture_studio.studio_live import attempt_incident_fix, fix_body

        form = await request.form()
        incident_id = str(form.get("incident_id", ""))
        # Same shape rule as review ids — the id becomes a path segment.
        if not _REVIEW_ID.match(incident_id):
            return RedirectResponse("/", status_code=303)
        try:
            attempt = await run_in_threadpool(
                attempt_incident_fix, root, incident_id, provider
            )
        except Exception as exc:  # noqa: BLE001 — a page, never a 500
            return _failure_page(request, exc)
        return _render(request, _("title_fix"), fix_body(_, attempt))

    @app.post("/reset")
    def reset():
        for stale in (
            "product/CONFIRMATION.md",
            "product/BUILD-REPORT.md",
            "FDR-QUESTIONS.md",
            ".mas/build.pid",  # else an interrupted build's marker loops the page
        ):
            (root / stale).unlink(missing_ok=True)
        return RedirectResponse("/", status_code=303)

    @app.get("/status")
    def status():
        return JSONResponse(_progress(root))

    return app


def serve_studio(repo_dir: str | Path, host: str = "127.0.0.1", port: int = 8433,
                 *, provider: str = "anthropic",
                 lang: str = DEFAULT_LANGUAGE, mode: str | None = None,
                 entry: str = "chat") -> None:
    import uvicorn

    uvicorn.run(
        create_studio_app(
            repo_dir, provider=provider, lang=lang, mode=mode, entry=entry
        ),
        host=host, port=port, log_level="warning",
    )
