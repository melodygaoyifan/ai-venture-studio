"""Backend↔frontend wire-up gate for the Founder Studio.

Every path the rendered HTML references — form actions, fetch() calls,
links, image sources — must resolve to a registered route with the right
method, and every route must be referenced by some rendered state: a dead
button and an orphaned endpoint are the same bug, drift between the two
halves of one feature. (The product-side analog is tools/wireup.py, which
does this for generated frontends against generated backends.)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager

import pytest
import yaml
from fastapi.testclient import TestClient

from ai_venture_studio.studio import create_studio_app
from ai_venture_studio.upstream import init_workspace

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_FORM_ACTION = re.compile(r"action=['\"]?(/[^'\" >]*)")
_FETCH = re.compile(r"fetch\(['\"](/[^'\"]+)['\"]\)")
_HREF = re.compile(r"href=['\"](/[^'\"]+)['\"]")
_SRC = re.compile(r"src=['\"](/[^'\"]+)['\"]")


def _routes(app) -> dict[str, set[str]]:
    table: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods:
            table.setdefault(path, set()).update(m for m in methods if m != "HEAD")
    return table


def _resolves(path: str, route: str) -> bool:
    path = path.split("?", 1)[0]  # ?mode=… routes by its path, not its query
    parts, route_parts = path.split("/"), route.split("/")
    return len(parts) == len(route_parts) and all(
        r.startswith("{") or p == r for p, r in zip(parts, route_parts)
    )


def _route_for(path: str, table: dict[str, set[str]]) -> str | None:
    for route in table:
        if _resolves(path, route):
            return route
    return None


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


@pytest.fixture
def studio(tmp_path):
    root = init_workspace(tmp_path / "prod", "prod", "web")
    client = TestClient(create_studio_app(root, spawn=lambda r: 4242, provider="mock"))
    return client, root


#: Everything that could authenticate a real provider, cleared so the gate
#: renders on a developer machine that has a key in its environment.
_KEY_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_FILE", "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN_FILE", "AVS_ANTHROPIC_MODE",
)


@contextmanager
def keyless_studio(root):
    """A Studio on a paying provider with no key anywhere — the first state
    a founder who has never held an API key actually sees. Everything else
    in the environment stays (git needs its PATH)."""
    from unittest import mock

    environ = {k: v for k, v in os.environ.items() if k not in _KEY_ENV}
    with mock.patch.dict(os.environ, environ, clear=True):
        yield TestClient(
            create_studio_app(root, spawn=lambda r: 4242, provider="anthropic")
        )


def _keyless_page(root) -> str:
    with keyless_studio(root) as client:
        return client.get("/").text


def _walk_all_states(client, root) -> dict[str, list[tuple[str, str]]]:
    """Render every Studio state and collect (method, path) references."""
    pages: list[str] = []

    def snap():
        pages.append(client.get("/").text)

    # 1. FDR editor (fresh workspace).
    snap()

    # 1b. Both doors onto the describe state: the conversation (now the
    # default) and the form behind ?form=1. Answering the open prompt runs
    # the extraction pass, whose SAID/GUESS rows are a state of their own —
    # the guess carries the confirm/correct forms.
    pages.append(client.get("/chat").text)
    pages.append(
        client.post(
            "/chat",
            data={"answer": "a shared task list for the two of us"},
            follow_redirects=True,
        ).text
    )
    pages.append(client.get("/?form=1").text)

    # 1c. The key gate (v0.69) — a describe-state door too, and the first
    # page a founder without an API key sees. The fixture Studio runs on
    # `mock`, which bills nobody and therefore never gates, so this state
    # comes from a second Studio on a paying provider with the key
    # variables removed. It must be rendered here, while the workspace is
    # still in the describe state.
    pages.append(_keyless_page(root))

    # 2. Plan-confirmation state.
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "CONFIRMATION.md").write_text("plan", encoding="utf-8")
    snap()

    # 3. Building (live worker) — polls /status.
    (root / "product" / "plan.yaml").write_text(yaml.safe_dump({
        "status": "locked", "brief_title": "x", "tasks": [
            {"id": "t1", "title": "one", "estimate_hours": 1},
            {"id": "t2", "title": "two", "estimate_hours": 1},
        ]}), encoding="utf-8")
    spec_dir = root / "specs" / "one"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.yaml").write_text(
        yaml.safe_dump({"request": "one (task:t1)", "built": True}), encoding="utf-8"
    )
    (root / ".mas" / "build.pid").write_text(str(os.getpid()))
    snap()

    # 4. Interrupted (dead worker, no report yet).
    (root / ".mas" / "build.pid").write_text(str(_dead_pid()))
    snap()

    # 4b. Real history: the report page's change list is built from the
    # checkpoint tags, and each row carries its own go-back form.
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    from ai_venture_studio.upstream.autopilot import tag_checkpoint

    for number, message in enumerate(("first build", "second build"), start=1):
        (root / f"change{number}.txt").write_text(message, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", f"feat({number}): {message}"],
            cwd=root, check=True,
        )
        tag_checkpoint(root)

    # 5. Feature awaiting confirmation (renders before the report page).
    (root / "product" / "BUILD-REPORT.md").write_text("# done", encoding="utf-8")
    # Real artifact shapes: the walkthrough's checkboxes and the
    # verification report's marks are what the Try-it page derives its
    # checkable rows from.
    (root / "product" / "ACCEPTANCE.md").write_text(
        "# Acceptance walkthrough\n\n- [ ] Open the app and add one task\n"
        "- [ ] Mark it done and see it move\n",
        encoding="utf-8",
    )
    (root / "product" / "VERIFICATION.md").write_text(
        "# Automated verification\n\n- ✅ root-responds\n- ❌ items-listed\n",
        encoding="utf-8",
    )
    shots = root / "product" / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    (shots / "home.png").write_bytes(b"\x89PNG\r\n")
    (root / "product" / "outcomes.yaml").write_text(yaml.safe_dump(
        [{"task_id": "t1", "title": "one", "status": "built"},
         {"task_id": "t2", "title": "two", "status": "build_failed"}]
    ), encoding="utf-8")
    pending = root / "product" / "features" / "f2-cancel-orders"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "CONFIRMATION.md").write_text("new feature", encoding="utf-8")
    snap()

    # 6. Full product/report page (feature confirmed → built).
    (pending / "REPORT.md").write_text("built", encoding="utf-8")
    snap()

    # 7. Acceptance walkthrough page, and the Try-it page beside it — the
    # rows there carry the tick form and the per-row complaint form.
    pages.append(client.get("/acceptance").text)
    pages.append(client.get("/try").text)

    # 7b. The classification preview: /correct no longer acts on submit, it
    # shows what the router decided and offers Confirm / Reword. Both forms
    # live on that page, so it is a state of its own. It needs a built spec
    # for the router to route to.
    spec_one = root / "specs" / "one" / "spec.yaml"
    spec_one.write_text(yaml.safe_dump({
        "slug": "one", "title": "one", "request": "one (task:t1)", "built": True,
        "criteria": ["The system shall list items newest first."],
        # A whole Spec, not a stub: the change path reads it through
        # load_spec, and a fixture missing a required field would render a
        # ValidationError page that still counts as "a state".
        "profile": "web", "design": "one module", "test_skeletons": [],
    }), encoding="utf-8")
    pages.append(
        client.post("/correct", data={
            "complaint": "the button on the task form should say Add task",
        }).text
    )

    # 7c. The change-drafted result: a requirement change comes back as a
    # plan with its own "make this change" button, which is the only place
    # /correct/change is rendered. Drafting writes nothing to the workspace,
    # so this state does not disturb the ones after it.
    pages.append(
        client.post("/correct/confirm", data={
            "complaint": "actually I want to sort them oldest first",
            "spec_slug": "one", "kind": "scope_change",
            "instruction": "sort items oldest first",
        }, follow_redirects=False).text
    )

    # 8. Engineer and enterprise modes (v0.56) — the mode cards render
    # references of their own (review links), so they are states too.
    import datetime

    review_dir = root / ".mas" / "reviews" / "rev-wire"
    review_dir.mkdir(parents=True, exist_ok=True)
    t0 = datetime.datetime(2026, 7, 27, 10, 0, 0)
    (review_dir / "01-dor_gate.yaml").write_text(yaml.safe_dump({
        "step": 1, "node": "dor_gate", "written_at": t0.isoformat(),
        "dor_pass": True}), encoding="utf-8")
    (review_dir / "02-final.yaml").write_text(yaml.safe_dump({
        "step": 2, "node": "final",
        "written_at": (t0 + datetime.timedelta(seconds=5)).isoformat(),
        "verdict": "APPROVE"}), encoding="utf-8")
    pages.append(client.get("/?mode=engineer").text)
    pages.append(client.get("/review/rev-wire").text)
    pages.append(client.get("/?mode=enterprise").text)

    # 9. The production loop (v0.61): Take-it-live page and the incident
    # triage result — both render forms of their own (/live/guide,
    # /live/probe, /incident/fix), so they are states too. The incident
    # needs a correlatable commit so the mock proposes a root cause and
    # the fix form actually renders.
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "main.py").write_text("def main(): ...\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "handle TypeError in app main"],
        cwd=root, check=True,
    )
    pages.append(client.get("/live").text)
    pages.append(
        client.post("/incident", data={
            "description": "TypeError in app main since the latest change.",
        }).text
    )

    refs: dict[str, list[tuple[str, str]]] = {"POST": [], "GET": []}
    for page in pages:
        for path in _FORM_ACTION.findall(page):
            refs["POST"].append((path, page[:60]))
        for pattern in (_FETCH, _HREF, _SRC):
            for path in pattern.findall(page):
                refs["GET"].append((path, page[:60]))
    return refs


def test_every_frontend_reference_resolves_to_a_backend_route(studio):
    client, root = studio
    table = _routes(client.app)
    refs = _walk_all_states(client, root)
    assert refs["POST"] and refs["GET"], "state walk rendered no references"
    for method in ("POST", "GET"):
        for path, _context in refs[method]:
            route = _route_for(path, table)
            assert route is not None, f"frontend references {path} — no route"
            assert method in table[route], (
                f"frontend uses {method} {path} but the route allows {table[route]}"
            )


def test_every_backend_route_is_reachable_from_some_rendered_state(studio):
    client, root = studio
    table = _routes(client.app)
    refs = _walk_all_states(client, root)
    referenced = {p for pairs in refs.values() for p, _ in pairs} | {"/"}
    for route, methods in table.items():
        hit = any(_resolves(path, route) for path in referenced)
        assert hit, (
            f"route {route} ({sorted(methods)}) is rendered by no Studio state — "
            "orphaned endpoint or missing UI"
        )


#: Every POST route, with the field names its own rendered forms send. The
#: route-name gate above proves the button points somewhere; this proves
#: pressing it does not explode. Add a row when you add a POST route — the
#: coverage assertion below fails until you do.
_POST_BODIES: dict[str, list[dict[str, str]]] = {
    "/chat": [{"answer": "the two of us"}, {"skip": "1"}],
    # Confirm and correct, plus the press with no proposal pending.
    "/chat/guess": [{"accept": "1"}, {"answer": "weekly, not daily"}, {}],
    "/chat/enough": [{}],
    "/chat/restart": [{}],
    "/fdr": [{"fdr": "## 1. Who is this for?\nus\n"}],
    "/correct": [
        {"complaint": "the button should say Add task"},
        # …and the same complaint arriving from a Try-it row, carrying the
        # criterion that failed.
        {"complaint": "it never moved", "criterion": "Mark it done and see it move"},
        # A feature card pre-scopes the complaint to the spec it sits on, so
        # `spec_slug` is now a path segment arriving from a form: the
        # traversal shape and the well-formed-but-absent shape both get
        # pressed here, the same as every other identifier a form supplies.
        {"complaint": "it should remember my last one", "spec_slug": "../../etc"},
        {"complaint": "it should remember my last one", "spec_slug": "no-such-spec"},
    ],
    # A tick, an untick, and a row id that is not in the current list.
    "/try/tick": [{"row": "deadbeef0000"}, {"row": "deadbeef0000", "off": "1"}],
    # Confirming a classification EXECUTES it, so the row presses a slug
    # that is well-formed and absent — the answered-out-loud branch. The
    # real confirm→execute path is pinned in tests/test_studio_correction.py.
    "/correct/confirm": [
        {"complaint": "say Add task", "spec_slug": "no-such-spec", "kind": "fix"},
        {"complaint": "say Add task", "spec_slug": "one", "kind": "carrier-pigeon"},
        # Several issues in one message: the fields arrive as parallel
        # repeated values, and a bad slug anywhere must still be caught.
        {"complaint": "a\nb", "include": ["0", "1"],
         "spec_slug": ["one", "no-such-spec"], "kind": ["fix", "fix"],
         "quote": ["a", "b"], "instruction": ["x", "y"]},
    ],
    # Pressing "make this change" with junk, with a well-formed absent spec,
    # and with an empty acceptance list. The real draft→press→build path is
    # pinned in tests/test_studio_correction.py; here the point is that a
    # hand-edited hidden field cannot 500 or reach the filesystem.
    "/correct/change": [
        {"plan": "not json"},
        {"plan": '{"spec_slug": "../../etc", "summary": "s", "criteria": ["c"]}'},
        {"plan": '{"spec_slug": "no-such-spec", "summary": "s", "criteria": ["c"]}'},
        {"plan": '{"spec_slug": "one", "summary": "s", "criteria": []}'},
    ],
    "/retry": [{"task_id": "t2"}],
    "/undo": [{}],
    # Same: a well-formed checkpoint name this workspace does not have, so
    # the gate presses the button without resetting the fixture's history.
    "/undo/to": [{"tag": "ap-checkpoint-404"}],
    "/feature": [{"fdr": "let us cancel an order"}],
    "/feature/build": [{"slug": "f2-cancel-orders"}],
    "/build": [{}],
    "/live/guide": [{}],
    "/live/sweep": [{}],
    "/live/probe": [{"url": "http://127.0.0.1:1/"}],
    "/incident": [{"description": "TypeError in app main"}],
    "/incident/fix": [{"incident_id": "nope"}],
    "/review/{review_id}/evidence": [{}],
    "/reset": [{}],
    # On the fixture's `mock` provider there is no variable to set, so this
    # is the no-key-to-set branch. The real paste path (a variable set for
    # this process only, never written to disk) is pinned in
    # tests/test_studio_key_gate.py, which is also where the secret-handling
    # assertions live — pressing it here must not put a key in this
    # process's environment.
    "/key": [{"key": "not-a-real-key"}, {"key": ""}],
}

#: Values that are an attempt rather than a typo. Each must be refused
#: without reaching the filesystem or an argv.
_HOSTILE = ("../../etc/passwd", "../" * 6 + "tmp", "a b; rm -rf /", "x" * 200)


def test_every_post_route_is_covered_by_the_press_it_gate(studio):
    """A POST route with no row above has never been pressed by any test."""
    client, _root = studio
    posts = {
        path for path, methods in _routes(client.app).items() if "POST" in methods
    }
    assert posts - set(_POST_BODIES) == set(), "add these to _POST_BODIES"


def test_pressing_every_button_does_not_500(studio):
    """The route-name gate renders pages; it never presses anything. Both
    unvalidated-path-segment bugs it missed (a `slug` that walked the build
    out of the workspace, a `task_id` that spawned a worker doomed on
    arrival) lived behind a POST no test had ever sent."""
    client, root = studio
    _walk_all_states(client, root)  # a workspace with a plan, a report, a feature
    for path, bodies in _POST_BODIES.items():
        target = path.replace("{review_id}", "rev-wire")
        for body in bodies:
            response = client.post(target, data=body, follow_redirects=False)
            assert response.status_code < 500, (
                f"POST {target} {body} → {response.status_code}"
            )


@pytest.mark.parametrize("evil", _HOSTILE)
def test_a_path_segment_from_a_form_is_never_taken_on_trust(studio, evil):
    """`task_id` and `slug` reach the filesystem and an argv, exactly like
    the review and incident ids that already carry this rule. Neither may
    start a worker, and neither may name a path outside the workspace."""
    client, root = studio
    _walk_all_states(client, root)
    (root / ".mas" / "build.pid").unlink(missing_ok=True)

    for path, field in (("/retry", "task_id"), ("/feature/build", "slug")):
        response = client.post(path, data={field: evil}, follow_redirects=False)
        assert response.status_code < 500
        assert not (root / ".mas" / "build.pid").exists(), (
            f"{path} started a worker for {field}={evil!r}"
        )


def test_a_name_the_workspace_does_not_have_is_said_out_loud(studio):
    """Well-formed but absent is a different answer from malformed: the
    founder pressed a real button, so silence reads as a broken button."""
    client, root = studio
    _walk_all_states(client, root)
    (root / ".mas" / "build.pid").unlink(missing_ok=True)

    page = client.post("/retry", data={"task_id": "t-nope"}).text
    assert "t-nope" in page and "nothing to retry" in page
    assert not (root / ".mas" / "build.pid").exists()

    page = client.post("/feature/build", data={"slug": "f-nope"}).text
    assert "f-nope" in page and "no pending feature" in page


def test_status_payload_matches_what_the_building_page_js_reads(studio):
    """The building page's script reads s.running / s.built / s.total and
    t.id / t.title / t.state / t.step per task — the JSON contract, pinned."""
    client, root = studio
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "plan.yaml").write_text(yaml.safe_dump({
        "status": "locked", "brief_title": "x",
        "tasks": [{"id": "t1", "title": "one", "estimate_hours": 1}],
    }), encoding="utf-8")
    data = client.get("/status").json()
    assert set(data) == {"total", "built", "running", "tasks", "step"}
    # `step` is the in-flight narration: what this task is doing right now,
    # rendered only while it is still pending.
    assert all(set(t) == {"id", "title", "state", "step"} for t in data["tasks"])
    page_src = client.get("/").text  # editor state — no script, but the
    # building page's JS is source-checked here so a rename fails loudly:
    from ai_venture_studio import studio as studio_mod
    import inspect

    src = inspect.getsource(studio_mod)
    for token in ("s.running", "s.built", "s.total", "t.state", "t.title",
                  "t.step", "'task-'+t.id"):
        assert token in src, f"building-page JS no longer reads {token}"
    assert page_src  # the walk above already covers rendering
