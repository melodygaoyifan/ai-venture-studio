"""Gate 2 — Test Gate (§09.5.4.10) with T3 sandbox and mutation testing.

The reviewed change is applied to an isolated git worktree; the project's
suite runs there — never in the user's checkout. An APPROVE-class verdict
cannot survive a failing (or unrunnable) suite.

Sandbox tiers:
- docker (T3, used automatically when the docker daemon is available):
  dependencies sync inside a container on the network, then the container
  is DISCONNECTED from the network before the suite runs — untrusted test
  code executes with no network and only the worktree mounted.
- subprocess (fallback): visible in the report as unsandboxed; acceptable
  for trusted repos only.

Mutation testing (deep mode, §08.2.2.6): mutmut mutates only the files the
diff changed; surviving mutants mean the tests don't actually constrain
the changed code. Score < 60% blocks APPROVE-class verdicts.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

_TEST_TIMEOUT_S = 300
# Dump the hung test's traceback well before the suite is killed, so the
# report says WHICH test hung instead of only that something did.
_HANG_DUMP_S = 120
_MUTATION_TIMEOUT_S = 300
_DOCKER_IMAGE = "python:3.12-slim"
MUTATION_SCORE_MIN = 0.60


class MutationReport(BaseModel):
    status: Literal["passed", "failed", "skipped", "error"]
    summary: str
    killed: int = 0
    survived: int = 0
    survivors: list[str] = []

    @property
    def score(self) -> float | None:
        total = self.killed + self.survived
        return self.killed / total if total else None


class TestReport(BaseModel):
    status: Literal["passed", "failed", "no_tests", "skipped", "error"]
    summary: str
    detail: str = ""
    sandbox: str = "subprocess"
    mutation: MutationReport | None = None

    @property
    def gate_blocks(self) -> bool:
        # 'error' blocks too: an APPROVE issued without a runnable suite
        # would violate charter rule 9 (test before done). Found by the
        # self-review of PR #3.
        if self.status in ("failed", "error"):
            return True
        return bool(self.mutation and self.mutation.status == "failed")


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Kill the whole tree, not just the process we launched.

    `subprocess.run`'s own timeout path calls `proc.kill()`, which signals the
    direct child alone. A product whose tests start a server leaves that
    server RUNNING after the timeout — holding its port against the next case
    and holding the stdout pipe it inherited, so the read that was supposed to
    end at the timeout can block past it. Measured: with a plain kill the
    grandchild outlives the timeout every time.
    """
    if not hasattr(os, "killpg"):  # pragma: no cover — POSIX-only path
        proc.kill()
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def _run(
    cmd: list[str], cwd: str | Path, timeout: int | None = None,
    env: dict[str, str] | None = None,
):
    """Run a command, and on timeout kill its process GROUP and keep its output.

    The timeout resolves at CALL time, not at import time: a default argument
    written as `timeout=_TEST_TIMEOUT_S` would freeze the module constant into
    the function object, so a test that lowers the constant would still wait
    the full five minutes while the report claimed the shorter budget.

    `start_new_session` is what makes the group killable: it puts the child in
    a session of its own, so one signal reaches everything it spawned.

    The re-raised `TimeoutExpired` carries `output`/`stderr` as text. CPython's
    own timeout path leaves them as raw bytes even under `text=True`, which is
    a quiet trap for any caller that tries to read what the hung command had
    printed — and reading that is the entire point of keeping it.
    """
    timeout = _TEST_TIMEOUT_S if timeout is None else timeout
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        start_new_session=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            # The group is dead, so both pipes are closed by every writer and
            # this returns rather than waiting on an orphan that outlived the
            # timeout.
            stdout, stderr = proc.communicate()
            raise subprocess.TimeoutExpired(
                cmd, timeout, output=stdout, stderr=stderr
            ) from None
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _has_tests(root: Path) -> bool:
    return any(root.glob("tests/**/test_*.py")) or any(root.glob("test_*.py"))


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return _run(["docker", "info", "--format", "ok"], ".", timeout=10).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def run_test_gate(
    repo_dir: str, diff_raw: str, *, mode: str = "standard",
    changed_files: list[str] | None = None,
) -> TestReport:
    repo = Path(repo_dir).resolve()
    if not (repo / ".git").exists():
        return TestReport(
            status="skipped", summary="not a git repository; test gate skipped"
        )

    worktree = Path(tempfile.mkdtemp(prefix="autoproduct-testgate-"))
    try:
        return _run_gate_in_worktree(
            repo, worktree, diff_raw, mode=mode, changed_files=changed_files or []
        )
    except subprocess.TimeoutExpired as exc:
        return TestReport(status="error", summary=f"timed out: {str(exc)[:200]}")
    finally:
        _run(["git", "worktree", "remove", "--force", str(worktree)], repo)
        shutil.rmtree(worktree, ignore_errors=True)


def _run_gate_in_worktree(
    repo: Path, worktree: Path, diff_raw: str, *, mode: str, changed_files: list[str]
) -> TestReport:
    added = _run(["git", "worktree", "add", "--detach", str(worktree), "HEAD"], repo)
    if added.returncode != 0:
        return TestReport(
            status="error",
            summary="could not create isolated worktree",
            detail=added.stderr[:400],
        )
    if diff_raw.strip():
        patch = worktree / ".ai_venture_studio.patch"
        patch.write_text(diff_raw, encoding="utf-8")
        applied = _run(["git", "apply", "--3way", patch.name], worktree)
        patch.unlink(missing_ok=True)
        if applied.returncode != 0:
            return TestReport(
                status="error",
                summary="reviewed diff did not apply cleanly to HEAD",
                detail=applied.stderr[:400],
            )
    if not _has_tests(worktree):
        return TestReport(status="no_tests", summary="no test files found in the project")

    use_docker = mode == "deep" and docker_available()
    if use_docker:
        report = _pytest_in_docker(worktree)
    else:
        report = _pytest_in_subprocess(worktree)
    report = combine_reports(report, run_js_tests(worktree))

    if mode == "deep" and report.status == "passed":
        report.mutation = run_mutation(worktree, changed_files)
    return report


def _classify(returncode: int, output: str, *, sandbox: str) -> TestReport:
    tail = "\n".join(output.strip().splitlines()[-15:])
    if returncode == 0:
        return TestReport(status="passed", summary=_last_line(tail), detail=tail, sandbox=sandbox)
    if returncode == 5:  # pytest: no tests collected
        return TestReport(status="no_tests", summary="pytest collected no tests", sandbox=sandbox)
    return TestReport(status="failed", summary=_last_line(tail), detail=tail, sandbox=sandbox)


def pytest_flags() -> list[str]:
    """`-q`, plus the flag that makes a hang describe itself.

    `faulthandler_timeout` makes pytest dump the traceback of every thread
    when a single test outruns it — naming the test and the exact line it is
    stuck on. It only prints; the run continues, so a merely slow suite is
    unaffected. Set below `_TEST_TIMEOUT_S` so the dump happens while the
    process is still alive to write it, and well above any honest suite
    (the bench's real products finish in tens of seconds).
    """
    return ["-q", "-o", f"faulthandler_timeout={_HANG_DUMP_S}"]


def pytest_cmd(worktree: Path) -> list[str]:
    if (worktree / "uv.lock").exists() and shutil.which("uv"):
        return ["uv", "run", "--project", str(worktree), "pytest", *pytest_flags()]
    return [sys.executable, "-m", "pytest", *pytest_flags()]


def _as_text(chunk: object) -> str:
    if not chunk:
        return ""
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8", "replace")
    return str(chunk)


def _clip(text: str, head: int, tail: int) -> str:
    """Keep BOTH ends of a long capture, never just the tail.

    faulthandler prints each stack most-recent-call-first, so the hung test's
    own frame is the FIRST line of the dump and the pytest/pluggy internals
    are the rest. A tail-only clip therefore drops the only line that names
    the test and keeps a page of runner plumbing — which is what the first
    version of this did.
    """
    if len(text) <= head + tail:
        return text
    elided = len(text) - head - tail
    return f"{text[:head]}\n[... {elided} characters elided ...]\n{text[-tail:]}"


def _hang_detail(cmd: list[str], exc: subprocess.TimeoutExpired) -> str:
    """Everything the suite had printed before it was killed.

    This used to be the command line and nothing else, and that is exactly
    why bench run 12's hang was never explained: `TimeoutExpired` was
    carrying the output the whole time — including pytest's faulthandler
    dump, which names the test and the line it is stuck on — and the report
    threw it away and said only that 300s had elapsed. A timeout report that
    omits what the process said is the harness discarding the one piece of
    evidence that identifies the cause.

    stderr comes first because the faulthandler dump goes there.
    """
    parts = [" ".join(str(part) for part in cmd)]
    err = _as_text(getattr(exc, "stderr", None)).strip()
    out = _as_text(getattr(exc, "output", None)).strip()
    if err:
        parts.append(
            "--- stderr (a faulthandler dump names the hung test) ---\n"
            + _clip(err, 2400, 800)
        )
    if out:
        parts.append("--- stdout ---\n" + _clip(out, 400, 800))
    if not err and not out:
        parts.append("the command printed nothing before it was killed")
    return "\n\n".join(parts)


def _run_and_classify(
    cmd: list[str], worktree: Path, *, sandbox: str = "subprocess"
) -> TestReport:
    """A suite that hangs is a BLOCKED GATE, not a dead process.

    `run_test_gate` catches `TimeoutExpired` for its own path, but four
    other callers (build, autopilot, correction, fixpr) reach the runners
    directly and had no guard — so one product whose tests never returned
    raised out of everything above it. In the bench that killed the whole
    case, which then scored zero and pulled the rates the kill criterion
    reads. `error` already blocks the gate: an unprovable suite must not
    pass, and it must not take the run down either.
    """
    try:
        proc = _run(cmd, worktree)
    except subprocess.TimeoutExpired as exc:
        return TestReport(
            status="error",
            summary=f"test command exceeded {_TEST_TIMEOUT_S}s and was killed",
            detail=_hang_detail(cmd, exc),
            sandbox=sandbox,
        )
    return _classify(proc.returncode, proc.stdout or proc.stderr, sandbox=sandbox)


def _pytest_in_subprocess(worktree: Path) -> TestReport:
    return _run_and_classify(pytest_cmd(worktree), worktree)


def _has_js_tests(root: Path) -> bool:
    return any(root.glob("tests/**/*.test.js")) or any(root.glob("**/*.test.js"))


def run_js_tests(worktree: Path) -> TestReport | None:
    """JS/小程序 test gate: `npm test` when package.json defines it, else
    `node --test` over *.test.js files. None means no JS surface; a missing
    node runtime is a VISIBLE skip, never a silent pass."""
    if not _has_js_tests(worktree) and not (worktree / "package.json").exists():
        return None
    if not shutil.which("node"):
        return TestReport(
            status="skipped",
            summary="JS tests present but node is not installed — install "
            "Node.js to gate them (they currently pass on review alone)",
        )
    package = worktree / "package.json"
    if package.exists() and shutil.which("npm"):
        import json

        scripts = (json.loads(package.read_text(encoding="utf-8")) or {}).get("scripts", {})
        if "test" in scripts:
            return _run_and_classify(["npm", "test", "--silent"], worktree)
    # NEVER inside .mas: it holds preserved copies of FAILED attempts
    # (.mas/failed-builds/), and pathlib's ** walks hidden directories. Once
    # one task failed, every later task's gate was running that task's broken
    # snapshot as if it were the product — 31 of 37 matched files in one real
    # run. node_modules and .git are excluded for the obvious reasons.
    _SKIP = {".mas", "node_modules", ".git", ".venv"}
    test_files = sorted(
        str(p.relative_to(worktree))
        for p in worktree.glob("**/*.test.js")
        if not _SKIP.intersection(p.relative_to(worktree).parts)
    )
    if not test_files:
        return None
    return _run_and_classify(["node", "--test", *test_files], worktree)


def combine_reports(python_report: TestReport, js_report: TestReport | None) -> TestReport:
    """Both runners must pass; either failing fails the gate; two empty
    surfaces stay no_tests."""
    if js_report is None:
        return python_report
    if python_report.status == "no_tests":
        merged = js_report
    elif js_report.status in ("failed", "error"):
        merged = js_report
    elif python_report.status in ("failed", "error"):
        merged = python_report
    else:
        merged = python_report
    merged.summary = (
        f"py: {python_report.summary or python_report.status} · "
        f"js: {js_report.summary or js_report.status}"
    )
    return merged


def _pytest_in_docker(worktree: Path) -> TestReport:
    """T3: sync dependencies with the network up, disconnect the container
    from every network, then run the suite — test code gets no network."""
    name = f"autoproduct-t3-{uuid.uuid4().hex[:8]}"
    try:
        created = _run(
            [
                "docker", "run", "-d", "--name", name,
                "-v", f"{worktree}:/work", "-w", "/work",
                "--memory", "2g", "--pids-limit", "512",
                _DOCKER_IMAGE, "sleep", "3600",
            ],
            worktree,
            timeout=60,
        )
        if created.returncode != 0:
            return _pytest_in_subprocess(worktree)  # sandbox unavailable → visible fallback
        # git is part of the sandbox toolchain (worktree-based suites need
        # it); installed during the network-up sync phase.
        base_sync = "apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null"
        if (worktree / "uv.lock").exists():
            sync_cmd = f"{base_sync} && pip install -q uv && uv sync --project /work --quiet"
            test_cmd = ["uv", "run", "--project", "/work", "--no-sync", "pytest", *pytest_flags()]
        else:
            sync_cmd = f"{base_sync} && pip install -q pytest"
            test_cmd = ["python", "-m", "pytest", *pytest_flags()]
        sync_cmd_argv = ["docker", "exec", name, "sh", "-c", sync_cmd]
        try:
            sync = _run(sync_cmd_argv, worktree, timeout=_TEST_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            # A wedged `apt-get`/`uv sync` is a blocked gate, not a crash.
            return TestReport(
                status="error",
                summary=f"dependency sync exceeded {_TEST_TIMEOUT_S}s and was killed",
                detail=_hang_detail(sync_cmd_argv, exc),
                sandbox="docker",
            )
        if sync.returncode != 0:
            return TestReport(
                status="error",
                summary="dependency sync failed inside sandbox",
                detail=(sync.stderr or sync.stdout)[-400:],
                sandbox="docker",
            )
        # Disconnect every network the container is attached to, then VERIFY
        # none remain — an unverified disconnect would make the
        # "no-network" label a false guarantee (self-review of PR #8).
        inspect_fmt = "{{range $k, $_ := .NetworkSettings.Networks}}{{$k}} {{end}}"
        nets = _run(
            ["docker", "inspect", "-f", inspect_fmt, name], worktree, timeout=30
        ).stdout.split()
        for net in nets:
            _run(["docker", "network", "disconnect", net, name], worktree, timeout=30)
        remaining = _run(
            ["docker", "inspect", "-f", inspect_fmt, name], worktree, timeout=30
        ).stdout.split()
        if remaining:
            return TestReport(
                status="error",
                summary=f"could not isolate sandbox network (still attached: {remaining})",
                sandbox="docker",
            )
        # Via _run_and_classify, not a bare _run: the sandboxed suite can hang
        # exactly like the subprocess one, and an unguarded TimeoutExpired here
        # would raise straight through every caller — the failure shape that
        # took bench run 12's case down with it.
        return _run_and_classify(
            ["docker", "exec", name, *test_cmd], worktree, sandbox="docker:no-network"
        )
    finally:
        _run(["docker", "rm", "-f", name], worktree, timeout=30)


_KILLED = re.compile(r"🎉 (\d+)")
# "no tests" counts as survived: mutmut 3 skips mutants no test exercises,
# and untested changed code is exactly what this gate exists to catch.
_SURVIVOR_LINE = re.compile(r"^\s{4}(\S+): (?:survived|no tests)", re.MULTILINE)


def run_mutation(worktree: Path, changed_files: list[str]) -> MutationReport:
    """Mutate only the .py source files the diff touched (§08.2.2.6)."""
    targets = [
        f for f in changed_files
        if f.endswith(".py") and not Path(f).name.startswith("test_")
        and "tests/" not in f and (worktree / f).exists()
    ]
    if not targets:
        return MutationReport(status="skipped", summary="no mutatable changed files")
    if not shutil.which("mutmut") and not _mutmut_in_env(worktree):
        return MutationReport(status="skipped", summary="mutmut not installed")

    pyproject = worktree / "pyproject.toml"
    # mutmut runs the suite against its mutants/ copy, which contains ONLY
    # source_paths + also_copy — everything the tests touch must be copied
    # or imports/data lookups break (found on the PR #9 self-review).
    copy_dirs = sorted(
        f"{d.name}/"
        for d in worktree.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name != "mutants"
    )
    config = (
        "\n[tool.mutmut]\nsource_paths = ["
        + ", ".join(f'"{t}"' for t in targets)
        + "]\nalso_copy = ["
        + ", ".join(f'"{d}"' for d in copy_dirs)
        + "]\n"
    )
    pyproject.write_text(
        (pyproject.read_text(encoding="utf-8") if pyproject.exists() else "") + config,
        encoding="utf-8",
    )
    try:
        run = _run(_mutmut_cmd(worktree, "run"), worktree, timeout=_MUTATION_TIMEOUT_S)
        results = _run(_mutmut_cmd(worktree, "results"), worktree, timeout=60)
    except subprocess.TimeoutExpired:
        return MutationReport(
            status="error", summary=f"mutation run exceeded {_MUTATION_TIMEOUT_S}s"
        )
    killed_match = _KILLED.findall(run.stdout or "")
    killed = int(killed_match[-1]) if killed_match else 0
    survivors = _SURVIVOR_LINE.findall(results.stdout or "")
    report = MutationReport(
        status="passed",
        summary="",
        killed=killed,
        survived=len(survivors),
        survivors=survivors[:20],
    )
    if report.score is None:
        return MutationReport(
            status="error",
            summary="mutmut produced no mutants",
            survivors=[],
        )
    report.status = "passed" if report.score >= MUTATION_SCORE_MIN else "failed"
    report.summary = (
        f"mutation score {report.score:.0%} "
        f"({report.killed} killed / {report.survived} survived; bar {MUTATION_SCORE_MIN:.0%})"
    )
    return report


def _mutmut_in_env(worktree: Path) -> bool:
    return (worktree / "uv.lock").exists() and shutil.which("uv") is not None


def _mutmut_cmd(worktree: Path, subcommand: str) -> list[str]:
    # A uv project must run mutmut inside ITS environment (--with injects
    # mutmut if the project doesn't depend on it) — running the host env's
    # mutmut resolves imports against the host checkout, so worktree
    # mutations never load and every mutant reports "no tests" (found on
    # the PR #9 self-review).
    if _mutmut_in_env(worktree):
        return [
            "uv", "run", "--project", str(worktree), "--with", "mutmut",
            "mutmut", subcommand,
        ]
    if shutil.which("mutmut"):
        return ["mutmut", subcommand]
    return [sys.executable, "-m", "mutmut", subcommand]


def _last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "(no output)"
