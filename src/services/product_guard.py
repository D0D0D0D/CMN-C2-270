"""S-3 individual-product-recommendation guard — deterministic text scan.

Legal context: this agent must NOT recommend or solicit specific financial
products (金商法 適合性原則 / robo-advisor 規制). A single leak is a compliance
violation, so the guard is applied in depth (docs/02_design.md §S-3):
  L1 prompt suppression      (in synthesis node system prompt)
  L2 post-generation check   (synthesis node, uses scan_for_product_recommendation)
  L3 final S-3 gate          (post_process node, re-runs this scan, fail-closed)
  L4 disclaimer preservation (post_process node)

fail-closed: on any doubt the caller must reject the output, never pass it.
DETERMINISTIC scan (no randomness / no wall-clock).
"""

from __future__ import annotations

import unicodedata

# Product-name / instrument dictionary. Config-extensible at runtime
# (config `product_name_blocklist` is unioned with this base set).
BASE_PRODUCT_TERMS: tuple[str, ...] = (
    # Fund / product brand fragments (examples; extend via config).
    "eMAXIS",
    "ひふみ",
    "iFree",
    "ニッセイ外国株式",
    "SBI・V",
    "楽天・全米株式",
    "楽天VTI",
    "オルカン",
    # Instrument identifiers.
    "ティッカー",
    "ISIN",
    # Securities firm / provider names (recommending a provider is solicitation).
    "SBI証券",
    "楽天証券",
    "マネックス",
    "野村證券",
    "大和証券",
)

# Recommendation / solicitation verbs & phrases (Japanese).
RECOMMEND_TERMS: tuple[str, ...] = (
    "おすすめ",
    "お勧め",
    "推奨",
    "買うべき",
    "買いましょう",
    "購入しましょう",
    "投資しなさい",
    "投資すべき",
    "がおすすめ",
    "を選ぶべき",
    "に投資して",
    "を購入して",
    "を買って",
)

DISCLAIMER = "※ 本情報は一般的な資産形成の参考であり、" "特定の金融商品を推奨・勧誘するものではありません。"


def scan_for_product_recommendation(
    text: str,
    extra_product_terms: list[str] | None = None,
) -> list[str]:
    """Return a list of violation descriptions; empty list == clean.

    A violation is raised when:
      (a) any concrete product/instrument/provider name appears, OR
      (b) a recommendation/solicitation verb appears.
    Either alone is treated as a violation (fail-closed: we do not require the
    verb and the product name to co-occur, because "buy X" and "X is great"
    are both solicitation in this regulated context).

    The agent's own non-solicitation DISCLAIMER is excluded before scanning —
    it legitimately contains the negated phrase "推奨・勧誘するものではありません"
    and must not trip the guard on itself.
    """
    if not text:
        return []

    # Normalize (NFKC folds half/full-width + case-fold) so orthographic
    # variants — "ｅＭＡＸＩＳ", "emaxis slim", "楽天・全米株式" in half-width —
    # cannot bypass the legal-critical guard. Both text and terms are folded.
    scanned = _normalize(text.replace(DISCLAIMER, " "))

    product_terms = list(BASE_PRODUCT_TERMS)
    if extra_product_terms:
        product_terms.extend(t for t in extra_product_terms if t)

    violations: list[str] = []
    for term in product_terms:
        if term and _normalize(term) in scanned:
            violations.append(f"product/provider name present: {term!r}")
    for term in RECOMMEND_TERMS:
        if _normalize(term) in scanned:
            violations.append(f"recommendation/solicitation phrase present: {term!r}")
    return violations


def _normalize(s: str) -> str:
    """NFKC + case-fold — collapses full/half-width and upper/lower variants."""
    return unicodedata.normalize("NFKC", s).casefold()


def is_clean(text: str, extra_product_terms: list[str] | None = None) -> bool:
    """True only when the text has zero product-recommendation violations."""
    return not scan_for_product_recommendation(text, extra_product_terms)


def has_disclaimer(text: str) -> bool:
    """L4 preservation check — the non-solicitation disclaimer must be present."""
    return DISCLAIMER in (text or "")
