"""The conversational FDR intake — one question at a time.

The form asks for the whole FDR at once. When the assessor comes back with
five questions, the founder has to find and edit the right lines inside a
4000-character textarea, and that edit is where people stop. This asks one
question, takes one answer, and composes the document itself.

Three properties it deliberately keeps:

- **FDR.md stays the single source of truth.** The conversation is an input
  method that COMPOSES the FDR; it is not a second place requirements live.
  discover / plan / spec / build read exactly the file they always read.
- **Deterministic control flow** (CLAUDE.md): Python decides which question
  comes next. The model only ever *generates* clarify questions through the
  existing `assess_fdr`; it never decides whether to ask another round.
- **It cannot trap you.** Clarify rounds are capped, and every turn offers a
  way to stop answering and go build. A loop that keeps asking until the
  model is satisfied is worse than a slightly under-specified FDR — the
  founder can always add a feature FDR later, but they cannot get the
  afternoon back.
"""

from __future__ import annotations

import logging
import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, Field

#: Where a deliberate degradation says what it degraded. Every handler
#: below that skips a row, a page, or a piece of bookkeeping logs here
#: first: CLAUDE.md forbids swallowing an exception silently, and until
#: ADR-062 nothing enforced it (`S110`/`S112` found 15). DEBUG, so it is
#: silent unless asked for — `AVS_DEBUG=1` is the ask.
_log = logging.getLogger(__name__)

#: The six FDR questions, in order. Slot names are stable identifiers — the
#: prose lives in studio_i18n under `chat_q_<slot>`.
INTAKE_SLOTS: tuple[str, ...] = (
    "who", "actions", "must", "not_needed", "constraints", "success",
)

CLARIFY = "clarify"

#: The open prompt that now comes FIRST — deliberately not one of the six.
#: Asking the six one at a time is a form wearing a chat's clothes: the
#: founder already knows what they want to build and is made to deliver it
#: in six instalments. So: one open prompt, one extraction pass over what
#: they wrote, and then questions only about what is genuinely missing.
OPEN = "open"

#: Turn kinds. "" is an ordinary line of conversation; the other two are
#: what the extraction pass left behind:
#: - "said": a synthetic (question, answer) pair whose answer text is the
#:   founder's OWN WORDS, lifted verbatim from their paragraph. It counts as
#:   answered and composes into FDR.md exactly like a typed answer.
#: - "guess": a PROPOSAL. It is not an answer, it never composes into
#:   FDR.md, and it becomes one only when the founder confirms it.
SAID = "said"
GUESS = "guess"

#: After this many assessor rounds the conversation stops asking and offers
#: to build. Two rounds is ten questions; past that the assessor is usually
#: polishing, not unblocking.
MAX_CLARIFY_ROUNDS = 2

_FILE = "conversation.jsonl"

#: Document headings, per language. These are artifact content (they end up
#: in FDR.md and downstream prompts read them), not UI chrome, so they live
#: beside the composer rather than in the UI string table.
_HEADINGS: dict[str, dict[str, str]] = {
    "zh": {
        "open": "## 0. 你自己的话 / In your own words",
        "who": "## 1. 这是给谁用的？/ Who is this for?",
        "actions": "## 2. 用户用它来做什么？/ What do users do with it?",
        "must": "## 3. 必须有的功能 / Must-have features",
        "not_needed": "## 4. 暂时不要的功能 / NOT needed for now",
        "constraints": "## 5. 有什么限制或偏好？/ Constraints or preferences",
        "success": "## 6. 怎么算成功？/ What does success look like?",
        "clarify": "## 7. 补充说明 / Follow-up answers",
        "title": "# 产品需求描述 / Product Requirements (FDR)",
    },
    "en": {
        "open": "## 0. In your own words",
        "who": "## 1. Who is this for?",
        "actions": "## 2. What do users do with it?",
        "must": "## 3. Must-have features",
        "not_needed": "## 4. NOT needed for now",
        "constraints": "## 5. Constraints or preferences",
        "success": "## 6. What does success look like?",
        "clarify": "## 7. Follow-up answers",
        "title": "# Product Requirements (FDR)",
    },
}


class Turn(BaseModel):
    """One line of the conversation. `slot` on an assistant turn says which
    question it is; the user turn that follows is its answer."""

    role: str  # assistant | user
    text: str
    slot: str = ""
    at: str = Field(default="")
    #: "" | SAID | GUESS — see the constants above. Defaulted, so every
    #: conversation.jsonl written before the extraction existed still loads.
    kind: str = ""
    #: For a GUESS turn only: the words that WOULD be written into FDR.md if
    #: the founder confirms it. Kept beside the proposal so confirming does
    #: not need a second model call to remember what was proposed.
    value: str = ""


def path_for(root: str | Path) -> Path:
    return Path(root) / ".mas" / _FILE


def load_thread(root: str | Path) -> list[Turn]:
    """Every turn so far. An unreadable line is skipped rather than fatal —
    a truncated last write must not make the conversation unrecoverable."""
    path = path_for(root)
    if not path.exists():
        return []
    turns: list[Turn] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            turns.append(Turn(**json.loads(line)))
        except Exception as exc:  # noqa: BLE001 — a bad row is not a conversation
            _log.debug("skipping an unreadable turn in the thread: %s", exc)
            continue
    return turns


def append_turn(
    root: str | Path, role: str, text: str, slot: str = "",
    kind: str = "", value: str = "",
) -> Turn:
    if role not in ("assistant", "user"):
        raise ValueError(f"unknown role {role!r} — expected assistant or user")
    turn = Turn(
        role=role, text=text, slot=slot, kind=kind, value=value,
        at=dt.datetime.now(dt.UTC).isoformat(),
    )
    path = path_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(turn.model_dump(), ensure_ascii=False) + "\n")
    return turn


def reset_thread(root: str | Path) -> None:
    path_for(root).unlink(missing_ok=True)


def pairs(turns: list[Turn]) -> list[tuple[Turn, Turn]]:
    """(question, answer) for every question that has been answered."""
    answered: list[tuple[Turn, Turn]] = []
    for index, turn in enumerate(turns):
        following = turns[index + 1] if index + 1 < len(turns) else None
        if turn.role == "assistant" and following is not None and following.role == "user":
            answered.append((turn, following))
    return answered


def open_question(turns: list[Turn]) -> Turn | None:
    """The question waiting for a typed answer, if any.

    A GUESS is deliberately not one: it is resolved by confirming or
    correcting it (its own route), never by the plain composer — an answer
    typed against "did you mean X?" would otherwise be filed as the slot's
    content, and "yes" is not a description of anybody's product.
    """
    if turns and turns[-1].role == "assistant" and turns[-1].kind != GUESS:
        return turns[-1]
    return None


def next_intake_slot(turns: list[Turn]) -> str | None:
    """The next of the six to ask, or None when all six are answered.

    Slots the extraction filled from the founder's own words are already
    answered, so this now drives the GAPS rather than reading a script.
    """
    done = {question.slot for question, _ in pairs(turns)}
    for slot in INTAKE_SLOTS:
        if slot not in done:
            return slot
    return None


def next_question_slot(turns: list[Turn]) -> str | None:
    """What to ask next: the open prompt on an empty thread, otherwise the
    next unanswered one of the six.

    A thread that already has turns is one somebody is in the middle of —
    including every conversation started before the open prompt existed —
    so it keeps the one-at-a-time path it began with.
    """
    if not turns:
        return OPEN
    return next_intake_slot(turns)


def pending_guess(turns: list[Turn]) -> Turn | None:
    """The first proposed guess the founder has not resolved yet.

    Resolved means its slot has an answer — which is what confirming or
    correcting a guess writes. Guesses are therefore offered one at a time
    and can never pile up unanswered behind each other.
    """
    answered = {question.slot for question, _ in pairs(turns)}
    for turn in turns:
        if turn.kind == GUESS and turn.slot not in answered:
            return turn
    return None


# ── the one extraction pass ──────────────────────────────────────────────

EXTRACTOR_MARKER = "intake extractor for a founder's own paragraph"

_EXTRACT_SYSTEM = f"""You are the {EXTRACTOR_MARKER}. The founder has
described their product in their own words. Map what they wrote onto these
six slots:

- who: who the product is for
- actions: what people do with it
- must: features it must have
- not_needed: what is deliberately out of scope for now
- constraints: constraints or preferences
- success: what success looks like

TWO KINDS OF OUTPUT, and the difference is the whole point:

`said` — ONLY where the founder's own words answer the slot. The value MUST
be a span copied VERBATIM from their paragraph, character for character. Do
not summarise, translate, tidy or complete it. Omit the slot entirely if
their words do not answer it. Never invent.

`guesses` — at most TWO things their paragraph strongly implies but does
not say. These are proposals the founder will be asked to confirm; they are
never treated as something they said.

Respond with ONLY YAML:
said:
  who: <their exact words>
  actions: <their exact words>
guesses:
  - slot: constraints
    value: what you would propose
    why: one short sentence on what implies it
"""


class Guess(BaseModel):
    slot: str
    value: str
    why: str = ""


class Extraction(BaseModel):
    """What one pass over the founder's paragraph produced. `said` holds
    verbatim spans of their own writing; `guesses` holds proposals."""

    said: dict[str, str] = Field(default_factory=dict)
    guesses: list[Guess] = Field(default_factory=list)


#: How many proposals a founder is asked to rule on. Two is a conversation;
#: six is the form we just replaced.
MAX_GUESSES = 2


def _squash(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", text).strip().casefold()


def is_verbatim(value: str, paragraph: str) -> bool:
    """Whether `value` really is a span of what the founder wrote.

    Whitespace is normalised and case is ignored — a model that re-wraps a
    line has still copied it. Anything else has been rewritten, and a
    rewrite is the model's sentence, not the founder's.
    """
    return bool(value.strip()) and _squash(value) in _squash(paragraph)


def extract_intake(
    paragraph: str, *, provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> Extraction:
    """One pass: the founder's paragraph → SAID spans and GUESS proposals.

    Python, not the model, enforces the charter rule. A `said` value that is
    not a verbatim span of the paragraph is DEMOTED to a guess rather than
    dropped or trusted: the founder still gets the benefit of what the model
    noticed, and it still has to be confirmed before it can enter FDR.md.
    """
    from ai_venture_studio.providers import get_provider
    from ai_venture_studio.yamlx import extract_mapping

    raw = get_provider(provider).complete(
        model=model,
        system=_EXTRACT_SYSTEM,
        user=f"<paragraph>\n{paragraph}\n</paragraph>",
        max_tokens=2048,
    )
    try:
        data = extract_mapping(raw, ("said", "guesses"))
    except ValueError:
        # An unparseable extraction is not a failure worth stopping for:
        # the conversation simply asks all six, which is where it started.
        return Extraction()

    raw_said = data.get("said")
    raw_guesses = data.get("guesses")
    said: dict[str, str] = {}
    demoted: list[Guess] = []
    for slot, value in (raw_said if isinstance(raw_said, dict) else {}).items():
        if slot not in INTAKE_SLOTS or not isinstance(value, str):
            continue
        if is_verbatim(value, paragraph):
            said[slot] = value.strip()
        elif value.strip():
            demoted.append(Guess(slot=slot, value=value.strip(), why=""))

    guesses: list[Guess] = []
    for entry in raw_guesses if isinstance(raw_guesses, list) else []:
        if not isinstance(entry, dict):
            continue
        slot = str(entry.get("slot", ""))
        value = str(entry.get("value", "")).strip()
        if slot in INTAKE_SLOTS and value:
            guesses.append(Guess(slot=slot, value=value, why=str(entry.get("why", ""))))
    seen = set(said)
    ordered: list[Guess] = []
    for guess in guesses + demoted:
        if guess.slot in seen:
            continue
        seen.add(guess.slot)
        ordered.append(guess)
    return Extraction(said=said, guesses=ordered[:MAX_GUESSES])


def apply_extraction(
    root: str | Path, extraction: Extraction, questions: dict[str, str]
) -> None:
    """Write the extraction into the thread.

    A SAID slot becomes a synthetic (question, answer) pair whose answer
    text is the founder's own words — so `compose_fdr` stays deterministic
    string assembly and the document still contains nothing but what they
    typed. A GUESS becomes a single assistant turn awaiting confirmation,
    and confirming it is the only thing that can turn it into an answer.
    """
    for slot in INTAKE_SLOTS:
        if slot in extraction.said:
            append_turn(
                root, "assistant", questions.get(slot, slot), slot=slot, kind=SAID
            )
            append_turn(
                root, "user", extraction.said[slot], slot=slot, kind=SAID
            )
    for guess in extraction.guesses:
        append_turn(
            root, "assistant", guess.why or guess.value,
            slot=guess.slot, kind=GUESS, value=guess.value,
        )


def resolve_guess(
    root: str | Path, guess: Turn, answer: str, question: str
) -> None:
    """Turn a proposal into an answer — with the founder's words if they
    corrected it, with the proposed words only because they confirmed it."""
    append_turn(root, "assistant", question, slot=guess.slot, kind=SAID)
    append_turn(root, "user", answer, slot=guess.slot, kind=SAID)


def clarify_rounds_used(turns: list[Turn]) -> int:
    """How many assessor questions have been ANSWERED, in rounds of up to
    five. Used against MAX_CLARIFY_ROUNDS so the loop is bounded."""
    answered = sum(
        1 for question, _ in pairs(turns) if question.slot == CLARIFY
    )
    return -(-answered // 5)  # ceiling division: 1-5 answers = round 1


def intake_complete(turns: list[Turn]) -> bool:
    return next_intake_slot(turns) is None


def has_intake(turns: list[Turn]) -> bool:
    """True when the product was described HERE — either through the open
    prompt or by answering any of the six.

    False means the FDR came from somewhere else — the form, the CLI, a
    hand-written file — and the conversation is only collecting follow-up
    answers. Composing a fresh document in that case would replace the
    founder's own writing with six "(not answered)" sections.
    """
    return any(
        question.slot in INTAKE_SLOTS or question.slot == OPEN
        for question, _ in pairs(turns)
    )


def compose_fdr(
    turns: list[Turn], lang: str = "en", base_fdr: str = ""
) -> str:
    """Build FDR.md from the answers.

    Deterministic string assembly, never a model call: the founder's words
    go into the document as they typed them. An unanswered section is
    written as an explicit blank rather than omitted, so the assessor sees a
    gap instead of a document that looks complete.

    `base_fdr` is an existing document to EXTEND rather than replace. When
    the six intake questions were not answered here, the conversation is a
    clarify pass over someone else's document and its only contribution is
    the follow-up section appended to the end.
    """
    if base_fdr and not has_intake(turns):
        follow_ups = _follow_up_block(turns, lang)
        if not follow_ups:
            return base_fdr
        return base_fdr.rstrip() + "\n\n" + follow_ups
    headings = _HEADINGS.get(lang if lang in _HEADINGS else "en")
    if headings is None:  # pragma: no cover — dict lookup above is total
        raise ValueError(f"no headings for language {lang!r}")
    answers = {
        question.slot: answer.text.strip()
        for question, answer in pairs(turns)
        if question.slot in INTAKE_SLOTS
    }
    blocks = [headings["title"], ""]
    # The paragraph they wrote first, verbatim and whole. The extraction
    # lifts spans out of it into the six sections, but a span is not the
    # paragraph: dropping the rest would throw away the founder's own
    # framing, which is the most valuable text in the document.
    opening = next(
        (answer.text.strip() for question, answer in pairs(turns)
         if question.slot == OPEN),
        "",
    )
    if opening:
        blocks += [headings["open"], "", opening, ""]
    for slot in INTAKE_SLOTS:
        blocks.append(headings[slot])
        blocks.append("")
        blocks.append(answers.get(slot, "") or "(未回答 / not answered)")
        blocks.append("")

    follow_ups = _follow_up_block(turns, lang)
    if follow_ups:
        blocks.append(follow_ups)
    return "\n".join(blocks)


def _follow_up_block(turns: list[Turn], lang: str) -> str:
    """The §7 answers-to-follow-up-questions section, or "" if there are
    none."""
    headings = _HEADINGS.get(lang if lang in _HEADINGS else "en", _HEADINGS["en"])
    answered = [
        (question, answer)
        for question, answer in pairs(turns)
        if question.slot == CLARIFY
    ]
    if not answered:
        return ""
    lines = [headings["clarify"], ""]
    for index, (question, answer) in enumerate(answered, start=1):
        lines.append(f"{index}. **{question.text.strip()}**")
        lines.append(f"   {answer.text.strip()}")
    lines.append("")
    return "\n".join(lines)


def transcript(turns: list[Turn]) -> str:
    """Plain-text rendering, for the operator and for tests."""
    return "\n".join(f"{turn.role}: {turn.text}" for turn in turns)
