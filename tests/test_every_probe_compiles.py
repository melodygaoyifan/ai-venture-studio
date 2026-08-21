"""A probe that cannot parse is not a failing product (ADR-058).

Run 17 scored `05-increment-repairs` at 1/2 probes. The failing probe's detail
reads:

    SyntaxError: unexpected character after line continuation character

The product was never exercised. The probe died before its first statement,
and a probe that raises scores exactly like a probe whose assertion failed —
so a defect in the *instrument* was recorded, in the tracked capability
ledger, as a defect in the *system*.

The cause is a quoting collision nobody could see. Probe scripts live in a
YAML **folded** scalar (`script: '`), which joins the lines of a paragraph
with a space. A Python line continuation —

    assert any(...), \\
      f"the third follow-up added nothing: {modules}"

— is therefore delivered to `exec` as `..., \\ f"..."`, and nothing may follow
a backslash but the newline. The Python was correct. The YAML was correct.
The combination could never run, and it shipped in two case files.

This is ADR-048's shape aimed at the probes instead of the gate: an
instrument that cannot fire, reporting as though it fired and found nothing.
The rate looked at the product and never at itself.

`compile()` is free, hermetic, and settles it for every probe at once. It
proves only that the script PARSES — what it then asserts is the case
author's business — but parsing is the property that was silently false.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

BENCH = Path(__file__).parent.parent / "benchmarks"


def _probes():
    for directory in ("products", "products-real"):
        for path in sorted((BENCH / directory).glob("*.yaml")):
            case = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for probe in case.get("probes") or []:
                yield path.name, probe.get("name", "<unnamed>"), probe.get("script", "")


ALL_PROBES = list(_probes())


def test_there_are_probes_to_check():
    """The guard must not pass by finding nothing.

    A loader that silently yields an empty list is the failure mode this whole
    file exists to prevent, one level up.
    """
    assert len(ALL_PROBES) > 10


@pytest.mark.parametrize(
    ("case", "name", "script"),
    ALL_PROBES,
    ids=[f"{c}::{n}" for c, n, _ in ALL_PROBES],
)
def test_every_probe_script_parses(case, name, script):
    """Named per probe, so a failure says WHICH one rather than 'a probe'."""
    assert script.strip(), f"{case}::{name} has an empty script"
    try:
        compile(script, f"{case}::{name}", "exec")
    except SyntaxError as exc:  # pragma: no cover - the assertion is the point
        pytest.fail(
            f"{case}::{name} cannot parse — line {exc.lineno}: {exc.msg}\n"
            f"  A probe that raises before its first statement scores exactly "
            f"like a probe whose assertion failed, so this would be recorded "
            f"as a defect in the product.\n"
            f"  {(exc.text or '').strip()}"
        )


def test_a_line_continuation_cannot_survive_the_folded_scalar(tmp_path):
    """The specific collision, pinned as a fact about the file format.

    Not a restatement of the test above: that one checks today's probes, this
    one records *why* they broke, so the next person who reaches for a
    backslash continuation in a probe finds the reason rather than rediscovering
    it through a bench run five hours long and sixty-eight dollars deep.
    """
    doc = tmp_path / "case.yaml"
    doc.write_text(
        "script: '\n"
        "    assert True, \\\n"
        '      f"message"\n'
        "  '\n",
        encoding="utf-8",
    )
    folded = yaml.safe_load(doc.read_text(encoding="utf-8"))["script"]
    # The newline is gone; the backslash now has content after it on one line.
    assert "\\ " in folded
    with pytest.raises(SyntaxError):
        compile(folded, "folded", "exec")
