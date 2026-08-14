"""An empty spec is a delivery failure, not a quiet pass (ADR-041).

Bench run 15 blocked two independent cases with the same spec on disk:
`criteria: []`, `test_skeletons: []`, `design: ''`, and one block reason,
"no acceptance criteria". That sentence reads as a judgment about a spec
the writer wrote. It wasn't — nothing was written.

Two defects produced it, and they compound:

  1. Emptiness passed every quality check by having nothing to check, so
     the revision loop's "good enough" break fired on a spec containing
     nothing, and the feedback sent back to the writer never once said
     "you returned no criteria".
  2. The spec stage was the only writer stage that never asked whether its
     response had been cut off at the output cap — so a truncated response,
     which `extract_mapping` happily parses into `{title: ...}`, arrived as
     a content verdict instead of a delivery failure.

These tests pin both, and pin the ledger field that makes the difference
visible after a run instead of only during one.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from ai_venture_studio.providers import base as provider_base
from ai_venture_studio.upstream import spec as spec_mod


@pytest.fixture(autouse=True)
def _clear_stop_reason():
    """The truncation flag is thread-local and sticky by design — a test that
    leaves it set would make the next one pass for the wrong reason."""
    provider_base.record_stop_reason(None)
    yield
    provider_base.record_stop_reason(None)


def _source_without_comments(path: pathlib.Path) -> str:
    """Source-inspection tests must not match their own explanatory prose."""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


SPEC_SRC = pathlib.Path(spec_mod.__file__)


# --- the three quality checks are all silent on an empty spec ------------


def test_an_empty_spec_passes_every_quality_check():
    """The premise of the bug: this is why emptiness was the quiet failure."""
    from ai_venture_studio.upstream import ears

    empty = {"criteria": [], "test_skeletons": [], "design": ""}
    assert ears.lint_criteria([]) == []
    assert spec_mod._coverage_gaps(empty) == []
    assert spec_mod._foreign_skeletons(empty, "web") == []


def test_no_criteria_is_the_loudest_complaint_not_the_quietest():
    assert spec_mod._undelivered({"criteria": [], "test_skeletons": []})
    reason = spec_mod._undelivered({"criteria": []})[0]
    assert "NO acceptance criteria" in reason


def test_criteria_without_skeletons_is_also_undelivered():
    found = spec_mod._undelivered(
        {"criteria": ["When x, the system shall y."], "test_skeletons": []}
    )
    assert found and "NO test skeletons" in found[0]


def test_a_complete_spec_is_not_reported_as_undelivered():
    assert spec_mod._undelivered(
        {
            "criteria": ["When x, the system shall y."],
            "test_skeletons": [{"path": "tests/test_x.py", "covers": [0]}],
        }
    ) == []


def test_an_empty_spec_draws_exactly_one_complaint_not_two():
    """With no criteria there are trivially no skeletons either. Reporting
    both would bury the one that matters under a consequence of itself."""
    assert len(spec_mod._undelivered({"criteria": [], "test_skeletons": []})) == 1


# --- the revision loop no longer breaks on an empty spec -----------------


def test_the_good_enough_break_is_not_reachable_by_emptiness():
    """The break condition must consult `undelivered`. Without it, a spec of
    nothing satisfies all four remaining terms and exits the loop as
    'good enough' on the first attempt."""
    code = _source_without_comments(SPEC_SRC)
    assert "if not undelivered and not lint and not gaps" in code


def test_the_writer_is_told_it_returned_nothing_first():
    """Order is the message: the other four keys are all empty when this one
    is not, so it has to lead or it reads as one nit among several."""
    code = _source_without_comments(SPEC_SRC)
    blob = code[code.index("feedback = yaml.safe_dump"):]
    assert blob.index("you_returned_nothing") < blob.index("ears_lint")


# --- truncation is a delivery failure, not a content verdict -------------


def test_the_spec_stage_asks_whether_its_response_was_cut_off():
    """plan.py, build.py and discover.py all ask. spec.py was the one that
    didn't, which is the whole defect."""
    code = _source_without_comments(SPEC_SRC)
    assert "last_response_truncated()" in code


def test_every_writer_stage_asks_the_same_question():
    """Pins the invariant rather than this one instance of it — the next
    writer stage added without the check should fail here."""
    root = pathlib.Path(spec_mod.__file__).parent
    for name in ("spec.py", "plan.py", "build.py", "discover.py"):
        src = _source_without_comments(root / name)
        assert "last_response_truncated()" in src, f"{name} never asks"


def test_a_truncated_response_parses_into_a_spec_of_nothing():
    """Why the check is needed at all: the dangerous case is not a parse
    error, it is a partial answer wearing the shape of a complete one."""
    from ai_venture_studio.yamlx import extract_mapping

    cut_off = 'title: "POST /api/groupbuys — create and validate group-buys"'
    parsed = extract_mapping(cut_off, ("criteria", "title"))
    assert parsed.get("title")
    assert parsed.get("criteria", []) == []


def test_a_cut_off_response_is_not_blamed_on_the_spec_content():
    """The two diagnoses differ in what a person would do next: raise the
    cap, or rewrite the prompt."""
    code = _source_without_comments(SPEC_SRC)
    assert "cut off at the output cap" in code
    assert 'else "no acceptance criteria"' in code


def test_the_plain_block_reason_survives_for_the_ordinary_case():
    """A spec that simply came back without criteria still says so in the
    words the rest of the system and its operators already know."""
    code = _source_without_comments(SPEC_SRC)
    assert '"no acceptance criteria"' in code


def test_truncation_is_rechecked_every_attempt():
    """`truncated` has to be cleared on a clean attempt, or one cut-off
    response would mislabel every later block reason in the same spec."""
    code = _source_without_comments(SPEC_SRC)
    assert "truncated = False" in code.split("for revision in range")[1]


# --- the ledger remembers why the model stopped --------------------------


def test_the_ledger_records_why_the_model_stopped():
    from ai_venture_studio.spend import SpendEntry

    assert "stop_reason" in SpendEntry.model_fields


def test_an_old_ledger_line_still_loads():
    """Every result already on disk predates the field."""
    from ai_venture_studio.spend import SpendEntry

    entry = SpendEntry(
        at="2026-08-14T06:53:43+00:00", model="claude-opus-4-8",
        input_tokens=1518, output_tokens=2048,
    )
    assert entry.stop_reason == ""


def test_a_recorded_call_carries_its_stop_reason(tmp_path):
    from ai_venture_studio import spend

    spend._buffer.clear()
    spend.record("claude-opus-4-8", 1518, 2048, stop_reason="max_tokens")
    try:
        assert spend._buffer[-1]["stop_reason"] == "max_tokens"
    finally:
        spend._buffer.clear()


def test_recording_never_raises_on_a_junk_stop_reason():
    """Metering must not take down the work being metered."""
    from ai_venture_studio import spend

    spend._buffer.clear()
    spend.record("m", 1, 1, stop_reason=None)
    try:
        assert spend._buffer[-1]["stop_reason"] == ""
    finally:
        spend._buffer.clear()


def test_the_adapter_hands_the_stop_reason_to_the_ledger():
    """Without this the field exists and is always empty — the worst of both,
    since it looks like evidence that nothing was ever truncated."""
    from ai_venture_studio.providers import anthropic_provider

    code = _source_without_comments(pathlib.Path(anthropic_provider.__file__))
    block = code[code.index("spend.record("):]
    assert "stop_reason=" in block[:300]


def test_output_tokens_alone_cannot_prove_truncation():
    """The reason the field was needed: a response that stops exactly on the
    cap is indistinguishable from one that was cut off there, and run 15 had
    to be diagnosed by squinting at that coincidence."""
    from ai_venture_studio.spend import SpendEntry

    capped = SpendEntry(at="t", model="m", output_tokens=2048,
                        stop_reason="max_tokens")
    complete = SpendEntry(at="t", model="m", output_tokens=2048,
                          stop_reason="end_turn")
    assert capped.output_tokens == complete.output_tokens
    assert capped.stop_reason != complete.stop_reason


def test_the_truncation_reason_set_covers_what_the_ledger_stores():
    assert "max_tokens" in provider_base.TRUNCATION_REASONS
    provider_base.record_stop_reason("max_tokens")
    assert provider_base.last_response_truncated()


def test_a_finished_response_is_not_read_as_truncated():
    provider_base.record_stop_reason("end_turn")
    assert not provider_base.last_response_truncated()


def test_the_feedback_blob_stays_valid_yaml_when_nothing_was_delivered():
    """It is dumped and handed straight back to the model; a blob that fails
    to serialize would replace the complaint with a crash."""
    blob = yaml.safe_dump(
        {
            "you_returned_nothing": spec_mod._undelivered({"criteria": []}),
            "ears_lint": [], "uncovered_criteria_indices": [],
            "critic_majors": [], "wrong_language_skeletons": [],
        },
        sort_keys=False, allow_unicode=True,
    )
    assert yaml.safe_load(blob)["you_returned_nothing"]
