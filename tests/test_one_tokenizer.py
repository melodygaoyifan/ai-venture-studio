"""ADR-050 — one tokenizer, and no consumer of it is blind to Chinese.

Two halves, and both are needed.

The BEHAVIOURAL half asserts, per consumer, that its lexical check is
non-inert in 中文. That is the property that actually failed: ADR-046's
duplicate gate never fired in the language the templates, the Studio
default and every benchmark case use, and it never errored while not
firing (ADR-048). Three more modules were in the same state at the same
time — `textsim` scored two IDENTICAL Chinese strings at 0.0, and all
three of its callers compare `>= threshold`, so they failed OPEN.

The STRUCTURAL half asserts that no module rebuilds a tokenizer beside
the shared one. Behavioural tests only cover the consumers that exist
today; the defect's actual mechanism is that a NEW site writes
`[a-z0-9]+` and nobody notices for a release. The AST walk is what makes
tomorrow's site fail here instead of in production.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ai_venture_studio import textsim
from ai_venture_studio.lexicon import content, content_length, is_cjk, tokenize
from ai_venture_studio.maintenance.correlate import _tokens as correlate_tokens
from ai_venture_studio.marketing.spam_policy import SpamPolicyConfig
from ai_venture_studio.marketing.substantiation import _content_words
from ai_venture_studio.product.mvp import _stems
from ai_venture_studio.upstream.requirements import tokens as requirement_tokens

SRC = Path(__file__).parent.parent / "src" / "ai_venture_studio"

# One sentence in each language saying the same thing, so every assertion
# below can be read as "and the Chinese one behaves like the English one".
ZH = "用户提交报修单之后不能删除它"
EN = "the user cannot delete a repair request after submitting it"


# --------------------------------------------------------------------------
# The tokenizer itself
# --------------------------------------------------------------------------


def test_chinese_text_produces_tokens_at_all():
    """The one-line statement of the whole defect class."""
    assert tokenize(ZH)
    assert tokenize(EN)


def test_cjk_is_detected_by_unicode_name_not_a_hard_coded_range():
    # The two call sites that had learned CJK independently each carried
    # their own range literal, and neither covered the extension blocks.
    assert is_cjk("报")
    assert is_cjk("\U00020000")  # Extension B — outside `[一-鿿]`
    assert not is_cjk("a")
    assert not is_cjk("。")


def test_bigrams_are_what_discriminates_and_unigrams_are_opt_in():
    grams = tokenize("报修单", unigrams=False)
    assert "报修" in grams and "修单" in grams
    assert "报" not in grams
    assert "报" in tokenize("报修单", unigrams=True)


def test_a_run_of_one_character_still_yields_that_character():
    # Otherwise the only token that run can produce is silently lost.
    assert tokenize("我要a的b", unigrams=False) == ["我要", "a", "b"]


def test_function_characters_are_dropped_but_only_when_alone():
    assert "的" not in tokenize("付款的方式", unigrams=True)
    # A bigram mixing a function character with a content one is a word.
    assert "款的" in tokenize("付款的方式", unigrams=False)
    # A bigram made ENTIRELY of function characters carries nothing.
    assert "的了" not in tokenize("的了", unigrams=False)


def test_the_latin_length_floor_never_applies_to_cjk():
    """The floor is a proxy for "this word carries meaning", and four
    characters of English is a word while four characters of Chinese is
    two. Applied to grams it drops every one of them — which is exactly
    the bug, restored by the fix for it."""
    assert content(ZH, min_latin=4)
    assert content("a bb ccc dddd", min_latin=4) == {"dddd"}


def test_content_length_counts_characters_not_grams():
    # Grams roughly double the character count; an ASCII rule reported
    # zero. Both are wrong.
    assert content_length("报修单") == 3
    assert content_length("two words") == 2


# --------------------------------------------------------------------------
# Every consumer, in both languages
# --------------------------------------------------------------------------


def test_the_reconciliation_gate_retrieves_in_chinese():
    """ADR-048's defect, pinned at its own call site."""
    assert requirement_tokens(ZH)
    assert requirement_tokens(EN)
    # And retrieval still discriminates: an unrelated sentence must not
    # share tokens with this one, or the gate matches everything.
    assert not requirement_tokens(ZH) & requirement_tokens("发票抬头需要支持公司名称")


def test_identical_chinese_text_is_similar_to_itself():
    """`textsim` failed OPEN in three consumers — opportunity dedup, the
    kill-registry Novelty match, and the marketing near-duplicate check
    all compare `>= threshold`, so a constant 0.0 meant nothing was ever
    a duplicate and everything was always novel."""
    assert textsim.similarity(ZH, ZH) == 1.0
    assert textsim.similarity(EN, EN) == 1.0
    assert textsim.similarity(ZH, "发票抬头需要支持公司名称") < 0.5


def test_the_incident_correlator_tokenizes_chinese():
    assert correlate_tokens("订单页面报错，用户无法提交")
    assert correlate_tokens("order page throws an error on submit")


def test_the_mvp_hypothesis_check_sees_a_chinese_slice():
    assert _stems(ZH)
    # And it still matches a paraphrase of itself, which is the job.
    assert _stems(ZH) & _stems("报修单提交后无法删除")


def test_the_claim_register_match_sees_chinese_copy():
    assert _content_words("订单页面在三秒内加载完成")
    assert _content_words("the order page loads in three seconds")


def test_a_substantial_chinese_page_is_not_thin():
    from ai_venture_studio.marketing.artifacts import Page
    from ai_venture_studio.marketing.spam_policy import spam_policy_check

    config = SpamPolicyConfig(thin_page_words=20)
    long_zh = "用户提交报修单之后不能删除它，管理员可以在后台查看全部记录并且导出。"
    pages = [
        Page(
            path=f"p{i}.md",
            text=long_zh,
            reviewer="someone",
            primary_measured=True,
        )
        for i in range(3)
    ]
    findings = spam_policy_check(pages, config=config)
    assert not [f for f in findings if f.rule == "thin_page_ratio"]


# --------------------------------------------------------------------------
# The structural half — tomorrow's call site
# --------------------------------------------------------------------------

# What a rebuilt tokenizer looks like, precisely: a `re.findall` /
# `finditer` / `split` whose pattern is character classes and quantifiers
# and NOTHING ELSE. That bareness is the signature — a pattern with any
# literal structure in it (`slug: ([\w-]+)`, `[\w/]+\.(?:py|ts)`) is
# parsing a known format, not turning prose into tokens, and its author
# knows exactly what it will and will not match. `[a-z0-9']+` standing
# alone is a claim about *language*, and it is wrong about half of them.
_TOKENIZING_CALLS = {"findall", "finditer", "split"}
# ...and it has to be matching WORDS. A bare class of punctuation —
# `re.split(r"[.。\n]", …)` — is a sentence splitter, it already carries
# 。 and ！ beside their ASCII twins, and it is not what broke.
_WORDISH = ("a-z", "A-Z", "0-9", "\\w", "\\s", "一-")
_STRUCTURE = re.compile(
    # Everything that is NOT structure: a character class, an escape, a
    # quantifier, an anchor, whitespace. What survives is literal text or
    # grouping — i.e. a format the pattern is parsing.
    r"\[[^\]]*\]|\\[a-zA-Z]|[+*?]|\{\d*,?\d*\}|[\s^$]"
)

# `lexicon` is the one place a tokenizer is written.
_ALLOWED = {"lexicon.py"}


def _tokenizer_shaped(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _TOKENIZING_CALLS:
        return False
    if not isinstance(func.value, ast.Name) or func.value.id != "re":
        return False
    if not node.args:
        return False
    arg = node.args[0]
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return False
    if not any(m in arg.value for m in _WORDISH):
        return False
    return not _STRUCTURE.sub("", arg.value)


def test_no_module_rebuilds_the_tokenizer():
    """The defect's real mechanism: a new call site writes `[a-z0-9]+`,
    it reads as obviously correct, and it is blind to half the system's
    users for a release. `lexicon` is the only place this is written."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name in _ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _tokenizer_shaped(node):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders, (
        "these split text into tokens with an ASCII-only pattern, which "
        "finds nothing in Chinese; import from ai_venture_studio.lexicon "
        f"instead: {offenders}"
    )


def test_the_guard_fires_on_the_patterns_that_actually_shipped():
    """A guard that cannot fire is the defect it was written to prevent —
    ADR-048's gate never fired and never errored either. So the detector
    is pinned against the four real patterns that shipped blind, and
    against the format-parsers it must leave alone."""
    caught = [
        r"[a-z0-9']+",                    # textsim.shingles, substantiation
        r"[A-Za-z_][A-Za-z0-9_]{2,}",     # requirements.tokens — ADR-048
        r"[a-z0-9]+",                     # mvp._stems, Latin half
        r"[一-鿿]",                        # mvp._stems, CJK half: a range
        r"[\s,]+",                        # a split on spaces, in a language
    ]                                      # that has none
    ignored = [
        r"slug: ([\w-]+)",                # a known prompt format
        r"[\w/]+\.(?:py|js|ts)",          # file paths out of a design doc
        r"^(\d+)\. \[\w+\] (\S+?):(\d+)",  # a numbered findings list
        r"[.。\n]",                        # a sentence splitter, already CJK
    ]

    def probe(pattern: str) -> bool:
        call = ast.parse(f"re.findall({pattern!r}, x)").body[0].value
        return _tokenizer_shaped(call)

    assert [p for p in caught if not probe(p)] == []
    assert [p for p in ignored if probe(p)] == []


def test_every_tokenizing_consumer_imports_from_the_one_module():
    """The counterpart to the AST walk: the six known consumers must
    still be *importing* it rather than having quietly grown their own
    again under a name the pattern check does not recognise."""
    consumers = [
        "textsim.py",
        "similarity.py",
        "maintenance/correlate.py",
        "upstream/requirements.py",
        "product/mvp.py",
        "marketing/substantiation.py",
        "marketing/spam_policy.py",
    ]
    for rel in consumers:
        source = (SRC / rel).read_text(encoding="utf-8")
        assert "from ai_venture_studio.lexicon import" in source, rel
