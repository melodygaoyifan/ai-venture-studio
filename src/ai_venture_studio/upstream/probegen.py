"""Auto-generated acceptance probes — from the USER'S PRD, not fixtures.

The hand-written probes in benchmarks/ are labeled regression fixtures
(deterministic scoring across runs). Real products can't have hand-setup:
this module generates behavioral probes from what the founder asked for
(FDR + built criteria) against what was actually built (the OBSERVED
route table) — the WebGen-Bench pattern, executor included.

Guardrails: the LLM writes only the call/assert body; the boot frame,
timeout, and teardown are templated; every body must parse (ast) or it is
dropped visibly.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import yaml
from pydantic import BaseModel

from ai_venture_studio.providers import get_provider
from ai_venture_studio.yamlx import extract_mapping

PROBEGEN_MARKER = "acceptance probe generator for built products"

BOOT_FRAME = '''import json, os, socket, subprocess, sys, time, urllib.request, urllib.error

def _free_port():
    # Every probe is its own process and boots its own server, so a fixed
    # port made each one collide with the one before it: the previous
    # probe's server was still holding 8646 when the next booted, and the
    # first real call came back "connection refused" — the harness charging
    # the product for the harness's own race (run 13, case 04).
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


PORT = _free_port()
BASE = f"http://127.0.0.1:{PORT}"
entry = next((e for e in ("app/main.py", "main.py", "app.py") if os.path.exists(e)), None)
assert entry, "no runnable entry point"
proc = subprocess.Popen([sys.executable, entry],
                        env={**os.environ, "PORT": str(PORT)},
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _answers():
    # An HTTP answer, not a bare TCP connect. A socket that accepts and
    # then dies reads as "up" to connect() and as "refused" to the very
    # next request, which is how a dying server passed for a ready one.
    # Any status is an answer here — 404 included; only a transport
    # failure means nobody is home.
    try:
        urllib.request.urlopen(BASE + "/", timeout=2)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def wait():
    for _ in range(60):
        if proc.poll() is not None:
            raise SystemExit(f"server exited before it answered (code {proc.returncode})")
        if _answers():
            return
        time.sleep(0.5)
    raise SystemExit("server never answered")


def _decode(raw):
    raw = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw}


def call(method, path, body=None, expect_redirect=False):
    req = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    opener = (urllib.request.build_opener(NoRedirect())
              if expect_redirect else urllib.request.build_opener())
    for attempt in (1, 2):
        try:
            resp = opener.open(req, timeout=10)
            return resp.status, _decode(resp.read()), dict(resp.headers)
        except urllib.error.HTTPError as e:
            # urllib raises on every 4xx/5xx, and the body is on the
            # exception. Reading it is the whole point of an error probe:
            # returning {} here failed a product that answered exactly as
            # its contract said, and the framework once "fixed" the product
            # prompt in response.
            return e.code, _decode(e.read()), dict(e.headers)
        except urllib.error.URLError as e:
            # No answer at all — nothing was measured about the product.
            # Tried twice before believing it, and then said so in words:
            # a raw traceback here reads as a product defect, and the last
            # time one did, it was the harness.
            if attempt == 2:
                raise AssertionError(
                    f"product did not answer {method} {path}: {e.reason}")
            time.sleep(0.5)


wait()
try:
{body}
finally:
    proc.terminate()
    try:
        # Wait for the port to actually come back: terminate() only asks.
        proc.wait(5)
    except Exception:
        proc.kill()
'''

_SYSTEM = f"""You are the {PROBEGEN_MARKER}. Write behavioral probes for
the product described below, using ONLY the observed routes.

Each probe body is python that runs inside a frame where these exist:
- call(method, path, body=None, expect_redirect=False) -> (status, data, headers)
- BASE, PORT (server already booted; do NOT boot or terminate anything)

Rules:
- Probe REAL user behaviors from the criteria (create → read back, math
  adds up, invalid input rejected). 2–5 probes, each independent.
- Only paths matching the observed routes. assert with messages.
- No imports, no file access, no sleeps.

Respond with ONLY YAML:
probes:
  - name: kebab-name
    body: |
      s, d, _ = call("POST", "/api/things", {{"name": "x"}})
      assert s in (200, 201), f"create returned {{s}}"
"""


def _route_literals(root: Path) -> list[str]:
    import re

    hits: set[str] = set()
    for path in list(root.glob("app/**/*.py")) + list(root.glob("*.py")):
        rel_parts = path.relative_to(root).parts
        if path.name.startswith("test") or ".mas" in rel_parts:
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r"[\"\']((?:/[A-Za-z0-9_<>{}.:-]+)+/?)[\"\']", src):
            lit = match.group(1)
            if len(lit) <= 80 and not lit.startswith(("/tmp", "/usr", "/var", "/etc", "/Users", "/home")):
                hits.add(lit)
    return sorted(hits)[:40]


class GeneratedProbe(BaseModel):
    name: str
    script: str


def generate_probes(
    workspace: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    max_probes: int = 5,
) -> tuple[list[GeneratedProbe], list[str]]:
    """Returns (probes, notes). Invalid bodies are dropped VISIBLY via notes."""
    root = Path(workspace).resolve()
    fdr = (root / "FDR.md").read_text(encoding="utf-8") if (root / "FDR.md").exists() else ""
    from ai_venture_studio.tools.wireup import collect_routes
    from ai_venture_studio.upstream.walkthrough import built_criteria

    routes = sorted("/" + "/".join(r) for r in collect_routes(root))
    criteria = [c for _, c in built_criteria(root)]
    route_note = ""
    if not routes:
        # Stdlib http.server products route by hand (do_GET/do_POST +
        # path parsing) — invisible to collect_routes, which left case 03
        # UNMEASURED for five bench runs. Path-shaped string literals from
        # the app source are weaker evidence, but with the FDR (the actual
        # contract) they are enough to generate probes from.
        routes = _route_literals(root)
        route_note = (
            "static route collection found nothing (hand-rolled routing is "
            "invisible to it); observed_routes are path string literals "
            "scraped from the source — treat the fdr as the authoritative "
            "contract for exact paths and methods"
        )
        entry = any((root / e).exists() for e in ("app/main.py", "main.py", "app.py"))
        if not entry:
            return [], ["no observed backend routes and no entry point — nothing to probe over HTTP"]

    raw = get_provider(provider).complete(
        model=model,
        system=_SYSTEM,
        user=yaml.safe_dump(
            {"fdr": fdr[:1500], "criteria": criteria[:20], "observed_routes": routes,
             **({"route_note": route_note} if route_note else {})},
            sort_keys=False, allow_unicode=True,
        ),
        max_tokens=4096,
    )
    try:
        data = extract_mapping(raw, ("probes",))
    except ValueError as exc:
        return [], [f"probe generation unparseable: {exc}"]

    probes, notes = [], []
    for item in (data.get("probes") or [])[:max_probes]:
        name = str(item.get("name", "probe"))[:48]
        body = str(item.get("body", ""))
        try:
            ast.parse(body)
        except SyntaxError as exc:
            notes.append(f"dropped probe {name!r}: body does not parse ({exc.msg})")
            continue
        script = BOOT_FRAME.replace("{body}", textwrap.indent(body.rstrip(), "    "))
        probes.append(GeneratedProbe(name=name, script=script))
    if not probes:
        notes.append("no valid probes generated")
    return probes, notes


def verify_product(
    workspace: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> Path:
    """Post-build acceptance verification for ANY user product: probes are
    generated from the FDR and executed against the booted product;
    results land in product/VERIFICATION.md in checklist form."""
    root = Path(workspace).resolve()
    from ai_venture_studio.product_bench import Probe, run_probe

    probes, notes = generate_probes(root, provider=provider, model=model)
    lines = ["# 自动验证 / Automated verification", ""]
    passed = 0
    for generated in probes:
        result = run_probe(root, Probe(name=generated.name, script=generated.script))
        mark = "✅" if result.passed else "❌"
        passed += result.passed
        lines.append(f"- {mark} {generated.name}" + (f" — {result.detail}" if result.detail else ""))
    for note in notes:
        lines.append(f"- ⚠️ {note}")
    if probes:
        lines.insert(1, f"\n{passed}/{len(probes)} 项行为验证通过 / behaviors verified.\n")
    path = root / "product" / "VERIFICATION.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
