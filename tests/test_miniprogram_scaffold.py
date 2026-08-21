"""A 小程序 workspace must start loadable.

Two runs of the same FDR under the same profile produced two different
layouts — `miniprogram/pages/...` one time, `utils/` and `server/` at the
repo root the next — and neither produced an app.json. DevTools could open
neither. The loadability gate silently no-opped the second time because it
could not find a project to check: a gate can only check a layout somebody
guaranteed.
"""
from __future__ import annotations

import json

import pytest

from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.build import _miniprogram_gate, _miniprogram_root
from ai_venture_studio.executables import resolve


@pytest.fixture
def mp(tmp_path):
    return init_workspace(tmp_path / "mp", "假装消费", "miniprogram")


def test_a_fresh_workspace_already_passes_the_loadability_gate(mp):
    assert _miniprogram_gate(mp) is None


def test_devtools_has_something_to_open(mp):
    config = json.loads((mp / "project.config.json").read_text(encoding="utf-8"))
    assert config["miniprogramRoot"] == "miniprogram/"
    assert config["compileType"] == "miniprogram"
    assert _miniprogram_root(mp) == (mp / "miniprogram").resolve()


def test_the_entry_files_exist(mp):
    src = mp / "miniprogram"
    for f in ("app.js", "app.json", "app.wxss", "sitemap.json"):
        assert (src / f).exists(), f
    app = json.loads((src / "app.json").read_text(encoding="utf-8"))
    assert app["pages"] == ["pages/index/index"]


def test_the_launch_page_is_complete(mp):
    page = mp / "miniprogram" / "pages" / "index"
    for suffix in (".js", ".wxml", ".wxss", ".json"):
        assert (page / f"index{suffix}").exists(), suffix


def test_no_appid_is_invented(mp):
    """One run's implementer produced `wxb1e7d6736079f6c3` from nowhere —
    that is somebody's identifier or nobody's, and neither is ours to
    write."""
    config = json.loads((mp / "project.config.json").read_text(encoding="utf-8"))
    assert config["appid"] == "touristappid"


def test_the_project_name_reaches_the_title_bar(mp):
    app = json.loads((mp / "miniprogram" / "app.json").read_text(encoding="utf-8"))
    assert app["window"]["navigationBarTitleText"] == "假装消费"


def test_other_profiles_get_no_miniprogram_tree(tmp_path):
    web = init_workspace(tmp_path / "web", "w", "web")
    assert not (web / "miniprogram").exists()
    assert not (web / "project.config.json").exists()


def test_the_implementer_is_told_the_layout_is_not_its_choice():
    """The scaffold only helps if the writer knows to build into it."""
    from ai_venture_studio.upstream.workspace import load_profile

    hint = load_profile("miniprogram")["stack_hint"]
    assert "LAYOUT IS FIXED" in hint
    assert "miniprogram/app.json" in hint
    assert "`pages` array" in hint
    assert ".py test file" in hint


def test_the_scaffold_is_committed_so_tasks_extend_it(mp):
    import subprocess

    tracked = subprocess.run(
        [resolve("git"), "ls-files"], cwd=mp, capture_output=True, text=True, timeout=60
    ).stdout
    assert "miniprogram/app.json" in tracked
    assert "project.config.json" in tracked


def test_a_stray_miniprogram_directory_does_not_disable_the_gate(tmp_path):
    """Evidence beats directory names.

    A real workspace kept its mini-program at the repo root (app.json and
    pages/ there) beside a stray `miniprogram/` holding nothing but a
    .DS_Store and an api/ folder. The name heuristic picked the stray, found
    no app.json, and the gate answered "not my business" — a vacuous pass
    over a product with three registered pages, which is exactly the silent
    no-op this gate exists to prevent.
    """
    import json

    repo = tmp_path / "rootlayout"
    (repo / "pages" / "home").mkdir(parents=True)
    (repo / "miniprogram" / "api").mkdir(parents=True)      # the stray
    (repo / "miniprogram" / ".DS_Store").write_text("", encoding="utf-8")
    (repo / "app.json").write_text(
        json.dumps({"pages": ["pages/home/home"]}), encoding="utf-8"
    )
    (repo / "app.js").write_text("App({})\n", encoding="utf-8")
    (repo / "pages" / "home" / "home.js").write_text("Page({})\n", encoding="utf-8")
    (repo / "pages" / "home" / "home.wxml").write_text("<view/>\n", encoding="utf-8")

    assert _miniprogram_root(repo) == repo, "the app.json decides, not the name"
    assert _miniprogram_gate(repo) is None, "a complete root-layout project loads"

    # ...and the gate now actually judges it: break the page it registered.
    (repo / "pages" / "home" / "home.wxml").unlink()
    problems = _miniprogram_gate(repo)
    assert problems and "pages/home/home.wxml" in problems, (
        "a stray miniprogram/ must not buy silence about a missing page file"
    )


def test_a_name_declared_twice_in_one_module_is_caught(tmp_path):
    """106 node --test cases stayed green while the app was entirely blank.

    avs-studio-3 (2026-08-03): a second `pad2` was added to utils/delivery.js.
    Verified rather than assumed — `function a(){} function a(){}` is legal in
    a sloppy script AND a strict one, but is a SyntaxError under ES-module
    semantics, which is what the 小程序 toolchain compiles with. The module
    never evaluated, so cart/delivery/profile all registered no Page() and
    rendered blank, and only the DevTools run found it. Node's own runner
    cannot see this; the gate can.
    """
    import json

    repo = tmp_path / "dup"
    (repo / "pages" / "home").mkdir(parents=True)
    (repo / "utils").mkdir()
    (repo / "app.json").write_text(
        json.dumps({"pages": ["pages/home/home"]}), encoding="utf-8"
    )
    (repo / "app.js").write_text("App({})\n", encoding="utf-8")
    (repo / "pages" / "home" / "home.wxml").write_text("<view/>\n", encoding="utf-8")
    (repo / "pages" / "home" / "home.js").write_text(
        "const { pad2 } = require('../../utils/fmt')\nPage({})\n", encoding="utf-8"
    )
    (repo / "utils" / "fmt.js").write_text(
        "function pad2(n) { return n }\n"
        "// a later edit adds a second one\n"
        "function pad2(n) { return '0' + n }\n"
        "module.exports = { pad2 }\n",
        encoding="utf-8",
    )

    problems = _miniprogram_gate(repo)

    assert problems and "pad2" in problems
    assert "2 times" in problems
    assert "blank" in problems, "say what it costs, not just that it is wrong"

    # A single declaration, and a re-declared `var` (legal everywhere), pass.
    (repo / "utils" / "fmt.js").write_text(
        "function pad2(n) { return n }\nvar x = 1\nvar x = 2\n"
        "module.exports = { pad2 }\n",
        encoding="utf-8",
    )
    assert _miniprogram_gate(repo) is None
