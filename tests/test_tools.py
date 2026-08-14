from ai_venture_studio.diff import parse_unified_diff
from ai_venture_studio.tools import external, probes


def _diff(path: str, *added: str) -> str:
    body = "\n".join(f"+{line}" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,0 +1,{len(added)} @@\n{body}\n"
    )


def test_secret_scan_catches_aws_key():
    diff = parse_unified_diff(_diff("config.py", 'AWS_KEY = "AKIAIOSFODNN7REALKEY"'))
    report = probes.secret_scan(diff, ".")
    assert len(report.findings) == 1
    assert report.findings[0].severity.value == "critical"
    assert report.findings[0].verification == "VERIFIED"


def test_secret_scan_skips_placeholder_assignment_values():
    diff = parse_unified_diff(
        _diff("tests/conftest.py", 'SECRET = "test-webhook-secret-value"')
    )
    assert probes.secret_scan(diff, ".").findings == []


def test_secret_scan_still_catches_realistic_assignments():
    diff = parse_unified_diff(
        _diff("config.py", 'API_KEY = "kJ9mPq2xVn8LwRt5YbCd3FgH"')
    )
    assert len(probes.secret_scan(diff, ".").findings) == 1


def test_secret_scan_skips_documentation_example_keys():
    diff = parse_unified_diff(_diff("config.py", 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'))
    assert probes.secret_scan(diff, ".").findings == []


def test_csrf_ssrf_probe_ignores_data_files():
    diff = parse_unified_diff(
        _diff("benchmarks/cases/08.yaml", "    resp = requests.get(callback_url)")
    )
    assert probes.csrf_ssrf_probe(diff, ".").findings == []


def test_secret_scan_clean_diff():
    diff = parse_unified_diff(_diff("config.py", "DEBUG = False"))
    assert probes.secret_scan(diff, ".").findings == []


def test_csrf_probe_flags_unprotected_endpoint():
    diff = parse_unified_diff(
        _diff("views.py", '@app.post("/orders/cancel")', "def cancel(): ...")
    )
    report = probes.csrf_ssrf_probe(diff, ".")
    assert any("CSRF" in f.title for f in report.findings)


def test_csrf_probe_quiet_when_protection_visible():
    diff = parse_unified_diff(
        _diff("views.py", '@app.post("/x")', "@csrf_protect", "def x(): ...")
    )
    assert probes.csrf_ssrf_probe(diff, ".").findings == []


def test_ssrf_probe_flags_variable_url():
    diff = parse_unified_diff(_diff("client.py", "resp = requests.get(user_url)"))
    report = probes.csrf_ssrf_probe(diff, ".")
    assert any("SSRF" in f.title for f in report.findings)


def test_csrf_ssrf_probe_skips_test_support_files():
    """Test clients hit variable localhost URLs by design — flagging them
    buried runs 7-9's reviews in SSRF noise (clean reviews pinned at 0%)."""
    for path in ("tests/helpers.py", "tests/test_client.py", "conftest.py",
                 "app/tests/conftest.py"):
        diff = parse_unified_diff(_diff(path, "resp = requests.get(base_url)"))
        assert probes.csrf_ssrf_probe(diff, ".").findings == [], path
    # Production code stays flagged.
    diff = parse_unified_diff(_diff("app/client.py", "resp = requests.get(u)"))
    assert probes.csrf_ssrf_probe(diff, ".").findings != []


def test_ssrf_probe_allows_literal_url():
    diff = parse_unified_diff(
        _diff("client.py", 'resp = requests.get("https://api.example.com/v1")')
    )
    assert probes.csrf_ssrf_probe(diff, ".").findings == []


def _dep_diff(*lines: str) -> str:
    return _diff("requirements.txt", *lines)


def test_slopsquat_nonexistent_package():
    diff = parse_unified_diff(_dep_diff("definitely-hallucinated-pkg==1.0"))
    report = probes.slopsquat_check(diff, ".", fetcher=lambda name: None)
    assert len(report.findings) == 1
    assert "does not exist" in report.findings[0].title


def test_slopsquat_typosquat_detected_without_registry():
    calls = []
    diff = parse_unified_diff(_dep_diff("reqeusts==2.0"))
    report = probes.slopsquat_check(
        diff, ".", fetcher=lambda name: calls.append(name)
    )
    assert "typosquat" in report.findings[0].title
    assert calls == []  # typosquat verdict needs no network


def test_slopsquat_young_package_flagged():
    diff = parse_unified_diff(_dep_diff("brand-new-pkg==0.1"))
    report = probes.slopsquat_check(
        diff, ".", fetcher=lambda name: {"first_upload_days": 5}
    )
    assert "<30 days" in report.findings[0].title


def test_slopsquat_established_package_clean():
    diff = parse_unified_diff(_dep_diff("requests>=2.31"))
    report = probes.slopsquat_check(
        diff, ".", fetcher=lambda name: {"first_upload_days": 4000}
    )
    assert report.findings == []


def test_external_tools_skip_when_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    diff = parse_unified_diff(_diff("a.py", "x = 1"))
    for runner in (external.semgrep, external.bandit, external.pip_audit, external.trufflehog):
        report = runner(diff, ".")
        assert report.status == "skipped"
        assert "not installed" in report.detail


def test_a_static_analysis_hit_on_a_test_file_is_a_note_not_a_blocker(
    monkeypatch, tmp_path
):
    """ADR-039. This was a per-test_id patch and the class kept recurring.

    B310 (urllib audit) on a test file flags the suite's own localhost
    client — 30 of 44 review findings in bench run 11 — so B310 was skipped
    by name. Then B306 (`tempfile.mktemp` in a fixture) did exactly the same
    thing in run 13, 9 of the 15 blocking findings, through the door the
    named patch left open. The rule is now the class: analyzer findings on
    test scaffolding report at LOW (visible, never blocking); production
    paths keep the full audit; credential checks keep full severity
    everywhere, because a hardcoded password in a fixture is a real leak.
    """
    import json as _json
    import shutil as _shutil

    from ai_venture_studio.leader import ACTIONABLE_SEVERITIES
    from ai_venture_studio.state import Severity
    from ai_venture_studio.tools import external

    (tmp_path / "tests").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import urllib.request\n")
    (tmp_path / "app" / "client.py").write_text("import urllib.request\n")
    diff = parse_unified_diff(
        _diff("tests/test_x.py", "import urllib.request")
        + _diff("app/client.py", "import urllib.request")
    )
    results = {"results": [
        {"filename": "tests/test_x.py", "line_number": 1, "test_id": "B310",
         "test_name": "blacklist", "issue_severity": "MEDIUM",
         "issue_text": "urlopen audit", "code": "urllib.request.urlopen(u)"},
        {"filename": "app/client.py", "line_number": 1, "test_id": "B310",
         "test_name": "blacklist", "issue_severity": "MEDIUM",
         "issue_text": "urlopen audit", "code": "urllib.request.urlopen(u)"},
        # The check that recurred under a NEW id after B310 was named.
        {"filename": "tests/test_x.py", "line_number": 1, "test_id": "B306",
         "test_name": "blacklist", "issue_severity": "MEDIUM",
         "issue_text": "mktemp", "code": "tempfile.mktemp()"},
        {"filename": "tests/test_x.py", "line_number": 1, "test_id": "B105",
         "test_name": "hardcoded_password_string", "issue_severity": "MEDIUM",
         "issue_text": "password", "code": "PW = 'hunter2'"},
    ]}
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(external, "_run_json", lambda cmd, cwd: (_json.dumps(results), ""))
    report = external.bandit(diff, str(tmp_path))
    by_id = {(f.file_path, f.title.split(":")[0]): f for f in report.findings}

    # Reported, not dropped — a real issue in a fixture stays visible.
    assert ("tests/test_x.py", "B310") in by_id
    assert ("tests/test_x.py", "B306") in by_id
    for test_id in ("B310", "B306"):
        assert by_id[("tests/test_x.py", test_id)].severity is Severity.LOW
        assert (
            by_id[("tests/test_x.py", test_id)].severity
            not in ACTIONABLE_SEVERITIES
        ), f"{test_id} on a test file can still block a verdict"

    # Production code keeps the full audit at full severity.
    assert by_id[("app/client.py", "B310")].severity is Severity.MEDIUM
    # Credentials are the one class where "it's only a test" is no defence.
    assert by_id[("tests/test_x.py", "B105")].severity is Severity.MEDIUM
