"""The constitution (ADR-047) — what this product has decided NOT to do.

The FDR template has always had a section 4, "暂时不要的功能 / NOT needed for
now", and the founder has always filled it in. Nothing has ever read it.
It reached the planner as part of the FDR blob on the FIRST build and then
vanished: `avs add` plans a feature against the code, the ledger and the
reconciler, and none of those know that in February the founder wrote "no
online payments yet". So the tenth feature quietly grows a checkout.

An invariant is not a requirement. A requirement is a promise the product
KEEPS and a test proves it; an invariant is a boundary the product does
not cross, and no test can prove a thing was not built. They therefore get
their own file, their own ids (`C-001`), and their own rendering.

Three properties, mirroring `requirements.py` because the failure modes
are the same ones:

1. **Derived, never typed.** `sync_constitution` reads the §4 of an FDR the
   founder already wrote. The plan's rule was "never a separate typing
   chore", and a constitution the founder must maintain by hand is exactly
   the chore, with the added cost that it goes stale silently.

2. **Append-only, per origin.** Ids come from `max(ever seen) + 1`. An
   invariant whose FDR no longer lists it is `withdrawn`, not deleted —
   the founder changed their mind, and that is a fact worth keeping.
   Reconciliation happens per ORIGIN: a feature FDR's §4 can only add to
   or withdraw from its OWN lines, never silently repeal what the founding
   document said.

3. **Shown whole, and it says when it is not.** Unlike the requirement
   ledger there is no retrieval step: a "do not build this" list is short
   by construction and every line of it applies to every plan, so slicing
   it by keyword overlap would hide the invariant a request is about to
   violate — precisely the one that mattered. There is still a cap,
   because "short by construction" is an expectation and not a guarantee,
   and a cap that drops work says so (ADR-039).

The constitution is deliberately NOT a gate. It is rendered into the
planner's prompt and nothing refuses a build over it. `reconcile.py`
already carries this product's one refusal, it is measured, and a second
stop-the-build path built on lines a founder typed in prose — with no
model call to judge what they meant — would refuse correct work far more
often than it caught a real overreach. A boundary the planner is TOLD
about is the whole win here; the founder's newest request always wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

#: Statuses an invariant can hold. There is no "retired": an invariant is
#: withdrawn by the founder deleting it from the document they wrote it
#: in, which is a decision, not a derivation about code.
STATUSES = ("live", "withdrawn")

_ID = re.compile(r"^C-(\d+)$")

#: A heading opening section 4 of the FDR, in either language and in both
#: the template's and the Studio composer's wording. Matched on the number
#: rather than the words: `fdr.TEMPLATE`, `fdr.TEMPLATE_EN` and
#: `studio_chat._HEADINGS` all write "## 4." and only the text after it
#: differs, so keying on the number survives a rewording of the prose.
_SECTION_4 = re.compile(r"^\s*#{1,6}\s*4[.、)]\s")
_ANY_HEADING = re.compile(r"^\s*#{1,6}\s")

#: Bullet and numbering marks stripped off the front of a line. The
#: founder's own words are what land in the file; "- " is not their word.
_BULLET = re.compile(r"^\s*(?:[-*+•·]|\d+[.)、])\s*")

#: The TEMPLATE talking, not the founder: section 4 of both templates ships
#: a parenthetical example, and `TEMPLATE_EN`'s wraps across two lines. A
#: guidance line that became an invariant would put "e.g. no logins yet"
#: into the constitution of every product built from the template. Matched
#: only when the line OPENS with the bracket, so a founder writing "no
#: payments (yet)" keeps their sentence.
_OPENS_GUIDANCE = ("（", "(")
_BRACKETS = {"（": "）", "(": ")"}

#: "there are none", in the words the templates suggest for it.
_NOTHING = {"无", "没有", "none", "n/a", "na", "-", "—", "n/a."}

#: How many invariants a planner is shown. See the module docstring: the
#: cap exists so the prompt cannot grow without bound, not because slicing
#: is desirable, and the render names what it dropped.
RENDER_CAP = 20


class Invariant(BaseModel):
    id: str
    text: str
    origin: str = Field(
        description="the FDR that said it, relative to the workspace. Unlike "
        "a requirement's origin this is never empty: an invariant is only "
        "ever created by reading a document, so the document is known."
    )
    status: str = "live"
    withdrawn_note: str = Field(
        default="",
        description="why it stopped applying. Set when the line left its "
        "own FDR's section 4 — the founder deleted it, and that is the only "
        "way an invariant is withdrawn today.",
    )


@dataclass
class ConstitutionSync:
    """What one derivation changed, so a caller can report it rather than
    diffing the file to find out."""

    added: list[str] = field(default_factory=list)
    withdrawn: list[str] = field(default_factory=list)
    total: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.withdrawn)


def constitution_path(repo_dir: str | Path) -> Path:
    return Path(repo_dir) / "product" / "constitution.yaml"


def load_constitution(repo_dir: str | Path) -> list[Invariant]:
    path = constitution_path(repo_dir)
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [Invariant.model_validate(entry) for entry in raw]


def save_constitution(repo_dir: str | Path, invariants: list[Invariant]) -> Path:
    path = constitution_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            [inv.model_dump() for inv in invariants], sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def _highest_id(invariants: list[Invariant]) -> int:
    best = 0
    for inv in invariants:
        match = _ID.match(inv.id)
        if match:
            best = max(best, int(match.group(1)))
    return best


def not_needed_lines(fdr_text: str) -> list[str]:
    """The founder's "not for now" lines, from section 4 of an FDR.

    Deterministic on purpose — no model call. A model asked to summarise
    this section can invent a boundary the founder never drew, and an
    invented invariant is worse than a missing one: it is shown to every
    future plan as something the founder decided. `compose_fdr` already
    sets the precedent that the founder's words enter the document as they
    typed them, and this reads them back out the same way.
    """
    lines: list[str] = []
    inside = False
    awaiting_close = ""
    for raw in fdr_text.splitlines():
        if _SECTION_4.match(raw):
            inside = True
            continue
        if inside and _ANY_HEADING.match(raw):
            break
        if not inside:
            continue
        text = _BULLET.sub("", raw).strip()
        if awaiting_close:
            if awaiting_close in text:
                awaiting_close = ""
            continue
        if not text or text.startswith(">"):
            continue
        if text[0] in _OPENS_GUIDANCE:
            close = _BRACKETS[text[0]]
            if close not in text[1:]:
                awaiting_close = close
            continue
        if text.strip(" .。!！").lower() in _NOTHING:
            continue
        if text not in lines:
            lines.append(text)
    return lines


def sync_constitution(
    repo_dir: str | Path, fdr_text: str, origin: str
) -> ConstitutionSync:
    """Bring the constitution level with one FDR's section 4.

    Scoped to `origin`: this reconciles only the invariants that came from
    THIS document. A feature FDR that says nothing about payments must not
    withdraw the founding document's "no online payments" line just by
    failing to repeat it — a derivation that reads silence as repeal would
    empty the constitution on the first feature.

    Idempotent, so it can run on every build without the file churning.
    """
    root = Path(repo_dir)
    existing = load_constitution(root)
    wanted = not_needed_lines(fdr_text)
    mine = {inv.text: inv for inv in existing if inv.origin == origin}
    sync = ConstitutionSync()
    next_id = _highest_id(existing) + 1

    for text in wanted:
        inv = mine.get(text)
        if inv is None:
            inv = Invariant(id=f"C-{next_id:03d}", text=text, origin=origin)
            next_id += 1
            existing.append(inv)
            sync.added.append(inv.id)
        elif inv.status != "live":
            # Written again after being withdrawn: the founder put it back,
            # and the document is what the constitution follows.
            inv.status = "live"
            inv.withdrawn_note = ""
            sync.added.append(inv.id)

    for inv in existing:
        if inv.origin != origin or inv.status != "live" or inv.text in wanted:
            continue
        inv.status = "withdrawn"
        inv.withdrawn_note = f"no longer listed in {origin}"
        sync.withdrawn.append(inv.id)

    sync.total = sum(1 for inv in existing if inv.status == "live")
    if sync.changed or not constitution_path(root).exists():
        save_constitution(root, existing)
    return sync


def live(repo_dir: str | Path) -> list[Invariant]:
    return [inv for inv in load_constitution(repo_dir) if inv.status == "live"]


def render_for_planner(invariants: list[Invariant], *, cap: int = RENDER_CAP) -> str:
    """What every planner is told the product has decided not to do."""
    if not invariants:
        return "(the founder has not ruled anything out)"
    shown = invariants[:cap]
    lines = [f"{inv.id} {inv.text}" for inv in shown]
    if len(invariants) > len(shown):
        lines.append(
            f"({len(shown)} of {len(invariants)} shown — "
            f"{len(invariants) - len(shown)} more were not, and they still apply)"
        )
    return "\n".join(lines)
