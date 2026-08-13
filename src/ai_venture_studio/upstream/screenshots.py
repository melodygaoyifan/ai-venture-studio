"""M2 — screenshots: founders validate by LOOKING, not reading file lists.

Availability-gated like every external tool: Playwright (web) and
miniprogram devtools are used when installed; their absence is a visible
note, never a silent skip. Captures land in product/screenshots/ and are
surfaced by the Studio and the build report.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

from pydantic import BaseModel


class ShotResult(BaseModel):
    captured: list[str] = []
    note: str = ""


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


#: Enough to show a founder their product, few enough that capture stays
#: a tail on the build rather than a second build.
MAX_SHOTS = 8

_ROUTE = re.compile(
    r"""@\w+\.(?:get|post|route)\(\s*['"](/[^'"{<]*)['"]""", re.IGNORECASE
)


def discover_paths(workspace: str | Path) -> list[str]:
    """The product's own GET routes, so the gallery is the product and not
    just its front door.

    Capture was called with the default `["/"]`, so a founder who built
    five pages got one picture of the home page — and after a change that
    touched checkout, the one picture was of the page that had not moved.
    Parameterised routes are skipped: there is no id to substitute, and a
    404 screenshot is worse than a missing one.
    """
    root = Path(workspace).resolve()
    found: list[str] = []
    files = [root / e for e in ("app/main.py", "main.py", "app.py")]
    files += sorted((root / "app").rglob("*.py")) if (root / "app").is_dir() else []
    for path in files:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for route in _ROUTE.findall(text):
            if route not in found:
                found.append(route)
    ordered = ["/"] + [r for r in found if r != "/"]
    return ordered[:MAX_SHOTS]


def _free_port() -> int:
    """A port the OS just told us is free.

    Capture used to hardcode 8642, which was survivable while it ran once
    per workspace lifetime. It now runs again after every change, so a
    leftover server from an earlier capture — or the founder's own
    `avs preview` — would make every subsequent screenshot a picture of the
    wrong process, or of nothing.
    """
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def capture_web(workspace: str | Path, paths: list[str] | None = None, port: int = 0) -> ShotResult:
    root = Path(workspace).resolve()
    if not _playwright_available():
        return ShotResult(
            note="screenshots skipped: playwright not installed "
            "(`pip install 'ai-venture-studio[screenshots]'` then "
            "`playwright install chromium` — needs the browser download "
            "allowed, or PLAYWRIGHT_DOWNLOAD_HOST at an internal mirror)"
        )
    entry = next(
        (e for e in ("app/main.py", "main.py", "app.py") if (root / e).exists()), None
    )
    if not entry:
        return ShotResult(note="screenshots skipped: no runnable web entry")

    import os
    import socket

    from ai_venture_studio.upstream.provisioning import preview_env

    port = port or _free_port()
    out_dir = root / "product" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, str(root / entry)],
        cwd=root,
        env={**os.environ, "PORT": str(port), **preview_env(root)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # Its own session, so the cleanup below can reach the whole tree.
        start_new_session=True,
    )
    captured: list[str] = []
    try:
        for _ in range(40):
            try:
                socket.create_connection(("127.0.0.1", port), 1).close()
                break
            except OSError:
                time.sleep(0.5)
        else:
            return ShotResult(note="screenshots skipped: server never listened")

        from playwright.sync_api import sync_playwright

        wanted = paths or discover_paths(root)
        fresh: set[Path] = set()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 900, "height": 700})
            for url_path in wanted:
                slug = url_path.strip("/").replace("/", "-") or "home"
                target = out_dir / f"{slug}.png"
                try:
                    page.goto(f"http://127.0.0.1:{port}{url_path}", timeout=15000)
                    page.screenshot(path=str(target), full_page=True)
                except Exception:  # noqa: BLE001 — one bad page, not a lost set
                    continue
                fresh.add(target)
                captured.append(str(target.relative_to(root)))
            browser.close()
        # A page the founder deleted must not keep its picture. Only prune
        # once something was captured: an empty run means the capture failed,
        # and deleting the last evidence over a failure is its own bug.
        if fresh:
            for old in out_dir.glob("*.png"):
                if old not in fresh:
                    old.unlink(missing_ok=True)
        return ShotResult(captured=captured)
    except Exception as exc:  # noqa: BLE001 — capture is best-effort, visibly
        return ShotResult(captured=captured, note=f"screenshot error: {exc}")
    finally:
        # terminate() alone signalled the parent and never reaped it: every
        # capture left a zombie plus whatever workers the server had forked,
        # all still bound to their ports, for the whole length of a bench run.
        from ai_venture_studio.testing import _kill_process_group

        _kill_process_group(server)
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover — SIGKILL landed
            pass


def capture(workspace: str | Path, profile: str) -> ShotResult:
    if profile == "web":
        return capture_web(workspace)
    if profile == "miniprogram":
        return ShotResult(
            note="小程序截图：用微信开发者工具打开项目即可预览各页面 "
            "(devtools-cli screenshots land here when configured)"
        )
    return ShotResult(note=f"screenshots not supported for profile {profile!r} yet")
