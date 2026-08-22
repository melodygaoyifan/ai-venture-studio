"""PC-1 must be falsifiable by the machine, not by someone remembering.

The gap this closes: `test_editions_platform.py` enforces the ledger against
OVERclaiming only — every README number must resolve to a claim — while
nothing measured the suite. So an UNDERSTATED PC-1 stayed green through
several commits and a release, sitting at 1572 while the suite passed 1655.
"""
from __future__ import annotations

import textwrap

from claim_count import (
    claimed_test_count, is_whole_suite_run, verdict,
)


def _ledger(tmp_path, body: str):
    path = tmp_path / "platform.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_the_claimed_count_is_read_from_pc1(tmp_path):
    path = _ledger(tmp_path, """
        claims:
          - id: PC-0
            n: 7
          - id: PC-1
            text: "1655 hermetic tests pass"
            n: 1655
    """)
    assert claimed_test_count(path) == 1655


def test_a_missing_or_unreadable_ledger_is_none_never_a_crash(tmp_path):
    assert claimed_test_count(tmp_path / "nope.yaml") is None
    assert claimed_test_count(_ledger(tmp_path, "claims: []")) is None
    assert claimed_test_count(_ledger(tmp_path, "{[not yaml")) is None
    # A PC-1 with no numeric n cannot be measured against anything.
    assert claimed_test_count(_ledger(tmp_path, """
        claims:
          - id: PC-1
            n: "about 1600"
    """)) is None


def test_an_understated_claim_is_caught_and_names_both_numbers():
    """The exact shape of the miss: fewer claimed than really pass."""
    problem = verdict(passed=1655, claimed=1572)
    assert problem and "understates" in problem
    assert "1572" in problem and "1655" in problem
    assert "README" in problem, "say every place the number has to change"


def test_an_overstated_claim_is_caught_too():
    problem = verdict(passed=1600, claimed=1655)
    assert problem and "overstates" in problem


def test_a_matching_claim_is_silent():
    assert verdict(passed=1655, claimed=1655) is None


def test_a_missing_pc1_is_itself_the_problem():
    problem = verdict(passed=1655, claimed=None)
    assert problem and "missing" in problem


def test_only_an_unfiltered_whole_suite_run_is_judged():
    """Subsets are what everyone runs all day; comparing a handful of tests
    against the whole suite's claim would fail for no reason. Measured: a bare
    `uv run pytest` arrives with args == [rootdir], a file run names the file."""
    root = "/repo"
    assert is_whole_suite_run([root], "", "", root)
    assert is_whole_suite_run([], "", "", root)
    assert is_whole_suite_run(["."], "", "", root)

    assert not is_whole_suite_run(["tests/test_x.py"], "", "", root)
    assert not is_whole_suite_run([root], "some_keyword", "", root)
    assert not is_whole_suite_run([root], "", "not slow", root)


def test_the_shipped_ledger_agrees_with_its_own_readme_prose():
    """PC-1's n and the number written into its text must be the same number —
    a release updates both or the ledger contradicts itself."""
    import re
    from claim_count import LEDGER
    import yaml

    data = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    pc1 = [c for c in data["claims"] if c["id"] == "PC-1"][0]
    in_text = re.search(r"(\d[\d,]*)", pc1["text"])
    assert in_text, pc1["text"]
    assert int(in_text.group(1).replace(",", "")) == pc1["n"], (
        "PC-1's prose and its n disagree: " + pc1["text"]
    )


# --- the wiring, which is what actually failed first ---------------------

class _Reporter:
    def __init__(self, passed):
        self.stats = {"passed": [None] * passed}
        self.lines = []

    def write_line(self, line, **kw):
        self.lines.append(line)


class _Session:
    def __init__(self, passed, root="/repo"):
        reporter = _Reporter(passed)

        class _Opt:
            keyword = ""
            markexpr = ""

        class _PM:
            def get_plugin(self, name):
                return reporter if name == "terminalreporter" else None

        class _Config:
            args = [root]
            option = _Opt()
            rootdir = root
            pluginmanager = _PM()

        self.config = _Config()
        self.exitstatus = 0
        self.reporter = reporter


def _root_conftest():
    """The hook must live in the ROOT conftest: pytest invokes
    pytest_sessionfinish only from there (or a plugin), never from a
    subdirectory conftest. Written into tests/conftest.py first, it silently
    never ran — the suite passed 1667 against a PC-1 of 1655 and said nothing,
    the same quiet non-enforcement this check exists to end."""
    import importlib.util
    from claim_count import LEDGER

    path = LEDGER.parent.parent / "conftest.py"
    assert path.is_file(), "the root conftest.py is where the hook has to live"
    spec = importlib.util.spec_from_file_location("_root_conftest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_hook_lives_in_the_root_conftest_where_pytest_calls_it():
    """Every session-scoped hook in one place, which is the rootdir conftest.

    The reason recorded here used to be that a subdirectory conftest's
    `pytest_sessionfinish` "would never be called". Measured on pytest 9.1.1
    while adding the ADR-071 audit, that is false: it fires. The rule is kept
    on the reason that actually holds — a subdirectory conftest is loaded only
    when collection reaches that directory, so a session-wide check living
    there is conditional on how the run was invoked, and a check the
    invocation can switch off is not a check.
    """
    assert hasattr(_root_conftest(), "pytest_sessionfinish")

    # Checked as text: importing a conftest by hand gives it a second module
    # identity alongside the one pytest already loaded, which is its own trap.
    from pathlib import Path

    here = Path(__file__).parent / "conftest.py"
    assert "def pytest_sessionfinish" not in here.read_text(encoding="utf-8"), (
        "session hooks belong in the rootdir conftest, where no choice of "
        "pytest arguments can leave them unloaded"
    )


def test_a_release_run_fails_on_drift(monkeypatch):
    from claim_count import RELEASE_ENV, claimed_test_count

    monkeypatch.setenv(RELEASE_ENV, "1")
    session = _Session(passed=(claimed_test_count() or 0) + 5)

    _root_conftest().pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1, "a released number that is fiction must fail"
    assert any("SELF-CHECK FAILED" in line for line in session.reporter.lines)


def test_an_ordinary_run_reports_drift_without_failing(monkeypatch):
    from claim_count import RELEASE_ENV, claimed_test_count

    monkeypatch.delenv(RELEASE_ENV, raising=False)
    session = _Session(passed=(claimed_test_count() or 0) + 5)

    _root_conftest().pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0, "main drifting between releases is normal"
    assert any("drift" in line for line in session.reporter.lines)


def test_a_matching_claim_says_nothing_at_all(monkeypatch):
    from claim_count import RELEASE_ENV, claimed_test_count

    monkeypatch.setenv(RELEASE_ENV, "1")
    session = _Session(passed=claimed_test_count() or 0)

    _root_conftest().pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0
    assert session.reporter.lines == []


def test_an_already_failing_suite_is_not_second_guessed(monkeypatch):
    """A red suite has a louder problem than a stale claim; don't bury it."""
    from claim_count import RELEASE_ENV

    monkeypatch.setenv(RELEASE_ENV, "1")
    session = _Session(passed=1)

    _root_conftest().pytest_sessionfinish(session, 1)   # non-zero exitstatus

    assert session.reporter.lines == []
