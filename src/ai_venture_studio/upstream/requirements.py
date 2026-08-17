"""The requirement ledger (ADR-045) — one stable name per promise.

Before this, a criterion's identity was its POSITION in a list: the fourth
string in `specs/order-flow/spec.yaml`. Inside one spec that is enough —
`TestSkeleton.covers` is a list of 0-based indices and it works. Outside
one spec it is nothing. You cannot say "this contradicts the thing you
asked for in February" when the thing has no name, and you cannot tell a
planner what the product already promises when the only cross-spec index
is a list of directory names.

The ledger is `product/requirements.yaml`: every acceptance criterion in
the workspace, each with an id that is allocated once and never reused.

Three properties it must have, all of them tested:

1. **Derived, never hand-maintained.** `sync_ledger` reads `specs/*` and
   brings the ledger up to date. A file a human has to remember to edit is
   a file that goes stale, and a stale requirements list is worse than no
   requirements list — it is a wrong answer with an authoritative shape.

2. **Append-only.** Ids are allocated from `max(seen) + 1` over every entry
   the file has EVER held, including retired ones. A criterion that leaves
   its spec is marked `retired` and keeps its text; it is never deleted and
   its id is never handed to something else.

3. **Keyed on text, not index.** A spec regenerated through the SCR channel
   can reorder its criteria. Matching on `(spec_slug, index)` would silently
   re-point R-007 at a different promise — the exact failure this file
   exists to prevent, committed by the fix for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# Statuses a requirement can hold. `retired` and `superseded` differ on
# purpose and the difference is load-bearing: DERIVATION may retire a
# criterion (it left its spec and nothing is known to have replaced it),
# but only an explicit reconciliation decision may supersede one, because
# supersession names a replacement and a derivation has no way to know it.
STATUSES = ("proposed", "approved", "built", "retired", "superseded")
LIVE_STATUSES = ("proposed", "approved", "built")

_ID = re.compile(r"^R-(\d+)$")

# A SECOND tokenizer, deliberately not `maintenance.correlate._tokens`.
# That one scores incident text against commits and stops words like
# "error" and "traceback". This one scores EARS criteria against each
# other, where the stopwords are the grammar itself — every criterion
# contains "shall", most contain "system", and a similarity score built on
# words that appear in all of them ranks nothing. Two jobs, two lists;
# collapsing them because both are called "tokenize" is how one of them
# quietly stops working (ADR-038).
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOPWORDS = {
    "shall", "the", "and", "for", "with", "that", "this", "when", "while",
    "where", "then", "system", "user", "users", "must", "will", "not",
    "are", "has", "have", "its", "any", "all", "each", "from", "into",
    "their", "they", "them", "than", "such", "only", "may", "can", "one",
}


# Chinese has no spaces, so `_TOKEN` — which requires an ASCII letter —
# found NOTHING in a Chinese criterion. Every score was zero, `relevant`
# returned an empty slice, and the ADR-046 gate therefore reported "no
# existing requirement matched this request" for every request in the
# language this product's founder actually writes in: the templates are
# bilingual, the Studio shipped Chinese by default, and every case in
# `benchmarks/products` is Chinese. The gate was not wrong, it was inert,
# and an inert gate is the hardest kind of broken to notice — it never
# fires and never errors.
#
# Character bigrams rather than a segmenter: no new runtime dependency, no
# dictionary to go stale, and the failure mode is symmetric noise (a bigram
# straddling two words scores equally against every candidate) instead of a
# missed match. Ranking is comparative and capped, so noise costs a little
# precision where the alternative cost everything.
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")
# The grammar itself, same role as `_STOPWORDS`: a bigram made only of
# these ranks nothing because it appears in every criterion.
_CJK_FUNCTION = set("的了是在和与及也都很就把被对从为以要能会有个之其所并且或者")


def _cjk_tokens(text: str) -> set[str]:
    found: set[str] = set()
    for run in _CJK.findall(text):
        if len(run) == 1:
            if run not in _CJK_FUNCTION:
                found.add(run)
            continue
        for index in range(len(run) - 1):
            gram = run[index : index + 2]
            if all(char in _CJK_FUNCTION for char in gram):
                continue
            found.add(gram)
    return found


def tokens(text: str) -> set[str]:
    """Content words of a requirement — see `_STOPWORDS` for why this is
    not the correlator's tokenizer, and `_CJK` for why a second rule is
    needed at all."""
    latin = {
        t.lower() for t in _TOKEN.findall(text) if t.lower() not in _STOPWORDS
    }
    return latin | _cjk_tokens(text)


class Requirement(BaseModel):
    id: str
    text: str
    spec_slug: str
    origin: str = Field(
        default="",
        description="where the founder asked for it — an FDR path, relative "
        "to the workspace. Empty for requirements derived before the ledger "
        "existed, which is a known gap rather than a guess.",
    )
    status: str = "proposed"
    superseded_by: str | None = Field(
        default=None,
        description="the ORIGIN of the replacement (an FDR path), not a "
        "requirement id — see `supersede` for why naming one of the "
        "replacement's several requirements would be a guess.",
    )
    verified_by: list[str] = Field(
        default_factory=list,
        description="test FILES the spec says cover this criterion. Files, "
        "not node ids: `TestSkeleton` carries a path and a purpose, and "
        "inventing a node id the runner never reported would be a "
        "traceability link to nothing.",
    )


@dataclass
class LedgerSync:
    """What one derivation changed. Returned so a caller can report it
    rather than having to diff the file to find out."""

    added: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    total: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.retired or self.updated)


@dataclass
class RequirementSlice:
    """The requirements shown to a planner, and an honest count of the ones
    that were not. A bound that drops work says so (ADR-039)."""

    shown: list[Requirement] = field(default_factory=list)
    matched: int = 0
    cap: int = 0

    @property
    def dropped(self) -> int:
        return max(0, self.matched - len(self.shown))


def ledger_path(repo_dir: str | Path) -> Path:
    return Path(repo_dir) / "product" / "requirements.yaml"


def load_ledger(repo_dir: str | Path) -> list[Requirement]:
    path = ledger_path(repo_dir)
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [Requirement.model_validate(r) for r in raw]


def save_ledger(repo_dir: str | Path, requirements: list[Requirement]) -> Path:
    path = ledger_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            [r.model_dump() for r in requirements], sort_keys=False, allow_unicode=True
        ),
        encoding="utf-8",
    )
    return path


def _highest_id(requirements: list[Requirement]) -> int:
    """The largest id the file has ever held. Retired and superseded entries
    count — that is what makes reuse impossible."""
    best = 0
    for req in requirements:
        match = _ID.match(req.id)
        if match:
            best = max(best, int(match.group(1)))
    return best


def _spec_files(repo_dir: str | Path) -> list[Path]:
    specs = Path(repo_dir) / "specs"
    if not specs.is_dir():
        return []
    return sorted(p / "spec.yaml" for p in sorted(specs.iterdir()) if (p / "spec.yaml").exists())


def _derived_status(spec_data: dict) -> str:
    if spec_data.get("built"):
        return "built"
    return "approved" if spec_data.get("status") == "approved" else "proposed"


def _spec_criteria(spec_data: dict) -> list[tuple[str, list[str]]]:
    """`(text, covering test files)` per criterion, deduplicated by text.

    A writer that states the same criterion twice has stated ONE promise,
    so it gets ONE id and the union of whatever covers either copy. The
    alternative — two ids with identical text — is a corpus that reports a
    duplicate to every future reconciliation, forever.
    """
    skeletons = spec_data.get("test_skeletons") or []
    order: list[str] = []
    covered: dict[str, list[str]] = {}
    for index, raw in enumerate(spec_data.get("criteria") or []):
        text = str(raw).strip()
        if not text:
            continue
        if text not in covered:
            order.append(text)
            covered[text] = []
        for skeleton in skeletons:
            if not isinstance(skeleton, dict):
                continue
            if index in (skeleton.get("covers") or []):
                path = str(skeleton.get("path", "")).strip()
                if path and path not in covered[text]:
                    covered[text].append(path)
    return [(text, covered[text]) for text in order]


def sync_ledger(repo_dir: str | Path) -> LedgerSync:
    """Bring `product/requirements.yaml` level with `specs/`.

    Idempotent: running it twice in a row changes nothing the second time,
    which is what lets it be called on every build without the file
    churning.

    Provenance is deliberately NOT a parameter here. This runs from inside
    the build, which knows the spec but not the FDR that asked for it, so
    a sync could only stamp origin by guessing that the feature currently
    in flight is responsible for every requirement it happens to observe.
    `attribute_origin` is the one way an origin is recorded, and it is
    called by the code that actually knows the answer.
    """
    root = Path(repo_dir)
    existing = load_ledger(root)
    by_key = {(r.spec_slug, r.text): r for r in existing}
    sync = LedgerSync()
    next_id = _highest_id(existing) + 1
    seen: set[tuple[str, str]] = set()
    result: list[Requirement] = []

    for spec_path in _spec_files(root):
        try:
            spec_data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            # An unreadable spec must not retire the requirements it holds:
            # they are not gone, they are unreadable, and retiring them
            # would record a decision nobody made.
            for req in existing:
                if req.spec_slug == spec_path.parent.name:
                    seen.add((req.spec_slug, req.text))
            continue
        slug = str(spec_data.get("slug") or spec_path.parent.name)
        status = _derived_status(spec_data)
        for text, verified_by in _spec_criteria(spec_data):
            key = (slug, text)
            seen.add(key)
            req = by_key.get(key)
            if req is None:
                req = Requirement(
                    id=f"R-{next_id:03d}", text=text, spec_slug=slug,
                    status=status, verified_by=verified_by,
                )
                next_id += 1
                sync.added.append(req.id)
                by_key[key] = req
                result.append(req)
                continue
            if req.status != status or req.verified_by != verified_by:
                # A retired or superseded requirement whose text reappears
                # in its spec is LIVE again; that is the spec speaking, and
                # the ledger follows the spec.
                req.status = status
                req.verified_by = verified_by
                sync.updated.append(req.id)
            result.append(req)

    for req in existing:
        if (req.spec_slug, req.text) in seen:
            continue
        if req.status in LIVE_STATUSES:
            req.status = "retired"
            sync.retired.append(req.id)
        result.append(req)

    result.sort(key=lambda r: int(_ID.match(r.id).group(1)) if _ID.match(r.id) else 0)
    sync.total = len(result)
    if sync.changed or not ledger_path(root).exists():
        save_ledger(root, result)
    return sync


def attribute_origin(
    repo_dir: str | Path, known_ids: set[str], origin: str
) -> list[str]:
    """Record which FDR asked for the requirements that appeared since
    `known_ids` was taken.

    The caller snapshots the ledger's ids before it starts building and
    passes them back afterwards; anything new in between was asked for by
    the FDR that ran. An origin already recorded is never overwritten — the
    FDR that FIRST asked for a promise is the answer, and a later feature
    touching the same spec did not ask for it.
    """
    ledger = load_ledger(repo_dir)
    stamped = [
        req.id for req in ledger if req.id not in known_ids and not req.origin
    ]
    if not stamped:
        return []
    for req in ledger:
        if req.id in stamped:
            req.origin = origin
    save_ledger(repo_dir, ledger)
    return stamped


def supersede(
    repo_dir: str | Path, ids: list[str], replacement_origin: str
) -> list[str]:
    """Retire promises the founder agreed to replace (ADR-046).

    `superseded_by` holds the ORIGIN of the replacement — the FDR that took
    over — not a requirement id. A feature that supersedes one promise
    usually adds several, and picking one of them to name would be a guess
    presented as a record. The FDR is the thing the founder actually
    decided about.

    Only live requirements move: superseding an already-retired one would
    rewrite a history nobody changed.
    """
    ledger = load_ledger(repo_dir)
    wanted = set(ids)
    moved = []
    for req in ledger:
        if req.id in wanted and req.status in LIVE_STATUSES:
            req.status = "superseded"
            req.superseded_by = replacement_origin
            moved.append(req.id)
    if moved:
        save_ledger(repo_dir, ledger)
    return moved


def relevant(
    repo_dir: str | Path,
    text: str,
    *,
    cap: int = 12,
    radius: list[str] | None = None,
) -> RequirementSlice:
    """The live requirements a new request plausibly touches.

    Deterministic on purpose: content-word overlap, plus a bonus when the
    request's blast radius names a file that verifies the requirement. No
    model call and no embedding store — a retrieval step that costs a
    round-trip gets skipped the first time someone is in a hurry.
    """
    ledger = [r for r in load_ledger(repo_dir) if r.status in LIVE_STATUSES]
    wanted = tokens(text)
    radius_set = set(radius or [])
    scored: list[tuple[int, str, Requirement]] = []
    for req in ledger:
        score = len(tokens(req.text) & wanted)
        if radius_set and any(path in radius_set for path in req.verified_by):
            score += 2
        if score:
            scored.append((score, req.id, req))
    # Highest score first; ties broken by id so the same corpus and the same
    # request always produce the same slice.
    scored.sort(key=lambda row: (-row[0], row[1]))
    return RequirementSlice(
        shown=[req for _, _, req in scored[:cap]], matched=len(scored), cap=cap
    )


@dataclass
class Since:
    """What changed between a checkpoint and now (ADR-048)."""

    tag: str
    added: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.superseded or self.retired or self.restored)


def baseline_path(repo_dir: str | Path, tag: str) -> Path:
    return Path(repo_dir) / "product" / "baselines" / f"{tag}.yaml"


def write_baseline(repo_dir: str | Path, tag: str) -> Path:
    """Freeze what the product promised at a checkpoint.

    The stored value is the id→status MAP, not a hash of the ledger. A hash
    is cheaper and can answer exactly one question — "did anything change?"
    — and the question a founder asks is the other one: *what* changed, and
    is anything I used to have gone. Six ids and a status each is a few
    hundred bytes; buying "+6 requirements, 1 superseded" for that is not a
    trade worth thinking about twice.
    """
    path = baseline_path(repo_dir, tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {req.id: req.status for req in load_ledger(repo_dir)},
            sort_keys=True, allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def load_baseline(repo_dir: str | Path, tag: str) -> dict[str, str] | None:
    path = baseline_path(repo_dir, tag)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in data.items()}


def since(repo_dir: str | Path, tag: str) -> Since | None:
    """The delta from a checkpoint's baseline to the ledger as it stands.

    Returns None when that checkpoint has no baseline — every checkpoint
    tagged before this file existed is one, and reporting "+0 since
    checkpoint 2" for a checkpoint nobody recorded would be a measurement
    of the missing file rather than of the product.
    """
    before = load_baseline(repo_dir, tag)
    if before is None:
        return None
    result = Since(tag=tag)
    for req in load_ledger(repo_dir):
        was = before.get(req.id)
        if was is None:
            if req.status in LIVE_STATUSES:
                result.added.append(req.id)
            continue
        if was == req.status:
            continue
        if req.status == "superseded":
            result.superseded.append(req.id)
        elif req.status == "retired":
            result.retired.append(req.id)
        elif was not in LIVE_STATUSES:
            result.restored.append(req.id)
    return result


def render_slice(slice_: RequirementSlice) -> str:
    """The planner-facing text. Says what it dropped."""
    if not slice_.shown:
        return "(no existing requirement matched this request)"
    lines = []
    for req in slice_.shown:
        verified = (
            " · verified by " + ", ".join(req.verified_by[:2]) if req.verified_by else ""
        )
        lines.append(
            f"{req.id} [{req.status}] {req.text}\n"
            f"    spec: {req.spec_slug}{verified}"
        )
    if slice_.dropped:
        lines.append(
            f"({len(slice_.shown)} of {slice_.matched} related requirements "
            f"shown — {slice_.dropped} lower-scoring one(s) were not)"
        )
    return "\n".join(lines)
