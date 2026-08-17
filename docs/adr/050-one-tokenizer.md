# ADR-050 — One tokenizer, imported everywhere

Status: accepted (v0.99.0)

## Context

ADR-048 recorded a defect: `requirements.tokens` used `[A-Za-z_][A-Za-z0-9_]{2,}`,
Chinese has no spaces and no ASCII letters, so every retrieval score was zero,
`relevant` returned an empty slice, and ADR-046's duplicate/contradiction gate
reported "no existing requirement matched" for **every** request in the
language the templates, the Studio default and every `benchmarks/products*`
case are written in. It never fired and it never errored.

That record treated it as one bug. It is not. Auditing the repo afterwards
turned up **seven** places that split text into tokens. Four had learned,
independently and in different words, that an ASCII rule finds nothing in
中文 — `similarity.py` and `product/mvp.py` each carry their own `[一-鿿]`
range, written from scratch, neither covering the extension blocks. Three had
not, and their state was verified rather than inferred:

- `textsim.similarity(x, x)` returned **0.0** for identical Chinese strings
  where identical English returned 1.0. All three of its consumers compare
  `>= threshold`, so it failed **open**: `product/opportunity.py` never
  deduplicated, `product/kill_registry.py`'s Novelty match never fired, and
  `marketing/spam_policy.py`'s near-duplicate rule never tripped.
- `maintenance/correlate._tokens("订单页面报错，用户无法提交")` returned the
  empty set — the incident-to-commit correlator scored nothing.
- `marketing/substantiation._content_words` returned nothing, so a registered
  claim was never recognised as registered and the finding fired on copy that
  was in fact substantiated.
- `marketing/spam_policy` counted a Chinese page as **zero** words, so a batch
  of substantial Chinese copy tripped the thin-page ratio at 100%.

Every one of these is the same shape as ADR-048's: silent, non-erroring, and
wrong in exactly one direction. Two of them are worse than the original,
because failing open means a gate that reports success while doing nothing.

The lesson kept having to be re-learned because there was nowhere to put it.

## Decision

**`ai_venture_studio/lexicon.py` is the only place in this system a tokenizer
is written.** Every call site imports from it. Latin words plus CJK grams, in
text order, lowercased.

**CJK is detected by Unicode name, not by a range literal.** `[一-鿿]` was in
two modules and neither covered the extension blocks. `unicodedata.name(ch)`
covers every CJK ideograph block the running Python knows about, including
ones added after the line was written.

**Bigrams, not a segmenter.** A single Han character appears in too many
unrelated words to rank anything; a bigram that straddles a word boundary is
symmetric noise, scoring equally against every candidate, rather than a missed
match. A real segmenter would be a new runtime dependency and a dictionary to
go stale, for a system whose whole premise is that the founder configures one
API key and nothing else.

**Policy stays per-caller; "sees CJK" is not a knob.** Stopword lists genuinely
differ — the correlator stops "error" and "traceback" because it is hunting
code symbols, and `requirements` stops "shall" and "system" because those are
the grammar of every EARS criterion. Collapsing those two lists because both
functions are called "tokenize" is how one of them quietly stops working
(ADR-038). So `lexicon` takes `stopwords`, `min_latin` and `unigrams` as
arguments and owns none of them. What it does own, and does not expose, is
that a token stream must see 中文.

**`min_latin` never applies to CJK.** A length floor is a proxy for "this word
carries meaning", and four characters of English is a word while four
characters of Chinese is two. Applied to grams the floor drops every one of
them — the original bug, restored by the fix for it. This is pinned by a test
rather than left to the next caller to notice.

**`unigrams` is the recall/precision dial, and the callers split on it.**
Ranking that must tolerate paraphrase passes True (付 links 付款 and 付钱);
retrieval that must *discriminate* — the ADR-046 gate, MVP scope matching —
passes False, because the slice it feeds is capped and noise costs the gate
the one candidate that mattered.

**The guard is structural, and the guard is itself pinned.** Behavioural tests
cover the consumers that exist today; the defect's mechanism is that a *new*
site writes `[a-z0-9]+`, reads as obviously correct, and ships blind for a
release. So an AST walk over `src/` fails on any `re.findall`/`finditer`/`split`
whose pattern is character classes and quantifiers and nothing else — the
bareness is the signature, since a pattern with literal structure in it
(`slug: ([\w-]+)`, `[\w/]+\.(?:py|ts)`) is parsing a known format rather than
making a claim about language. And because a guard that cannot fire is
precisely the defect this record is about, a further test drives the detector
against the five real patterns that shipped blind and the four format-parsers
it must leave alone.

## What this reverses

Nothing in ADR-038, which said two tokenizers with different stopword lists
are two jobs and must not be merged. That still holds and is why `lexicon`
takes stopwords as an argument instead of owning one list. What is merged here
is the *lexical* layer underneath both, which was never two jobs — it was one
job written seven times, correctly four of them.

It does change behaviour at four call sites beyond the CJK fix, and each is
deliberate:

- `requirements.tokens` previously required a letter or underscore to start a
  token, so a bare number was not a token. It now is. A criterion that says
  "within 3 seconds" should retrieve against one that says "within 5 seconds";
  those are candidates for `contradicts`, which is the gate's whole point.
- `mvp._stems` truncates to four characters for Latin tokens only. Truncating
  a bigram is a no-op today and would be a bug the moment the gram size moves.
- `substantiation._content_words` keeps its digit-drop, because numbers are
  `_numbers`' job and counting them twice would let a bare figure carry the
  register overlap on its own.
- `spam_policy`'s thin-page count is now Latin words plus CJK **characters**.
  Counting grams would report a Chinese page as roughly twice its length,
  which is the opposite error and equally wrong.

## What stays out

- **A segmentation dependency.** Covered above.
- **Merging the stopword lists.** Covered above; ADR-038's scar.
- **Making `unigrams` a global default.** The two callers that need False need
  it for opposite reasons than the ones that need True, and a single default
  would silently degrade one of them.
- **Extending the guard past `src/`.** Tests legitimately write throwaway
  patterns, and a check that fires on its own fixtures gets suppressed.
- **A lint rule instead of a test.** `ruff` is not in this repo's verify job;
  the hermetic suite is. A guard that only runs where the release check does
  not is a guard that does not run.
- **Backfilling a bench case for this.** The behaviour it protects is already
  measured by the increment axis ADR-049 added — `05-increment-repairs` is a
  Chinese case whose correct first outcome is `already_satisfied`, which is
  reachable only if retrieval works in Chinese. A defect that makes the gate
  inert now shows up as a gate rate, not as a silent pass.

## Mechanism

`src/ai_venture_studio/lexicon.py` — `is_cjk`, `tokenize`, `content`,
`content_length`, and `CJK_FUNCTION` (adopted from the tuned set
`requirements` had grown).

Seven call sites rewired: `textsim.shingles`, `similarity.rank` (which
re-exports `tokenize`, since `from ai_venture_studio.similarity import
tokenize` was a real import path), `maintenance/correlate._tokens`,
`upstream/requirements.tokens`, `product/mvp._stems`,
`marketing/substantiation._content_words`, and `marketing/spam_policy`'s
thin-page count.

`tests/test_one_tokenizer.py` — sixteen tests in three groups: the tokenizer's
own rules (bigrams discriminate, a one-character run still yields its
character, function characters drop alone but not inside a bigram, the Latin
floor never touches CJK, `content_length` counts characters not grams); every
consumer asserted non-inert in both languages, with the English assertion
beside the Chinese one so the pair reads as "and this behaves the same"; and
the structural half — the AST walk, the detector pinned against real patterns,
and an import check on all seven consumers so a site cannot grow its own again
under a name the pattern check does not recognise.
