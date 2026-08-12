"""Founder Studio strings, per language.

The Studio was written bilingual-with-Chinese-first, because its first users
were 小程序 founders. That is a fine default and a bad ceiling: an
English-speaking founder saw `写下你的产品需求 / Describe your product`, and
the README's product demo could not be shown in English at all.

So every user-facing string lives here, keyed by language:

- `en` is English only, and is the DEFAULT — no bilingual slash, because a
  bilingual UI is a compromise for a mixed audience, not an improvement for
  a single one.
- `zh` is the ORIGINAL bilingual text, character for character. `--lang zh`
  brings back exactly the UI 小程序 founders have been using; the strings
  were not touched, only the default.

There is deliberately no autodetection: guessing a founder's language from a
locale header and getting it wrong is worse than one flag, and the FDR itself
may be written in either language whichever UI they read.
"""

from __future__ import annotations

LANGUAGES = ("zh", "en")
# English is the default (v0.53). The Studio began Chinese-first because its
# first users were 小程序 founders; the repository is public and English-
# speaking, so the default now matches the audience that meets it first.
# `--lang zh` restores the original bilingual UI in full — nothing was
# removed, only the default moved.
DEFAULT_LANGUAGE = "en"

STRINGS: dict[str, dict[str, str]] = {
    # --- page titles ---------------------------------------------------
    "title_describe": {
        "zh": "写下你的产品需求 / Describe your product",
        "en": "Describe your product",
    },
    "title_confirm_plan": {
        "zh": "确认计划 / Confirm the plan",
        "en": "Confirm the plan",
    },
    "title_building": {"zh": "正在搭建 / Building…", "en": "Building…"},
    "title_interrupted": {
        "zh": "搭建中断了 / Build was interrupted",
        "en": "The build was interrupted",
    },
    "title_product": {"zh": "你的产品 / Your product", "en": "Your product"},
    "title_confirm_feature": {
        "zh": "确认新功能 / Confirm the new feature",
        "en": "Confirm the new feature",
    },
    "title_acceptance": {
        "zh": "验收清单 / Acceptance walkthrough",
        "en": "Acceptance walkthrough",
    },
    # --- buttons ------------------------------------------------------
    "btn_check_and_plan": {
        "zh": "检查并生成计划 / Check &amp; make the plan",
        "en": "Check it &amp; make the plan",
    },
    "btn_start_building": {
        "zh": "开始搭建 / Start building",
        "en": "Start building",
    },
    "btn_edit_fdr": {"zh": "改需求 / Edit FDR", "en": "Edit the FDR"},
    "btn_edit_and_restart": {
        "zh": "改需求，重新来 / Edit FDR &amp; start over",
        "en": "Edit the FDR &amp; start over",
    },
    "btn_build_feature": {
        "zh": "开始添加这个功能 / Build this feature",
        "en": "Build this feature",
    },
    "btn_correct": {"zh": "修正 / Correct it", "en": "Correct it"},
    "btn_check_feature": {
        "zh": "检查这个功能 / Check this feature",
        "en": "Check this feature",
    },
    "btn_undo": {
        "zh": "⏪ 回到上一个版本 / Undo last change",
        "en": "⏪ Undo the last change",
    },
    "btn_retry": {"zh": "重试", "en": "Retry"},
    "btn_resume": {"zh": "继续", "en": "Resume"},
    "btn_continue_build": {
        "zh": "▶ 继续构建（自动补齐没做成的）/ Continue the build",
        "en": "▶ Continue the build (finishes what failed)",
    },
    # --- headings and prose -------------------------------------------
    "h_screenshots": {"zh": "页面截图 / Screenshots", "en": "Screenshots"},
    "h_features": {"zh": "功能 / Features", "en": "Features"},
    "h_something_wrong": {
        "zh": "哪里不对？/ Something wrong?",
        "en": "Something wrong?",
    },
    "h_add_feature": {"zh": "添加新功能 / Add a feature", "en": "Add a feature"},
    # ── Feature cards, and the composer's two real intents (v0.75) ───────
    # The home page listed post-build additions only, as bare directory
    # slugs, so the features the product was BUILT from were invisible —
    # there was nothing on the page a founder could point at and say
    # "this one". Pointing is the whole fix: recognition, not recall.
    "h_recent_changes": {
        "zh": "最近的改动 / Recent changes", "en": "Recent changes",
    },
    "spec_card_does": {
        "zh": "现在会做的 / What it does today",
        "en": "What it does today",
    },
    "btn_change_this": {"zh": "改这个 / Change this", "en": "Change this"},
    "change_this_placeholder": {
        "zh": "这个功能应该改成什么样？用你自己的话说。",
        "en": "What should this do instead? Say it in your own words.",
    },
    "btn_change_this_go": {
        "zh": "看看会改成什么样 / Show me what would change",
        "en": "Show me what would change",
    },
    # Two tabs, not three. "Something wrong?" and "Is it broken?" posted to
    # the same form and the router never saw the difference — a fork the
    # founder had to resolve and the backend then ignored.
    "tab_change": {"zh": "改点什么 / Change something", "en": "Change something"},
    "tab_add": {"zh": "加点新东西 / Add something new", "en": "Add something new"},
    "link_acceptance": {
        "zh": "📋 验收清单 / Acceptance walkthrough",
        "en": "📋 Acceptance walkthrough",
    },
    "link_back": {"zh": "← 返回 / back", "en": "← Back"},
    # A named thing this workspace does not have. Loud, because a silent
    # redirect home after a button press reads as a broken button.
    "no_such_lead": {
        "zh": "这个名字不在这个工作区里 / Not in this workspace",
        "en": "That name is not in this workspace",
    },
    "title_no_task": {"zh": "找不到该模块 / Module not found", "en": "Module not found"},
    "title_no_task_missing": {
        "zh": "计划里没有叫 {name} 的模块，所以没有东西可以重试。"
              " / No module called {name} is in the plan, so there is nothing to retry.",
        "en": "No module called {name} is in the plan, so there is nothing to retry.",
    },
    "title_no_feature": {
        "zh": "找不到该功能 / Feature not found", "en": "Feature not found",
    },
    "title_no_feature_missing": {
        "zh": "这个工作区里没有叫 {name} 的待建功能。"
              " / This workspace has no pending feature called {name}.",
        "en": "This workspace has no pending feature called {name}.",
    },
    "guide_summary": {
        "zh": "怎么写好？/ How to write a good FDR",
        "en": "How to write a good FDR",
    },
    "answer_first": {
        "zh": "请先回答这些问题 / Please answer:",
        "en": "Please answer these first:",
    },
    "planning": {"zh": "正在做计划… / planning…", "en": "planning…"},
    "state_done": {"zh": "✅ 已完成", "en": "✅ done"},
    "state_pending_confirm": {"zh": "待确认", "en": "awaiting confirmation"},
    "first_version": {"zh": "(初版)", "en": "(first version)"},
    "correction_hint": {
        "zh": "用你自己的话说 — 小修会直接修好，需求变化会走正规变更。",
        "en": "Say it in your own words. A small fix is repaired directly; a "
              "change of requirements goes through the formal change process.",
    },
    "correction_placeholder": {
        "zh": "例：下单按钮的文字应该是「参加接龙」，不是「提交」。",
        "en": "e.g. the button on the task form should say “Add task”, not "
              "“Submit”.",
    },
    "feature_hint": {
        "zh": "一次只写一个功能或改动 — 越小越准。One feature per FDR — smaller "
              "is better.",
        "en": "One feature or change per FDR — smaller is more accurate.",
    },
    "feature_placeholder": {
        "zh": "例：住户可以取消自己的订单，取消后汇总自动更新。",
        "en": "e.g. anyone can reopen a task they marked done by mistake.",
    },
    "failed_modules": {"zh": "没做成的模块 / Failed modules",
                       "en": "Modules that did not build"},
    # Named _modules_ explicitly: this used to be "failed_hint", the same key
    # the error page uses further down, so the later definition silently won
    # and this card told founders their API key was missing.
    "failed_modules_hint": {
        "zh": "可以先不管它们，产品其余部分能用；也可以单独重试：",
        "en": "You can leave them: the rest of the product works. Or retry one "
              "on its own:",
    },
    "interrupted_lead": {
        "zh": "上次搭建没有做完就停了。",
        "en": "The last build stopped before it finished.",
    },
    "interrupted_all_done": {
        "zh": "所有模块其实都做完了 — 在终端运行 <code>avs preview</code> "
              "查看产品。",
        "en": "Every module actually finished — run <code>autoproduct "
              "preview</code> in the terminal to see the product.",
    },
    "interrupted_resume": {
        "zh": "已完成的模块都保留着。一键继续，剩下的会带着上次失败的原因重新做；"
              "也可以逐个继续：",
        "en": "Finished modules are kept. Continue in one click — the rest "
              "are re-attempted knowing why they failed last time — or "
              "resume one at a time:",
    },
    # --- studio modes (v0.55) -------------------------------------------
    "h_engineer": {
        "zh": "构建内幕 / Build internals",
        "en": "Build internals",
    },
    "mode_note_engineer": {
        "zh": "工程师模式 — 显示命令行视角的模块 ID 和状态，可用 --mode 切换。"
              " / Engineer mode — switch with --mode.",
        "en": "Engineer mode — task IDs and states as the CLI sees them. "
              "Switch with --mode.",
    },
    "eng_profile": {"zh": "项目类型 / Profile", "en": "Profile"},
    "eng_no_plan": {
        "zh": "还没有计划 — 先写需求。/ No plan yet.",
        "en": "No plan yet — write the FDR first.",
    },
    "eng_cli": {
        "zh": "命令行等价操作 / CLI equivalents",
        "en": "CLI equivalents",
    },
    "eng_cli_body": {
        "zh": "avs retry-task <id> --repo-dir .   # 「重试」按钮\n"
              "avs preview                        # 在本地运行产品\n"
              "avs walkthrough                    # 验收清单\n"
              "avs verify                         # 重新跑检查",
        "en": "avs retry-task <id> --repo-dir .   # the Retry button\n"
              "avs preview                        # run the product locally\n"
              "avs walkthrough                    # acceptance walkthrough\n"
              "avs verify                         # re-run the checks",
    },
    "h_governance": {"zh": "治理 / Governance", "en": "Governance"},
    "mode_note_enterprise": {
        "zh": "企业模式 — 显示当前 edition 的治理设置（.mas/edition.yaml），"
              "可用 --mode 切换。 / Enterprise mode — switch with --mode.",
        "en": "Enterprise mode — what this edition enforces, read from "
              ".mas/edition.yaml. Switch with --mode.",
    },
    "gov_edition": {"zh": "预设 / Edition", "en": "Edition"},
    "gov_rung": {"zh": "基建层级 / Substrate rung", "en": "Substrate rung"},
    "gov_wip": {"zh": "并行上限 / WIP limit", "en": "WIP limit"},
    "gov_weekly": {
        "zh": "每周评审预算（分钟）/ Weekly review minutes",
        "en": "Weekly review budget (minutes)",
    },
    "gov_never": {
        "zh": "永不合并的闸门 / Never-batched gates",
        "en": "Never-batched gates",
    },
    "gov_gate_owner_yes": {
        "zh": "每个闸门都需要指定负责人。/ Named gate owner required.",
        "en": "Every gate requires a named owner.",
    },
    "gov_gate_owner_no": {
        "zh": "闸门不要求指定负责人。/ No gate owner required.",
        "en": "No named gate owner required.",
    },
    "gov_attestations": {
        "zh": "存证记录 / Attestation entries",
        "en": "Attestation ledger entries",
    },
    "gov_no_ledger": {
        "zh": "还没有存证记录 — 尚未进行任何存证。/ No attestation ledger yet.",
        "en": "No attestation ledger yet — nothing has been attested.",
    },
    "gov_no_edition": {
        "zh": "此工作区还没有选择 edition — 运行 avs init --edition "
              "enterprise。/ No edition resolved for this workspace.",
        "en": "No edition resolved for this workspace — run "
              "avs init --edition enterprise.",
    },
    "gov_edition_error": {
        "zh": "edition 文件未通过检查 / Edition file fails lint",
        "en": "The edition file fails lint",
    },
    # --- the production loop: take it live / it's broken / housekeeping --
    "title_live": {
        "zh": "上线 / Take it live",
        "en": "Take it live",
    },
    "link_live": {
        "zh": "上线（部署与检查）/ Take it live",
        "en": "Take it live",
    },
    "live_run": {
        "zh": "在服务器上运行 / Run it on a server",
        "en": "Run it on a server",
    },
    "live_run_hint": {
        "zh": "把这个文件夹放到服务器上（内网虚拟机即可），装好 Python，"
              "运行这一条命令 — 和每次验证用的完全相同：/ Copy this folder "
              "to the server, install Python, run this one command:",
        "en": "Copy this folder to the server (an internal VM is fine), "
              "install Python, and run this one command — the same one "
              "every build verification used:",
    },
    "live_run_note": {
        "zh": "PORT 环境变量决定端口；数据文件随文件夹一起走。/ PORT picks "
              "the port; the data file travels with the folder.",
        "en": "The PORT environment variable picks the port; the data "
              "file travels with the folder.",
    },
    "live_persistence": {
        "zh": "数据存储 / Your data",
        "en": "Your data",
    },
    "live_local_db": {
        "zh": "本地数据库已就绪：/ Local database provisioned:",
        "en": "Local database provisioned:",
    },
    "live_no_services": {
        "zh": "尚未登记任何存储服务。/ No storage service registered yet.",
        "en": "No storage service registered yet.",
    },
    "live_cloud_steps": {
        "zh": "云数据库开通步骤（SERVICES.md）/ Cloud database steps",
        "en": "Cloud database steps (SERVICES.md)",
    },
    "btn_cloud_guide": {
        "zh": "生成云数据库指南 / Write the cloud guide",
        "en": "Write the cloud database guide",
    },
    "live_no_catalog": {
        "zh": "此产品类型没有引导式云服务目录 — 数据存储是产品自身设计的一部分"
              "（见 FDR 与 design.md）。/ No guided cloud catalog for this "
              "profile; storage is part of the product's own design.",
        "en": "No guided cloud catalog for this profile — the data store "
              "is part of the product's own design (see the FDR and "
              "design.md).",
    },
    "btn_cloud_guide_again": {
        "zh": "重新生成指南 / Rewrite the guide",
        "en": "Rewrite the guide",
    },
    "live_guide_effect": {
        "zh": "会写入 SERVICES.md：按你的产品类型给出白话开通步骤，"
              "凭据放入保管库，绝不进入代码或提示词。/ Writes SERVICES.md "
              "with plain-language steps; credentials go to the vault.",
        "en": "Writes SERVICES.md — plain-language setup steps for your "
              "product type; credentials go in the vault, never into code "
              "or prompts.",
    },
    "live_boundary": {
        "zh": "谁来按部署按钮 / Who presses the deploy button",
        "en": "Who presses the deploy button",
    },
    "live_boundary_note": {
        "zh": "avs 从不自行部署上线。自动化部署的机关存在，但默认解除武装 — "
              "由一位署名的人写下限期策略后才生效（ADR-031）。在那之前，"
              "按钮是你的。/ avs never deploys on its own; automation stays "
              "disarmed until a named human arms a policy.",
        "en": "avs never deploys to production on its own. The automation "
              "exists but stays disarmed until a named human writes an "
              "attributed, expiring policy (ADR-031). Until then, the "
              "button is yours — which is the point.",
    },
    "live_verify": {
        "zh": "它现在在线吗？ / Is it answering right now?",
        "en": "Is it answering right now?",
    },
    "live_never_checked": {
        "zh": "还没检查过 — 填入你部署后的网址试试。/ Never checked — paste "
              "your deployed URL.",
        "en": "Never checked — paste the URL where you put it.",
    },
    "btn_check_live": {
        "zh": "检查 / Check",
        "en": "Check",
    },
    "house_head": {
        "zh": "日常维护 / Housekeeping",
        "en": "Housekeeping",
    },
    "house_never": {
        "zh": "清扫角色还没运行过 — 运行：/ The sweep role has not run yet:",
        "en": "The sweep role has not run yet — run:",
    },
    "house_unreadable": {
        "zh": "清扫摘要无法解析。/ The sweep digest is unreadable.",
        "en": "The sweep digest is unreadable.",
    },
    "house_clean": {
        "zh": "上次清扫：无事可做（已记录，不是沉默）。/ Last sweep: clean "
              "pass, recorded.",
        "en": "Last sweep: nothing to tidy — a recorded clean pass, not "
              "silence.",
    },
    "house_items": {
        "zh": "项待处理 / item(s) queued",
        "en": "item(s) queued",
    },
    "house_actionable": {
        "zh": "项可自动处理（人工晋升后）/ actionable",
        "en": "actionable within the cap",
    },
    "house_note": {
        "zh": "由清扫角色从框架队列收集；升级动作永远由人晋升。/ Harvested "
              "by the sweep role; promotion is always a human decision.",
        "en": "Harvested by the sweep role from the framework's own "
              "queues; promoting it to act is always a human decision.",
    },
    "btn_run_sweep": {
        "zh": "现在做一次维护检查 / Run a housekeeping check",
        "en": "Run a housekeeping check",
    },
    "house_run_note": {
        "zh": "SW0 只报告，不改动任何东西；对某项采取行动永远是人的晋升决定。"
              "/ Report-only at SW0; acting on an item is a human promotion.",
        "en": "Report-only at rung SW0 — nothing is changed; acting on an "
              "item is always a human promotion decision.",
    },
    "btn_evidence": {
        "zh": "导出 Gate-R 证据包 / Export the Gate-R evidence bundle",
        "en": "Export the Gate-R evidence bundle",
    },
    "title_evidence": {
        "zh": "证据包 / Evidence bundle",
        "en": "Evidence bundle",
    },
    "evidence_written": {
        "zh": "证据包已写入 / Evidence bundle written",
        "en": "Evidence bundle written",
    },
    "evidence_note": {
        "zh": "逐条列出这次评审的闸门与结论 — 由人附到 CAB/变更申请上；"
              "Studio 从不代为提交。/ Line-by-line gate record for the CAB "
              "submission; a human attaches it, the Studio never submits.",
        "en": "The line-by-line gate record for a CAB/change submission — "
              "a human attaches it; the Studio never submits anything "
              "anywhere.",
    },
    "gov_deploys": {
        "zh": "部署评审（Gate 5）/ Deploy reviews (Gate 5)",
        "en": "Deploy reviews (Gate 5)",
    },
    "gov_no_deploys": {
        "zh": "还没有部署评审 — 运行：/ None yet — run:",
        "en": "None yet — run:",
    },
    "gov_deploys_note": {
        "zh": "建议，从不执行：deploy-execute 在有署名限期策略之前保持解除武装。"
              "/ Recommendations only; deploy-execute stays disarmed.",
        "en": "Recommendations, never executions — deploy-execute stays "
              "disarmed until a named, expiring policy arms it.",
    },
    "h_broken": {
        "zh": "产品出故障了？ / Is it broken?",
        "en": "Is it broken?",
    },
    "inc_hint": {
        "zh": "用你自己的话描述故障（什么坏了、从什么时候开始、影响谁）。"
              "系统会分诊、找根因，并在可能时提出修复 — 修复和其他改动一样"
              "要过评审。/ Describe the failure in your own words; it will "
              "be triaged and root-caused.",
        "en": "Describe the failure in your own words — what broke, since "
              "when, who it affects. It gets triaged and root-caused; a "
              "proposed fix re-enters review like any other change.",
    },
    "inc_placeholder": {
        "zh": "例如：从今天早上开始，提交新申请的按钮点了没反应。/ e.g. since "
              "this morning, submitting a new request does nothing.",
        "en": "e.g. since this morning, clicking “submit a request” does "
              "nothing and no request shows up.",
    },
    "btn_incident": {
        "zh": "分诊这个故障 / Triage it",
        "en": "Triage it",
    },
    "title_incident": {
        "zh": "故障分诊 / Incident triage",
        "en": "Incident triage",
    },
    "inc_head": {
        "zh": "分诊结果 / What the triage found",
        "en": "What the triage found",
    },
    "inc_hypothesis": {
        "zh": "根因假设 / Likely cause",
        "en": "Likely cause",
    },
    "inc_next": {
        "zh": "建议动作 / Suggested next step",
        "en": "Suggested next step",
    },
    "inc_v_low": {
        "zh": "已记录 — 优先级不高，暂不需要动作。/ Logged — low priority.",
        "en": "Logged — low priority, nothing urgent to do.",
    },
    "inc_v_cause": {
        "zh": "找到了可能的原因。/ A likely cause was found.",
        "en": "A likely cause was found.",
    },
    "inc_v_escalate": {
        "zh": "需要人来看 — 系统没能自动定位原因。/ Needs a human — the "
              "cause could not be pinned down automatically.",
        "en": "This needs a human — the cause could not be pinned down "
              "automatically.",
    },
    "inc_saved_at": {
        "zh": "完整的技术记录已保存，交给维护这个产品的人即可：/ The full "
              "technical record is saved here — hand it to whoever "
              "maintains the product:",
        "en": "The full technical record is saved here — hand it to "
              "whoever maintains the product:",
    },
    "btn_try_fix": {
        "zh": "尝试修复（会进评审）/ Attempt the fix",
        "en": "Attempt the fix",
    },
    "inc_fix_note": {
        "zh": "点击即批准一次修复尝试；产出的改动会像任何 PR 一样重新进入"
              "代码评审，绝不直接上线。/ The click approves one attempt; "
              "the change re-enters review, never straight to production.",
        "en": "Your click approves one fix attempt; the resulting change "
              "re-enters code review like any PR — never straight to "
              "production.",
    },
    "title_fix": {
        "zh": "修复尝试 / Fix attempt",
        "en": "Fix attempt",
    },
    "fix_head": {
        "zh": "修复尝试结果 / How the attempt went",
        "en": "How the attempt went",
    },
    "fix_branch": {
        "zh": "分支 / branch",
        "en": "branch",
    },
    "fix_files": {
        "zh": "改动文件 / files changed",
        "en": "files changed",
    },
    "pre_head": {
        "zh": "可以开工了吗？ / Ready to build?",
        "en": "Ready to build?",
    },
    "pre_note": {
        "zh": "一个团队今天就能用它构建软件所需的每一项 — 实时读取，"
              "未就绪的项附上确切的修复命令。/ Every prerequisite for a "
              "team to build software today — read live; each gap carries "
              "its exact fix.",
        "en": "Every prerequisite for a team to build software today — "
              "read live from the environment, git, and the forge CLI; "
              "each gap carries its exact fix.",
    },
    # --- enterprise posture / trust / codebase panels -------------------
    "gov_posture": {
        "zh": "治理态势 / Governance posture",
        "en": "Governance posture",
    },
    "gov_posture_attention": {
        "zh": "需要处理 / needs attention:",
        "en": "needs attention:",
    },
    "gov_posture_measured": {
        "zh": "已度量 / measured:",
        "en": "measured:",
    },
    "gov_posture_unmeasured": {
        "zh": "尚未配置 / not yet configured:",
        "en": "not yet configured:",
    },
    "gov_posture_note": {
        "zh": "未度量的项显示为灰色，绝不显示为绿色 — 绿色只属于真正度量过的东西。"
              "/ Unmeasured items render grey, never green.",
        "en": "Unmeasured items render grey, never green — green is only "
              "earned by something actually measured.",
    },
    "gov_action_reload": {
        "zh": "本页每次刷新都会重新读取工作区 — 运行命令后刷新即可。"
              "/ This page re-reads the workspace on every reload.",
        "en": "This page re-reads the workspace on every reload — run the "
              "command, then refresh.",
    },
    "gov_edition_effect": {
        "zh": "该命令会写入 .mas/edition.yaml（含指定的闸门负责人）。"
              "/ Writes .mas/edition.yaml with the named gate owner.",
        "en": "This writes .mas/edition.yaml with the named gate owner; "
              "the Governance card fills in from it.",
    },
    "gov_substrate_effect": {
        "zh": "该命令会打印阶梯与起步档案；声明 .mas/substrate-profile.yaml "
              "后此表格生效。/ Prints the ladder; declaring the profile "
              "activates this grid.",
        "en": "Prints the rung-by-rung ladder and a starter profile; "
              "declaring .mas/substrate-profile.yaml activates this grid.",
    },
    "trust_head": {
        "zh": "模型通道与数据流向 / Model door & egress",
        "en": "Model door & egress",
    },
    "trust_note": {
        "zh": "安全评审最先问的问题 — 每次加载从环境与工作区实时读取，"
              "从不显示密钥的值。/ Read live from the environment; never "
              "shows a secret's value.",
        "en": "What a security review asks first — read live from the "
              "environment and workspace on each load; presence only, "
              "never a secret's value.",
    },
    "trust_provider": {
        "zh": "模型通道 / Model door",
        "en": "Model door",
    },
    "trust_auth_env": {
        "zh": "密钥来自环境变量 / key in environment",
        "en": "key in environment",
    },
    "trust_auth_file": {
        "zh": "密钥来自 *_FILE 挂载 / key via *_FILE mount",
        "en": "key via *_FILE secret mount",
    },
    "trust_auth_gateway": {
        "zh": "网关令牌（ANTHROPIC_AUTH_TOKEN + base URL）/ gateway bearer",
        "en": "gateway (ANTHROPIC_AUTH_TOKEN + base URL)",
    },
    "trust_auth_none": {
        "zh": "当前进程看不到任何凭据 / no credential visible",
        "en": "no credential visible to this process",
    },
    "trust_forge": {
        "zh": "代码托管（origin）/ Forge (origin remote)",
        "en": "Forge (origin remote)",
    },
    "trust_forge_none": {
        "zh": "未检测到远端 / none detected",
        "en": "no forge remote detected",
    },
    "trust_egress": {
        "zh": "出网 / Egress",
        "en": "Egress",
    },
    "trust_egress_note": {
        "zh": "遥测不发送任何内容（未配置端点）；完整出网清单见采购包 "
              "network-egress.md。/ Telemetry sends nothing; full outbound "
              "list in the procurement pack.",
        "en": "telemetry sends nothing (no endpoint configured); the "
              "complete outbound-host list ships in the procurement pack "
              "(network-egress.md)",
    },
    "trust_spend": {
        "zh": "本工作区花费 / Spend (this workspace)",
        "en": "Spend (this workspace)",
    },
    "trust_spend_none": {
        "zh": "尚无模型调用记录 / no model calls recorded",
        "en": "no model calls recorded",
    },
    "trust_spend_floor": {
        "zh": "至少 / at least",
        "en": "at least",
    },
    "code_head": {
        "zh": "代码库（avs 读到的）/ Codebase (what avs found)",
        "en": "Codebase (what avs found)",
    },
    "code_none": {
        "zh": "还没有代码地图 — 运行（本地读取，无 LLM、无网络）："
              "/ No codebase map yet — run (local read, no LLM, no network):",
        "en": "No codebase map yet — run (reads the repo locally; no LLM, "
              "no network):",
    },
    "code_unreadable": {
        "zh": "代码地图无法解析 — 重新运行 avs map 。/ Map unreadable — "
              "re-run avs map.",
        "en": "The codebase map is unreadable — re-run avs map.",
    },
    "code_http": {
        "zh": "HTTP 路由 / HTTP routes",
        "en": "HTTP routes",
    },
    "code_entries": {
        "zh": "入口 / entry points",
        "en": "entry points",
    },
    "code_note": {
        "zh": "由 avs map 从代码推导 — 规划器读取它，而不是靠文件名猜测。"
              "/ Derived from the code; the planner reads this instead of "
              "guessing.",
        "en": "Derived from the code by avs map — the planner reads this "
              "instead of guessing from filenames.",
    },
    # --- mode strip + per-mode pages (v0.56) ----------------------------
    "mode_founder": {"zh": "创始人 / Founder", "en": "Founder"},
    "mode_engineer": {"zh": "工程师 / Engineer", "en": "Engineer"},
    "mode_enterprise": {"zh": "企业 / Enterprise", "en": "Enterprise"},
    "correction_log": {
        "zh": "修正历史 / Correction history",
        "en": "Correction history",
    },
    "title_review": {
        "zh": "评审时间线 / Review timeline",
        "en": "Review timeline",
    },
    "review_verdict": {"zh": "结论 / Verdict", "en": "Verdict"},
    "review_duration": {"zh": "耗时 / Duration", "en": "Duration"},
    "eng_reviews": {
        "zh": "最近的评审 / Recent reviews",
        "en": "Recent reviews",
    },
    "eng_reviews_none": {
        "zh": "还没有评审记录。/ None yet.",
        "en": "None yet.",
    },
    "eng_voter_health": {
        "zh": "评审员健康度 / Voter health",
        "en": "Voter health",
    },
    "eng_voter_cols": {
        "zh": "（次数 · 被阻塞 · 换用备选模型）/ (runs · blocked · substituted)",
        "en": "(runs · blocked · substituted)",
    },
    "gov_ledger_ok": {
        "zh": "链校验通过 / chain verified",
        "en": "chain verified",
    },
    "gov_ledger_broken": {
        "zh": "存证链已损坏，从第 / ATTESTATION CHAIN BROKEN at entry",
        "en": "ATTESTATION CHAIN BROKEN at entry",
    },
    "gov_stages": {
        "zh": "阶段就绪状态 / Stage activation",
        "en": "Stage activation",
    },
    "gov_no_substrate": {
        "zh": "未声明基建档案（.mas/substrate-profile.yaml）— 各阶段按 S0 处理。"
              "运行 avs readiness 查看。/ No substrate profile declared.",
        "en": "No substrate profile declared (.mas/substrate-profile.yaml) — "
              "stages assume S0. Run avs readiness to see the ladder.",
    },
    "gov_dwell": {
        "zh": "闸门停留时间 / Gate dwell",
        "en": "Gate dwell",
    },
    "gov_dwell_median": {
        "zh": "中位停留 / Median dwell",
        "en": "Median dwell",
    },
    "gov_override_rate": {
        "zh": "推翻率 / Override rate",
        "en": "Override rate",
    },
    "gov_automation": {
        "zh": "自动化策略 / Automation policies",
        "en": "Automation policies",
    },
    "gov_armed": {
        "zh": "已启用，授权人 / ARMED by",
        "en": "ARMED by",
    },
    "gov_expires": {
        "zh": "到期 / expires",
        "en": "expires",
    },
    "gov_disarmed": {
        "zh": "未启用（默认）/ disarmed (the default)",
        "en": "disarmed (the default)",
    },
    "gov_policy_error": {
        "zh": "策略文件无效 / POLICY ERROR",
        "en": "POLICY ERROR",
    },
    # --- in-flight and failure pages (v0.57.1) --------------------------
    # --- cost transparency (v0.60) --------------------------------------
    "h_cost": {"zh": "花了多少 / What this cost", "en": "What this cost"},
    "cost_what": {"zh": "这个产品到目前", "en": "This product so far"},
    "cost_own_key": {
        "zh": "这笔费用由你自己的 API key 承担 — 系统从不代你花钱。",
        "en": "Billed to your own API key — the framework never spends money "
              "on your behalf.",
    },
    # --- the spend card (visibility on the pages where money is decided;
    # deliberately no cap — billing limits live at the provider, ADR-032) ---
    "cost_no_spend": {
        "zh": "本月还没有产生费用。",
        "en": "No model calls yet this month.",
    },
    "cost_floor_note": {
        "zh": "有些调用还没有单价记录，所以这个数字是下限，实际略高。",
        "en": "Some calls have no price on file, so this total is a floor — "
              "the true figure is slightly higher.",
    },
    "eng_cost_detail": {
        "zh": "按模型细分（`avs cost` 的输出）/ Per-model spend",
        "en": "Per-model spend (what `avs cost` prints)",
    },
    "link_verification": {
        "zh": "🔎 自动验收结果 / What was checked automatically",
        "en": "🔎 What was checked automatically",
    },
    "title_verification": {
        "zh": "自动验收结果 / Automatic verification",
        "en": "Automatic verification",
    },
    "title_working": {"zh": "正在处理 / Working…", "en": "Working on it…"},
    "working_lead": {
        "zh": "已经在做了，请不要重复提交。",
        "en": "Already working on this — no need to submit again.",
    },
    "working_hint": {
        "zh": "这一步要调用模型，通常需要几分钟。这个页面会自动刷新。",
        "en": "This step calls the model and usually takes a few minutes. "
              "This page refreshes itself.",
    },
    # The clock on the working page. A page that reloads every four seconds
    # and says the identical thing each time is indistinguishable from a
    # hung one, and this is the cheapest honest proof that it is not.
    "working_elapsed_fmt": {
        "zh": "已经等了 {clock}。",
        "en": "Running for {clock}.",
    },
    "working_fdr": {
        "zh": "正在读你的需求并生成计划。",
        "en": "Reading your requirements and making the plan.",
    },
    "working_correct": {
        "zh": "正在处理你的修正。",
        "en": "Working through your correction.",
    },
    "working_feature": {
        "zh": "正在检查这个新功能。",
        "en": "Checking the new feature.",
    },
    "title_failed": {
        "zh": "这一步没成功 / That step did not finish",
        "en": "That step did not finish",
    },
    "failed_lead": {
        "zh": "这一步没有做完。",
        "en": "That step stopped before it finished.",
    },
    # The reassurance is always true, so it is always shown. The CAUSE is a
    # separate string chosen from the actual exception (studio.failure_cause)
    # — one hardcoded guess used to claim "missing or exhausted API key" for
    # every failure, including transient overloads on a perfectly good key.
    "failed_safe": {
        "zh": "你的需求和已有成果都还在，什么都没丢。",
        "en": "Nothing was lost — your requirements and anything already built "
              "are still here.",
    },
    "failed_cause_key": {
        "zh": "看起来是模型 API key 的问题：没设置、被拒绝或额度用完了。"
              "修好后重试即可。",
        "en": "This looks like a problem with your model API key — missing, "
              "rejected, or out of credit. Fix that and try again.",
    },
    "failed_cause_busy": {
        "zh": "模型服务当时繁忙或连不上，自动重试也用完了。你的设置没有问题，"
              "过一会儿再点一次就行。",
        "en": "The model service was busy or unreachable, and the automatic "
              "retries ran out. Nothing is wrong with your setup — wait a "
              "moment and press the button again.",
    },
    "failed_cause_unknown": {
        "zh": "这次的原因不能确定，下面的技术细节写明了实际发生了什么。",
        "en": "The cause is not certain this time. The technical detail below "
              "says exactly what happened.",
    },
    "failed_detail": {
        "zh": "技术细节 / Technical detail",
        "en": "Technical detail",
    },
    # ── Lost-update guard on the FDR form ────────────────────────────────
    "title_conflict": {
        "zh": "需求文档在你编辑期间变过 / The requirements changed while you were editing",
        "en": "The requirements changed while you were editing",
    },
    "conflict_lead": {
        "zh": "这个页面打开之后，FDR.md 被改过了。",
        "en": "FDR.md changed after this page was opened.",
    },
    "conflict_hint": {
        "zh": "直接提交会盖掉那些改动，所以先停下来问你一句。两份都在下面，"
              "你选一份 —— 什么都不会自动丢。",
        "en": "Submitting would overwrite those changes, so nothing was saved "
              "yet. Both versions are below — pick one. Nothing is discarded "
              "automatically.",
    },
    "conflict_on_disk": {
        "zh": "磁盘上现在的版本（较新）",
        "en": "What is on disk now (newer)",
    },
    "conflict_yours": {
        "zh": "你这个页面里的版本",
        "en": "What this page had",
    },
    "btn_use_on_disk": {
        "zh": "用磁盘上的这份",
        "en": "Use the version on disk",
    },
    "btn_use_mine": {
        "zh": "用我页面里的这份（会覆盖）",
        "en": "Use mine (overwrites)",
    },
    # ── The conversational intake (studio_chat) ──────────────────────────
    # v0.69: the page opens with one open prompt, not with question 1 of 6.
    # The old title ("One question at a time") described the loop that came
    # after; it read as a form with extra steps, which is what it was.
    "title_chat": {
        "zh": "说说你想做什么 / Tell me what you want to build",
        "en": "Tell me what you want to build",
    },
    "chat_intro": {
        "zh": "用你自己的话写一段就行。我读完会先把取到的内容摆出来给你看，"
              "然后只问缺的那几项。随时可以停下来直接生成计划。",
        "en": "Write one paragraph in your own words. I read it, show you "
              "what I took from it, and then ask only about what is still "
              "missing. You can stop and go straight to the plan at any point.",
    },
    "chat_have_fdr": {
        "zh": "你已经写过需求文档了。",
        "en": "You already have a requirements document.",
    },
    "chat_have_fdr_hint": {
        "zh": "直接用它生成计划，或者改一改；也可以丢开它、用对话重新写一份。",
        "en": "Use it as it stands, edit it, or set it aside and build a new "
              "one through the conversation.",
    },
    "chat_start_over": {
        "zh": "不用这份，用对话重新写",
        "en": "Ignore it and answer questions instead",
    },
    "chat_switch_to_form": {
        "zh": "或者用表格一次填完",
        "en": "Or fill in the whole form instead",
    },
    "chat_switch_to_chat": {
        "zh": "不想填表格？一句一句说",
        "en": "Rather not fill in a form? Answer one question at a time",
    },
    "chat_answer_label": {
        "zh": "你的回答",
        "en": "Your answer",
    },
    "btn_chat_send": {
        "zh": "回答",
        "en": "Answer",
    },
    "btn_chat_skip": {
        "zh": "跳过这一题",
        "en": "Skip this one",
    },
    "btn_chat_enough": {
        "zh": "够了，直接生成计划",
        "en": "That's enough — go to the plan",
    },
    "btn_chat_restart": {
        "zh": "重新开始对话",
        "en": "Start the conversation over",
    },
    "chat_checking": {
        "zh": "正在看你写的需求，可能要一两分钟…",
        "en": "Reading your requirements — this can take a minute or two…",
    },
    "chat_clarify_lead": {
        "zh": "还有几个地方我不确定，问清楚了再动手，免得建错。",
        "en": "A few things I am not sure about. Better to ask than to guess "
              "and build the wrong thing.",
    },
    "chat_rounds_done": {
        "zh": "问得差不多了。剩下不清楚的地方我用合理的默认值处理，"
              "你也可以以后再单独加功能。",
        "en": "That is enough questions. I will use sensible defaults for "
              "whatever is still open — you can always add a feature later.",
    },
    "chat_skipped": {
        "zh": "（跳过）",
        "en": "(skipped)",
    },
    "chat_prior_fdr_saved": {
        "zh": "你原来写的需求已另存为 {name}，没有被覆盖。",
        "en": "Your previous requirements were saved as {name} — nothing was "
              "overwritten.",
    },
    # The six intake questions, conversational rather than form-shaped.
    "chat_q_who": {
        "zh": "这个产品是给谁用的？他们现在是怎么解决这个问题的？",
        "en": "Who is this for, and how do they solve the problem today?",
    },
    "chat_q_actions": {
        "zh": "用户打开它之后会做什么？按顺序说，越具体越好。",
        "en": "What does someone do after they open it? In order, as "
              "specifically as you can.",
    },
    "chat_q_must": {
        "zh": "哪些功能是没有就不能用的？",
        "en": "Which features would make it unusable if they were missing?",
    },
    "chat_q_not_needed": {
        "zh": "有什么是你想到了、但第一版不做的？写下来能防止系统做多。",
        "en": "What have you thought of but do NOT want in the first version? "
              "Naming it stops it being built by mistake.",
    },
    "chat_q_constraints": {
        "zh": "有什么限制或偏好吗？比如只在微信里用、要能发到群里。没有就说没有。",
        "en": "Any constraints or preferences? Say none if there are none.",
    },
    "chat_q_success": {
        "zh": "怎么算成功？一句能验证的话就行。",
        "en": "What does success look like? One sentence you could check.",
    },
    # ── The visual + IA redesign (v0.68) ─────────────────────────────────
    # The stage rail: where am I in the flow, on every founder-flow page.
    "rail_describe": {"zh": "描述 / Describe", "en": "Describe"},
    "rail_plan": {"zh": "计划 / Plan", "en": "Plan"},
    "rail_build": {"zh": "搭建 / Build", "en": "Build"},
    "rail_product": {"zh": "你的产品 / Your product", "en": "Your product"},
    # The mode switcher's add-only reassurance line.
    "mode_addonly_note": {
        "zh": "创始人能看到的一切都还在这个页面上 — 模式只增加可见性。"
              "/ Everything the founder sees is still on this page.",
        "en": "Everything the founder sees is still on this page — a mode "
              "only adds visibility, it never hides a form or a required "
              "action.",
    },
    "mode_back_founder": {
        "zh": "回到创始人视图 / Back to Founder",
        "en": "Back to Founder",
    },
    # The chat sidebar: the document being written, beside the conversation.
    "chat_sidebar_head": {
        "zh": "从你说的话里 / From what you said",
        "en": "From what you said",
    },
    "chat_sidebar_note": {
        "zh": "要搭建的就是这份文件 — 没有别的。"
              "/ This file is what gets built — nothing else.",
        "en": "This file is what gets built — nothing else.",
    },
    "chat_slot_who": {"zh": "给谁用 / Who it is for", "en": "Who it is for"},
    "chat_slot_actions": {
        "zh": "用户做什么 / What people do",
        "en": "What people do",
    },
    "chat_slot_must": {"zh": "必须有 / Must have", "en": "Must have"},
    "chat_slot_not_needed": {
        "zh": "第一版不做 / Not in v1",
        "en": "Not in v1",
    },
    "chat_slot_constraints": {
        "zh": "限制 / Constraints",
        "en": "Constraints",
    },
    "chat_slot_success": {
        "zh": "怎么算成功 / What success looks like",
        "en": "What success looks like",
    },
    "chat_read_file": {
        "zh": "查看或直接编辑这份文件 / Read or edit the file itself",
        "en": "Read or edit the file itself",
    },
    "chat_composer_note": {
        "zh": "现在还什么都没搭建。任何东西写下之前，你都会先确认一份计划。"
              "/ Nothing is built yet.",
        "en": "Nothing is built yet. You confirm a plan before anything is "
              "written.",
    },
    # Rendered markdown keeps the raw text one click away.
    "md_original": {"zh": "原文 / original text", "en": "original text"},
    "confirm_hint": {
        "zh": "请认真读第一段。如果它描述的不是你想要的，现在改需求是免费的 — "
              "按下按钮之后就不是了。/ Read the first paragraph carefully.",
        "en": "Read the first paragraph carefully. If it describes something "
              "you did not mean, editing the requirements now is free — "
              "after this button it is not.",
    },
    "cost_provider_limits": {
        "zh": "搭建会多次调用模型。花费上限在你的服务商账户里设置，不在这里。"
              "/ Spending limits live in your provider account, not here.",
        "en": "Building calls the model many times. Spending limits live in "
              "your provider account, not here.",
    },
    # The building page: honest texture for the long wait.
    "building_headline": {
        "zh": "正在搭建 / Building — {done} / {total}",
        "en": "Building — {done} of {total} modules done",
    },
    "building_note": {
        "zh": "可以关掉这个页面。搭建会自己继续，回来时这里接着显示。"
              "/ You can close this tab.",
        "en": "You can close this tab. The build runs on its own and this "
              "page picks it back up.",
    },
    "building_honesty": {
        "zh": "故意不显示百分比和预计时间 — 系统不知道下一次尝试是不是最后"
              "一次，编造的数字比诚实的步骤更糟。/ No percentage and no "
              "estimate on purpose.",
        "en": "No percentage and no estimate on purpose — the system does "
              "not know whether the next attempt is the last one, and a "
              "made-up number is worse than an honest step.",
    },
    "building_elapsed": {"zh": "已用时 / elapsed", "en": "elapsed"},
    # {amount} already carries its own "$" and any "≥" floor marker — a
    # lower bound must not be reprinted as a total.
    "building_spent_fmt": {
        "zh": "已花 {amount} / so far",
        "en": "{amount} so far",
    },
    # Status chips. Small caps labels; the failed states keep their
    # verbatim state name instead.
    "chip_done": {"zh": "完成 / DONE", "en": "DONE"},
    "chip_now": {"zh": "进行中 / NOW", "en": "NOW"},
    "chip_queued": {"zh": "排队 / QUEUED", "en": "QUEUED"},
    "chip_left": {"zh": "待做 / LEFT", "en": "LEFT"},
    "chip_built": {"zh": "已建成 / BUILT", "en": "BUILT"},
    "chip_partly": {
        "zh": "部分建成 / PARTLY BUILT",
        "en": "PARTLY BUILT",
    },
    # The verdict-first report page.
    "rep_modules_fmt": {
        "zh": "{done} / {total} 个模块 / modules",
        "en": "{done} of {total} modules",
    },
    "rep_working": {"zh": "现在能用的 / Working now", "en": "Working now"},
    "composer_head": {
        "zh": "告诉我要改什么 / Tell me what to change",
        "en": "Tell me what to change",
    },
    # Failure and interrupted cards: reassurance outranks the error.
    "fail_chip": {"zh": "没有做完 / DID NOT FINISH", "en": "DID NOT FINISH"},
    "btn_retry_step": {
        "zh": "再试一次这一步 / Try that step again",
        "en": "Try that step again",
    },
    "int_chip": {"zh": "提前停止 / STOPPED EARLY", "en": "STOPPED EARLY"},
    "int_headline": {
        "zh": "{done} / {total} 个模块已完成并保留。/ modules are finished "
              "and kept.",
        "en": "{done} of {total} modules are finished and kept.",
    },
    "int_why": {"zh": "为什么停了 / Why it stopped", "en": "Why it stopped"},
    # Engineer panel chrome.
    "eng_founder_link": {
        "zh": "▸ 创始人页面 / The founder page",
        "en": "▸ The founder page",
    },
    "eng_col_id": {"zh": "编号 / ID", "en": "ID"},
    "eng_col_state": {"zh": "状态 / STATE", "en": "STATE"},
    "eng_col_title": {"zh": "标题 / TITLE", "en": "TITLE"},
    # Enterprise panel: the drill-downs, grouped.
    "gov_evidence": {
        "zh": "证据，想看就看 / The evidence, when you want it",
        "en": "The evidence, when you want it",
    },
    # ── The key gate (v0.69) ─────────────────────────────────────────────
    # The tool is free, the model is not. Before this, that fact reached a
    # founder as a stack trace on their first send.
    "title_key_gate": {
        "zh": "先连上模型 / Connect the model first",
        "en": "Connect the model first",
    },
    "key_lead": {
        "zh": "这个工具是免费的，模型不是。搭建时的每一次模型调用都记在你自己的"
              "服务商账户上 — 我们不转售、不代付，也看不到你的账单。"
              " / The tool is free; the model is not.",
        "en": "This tool is free. The model is not. Every call a build makes "
              "is billed to your own provider account — we do not resell it, "
              "we do not pay for it, and we never see your bill.",
    },
    "key_paste_head": {
        "zh": "把你的密钥贴在这里 / Paste your key here",
        "en": "Paste your key here",
    },
    "key_paste_hint_fmt": {
        "zh": "读取自 {name}。/ Read from {name}.",
        "en": "This is the {name} the provider adapter reads.",
    },
    "btn_key_save": {
        "zh": "用这个密钥继续 / Use this key",
        "en": "Use this key",
    },
    "key_process_only": {
        "zh": "这个密钥只在当前进程里使用，绝不会写到硬盘上。关掉 Studio 就没了；"
              "要长期保存，请写进你自己的环境变量。"
              " / Used by this process only, never written to disk.",
        "en": "This key is used by this process only and is never written to "
              "disk. It is gone when you close the Studio; to keep it, set it "
              "in your own environment instead.",
    },
    # The shared-machine deployment (AVS_STUDIO_TOKEN). "This process only"
    # is true there and still misleading — the process is everyone's.
    "key_shared_head": {
        "zh": "这个 Studio 是共享的，密钥要从环境里来"
              " / This Studio is shared — the key comes from its environment",
        "en": "This Studio is shared — the key comes from its environment",
    },
    "key_shared_note": {
        "zh": "这个 Studio 用访问令牌保护，也就是说不止你一个人在用它。"
              "在这里粘贴的密钥会被整个进程共用，所有人的搭建都会花你的钱，"
              "所以这里不提供输入框。请让启动 Studio 的人把密钥放进环境变量，"
              "或者用下面任意一种通道。"
              " / A key pasted here would be spent by everyone who can reach"
              " this Studio, so the box is not offered.",
        "en": "This Studio is protected by an access token, which means more "
              "than one person can reach it. A key pasted here belongs to the "
              "whole process, so every one of their builds would spend your "
              "money — which is why the box is not offered. Ask whoever "
              "started the Studio to set the key in its environment, or use "
              "one of the doors below.",
    },
    "key_doors_head": {
        "zh": "不需要在这里输入密钥的通道 / Doors that need no key typed here",
        "en": "Doors that need no key typed here",
    },
    "key_doors_note": {
        "zh": "如果你的公司已经有下面任何一种通道，就用它启动 Studio，"
              "这一页不会再出现。/ Start the Studio with any of these instead.",
        "en": "If your company already has one of these, start the Studio "
              "with it and this page never appears again.",
    },
    "key_cost_head": {"zh": "大概要花多少 / What it costs", "en": "What it costs"},
    "key_cost_no_figure": {
        "zh": "钱花在搭建上：计划、写代码、评审，每一步都是模型调用。"
              "这个工作区还没有花过钱，所以这里没有数字可给 — 真实数字在你的"
              "服务商后台。/ No figure here: nothing has been spent yet.",
        "en": "Building is where the money goes — the plan, the code, the "
              "review are all model calls. This workspace has not spent "
              "anything yet, so there is no figure to show here; the real "
              "number lives in your provider's dashboard.",
    },
    "key_cost_spent_fmt": {
        "zh": "这个工作区本月已经花了 {amount}（按记录的用量估算）。"
              " / {amount} spent this month.",
        "en": "This workspace has spent {amount} this month, by its own "
              "recorded usage.",
    },
    "key_strip_set": {
        "zh": "密钥已就绪（仅本进程）。/ A key is set for this process.",
        "en": "A key is set for this process — nothing was written to disk.",
    },
    "key_fail_head": {
        "zh": "如果是密钥的问题，在这里换一个 / Paste a working key here",
        "en": "If the key is the problem, paste a working one here",
    },
    "key_demo_head": {
        "zh": "先看一次真实的运行记录 / See a real run first",
        "en": "See a real run first",
    },
    "key_demo_note": {
        "zh": "不需要密钥：这是这个仓库自己代码的一次真实评审记录（已脱敏），"
              "每一步都是流水线当时写下的。/ No key needed.",
        "en": "No key needed. This is a real, redacted review of this "
              "repository's own code — every step below was written by the "
              "pipeline while it ran.",
    },
    "link_demo": {
        "zh": "▶ 看这次记录 / Open the recorded run",
        "en": "▶ Open the recorded run",
    },
    "title_demo": {
        "zh": "记录回放 / A recorded run",
        "en": "A recorded run",
    },
    "demo_note": {
        "zh": "这是随包附带的演示评审，等同于命令行的 avs replay --demo。"
              " / The vendored demo review — the same as avs replay --demo.",
        "en": "The vendored demo review — the same audit trail "
              "avs replay --demo prints, rendered here.",
    },
    "title_no_demo": {
        "zh": "没有演示记录 / No recorded run",
        "en": "No recorded run",
    },
    "title_no_demo_missing": {
        "zh": "这个安装里没有附带 {name} 演示评审。"
              " / This installation ships no {name} review.",
        "en": "This installation ships no {name} review bundle.",
    },
    "key_refused": {
        "zh": "没有收到可用的密钥 — 什么都没有改动。"
              " / No usable key was given; nothing changed.",
        "en": "That was not a usable key — nothing was changed.",
    },
    # ── Open-prompt-first intake (v0.69) ─────────────────────────────────
    # One open prompt, one extraction pass, then questions only about the
    # gaps. Asking the six in a fixed order was a form wearing a chat's
    # clothes.
    "chat_q_open": {
        "zh": "说说你想做什么 — 用你自己的话写一段就够了，不用分点。"
              " / Tell me what you want to build.",
        "en": "Tell me what you want to build — one paragraph in your own "
              "words is plenty.",
    },
    "chat_open_lead": {
        "zh": "用你自己的话 / In your own words",
        "en": "In your own words",
    },
    "chat_reading": {
        "zh": "正在读你写的东西… / Reading what you wrote…",
        "en": "Reading what you wrote…",
    },
    "chat_extract_head": {
        "zh": "从你写的里面取到的 / Taken from what you wrote",
        "en": "Taken from what you wrote",
    },
    "chat_chip_said": {"zh": "你说的 / SAID", "en": "SAID"},
    "chat_chip_guess": {"zh": "猜的 / GUESS", "en": "GUESS"},
    "chat_guess_head": {
        "zh": "这一条是我猜的，对吗？/ This one is a guess — is it right?",
        "en": "This one is a guess — is it right?",
    },
    "chat_guess_note": {
        "zh": "你确认之前，它不会写进需求文件。文件里只放你自己说过的话。"
              " / Not written down until you confirm it.",
        "en": "It is not written into the requirements until you confirm it. "
              "The document holds your words, not ours.",
    },
    "chat_guess_fix": {
        "zh": "不对的话，用你自己的话写：/ Not right? Say it in your own words:",
        "en": "Not right? Say it in your own words:",
    },
    "btn_guess_yes": {
        "zh": "对，就是这样 / Yes, that is right",
        "en": "Yes, that is right",
    },
    "btn_guess_mine": {
        "zh": "用我写的 / Use my words instead",
        "en": "Use my words instead",
    },
    # ── Try it, beside its own acceptance list (v0.69) ───────────────────
    "title_try": {"zh": "试一试 / Try it", "en": "Try it"},
    "link_try": {"zh": "▶ 试一试 / Try it", "en": "Try it"},
    "try_lead": {
        "zh": "左边是怎么把产品跑起来，右边是当初说好的验收条件。一条一条看，"
              "对就打勾，不对就说哪里不对。/ Run it on the left, check the "
              "criteria on the right.",
        "en": "How to run it on the left; the criteria it was supposed to "
              "meet on the right. Go down the list: mark what is fine, and "
              "say what is wrong.",
    },
    "try_left_head": {"zh": "产品本体 / The product", "en": "The product"},
    "try_right_head": {
        "zh": "验收条件 / What it was supposed to do",
        "en": "What it was supposed to do",
    },
    "try_run_hint": {
        "zh": "在终端运行这一条，产品就跑起来了 — Studio 不会替你启动它，"
              "因为一个由网页启动、没人管生命周期的服务进程比手动一条命令更糟。"
              " / Run this in a terminal:",
        "en": "Run this in a terminal and the product starts. The Studio "
              "does not start it for you: a server spawned by a page load "
              "is a process nobody owns, on a port nobody chose.",
    },
    "try_run_entry_fmt": {
        "zh": "入口文件：{entry} / entry point: {entry}",
        "en": "Entry point: {entry}",
    },
    "try_run_miniprogram": {
        "zh": "小程序预览：用微信开发者工具打开这个目录（工具 → 导入项目）。"
              " / Open this folder in WeChat DevTools.",
        "en": "Open this folder in WeChat DevTools (import project) to see "
              "it running.",
    },
    "try_run_none": {
        "zh": "没有找到可以直接运行的入口文件（找过 app/main.py、main.py、"
              "app.py）。/ No runnable entry point was found.",
        "en": "No runnable entry point was found — app/main.py, main.py and "
              "app.py are the three this looks for.",
    },
    "try_no_shots": {
        "zh": "这次搭建没有留下截图。/ This build left no screenshots.",
        "en": "This build left no screenshots.",
    },
    "try_not_a_verdict": {
        "zh": "打勾只是你自己的记录，不是对产品的判决 — 在你按下修正按钮之前，"
              "产品不会有任何变化。/ Ticking changes nothing on its own.",
        "en": "A tick is your own note, not a verdict about the product. "
              "Nothing changes until you press the fix button.",
    },
    "try_ticks_fmt": {
        "zh": "你已经看过 {done} / {total} 条。/ {done} of {total} checked.",
        "en": "{done} of {total} checked by you.",
    },
    "try_no_rows": {
        "zh": "还没有验收条件可看 — 先搭建，验收清单会在那之后生成。"
              " / No criteria yet.",
        "en": "No criteria yet — the acceptance walkthrough is written "
              "after a build.",
    },
    "try_chip_fine": {"zh": "没问题 / FINE", "en": "FINE"},
    "try_chip_open": {"zh": "待看 / TO CHECK", "en": "TO CHECK"},
    "try_mark_auto": {
        "zh": "自动检查的结果 / what the automatic check found",
        "en": "what the automatic check found",
    },
    "btn_try_fine": {"zh": "✓ 没问题 / Fine", "en": "✓ Fine"},
    "btn_try_untick": {"zh": "取消这个勾 / undo this tick", "en": "undo this tick"},
    "btn_try_wrong": {"zh": "✗ 这里不对 / Wrong", "en": "✗ Wrong"},
    "btn_try_send_wrong": {
        "zh": "把这条报上去 / Send this one",
        "en": "Send this one",
    },
    "try_wrong_placeholder": {
        "zh": "这一条哪里不对？用你自己的话说。",
        "en": "What is wrong with this one? Say it in your own words.",
    },
    # ── One-tap complaints (v0.75) ───────────────────────────────────────
    # Marking a row wrong IS the complaint. It used to open an empty box and
    # wait for the founder to type it out again in prose, which is the most
    # expensive thing this page can ask for.
    "try_one_tap_complaint_fmt": {
        "zh": "这一条不对：{row}",
        "en": "This one is not right: {row}",
    },
    "try_wrong_one_tap_hint": {
        "zh": "直接报这一条，不用打字。",
        "en": "Sends this row as it stands — no typing.",
    },
    "btn_try_wrong_more": {
        "zh": "想多说两句 / Add a few words",
        "en": "Add a few words",
    },
    "title_no_row": {
        "zh": "找不到这一条 / Row not found",
        "en": "Row not found",
    },
    # ── Classification preview (v0.69) ───────────────────────────────────
    # The correction used to run on submit, so a founder learned that their
    # bug report had been read as a scope change — their own SCR, approved
    # in their name — afterwards, from a log line.
    "title_classify": {
        "zh": "先看清楚要做什么 / What this will do",
        "en": "What this will do",
    },
    "cls_fix_chip": {"zh": "小修 / SMALL FIX", "en": "SMALL FIX"},
    "cls_scope_chip": {
        "zh": "新需求 / NEW REQUIREMENT",
        "en": "NEW REQUIREMENT",
    },
    "cls_fix_head": {
        "zh": "这是一个小修，直接改好 / A small fix — repaired directly",
        "en": "A small fix — repaired directly",
    },
    "cls_scope_head": {
        "zh": "这是一个新需求，要单独做一次 / A new requirement — its own "
              "small build",
        "en": "A new requirement — its own small build",
    },
    "cls_fix_what": {
        "zh": "产品没有做到它自己承诺的事。会直接去改，改完跑测试；测试过不了"
              "就撤销，不会留下半截。/ The product is not doing what it "
              "already promised.",
        "en": "The product is not doing what it already promised, so it is "
              "repaired in place and the tests must pass afterwards. If they "
              "do not, the change is rolled back rather than left half done.",
    },
    "cls_scope_what": {
        "zh": "你要的是当初没答应过的东西 — 这会改需求本身，并按正式变更记录"
              "下来（你说的话就是授权，会原样存档）。/ This changes the "
              "requirement itself and is recorded as a formal change.",
        "en": "You are asking for something the criteria never promised. "
              "That changes the requirement itself: it is recorded as a "
              "formal change with your own words as the authorization, and "
              "the feature is rebuilt from the new requirement.",
    },
    "cls_many_head": {
        "zh": "你一次说了 {n} 件事 / You raised {n} separate things",
        "en": "You raised {n} separate things",
    },
    "cls_many_what": {
        "zh": "它们分别属于不同的功能，所以会一件一件处理 —— 每件都单独改、"
              "单独记录。不想现在处理的，把勾去掉就行。/ Each is handled on "
              "its own; untick anything you do not want done now.",
        "en": "They belong to different features, so each is handled on its "
              "own — its own change, its own record. Nothing here is "
              "bundled into anything else. Untick anything you did not "
              "mean as a request.",
    },
    "cls_issue_n": {"zh": "第 {n} 件 / Issue {n}", "en": "Issue {n}"},
    "btn_cls_confirm_all": {
        "zh": "好，这 {n} 件都做 / Yes, do all {n}",
        "en": "Yes, do all {n}",
    },
    "title_cls_result": {
        "zh": "改完了 / What was done",
        "en": "What was done",
    },
    "cls_result_head": {
        "zh": "{n} 件，每件的结果 / What happened to each of the {n}",
        "en": "What happened to each of the {n}",
    },
    "cls_res_fixed": {"zh": "已修好 / FIXED", "en": "FIXED"},
    "cls_res_scr_raised": {
        "zh": "已记为新需求 / RECORDED AS A NEW REQUIREMENT",
        "en": "RECORDED AS A NEW REQUIREMENT",
    },
    "cls_res_error": {
        "zh": "没能做成 / NOT DONE",
        "en": "NOT DONE",
    },
    # ── A requirement change, drafted and waiting for one press. Deliberately
    # not "SCR raised": nothing is raised until the founder agrees, and the
    # founder does not know what an SCR is.
    "cls_res_change_planned": {
        "zh": "改动方案已拟好 / CHANGE DRAFTED",
        "en": "CHANGE DRAFTED",
    },
    # ── What actually happened, said to the founder rather than to the log.
    # One per `correction.REASONS`. The rule for every one of them: say
    # whether their PRODUCT changed, and what to do next — those are the two
    # things they are reading the card for. `detail` still carries the
    # attempt counts and file lists, one fold down, for whoever wants them.
    "res_why_repaired": {
        "zh": "已经改好并保存。可以再试一次；不满意可以撤销。",
        "en": "Fixed and saved. Try it again — and you can undo it if it is "
              "not what you wanted.",
    },
    "res_why_no_change": {
        "zh": "产品没有任何改动 —— 这次改出来的东西和原来一模一样。"
              "换个说法再讲一次，说说你期待看到什么。",
        "en": "Nothing changed — the attempt produced exactly what was there "
              "already. Say it again in different words, and say what you "
              "expected to see.",
    },
    "res_why_tests_failed": {
        "zh": "没能在不弄坏别处的前提下改好，所以什么都没动 ——"
              "你的产品和刚才完全一样。可以说得更具体一点再试。",
        "en": "It could not be changed without breaking something else, so "
              "nothing was changed — your product is exactly as it was. Try "
              "again with more detail about what is wrong.",
    },
    "res_why_planned": {
        "zh": "这是新的需求，不是坏了。方案在下面，按下去才会开始做。",
        "en": "This is a new requirement rather than something broken. The "
              "plan is below; nothing is built until you press it.",
    },
    "res_why_unrouted": {
        "zh": "没能确定你说的是哪个功能，所以没有动任何东西。"
              "在对应功能上按「改这个」，就不用猜了。",
        "en": "It was not clear which feature you meant, so nothing was "
              "touched. Press “Change this” on the feature itself and there "
              "is nothing left to guess.",
    },
    "res_why_unknown_spec": {
        "zh": "指到了一个不存在的功能，所以没有动任何东西。"
              "在对应功能上按「改这个」再说一次。",
        "en": "It pointed at a feature that does not exist, so nothing was "
              "touched. Say it again from the feature's own “Change this”.",
    },
    "res_why_many_issues": {
        "zh": "这条里有好几件事，需要一件一件处理。分开再说一次。",
        "en": "This holds several separate things and they have to be taken "
              "one at a time. Send them separately.",
    },
    "res_why_crashed": {
        "zh": "出错了，没有动你的产品。可以再试一次。",
        "en": "Something went wrong. Your product was not touched, and you "
              "can try again.",
    },
    "btn_res_detail": {
        "zh": "技术细节 / Technical detail",
        "en": "Technical detail",
    },
    "chg_assumptions": {
        "zh": "我替你做的判断 / What I assumed",
        "en": "What I assumed",
    },
    "chg_criteria": {
        "zh": "改完之后的验收标准 / Acceptance after the change",
        "en": "Acceptance after the change",
    },
    "btn_chg_go": {"zh": "就这么改，开始做 / Make this change",
                   "en": "Make this change"},
    "title_chg": {"zh": "改需求 / Change a requirement",
                  "en": "Change a requirement"},
    "chg_busy": {
        "zh": "正在做上一次的改动，这次还没开始。"
              " / A build is already running — this change has NOT started.",
        "en": "A build is already running — this change has NOT started.",
    },
    "chg_busy_hint": {
        "zh": "等它做完，再点一次「哪里不对」把这条说一遍。"
              " / Wait for it to finish, then say this again.",
        "en": "Two builds on one workspace corrupt it. Wait for the current "
              "one to finish, then say this again.",
    },
    "cls_your_words": {"zh": "你说的 / Your words", "en": "Your words"},
    "cls_criterion": {
        "zh": "对应的验收条件 / The criterion this came from",
        "en": "The criterion this came from",
    },
    "cls_feature": {"zh": "对应的功能 / The feature", "en": "The feature"},
    "cls_instruction": {
        "zh": "会交给实现者的一句话 / What the implementer will be told",
        "en": "What the implementer will be told",
    },
    "cls_reword": {
        "zh": "说得不对？改一改再看一次 / Not right? Reword it",
        "en": "Not what you meant? Reword it and see again",
    },
    "cls_nothing_yet": {
        "zh": "到这里为止还什么都没有改。/ Nothing has changed yet.",
        "en": "Nothing has been changed yet — this page is the decision, "
              "not the work.",
    },
    "cls_cannot_route": {
        "zh": "没法把这条意见对应到某个功能上 / This could not be matched to "
              "a feature",
        "en": "This could not be matched to a feature",
    },
    "btn_cls_confirm_fix": {
        "zh": "对，去修 / Yes, fix it",
        "en": "Yes, fix it",
    },
    "btn_cls_confirm_scope": {
        "zh": "对，按新需求做 / Yes, build it as a new requirement",
        "en": "Yes, build it as a new requirement",
    },
    "btn_cls_reword": {
        "zh": "换个说法再看 / Reword and check again",
        "en": "Reword and check again",
    },
    "title_no_spec": {"zh": "找不到该功能 / Feature not found",
                      "en": "Feature not found"},
    "title_no_spec_missing": {
        "zh": "这个工作区里没有叫 {name} 的功能。"
              " / This workspace has no feature called {name}.",
        "en": "This workspace has no feature called {name}.",
    },
    # ── The change list, as the undo surface (v0.69) ─────────────────────
    "h_changes": {
        "zh": "改动记录，最新在上 / Changes, newest first",
        "en": "Changes, newest first",
    },
    "changes_linear_note": {
        "zh": "版本是一条直线，所以只能回到某一次改动之前 — 那之后的改动会"
              "一起没有。每次回退都会先存一个 rescue 分支，所以回退本身也是可以"
              "撤销的。/ A straight line: going back past one change undoes "
              "the later ones too.",
        "en": "The history is a straight line, so you can go back to a point "
              "in it — you cannot lift one change out of the middle and "
              "leave the rest. Every button below says how much it takes "
              "with it, and a rescue branch is saved first, so going back is "
              "itself reversible.",
    },
    "btn_undo_to": {
        "zh": "↩ 回到这次改动之前 / Go back to just before this change",
        "en": "↩ Go back to just before this change",
    },
    "undo_to_note_fmt": {
        "zh": "这样会连它后面的 {later} 次改动一起撤销（一共 {commits} 个提交）。"
              " / also undoes the {later} later change(s).",
        "en": "This also undoes the {later} later change(s) — {commits} "
              "commits in total.",
    },
    "undo_to_note_last_fmt": {
        "zh": "它后面没有别的改动记录了；会撤销 {commits} 个提交（这次改动，"
              "以及之后提交的任何东西）。/ undoes {commits} commit(s).",
        "en": "Nothing later has been recorded as a change; this undoes "
              "{commits} commit(s) — this change and anything committed "
              "after it.",
    },
    "undo_to_first": {
        "zh": "这是第一个版本，前面没有可以回到的地方。"
              " / The first version — nothing earlier to return to.",
        "en": "The first version — there is nothing earlier to return to.",
    },
    "title_no_checkpoint": {
        "zh": "找不到这个版本 / No such checkpoint",
        "en": "No such checkpoint",
    },
    "title_no_checkpoint_missing": {
        "zh": "这个工作区里没有叫 {name} 的版本记录。"
              " / This workspace has no checkpoint called {name}.",
        "en": "This workspace has no checkpoint called {name}.",
    },
    "title_no_row_missing": {
        "zh": "验收清单里已经没有编号 {name} 的这一条了 — 可能是重新生成过。"
              " / No row {name} is in the current list.",
        "en": "No row called {name} is in the current list — the acceptance "
              "walkthrough may have been rewritten since this page loaded.",
    },
}


def normalize(lang: str | None) -> str:
    """Accept 'en', 'EN', 'en-US'; anything unknown falls back to default."""
    if not lang:
        return DEFAULT_LANGUAGE
    code = str(lang).strip().lower().replace("_", "-").split("-")[0]
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


def t(lang: str, key: str) -> str:
    """One string. A missing key is a KeyError on purpose: a Studio page with
    a blank label is worse than a loud failure at startup."""
    return STRINGS[key][normalize(lang)]
