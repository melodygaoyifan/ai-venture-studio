"""小程序 deterministic checks (doc 17 §43.1) — the named preflights.

mp_size_check (the 2MB main-package budget, per compiled target),
mp_domain_check (every request host on the declared whitelist),
mp_setdata_lint (oversized/hot-path setData payload patterns),
mp_privacy_check (授权 APIs demand a declared 隐私协议 entry + lazy
authorization — request at point of use, never at launch).
"""

from __future__ import annotations

import re

from pydantic import BaseModel
from ai_venture_studio.executables import find

MAIN_PACKAGE_BUDGET_BYTES = 2 * 1024 * 1024  # platform limit, verify-at-adoption
_PRIVACY_APIS = ("getUserProfile", "getLocation", "chooseAddress",
                 "getPhoneNumber", "chooseImage", "getWeRunData")
_URL = re.compile(r"https?://([\w.-]+)")
_SETDATA = re.compile(r"\.setData\(")


class MpFinding(BaseModel):
    check: str
    rule: str
    message: str


def mp_size_check(compiled_sizes: dict[str, int]) -> list[MpFinding]:
    """Per compiled TARGET (F-17.6): dev-tools size lies about the dist."""
    return [
        MpFinding(check="mp_size_check", rule="package_over_budget",
                  message=f"{target}: {size} bytes exceeds the "
                          f"{MAIN_PACKAGE_BUDGET_BYTES}-byte main-package budget")
        for target, size in sorted(compiled_sizes.items())
        if size > MAIN_PACKAGE_BUDGET_BYTES
    ]


def mp_domain_check(sources: dict[str, str], whitelist: list[str]) -> list[MpFinding]:
    findings = []
    allowed = {d.lower() for d in whitelist}
    for path, source in sorted(sources.items()):
        for match in _URL.finditer(source):
            host = match.group(1).lower()
            if host not in allowed:
                findings.append(MpFinding(
                    check="mp_domain_check", rule="undeclared_domain",
                    message=f"{path}: {host} is not on the request whitelist — "
                            "the platform will block it in production"))
    return findings


def mp_setdata_lint(sources: dict[str, str], *, max_calls_per_file: int = 8) -> list[MpFinding]:
    findings = []
    for path, source in sorted(sources.items()):
        calls = len(_SETDATA.findall(source))
        if calls > max_calls_per_file:
            findings.append(MpFinding(
                check="mp_setdata_lint", rule="setdata_hot_path",
                message=f"{path}: {calls} setData calls — batch updates; "
                        "per-frame setData is the classic jank source"))
        if re.search(r"setData\(\s*\{\s*[\w.]*list", source) and "slice" not in source:
            findings.append(MpFinding(
                check="mp_setdata_lint", rule="unbounded_setdata_payload",
                message=f"{path}: whole-list setData without pagination/slicing"))
    return findings


def mp_privacy_check(
    sources: dict[str, str], *, privacy_agreement_declared: bool,
    launch_files: tuple[str, ...] = ("app.js", "app.ts"),
) -> list[MpFinding]:
    findings = []
    for path, source in sorted(sources.items()):
        used = [api for api in _PRIVACY_APIS if api in source]
        if used and not privacy_agreement_declared:
            findings.append(MpFinding(
                check="mp_privacy_check", rule="missing_privacy_agreement",
                message=f"{path}: uses {used} with no declared 隐私协议 entry"))
        if any(path.endswith(f) for f in launch_files) and used:
            findings.append(MpFinding(
                check="mp_privacy_check", rule="eager_authorization",
                message=f"{path}: 授权 at launch ({used}) — request lazily, at "
                        "the point of use; eager prompts are a review rejection"))
    return findings


# --- runtime verification (item P1.3's sibling: does it actually LOAD?) ------
#
# `_miniprogram_gate` in build.py is STATIC — it reads app.json and the page
# files and answers "would DevTools open this project". That gate exists
# because a run once built nine modules and seven page directories with no
# app.json at all. What it cannot answer is whether the pages then RENDER,
# and until now the only thing that could was a human opening DevTools —
# which is why every "it works" claim about a 小程序 ended with an unverified
# promise.
#
# It is verifiable, with preconditions this module refuses to paper over:
#
#   1. WeChat DevTools is a desktop application. macOS and Windows only,
#      no Linux, so this can never be a CI gate — `ubuntu-latest` cannot
#      run it at all.
#   2. `miniprogram-automator` (npm) drives it. Not a dependency of this
#      package; the workspace installs it or the check is skipped.
#   3. DevTools' **service port must be enabled by a human**, once, in
#      Settings → Security. This is the wall a first attempt hits:
#         [error] IDE service port disabled. To use CLI Call, please ...
#                 set Service Port On.
#      It is a security setting on someone's machine and the framework has
#      no business flipping it — so the check names it and stops.
#
# Every one of those is a VISIBLE skip naming the remedy, never a silent
# pass: "no runtime check ran" and "the pages render" must never look alike.

DEVTOOLS_CLI_MACOS = "/Applications/wechatwebdevtools.app/Contents/MacOS/cli"
DEVTOOLS_CLI_WINDOWS = r"C:\Program Files (x86)\Tencent\微信web开发者工具\cli.bat"
_SERVICE_PORT_DISABLED = "service port disabled"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _cli_reported_auto_ok(log_text: str) -> bool:
    """Did `cli auto` print its success marker (`✔ auto`) yet?

    Matched as a whole line with the decoration stripped, never as a bare
    substring: this workspace's own path contains "auto", so a substring
    test would call every failure a success.
    """
    for line in _ANSI.sub("", log_text).splitlines():
        if line.strip().lstrip("✔✓-").strip() == "auto":
            return True
    return False


class MpRuntimeReport(BaseModel):
    status: str  # ok | failed | skipped
    detail: str = ""
    pages_checked: list[str] = []
    findings: list[MpFinding] = []
    #: Where the per-page PNGs landed (.mas/mp-runtime/). "Rendered" from the
    #: protocol only means reLaunch did not throw — the screenshots are the
    #: evidence a human can actually judge blankness by.
    screenshot_dir: str = ""


def devtools_cli(explicit: str | None = None) -> str | None:
    """The DevTools CLI, or None when the desktop app is not installed."""
    import os
    import pathlib

    for candidate in (explicit, os.environ.get("AUTOPRODUCT_DEVTOOLS_CLI"),
                      DEVTOOLS_CLI_MACOS, DEVTOOLS_CLI_WINDOWS):
        if candidate and pathlib.Path(candidate).exists():
            return str(candidate)
    return None


def mp_runtime_check(
    repo_dir, *, timeout_s: int = 120, cli_path: str | None = None
) -> MpRuntimeReport:
    """Open the project in DevTools automation and visit every page.

    Returns `skipped` — loudly, with the remedy — when the desktop app,
    the automator package, or the human-set service port is missing.
    """
    import json
    import pathlib
    import subprocess
    import tempfile

    root = pathlib.Path(repo_dir).resolve()
    cli = devtools_cli(cli_path)
    if cli is None:
        return MpRuntimeReport(
            status="skipped",
            detail="WeChat DevTools is not installed (looked in "
                   f"{DEVTOOLS_CLI_MACOS}). Runtime verification needs the "
                   "desktop app and runs on macOS/Windows only — it can never "
                   "run in CI. The static loadability gate still applies.",
        )
    # The guard already looked `node` up and then threw the answer away,
    # leaving PATH to choose again at the call below — the gap between "we
    # checked node exists" and "we ran whatever node resolves to now"
    # (ADR-064). Same lookup, and now its result is what gets run.
    node = find("node")
    if node is None:
        return MpRuntimeReport(
            status="skipped",
            detail="node is not on PATH; miniprogram-automator is a node package.",
        )
    driver_dir = _automator_root(root)
    if driver_dir is None:
        return MpRuntimeReport(
            status="skipped",
            detail="miniprogram-automator is not installed. From the "
                   f"workspace: `npm i -D miniprogram-automator` ({root}).",
        )

    pages = _registered_pages(root)
    if not pages:
        return MpRuntimeReport(
            status="skipped",
            detail="app.json registers no pages — nothing to visit. The "
                   "static gate covers this case and blocks on it.",
        )

    shot_dir = root / ".mas" / "mp-runtime"
    shot_dir.mkdir(parents=True, exist_ok=True)
    auto_proc, auto_port, start_error = _start_automation(
        cli, _project_root(root), shot_dir=shot_dir
    )
    if start_error:
        return MpRuntimeReport(status="skipped", detail=start_error)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            driver = pathlib.Path(tmp) / "runtime-check.js"
            driver.write_text(_DRIVER_JS, encoding="utf-8")
            proc = subprocess.run(
                [node, str(driver)],
                cwd=driver_dir,
                capture_output=True,
                text=True,
                timeout=timeout_s + 30,
                env={
                    **_clean_env(),
                    # The driver lives in a temp dir, so `require` resolves
                    # from THERE, not from cwd — NODE_PATH is what points it
                    # at the workspace's node_modules (`ws` arrives with
                    # miniprogram-automator).
                    "NODE_PATH": str(driver_dir / "node_modules"),
                    "AVS_AUTO_PORT": str(auto_port),
                    "AVS_SHOT_DIR": str(shot_dir),
                    "AVS_PAGES": json.dumps(pages),
                    "AVS_TIMEOUT": str(timeout_s * 1000),
                },
            )
    finally:
        if auto_proc is not None:
            auto_proc.terminate()
    raw = (proc.stdout or "").strip().splitlines()
    payload = next(
        (json.loads(line) for line in reversed(raw) if line.startswith("{")), None
    )
    if payload is None:
        stderr = (proc.stderr or "").strip()[-400:]
        if _SERVICE_PORT_DISABLED in (proc.stdout + proc.stderr).lower():
            return MpRuntimeReport(
                status="skipped",
                detail="DevTools' service port is off. Open DevTools → 设置 → "
                       "安全设置 → 服务端口 (Settings → Security → Service Port) "
                       "and turn it on; it is a one-time, human-only toggle "
                       "on your own machine.",
            )
        return MpRuntimeReport(
            status="failed",
            detail=f"the automation driver returned nothing usable: {stderr or '(no output)'}",
        )
    if payload.get("error"):
        detail = str(payload["error"])
        lowered = detail.lower()
        if _SERVICE_PORT_DISABLED in lowered or "timed out" in lowered:
            # The automator spawns the CLI itself and swallows its stderr, so
            # the disabled-port error — which the CLI states plainly —
            # reaches us as a bare "Wait timed out". Verified by hand: with
            # the port off, `cli auto --project <p>` prints
            #   [error] IDE service port disabled ... set Service Port On.
            # while the automator on the same project times out at 30s with
            # nothing else. Reporting that as `failed` would read as "the
            # pages are broken" when nothing was ever checked.
            return MpRuntimeReport(
                status="skipped",
                detail=(
                    "DevTools never accepted the automation connection "
                    f"({detail}). Almost always the service port: open "
                    "DevTools → 设置 → 安全设置 → 服务端口 (Settings → Security "
                    "→ Service Port) and switch it on — a one-time toggle on "
                    "your own machine that the framework will not flip for "
                    f"you. To see the CLI say it itself: `{cli} auto "
                    f"--project {_project_root(root)}`."
                ),
            )
        return MpRuntimeReport(status="failed", detail=detail)

    failed = [p for p in payload.get("pages", []) if not p.get("ok")]
    findings = [
        MpFinding(check="mp_runtime_check", rule="page_did_not_render",
                  message=f"{p['path']}: {p.get('error', 'no error reported')}")
        for p in failed
    ]
    # The screenshot is the judge of blankness, not the protocol: a page
    # whose JS threw before Page() still "renders" (reLaunch succeeds, a
    # placeholder page sits on the stack, the runtime's console line does
    # not reliably reach a fresh automation session) — it is just pure
    # white. A single flat color is mechanical to detect and cause-agnostic.
    for entry in payload.get("pages", []):
        if not entry.get("ok"):
            continue
        shot = shot_dir / (entry["path"].replace("/", "_") + ".png")
        if shot.exists() and _is_flat_png(shot):
            findings.append(MpFinding(
                check="mp_runtime_check", rule="page_blank",
                message=f"{entry['path']}: rendered a single flat color — no "
                        f"visible content ({shot}). A require chain that "
                        "throws before Page() is the known cause; a page with "
                        "no content is the other.",
            ))
    return MpRuntimeReport(
        status="failed" if findings else "ok",
        detail=(
            f"{len(findings)} of {len(pages)} registered page(s) did not "
            f"render visible content — screenshots in {shot_dir}"
            if findings
            else f"all {len(pages)} registered page(s) rendered — screenshots "
                 f"in {shot_dir} are the judgeable evidence"
        ),
        pages_checked=[p["path"] for p in payload.get("pages", [])],
        findings=findings,
        screenshot_dir=str(shot_dir),
    )


def _is_flat_png(path) -> bool:
    """True when every pixel of the PNG is the same color.

    Stdlib-only on purpose (no PIL dependency): parse IHDR, inflate the
    IDAT stream, undo the five scanline filters, compare pixels. Handles
    the 8-bit RGB/RGBA/greyscale images DevTools' captureScreenshot emits;
    anything else (palette, 16-bit, interlaced) is conservatively treated
    as not-flat so an exotic encoding can never fail a healthy page.
    """
    import struct
    import zlib

    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    pos, width = 8, 0
    bit_depth = color_type = interlace = 0
    idat = b""
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = (
                struct.unpack(">IIBBBBB", body))
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    if not width or bit_depth != 8 or channels is None or interlace:
        return False
    try:
        raw = zlib.decompress(idat)
    except zlib.error:
        return False
    stride = width * channels
    first_pixel = None
    prior = bytearray(stride)
    offset = 0
    while offset + 1 + stride <= len(raw):
        filter_type = raw[offset]
        line = bytearray(raw[offset + 1:offset + 1 + stride])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prior[i]
            if filter_type == 1:
                line[i] = (line[i] + a) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + b) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif filter_type == 4:
                c = prior[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        pixel = bytes(line[:channels])
        if first_pixel is None:
            first_pixel = pixel
        for i in range(0, stride, channels):
            if bytes(line[i:i + channels]) != first_pixel:
                return False
        prior = line
        offset += 1 + stride
    return first_pixel is not None


_AUTO_PORT_RANGE = range(9420, 9440)  # 9420 is the automator convention


def _port_open(port: int) -> bool:
    import socket

    with socket.socket() as sock:
        sock.settimeout(1)
        try:
            sock.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _start_automation(cli, project_root, *, shot_dir, wait_s: int = 300):
    # 300s, not 60: opening a NEW project window (compile + window) took
    # 60-150s+ on the reference machine depending on how many windows the
    # IDE already had; giving up early leaves the IDE-side session to bind
    # the port AFTER we stopped listening for it. A CLI error in the log
    # aborts the wait early, so the ceiling is only paid when it is real.
    """Bring up `cli auto` for THIS project and wait for its WebSocket.

    Spawned HERE, not by miniprogram-automator: the automator's own
    launch()/connect() hang without diagnosis against IDE 2.01.2510290,
    while the CLI it would have spawned works and states its errors plainly
    (its output lands in <shot_dir>/cli-auto.log).

    Never reuses a port that is already listening — a leftover session
    serves whatever project IT opened, and reusing it once verified the
    wrong app under this one's name (all pages "rendered", none of them
    ours). A fresh session on a fresh port is the only way the results are
    about this project.

    Returns (proc | None, port, skip_detail | None); skip_detail is None
    exactly when the port is up.
    """
    import subprocess
    import time

    port = next((p for p in _AUTO_PORT_RANGE if not _port_open(p)), None)
    if port is None:
        return None, 0, (
            f"every automation port in {_AUTO_PORT_RANGE.start}-"
            f"{_AUTO_PORT_RANGE.stop - 1} is taken — stale `cli auto` "
            "sessions from earlier runs are the known cause; "
            "`pkill -f 'cli auto'` and re-run."
        )
    log_path = shot_dir / "cli-auto.log"
    # The log is appended to across runs — it is forensics, and truncating it
    # would throw away the history of a flaky handshake. So the diagnosis must
    # read only what THIS run wrote: reading the whole file made a `✔ auto`
    # left by an earlier successful run look like this run's success, and
    # reported "first-open cost" for a session that had exited in 4 seconds.
    start = log_path.stat().st_size if log_path.exists() else 0

    def _this_run_said() -> str:
        try:
            with log_path.open("rb") as handle:
                handle.seek(start)
                return handle.read().decode("utf-8", "replace")
        except OSError:
            return ""

    log = log_path.open("ab")
    proc = subprocess.Popen(  # noqa: S603
        [cli, "auto", "--project", str(project_root), "--auto-port", str(port)],
        stdout=log, stderr=subprocess.STDOUT,
        env=_clean_env(), start_new_session=True,
    )
    began = time.monotonic()
    deadline = began + wait_s
    exited = False
    while time.monotonic() < deadline:
        if _port_open(port):
            return proc, port, None
        said = _this_run_said()
        if "[error]" in said or _SERVICE_PORT_DISABLED in said.lower():
            break  # the CLI has already said why — waiting longer is theater
        if proc.poll() is not None:
            exited = True
            break
        time.sleep(1)
    proc.terminate()
    waited = int(time.monotonic() - began)
    # Name a cause from the CLI's own words, not from a guess. Blaming the
    # service port unconditionally sent one investigation chasing a toggle
    # that was already on: the CLI had printed its success marker and the port
    # came up AFTER the wait, because DevTools was compiling a project it had
    # never opened before (measured: first open >300s, second 27s).
    said = _this_run_said()
    how_long = (
        f"the CLI exited after {waited}s" if exited
        else f"the port stayed closed for {waited}s"
    )
    if _cli_reported_auto_ok(said):
        return None, port, (
            f"DevTools did not expose the automation port ({how_long}), but "
            "the CLI reported success — so this is almost certainly first-open "
            "cost, not a misconfiguration: a project DevTools has not opened "
            "before is compiled from scratch, which can take several minutes. "
            "**Re-run and it will be fast** (the session is warm now). The "
            f"CLI's own words: {log_path}."
        )
    if exited:
        return None, port, (
            f"`cli auto` exited after {waited}s without establishing "
            f"automation and without reporting success — its own words are in "
            f"{log_path}. A session already open on this project, or a stale "
            "one from an earlier run, is the usual cause: "
            "`pkill -f 'cli auto'`, quit DevTools, and re-run."
        )
    return None, port, (
        f"DevTools never accepted the automation connection ({how_long} and "
        f"the CLI never reported success; its own words are in {log_path}). "
        "Most often the service port: open DevTools → 设置 → 安全设置 → "
        "服务端口 (Settings → Security → Service Port) and switch it on — a "
        "one-time toggle on your own machine that the framework will not flip "
        "for you. If it is already on, start the IDE first "
        "(open -a wechatwebdevtools), let it settle, and re-run."
    )


def _clean_env() -> dict:
    import os

    return {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}


def _project_root(root):
    """Where project.config.json lives — what DevTools opens."""
    import pathlib

    for candidate in (root, root / "miniprogram"):
        if (pathlib.Path(candidate) / "project.config.json").exists():
            return candidate
    return root


def _automator_root(root):
    """A directory from which `require('miniprogram-automator')` resolves."""
    import pathlib

    for candidate in (root, root.parent, pathlib.Path.cwd()):
        if (candidate / "node_modules" / "miniprogram-automator").is_dir():
            return candidate
    return None


def _registered_pages(root) -> list[str]:
    import json
    import pathlib

    for base in (root / "miniprogram", root):
        app_json = pathlib.Path(base) / "app.json"
        if app_json.exists():
            try:
                return [str(p) for p in (json.loads(
                    app_json.read_text(encoding="utf-8")) or {}).get("pages", [])]
            except ValueError:
                return []
    return []


#: Visits every registered page and reports per-page, as one JSON line.
#: Speaks the automation protocol raw over WebSocket — NOT through
#: miniprogram-automator, whose launch() and connect() both hang without
#: diagnosis against IDE 2.01.2510290 (its Connection layer; the protocol
#: itself answers instantly). Three signals per page, because "reLaunch did
#: not throw" alone once reported three blank pages as rendered:
#:   1. reLaunch succeeded;
#:   2. no `Page "<path>" has not been registered` console line — the
#:      runtime's own words for "this page's JS threw before Page()";
#:   3. a screenshot on disk a human can judge for blankness.
#: (`ws` resolves via NODE_PATH; it ships inside miniprogram-automator's
#: install, which stays the one-line remedy.)
_DRIVER_JS = """
const WebSocket = require('ws');
const fs = require('fs');
const pages = JSON.parse(process.env.AVS_PAGES);
const shotDir = process.env.AVS_SHOT_DIR || '';
const ws = new WebSocket('ws://127.0.0.1:' + (process.env.AVS_AUTO_PORT || '9420'));
let seq = 0;
const pending = new Map();
const notRegistered = new Set();
function send(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params: params || {} }));
    setTimeout(() => {
      if (pending.has(id)) { pending.delete(id); reject(new Error(method + ' timed out')); }
    }, 15000);
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
ws.on('message', (raw) => {
  const msg = JSON.parse(raw);
  if (msg.id && pending.has(msg.id)) {
    const cb = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? cb.reject(new Error(msg.error.message)) : cb.resolve(msg.result);
  } else if (msg.method === 'App.logAdded') {
    const text = ((msg.params || {}).args || []).map(String).join(' ');
    const m = text.match(/Page "(.+?)" has not been registered/);
    if (m) notRegistered.add(m[1]);
  }
});
ws.on('error', (e) => {
  console.log(JSON.stringify({ error: 'ws: ' + e.message }));
  process.exit(0);
});
ws.on('open', async () => {
  const out = { pages: [] };
  try {
    const info = await send('Tool.getInfo');
    out.sdk = info.SDKVersion || '';
    try { await send('App.enableLog'); } catch (e) {}
    const visitError = {};
    for (const path of pages) {
      try {
        await send('App.callWxMethod', { method: 'reLaunch', args: [{ url: '/' + path }] });
        await sleep(1500);
        if (shotDir) {
          try {
            const shot = await send('App.captureScreenshot');
            fs.writeFileSync(shotDir + '/' + path.replace(/\\//g, '_') + '.png',
                             Buffer.from(shot.data, 'base64'));
          } catch (e) { /* a missing shot must not fail the visit */ }
        }
      } catch (e) {
        visitError[path] = String(e).slice(0, 300);
      }
    }
    await sleep(500); // let straggler console events land before judging
    for (const path of pages) {
      const err = notRegistered.has(path)
        ? 'Page() never registered — the page JS threw before registration ' +
          '(broken require chains are the known cause; the loadability ' +
          "gate's require scan catches this class statically)"
        : visitError[path];
      out.pages.push(err ? { path, ok: false, error: err } : { path, ok: true });
    }
    console.log(JSON.stringify(out));
    process.exit(0);
  } catch (e) {
    console.log(JSON.stringify({ error: String(e).slice(0, 400) }));
    process.exit(0);
  }
});
setTimeout(() => {
  console.log(JSON.stringify({ error: 'driver global timeout' }));
  process.exit(0);
}, Number(process.env.AVS_TIMEOUT) || 120000);
"""
