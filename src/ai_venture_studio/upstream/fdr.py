"""FDR — the one document a non-technical founder writes.

The FDR (Feature & Requirements Description) is the system's single input
for autopilot builds. Two jobs here:

1. Coach the user to a buildable FDR: a fill-in template with guidance and
   examples (bilingual), written into the workspace.
2. Assess an FDR before building: the assessor either declares it ready or
   produces SPECIFIC questions in the user's own language — the system
   asks instead of assuming (charter: no fabricated user intent).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ai_venture_studio.providers import get_provider
from ai_venture_studio.yamlx import extract_mapping

FDR_ASSESSOR_MARKER = "requirements assessor for non-technical founders"

TEMPLATE = """# 产品需求描述 / Product Requirements (FDR)

> 用你自己的话填写，不需要任何技术词汇。写中文或英文都可以。
> Fill this in using your own words — no technical terms needed.

## 1. 这是给谁用的？/ Who is this for?

（谁会用它？他们现在怎么解决这个问题？）
（例：小区里做团购的宝妈，现在用微信群接龙+Excel记账，经常记错。）

## 2. 用户用它来做什么？/ What do users do with it?

（按顺序写出用户会做的事，越具体越好。）
（例：1. 团长发起一个团购，写上商品和价格。2. 邻居点开小程序选商品下单。
3. 团长看到谁买了什么，一共收多少钱。）

## 3. 必须有的功能 / Must-have features

（没有这些就没法用的功能。每行一个。）

## 4. 暂时不要的功能 / NOT needed for now

（想到了但第一版不做的，写在这里防止误做。例：暂时不需要在线支付。）

## 5. 有什么限制或偏好？/ Constraints or preferences

（例：只在微信里用；要能发到群里；界面要大字体。没有就写"无"。）

## 6. 怎么算成功？/ What does success look like?

（例：第一周有10个团长发起过团购。）
"""

TEMPLATE_EN = """# Product Requirements (FDR)

> Fill this in using your own words — no technical terms needed.

## 1. Who is this for?

(Who will use it? How do they solve this problem today?)
(e.g. the two of us running a small studio; right now we track work in chat
messages and lose track of what is finished.)

## 2. What do users do with it?

(List what a user does, in order. The more specific, the better.)
(e.g. 1. Anyone adds a task with a title and who it is for. 2. Anyone marks
a task done. 3. We look at the open tasks and the finished ones separately.)

## 3. Must-have features

(Without these it is not usable. One per line.)

## 4. NOT needed for now

(Things you thought of but do not want in the first version. Writing them
here stops them being built by mistake. e.g. no logins yet.)

## 5. Constraints or preferences

(e.g. it only needs to work in a browser; it must run on our laptop. Write
"none" if there are none.)

## 6. What does success look like?

(e.g. the two of us stop tracking work in chat messages.)
"""

GUIDE = """# 怎么写好一份 FDR / How to write a good FDR

**好的 FDR 描述"用户做什么"，不描述"系统怎么实现"。**
A good FDR describes what USERS DO, never how the system works inside.

写得好的例子 / Good:
- "顾客选好商品后下单，可以留言备注" （具体的用户动作）
- "团长能看到按商品汇总的数量" （具体能看到什么）

写得不好的例子 / Not helpful:
- "系统要快、好用" → 说不清就删掉，系统会用合理的默认值
- "用数据库存储订单" → 这是实现细节，不用你操心
- "要有会员系统" → 太大了：拆开写用户具体做什么

四条规则 / Four rules:
1. 每个功能都从"谁 + 做什么 + 看到什么"的角度写。
   Every feature = who + does what + sees what.
2. "暂时不要"和"必须有"一样重要 — 它防止系统做多。
   The NOT-needed list matters as much as the must-have list.
3. 写不清楚没关系 — 系统会把问题列出来问你，而不是自己猜。
   If something is unclear, the system will ASK you — it never guesses.
4. **一份 FDR 只写一件事。** 第一份 FDR 写"最小能用的产品"；之后每个新功能、
   每个改动，都单独写一份小 FDR，用 `avs add` 加进去。FDR 越小，
   构建越准，出错越好查。
   **One FDR = one thing.** The first FDR is the smallest usable product;
   every later feature or change is its OWN small FDR added with
   `avs add`. Granular FDRs build more accurately and fail more
   debuggably.
"""


class Assessment(BaseModel):
    ready: bool
    summary: str = ""
    questions: list[str] = Field(default_factory=list)


_ASSESSOR_SYSTEM = f"""You are the {FDR_ASSESSOR_MARKER}. Decide whether this
FDR is buildable: does it say who the users are, what they do (concrete
actions), what must exist, and what is out of scope?

Rules:
- If anything essential is missing or ambiguous, ready: false and ask AT
  MOST 5 specific questions A NON-TECHNICAL PERSON CAN ANSWER, in the same
  language the FDR is written in. Never ask about technology choices.
- If it is buildable (imperfect is fine — reasonable defaults exist),
  ready: true with a one-line summary in the FDR's language.

Respond with ONLY YAML:
ready: true|false
summary: "..."
questions: ["...", "..."]
"""


def template_for(lang: str | None = None) -> str:
    """The blank FDR in the founder's language. The two templates ask the
    same six questions — a translation, not a different form."""
    from ai_venture_studio.studio_i18n import normalize

    return TEMPLATE_EN if normalize(lang) == "en" else TEMPLATE


def write_template(workspace: str | Path, lang: str | None = None) -> Path:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "FDR-GUIDE.md").write_text(GUIDE, encoding="utf-8")
    path = root / "FDR.md"
    if not path.exists():
        path.write_text(template_for(lang), encoding="utf-8")
    return path


_FEATURE_ASSESSOR_SYSTEM = f"""You are the {FDR_ASSESSOR_MARKER}, reading a
CHANGE REQUEST against a product THAT ALREADY EXISTS. The existing product's
requirements are given to you below the FDR; they are already built, already
true, and you must not ask for them again.

The only question is whether this ONE change is specific enough to act on:
what should be different after it lands, and for whom. Everything the FDR
does not mention stays exactly as it is — that is the default, not a gap.

Rules:
- DO NOT ask who the users are, what the product is for, what is out of
  scope, or anything the existing requirements below already answer. A
  change request is not a product brief and must not be judged as one.
- ready: false ONLY if you cannot tell what should change — the request
  names no observable difference, or names two incompatible ones. Then ask
  AT MOST 3 specific questions A NON-TECHNICAL PERSON CAN ANSWER, in the
  FDR's language. Never ask about technology choices.
- Otherwise ready: true with a one-line summary in the FDR's language. A
  one-sentence change request that says what should be different IS ready.
  "Also let people cancel an order" is ready. Prefer ready: true.

Respond with ONLY YAML:
ready: true|false
summary: "..."
questions: ["...", "..."]
"""


def assess_fdr(
    fdr_text: str, *, provider: str = "anthropic", model: str = "claude-opus-4-8",
    product_context: str = "",
) -> Assessment:
    """Is this FDR buildable? Two bars, because there are two questions.

    WITHOUT `product_context` this is a first FDR: nothing exists yet, so the
    document has to establish users, actions, scope and non-scope, and asking
    about a gap is the only way to fill it.

    WITH it, the FDR is a change to a product that already answered all of
    that. The same bar becomes wrong in a specific way: it reads "everything
    this request does not mention" as missing rather than as unchanged, so a
    perfectly clear one-line change ("also let people cancel an order") comes
    back `ready: false` with five questions about users and scope — and
    `run_feature` returns at intake, before the reconciliation gate it exists
    to exercise. All three of run 18's follow-up FDRs stopped here, which is
    why the increment axis scored 0% without the gate ever running: one
    control, two call paths, and the second silently applying a bar
    calibrated for the first (ADR-051's shape, ADR-058).
    """
    user = f"<fdr>\n{fdr_text}\n</fdr>"
    if product_context:
        user += (
            "\n\n<existing_product_requirements>\n"
            f"{product_context}\n"
            "</existing_product_requirements>"
        )
    raw = get_provider(provider).complete(
        model=model,
        system=_FEATURE_ASSESSOR_SYSTEM if product_context else _ASSESSOR_SYSTEM,
        user=user,
        max_tokens=1024,
    )
    try:
        data = extract_mapping(raw, ("ready",))
    except ValueError:
        return Assessment(
            ready=False,
            summary="assessment unreadable — please try again",
            questions=["(internal: assessor output failed to parse; re-run)"],
        )
    return Assessment(
        ready=bool(data.get("ready")),
        summary=str(data.get("summary", "")),
        questions=[str(q) for q in (data.get("questions") or [])][:5],
    )
