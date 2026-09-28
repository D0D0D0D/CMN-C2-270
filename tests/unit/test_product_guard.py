# CMN-C2-270 — Unit tests: S-3 product-recommendation guard (legal-critical)

from src.services.product_guard import (
    DISCLAIMER,
    has_disclaimer,
    is_clean,
    scan_for_product_recommendation,
)


class TestScanForProductRecommendation:
    def test_clean_general_advice_passes(self):
        text = "一般的には時間分散・資産分散の観点で無理のない計画を検討することが大切です。"
        assert scan_for_product_recommendation(text) == []
        assert is_clean(text)

    def test_product_name_is_blocked(self):
        text = "eMAXIS Slim 全世界株式を検討してはどうでしょう。"
        assert scan_for_product_recommendation(text)
        assert not is_clean(text)

    def test_provider_name_is_blocked(self):
        text = "SBI証券で口座を開くとよいです。"
        assert scan_for_product_recommendation(text)

    def test_recommendation_verb_is_blocked(self):
        text = "この投資信託がおすすめです。"
        assert scan_for_product_recommendation(text)

    def test_solicitation_phrase_is_blocked(self):
        assert scan_for_product_recommendation("今すぐ買うべきです。")

    def test_disclaimer_itself_does_not_trip_guard(self):
        """The non-solicitation disclaimer contains '推奨・勧誘' in negated form and
        must NOT be flagged (regression: guard tripping on its own disclaimer)."""
        assert scan_for_product_recommendation(DISCLAIMER) == []
        assert is_clean(f"一般的な考え方です。\n\n{DISCLAIMER}")

    def test_extra_blocklist_terms(self):
        text = "SocialTrust ファンドはいかがですか。"
        assert scan_for_product_recommendation(text, extra_product_terms=["SocialTrust"])

    def test_empty_text_is_clean(self):
        assert scan_for_product_recommendation("") == []

    def test_lowercase_variant_is_blocked(self):
        """Regression: case-insensitive — 'emaxis' must not bypass the guard."""
        assert scan_for_product_recommendation("emaxis slim をおすすめします。")

    def test_fullwidth_variant_is_blocked(self):
        """Regression: NFKC — full-width 'ＳＢＩ証券' must not bypass the guard."""
        assert scan_for_product_recommendation("ＳＢＩ証券がよいです。")

    def test_halfwidth_katakana_provider_blocked(self):
        """NFKC folds half-width katakana provider fragments."""
        assert scan_for_product_recommendation("ﾏﾈｯｸｽ がおすすめ")


class TestDisclaimer:
    def test_has_disclaimer_true_false(self):
        assert has_disclaimer(f"本文\n\n{DISCLAIMER}")
        assert not has_disclaimer("本文のみ")
