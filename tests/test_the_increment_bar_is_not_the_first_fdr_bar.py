"""A change request is judged as a change, not as a product brief (ADR-058).

Run 18 scored the increment axis 0%, and the 0 was not a reading of the gate.
All three follow-up FDRs came back `needs_answers` from `assess_fdr`, so
`run_feature` returned at intake and the reconciliation gate — the thing the
case exists to measure — was never reached. The cause is one control with two
call paths: the same assessor, calibrated to ask a FIRST FDR whether it
establishes users, actions and scope, applied to a request whose entire job is
to change one thing about a product that established all three already.

ADR-051's shape, inverted: there the second path silently did less; here it
silently demands more.
"""

from __future__ import annotations

import inspect

from ai_venture_studio.providers.base import Provider, register
from ai_venture_studio.upstream import fdr as fdr_mod
from ai_venture_studio.upstream.fdr import assess_fdr

CHANGE_REQUEST = "也让用户可以取消订单。/ Also let people cancel an order.\n"


@register
class RecordingAssessor(Provider):
    """Answers ready:true and keeps the prompt it was asked with."""

    name = "recording_assessor"
    system = ""
    user = ""

    def complete(self, *, model, system, user, max_tokens=1024):
        RecordingAssessor.system = system
        RecordingAssessor.user = user
        return "ready: true\nsummary: cancel an order\nquestions: []\n"

    def chat(self, *, model, system, messages, max_tokens=4096):
        raise NotImplementedError


def _assess(product_context: str):
    RecordingAssessor.system = RecordingAssessor.user = ""
    return assess_fdr(
        CHANGE_REQUEST, provider="recording_assessor", model="m",
        product_context=product_context,
    )


def test_a_follow_up_is_told_the_product_already_exists():
    _assess("REQ-1 users can place an order · verified by test_order")
    assert "ALREADY EXISTS" in RecordingAssessor.system
    assert "REQ-1 users can place an order" in RecordingAssessor.user, (
        "the assessor must be shown what it is not allowed to ask about again"
    )


def test_a_first_fdr_keeps_the_original_bar():
    """The strict bar is right when nothing exists yet — do not weaken it."""
    _assess("")
    assert RecordingAssessor.system == fdr_mod._ASSESSOR_SYSTEM
    assert "existing_product_requirements" not in RecordingAssessor.user


def test_the_feature_bar_forbids_the_questions_that_blocked_run_18():
    """It is not enough to add context; the instruction must change too."""
    text = " ".join(fdr_mod._FEATURE_ASSESSOR_SYSTEM.split())
    assert "DO NOT ask who the users are" in text
    assert "what is out of scope" in text
    assert "Prefer ready: true" in text


def test_run_feature_hands_over_the_existing_requirements():
    """The wiring, not just the capability: a parameter nobody passes is
    the same defect one layer down (ADR-048's inert instrument)."""
    from ai_venture_studio.upstream import autopilot

    source = inspect.getsource(autopilot.run_feature)
    assert "product_context=existing" in source
    assert "_render_slice" in source and "_relevant" in source


def test_an_unreadable_ledger_falls_back_to_the_strict_bar():
    """Degrading toward MORE questions is the safe direction; assert it."""
    from ai_venture_studio.upstream import autopilot

    source = inspect.getsource(autopilot.run_feature)
    assert 'existing = ""' in source
    assert "except (OSError, ValueError)" in source


# --- The row has to say which of the two zeroes it is -------------------------


def test_a_needs_answers_row_says_the_gate_never_ran():
    from ai_venture_studio.product_bench import _score_increment

    row = _score_increment(
        index=0, fdr=CHANGE_REQUEST, expected="completed",
        status="needs_answers", new_scrs=set(),
        intake_questions=["谁是用户?", "什么不做?"],
    )
    assert row.correct is False
    assert "STOPPED AT INTAKE" in row.detail
    assert "谁是用户?" in row.detail, (
        "a gate rate of 0 with no record of what was asked cannot be told "
        "apart from a gate that answered wrongly"
    )


def test_a_row_that_reached_the_gate_carries_no_intake_note():
    from ai_venture_studio.product_bench import _score_increment

    row = _score_increment(
        index=0, fdr=CHANGE_REQUEST, expected="raises_scr",
        status="completed", new_scrs={"SCR-001.yaml"},
        intake_questions=[],
    )
    assert row.actual == "raises_scr" and row.correct is True
    assert "STOPPED AT INTAKE" not in row.detail
