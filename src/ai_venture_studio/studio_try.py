"""Try it — the built product beside its own acceptance list.

The report page tells a founder what was built. It does not help them find
out whether it is right, which is the only question they actually have. So:
the product on the left, the acceptance criteria on the right, and one
honest verb per row — Fine, or Wrong.

Three rules this surface keeps:

- **Never fake a running product.** If the Studio cannot start it safely,
  the page shows the command that does and the screenshots the build
  already took. A fake frame would be the worst lie in the product.
- **Ticks are founder input, not a verdict.** They persist in
  product/try-checks.yaml — the same workspace the CLI owns, no second
  source of truth — and they change nothing about the product. That
  sentence is on the page, not only in this docstring.
- **A complaint carries its criterion.** "Wrong" sends the row text along
  with the founder's words, so the correction router sees which criterion
  failed instead of guessing which feature is meant.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel

TICKS_FILE = "try-checks.yaml"

#: `- [ ] step` / `1. [x] step` — the acceptance walkthrough's own shape.
_CHECKBOX = re.compile(r"^\s*(?:[-*]|\d+\.)\s*\[([ xX])\]\s*(.+?)\s*$")
#: `- ✅ probe — detail` — the shape verify_product writes.
_MARKED = re.compile(r"^\s*[-*]\s*(✅|❌|⚠️)\s*(.+?)\s*$")


class Row(BaseModel):
    """One checkable line, derived from an artifact the build already
    wrote. `id` is content-addressed: a re-run that keeps a criterion keeps
    its tick, and one that rewords it gets a fresh, untouched row rather
    than inheriting a tick for words nobody read."""

    id: str
    text: str
    source: str          # acceptance | verification
    mark: str = ""       # the machine's own ✅/❌/⚠️, where there is one


def _row_id(source: str, text: str) -> str:
    return hashlib.sha256(f"{source}:{text}".encode()).hexdigest()[:12]


def _rows_from(path: Path, source: str) -> list[Row]:
    if not path.exists():
        return []
    rows: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        checkbox = _CHECKBOX.match(line)
        marked = _MARKED.match(line)
        if checkbox:
            text, mark = checkbox.group(2), ""
        elif marked:
            text, mark = marked.group(2), marked.group(1)
        else:
            continue
        if not text.strip():
            continue
        rows.append(
            Row(id=_row_id(source, text), text=text, source=source, mark=mark)
        )
    return rows


def acceptance_rows(root: Path) -> list[Row]:
    """Every checkable row the workspace already has. Derived, never
    authored here: the acceptance walkthrough and the verification report
    are written by the build, and this only reads them."""
    product = Path(root) / "product"
    return (
        _rows_from(product / "ACCEPTANCE.md", "acceptance")
        + _rows_from(product / "VERIFICATION.md", "verification")
    )


def load_ticks(root: Path) -> dict[str, dict]:
    path = Path(root) / "product" / TICKS_FILE
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    checked = data.get("checked")
    return checked if isinstance(checked, dict) else {}


def set_tick(root: Path, row: Row, *, on: bool) -> None:
    """Record (or clear) one founder tick.

    Deliberately stores the row TEXT beside the id: the file has to be
    readable by the person whose workspace it is, and a list of hashes is
    not a record of anything.
    """
    ticks = load_ticks(root)
    if on:
        ticks[row.id] = {
            "row": row.text,
            "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        }
    else:
        ticks.pop(row.id, None)
    path = Path(root) / "product" / TICKS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# The founder's own ticks from the Studio's Try-it page.\n"
        "# NOT a verdict about the product: a tick records that a person\n"
        "# looked at one row and was happy with it. Nothing in the product\n"
        "# changes until they press the fix button.\n"
        + yaml.safe_dump({"checked": ticks}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def screenshot_note(root: Path) -> str:
    """Why there are no pictures, when there are none.

    `capture` already explains itself — playwright missing, no runnable
    entry, the server never listened — and `screenshots.yaml` already
    stores that sentence. Nothing read it, so a founder whose product
    could not be photographed saw an empty space and concluded the
    Studio was broken, or worse, saw the PREVIOUS build's pictures with
    nothing to say they were old.
    """
    path = Path(root) / "product" / "screenshots.yaml"
    if not path.exists():
        return ""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return ""
    return str(data.get("note", "")) if isinstance(data, dict) else ""


def preview_plan(root: Path, profile: str) -> tuple[str, str]:
    """(how to run it, which entry point) — exactly what `avs preview`
    would do, named rather than performed.

    The Studio does not start the product itself: `avs preview` runs it in
    the foreground on a port of its own, and a web server spawned by a page
    load is a process nobody owns, on a port nobody chose, with a lifetime
    nobody controls. Naming the command is honest; a frame pretending to
    hold a running product would not be.
    """
    from ai_venture_studio.upstream.provisioning import preview_entry

    root = Path(root)
    if profile == "miniprogram":
        return ("", "miniprogram")
    entry = preview_entry(root)
    return (f"avs preview --repo-dir {root}", entry) if entry else ("", "")


def try_body(
    root: Path, t_: Callable[[str], str], profile: str, *,
    correct_action: str = "/correct",
) -> str:
    root = Path(root)
    rows = acceptance_rows(root)
    ticks = load_ticks(root)

    # ── left: how to run it, and what it looked like when it was built.
    command, entry = preview_plan(root, profile)
    if entry == "miniprogram":
        run = (
            f"<p>{t_('try_run_miniprogram')}</p>"
            f"<pre>{html.escape(str(root))}</pre>"
        )
    elif command:
        run = (
            f"<p>{t_('try_run_hint')}</p><pre>{html.escape(command)}</pre>"
            f"<p class=muted>{t_('try_run_entry_fmt').format(entry=html.escape(entry))}</p>"
        )
    else:
        run = f"<p class=muted>{t_('try_run_none')}</p>"
    shots = sorted((root / "product" / "screenshots").glob("*.png")) if (
        root / "product" / "screenshots"
    ).is_dir() else []
    gallery = "".join(
        f"<img class=shot src='/shots/{html.escape(shot.name)}'>" for shot in shots
    )
    note = screenshot_note(root)
    left = (
        f"<div class=colcard><div class=collab>{t_('try_left_head')}</div>"
        f"{run}"
        + (f"<div class=lbl>{t_('h_screenshots')}</div>{gallery}"
           if gallery else f"<p class=muted>{t_('try_no_shots')}</p>")
        + (f"<p class=muted>{html.escape(note)}</p>" if note and not gallery else "")
        + "</div>"
    )

    # ── right: the criteria, one honest verb each.
    if rows:
        checked = sum(1 for row in rows if row.id in ticks)
        row_html = "".join(_row_html(row, row.id in ticks, t_, correct_action)
                           for row in rows)
        right = (
            f"<div class=colcard><div class=collab>{t_('try_right_head')}</div>"
            f"<p class=muted>{t_('try_ticks_fmt').format(done=checked, total=len(rows))}</p>"
            f"<p class=muted>{t_('try_not_a_verdict')}</p>"
            f"{row_html}</div>"
        )
    else:
        right = (
            f"<div class=colcard><div class=collab>{t_('try_right_head')}</div>"
            f"<p class=muted>{t_('try_no_rows')}</p></div>"
        )

    return (
        f"<p class=muted>{t_('try_lead')}</p>"
        f"<div class=twocol>{left}{right}</div>"
        f"<p><a href='/'>{t_('link_back')}</a></p>"
    )


def _row_html(
    row: Row, ticked: bool, t_: Callable[[str], str], correct_action: str
) -> str:
    # The automated mark belongs to the criterion, not to the founder's
    # tick, so it reads as a note under the text rather than as a second
    # status chip competing with the row's own one.
    mark = (
        f"<div class=muted>{html.escape(row.mark)} "
        f"{t_('try_mark_auto')}</div>" if row.mark else ""
    )
    if ticked:
        actions = (
            "<form method=post action=/try/tick>"
            f"<input type=hidden name=row value='{html.escape(row.id)}'>"
            "<input type=hidden name=off value=1>"
            f"<button class=linkish>{t_('btn_try_untick')}</button></form>"
        )
        chip = f"<span class='chip g'>{t_('try_chip_fine')}</span>"
    else:
        # Two real forms, no JavaScript: the tick, and the complaint that
        # travels WITH this row so the router knows which criterion failed.
        actions = (
            "<form method=post action=/try/tick>"
            f"<input type=hidden name=row value='{html.escape(row.id)}'>"
            f"<button class=linkish>{t_('btn_try_fine')}</button></form>"
            f"<details><summary class=muted>{t_('btn_try_wrong')}</summary>"
            f"<form method=post action={correct_action}>"
            f"<input type=hidden name=criterion value='{html.escape(row.text)}'>"
            f"<textarea name=complaint style='min-height:70px' "
            f"placeholder='{t_('try_wrong_placeholder')}'></textarea>"
            f"<p><button class=secondary>{t_('btn_try_send_wrong')}</button></p>"
            "</form></details>"
        )
        chip = f"<span class='chip q'>{t_('try_chip_open')}</span>"
    # The criterion, then its actions on their own line: inline forms sat
    # flush against the last word, so the row read as one run-on sentence
    # ("…and who it is for✓ Fine").
    return (
        f"<div class=trow>{chip}<div class=ttl>"
        f"<div>{html.escape(row.text)}</div>{mark}"
        f"<div class=tryacts>{actions}</div>"
        "</div></div>"
    )
