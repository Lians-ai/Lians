"""
BM25 tokenizer: unicode-aware word extraction + bigram fallback for
unsegmented scripts.

The original tokenizer was a naive str.split(): punctuation glued onto
tokens ("revenue." never matched "revenue"), and whole CJK/Thai sentences
collapsed into a single "word" so lexical scoring was impossible for those
languages. These tests pin the fixed behavior.
"""
from __future__ import annotations

from lians.ranking import _bm25_score, _bm25_tokens


# ── Tokenization ──────────────────────────────────────────────────────────────

def test_punctuation_never_glues_onto_tokens():
    assert _bm25_tokens("Total revenue.") == ["total", "revenue"]
    assert _bm25_tokens("[LANGTOK_ar_1a2b3c]") == ["langtok_ar_1a2b3c"]
    # "raised" light-stems to "rais" — crude, but query and content pass
    # through the same stemmer, so conjugation variants meet at one form.
    assert _bm25_tokens("guidance: $9B (raised)") == ["guidance", "9b", "rais"]


def test_inflections_stem_to_a_common_form():
    assert _bm25_tokens("attending") == _bm25_tokens("attended") == ["attend"]
    assert _bm25_tokens("studies") == _bm25_tokens("studied") == ["study"]
    assert _bm25_tokens("planning") == ["plan"]
    # short and non-Latin tokens pass through unstemmed
    assert _bm25_tokens("was") == ["was"]
    assert _bm25_tokens("акции") == ["акции"]


def test_unicode_words_tokenize_per_word():
    assert _bm25_tokens("Клиент предпочитает акции") == ["клиент", "предпочитает", "акции"]
    assert "χαρτοφυλακίου" in _bm25_tokens("αναφορά χαρτοφυλακίου")
    assert "الشريعة" in _bm25_tokens("مع الشريعة الإسلامية")


def test_unsegmented_scripts_become_bigrams():
    assert _bm25_tokens("止损位") == ["止损", "损位"]
    assert _bm25_tokens("가나다") == ["가나", "나다"]
    # single unsegmented char stays a unigram
    assert _bm25_tokens("株") == ["株"]


def test_mixed_latin_and_cjk_in_one_run():
    # \w+ keeps "q3四半期" as one run; the tokenizer must split the scripts apart
    toks = _bm25_tokens("Q3四半期")
    assert "q3" in toks and "四半" in toks and "半期" in toks


# ── Scoring ───────────────────────────────────────────────────────────────────

def test_exact_token_query_matches_bracketed_content():
    assert _bm25_score("LANGTOK_zh_abc123", "客户的止损位 [LANGTOK_zh_abc123]") > 0


def test_cjk_query_matches_cjk_content():
    assert _bm25_score("止损位", "客户的止损位设定在每股一百二十三点四五美元") > 0
    assert _bm25_score("リスク許容度", "顧客のリスク許容度は保守的。") > 0
    assert _bm25_score("ไตรมาส", "ปรับสมดุลพอร์ตทุกไตรมาสโดยอัตโนมัติ") > 0


def test_no_cross_language_false_positives():
    assert _bm25_score("unrelated words", "客户的止损位") == 0.0


def test_empty_inputs_are_safe():
    assert _bm25_score("", "content") == 0.0
    assert _bm25_score("query", "") == 0.0
    assert _bm25_score("", "") == 0.0
