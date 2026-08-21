"""What the linter is allowed to stop catching, and what it must not.

ADR-062. `select = ["F"]` was drawn deliberately narrow in ADR-055 and never
revisited; widening it found two live defects on the first run, and the way a
gate like this dies is not by being wrong — it is by being turned off one
`ignore` at a time, each with a good reason nobody wrote down.

So these tests are about the CONFIGURATION as much as the code:

  - the families that were adopted stay adopted;
  - every blanket `ignore` has a written reason next to it in `pyproject.toml`,
    because a bare `"S607"` is indistinguishable from a rule someone found
    annoying — and that particular one is now fixed rather than ignored
    (ADR-064), which is what an ignore-with-a-reason is for;
  - the two defects the widening found stay fixed, tested by behaviour rather
    than by asking ruff again.
"""

from __future__ import annotations

import logging
import pathlib
import tomllib

import pytest

ROOT = pathlib.Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _lint_config() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["tool"]["ruff"]["lint"]


# --- the gate itself ---------------------------------------------------------


def test_the_families_that_were_adopted_are_still_selected():
    """A regression here is silent: the suite goes green, the lint job goes
    green, and the rules simply stop being asked."""
    selected = set(_lint_config()["select"])
    for family in ("F", "B", "S", "BLE"):
        assert family in selected, (
            f"{family} was adopted in ADR-062 and is no longer selected. If "
            f"that is deliberate, the reason belongs in pyproject.toml beside "
            f"the change and in an ADR, not in a diff nobody reads."
        )


def test_no_rule_is_ignored_without_a_written_reason():
    """The allowlist-as-judgment shape from ADR-060, applied to the linter.

    `S603` is ignored because the rule it encodes is met by other means, and
    that is written down. What is not defensible is the next entry being added
    with no sentence at all — which is how `S607` would have stayed.
    """
    ignored = _lint_config().get("ignore", [])
    assert ignored, "the test is meaningless if nothing is ignored"

    source = PYPROJECT.read_text(encoding="utf-8")
    for code in ignored:
        # The comment block immediately above the entry.
        before = source.split(f'"{code}"')[0].rstrip().splitlines()
        reason = []
        for line in reversed(before):
            stripped = line.strip()
            if stripped.startswith("#"):
                reason.insert(0, stripped.lstrip("# ").strip())
            elif stripped.endswith(('",', '"')):
                continue  # a neighbouring code on the same comment block
            else:
                break
        words = " ".join(reason).split()
        assert len(words) >= 12, (
            f"{code} is ignored with {len(words)} word(s) of explanation. An "
            f"ignore is a decision; write the decision down."
        )


def test_s607_is_enforced_and_not_ignored_anywhere():
    """This test used to assert the opposite.

    ADR-062 shipped `S607` as an ignore-with-a-reason — 152 sites invoking
    `git`, `gh`, `npm` and friends by bare name, against a CLAUDE.md rule
    stated in words. The ignore named its own fix ("one resolver that calls
    `shutil.which`"), and ADR-064 wrote it, so the entry is gone rather than
    grandfathered. The failure this guards against is the entry coming BACK
    the next time someone adds a call site in a hurry: an ignore that was
    once temporary is the easiest one in the file to re-add.
    """
    config = _lint_config()
    assert "S607" not in config.get("ignore", []), (
        "S607 was fixed at every site in ADR-064, not deferred. If a new call "
        "site needs a bare name, route it through "
        "`ai_venture_studio.executables.resolve` instead of reopening the rule."
    )
    for pattern, codes in config["per-file-ignores"].items():
        assert "S607" not in codes, (
            f"S607 is ignored for {pattern!r}; tests invoke `git` as much as "
            f"src does and the resolver works in both"
        )


def test_every_subprocess_head_in_src_resolves_through_one_place():
    """The rule ADR-064 actually cares about, asked of the code rather than
    of the config: a bare-name argv head is how you run whatever happens to
    be first on PATH — in a workspace built from model output, next to an
    `npm install` that just ran in it."""
    import ast

    src = ROOT / "src" / "ai_venture_studio"
    offenders = []
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in {"run", "Popen", "check_output", "check_call"}:
                continue
            if not node.args or not isinstance(node.args[0], ast.List):
                continue
            head = node.args[0].elts[0] if node.args[0].elts else None
            if (
                isinstance(head, ast.Constant)
                and isinstance(head.value, str)
                and "/" not in head.value
            ):
                offenders.append(f"{path.name}:{head.lineno} {head.value!r}")
    assert not offenders, (
        "these subprocess calls name an executable without resolving it: "
        + ", ".join(offenders)
    )


def test_asserts_are_still_banned_in_src():
    """`S101` is ignored in tests only. src/ was measured at zero when the
    family was adopted, and CLAUDE.md's rule ('replace all runtime assert
    statements') only means something while it stays there."""
    per_file = _lint_config()["per-file-ignores"]
    for pattern, codes in per_file.items():
        if "S101" in codes:
            assert pattern.startswith("tests"), (
                f"S101 is ignored for {pattern!r} — outside tests/, an assert "
                f"is a check that vanishes under `python -O`"
            )


# --- the defects the widening found ------------------------------------------


def test_a_client_that_stopped_early_is_a_desync():
    """`desync_probe` zipped two streams and read the common prefix. A client
    that stopped producing has no divergence in that prefix, so the probe
    passed — a silent green on the exact failure it exists to catch."""
    from ai_venture_studio.lanes.realtime import desync_probe

    verdict = desync_probe(
        ["a", "b", "c", "d"], ["a", "b"], hash_every_n_ticks=10
    )
    assert not verdict.passed, (
        "the client stopped at tick 2 and the server did not; that is the "
        "most complete desync available"
    )
    assert "different lengths" in verdict.detail
    assert "4" in verdict.detail and "2" in verdict.detail, (
        "the reader has to be able to tell WHICH side stopped"
    )


def test_two_identical_streams_are_still_clean():
    """The other direction must not move — a probe that fails everything is
    as useless as one that passes everything."""
    from ai_venture_studio.lanes.realtime import desync_probe

    verdict = desync_probe(["a", "b"], ["a", "b"], hash_every_n_ticks=10)
    assert verdict.passed and verdict.detail == "no divergence"


def test_a_replay_that_agrees_then_stops_says_so():
    """`cross_build_replay` reported this as 'divergence at tick None'."""
    from ai_venture_studio.lanes.realtime import cross_build_replay

    verdict = cross_build_replay(
        ["a", "b", "c"], ["a", "b"], change_expected_from_tick=None
    )
    assert not verdict.passed
    assert "None" not in verdict.detail, verdict.detail
    assert "stops" in verdict.detail


def test_a_number_the_register_cannot_check_is_a_finding():
    """`zip(draft_numbers, register_numbers)` stopped at the shorter one, so
    every figure past the register entry's last was checked against nothing
    and passed — an unsubstantiated number leaving through the check built to
    catch unsubstantiated numbers."""
    from ai_venture_studio.marketing.substantiation import check_substantiation

    register = _register("Teams ship 40% faster.")
    findings = check_substantiation("Teams ship 40% faster across 12000 sessions.",
                                    register)
    rules = [f.rule for f in findings]
    assert "unsubstantiated_number" in rules, (
        f"12000 has no counterpart in the register entry and was never "
        f"checked; got {rules}"
    )
    unsub = next(f for f in findings if f.rule == "unsubstantiated_number")
    assert "12000" in unsub.message
    assert "number_drift" not in rules, "40 matches 40 — that half was fine"


def test_a_draft_with_no_extra_numbers_is_left_alone():
    """The new rule must not fire on the case the old code handled."""
    from ai_venture_studio.marketing.substantiation import check_substantiation

    register = _register("Teams ship 40% faster across 12000 sessions.")
    findings = check_substantiation("Teams ship 40% faster.", register)
    assert [f.rule for f in findings] == [], [f.message for f in findings]


def _register(text: str):
    from ai_venture_studio.marketing.register import ReleaseContract

    return ReleaseContract.model_validate({
        "prd_ref": "product/PRD.md",
        "claims_available": [
            {"id": "C-1", "text": text, "source_type": "primary_measured"},
        ],
    })


# --- the fifteen that had nowhere to say it ----------------------------------


def test_a_skipped_spend_row_says_so(tmp_path, caplog):
    """The CLAUDE.md rule with no enforcement until now: 'never silently
    swallow exceptions; log the error or handle it explicitly'. This one is
    money — a row that will not parse is spend that is not counted, and the
    total looked the same either way."""
    from ai_venture_studio import spend

    ledger = tmp_path / ".mas" / spend.LEDGER_FILE
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        '{"at": "2026-08-21T00:00:00", "model": "m", "input_tokens": 1, '
        '"output_tokens": 1}\nthis line is not json\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.DEBUG, logger="ai_venture_studio.spend"):
        rows = spend.read_entries(tmp_path)

    assert len(rows) == 1, "the good row still loads — degrading, not failing"
    assert caplog.records, "the bad row was dropped without a word"
    assert "not counted" in caplog.text


def test_no_handler_in_src_swallows_an_exception_in_silence():
    """The sweep, not the instance. `S110`/`S112` are selected, so ruff is the
    enforcement; this asserts that they ARE selected, because a rule that
    stops being asked fails open."""
    selected = set(_lint_config()["select"])
    ignored = set(_lint_config().get("ignore", []))
    assert "S" in selected or {"S110", "S112"} <= selected
    assert not ({"S110", "S112"} & ignored), (
        "these two encode a CLAUDE.md invariant; ignoring them retires the "
        "rule without saying so"
    )


def test_debug_logging_is_off_unless_asked_for():
    """These lines go to stderr and the CLI's real output is Rich on stdout.
    A logger that prints by default is a new bug, not a fix for an old one."""
    logger = logging.getLogger("ai_venture_studio.spend")
    assert logger.level == logging.NOTSET, (
        "the module must not set its own level — `AVS_DEBUG=1` in `cli.main` "
        "is the single switch"
    )
    source = (ROOT / "src" / "ai_venture_studio" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert 'os.environ.get("AVS_DEBUG")' in source, (
        "without a reader, every _log.debug added by ADR-062 is the ADR-060 "
        "defect wearing a different hat"
    )


@pytest.mark.parametrize("module", [
    "compound", "lanes.botfleet", "maintenance.skills_registry",
    "product_bench", "spend", "studio_chat", "upstream.autopilot",
    "upstream.build", "upstream.progress", "upstream.screenshots",
    "upstream.telemetry", "verify",
])
def test_every_module_that_degrades_has_somewhere_to_say_it(module):
    import importlib

    mod = importlib.import_module(f"ai_venture_studio.{module}")
    assert hasattr(mod, "_log"), (
        f"{module} skips a row, a page or a piece of bookkeeping somewhere; "
        f"it needs a logger to say which"
    )
    assert mod._log.name == f"ai_venture_studio.{module}", (
        "named per module so `AVS_DEBUG` output says where the loss happened"
    )
