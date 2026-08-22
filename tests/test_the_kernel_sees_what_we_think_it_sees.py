"""The runtime half of the bare-executable rule (ADR-071).

`tests/exec_audit.py` watches CPython's `subprocess.Popen` audit event for the
whole session. This file is the ratchet over what it recorded, plus the
controls that make a reading of zero mean something.

Every other assertion here passes just as well when the hook is broken: no
hook means no events means no violations, byte-identical to a clean tree. That
is ADR-067's finding — *an empty measurement reads exactly like a passing one*
— and it applies with particular force to a detector whose passing output is
the number zero. So the hook is exercised in both directions before its silence
is accepted as evidence.
"""

from __future__ import annotations

import contextlib
import platform
import subprocess
import sys
from pathlib import Path

import pytest

import exec_audit

REPO = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def _isolated_ledger():
    """Run a control without its planted violation failing the session."""
    saved, repeats = list(exec_audit.VIOLATIONS), dict(exec_audit.REPEATS)
    exec_audit.VIOLATIONS.clear()
    exec_audit.REPEATS.clear()
    try:
        yield exec_audit.VIOLATIONS
    finally:
        exec_audit.VIOLATIONS.clear()
        exec_audit.VIOLATIONS.extend(saved)
        exec_audit.REPEATS.clear()
        exec_audit.REPEATS.update(repeats)


def _spawn(argv: list[str]) -> None:
    """Raise the audit event and get out.

    The event fires inside `Popen.__init__`, before the fork, so a name that
    does not exist still exercises the detector without running anything.
    """
    with contextlib.suppress(FileNotFoundError, OSError):
        subprocess.run(argv, capture_output=True, timeout=5)  # noqa: S603


# ── the ratchet ────────────────────────────────────────────────────────────


def test_no_bare_executable_name_reached_the_kernel():
    """The measurement ADR-064, ADR-069 and ADR-070 could only bound.

    Those three read source text and closed on a floor. This one reports what
    was actually handed to the kernel, through a dataclass field, a dict, a
    reshaping function or any other shape no static scan enumerates.

    `pytest_sessionfinish` fails the run too, and is the authority — it sees
    execs that happen after this test. This assertion exists so the failure has
    a name and a location when the offender ran early.
    """
    assert exec_audit.VIOLATIONS == [], "\n" + exec_audit.report()


def test_the_audit_hook_is_installed_for_the_whole_session():
    assert exec_audit.is_installed(), (
        "conftest.py did not install the exec audit, so every other assertion "
        "in this file is vacuous"
    )


def test_the_audit_is_installed_from_the_rootdir_conftest():
    """Not from `tests/conftest.py`, which is loaded only when collection
    reaches that directory — an audit the invocation can leave uninstalled
    reports zero for the same reason a clean tree does."""
    assert "exec_audit.install()" in (REPO / "conftest.py").read_text(), (
        "the rootdir conftest no longer installs the exec audit"
    )
    assert "exec_audit.install()" not in (REPO / "tests" / "conftest.py").read_text()


def test_the_audit_observed_real_execs_rather_than_nothing():
    """A session that saw zero spawns measured nothing."""
    before = exec_audit.OBSERVED[0]
    _spawn([sys.executable, "-c", ""])
    assert exec_audit.OBSERVED[0] > before, (
        "the hook is installed but did not fire for a subprocess this test "
        "just spawned — the ledger's zero is an absence of measurement, not a "
        "clean result"
    )


# ── controls ───────────────────────────────────────────────────────────────


def test_the_audit_catches_a_bare_name_handed_over_by_one_of_our_own_frames():
    """The detector's positive direction, proven from a frame inside `tests/`.

    The head is *concatenated* on purpose. `S607`, ADR-064's ratchet,
    ADR-069's wrapper scan and ADR-070's binder all need a string literal to
    match, and there is none here — which is exactly the class this instrument
    exists to cover, demonstrated rather than described.
    """
    head = "definitely" + "-not-a-real-binary"
    with _isolated_ledger() as ledger:
        _spawn([head])
        assert len(ledger) == 1, f"expected one violation, got {ledger}"
        recorded, chain = ledger[0]
        assert recorded == head
        assert chain[0][0] == __file__, "the accusation must name the frame that spawned"
        assert chain[0][1] > 0
        assert chain[0][2] == "_spawn"


def test_the_accusation_names_the_caller_and_not_just_the_wrapper():
    """ADR-069 exists because a hop hid the offender. A report that stops at
    the nearest frame recreates exactly that: argv routed through
    `testing._run` would be blamed on `_run`'s own `subprocess` line, which is
    true, unactionable, and points at code that is not wrong.
    """

    def _caller_that_chose_a_bare_name():
        _spawn(["definitely" + "-not-a-real-binary"])

    with _isolated_ledger() as ledger:
        _caller_that_chose_a_bare_name()
        _head, chain = ledger[0]
        functions = [func for _f, _l, func in chain]
        assert functions[0] == "_spawn"
        assert "_caller_that_chose_a_bare_name" in functions, (
            f"the chain stopped at the wrapper: {functions}"
        )


def test_repeats_are_counted_rather_than_listed():
    """One unconverted `git` inside `testing._run` fires 3,788 times in a full
    run. Listing each buries every other offender."""
    with _isolated_ledger() as ledger:
        head = "definitely" + "-not-a-real-binary"
        for _ in range(3):
            _spawn([head])
        assert len(ledger) == 1, "the same site was listed more than once"
        assert exec_audit.REPEATS[(head, tuple(ledger[0][1]))] == 3
        assert "(×3)" in exec_audit.report()


def test_a_resolved_absolute_path_is_not_an_accusation():
    with _isolated_ledger() as ledger:
        _spawn([sys.executable, "-c", ""])
        assert ledger == [], f"an absolute head was reported as bare: {ledger}"


def test_the_stdlibs_own_bare_execs_are_not_attributed_to_us():
    """`platform.architecture()` shells out to a bare `file`, every call.

    It is reached from a frame in *this* file, so a detector asking "is any
    frame in the stack ours?" would report it. The question asked is "which
    frame chose the argv", and the answer is CPython's `platform.py`.

    An allowlist of `{"file", "uname"}` would have silenced this too — and
    would have gone on silencing it if our own code ever ran `file`. That is
    the ledger trap ADR-060 earned and ADR-068 reused, which is why the
    scoping is by frame and there is no allowlist anywhere in `exec_audit`.
    """
    with _isolated_ledger() as ledger:
        before = exec_audit.OBSERVED[0]
        platform.architecture()
        assert exec_audit.OBSERVED[0] > before, (
            "platform.architecture() no longer shells out on this platform, so "
            "this control proves nothing and needs a different stdlib caller"
        )
        assert ledger == [], f"a stdlib exec was blamed on us: {ledger}"


def test_a_relative_path_is_left_to_the_static_scans():
    """`./foo` is not the class this hook is for, and it says so by not firing.

    ADR-064's carve-outs and `lanes/botfleet.py`'s documented cwd-relative
    command live here. Recording them would make this detector disagree with
    the four that came before it, and a ratchet that disagrees gets turned off.
    """
    with _isolated_ledger() as ledger:
        _spawn(["./definitely-not-here"])
        assert ledger == [], f"a relative head was reported as bare: {ledger}"


def test_the_hook_ignores_every_event_that_is_not_a_spawn():
    """The fast path. This hook is called for `open`, `import` and `compile`
    too, which is why the first line rejects them and why installing it costs
    +0.6% rather than something worth arguing about."""
    before = exec_audit.OBSERVED[0]
    (REPO / "pyproject.toml").read_text()
    assert exec_audit.OBSERVED[0] == before


def test_a_failure_inside_the_hook_is_recorded_rather_than_swallowed():
    """A detector that fails silently reports zero for the same reason a clean
    tree does. If the audit event's shape ever changes under us, the ledger
    says so instead of going quiet."""
    with _isolated_ledger() as ledger:
        exec_audit._hook("subprocess.Popen", (None,))  # no argv at args[1]
        assert len(ledger) == 1
        assert "audit hook failed" in ledger[0][0]
        assert exec_audit.report()  # and it renders rather than raising again


@pytest.mark.parametrize("head", ["/usr/bin/git", "./rel", "a/b"])
def test_anything_with_a_separator_is_someone_elses_problem(head):
    with _isolated_ledger() as ledger:
        _spawn([head])
        assert ledger == []


def test_the_report_names_the_offender_and_the_way_out():
    with _isolated_ledger() as ledger:
        ledger.append(("npm", [(str(REPO / "src" / "x.py"), 12, "build")]))
        text = exec_audit.report()
    assert "'npm'" in text
    assert "src/x.py:12" in text
    assert "executables" in text
