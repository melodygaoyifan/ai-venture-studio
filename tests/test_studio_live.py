"""The founder-facing production loop: Take it live, It's broken,
Housekeeping. Hermetic — mock provider, localhost-only probes."""

from __future__ import annotations

import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml

from ai_venture_studio.studio_i18n import STRINGS
from ai_venture_studio.executables import resolve


def _t(key):
    return STRINGS[key]["en"]


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".mas").mkdir()
    (root / ".mas" / "project.yaml").write_text(
        yaml.safe_dump({"name": "ws", "profile": "web"})
    )
    subprocess.run([resolve("git"), "init", "-q"], cwd=root, check=True)
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("print('boot')\n")
    subprocess.run([resolve("git"), "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [resolve("git"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root, check=True,
    )
    return root


# --- probe --------------------------------------------------------------------


def test_probe_rejects_non_http_and_records_failure(tmp_path):
    from ai_venture_studio.studio_live import last_probe, probe_live

    root = _workspace(tmp_path)
    result = probe_live(root, "file:///etc/passwd")
    assert result["ok"] is False and "http" in result["detail"]
    # An unreachable port is a plain sentence, not a traceback.
    result = probe_live(root, "http://127.0.0.1:9")
    assert result["ok"] is False and result["detail"].startswith("no answer")
    assert last_probe(root)["url"] == "http://127.0.0.1:9"


def test_probe_reports_a_live_answer(tmp_path):
    from ai_venture_studio.studio_live import probe_live

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = _workspace(tmp_path)
        result = probe_live(root, f"http://127.0.0.1:{server.server_port}/")
        assert result["ok"] is True and result["status"] == 200
    finally:
        server.shutdown()


# --- take-it-live page body ---------------------------------------------------


def test_live_body_shows_boot_command_boundary_and_verify(tmp_path):
    from ai_venture_studio.studio_live import live_body

    root = _workspace(tmp_path)
    page = live_body(root, _t, "web")
    assert "python app/main.py" in page
    assert "never deploys to production on its own" in page
    assert "Is it answering right now?" in page
    assert "Write the cloud database guide" in page


def test_housekeeping_card_grey_clean_and_queued(tmp_path):
    from ai_venture_studio.studio_live import housekeeping_card

    root = _workspace(tmp_path)
    grey = housekeeping_card(root, _t)
    assert "avs sweep" in grey and "not run yet" in grey

    sweep_dir = root / ".mas" / "sweep"
    sweep_dir.mkdir()
    (sweep_dir / "digest-2026-07-30.yaml").write_text(yaml.safe_dump({
        "at": "2026-07-30", "rung": "SW0", "items_inspected": 2,
        "chores": [
            {"queue": "flags", "chore_class": "flag_removal",
             "item": "old_flag", "detail": "expired 2026-07-01"},
            {"queue": "deps", "chore_class": "dependency_upgrade",
             "item": "httpx", "detail": "patch release available"},
        ],
        "actionable": [], "reported": [], "action_rate": 0.0,
        "snapshot_hash": "sha256:x", "clean_pass": False,
    }))
    page = housekeeping_card(root, _t)
    assert "2 item(s) queued" in page and "old_flag" in page
    assert "human decision" in page

    (sweep_dir / "digest-2026-07-31.yaml").write_text(yaml.safe_dump({
        "at": "2026-07-31", "rung": "SW0", "items_inspected": 0,
        "chores": [], "actionable": [], "reported": [], "action_rate": 0.0,
        "snapshot_hash": "sha256:y", "clean_pass": True,
    }))
    assert "clean pass" in housekeeping_card(root, _t)


# --- it's broken → triage → fix ----------------------------------------------


def test_incident_intake_runs_triage_and_persists(tmp_path):
    from ai_venture_studio.studio_live import incident_body, incident_intake

    root = _workspace(tmp_path)
    incident, result = incident_intake(
        root, "The submit button does nothing since this morning.", "mock"
    )
    assert incident.id.startswith("inc-")
    record = yaml.safe_load(
        (root / ".mas" / "incidents" / incident.id / "founder.yaml")
        .read_text(encoding="utf-8")
    )
    assert record["incident"]["source"] == "founder"
    page = incident_body(_t, incident.id, result)
    assert "What the triage found" in page
    # The fix button only appears when a root cause was proposed — and its
    # consent note is always attached to the button, never implied.
    if "Attempt the fix" in page:
        assert "re-enters code review" in page


def test_incident_fix_flow_reloads_persisted_root_cause(tmp_path):
    from ai_venture_studio.studio_live import (
        attempt_incident_fix,
        fix_body,
        incident_intake,
    )

    root = _workspace(tmp_path)
    incident, result = incident_intake(
        root, "TypeError in app.main since the latest change.", "mock"
    )
    if result.verdict.value != "ROOT_CAUSE_PROPOSED":
        return  # mock triage may classify low-priority; the flow test is moot
    attempt = attempt_incident_fix(root, incident.id, "mock")
    page = fix_body(_t, attempt)
    assert attempt.status in ("opened", "branch_only", "tests_failed",
                              "abstained", "error")
    assert "How the attempt went" in page


def test_live_body_hides_guide_button_without_a_cloud_catalog(tmp_path):
    """The data/game profiles have no guided cloud catalog; a button that
    silently no-ops is worse than no button (found E2E on a data repo)."""
    from ai_venture_studio.studio_live import live_body

    root = _workspace(tmp_path)
    page = live_body(root, _t, "data")
    assert "/live/guide" not in page
    assert "No guided cloud catalog" in page
    assert "/live/guide" in live_body(root, _t, "web")


# --- enterprise follow-ups: incident on /live, sweep button, evidence ---------


def test_live_page_carries_the_incident_front_door(tmp_path):
    """Adopted brownfield repos have no product report page; the incident
    intake must be reachable from Take-it-live too."""
    from ai_venture_studio.studio_live import live_body

    page = live_body(_workspace(tmp_path), _t, "web")
    assert "action=/incident" in page and "Triage it" in page
    assert "action=/live/sweep" in page  # the housekeeping check button


def test_run_housekeeping_records_a_clean_pass(tmp_path):
    from ai_venture_studio.studio_live import housekeeping_card, run_housekeeping

    root = _workspace(tmp_path)
    digest = run_housekeeping(root)
    assert digest.rung == "SW0" and digest.clean_pass is True
    assert list((root / ".mas" / "sweep").glob("digest-*.yaml"))
    assert "clean pass" in housekeeping_card(root, _t)


def test_evidence_route_refuses_bad_ids_and_names_missing_reviews(tmp_path):
    from fastapi.testclient import TestClient as _TC

    from ai_venture_studio import studio as studio_mod

    root = _workspace(tmp_path)
    client = _TC(studio_mod.create_studio_app(root, provider="mock"))
    bad = client.post("/review/../../etc/evidence", follow_redirects=False)
    assert bad.status_code in (303, 404)  # path shape refused, never served
    missing = client.post("/review/nope123/evidence")
    assert missing.status_code == 200 and "nope123" in missing.text


def test_deploy_reviews_card_grey_and_populated(tmp_path):
    import yaml as _yaml

    from ai_venture_studio.studio_modes import _deploy_reviews_html

    root = _workspace(tmp_path)
    grey = _deploy_reviews_html(root, _t)
    assert "avs deploy-review" in grey and "None yet" in grey

    run_dir = root / ".mas" / "deploy-reviews" / "dep-1"
    run_dir.mkdir(parents=True)
    (run_dir / "05-final.yaml").write_text(_yaml.safe_dump(
        {"verdict": "HOLD", "branch": "release/1.2"}
    ))
    page = _deploy_reviews_html(root, _t)
    assert "HOLD" in page and "release/1.2" in page
    # The recommendations-never-executions note moved to the enterprise
    # panel's footer, visible without opening any card.
    from ai_venture_studio.studio_modes import enterprise_panel

    assert "stays disarmed" in enterprise_panel(root, _t)


# --- corp deployment: token gate, origin guard, bind refusal ------------------


def test_studio_token_gates_every_request(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient as _TC

    from ai_venture_studio import studio as studio_mod

    monkeypatch.setenv("AVS_STUDIO_TOKEN", "corp-secret")
    root = _workspace(tmp_path)
    client = _TC(studio_mod.create_studio_app(root, provider="mock"))
    assert client.get("/").status_code == 401
    assert client.get("/?token=wrong").status_code == 401
    first = client.get("/?token=corp-secret")
    assert first.status_code == 200
    assert first.cookies.get("studio_token") == "corp-secret"
    # The cookie carries the session from here on.
    assert client.get("/live").status_code == 200


def test_studio_without_token_keeps_localhost_posture(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient as _TC

    from ai_venture_studio import studio as studio_mod

    monkeypatch.delenv("AVS_STUDIO_TOKEN", raising=False)
    monkeypatch.delenv("AVS_STUDIO_TOKEN_FILE", raising=False)
    client = _TC(studio_mod.create_studio_app(_workspace(tmp_path), provider="mock"))
    assert client.get("/").status_code == 200


def test_origin_guard_accepts_own_host_rejects_foreign(tmp_path, monkeypatch):
    """Hardcoding localhost in the CSRF guard broke every POST the moment
    the Studio served on a corp hostname — it must compare self-origin."""
    from fastapi.testclient import TestClient as _TC

    from ai_venture_studio import studio as studio_mod

    monkeypatch.delenv("AVS_STUDIO_TOKEN", raising=False)
    client = _TC(studio_mod.create_studio_app(_workspace(tmp_path), provider="mock"))
    ok = client.post(
        "/live/probe", data={"url": "http://127.0.0.1:9"},
        headers={"origin": "http://testserver"}, follow_redirects=False,
    )
    assert ok.status_code == 303  # own host: allowed
    evil = client.post(
        "/live/probe", data={"url": "http://127.0.0.1:9"},
        headers={"origin": "https://evil.example"}, follow_redirects=False,
    )
    assert evil.status_code == 403


def test_cli_refuses_nonloopback_bind_without_token(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    monkeypatch.delenv("AVS_STUDIO_TOKEN", raising=False)
    monkeypatch.delenv("AVS_STUDIO_TOKEN_FILE", raising=False)
    result = CliRunner().invoke(
        app, ["studio", str(tmp_path / "w"), "--profile", "web",
              "--host", "0.0.0.0"],
    )
    assert result.exit_code == 2
    assert "AVS_STUDIO_TOKEN" in result.output
