"""Deterministic provider for tests and offline runs.

Emits a finding for every added line that matches one of the planted-bug
patterns in the fixture set, in the same YAML shape a real voter must emit.
This keeps the end-to-end graph test hermetic (no network, no keys).
"""

from __future__ import annotations

import re

import yaml

from ai_venture_studio.providers.base import Provider, record_stop_reason, register

_PLANTED = [
    (re.compile(r"except\s*(Exception)?\s*:\s*pass"), "Swallowed exception", "P9", "high"),
    (re.compile(r"\beval\("), "eval() on untrusted input", "P6", "high"),
    (
        re.compile(r"f\"SELECT|SELECT .*(%s|\" *\+)", re.IGNORECASE),
        "SQL built by interpolation",
        "P6",
        "critical",
    ),
]

_DIFF_LINE = re.compile(r"^\+(?!\+\+)(.*)$", re.MULTILINE)
_FILE_HEADER = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


@register
class MockProvider(Provider):
    name = "mock"

    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
    ) -> str:
        return self.complete(
            model=model, system=system, user=messages[0]["content"], max_tokens=max_tokens
        )

    #: Tests set this to reproduce a response cut off at the output cap. Every
    #: mock reply resets the thread's stop reason, so a test that sets it gets
    #: exactly one truncated call — the same shape as the real failure, where a
    #: model returns a partial answer once and a complete one on retry.
    truncate_next: bool = False

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 4096) -> str:
        if type(self).truncate_next:
            type(self).truncate_next = False
            record_stop_reason("max_tokens")
        else:
            record_stop_reason("end_turn")
        from ai_venture_studio.compound import COMPOUND_MARKER
        from ai_venture_studio.leader import LEADER_MARKER
        from ai_venture_studio.maintenance.review import ROOTCAUSE_MARKER, TRIAGE_MARKER
        from ai_venture_studio.verify import VERIFIER_MARKER

        if VERIFIER_MARKER in system:
            return self._verify(user)
        if LEADER_MARKER in system:
            return self._lead(user)
        if COMPOUND_MARKER in system:
            return self._compound(user)
        if TRIAGE_MARKER in system:
            priority = "P4" if "cosmetic" in user.lower() else "P2"
            return yaml.safe_dump(
                {"priority": priority, "category": "crash", "rationale": "mock triage"}
            )
        if ROOTCAUSE_MARKER in system:
            has_suspects = "score" in user
            files_match = re.search(r"files: ([\w./-]+)", user)
            return yaml.safe_dump(
                {
                    "hypothesis": "mock hypothesis from top suspect"
                    if has_suspects
                    else "insufficient evidence",
                    "confidence": 75 if has_suspects else 30,
                    "implicated_commit": None,
                    "implicated_files": [files_match.group(1)] if files_match else [],
                    "next_action": "propose fix-PR",
                }
            )
        from ai_venture_studio.maintenance.fixpr import FIXPR_MARKER

        if FIXPR_MARKER in system:
            return self._fixpr(user)
        from ai_venture_studio.upstream.autopilot import REPORTER_MARKER
        from ai_venture_studio.upstream.fdr import FDR_ASSESSOR_MARKER

        if FDR_ASSESSOR_MARKER in system:
            if "just an idea" in user:
                return yaml.safe_dump(
                    {"ready": False, "summary": "需要更多信息",
                     "questions": ["谁会用它？", "用户具体做什么？"]}
                )
            return yaml.safe_dump({"ready": True, "summary": "可以开始构建", "questions": []})
        if REPORTER_MARKER in system:
            return "mock 确认/报告：会做 X，不做 Y。(plain-language output)"
        from ai_venture_studio.studio_chat import EXTRACTOR_MARKER
        from ai_venture_studio.upstream.correction import CORRECTION_MARKER
        from ai_venture_studio.upstream.telemetry import DIGEST_MARKER
        from ai_venture_studio.upstream.walkthrough import WALKTHROUGH_MARKER

        if EXTRACTOR_MARKER in system:
            return self._intake_extract(user)
        if CORRECTION_MARKER in system:
            slugs = re.findall(r"slug: ([\w-]+)", user)
            body = re.search(
                r"<complaint>\n(.*?)\n</complaint>", user, re.DOTALL
            )
            # One issue per non-empty line — the shape a founder actually
            # pastes when several things are wrong at once, and the only
            # split a mock can make without a model. A single-line
            # complaint therefore still yields exactly one issue.
            lines = [
                ln.strip() for ln in (body.group(1) if body else user).splitlines()
                if ln.strip()
            ] or [""]
            issues = []
            for line in lines:
                # Both spellings of the same intent: the zh UI says 新增, and
                # the en Studio's tests have to be able to reach the
                # scope-change branch without writing Chinese into an
                # English page.
                kind = (
                    "scope_change"
                    if "新增" in line or "new requirement" in line.lower()
                    else "fix"
                )
                # A line naming a feature routes to it, so a multi-issue
                # complaint can land on several specs the way a real
                # router would.
                named = next((s for s in slugs if s in line), None)
                issues.append({
                    "quote": line,
                    "spec_slug": named or (slugs[0] if slugs else "unknown"),
                    "kind": kind,
                    "instruction": "apply the founder's correction",
                })
            return yaml.safe_dump({"issues": issues}, allow_unicode=True)
        if WALKTHROUGH_MARKER in system:
            return "(mock: not a checklist)"  # forces the deterministic fallback
        if DIGEST_MARKER in system:
            return "# 本周 mock digest\n用户做了一些事。"
        from ai_venture_studio.upstream.probegen import PROBEGEN_MARKER

        if PROBEGEN_MARKER in system:
            return yaml.safe_dump(
                {"probes": [
                    {"name": "root-responds",
                     "body": 's, d, _ = call("GET", "/")\n'
                             'assert s < 500, f"root returned {s}"'},
                    {"name": "bad-body-dropped", "body": "this is not python ("},
                ]},
                sort_keys=False,
            )
        from ai_venture_studio.gepa import GEPA_PROPOSER_MARKER

        if GEPA_PROPOSER_MARKER in system:
            return yaml.safe_dump(
                {"charter": "mock improved charter: judge the artifact, cite "
                            "verbatim evidence, never widen scope",
                 "rationale": "mock: sharpened the evidence-citation rule"},
                sort_keys=False,
            )
        from ai_venture_studio.product.stage_engine import (
            PRODUCT_LEADER_MARKER,
            PRODUCT_VERIFIER_MARKER,
            PRODUCT_VOTER_MARKER,
        )
        from ai_venture_studio.product.stages import (
            EVIDENCE_WRITER_MARKER,
            MARKET_WRITER_MARKER,
            OPPORTUNITY_WRITER_MARKER,
            PRD_WRITER_MARKER,
        )

        if PRODUCT_VOTER_MARKER in system:
            return yaml.safe_dump(
                {"findings": [{"severity": "minor",
                               "problem": "mock nit: one sentence could be tighter",
                               "evidence": user.strip().splitlines()[0][:80]}]},
                sort_keys=False, allow_unicode=True,
            )
        if PRODUCT_VERIFIER_MARKER in system:
            return yaml.safe_dump({"verdict": "verified", "reason": "mock re-derivation"})
        if PRODUCT_LEADER_MARKER in system:
            return yaml.safe_dump({"summary": "mock leader synthesis for the gate."})
        if OPPORTUNITY_WRITER_MARKER in system:
            return self._opportunity_writer()
        if MARKET_WRITER_MARKER in system:
            return self._market_writer()
        if PRD_WRITER_MARKER in system:
            return self._prd_writer()
        if EVIDENCE_WRITER_MARKER in system:
            return self._evidence_writer(user)
        from ai_venture_studio.upstream.discover import BRIEFWRITER_MARKER
        from ai_venture_studio.upstream.plan import PLANNER_MARKER

        if BRIEFWRITER_MARKER in system:
            return yaml.safe_dump(
                {
                    "title": "Link sharing tool",
                    # Echo test markers from the idea so downstream mock
                    # stages (planner) can key on them via the brief.
                    "problem": "Sharing long URLs is unwieldy."
                    + (" make a cycle" if "make a cycle" in user else "")
                    + (" parallel plan" if "parallel plan" in user else ""),
                    "target_user": "Solo creators sharing links in chat.",
                    "hypotheses": [
                        {"statement": "Creators shorten >5 links/week", "evidence": "assumed"},
                        {"statement": "Click counts drive retention", "evidence": "sourced"},
                    ],
                    "scope_now": ["shorten a URL", "count clicks"],
                    "scope_later": ["custom domains"],
                    "scope_never": ["ads"],
                    "success_metrics": ["100 links created in week 1"],
                },
                sort_keys=False,
            )
        if PLANNER_MARKER in system:
            if "parallel plan" in user:
                return yaml.safe_dump(
                    {"tasks": [
                        {"id": "t1", "title": "API base", "description": "an item store (api)",
                         "depends_on": [], "lane": "api", "estimate_hours": 3,
                         "files_expected": ["feature_t1.py"]},
                        {"id": "t2", "title": "UI base", "description": "an item store (ui)",
                         "depends_on": [], "lane": "ui", "estimate_hours": 3,
                         "files_expected": ["feature_t2.py"]},
                        {"id": "t3", "title": "Wire together", "description": "an item store (wire)",
                         "depends_on": ["t1", "t2"], "lane": "api", "estimate_hours": 2,
                         "files_expected": ["feature_t3.py"]},
                    ]},
                    sort_keys=False,
                )
            cyclic = "make a cycle" in user and "revision_feedback" not in user
            tasks = [
                {"id": "t1", "title": "URL store", "description": "an item store for links",
                 "depends_on": ["t2"] if cyclic else [], "lane": "api", "estimate_hours": 4},
                {"id": "t2", "title": "Shorten endpoint", "description": "POST /links",
                 "depends_on": ["t1"], "lane": "api", "estimate_hours": 4},
                {"id": "t3", "title": "Click counting", "description": "count redirects",
                 "depends_on": ["t2"], "lane": "api", "estimate_hours": 3},
            ]
            return yaml.safe_dump({"tasks": tasks}, sort_keys=False)
        from ai_venture_studio.upstream.build import IMPLEMENTER_MARKER
        from ai_venture_studio.upstream.spec import SPECWRITER_MARKER

        if SPECWRITER_MARKER in system:
            return self._spec(user)
        if IMPLEMENTER_MARKER in system:
            return self._implement(user)
        from ai_venture_studio.maintenance.skills_registry import SKILL_DRAFT_MARKER

        if SKILL_DRAFT_MARKER in system:
            return yaml.safe_dump(
                {
                    "name": "mock-recurring-class",
                    "description": "mock skill for a recurring incident class",
                    "body": "Check the usual suspect first.",
                },
                sort_keys=False,
            )
        files = _FILE_HEADER.findall(user)
        file_path = files[0] if files else "unknown"
        findings = []
        for lineno, line in enumerate(_DIFF_LINE.findall(user), start=1):
            for pattern, title, taxonomy, severity in _PLANTED:
                if pattern.search(line):
                    findings.append(
                        {
                            "title": title,
                            "severity": severity,
                            "confidence": "likely",
                            "file_path": file_path,
                            "line_start": lineno,
                            "line_end": lineno,
                            "evidence": line.strip(),
                            "explanation": f"Mock provider matched planted pattern: {title}",
                            "taxonomy_hint": taxonomy,
                        }
                    )
        return yaml.safe_dump({"status": "OK", "findings": findings}, sort_keys=False)

    def _intake_extract(self, user: str) -> str:
        """The open-paragraph intake pass, deterministically.

        Only ever returns spans it actually copied out of the paragraph, so
        the verbatim guard in studio_chat has real input to check, plus one
        GUESS — the case that must never reach FDR.md unconfirmed.
        """
        match = re.search(r"<paragraph>\n(.*)\n</paragraph>", user, re.DOTALL)
        paragraph = (match.group(1) if match else user).strip()
        spans = [s.strip() for s in re.split(r"[.。\n]", paragraph) if s.strip()]
        said = {}
        for slot, span in zip(("who", "actions", "must"), spans):
            said[slot] = span
        return yaml.safe_dump(
            {
                "said": said,
                "guesses": [
                    {"slot": "success", "value": "people come back and use it "
                                                 "again the next week",
                     "why": "mock guess from the paragraph"},
                ],
            },
            sort_keys=False, allow_unicode=True,
        )

    def _lead(self, user: str) -> str:
        """Cluster findings that share a file and overlap within 2 lines."""
        rows = re.findall(
            r"^(\d+)\. \[\w+\] (\S+?):(\d+)-(\d+)", user, re.MULTILINE
        )
        clusters: list[list[int]] = []
        placed: dict[int, list[int]] = {}
        parsed = [(int(n), path, int(a), int(b)) for n, path, a, b in rows]
        for n, path, a, b in parsed:
            for m, mpath, ma, mb in parsed:
                if m in placed and mpath == path and a <= mb + 2 and ma <= b + 2:
                    placed[m].append(n)
                    placed[n] = placed[m]
                    break
            if n not in placed:
                cluster = [n]
                placed[n] = cluster
                clusters.append(cluster)
        return yaml.safe_dump(
            {"clusters": clusters, "summary": "mock leader summary"}
        )

    def _compound(self, user: str) -> str:
        data = yaml.safe_load(user) or {}
        recurring = data.get("recurring_findings") or []
        proposals = [
            {
                "constraint": f"Do not reintroduce: {item['title']}",
                "rationale": f"seen {item['count']}x in the window",
            }
            for item in recurring[:2]
        ]
        return yaml.safe_dump({"proposals": proposals}, sort_keys=False)

    @staticmethod
    def _is_miniprogram(user: str) -> bool:
        """The prompt carries the workspace profile (spec.py sends
        `profile:` plus the stack_hint). The mock used to answer with a
        Python module whatever the platform was, which is a fiction: WeChat
        小程序 cannot execute Python, and every hermetic miniprogram test
        was therefore exercising a product that could never have run. The
        cost of that fiction was invisible until the language rule became a
        wall."""
        return "miniprogram" in user or "小程序" in user

    def _spec(self, user: str) -> str:
        """Canned item-store spec; emits a vague criterion on the first pass
        when the request asks for it, clean once revision feedback arrives.
        A task:<id> marker in the request uniquifies the title/tests so
        autopilot runs produce distinct specs per task. JavaScript for
        小程序 workspaces, Python everywhere else — the mock answers in the
        language the profile can actually run."""
        task = re.search(r"task:([\w-]+)", user)
        suffix = f" {task.group(1)}" if task else ""
        vague_first_pass = "make it vague" in user and "revision_feedback" not in user
        criteria = [
            "When a client POSTs /items with a non-empty name, the system shall "
            "store the item and return its integer id.",
            "The system shall return all stored items, newest first, via GET /items.",
        ]
        if vague_first_pass:
            criteria[0] = "The system shall be fast when adding items."
        module = (
            "feature_" + re.sub(r"[^a-z0-9]", "_", task.group(1).lower())
            if task
            else "feature"
        )
        if self._is_miniprogram(user):
            return yaml.safe_dump(
                {
                    "title": f"Item store API{suffix}",
                    "design": f"Single module `miniprogram/utils/{module}.js` with an "
                    "in-memory ItemStore; tests drive add() and listItems().",
                    "criteria": criteria,
                    "test_skeletons": [
                        {
                            "path": f"miniprogram/utils/{module}.test.js",
                            "purpose": "add returns id; list returns newest first",
                            "covers": [0, 1],
                        }
                    ],
                },
                sort_keys=False,
            )
        return yaml.safe_dump(
            {
                "title": f"Item store API{suffix}",
                "design": f"Single module `{module}.py` with an in-memory ItemStore; "
                "tests drive add() and list_items().",
                "criteria": criteria,
                "test_skeletons": [
                    {
                        "path": f"tests/test_{module}.py",
                        "purpose": "add returns id; list returns newest first",
                        "covers": [0, 1],
                    }
                ],
            },
            sort_keys=False,
        )

    def _implement(self, user: str) -> str:
        if "<complaint>" in user:
            match = re.search(r'<existing_file path="([^"]+)">\n(.*?)\n</existing_file>',
                              user, re.DOTALL)
            if not match:
                return yaml.safe_dump({"files": []})
            return yaml.safe_dump(
                {"files": [{"path": match.group(1),
                            "new_content": match.group(2) + "\n# corrected per founder\n"}]},
                sort_keys=False,
            )
        if "review_findings" in user:
            match = re.search(r'<file path="([^"]+)">\n(.*?)\n</file>', user, re.DOTALL)
            if not match:
                return yaml.safe_dump({"files": []})
            return yaml.safe_dump(
                {"files": [{"path": match.group(1),
                            "new_content": match.group(2) + "\n# reviewed\n"}]},
                sort_keys=False,
            )
        js = re.search(r"Single module `miniprogram/utils/(feature[\w-]*)\.js`", user)
        if js:
            # Real JavaScript, runnable by `node --test`: a mock whose output
            # the product's own gate cannot execute teaches the suite nothing.
            module = js.group(1)
            return yaml.safe_dump(
                {
                    "files": [
                        {
                            "path": f"miniprogram/utils/{module}.js",
                            "new_content": (
                                "const items = [];\n\n"
                                "function add(name) {\n"
                                "  if (!name) throw new Error('name required');\n"
                                "  const id = items.length + 1;\n"
                                "  items.push({ id, name });\n"
                                "  return id;\n"
                                "}\n\n"
                                "function listItems() {\n"
                                "  return items.slice().reverse();\n"
                                "}\n\n"
                                "module.exports = { add, listItems };\n"
                            ),
                        },
                        {
                            "path": f"miniprogram/utils/{module}.test.js",
                            "new_content": (
                                "const test = require('node:test');\n"
                                "const assert = require('node:assert');\n"
                                f"const store = require('./{module}.js');\n\n"
                                "test('add returns an id', () => {\n"
                                "  assert.strictEqual(typeof store.add('a'), 'number');\n"
                                "});\n\n"
                                "test('list is newest first', () => {\n"
                                "  store.add('b');\n"
                                "  assert.strictEqual(store.listItems()[0].name, 'b');\n"
                                "});\n"
                            ),
                        },
                    ],
                    "notes": "mock implementation (小程序 / JavaScript)",
                },
                sort_keys=False,
            )
        design = re.search(r"Single module `(feature[\w-]*)\.py`", user)
        module = design.group(1) if design else "feature"
        return yaml.safe_dump(
            {
                "files": [
                    {
                        "path": f"{module}.py",
                        "new_content": (
                            "class ItemStore:\n"
                            "    def __init__(self):\n"
                            "        self._items = []\n\n"
                            "    def add(self, name):\n"
                            "        if not name:\n"
                            "            raise ValueError('name required')\n"
                            "        item_id = len(self._items) + 1\n"
                            "        self._items.append({'id': item_id, 'name': name})\n"
                            "        return item_id\n\n"
                            "    def list_items(self):\n"
                            "        return list(reversed(self._items))\n"
                        ),
                    },
                    {
                        "path": f"tests/test_{module}.py",
                        "new_content": (
                            f"from {module} import ItemStore\n\n\n"
                            "def test_add_returns_id():\n"
                            "    store = ItemStore()\n"
                            "    assert store.add('a') == 1\n\n\n"
                            "def test_list_newest_first():\n"
                            "    store = ItemStore()\n"
                            "    store.add('a'); store.add('b')\n"
                            "    assert [i['name'] for i in store.list_items()] == ['b', 'a']\n"
                        ),
                    },
                ],
                "notes": "mock implementation",
            },
            sort_keys=False,
        )

    def _fixpr(self, user: str) -> str:
        """Fix the planted `return a - b` bug in the provided file, else abstain."""
        match = re.search(r'<file path="([^"]+)">\n(.*?)\n</file>', user, re.DOTALL)
        if not match or "return a - b" not in match.group(2):
            return yaml.safe_dump(
                {"files": [], "abstain_reason": "no known planted bug found"}
            )
        fixed = match.group(2).replace("return a - b", "return a + b")
        return yaml.safe_dump(
            {
                "files": [{"path": match.group(1), "new_content": fixed + "\n"}],
                "regression_test": {
                    "path": "tests/test_regression_mock.py",
                    "new_content": "from calc import add\n\n"
                    "def test_add_regression():\n    assert add(1, 2) == 3\n",
                },
                "commit_message": "fix: restore addition in add()",
                "abstain_reason": None,
            },
            sort_keys=False,
        )

    def _verify(self, user: str) -> str:
        """Refute-by-quote: VERIFIED iff the claimed evidence text actually
        appears in the diff section of the prompt."""
        evidence_match = re.search(r"^evidence: (.+)$", user, re.MULTILINE)
        diff_match = re.search(r"<untrusted_diff>\n(.*)</untrusted_diff>", user, re.DOTALL)
        verified = bool(
            evidence_match
            and diff_match
            and evidence_match.group(1).strip() in diff_match.group(1)
        )
        return yaml.safe_dump(
            {
                "verdict": "VERIFIED" if verified else "NOT_REPRODUCIBLE",
                "reason": "mock quote check",
            }
        )

    # --- product-loop stage writers (P0/P1/P2/P4) ---------------------------

    def _opportunity_writer(self) -> str:
        claim = {
            "id": "C-O1",
            "text": "Support tickets cluster on manual CSV export pain",
            "kind": "user_need",
            "source_type": "user_reported",
            "n": 12,
            "evidence": [{"method": "ticket_cluster",
                          "locator": "evidence://tickets/export-pain",
                          "retrieved_at": "2026-07-23T16:20:00Z"}],
            "falsifier": "the cluster resolves to fewer than 5 distinct reporters",
        }
        candidates = [
            {"id": f"cand-{letter}",
             "statement": f"Reduce manual export pain, framing {letter}",
             "hypothesis": "admins will adopt one-click bulk export",
             "falsifier": "under 5% of active workspaces click the stub in 2 weeks",
             "cheapest_test": "bulk-export stub behind a click counter",
             "claims": [dict(claim, id=f"C-O{i}")]}
            for i, letter in enumerate("abc", start=1)
        ]
        return yaml.safe_dump({"candidates": candidates}, sort_keys=False)

    def _market_writer(self) -> str:
        return yaml.safe_dump(
            {
                "narrative": "Mock market assessment: bottom-up build from "
                "closed deals and the ticket cluster.",
                "claims": [
                    {"id": "C-M1",
                     "text": "Our closed deals in the segment averaged $3,600 "
                             "annual contract value",
                     "kind": "pricing",
                     "source_type": "primary_measured",
                     "n": 27,
                     "evidence": [{"method": "crm_query",
                                   "locator": "crm://reports/closed-won-2026H1",
                                   "retrieved_at": "2026-07-22T08:30:00Z"}],
                     "falsifier": "recomputing over the same set lands outside "
                                  "$3,300-$3,900"},
                    {"id": "C-M2",
                     "text": "Recruiting-ops users file tickets about manual "
                             "export pain",
                     "kind": "user_need",
                     "source_type": "user_reported",
                     "n": 12,
                     "evidence": [{"method": "ticket_cluster",
                                   "locator": "evidence://tickets/export-pain",
                                   "retrieved_at": "2026-07-23T16:20:00Z"}],
                     "falsifier": "the cluster resolves to fewer than 5 "
                                  "distinct reporters"},
                ],
                "sizing": {
                    "factors": [
                        {"name": "active_workspaces", "value": 900,
                         "source_type": "primary_measured", "n": 900},
                        {"name": "annual_contract_value", "value": 3600,
                         "source_type": "primary_measured", "n": 27},
                    ]
                },
            },
            sort_keys=False,
        )

    def _prd_writer(self) -> str:
        return yaml.safe_dump(
            {
                "prd": {
                    "id": "PRD-2026-014",
                    "problem_statement": "Recruiting ops teams lose hours "
                    "weekly to manual exports.",
                    "evidence_refs": ["C-M1", "C-M2"],
                    "affected_segment": {"name": "mid-market recruiting ops",
                                         "size_claim": "C-M1"},
                    "non_goals": ["No custom report builder this cycle.",
                                  "No new segment beyond workspace admins."],
                    "outcomes": [{
                        "id": "O-1",
                        "metric": "activation_rate",
                        "definition_ref": "metrics/activation_rate.md",
                        "baseline": {"value": 0.11,
                                     "source_type": "primary_measured",
                                     "n": 1840},
                        "target": {"value": 0.18, "by": "2026-11-30"},
                        "instrumentation": {"event": "workspace.first_export",
                                            "exists": False},
                    }],
                    "demand_hypotheses": [{
                        "id": "H-1",
                        "statement": "admins will adopt one-click bulk export",
                        "falsifier": "under 10% of active workspaces use it "
                                     "within 30 days",
                        "check": {"stage": "P4", "method": "cohort",
                                  "window_days": 30},
                    }],
                    "scope_tier": "standard",
                    "kill_criteria": ["O-1 misses 50% of target lift after 2 "
                                      "loops => Gate PL5 review"],
                },
                "prose": "Who: mid-market recruiting ops. Problem: hours lost "
                "weekly to manual exports, per ticket cluster C-M2. Why now: "
                "churn interviews name it.",
                "claims": [],
            },
            sort_keys=False,
        )

    def _evidence_writer(self, user: str) -> str:
        verdict = "not_supported" if "0.128" in user or "below" in user else "supported"
        return yaml.safe_dump(
            {
                "narrative": "Mock evidence bundle: cohort reading against the "
                "O-1 target, verdict against the pre-stated falsifier.",
                "verdicts": [{"id": "H-1", "verdict": verdict,
                              "falsifier_met": verdict == "not_supported"}],
                "reasons": [],
                "claims": [{
                    "id": "C-E1",
                    "text": "Cohort w1 activation reading recorded against "
                            "the O-1 target",
                    "kind": "demand",
                    "source_type": "primary_measured",
                    "n": 250,
                    "evidence": [{"method": "cohort_calc",
                                  "locator": "analytics://cohorts/w1",
                                  "retrieved_at": "2026-07-26T09:00:00Z"}],
                    "falsifier": "re-running the cohort query yields a "
                                 "different numerator",
                }],
            },
            sort_keys=False,
        )
