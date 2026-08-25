"""ADR-077: each design-memory section carries exactly one `files:` trailer.

Run 19, case 03, task t2: the spec model — shown the prior design.md as
memory — ended its design text with a `files:` line imitating the trailer
format, listing four files it guessed at. `_append_design_memory` then
appended its own trailer (`files: app/main.py`, the disk truth). The
committed section carried two consecutive, divergent trailers, and the
reviewer flagged it. The trailer is the appender's record; a model-authored
one at the end of the design text is stripped before the real one is written.
"""

from pathlib import Path

from ai_venture_studio.upstream.build import _append_design_memory
from ai_venture_studio.upstream.spec import Spec, TestSkeleton


def _spec(design: str) -> Spec:
    return Spec(
        slug="t2-detail-endpoint",
        title="GET /groupbuys/{id}",
        request="detail endpoint",
        profile="webapp",
        design=design,
        criteria=["The endpoint shall return 200 for a known id."],
        test_skeletons=[
            TestSkeleton(path="tests/test_detail.py", purpose="detail 200", covers=[0])
        ],
    )


def test_model_authored_trailer_is_replaced_by_disk_truth(tmp_path: Path):
    # The run-19 shape: the design text ends with an imitated, wrong trailer.
    spec = _spec(
        "Adds the detail endpoint to app/main.py.\n\n"
        "files: app/__init__.py, app/db.py, app/groupbuys.py, app/main.py\n"
    )
    _append_design_memory(tmp_path, spec, ["app/main.py", "tests/test_detail.py"])

    text = (tmp_path / "product" / "design.md").read_text(encoding="utf-8")
    trailers = [l for l in text.splitlines() if l.startswith("files:")]
    assert trailers == ["files: app/main.py"]
    # The design prose itself survives.
    assert "Adds the detail endpoint to app/main.py." in text


def test_plain_design_is_untouched(tmp_path: Path):
    spec = _spec("Module layout: app/main.py routes; app/db.py persistence.")
    _append_design_memory(tmp_path, spec, ["app/main.py"])

    text = (tmp_path / "product" / "design.md").read_text(encoding="utf-8")
    assert "Module layout: app/main.py routes; app/db.py persistence." in text
    assert [l for l in text.splitlines() if l.startswith("files:")] == [
        "files: app/main.py"
    ]


def test_a_mid_text_files_mention_is_not_stripped(tmp_path: Path):
    # Only TRAILING trailer-format lines are the appender's business; a
    # "files:" line mid-prose is the model's own text and stays.
    spec = _spec(
        "files: are listed per module below.\n\nRouting lives in app/main.py."
    )
    _append_design_memory(tmp_path, spec, ["app/main.py"])

    text = (tmp_path / "product" / "design.md").read_text(encoding="utf-8")
    assert "files: are listed per module below." in text
    assert text.splitlines()[-1] == "files: app/main.py"
