"""Plan D15 + the D16 remainder: deploy/maintenance as checkpointed graphs
(mid-stage resume via `recover`) and the encrypted checkpointer serde
(doc 09 §3.1 — key honored or loud error, never silent plaintext)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import ai_venture_studio.deploy.graph as deploy_graph
import ai_venture_studio.maintenance.graph as maint_graph
from ai_venture_studio.deploy import DeployVerdict, recover_deploy_reviews, run_deploy_review
from ai_venture_studio.maintenance import (
    Incident,
    MaintenanceVerdict,
    recover_maintenance,
    run_maintenance,
)
from ai_venture_studio.orchestrator.checkpoint import (
    KEY_ENV,
    CheckpointKeyError,
    build_saver,
    encryption_status,
)
from ai_venture_studio.secrets import SecretError
from ai_venture_studio.executables import resolve

SKILLS = str(Path(__file__).parent.parent / "skills" / "deploy")


def _diff(path: str, *added: str) -> str:
    body = "\n".join(f"+{line}" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,0 +1,{len(added)} @@\n{body}\n"
    )


def _repo_with_history(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([resolve("git"), "init", "-q"], cwd=repo, check=True)
    (repo / "billing.py").write_text("def invoice_total(items):\n    return sum(items)\n")
    subprocess.run([resolve("git"), "add", "."], cwd=repo, check=True)
    subprocess.run(
        [resolve("git"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         "billing: invoice_total over items"],
        cwd=repo, check=True,
    )
    return repo


# --- mid-stage resume: deploy ---------------------------------------------------


def test_deploy_review_resumes_after_crash_without_rerunning_probes(
    tmp_path, monkeypatch
):
    calls = {"n": 0}
    real_vote = deploy_graph.vote_node

    def crashing_vote(state, *, mirror):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash mid-vote")
        return real_vote(state, mirror=mirror)

    monkeypatch.setattr(deploy_graph, "vote_node", crashing_vote)
    with pytest.raises(RuntimeError, match="simulated crash"):
        run_deploy_review(
            "bench://resume",
            repo_dir=str(tmp_path),
            skills_dir=SKILLS,
            provider_override="mock",
            diff_text=_diff("helm/values.yaml", "replicaCount: 3"),
        )

    run_dirs = list((tmp_path / ".mas" / "deploy-reviews").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "meta.yaml").exists()
    assert list(run_dir.glob("[0-9]*-probes.yaml"))
    assert not list(run_dir.glob("[0-9]*-final.yaml"))

    results = recover_deploy_reviews(str(tmp_path))
    assert results == [
        {"kind": "deploy", "id": run_dir.name, "status": "recovered",
         "verdict": DeployVerdict.PROMOTE.value}
    ]
    # Mid-stage resume, not a cold restart: probes ran exactly once.
    assert len(list(run_dir.glob("[0-9]*-probes.yaml"))) == 1
    assert len(list(run_dir.glob("[0-9]*-final.yaml"))) == 1
    # A recovered run is done — nothing left to recover.
    assert recover_deploy_reviews(str(tmp_path)) == []


# --- mid-stage resume: maintenance ----------------------------------------------


def test_maintenance_resumes_after_crash_without_repaying_triage(
    tmp_path, monkeypatch
):
    repo = _repo_with_history(tmp_path)
    incident = Incident(
        id="inc-resume", title="TypeError in invoice_total",
        body="TypeError in billing.py invoice_total when items is None",
    )
    calls = {"n": 0}
    real_rootcause = maint_graph.rootcause_node

    def crashing_rootcause(state, *, mirror):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash mid-rootcause")
        return real_rootcause(state, mirror=mirror)

    monkeypatch.setattr(maint_graph, "rootcause_node", crashing_rootcause)
    with pytest.raises(RuntimeError, match="simulated crash"):
        run_maintenance(incident, repo_dir=str(repo), provider="mock")

    run_dir = repo / ".mas" / "incidents" / "inc-resume"
    assert list(run_dir.glob("[0-9]*-triage.yaml"))
    assert not list(run_dir.glob("[0-9]*-final.yaml"))

    results = recover_maintenance(str(repo))
    assert results == [
        {"kind": "incident", "id": "inc-resume", "status": "recovered",
         "verdict": MaintenanceVerdict.ROOT_CAUSE_PROPOSED.value}
    ]
    # intake/correlate/triage were checkpointed — each ran exactly once.
    for step in ("intake", "correlate", "triage", "root_cause", "final"):
        assert len(list(run_dir.glob(f"[0-9]*-{step}.yaml"))) == 1, step
    assert recover_maintenance(str(repo)) == []


# --- encrypted checkpointer serde (doc 09 §3.1) ----------------------------------


CANARY = "XYZZY-PLAINTEXT-CANARY"


def _run_incident(repo: Path) -> None:
    run_maintenance(
        Incident(id="inc-enc", title=f"cosmetic {CANARY}", body="cosmetic only"),
        repo_dir=str(repo), provider="mock",
    )


def _db_bytes(repo: Path) -> bytes:
    """checkpoints.db plus its -wal/-shm sidecars — rows can live in the
    write-ahead log until SQLite checkpoints the main file."""
    base = repo / ".mas" / "checkpoints.db"
    return b"".join(
        p.read_bytes()
        for p in (base, base.with_suffix(".db-wal"), base.with_suffix(".db-shm"))
        if p.exists()
    )


def test_checkpoints_plaintext_without_key_and_meta_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    repo = _repo_with_history(tmp_path)
    _run_incident(repo)
    assert CANARY.encode() in _db_bytes(repo)  # honest baseline: no key, no encryption
    meta = yaml.safe_load(
        (repo / ".mas" / "incidents" / "inc-enc" / "meta.yaml").read_text()
    )
    assert meta["checkpoint_encryption"] == "off"


def test_checkpoints_encrypted_at_rest_with_key(tmp_path, monkeypatch):
    pytest.importorskip("Crypto")
    monkeypatch.setenv(KEY_ENV, "any-length-passphrase-works")  # sha256-derived
    assert encryption_status() == "aes"
    repo = _repo_with_history(tmp_path)
    _run_incident(repo)
    assert CANARY.encode() not in _db_bytes(repo)
    meta = yaml.safe_load(
        (repo / ".mas" / "incidents" / "inc-enc" / "meta.yaml").read_text()
    )
    assert meta["checkpoint_encryption"] == "aes"
    # The mirror stays deliberately plaintext (§09.6 asymmetry).
    intake = next((repo / ".mas" / "incidents" / "inc-enc").glob("[0-9]*-intake.yaml"))
    assert CANARY in intake.read_text()
    # And an encrypted run still resumes: same key, fresh graph, same thread.
    assert recover_maintenance(str(repo)) == []


def test_key_without_pycryptodome_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "0123456789abcdef")
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.serde.encrypted", None)
    with pytest.raises(CheckpointKeyError, match="refusing"):
        build_saver(tmp_path)


def test_secret_ref_key_resolves_or_errors_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "secret://CHECKPOINT_KEY")
    monkeypatch.delenv("CHECKPOINT_KEY", raising=False)
    with pytest.raises(SecretError, match="CHECKPOINT_KEY"):
        build_saver(tmp_path)
    pytest.importorskip("Crypto")
    monkeypatch.setenv("CHECKPOINT_KEY", "0123456789abcdef0123456789abcdef")
    assert build_saver(tmp_path) is not None
