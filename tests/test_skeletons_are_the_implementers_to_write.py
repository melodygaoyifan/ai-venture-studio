"""Bench run 19, case 04: the implementer was told two opposite things.

The system prompt has always said "the test skeletons are the contract —
write them as real tests". The user prompt said "Your spec's skeleton tests
already exist ON DISK: do not resubmit them" — and nothing has EVER written
them: the spec stores only path/purpose/covers. Whichever instruction the
model happened to obey decided the task. 04-t1 and 04-t2 obeyed the user
prompt, submitted source only, and died on 'pytest collected no tests'
three attempts straight — with the feedback naming the symptom while the
prompt still forbade the fix. Both preserved failed-build workspaces have
no tests/ directory at all.

The prompt now tells the truth per path, checked against the disk.
"""

from __future__ import annotations

from ai_venture_studio.executables import resolve
from ai_venture_studio.upstream import approve_spec, run_build
from ai_venture_studio.upstream.spec import run_spec_stage
from ai_venture_studio.upstream.workspace import init_workspace


def _capture_implementer_prompt(tmp_path, *, pre_create: bool):
    """Run the build path with a provider that records the implementer's
    user prompt and then answers like the mock."""
    import subprocess

    from ai_venture_studio.providers.base import Provider, register
    from ai_venture_studio.providers.mock import MockProvider
    from ai_venture_studio.upstream.build import IMPLEMENTER_MARKER

    captured: list[str] = []

    @register
    class PromptRecorder(Provider):
        name = f"prompt_recorder_{pre_create}"

        def chat(self, *, model, system, messages, max_tokens=4096):
            if IMPLEMENTER_MARKER in system:
                captured.append(messages[0]["content"])
            return MockProvider().chat(
                model=model, system=system, messages=messages
            )

    root = init_workspace(tmp_path / "p", "p", "web")
    subprocess.run(
        [resolve("git"), "add", "-A"], cwd=root, check=True,
        capture_output=True, timeout=60,
    )
    spec = run_spec_stage(root, "an item store API", provider="mock")
    approve_spec(root, spec.slug)
    skeleton_paths = [s.path for s in spec.test_skeletons]
    assert skeleton_paths, "the mock spec must carry skeletons for this test"
    if pre_create:
        for rel in skeleton_paths:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def test_a():\n    assert True\n", encoding="utf-8")
    run_build(root, spec.slug, provider=f"prompt_recorder_{pre_create}")
    assert captured, "the implementer was never called"
    return captured[0], skeleton_paths


def test_a_missing_skeleton_is_marked_yours_to_write(tmp_path, monkeypatch):
    import ai_venture_studio.upstream.build as build_mod
    from ai_venture_studio import testing as testing_mod

    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    monkeypatch.setattr(build_mod, "docker_available", lambda: False)

    prompt, skeletons = _capture_implementer_prompt(tmp_path, pre_create=False)
    assert "already exist ON DISK" not in prompt, (
        "the run-19 lie: nothing writes skeletons, so this claim made the "
        "system and user prompts contradict each other"
    )
    for rel in skeletons:
        assert f"{rel}" in prompt
    assert "[NOT on disk — write this file]" in prompt
    assert "[on disk — read-only wall]" not in prompt


def test_an_existing_skeleton_is_marked_as_the_wall_it_is(tmp_path, monkeypatch):
    import ai_venture_studio.upstream.build as build_mod
    from ai_venture_studio import testing as testing_mod

    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    monkeypatch.setattr(build_mod, "docker_available", lambda: False)

    prompt, _ = _capture_implementer_prompt(tmp_path, pre_create=True)
    assert "[on disk — read-only wall]" in prompt
    assert "[NOT on disk — write this file]" not in prompt


def test_the_system_prompt_still_owns_the_test_first_contract():
    """The half that was always right must stay: the skeletons are the
    implementer's to author as real tests."""
    from ai_venture_studio.upstream.build import _SYSTEM

    flat = " ".join(_SYSTEM.split())
    assert "write them as real tests" in flat
    assert "Include the test files" in flat
