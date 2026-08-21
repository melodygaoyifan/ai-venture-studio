"""Docs 26-28 deterministic core: perf lane, realtime + streaming deltas,
architecture fitness, delivery hardening (invariants 14.24-14.28)."""

from __future__ import annotations

import datetime as dt

import pytest

from ai_venture_studio.lanes import (
    DepsGraphError,
    EnvironmentsError,
    PerfRunTelemetry,
    StreamContractError,
    api_surface_check,
    arch_contract_check,
    backpressure_scan,
    capacity_check,
    check_compatibility,
    check_environments,
    check_net_model,
    checkpoint_check,
    cross_build_replay,
    desync_probe,
    det_sim_scan,
    expand_contract_violation,
    flag_lint,
    graph_fingerprint,
    lint_perf_criteria,
    load_stream_contracts,
    migration_rehearsal,
    perf_run_environment_ok,
    replay_identity,
    stream_contract_check,
    tick_budget_ok,
    type_delivery_claim,
    type_perf_run,
)

TODAY = dt.date(2026, 7, 26)


# --- perf lane (doc 26) --------------------------------------------------------


def test_perf_grammar_kills_vague_and_accepts_measured():
    issues = lint_perf_criteria([
        "UNDER 200 rps open-model arrival THE SYSTEM SHALL http_req_duration < 300ms AT p95 FOR 10m",
        "UNDER 2000 concurrent WebSocket sessions THE SYSTEM SHALL error_rate < 0.1% FOR 30m",
        "handles high traffic",
        "UNDER heavy usage THE SYSTEM SHALL latency < 100ms",  # no arrival model
    ])
    assert [i.index for i in issues] == [2, 3]
    assert "vague perf term" in issues[0].problem
    assert "arrival model" in issues[1].problem


def _telemetry(**overrides) -> PerfRunTelemetry:
    base = {"generator_cpu_max": 0.5, "dropped_iterations": 0, "blocked_spike": False,
                "entry_point": "cdn", "slo_entry_point": "cdn", "arrival_model": "open",
                "ac_arrival_model": "open", "environment": "staging",
                "environment_parity": "prod_mirror", "slot": "perf_regression",
                "percentiles": {"p50": 80, "p95": 220, "p99": 480}}
    base.update(overrides)
    return PerfRunTelemetry(**base)


def test_invalid_run_is_not_a_number():
    assert type_perf_run(_telemetry()).status == "VALID"
    saturated = type_perf_run(_telemetry(generator_cpu_max=0.95))
    assert saturated.status == "INVALID_RUN" and "generator_saturated" in saturated.failures[0]
    assert type_perf_run(_telemetry(entry_point="origin")).status == "INVALID_RUN"
    assert type_perf_run(_telemetry(ac_arrival_model="closed")).status == "INVALID_RUN"
    assert type_perf_run(_telemetry(environment_parity="low")).status == "INVALID_RUN"
    assert type_perf_run(_telemetry(percentiles={"p95": 220})).status == "INVALID_RUN"


def test_capacity_check_arithmetic_and_staleness():
    entries = [{
        "endpoint": "POST /api/orders",
        "traffic_model": {"expected_rps": 120, "peak_multiplier": 4},
        "measured": {"saturation_rps": 1000, "at": "2026-07-20",
                     "run": "perf/runs/sat.json"},
    }]
    ok = capacity_check(entries, valid_runs={"perf/runs/sat.json"},
                        last_perf_relevant_merge=dt.date(2026, 7, 19))
    assert ok == []  # 120×4×2 = 960 <= 1000: headroom holds
    entries[0]["measured"]["saturation_rps"] = 959
    short = capacity_check(entries, valid_runs={"perf/runs/sat.json"},
                           last_perf_relevant_merge=dt.date(2026, 7, 19))
    assert [i.rule for i in short] == ["insufficient_headroom"]
    stale = capacity_check(entries, valid_runs={"perf/runs/sat.json"},
                           last_perf_relevant_merge=dt.date(2026, 7, 25))
    assert "stale" in {i.rule for i in stale}
    no_run = capacity_check(entries, valid_runs=set(),
                            last_perf_relevant_merge=dt.date(2026, 7, 19))
    assert [i.rule for i in no_run] == ["no_valid_run"]


# --- realtime delta (doc 27 Part 79) ---------------------------------------------


def test_net_model_declared_or_escalate():
    issues = check_net_model({"tick_rate": 30})
    assert issues[0].rule == "ESCALATE_REQUIREMENT_CONFLICT"
    issues = check_net_model({"net_model": "rollback", "tick_rate": 30,
                              "snapshot_policy": {}})
    assert "missing_hash_cadence" in {i.rule for i in issues}
    assert check_net_model({"net_model": "server_authoritative", "tick_rate": 30,
                            "snapshot_policy": {}}) == []


def test_det_sim_scan_catches_the_enumerated_leaks():
    source = (
        "import time, random\n"
        "def tick(state):\n"
        "    if state.x == 0.1:\n"
        "        state.y += 0.016\n"
        "    r = random.random()\n"
        "    for k in state.units.keys():\n"
        "        pass\n"
        "    now = time.time()\n"
    )
    rules = {f.rule for f in det_sim_scan(source)}
    assert {"float_equality", "float_accumulation", "unseeded_rng",
            "dict_iteration", "wall_clock"} <= rules
    assert det_sim_scan("def tick(state, rng, now_tick):\n    state.x += 1\n") == []


def test_replay_checks():
    good = replay_identity([["h1", "h2", "h3"]] * 3)
    assert good.passed
    bad = replay_identity([["h1", "h2", "h3"], ["h1", "hX", "h3"]])
    assert not bad.passed and "incident" in bad.detail

    assert cross_build_replay(["a", "b", "c"], ["a", "b", "c"],
                              change_expected_from_tick=None).passed
    assert cross_build_replay(["a", "b", "c"], ["a", "b", "C2"],
                              change_expected_from_tick=2).passed
    silent = cross_build_replay(["a", "b", "c"], ["a", "B2", "c"],
                                change_expected_from_tick=None)
    assert not silent.passed and "silent" in silent.detail

    probe = desync_probe(["a", "b", "c"], ["a", "b", "X"], hash_every_n_ticks=30)
    assert probe.passed and "incident" in probe.detail
    assert tick_budget_ok(20.0, 30) and not tick_budget_ok(40.0, 30)


# --- streaming delta (doc 27 Part 80) ----------------------------------------------


def test_default_is_lexically_illegal():
    with pytest.raises(StreamContractError, match="default"):
        load_stream_contracts("topics:\n  - {name: orders, compatibility: default}\n")
    with pytest.raises(StreamContractError, match="enforcement_tier"):
        load_stream_contracts("topics:\n  - {name: orders, compatibility: BACKWARD}\n")
    topics = load_stream_contracts(
        "topics:\n  - {name: orders, compatibility: BACKWARD,\n"
        "     enforcement_tier: sdk_only, max_lag_seconds: 60}\n")
    assert topics[0]["compatibility"] == "BACKWARD"


def test_compatibility_and_upgrade_order():
    old = {"fields": [{"name": "id"}, {"name": "note", "default": ""}]}
    new_no_default = {"fields": [{"name": "id"}, {"name": "note", "default": ""},
                                 {"name": "priority"}]}
    issues = check_compatibility(old, new_no_default, "BACKWARD")
    assert [i.rule for i in issues] == ["backward_incompatible"]

    removed = {"fields": [{"name": "id"}]}
    assert check_compatibility(old, removed, "FORWARD") == []  # note had a default

    topic = {"name": "orders", "compatibility": "BACKWARD",
             "enforcement_tier": "sdk_only"}
    issues, order = stream_contract_check(
        topic, old, new_no_default, {"auto.register.schemas": "true"})
    assert order == "consumers deploy first"
    assert {"backward_incompatible", "rogue_producer_risk"} == {i.rule for i in issues}


def test_exactly_once_typed_or_downgraded():
    typed = type_delivery_claim("exactly_once", "transactional_producer_read_committed")
    assert not typed.downgraded and "replay verification" in typed.note
    downgraded = type_delivery_claim("exactly_once", "hope")
    assert downgraded.claim == "at_least_once" and downgraded.downgraded


def test_backpressure_scan():
    findings = backpressure_scan("buffer = []\nbuffer.append(msg)\n",
                                 max_lag_seconds=None)
    assert {"no_lag_slo", "unbounded_buffer"} == {f.rule for f in findings}
    assert backpressure_scan("q = Queue(maxsize=1000)\n", max_lag_seconds=30) == []


# --- architecture evolution (doc 28 Part 81) -----------------------------------------


DEPS = """
modules:
  users:   {public: [users.api], may_import: [shared]}
  orders:  {public: [orders.api], may_import: [users.api, shared]}
  billing: {public: [billing.api], may_import: [orders.api, shared]}
"""


def test_arch_contract_check_enforces_the_graph():
    sources = {
        "modules/orders/service.py": "from modules.users.api import get_user\n",
        "modules/users/service.py": "from modules.billing.api import charge\n",
        "modules/orders/repo.py": "from modules.users.internal.db import raw\n",
    }
    violations = arch_contract_check(DEPS, sources)
    keyed = {(v.module, v.imports) for v in violations}
    assert ("users", "billing.api") in keyed  # forbidden edge
    assert any(v.module == "orders" and v.imports.startswith("users.internal")
               for v in violations)  # internal reach past the public surface
    assert not any(v.module == "orders" and v.imports == "users.api"
                   for v in violations)  # the allowed public edge is clean

    with pytest.raises(DepsGraphError, match="not a declared module"):
        arch_contract_check("modules:\n  a: {may_import: [ghost]}\n", {})
    assert graph_fingerprint(DEPS).startswith("sha256:")


def test_checkpoint_mode_counts_debt_and_fails_new():
    baseline = {("users", "billing.api")}
    sources = {
        "modules/users/service.py": "from modules.billing.api import charge\n",
        "modules/orders/repo.py": "from modules.users.internal.db import raw\n",
    }
    violations = arch_contract_check(DEPS, sources, baseline=baseline)
    result = checkpoint_check(violations, baseline)
    assert len(result.new_violations) == 1  # the internal reach is new
    assert result.remaining_debt == 1 and result.debt_delta == 0


def test_api_surface_deprecation_window():
    issues = api_surface_check(["GET /api/orders", "POST /api/orders"],
                               ["POST /api/orders"])
    assert issues[0].rule == "ESCALATE_CONTRACT_BREAK"
    assert api_surface_check(["GET /api/orders"], [],
                             deprecated={"GET /api/orders": "since 0.22"}) == []


# --- delivery hardening (doc 28 Part 82) -----------------------------------------------


def test_environments_dag_and_perf_parity():
    envs = check_environments(
        "environments:\n"
        "  - {name: dev, parity: low, promotes_to: staging}\n"
        "  - {name: staging, parity: prod_mirror, promotes_to: prod,\n"
        "     gates: [perf_regression]}\n"
        "  - {name: prod, parity: prod, gates: [gate5_deploy_review]}\n")
    assert perf_run_environment_ok(envs, "staging", "perf_regression")
    assert not perf_run_environment_ok(envs, "dev", "perf_soak")
    assert perf_run_environment_ok(envs, "dev", "perf_smoke")  # smoke anywhere

    with pytest.raises(EnvironmentsError, match="cycle"):
        check_environments(
            "environments:\n"
            "  - {name: dev, promotes_to: staging}\n"
            "  - {name: staging, promotes_to: dev}\n"
            "  - {name: prod}\n")
    with pytest.raises(EnvironmentsError, match="ending at prod"):
        check_environments(
            "environments:\n  - {name: island}\n  - {name: prod}\n")


def test_flag_lint():
    registry = (
        "flags:\n"
        "  - {name: new-onboarding, category: release, owner: melody,\n"
        "     created: '2026-05-01', expiry: '2026-06-01',\n"
        "     final_state: 'on', removal_trigger: '30d at 100%'}\n"
        "  - {name: kill-exports, category: ops_kill_switch, owner: melody,\n"
        "     created: '2026-05-01', final_state: 'off',\n"
        "     removal_trigger: 'never — operational control'}\n")
    sources = {"app.py": "if flag('new-onboarding') and flag('mystery-toggle'):\n"}
    issues = flag_lint(registry, sources, today=TODAY)
    rules = {(i.flag, i.rule) for i in issues}
    assert ("mystery-toggle", "unregistered_flag") in rules
    assert ("new-onboarding", "expired_blocking") in rules
    assert not any(flag == "kill-exports" for flag, _ in rules)  # long-lived OK


def test_migration_rehearsal_round_trip():
    schema = "CREATE TABLE orders (id INTEGER PRIMARY KEY, title TEXT);"
    good = migration_rehearsal(
        schema,
        up_sql="ALTER TABLE orders ADD COLUMN qty INTEGER DEFAULT 1;",
        down_sql="ALTER TABLE orders DROP COLUMN qty;")
    assert good.status == "VALID" and good.applied_cleanly and good.reversible
    assert good.destructive_ops == []  # the up is expand-only

    irreversible = migration_rehearsal(
        schema, up_sql="DROP TABLE orders;", down_sql="SELECT 1;")
    assert irreversible.applied_cleanly and not irreversible.reversible
    assert irreversible.destructive_ops  # on the record, with evidence

    broken = migration_rehearsal(schema, up_sql="ALTER TABLE ghost ADD x;",
                                 down_sql="SELECT 1;")
    assert broken.status == "INVALID_REHEARSAL"

    assert expand_contract_violation("ALTER TABLE o DROP COLUMN x;",
                                     same_pr_as_expand=True)
    assert not expand_contract_violation("ALTER TABLE o ADD COLUMN x;",
                                         same_pr_as_expand=True)
