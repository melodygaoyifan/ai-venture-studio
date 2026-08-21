"""Auto-generated probes: from the founder's PRD against the ACTUAL built
product — the fixtures in benchmarks/ are labeled regression cases; real
users get generation."""

import shutil
import subprocess
import sys

import pytest

from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.probegen import generate_probes, verify_product
from ai_venture_studio.executables import resolve

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

# A tiny real stdlib product: one route, JSON 200 on "/".
MAIN_PY = """import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", int(os.environ.get("PORT", 8646))), Handler).serve_forever()
"""


def _workspace_with_product(tmp_path):
    root = init_workspace(tmp_path / "p", "p", "web")
    (root / "FDR.md").write_text("一个能打开的首页。必须有：首页能打开。")
    (root / "main.py").write_text(MAIN_PY)
    # A built spec so criteria exist, and an observable route for probegen.
    spec_dir = root / "specs" / "home"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.yaml").write_text(
        "slug: home\ntitle: Home\nstatus: approved\nrequest: 首页\nprofile: web\n"
        "design: main.py\ncriteria: ['The system shall serve the home page.']\n"
        "test_skeletons: []\nbuilt: true\n"
    )
    (root / "routes.py").write_text('@app.get("/")\ndef home(): ...\n')
    subprocess.run([resolve("git"), "add", "-A"], cwd=root, check=True)
    return root


def test_generation_guardrails_drop_invalid_bodies(tmp_path):
    root = _workspace_with_product(tmp_path)
    probes, notes = generate_probes(root, provider="mock")
    # Mock emits one valid probe and one non-parsing body.
    assert [p.name for p in probes] == ["root-responds"]
    assert any("does not parse" in n for n in notes)
    assert "wait()" in probes[0].script and "proc.terminate()" in probes[0].script


def test_verify_runs_generated_probes_against_booted_product(tmp_path):
    root = _workspace_with_product(tmp_path)
    path = verify_product(root, provider="mock")
    text = path.read_text()
    assert "✅ root-responds" in text          # generated probe passed live
    assert "1/1" in text
    assert "⚠️" in text                        # the dropped body is visible


def test_a_finished_probe_leaves_no_worker_holding_the_port(tmp_path):
    """The frame terminates the server it booted — the whole tree of it.

    Real web products fork (uvicorn reloaders, worker pools). Signalling
    only the parent left a worker bound to the port after the probe had
    exited, which is the same collision the random-port fix addressed one
    level up: the next probe's first call comes back refused and the
    product is charged for the harness's race.
    """
    import textwrap

    from ai_venture_studio.upstream.probegen import BOOT_FRAME

    root = tmp_path / "forking"
    root.mkdir()
    (root / "main.py").write_text(
        "import subprocess, sys\n"
        'subprocess.Popen([sys.executable, "-c", '
        '"import time; time.sleep(90)  # forked-worker"])\n'
        + MAIN_PY
    )
    script = root / "probe.py"
    script.write_text(
        BOOT_FRAME.replace(
            "{body}",
            textwrap.indent('s, _, _ = call("GET", "/")\nassert s == 200', "    "),
        )
    )

    done = subprocess.run(
        [sys.executable, str(script)], cwd=root, capture_output=True, text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr

    survivors = subprocess.run(
        [resolve("pgrep"), "-f", "forked-worker"], capture_output=True, text=True
    )
    assert "forked-worker" not in survivors.stdout, (
        "a worker outlived the probe and is still holding the product's port"
    )


def test_no_routes_is_a_visible_note_not_a_pass(tmp_path):
    root = init_workspace(tmp_path / "empty", "e", "web")
    (root / "FDR.md").write_text("东西")
    probes, notes = generate_probes(root, provider="mock")
    assert probes == []
    assert any("no observed backend routes" in n for n in notes)


def test_a_probe_can_read_the_error_body_the_product_sent():
    """urllib RAISES on 4xx and puts the body on the exception. The frame
    used to return `{}` there, so every probe asserting on an error message
    failed against a product that answered exactly as its contract said —
    and run 7's identical `{}` failures were once "fixed" by tightening the
    *product* prompt, one layer below the actual defect.
    """
    import http.server
    import json
    import threading

    from ai_venture_studio.upstream.probegen import BOOT_FRAME

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"error": "id must be an integer"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # Exercise the frame's own `call` against a real 4xx response:
        # take the helper definitions verbatim, minus the boot block (the
        # server is already up here).
        helpers = BOOT_FRAME[
            BOOT_FRAME.index("class NoRedirect"):BOOT_FRAME.index("\nwait()")
        ]
        frame_src = (
            "import json, time, urllib.request, urllib.error\n"
            f'BASE = "http://127.0.0.1:{server.server_address[1]}"\n' + helpers
        )
        namespace: dict = {}
        exec(compile(frame_src, "<frame>", "exec"), namespace)  # noqa: S102
        status, data, _ = namespace["call"]("GET", "/groupbuys/abc")
    finally:
        server.shutdown()

    assert status == 400
    assert data == {"error": "id must be an integer"}, data


def _frame_helpers(base: str) -> dict:
    """The frame's own helper definitions, bound to an arbitrary BASE.

    Takes them verbatim, minus the boot block, so these tests exercise the
    code the probes actually run rather than a copy of it.
    """
    from ai_venture_studio.upstream.probegen import BOOT_FRAME

    helpers = BOOT_FRAME[
        BOOT_FRAME.index("class NoRedirect"):BOOT_FRAME.index("\nwait()")
    ]
    src = (
        "import json, time, urllib.request, urllib.error\n"
        f'BASE = "{base}"\n' + helpers
    )
    namespace: dict = {}
    exec(compile(src, "<frame>", "exec"), namespace)  # noqa: S102
    return namespace


def test_two_probes_running_back_to_back_do_not_fight_over_one_port():
    """Run 13, case 04: the port was a constant, so the next probe booted
    onto the port the previous probe's server had not finished releasing,
    and the first call came back "connection refused" — scored against the
    product, which had answered nothing at all."""
    import socket

    from ai_venture_studio.upstream.probegen import BOOT_FRAME

    prelude = BOOT_FRAME[: BOOT_FRAME.index("entry =")]
    first: dict = {}
    exec(compile(prelude, "<prelude>", "exec"), first)  # noqa: S102

    # Hold it the way a server that has not finished shutting down does.
    holder = socket.socket()
    holder.bind(("127.0.0.1", first["PORT"]))
    holder.listen(1)
    try:
        second: dict = {}
        exec(compile(prelude, "<prelude>", "exec"), second)  # noqa: S102
        assert second["PORT"] != first["PORT"]
    finally:
        holder.close()


def test_a_socket_that_accepts_and_dies_is_not_a_ready_server():
    """The old readiness check was a bare TCP connect, which succeeds
    against a server that is already closing. Readiness is an answer."""
    import socket
    import threading

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def accept_and_hang_up():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            conn.close()

    threading.Thread(target=accept_and_hang_up, daemon=True).start()
    try:
        frame = _frame_helpers(f"http://127.0.0.1:{port}")
        assert frame["_answers"]() is False
    finally:
        listener.close()


def test_a_probe_says_in_words_when_the_product_never_answered():
    """A bare URLError traceback in the results reads as a product defect.
    The last time one did, it was the harness."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing is listening here now

    frame = _frame_helpers(f"http://127.0.0.1:{port}")
    with pytest.raises(AssertionError) as excinfo:
        frame["call"]("GET", "/anything")
    assert "did not answer" in str(excinfo.value)
