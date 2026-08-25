"""Bench run 19, case 05 t5, the other half: `built` citing tests nobody wrote.

On an established product the pre-existing suite keeps the build gate green
even when the implementer writes no tests at all — t5 shipped `app/db.py` and
`app/handler.py` on 39 old green tests — and `sync_ledger` then cites the
spec's declared-but-absent skeleton paths as `verified_by`: five
`tests/test_complete_*.py` rows whose proof never existed on disk. The build
gate now refuses to save until every spec-declared test file exists, and the
feedback names the missing files so the next iteration writes them.
"""

from __future__ import annotations

import subprocess

import yaml

from ai_venture_studio.executables import resolve
from ai_venture_studio.upstream import approve_spec, run_build
from ai_venture_studio.upstream.spec import run_spec_stage
from ai_venture_studio.upstream.workspace import init_workspace


def test_a_missing_declared_test_file_blocks_the_save_and_names_itself(
    tmp_path, monkeypatch
):
    import ai_venture_studio.upstream.build as build_mod
    from ai_venture_studio import testing as testing_mod
    from ai_venture_studio.providers.base import Provider, register
    from ai_venture_studio.providers.mock import MockProvider
    from ai_venture_studio.upstream.build import IMPLEMENTER_MARKER

    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    monkeypatch.setattr(build_mod, "docker_available", lambda: False)

    implementer_prompts: list[str] = []

    @register
    class WithholdsTestsOnce(Provider):
        name = "withholds_tests_once"

        def chat(self, *, model, system, messages, max_tokens=4096):
            answer = MockProvider().chat(
                model=model, system=system, messages=messages
            )
            if IMPLEMENTER_MARKER not in system:
                return answer
            implementer_prompts.append(messages[0]["content"])
            if len(implementer_prompts) == 1:
                # The run-19 shape: source only, no tests — and nothing else
                # in the product to make the suite red about it.
                data = yaml.safe_load(answer)
                data["files"] = [
                    f for f in data["files"] if "test" not in f["path"]
                ]
                return yaml.safe_dump(data, sort_keys=False)
            return answer

    root = init_workspace(tmp_path / "p", "p", "web")
    # An established product: a pre-existing green test keeps the suite
    # from ever reporting "no tests collected" on the withheld iteration.
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_existing.py").write_text(
        "def test_already_green():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(
        [resolve("git"), "add", "-A"], cwd=root, check=True,
        capture_output=True, timeout=60,
    )
    spec = run_spec_stage(root, "an item store API", provider="mock")
    approve_spec(root, spec.slug)
    skeleton_paths = [s.path for s in spec.test_skeletons]
    assert skeleton_paths, "the mock spec must declare skeletons for this test"

    result = run_build(root, spec.slug, provider="withholds_tests_once")

    assert len(implementer_prompts) >= 2, (
        "the gate must have sent the implementer back for the missing files"
    )
    followup = implementer_prompts[1]
    assert "do not exist on disk" in followup
    for rel in skeleton_paths:
        assert rel in followup
    assert result.status == "built"
    for rel in skeleton_paths:
        assert (root / rel).exists(), (
            "a built spec's declared proof must exist on disk"
        )
