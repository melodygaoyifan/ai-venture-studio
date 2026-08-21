import pytest
import yaml

from ai_venture_studio.harness import SpecValidator, VoterSpecValidationError
from ai_venture_studio.providers.base import Provider, register
from ai_venture_studio.state import VoterStatus
from ai_venture_studio.tools.voter_tools import ToolBox
from ai_venture_studio.voters.base import Voter

# --- ToolBox ----------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "utils.py").write_text(
        "def helper(a, b):\n    return a + b\n"
    )
    (tmp_path / "app" / "orders.py").write_text(
        "from app.utils import helper\n\ntotal = helper(1, 2)\n"
    )
    return tmp_path


def test_read_file_numbers_lines(repo):
    box = ToolBox(repo, ["read_file"])
    out = box.call("read_file", {"path": "app/utils.py"})
    assert out.startswith("1\tdef helper(a, b):")


def test_path_traversal_blocked(repo):
    box = ToolBox(repo, ["read_file"])
    out = box.call("read_file", {"path": "../../etc/passwd"})
    assert "escapes the repository root" in out


def test_grep_finds_callers(repo):
    box = ToolBox(repo, ["grep"])
    out = box.call("grep", {"pattern": r"helper\("})
    assert "app/orders.py:3" in out
    assert "app/utils.py:1" in out


def test_allowlist_enforced(repo):
    box = ToolBox(repo, ["read_file"])
    out = box.call("grep", {"pattern": "x"})
    assert "not in your allowlist" in out


def test_budget_enforced(repo):
    from ai_venture_studio.tools.voter_tools import ToolBudgetExceeded

    box = ToolBox(repo, ["read_file"], budget=1)
    box.call("read_file", {"path": "app/utils.py"})
    with pytest.raises(ToolBudgetExceeded):
        box.call("read_file", {"path": "app/utils.py"})


# --- Voter investigation loop -------------------------------------------------


@register
class ScriptedProvider(Provider):
    """Replays a canned conversation: first requests a grep, then reports a
    finding grounded in the tool result it received."""

    name = "scripted"
    transcript: list[list[dict]] = []

    def chat(self, *, model, system, messages, max_tokens=4096):
        ScriptedProvider.transcript.append(messages)
        if len(messages) == 1:
            return yaml.safe_dump(
                {"tool_request": {"tool": "grep", "args": {"pattern": r"helper\("}}}
            )
        assert "<tool_result" in messages[-1]["content"]
        return yaml.safe_dump(
            {
                "status": "OK",
                "findings": [
                    {
                        "title": "Caller not updated for new signature",
                        "severity": "high",
                        "confidence": "certain",
                        "file_path": "app/orders.py",
                        "line_start": 3,
                        "line_end": 3,
                        "evidence": "total = helper(1, 2)",
                        "explanation": "grep showed a caller outside the diff",
                        "taxonomy_hint": "P8",
                    }
                ],
            }
        )


def _tooled_skill(tmp_path):
    path = tmp_path / "tooled.md"
    path.write_text(
        "---\nname: tooled\ndescription: d\nprovider: mock\nmodel: m\n"
        "tools: [grep, read_file]\ntool_budget: 3\n---\nInvestigate then judge.\n"
    )
    return SpecValidator().load(path)


def test_voter_tool_roundtrip(repo, tmp_path):
    ScriptedProvider.transcript = []
    voter = Voter(_tooled_skill(tmp_path), provider_override="scripted")
    output = voter.run("(diff)", repo_dir=str(repo))
    assert output.status is VoterStatus.OK
    assert output.findings[0].file_path == "app/orders.py"
    # Second turn carried the actual grep hits back to the model.
    final_messages = ScriptedProvider.transcript[-1]
    assert "app/orders.py:3" in final_messages[-1]["content"]


@register
class FlakyEmptyProvider(Provider):
    """First reply empty, then a valid envelope after the nudge."""

    name = "flaky_empty"
    calls = 0

    def chat(self, *, model, system, messages, max_tokens=4096):
        FlakyEmptyProvider.calls += 1
        if FlakyEmptyProvider.calls == 1:
            return "   "
        assert "previous reply was empty" in messages[-1]["content"]
        return yaml.safe_dump({"status": "OK", "findings": []})


def test_empty_response_nudged_once_then_recovers(tmp_path):
    FlakyEmptyProvider.calls = 0
    path = tmp_path / "plain.md"
    path.write_text(
        "---\nname: plain\ndescription: d\nprovider: mock\nmodel: m\n---\nJudge.\n"
    )
    voter = Voter(SpecValidator().load(path), provider_override="flaky_empty")
    output = voter.run("(diff)")
    assert output.status is VoterStatus.OK
    assert FlakyEmptyProvider.calls == 2


def test_unknown_tool_in_spec_rejected(tmp_path):
    path = tmp_path / "bad.md"
    path.write_text(
        "---\nname: bad\ndescription: d\nprovider: mock\nmodel: m\n"
        "tools: [run_shell]\n---\nbody\n"
    )
    with pytest.raises(VoterSpecValidationError, match="unknown tools"):
        SpecValidator().load(path)


# --- The unquoted-glob block (ADR-058) ---------------------------------------
#
# Run 18 lost twelve of its seventeen blocked votes to one YAML rule. A voter
# asked for a tool with `glob: **/*.py` unquoted; that is a scanner error, so
# `_tool_request` returned None; None also means "not a tool request", so the
# investigation turn was handed to the verdict parser, which raised and burned
# every retry re-sending an identical prompt. Two such blocks on one task is
# `len(blocked) == 2`, which the leader reads as REQUEST_CHANGES — a rejection
# no voter had objected to. These tests hold each link of that chain.

RUN_18_MALFORMED = (
    "tool_request:\n"
    "  tool: grep\n"
    "  args: {pattern: \"def cancel\", glob: **/*.py}\n"
)


def test_the_run_18_request_really_is_unparseable():
    """The premise, not an assumption: this payload cannot be read."""
    from ai_venture_studio.yamlx import extract_mapping

    with pytest.raises(ValueError):
        extract_mapping(RUN_18_MALFORMED, ("tool_request",))
    # ...and the only difference is the quotes.
    quoted = RUN_18_MALFORMED.replace("**/*.py", '"**/*.py"')
    assert extract_mapping(quoted, ("tool_request",))["tool_request"]["tool"] == "grep"


@register
class UnquotedGlobProvider(Provider):
    """The run-18 failure, then the corrected request after being told."""

    name = "unquoted_glob"
    calls = 0
    nudge = ""

    def chat(self, *, model, system, messages, max_tokens=4096):
        UnquotedGlobProvider.calls += 1
        if UnquotedGlobProvider.calls == 1:
            return RUN_18_MALFORMED
        if UnquotedGlobProvider.calls == 2:
            UnquotedGlobProvider.nudge = messages[-1]["content"]
            return yaml.safe_dump(
                {"tool_request": {"tool": "grep", "args": {"pattern": r"helper\("}}}
            )
        return yaml.safe_dump({"status": "OK", "findings": []})


def test_unquoted_glob_is_nudged_instead_of_blocking_the_voter(repo, tmp_path):
    UnquotedGlobProvider.calls = 0
    voter = Voter(_tooled_skill(tmp_path), provider_override="unquoted_glob")
    output = voter.run("(diff)", repo_dir=str(repo))
    assert output.status is VoterStatus.OK          # was BLOCKED_TOOL_FAILURE
    assert "quote" in UnquotedGlobProvider.nudge.lower()
    assert "**/*.py" in UnquotedGlobProvider.nudge  # names the actual rule
    assert UnquotedGlobProvider.calls == 3          # nudge, tool call, verdict


@register
class AlwaysMalformedProvider(Provider):
    """Never takes the correction. The nudges must not run forever."""

    name = "always_malformed"
    calls = 0

    def chat(self, *, model, system, messages, max_tokens=4096):
        AlwaysMalformedProvider.calls += 1
        return RUN_18_MALFORMED


def test_requote_nudges_are_bounded(repo, tmp_path):
    AlwaysMalformedProvider.calls = 0
    voter = Voter(_tooled_skill(tmp_path), provider_override="always_malformed")
    output = voter.run("(diff)", repo_dir=str(repo))
    assert output.status is VoterStatus.BLOCKED_TOOL_FAILURE
    # Three calls per attempt (first + two nudges), and the retry loop is
    # bounded by max_retries. A voter that will not requote still terminates.
    assert AlwaysMalformedProvider.calls <= 3 * (voter.spec.max_retries + 1)


@register
class VerdictMentioningToolRequestProvider(Provider):
    """A real verdict that happens to contain the word `tool_request`.

    This is the case the fix must NOT capture: the nudge is for responses with
    no verdict to fall back on, so a readable verdict always wins.
    """

    name = "verdict_mentions_tool_request"
    calls = 0

    def chat(self, *, model, system, messages, max_tokens=4096):
        VerdictMentioningToolRequestProvider.calls += 1
        return (
            "I considered issuing a tool_request: **/*.py but did not need one.\n\n"
            + yaml.safe_dump({"status": "OK", "findings": []})
        )


def test_a_verdict_that_mentions_a_tool_request_is_still_a_verdict(repo, tmp_path):
    VerdictMentioningToolRequestProvider.calls = 0
    voter = Voter(
        _tooled_skill(tmp_path), provider_override="verdict_mentions_tool_request"
    )
    output = voter.run("(diff)", repo_dir=str(repo))
    assert output.status is VoterStatus.OK
    assert VerdictMentioningToolRequestProvider.calls == 1   # not nudged


def test_the_protocol_doc_states_the_quoting_rule():
    """Prevention, not only recovery: the voter is told before it guesses."""
    from ai_venture_studio.tools.voter_tools import TOOL_PROTOCOL_DOC

    assert "QUOTE EVERY PATTERN AND GLOB" in TOOL_PROTOCOL_DOC
