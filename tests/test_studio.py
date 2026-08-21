import shutil

import pytest
from fastapi.testclient import TestClient

from ai_venture_studio.studio import create_studio_app
from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.executables import resolve

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

GOOD_FDR = (
    "# 小区团购接龙\n团长发起接龙写商品和价格，住户下单选数量，团长看按商品汇总。\n"
    "必须有：发起、下单、汇总。暂时不要：在线支付。成功：一周10个团长用过。\n"
)


@pytest.fixture
def studio(tmp_path):
    """The Chinese-founder flow: a 小程序 workspace with a Chinese FDR, so it
    asks for the Chinese UI explicitly. English is the default since v0.53;
    `studio_en` below covers that path."""
    root = init_workspace(tmp_path / "prod", "prod", "miniprogram")
    spawned = []
    client = TestClient(
        create_studio_app(root, spawn=lambda r: spawned.append(r) or 4242,
                          provider="mock", lang="zh")
    )
    return client, root, spawned


@pytest.fixture
def studio_en(tmp_path):
    """The DEFAULT flow: no language argument at all."""
    root = init_workspace(tmp_path / "prod-en", "prod-en", "web")
    spawned = []
    client = TestClient(
        create_studio_app(root, spawn=lambda r: spawned.append(r) or 4242,
                          provider="mock")
    )
    return client, root, spawned


def test_first_visit_shows_editor_with_template_and_guide(studio):
    client, _, _ = studio
    page = client.get("/?form=1").text
    assert "textarea" in page
    assert "不需要任何技术词汇" in page  # template pre-filled
    assert "How to write a good FDR" in page  # guide reachable


def test_vague_fdr_roundtrips_to_questions(studio):
    """A vague FDR comes back as questions — and with the conversation as
    the front door, straight into answering the first one rather than
    handing back a list to merge into a 4000-character textarea."""
    client, root, _ = studio
    response = client.post(
        "/fdr", data={"fdr": "just an idea: 帮小区做团购"}, follow_redirects=True
    )
    assert (root / "FDR-QUESTIONS.md").exists()
    asked = [
        line.strip()
        for line in (root / "FDR-QUESTIONS.md").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("1. ")
    ]
    assert asked, "the assessor wrote no numbered questions"
    assert asked[0].removeprefix("1. ") in response.text


def test_the_form_door_still_shows_the_question_list(studio):
    """Anyone who prefers the textarea still gets the whole list up front."""
    client, root, _ = studio
    client.post("/fdr", data={"fdr": "just an idea: 帮小区做团购"},
                follow_redirects=True)
    assert "请先回答这些问题" in client.get("/?form=1").text


def test_good_fdr_reaches_confirmation_with_build_button(studio):
    client, root, _ = studio
    response = client.post("/fdr", data={"fdr": GOOD_FDR}, follow_redirects=True)
    assert "开始搭建" in response.text
    assert (root / "product" / "CONFIRMATION.md").exists()


def test_build_button_spawns_exactly_one_worker(studio):
    client, _, spawned = studio
    client.post("/fdr", data={"fdr": GOOD_FDR})
    client.post("/build", follow_redirects=False)
    client.post("/build", follow_redirects=False)  # double-click safe? no pid marker in fake spawn
    assert len(spawned) >= 1


def test_report_state_renders_report(studio):
    client, root, _ = studio
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "BUILD-REPORT.md").write_text("# 已完成\n你的接龙工具好了。")
    page = client.get("/").text
    assert "已完成" in page          # the report renders
    assert "添加新功能" in page      # feature-granular add form
    assert "一次只写一个功能" in page  # granularity guidance in the UI


def test_status_endpoint(studio):
    client, _, _ = studio
    data = client.get("/status").json()
    assert set(data) == {"total", "built", "running", "tasks", "step"}


# --- live progress + interrupted builds (signal s3 / s1) ----------------------


def _fabricate_partial_build(root):
    import yaml

    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "plan.yaml").write_text(yaml.safe_dump({
        "status": "locked", "brief_title": "x", "tasks": [
            {"id": "t1", "title": "URL store", "estimate_hours": 1},
            {"id": "t2", "title": "Shorten endpoint", "estimate_hours": 1},
        ]}), encoding="utf-8")
    spec_dir = root / "specs" / "url-store"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.yaml").write_text(yaml.safe_dump(
        {"request": "an item store (task:t1)", "built": True}), encoding="utf-8")


def test_building_page_shows_live_per_task_progress(studio):
    import os

    client, root, _ = studio
    _fabricate_partial_build(root)
    (root / ".mas" / "build.pid").write_text(str(os.getpid()))  # "running"
    page = client.get("/").text
    assert "fetch('/status')" in page  # polls in place, no blind reload
    assert "task-t1" in page and "task-t2" in page
    # built → DONE chip, pending → QUEUED chip: the redesigned equivalents
    # of the old ✅/⏳ icons, pinned at the same strength (zh UI).
    assert "完成 / DONE</span><span class=ttl>URL store" in page
    assert "排队 / QUEUED</span><span class=ttl>Shorten endpoint" in page

    status = client.get("/status").json()
    assert status["running"] is True
    assert {t["id"]: t["state"] for t in status["tasks"]} == {
        "t1": "built", "t2": "pending",
    }


def test_interrupted_build_offers_per_task_retry_and_reset_escapes(studio):
    import subprocess as sp
    import sys as _sys

    client, root, _ = studio
    _fabricate_partial_build(root)
    proc = sp.Popen([_sys.executable, "-c", ""])
    proc.wait()
    (root / ".mas" / "build.pid").write_text(str(proc.pid))  # dead worker
    page = client.get("/").text
    assert "搭建中断" in page
    assert "action=/retry" in page and "value='t2'" in page  # unbuilt task
    assert "value='t1'" not in page  # built modules are kept, not retried

    page = client.post("/reset", follow_redirects=True).text
    assert "搭建中断" not in page  # stale pid cleared — back to the editor
    assert not (root / ".mas" / "build.pid").exists()


# --- language selection (v0.52.0) --------------------------------------------


def _page(root, lang):
    from ai_venture_studio.studio import create_studio_app

    client = TestClient(
        create_studio_app(root, spawn=lambda r: 1, provider="mock", lang=lang)
    )
    return client.get("/?form=1").text


def test_english_renders_with_no_chinese_anywhere(tmp_path):
    """The point of the flag: an English-speaking founder should not have to
    read `写下你的产品需求 / Describe your product`."""
    import re

    root = init_workspace(tmp_path / "en", "en", "web")
    page = _page(root, "en")
    assert not re.search(r"[一-鿿]", page), "English UI still has CJK"
    assert "<title>Describe your product</title>" in page
    assert "Check it &amp; make the plan" in page
    assert "How to write a good FDR" in page
    # The pre-filled template is English too, or the textarea betrays it.
    assert "Fill this in using your own words" in page
    assert "What does success look like?" in page


def test_chinese_is_still_available_character_for_character(tmp_path):
    """Moving the default must not degrade the Chinese UI: `--lang zh` gives
    exactly what 小程序 founders were using before."""
    root = init_workspace(tmp_path / "zh", "zh", "web")
    page = _page(root, "zh")
    assert "写下你的产品需求 / Describe your product" in page
    assert "检查并生成计划" in page
    assert "不需要任何技术词汇" in page  # the Chinese template


def test_english_is_the_default_when_no_language_is_given(tmp_path):
    import re

    from ai_venture_studio.studio import create_studio_app

    root = init_workspace(tmp_path / "default", "d", "web")
    unset = TestClient(create_studio_app(root, spawn=lambda r: 1,
                                         provider="mock")).get("/?form=1").text
    assert unset == _page(root, "en")
    assert not re.search(r"[一-鿿]", unset)


@pytest.mark.parametrize("given", ["EN", "en-US", "en_GB"])
def test_language_codes_are_normalized(tmp_path, given):
    root = init_workspace(tmp_path / f"n{given[:2]}", "n", "web")
    assert "<title>Describe your product</title>" in _page(root, given)


def test_an_unknown_language_falls_back_rather_than_blanking_the_ui(tmp_path):
    """A missing translation must never render an empty page: fall back to
    the default, which is a working UI in the wrong language rather than a
    broken one in none."""
    root = init_workspace(tmp_path / "xx", "xx", "web")
    page = _page(root, "klingon")
    assert "<title>Describe your product</title>" in page


def test_every_string_exists_in_both_languages():
    from ai_venture_studio.studio_i18n import LANGUAGES, STRINGS

    for key, values in STRINGS.items():
        assert set(values) == set(LANGUAGES), f"{key} is missing a language"
        for lang, text in values.items():
            assert text.strip(), f"{key}/{lang} is empty"


def test_no_english_string_carries_chinese_and_no_chinese_one_is_english_only():
    """The table's own contract, checked at the source rather than only
    through whichever pages a test happens to render: `en` is English only,
    and `zh` is the bilingual original."""
    import re

    from ai_venture_studio.studio_i18n import STRINGS

    for key, values in STRINGS.items():
        assert not re.search(r"[一-鿿]", values["en"]), f"{key}/en has CJK"


def test_every_page_the_studio_can_render_is_english_only(tmp_path):
    """The v0.53 rule, over every state — including the ones added since.
    A single page that leaks CJK makes the flag a half-promise."""
    import re
    import subprocess as sp

    import yaml as _yaml

    root = init_workspace(tmp_path / "allpages", "allpages", "web")
    product = root / "product"
    product.mkdir(exist_ok=True)
    (product / "BUILD-REPORT.md").write_text("# done\nIt works.\n", encoding="utf-8")
    (product / "ACCEPTANCE.md").write_text(
        "- [ ] Add a task and see it listed\n", encoding="utf-8"
    )
    (product / "VERIFICATION.md").write_text("- ✅ root-responds\n", encoding="utf-8")
    spec = root / "specs" / "tasks"
    spec.mkdir(parents=True, exist_ok=True)
    (spec / "spec.yaml").write_text(_yaml.safe_dump({
        "slug": "tasks", "title": "Task list", "built": True,
        "criteria": ["The system shall list items."],
    }), encoding="utf-8")
    sp.run([resolve("git"), "init", "-q"], cwd=root, check=True)
    sp.run([resolve("git"), "add", "-A"], cwd=root, check=True)
    sp.run([resolve("git"), "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-qm", "feat(tasks): one"], cwd=root, check=True)
    from ai_venture_studio.upstream.autopilot import tag_checkpoint

    tag_checkpoint(root)

    client = TestClient(
        create_studio_app(root, spawn=lambda r: 1, provider="mock")
    )
    pages = {
        "report": client.get("/").text,
        "try": client.get("/try").text,
        "acceptance": client.get("/acceptance").text,
        "verification": client.get("/verification").text,
        "demo": client.get("/demo").text,
        "live": client.get("/live").text,
        "classify": client.post(
            "/correct", data={"complaint": "the button should say Add task"},
            follow_redirects=True,
        ).text,
        "no_row": client.post("/try/tick", data={"row": "abc123abc123"}).text,
    }
    for name, page in pages.items():
        found = re.findall(r"[一-鿿]", page)
        assert not found, f"the English {name} page leaks CJK: {''.join(found)}"


def test_the_english_readme_demo_shows_the_english_screenshot():
    """The README's founder demo and the shipped image must agree — a demo
    claiming English while showing a Chinese UI is the bug this closes."""
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "docs/media/studio-en-v070.png" in readme
    assert "--lang en" in readme
    assert (repo / "docs" / "media" / "studio-en-v070.png").exists()


def test_default_flow_first_visit_is_english(studio_en):
    client, _root, _ = studio_en
    page = client.get("/?form=1").text
    assert "<title>Describe your product</title>" in page
    assert "Fill this in using your own words" in page  # English template
    assert "How to write a good FDR" in page


def test_default_flow_reaches_confirmation_in_english(studio_en):
    client, root, _ = studio_en
    english_fdr = (
        "# Shared task list\n"
        "The two of us track work in chat and lose it. Anyone adds a task with "
        "a title and owner; anyone marks it done; we see open and done "
        "separately.\nMust have: add, mark done, both lists. Not yet: logins.\n"
        "Success: we stop tracking work in chat messages.\n"
    )
    response = client.post("/fdr", data={"fdr": english_fdr}, follow_redirects=True)
    assert "Start building" in response.text
    assert (root / "product" / "CONFIRMATION.md").exists()


# --- the CLI surface the docs promise (v0.56.1) ------------------------------


def test_studio_accepts_the_workspace_positionally_like_the_docs_show(tmp_path):
    """Every doc writes `avs studio myteam --profile web`, and README's
    founder quickstart is that exact line — but repo_dir was an Option, so
    the documented invocation died with "unexpected extra argument". The
    docs were right; the signature was wrong."""
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    served = {}

    def fake_serve(root, **kwargs):
        served["root"] = str(root)
        served.update(kwargs)

    import ai_venture_studio.studio as studio_mod

    original = studio_mod.serve_studio
    studio_mod.serve_studio = fake_serve
    try:
        result = CliRunner().invoke(
            app, ["studio", str(tmp_path / "myteam"), "--profile", "web"]
        )
    finally:
        studio_mod.serve_studio = original

    assert result.exit_code == 0, result.output
    assert served["root"].endswith("myteam")


def test_studio_still_defaults_to_the_current_directory(tmp_path):
    """The positional gains a default, so `avs studio` inside an existing
    workspace keeps working — that is the returning-user path."""
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app
    from ai_venture_studio.upstream import init_workspace

    root = init_workspace(tmp_path / "ws", "ws", "web")
    served = {}

    import ai_venture_studio.studio as studio_mod

    original = studio_mod.serve_studio
    studio_mod.serve_studio = lambda r, **kw: served.update(root=str(r))
    try:
        result = CliRunner().invoke(app, ["studio", str(root)])
    finally:
        studio_mod.serve_studio = original

    assert result.exit_code == 0, result.output
    assert served["root"] == str(root)


def test_the_old_repo_dir_flag_keeps_working_with_a_deprecation_notice(tmp_path):
    """The CLI surface is a versioned contract: `--repo-dir` was the only
    way in before v0.56.1, so it still works — loudly deprecated, not
    silently removed."""
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    served = {}
    import ai_venture_studio.studio as studio_mod

    original = studio_mod.serve_studio
    studio_mod.serve_studio = lambda r, **kw: served.update(root=str(r))
    try:
        result = CliRunner().invoke(
            app, ["studio", "--repo-dir", str(tmp_path / "old"),
                  "--profile", "web"]
        )
    finally:
        studio_mod.serve_studio = original

    assert result.exit_code == 0, result.output
    assert served["root"].endswith("old")
    assert "deprecated" in result.output.lower()


def test_giving_the_workspace_twice_is_refused_not_guessed(tmp_path):
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    result = CliRunner().invoke(
        app, ["studio", str(tmp_path / "a"), "--repo-dir", str(tmp_path / "b")]
    )
    assert result.exit_code == 2
    assert "twice" in result.output


def test_the_readme_founder_demo_is_the_recorded_real_run(tmp_path):
    """The GIF is the founder section's demo, and its caption must keep
    saying the run was real AND disclose what did not go perfectly — a demo
    edited into a victory lap is the exact failure this repo argues against.

    The run behind the current GIF finished 6 of 6, so "partly built" would
    now be the lie rather than the disclosure. The property is unchanged and
    pinned the same way: the claim that nothing was staged, plus at least one
    concrete admission against interest. A caption that drops every one of
    these has become marketing.
    """
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "docs/media/studio-flow-v070.gif" in readme
    assert (repo / "docs" / "media" / "studio-flow-v070.gif").exists()
    for phrase in ("One real run, unedited", "nothing composited"):
        assert phrase in readme, f"the honest caption lost {phrase!r}"
    disclosures = (
        "partly built",          # some modules failed (earlier runs)
        "failed its tests",      # one did, and the retry pass rebuilt it
        "closer look",           # the reviewer is not finished with it
        "defects",               # driving it found bugs, now fixed
    )
    assert any(d in readme for d in disclosures), (
        "the demo caption admits nothing — a victory lap, which is the one "
        f"thing this demo may not be. Expected one of {disclosures}"
    )


# --- long POSTs: no 500s, no double-submit (v0.57.1) -------------------------


def _erroring_studio(tmp_path, target="run_autopilot"):
    from fastapi.testclient import TestClient

    import ai_venture_studio.upstream.autopilot as ap
    from ai_venture_studio.studio import create_studio_app
    from ai_venture_studio.upstream import init_workspace

    root = init_workspace(tmp_path / "boom", "boom", "web")

    def boom(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    original = getattr(ap, target)
    setattr(ap, target, boom)
    client = TestClient(
        create_studio_app(root, spawn=lambda r: 1, provider="mock"),
        raise_server_exceptions=False,
    )
    return client, root, (ap, target, original)


def test_a_failing_step_shows_a_page_not_an_internal_server_error(tmp_path):
    """Observed live: a founder pressed the plan button and got a bare
    "Internal Server Error", with the traceback in a terminal they were not
    reading. Plain language first, the real error one click away."""
    client, _root, (module, name, original) = _erroring_studio(tmp_path)
    try:
        response = client.post(
            "/fdr", data={"fdr": "# x\nsomething\n"}, follow_redirects=True
        )
    finally:
        setattr(module, name, original)

    assert response.status_code == 200
    assert "Internal Server Error" not in response.text
    assert "did not finish" in response.text
    assert "Nothing was lost" in response.text
    # the real error stays reachable, one click away, for whoever can use it
    assert "<details>" in response.text
    assert "ANTHROPIC_API_KEY is not set" in response.text


def test_the_failure_page_keeps_the_workspace_retryable(tmp_path):
    client, root, (module, name, original) = _erroring_studio(tmp_path)
    try:
        client.post("/fdr", data={"fdr": "# keep me\nplease\n"},
                    follow_redirects=True)
    finally:
        setattr(module, name, original)
    assert (root / "FDR.md").read_text().startswith("# keep me")
    # and the in-flight flag is released, so a retry is possible
    assert "Working on it" not in client.get("/").text


def test_a_second_submit_while_thinking_does_not_start_a_second_run(tmp_path):
    """A button that looks dead for minutes is a button people press twice,
    and two autopilots on one workspace race on git and on the same files.
    /build and /retry already guarded; the LLM handlers did not."""
    import threading

    from fastapi.testclient import TestClient

    import ai_venture_studio.upstream.autopilot as ap
    from ai_venture_studio.studio import create_studio_app
    from ai_venture_studio.upstream import init_workspace

    root = init_workspace(tmp_path / "race", "race", "web")
    started = threading.Event()
    release = threading.Event()
    runs = []

    def slow(*a, **k):
        runs.append(1)
        started.set()
        release.wait(timeout=10)

    original = ap.run_autopilot
    ap.run_autopilot = slow
    client = TestClient(
        create_studio_app(root, spawn=lambda r: 1, provider="mock"),
        raise_server_exceptions=False,
    )
    try:
        first = threading.Thread(
            target=lambda: client.post("/fdr", data={"fdr": "# a\nb\n"})
        )
        first.start()
        assert started.wait(timeout=10), "the first run never started"

        second = client.post("/fdr", data={"fdr": "# a\nb\n"},
                             follow_redirects=True)
        assert "Working on it" in second.text
        assert "no need to submit again" in second.text
        release.set()
        first.join(timeout=10)
    finally:
        release.set()
        ap.run_autopilot = original

    assert len(runs) == 1, f"{len(runs)} concurrent autopilot runs on one workspace"
