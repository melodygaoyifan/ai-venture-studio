"""The seeded perf-defect calibration (§77.2) — the run that converts the
lane from PROVISIONAL.

A lane that cannot catch its own seeded defects has no business gating
anyone's release. The harness boots a loopback app where each endpoint
carries exactly one seeded defect (plus a clean control), drives a small
closed-model load, and checks that each defect is DETECTABLE — its p95
degrades by at least the detection factor relative to control. This is
relative-detection calibration at S0 (loopback, low parity): it proves the
lane can see its defects, and says so on the record; it satisfies no AC
(ADR-U30 precondition 4 still applies to AC runs).
"""

from __future__ import annotations

import http.client
import pathlib
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml
from pydantic import BaseModel

from ai_venture_studio.lanes.perf import SEEDED_PERF_DEFECTS

DETECTION_FACTOR = 3.0
CALIBRATION_FILE = pathlib.Path(__file__).resolve().parents[3] / (
    "benchmarks/perf_seeded/calibration.yaml")

_ROWS = 5000
_QUAD_N = 900


def _build_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, note TEXT)")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    for i in range(_ROWS):
        conn.execute("INSERT INTO users VALUES (?, ?)", (i, f"user-{i}"))
        conn.execute("INSERT INTO orders VALUES (?, ?, ?)", (i, i, "x" * 50))
    conn.execute("CREATE INDEX idx_orders_user ON orders(user_id)")
    conn.commit()
    return conn


class _Handler(BaseHTTPRequestHandler):
    db: sqlite3.Connection

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):  # noqa: N802 — stdlib naming
        db = self.db
        if self.path == "/clean":
            # the well-written endpoint: one indexed point lookup
            db.execute("SELECT name FROM users WHERE id = ?", (7,)).fetchone()
        elif self.path == "/n_plus_one_query":
            for (uid,) in db.execute("SELECT user_id FROM orders"):
                db.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
        elif self.path == "/missing_index_on_filtered_column":
            # full scans on the unindexed text column, repeated
            for _ in range(15):
                db.execute("SELECT COUNT(*) FROM orders WHERE note LIKE '%xx%'").fetchone()
        elif self.path == "/unbounded_connection_pool":
            # a fresh connection per request, never reused, plus setup cost
            fresh = sqlite3.connect(":memory:")
            fresh.executescript("CREATE TABLE t(a);" + "INSERT INTO t VALUES (1);" * 3000)
            fresh.close()
        elif self.path == "/sync_call_in_async_handler":
            time.sleep(0.02)  # the blocking call where the event loop lives
        elif self.path == "/quadratic_serializer_on_list_endpoint":
            items = list(range(_QUAD_N))
            _ = [sum(1 for b in items if b <= a) for a in items]  # O(n²)
        else:
            self.send_response(404); self.end_headers(); return
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class EndpointReading(BaseModel):
    defect: str
    p95_ms: float
    control_p95_ms: float
    degradation: float
    caught: bool


class CalibrationResult(BaseModel):
    catch_rate: float
    detection_factor: float
    environment: str
    parity: str
    readings: list[EndpointReading]
    note: str


def _measure(port: int, path: str, n: int) -> float:
    samples = []
    for _ in range(n):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        start = time.perf_counter()
        conn.request("GET", path)
        conn.getresponse().read()
        samples.append((time.perf_counter() - start) * 1000)
        conn.close()
    samples.sort()
    return samples[max(0, int(len(samples) * 0.95) - 1)]


def run_perf_calibration(*, requests_per_endpoint: int = 40) -> CalibrationResult:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.db = _build_db()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        _measure(port, "/clean", 5)  # warm-up
        control = _measure(port, "/clean", requests_per_endpoint)
        readings = []
        for defect in SEEDED_PERF_DEFECTS:
            p95 = _measure(port, f"/{defect}", requests_per_endpoint)
            degradation = p95 / max(control, 1e-6)
            readings.append(EndpointReading(
                defect=defect, p95_ms=round(p95, 3),
                control_p95_ms=round(control, 3),
                degradation=round(degradation, 2),
                caught=degradation >= DETECTION_FACTOR))
    finally:
        server.shutdown()
    caught = sum(1 for r in readings if r.caught)
    return CalibrationResult(
        catch_rate=caught / len(readings),
        detection_factor=DETECTION_FACTOR,
        environment="loopback", parity="low",
        readings=readings,
        note="relative-detection calibration: proves the lane can see its "
             "seeded defects; satisfies no AC (ADR-U30 precondition 4 still "
             "applies to AC runs)")


def record_calibration(result: CalibrationResult, *, at: str) -> pathlib.Path:
    """Write the calibration `lane_status()` reads.

    `avs_version` is written on EVERY record, not only on stale ones. A field
    that appears exactly when something is wrong is a field nobody looks for
    (ADR-056), and the whole point of stamping the build is that a reader can
    see the answer before deciding whether to trust it.
    """
    from ai_venture_studio import __version__

    CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_FILE.write_text(yaml.safe_dump(
        {"calibration": {**result.model_dump(), "at": at,
                         "avs_version": __version__}}, sort_keys=False))
    return CALIBRATION_FILE


def lane_status() -> str:
    """PROVISIONAL until a recorded calibration catches >=80% of the
    seeded manifest — then CALIBRATED, with its scope stated.

    The scope now includes the BUILD the reading was taken on. It said
    `CALIBRATED (loopback, low parity, relative detection, 2026-07-26)` for
    ninety releases, and a date is a proxy that breaks exactly when releases
    outpace the measurement — the same defect ADR-054 found in `avs cadence`
    reading "ok (4d)" over a reading nine releases old. Staleness is
    DESCRIBED, never a downgrade: nothing here knows whether the perf lane's
    behaviour actually moved between two builds, and silently demoting a
    real reading to PROVISIONAL on a version bump would be a guess wearing a
    verdict's clothes.
    """
    if CALIBRATION_FILE.exists():
        raw = yaml.safe_load(CALIBRATION_FILE.read_text()) or {}
        calibration = raw.get("calibration") or {}
        if float(calibration.get("catch_rate", 0)) >= 0.8:
            from ai_venture_studio import __version__

            # Absent reads as "unrecorded", never as "current". Guessing the
            # other way turns a file this code has never seen into a fresh
            # reading, which is the one error that cannot be noticed.
            measured_on = calibration.get("avs_version")
            build = f"v{measured_on}" if measured_on else "an unrecorded build"
            if measured_on != __version__:
                build += f", current build v{__version__}"
            return (f"CALIBRATED ({calibration.get('environment')}, "
                    f"{calibration.get('parity')} parity, relative detection, "
                    f"{calibration.get('at', '?')} on {build})")
    return "PROVISIONAL"
