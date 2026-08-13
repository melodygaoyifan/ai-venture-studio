"""Run-4 case-03 regressions: the boot contract, failure forensics, and
workspace hygiene (see .mas/product-bench forensics, 2026-07-26)."""

import shutil
import subprocess

import pytest

from ai_venture_studio.upstream.build import (
    _blocks_on_import,
    _boot_gate,
    _preserve_failed_attempt,
    _reset_workspace,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

_SERVER = """\
import os, socket, time
s = socket.socket()
s.bind(("127.0.0.1", int(os.environ["PORT"])))
s.listen()
time.sleep(30)
"""


def test_boot_gate_passes_when_entry_serves_on_port(tmp_path):
    (tmp_path / "main.py").write_text(_SERVER)
    assert _boot_gate(tmp_path) is None


def test_boot_gate_fails_when_entry_exits_without_serving(tmp_path):
    """Idiomatic FastAPI module without a __main__ block: defines app,
    exits — run 4's 'server never listened' on every probe."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("app = object()\n")
    failure = _boot_gate(tmp_path)
    assert failure is not None and "BOOT GATE" in failure and "exited" in failure
    assert "__main__" in failure  # feedback teaches the fix, not just the fact


def test_boot_gate_skips_when_no_entry_point(tmp_path):
    (tmp_path / "lib.py").write_text("x = 1\n")
    assert _boot_gate(tmp_path) is None


# --- the hang run 12 could not explain, answered from a parse ---------------
#
# The boot contract ("`python main.py` must serve") is satisfied by a
# module-level `uvicorn.run(app)` — and that same line makes every test that
# imports the module block forever. The suite then burns the full timeout and
# reports only that time passed. Cheap to check statically; the dynamic
# version of the check IS the hang.


def test_a_module_that_serves_on_import_is_rejected_before_the_suite_runs(tmp_path):
    (tmp_path / "main.py").write_text(
        'import os, uvicorn\napp = object()\n'
        'uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]))\n'
    )
    failure = _blocks_on_import(tmp_path)
    assert failure is not None and "IMPORT GATE" in failure
    assert "line 3" in failure                 # points at the line, not the file
    assert '__main__' in failure               # and teaches the fix


def test_the_contract_form_passes_the_import_gate(tmp_path):
    """Exactly what the profile's boot contract tells the model to write."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text(
        'import os\napp = object()\n'
        'if __name__ == "__main__":\n'
        '    import uvicorn\n'
        '    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))\n'
    )
    assert _blocks_on_import(tmp_path) is None


def test_the_import_gate_does_not_flag_ordinary_module_level_calls(tmp_path):
    """`.run` is everywhere. Only the known server objects block on it."""
    (tmp_path / "main.py").write_text(
        "import subprocess\n"
        "VERSION = subprocess.run(['git', 'describe'], capture_output=True)\n"
        "def serve():\n"
        "    app.run()\n"
    )
    assert _blocks_on_import(tmp_path) is None


def test_a_non_web_product_that_blocks_on_import_is_rejected_too(tmp_path):
    """v0.85.0 ran this gate only for web/enterprise-web, reasoning that only
    they carry the boot contract. But the contract is what makes a web product
    PRONE to the shape, not what makes the hang possible: `data` is "Python +
    the team's existing warehouse/orchestrator", and a module-level
    `run_forever()` there hangs its suite identically. `_BLOCKING_SERVE` could
    already name that call while the gate never ran over it."""
    (tmp_path / "pipeline.py").write_text(
        "import scheduler\nsched = scheduler.Scheduler()\nsched.run_forever()\n"
    )
    failure = _blocks_on_import(tmp_path, "data")
    assert failure is not None and "IMPORT GATE" in failure
    assert "line 3" in failure


def test_a_non_web_product_is_not_told_to_add_uvicorn(tmp_path):
    """The fix sentence is the only thing the profile picks. Telling a data
    pipeline to append `uvicorn.run(app)` would be advice that cannot apply,
    and feedback the model cannot act on is feedback that loops."""
    (tmp_path / "pipeline.py").write_text("import x\nx.mainloop()\n")
    failure = _blocks_on_import(tmp_path, "data")
    assert failure is not None
    # "PORT" alone is a substring of "IMPORT GATE" — match the contract's
    # own phrase, not a fragment that the heading happens to contain.
    assert "uvicorn" not in failure and "PORT env var" not in failure
    assert "__main__" in failure  # still teaches the fix

    (tmp_path / "pipeline.py").unlink()
    (tmp_path / "main.py").write_text("import uvicorn\napp = object()\nuvicorn.run(app)\n")
    web = _blocks_on_import(tmp_path, "web")
    assert web is not None and "uvicorn" in web  # web keeps the boot-contract hint


def test_boot_gate_leaves_no_worker_serving_behind_it(tmp_path):
    """The gate boots a process TREE — uvicorn reloaders and worker pools
    fork. Killing the parent alone hands the test run that follows a live
    server it never started, on a port it does not know about."""
    (tmp_path / "main.py").write_text(
        "import os, socket, subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', "
        '"import time; time.sleep(90)  # pretend-worker"])\n'
        + _SERVER
    )
    assert _boot_gate(tmp_path) is None  # the parent did listen

    survivors = subprocess.run(
        ["pgrep", "-f", "pretend-worker"], capture_output=True, text=True
    )
    assert "pretend-worker" not in survivors.stdout, (
        "a forked worker outlived the boot gate and is still holding its port"
    )


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@local", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )


def test_preserve_and_reset_after_failed_in_place_build(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text(".mas/\n")  # as init_workspace writes
    (tmp_path / "a.py").write_text("original\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    pre_existing = {"a.py"}

    # A failed attempt's residue: modified tracked file + new files.
    (tmp_path / "a.py").write_text("dirtied by failed attempt\n")
    (tmp_path / "new_module").mkdir()
    (tmp_path / "new_module" / "b.py").write_text("residue\n")

    preserved = _preserve_failed_attempt(tmp_path, "some-slug")
    _reset_workspace(tmp_path, pre_existing)

    keep = tmp_path / preserved
    assert (keep / "a.py").read_text() == "dirtied by failed attempt\n"
    assert (keep / "new_module" / "b.py").read_text() == "residue\n"
    # Workspace is clean again: tracked restored, residue gone (dir too).
    assert (tmp_path / "a.py").read_text() == "original\n"
    assert not (tmp_path / "new_module").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path,
        capture_output=True, text=True,
    ).stdout
    assert status.strip() == ""


def test_web_profile_carries_scope_law_and_boot_contract():
    from ai_venture_studio.upstream.workspace import load_profile

    profile = load_profile("web")
    text = " ".join(profile["constraints"])
    assert "scope is law" in text
    assert "Where the product includes accounts or login" in text
    assert "BOOT CONTRACT" in profile["stack_hint"]
    assert "PORT" in profile["stack_hint"]
    assert "never crash on user input" in text
    assert "human-readable message" in text
    assert "Where the product fetches or redirects" in text
    assert "never as a separate task" in text


def test_implementer_receives_the_literal_source_contract(tmp_path, monkeypatch):
    """Contract drift migrated to the implementer once specs held it (live
    post-fix test: the scores handler invented "index" for the FDR's
    "item" and every probe died) — the implementer prompt must carry the
    FDR verbatim, via the workspace FDR.md fallback."""
    import ai_venture_studio.upstream.build as build_mod
    from ai_venture_studio.upstream import approve_spec, init_workspace, run_spec_stage

    root = init_workspace(tmp_path / "p", "p", "web")
    spec = run_spec_stage(root, "an item store API", provider="mock")
    approve_spec(root, spec.slug)
    contract = 'scores use the field name "item" and rounds "day5"/"day12"'
    (root / "FDR.md").write_text(contract, encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "conftest.py").write_text(
        "def marker_fixture():\n    return 1\n", encoding="utf-8"
    )

    seen = []

    class Stub:
        def complete(self, **kwargs):
            seen.append(kwargs)
            return "not: [parseable"

    monkeypatch.setattr(build_mod, "get_provider", lambda name: Stub())
    result = build_mod.run_build(root, spec.slug, provider="stub")
    assert result.status == "error"
    prompt = seen[0]["user"]
    assert "<source_contract>" in prompt and '"item"' in prompt
    assert "LITERAL" in seen[0]["system"] and "4xx" in seen[0]["system"]
    assert "additively" in seen[0]["system"] and "tests-only" in seen[0]["system"]
    assert "shared_test_fixtures" in prompt and "def marker_fixture" in prompt


def test_shared_test_fixtures_are_additive_only(tmp_path):
    """conftest/helpers under tests/ are a vocabulary sibling tasks import —
    a rewrite that drops a name is kept out (run 7, case 04: a conftest
    rewrite lost post_json and three straight iterations died on
    ImportError before any test ran)."""
    from ai_venture_studio.upstream.build import _write_files

    (tmp_path / "tests").mkdir()
    conftest = tmp_path / "tests" / "conftest.py"
    conftest.write_text("def post_json(base, path, body):\n    return 1\n")

    # Parallel-vocabulary rewrite dropping post_json: kept out, original intact.
    written, kept = _write_files(
        tmp_path,
        [{"path": "tests/conftest.py",
          "new_content": "def http(base):\n    return 2\n"}],
        allowed_test_paths=set(),
    )
    assert written == [] and len(kept) == 1 and "post_json" in kept[0]
    assert "post_json" in conftest.read_text()

    # Additive extension: written.
    written, kept = _write_files(
        tmp_path,
        [{"path": "tests/conftest.py",
          "new_content": "def post_json(base, path, body):\n    return 1\n\n"
                         "def http(base):\n    return 2\n"}],
        allowed_test_paths=set(),
    )
    assert written == ["tests/conftest.py"] and kept == []


def test_private_names_are_not_vocabulary(tmp_path):
    """A helpers rewrite may restructure _private internals freely — only
    public names sibling tests can import are guarded (run 8, case 01 t3:
    the guard blocked a rewrite over reshuffled _check_url/_do)."""
    from ai_venture_studio.upstream.build import _write_files

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "helpers.py").write_text(
        "def _check_url(u):\n    return u\n\ndef post(base):\n    return 1\n"
    )
    written, kept = _write_files(
        tmp_path,
        [{"path": "tests/helpers.py",
          "new_content": "def post(base):\n    return 1\n\ndef http_post(base):\n    return 2\n"}],
        allowed_test_paths=set(),
    )
    assert written == ["tests/helpers.py"] and kept == []
    # Dropping a PUBLIC name still keeps the file out.
    written, kept = _write_files(
        tmp_path,
        [{"path": "tests/helpers.py",
          "new_content": "def only_new(base):\n    return 3\n"}],
        allowed_test_paths=set(),
    )
    assert written == [] and "post" in kept[0]


def test_stale_import_note_names_phantom_imports(tmp_path):
    """Files persisting across iterations that import names which don't
    exist get a precise deterministic callout in the feedback."""
    from ai_venture_studio.upstream.build import _stale_import_note

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "helpers.py").write_text("def post(base):\n    return 1\n")
    (tmp_path / "tests" / "test_ok.py").write_text("from tests.helpers import post\n")
    assert _stale_import_note(tmp_path) == ""
    (tmp_path / "tests" / "test_stale.py").write_text(
        "from tests.helpers import http_post\n"
    )
    note = _stale_import_note(tmp_path)
    assert "test_stale.py" in note and "http_post" in note and "post" in note
    assert "test_ok.py" not in note


def test_probegen_dry_case_is_visibly_unmeasured(tmp_path, monkeypatch):
    """Zero generated probes must not read as a silently-scored 0% — run 9
    case 03 had 3/4 built and NO behavioral measurement at all."""
    from ai_venture_studio import product_bench
    from ai_venture_studio.upstream import probegen as probegen_mod

    calls = []
    monkeypatch.setattr(
        probegen_mod, "generate_probes",
        lambda ws, provider="anthropic": (calls.append(1), ([], ["why-note"]))[1],
    )
    case = product_bench.ProductCase(
        name="dry", fdr="a link sharing tool", auto_probes=True
    )
    result = product_bench.run_case(case, provider="mock", keep_dir=tmp_path)
    assert len(calls) == 2  # one retry before declaring dry
    synthetic = [p for p in result.probes if p.name == "probe-generation"]
    assert len(synthetic) == 1 and not synthetic[0].passed
    assert "UNMEASURED" in synthetic[0].detail and "why-note" in synthetic[0].detail


def test_save_summary_dual_writes_to_tracked_results(tmp_path):
    """.mas/ is gitignored and was lost once (2026-07-26, the whole bench
    history) — scoreboards also land in tracked benchmarks/results/."""
    from ai_venture_studio.product_bench import BenchSummary, save_summary

    (tmp_path / "benchmarks").mkdir()
    summary = BenchSummary(
        cases=[], build_rate=0.5, probe_pass_rate=0.5, clean_review_rate=0.5
    )
    path = save_summary(summary, tmp_path)
    assert path.exists()
    tracked = tmp_path / "benchmarks" / "results" / path.name
    assert tracked.exists() and tracked.read_text() == path.read_text()


def test_probegen_falls_back_to_source_literals_and_fdr(tmp_path, monkeypatch):
    """Stdlib http.server products route by hand — invisible to
    collect_routes, which left bench case 03 UNMEASURED for five runs.
    With an entry point present, generation proceeds on scraped path
    literals + the FDR instead of bailing before the model call."""
    import ai_venture_studio.upstream.probegen as probegen_mod

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text(
        'import re\n_R = re.compile(r"^[0-9]+$")\n'
        'def route(p):\n    return p.rstrip("/") == "/groups"\n'
    )
    (tmp_path / "FDR.md").write_text("POST /api/groupbuys {\"title\", \"price\"}")

    seen = []

    class Stub:
        def complete(self, **kwargs):
            seen.append(kwargs)
            return 'probes:\n  - name: "smoke"\n    body: "assert True"\n'

    monkeypatch.setattr(probegen_mod, "get_provider", lambda name: Stub())
    probes, notes = probegen_mod.generate_probes(tmp_path, provider="stub")
    assert len(probes) == 1 and probes[0].name == "smoke"
    assert "/groups" in seen[0]["user"]              # scraped literal offered
    assert "authoritative" in seen[0]["user"]        # route_note present

    # No entry point at all → still bails, visibly.
    (tmp_path / "app" / "main.py").unlink()
    probes, notes = probegen_mod.generate_probes(tmp_path, provider="stub")
    assert probes == [] and any("no entry point" in n for n in notes)
