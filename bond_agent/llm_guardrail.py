from __future__ import annotations

import math
import re
from typing import Any

# ASCII hyphen-minus, Unicode minus (U+2212), en-dash, fullwidth hyphen-minus.
# Models often emit "−5.95" (U+2212); treating it as bare 5.95 falsely fails numeric checks.
_SIGN_CLASS = r"[\-\u2212\u2013\uff0d]"
NUMBER_RE = re.compile(rf"(?<![\w.%％]){_SIGN_CLASS}?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?")

# CJK numeral handling: ASCII-only NUMBER_RE misses Chinese numerals, so a
# model can write "收益率约百分之七点八" (yield ≈ 7.8%) and the numeric guardrail
# would extract zero claims → numeric_status="passed" despite no evidence
# support. We normalize common CJK numerals to ASCII before scanning.
# Coverage is deliberately bounded to the common adversary forms
# (十/百/千/万/亿/点 + the 0-9 single-char digits); formal/banking numerals
# (壹颢/¿) are out of scope. See tests for canary coverage.
_CJK_DIGIT = {
    "零": "0", "〇": "0", "一": "1", "二": "2", "两": "2", "三": "3",
    "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
}
_CJK_UNIT = {"亿": "00000000", "万": "0000", "千": "000", "百": "00", "十": "0"}
_CJK_POINT = {"点": ".", "點": "."}


def _normalize_cjk_numerals(text: str) -> str:
    """Normalize CJK numerals to ASCII; conservative by design.

    Only safely-linear forms rewrite cleanly:
      - 七点八 -> 7.8        (digit, point, fraction digits)
      - 百分之七点八 -> 百分之7.8  (leaves 百分之 prefix intact; caller detects prefix)

    Positional-multiplier numerals that contain (十/百/千/万/亿) require
    non-linear interpretation; collapsing 一百零五 to 1005 is wrong.
    We therefore DROP unit-multiplier chars from a digit run so such runs
    collapse into the bare digit sequence (lossy but conservative: the
    guardrail then sees at least one claimed number, and the magnitude
    will still fail to match evidence). For pure 万/亿 runs like 一万,
    the dropped-unit form collapses to 1, which still surfaces a numeric
    claim. See _has_cjk_numerals for the fail-closed canary when even
    that bare-digit extraction cannot produce a claim.
    """
    if not text:
        return text
    if not any(
        ch in _CJK_DIGIT or ch in _CJK_UNIT or ch in _CJK_POINT for ch in text
    ):
        return text

    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _CJK_DIGIT or ch in _CJK_POINT:
            j = i
            arabic: list[str] = []
            seen_digit = False
            # Consume a run of digits + points only. Skip unit multipliers
            # (十/百/千/万/亿): they don't map linearly to ASCII digits, so
            # dropping them is the safe lossy choice (see docstring).
            while j < n and (text[j] in _CJK_DIGIT or text[j] in _CJK_POINT):
                cj = text[j]
                if cj in _CJK_DIGIT:
                    seen_digit = True
                    arabic.append(_CJK_DIGIT[cj])
                else:  # _CJK_POINT
                    arabic.append(_CJK_POINT[cj])
                j += 1
            if seen_digit:
                out.append("".join(arabic))
                i = j
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _has_cjk_numerals(text: str) -> bool:
    """True when the text contains any CJK finance numeral char.

    Used by assess_llm_faithfulness as a canary: if the LLM emits
    Chinese digits but the ASCII scan + our conservative normalizer
    cannot extract them, we treat numeric claims as needs_review (failed)
    rather than running zero-item-claimed, which would falsely mark numeric_status as passed.
    """
    return bool(text) and any(
        ch in _CJK_DIGIT or ch in _CJK_UNIT or ch in _CJK_POINT for ch in text
    )


UNSAFE_LANGUAGE_RULES = [
    # 中文：建议买入/配置/持有。
    ("buy_recommendation", re.compile(r"(建议|推荐|应该|可以|适合|需|要|请|务必|不妨).{0,10}(买入|购买|配置|加仓|补仓|姨底|建仓|投资)")),
    # 中文：建议卖出/调仓/止损。
    ("sell_recommendation", re.compile(r"(建议|推荐|应该|可以|需|要|请|务必).{0,10}(卖出|减仓|清仓|割肉|止损|止盈|出局)")),
    # 中文：保本/稳收/稳赚不赔类。
    ("guaranteed_return", re.compile(r"(保证收益|收益保证|稳赚|稳赚不赔|保本|保本保收益|保收益|稳收益|不会亏|亏不了|包赚|铁定赚)")),
    # 中文：无风险/绝对安全/低风险高收益。
    ("risk_free_claim", re.compile(r"(无风险|零风险|非常安全|绝对安全|放心买|低风险高收益|稳健增值|不必担心|闭眼买)")),
    ("rating_opinion", re.compile(r"(买入评级|卖出评级|强烈推荐|目标价|增持|减持)")),
    # English: buy/sell recommendations (verb-anchored, skip-safe bare nouns).
    ("english_buy_recommendation", re.compile(r"\b(strong buy|buy recommendation|you should buy|safe investment|consider buying|worth buying|add to position|top pick|accumulate|overweight|a buy)(?!\s*-)\b", re.IGNORECASE)),
    ("english_sell_recommendation", re.compile(r"\b(sell recommendation|you should sell|consider selling|underweight|reduce|trim|a sell)(?!\s*-)\b", re.IGNORECASE)),
    # English: guaranteed return / risk-free / can't lose.
    ("english_guarantee", re.compile(r"\b(guaranteed (?:return|yield|profit)|risk[-\s]?free|can'?t lose|no downside)\b", re.IGNORECASE)),
]
# Look-back window for negation before an otherwise-unsafe phrase.
_NEGATION_LOOKBACK = re.compile(
    r"(?is)"
    r"(?:"
    r"\b(?:not|never|without|avoid|denies?|deny|neither|nor)\b|"
    r"\b(?:do|does|did|is|are|was|were|can|could|should|must|will)\s+not\b|"
    r"\bno\b(?!\s+downside\b)|"  # "no risk-free..." OK; keep "no downside" as unsafe phrase itself
    r"并非|不是|绝不|不要|禁止|避免|不得|无(?:买卖|投资建议)"
    r")"
    r"[\w\s\-\"'‘’“”，。、：:（）()]{0,40}$"
)


def assess_llm_faithfulness(text: str | None, report: dict) -> dict:
    if not text:
        return {
            "status": "not_run",
            "numeric_status": "not_run",
            "language_status": "not_run",
            "score": None,
            "used_for_final": False,
            "unsupported_numbers": [],
            "unsafe_phrases": [],
            "supported_number_count": 0,
            "checked_number_count": 0,
            "applicability": "skipped_no_llm_output",
            "summary": "LLM output was not available, so faithfulness checks were not run.",
        }

    evidence_numbers = _extract_evidence_numbers(report)
    extracted_numbers = _extract_text_numbers(text)
    unsafe_phrases = _find_unsafe_phrases(text)
    unsupported = []
    supported_count = 0

    for item in extracted_numbers:
        if _matches_evidence(item, evidence_numbers):
            supported_count += 1
        else:
            unsupported.append(item)

    # CJK-numeral canary: if the LLM emits Asian-finance numerals but our
    # conservative normalizer could not extract them as ASCII claims (e.g. a
    # bare multiplier form like \u4e00\u4e07 without \u70b9), we fail the numeric
    # check rather than silently passing with zero claimed numbers. This closes
    # the \u201c\u6536\u76ca\u7387\u7ea6\u4e00\u4e07\u70b9\u4e94\u201d-style adversary where the only number in the
    # text is written in Chinese. Seeded adversarial numbers don\u2019t map cleanly
    # to ASCII so the canary reblocks them even when _extract_text_numbers
    # produced an empty list.
    if _has_cjk_numerals(text) and not extracted_numbers:
        unsupported.append({"text": "<cjk-numeral>", "value": None, "unit": "number",
                            "decimal_places": 0}
)

    score = max(0, 100 - len(unsupported) * 20 - len(unsafe_phrases) * 30)
    numeric_status = "passed" if not unsupported else "failed"
    language_status = "passed" if not unsafe_phrases else "failed"
    status = "passed" if numeric_status == "passed" and language_status == "passed" else "failed"
    if status == "passed":
        summary = "LLM numeric claims and risk language are supported by guardrails."
    elif unsafe_phrases:
        summary = "LLM output contains investment-advice or overconfident risk language; deterministic report should be used."
    else:
        summary = "LLM output contains numeric claims that are not present in structured evidence; deterministic report should be used."

    return {
        "status": status,
        "numeric_status": numeric_status,
        "language_status": language_status,
        "score": score,
        "used_for_final": status == "passed",
        "unsupported_numbers": unsupported[:10],
        "unsafe_phrases": unsafe_phrases[:10],
        "supported_number_count": supported_count,
        "checked_number_count": len(extracted_numbers),
        "applicability": "checked_llm_output",
        "summary": summary,
    }


def _extract_evidence_numbers(report: dict) -> list[dict]:
    numbers: list[dict] = []
    _walk_evidence(report, [], numbers)
    return numbers


def _find_unsafe_phrases(text: str) -> list[dict]:
    findings = []
    for rule_id, pattern in UNSAFE_LANGUAGE_RULES:
        for match in pattern.finditer(text):
            if _is_negated_claim(text, match):
                # "not risk-free" / "no risk-free conclusion" / "避免无风险表述" are boundary language.
                continue
            findings.append({"rule_id": rule_id, "text": match.group(0)})
    return findings


def _is_negated_claim(text: str, match: re.Match) -> bool:
    """True when the unsafe token is used inside a clear negation / refusal."""
    window = text[max(0, match.start() - 48) : match.start()]
    return bool(_NEGATION_LOOKBACK.search(window))


def _walk_evidence(value: Any, path: list[str], numbers: list[dict]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = [*path, str(key)]
            if isinstance(key, str):
                _append_text_numbers(key, child_path, numbers, source="key")
            _walk_evidence(child, child_path, numbers)
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk_evidence(child, [*path, str(index)], numbers)
        return

    if isinstance(value, bool) or value is None:
        return

    if isinstance(value, int | float) and math.isfinite(float(value)):
        numbers.append({"value": float(value), "unit": _unit_for_numeric_path(path), "path": ".".join(path)})
        return

    if isinstance(value, str):
        _append_text_numbers(value, path, numbers, source="value")


def _append_text_numbers(text: str, path: list[str], numbers: list[dict], source: str) -> None:
    for match in NUMBER_RE.finditer(text):
        token = match.group(0)
        end = match.end()
        # Prefer explicit % / ％ suffix in the source string (e.g. quality notes "7.8%").
        if (end < len(text) and text[end] in {"%", "％"}) or (source == "key" and "yield_distribution" in ".".join(path).lower()):
            unit = "percent"
        else:
            unit = _unit_for_path(path)
        numbers.append(
            {
                "value": _to_float(token),
                "unit": unit,
                "path": ".".join(path),
                "source": source,
            }
        )


_ISO_DATE_RE = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b")


def _extract_text_numbers(text: str) -> list[dict]:
    """Extract numeric claims from LLM text, including CJK numerals.

    Models can defeat ASCII-only numeric extraction by writing fabricated
    numbers in Chinese ("收益率约百分之七点八"). We scan the original text with
    NUMBER_RE and ALSO scan the CJK-normalized projection; matches that
    appear only after normalization are added as genuine claims. Dedup by
    value/unit/decimals so an ASCII-native number is not double-counted.
    """
    items: list[dict] = []
    seen: set[tuple[float, str, int, int]] = set()
    candidates = [text]
    # Only build the CJK projection when the source actually contains CJK
    # numeral chars — otherwise we double the regex cost for nothing.
    if any(ch in _CJK_DIGIT or ch in _CJK_UNIT or ch in _CJK_POINT for ch in text):
        cjk_norm = _normalize_cjk_numerals(text)
        if cjk_norm != text:
            candidates.append(cjk_norm)

    for source in candidates:
        for match in NUMBER_RE.finditer(source):
            if _is_list_marker(source, match):
                continue
            if _is_quantile_label(source, match):
                # "25分位" / "p25" are labels for evidence keys, not free-standing claims.
                continue
            if _is_inside_iso_date(source, match):
                # Fragments of 2026-07-26 are not numeric claims.
                continue

            token = match.group(0)
            start = match.start()
            end = match.end()
            # "%" / "％" right after the token marks a percent claim.
            has_percent_suffix = end < len(source) and source[end] in {"%", "％"}
            # Chinese "百分之/百分率" prefix (in the ORIGINAL text before
            # the number) also marks percent even after CJK normalized.
            pre = text[max(0, start - 4) : start]
            has_cjk_percent_prefix = "百分之" in pre or "百分率" in pre
            unit = "percent" if (has_percent_suffix or has_cjk_percent_prefix) else "number"
            value = _to_float(token)
            decimals = _decimal_places(token)
            # Dedup key: use original-text start position when scanning
            # the original, and its negation when scanning the projection,
            # so an ASCII-native claim is not double-counted across scans.
            anchor = start if source is text else -start
            key = (value, unit, decimals, anchor)
            if key in seen:
                continue
            seen.add(key)
            # Capture a window around the token so _matches_evidence can
            # infer the claim's semantic category (yield / duration / coupon …)
            # from the nearest keyword and reject cross-domain magnitude matches.
            # ±16 chars is short enough that "久期 7.8 年" anchors duration even when
            # the same sentence also mentions "收益率".
            ctx_start = max(0, start - 16)
            ctx_end = min(len(text), end + 16)
            items.append(
                {
                    "text": token + ("%" if unit == "percent" else ""),
                    "value": value,
                    "unit": unit,
                    "decimal_places": decimals,
                    "context": text[ctx_start:ctx_end],
                    # Offset of the token inside `context` — _claim_category
                    # uses this, NOT min(16, len(ctx)), to know where the
                    # prefix ends. When the token sits near the start of the
                    # text (e.g. "收益率 7.8" where 7.8 starts at idx 5), the
                    # prefix is only 5 chars long, not 16.
                    "token_offset": start - ctx_start,
                }
            )
    return items


def _is_inside_iso_date(text: str, match: re.Match) -> bool:
    """True when the number token sits inside an ISO-like calendar date."""
    for date_match in _ISO_DATE_RE.finditer(text):
        if date_match.start() <= match.start() and match.end() <= date_match.end():
            return True
    return False


# Semantic categories used to keep a bare-numeric claim (e.g. 7.8) from being
# laundered by evidence of the same magnitude in a DIFFERENT field domain.
# Yield (7.8%) and duration (7.8 years) overlap heavily in magnitude, so a
# fabricated claim in one domain must not pass by matching real evidence in
# another. See _matches_evidence -> _claim_category / _evidence_category.
_CLAIM_KEYWORDS_BY_CATEGORY = {
    "yield":    ("收益率", "到期收益率", "中位收益率", "票息率", "再投资收益率", "yield", "ytm"),
    "duration": ("久期", "残期", "剩余期限", "年限", "到期年限", "duration", "maturity", "durationyrs"),
    "coupon":   ("票面", "票息", "coupon"),
    "price":    ("净价", "全价", "价格", "收盘价", "price"),
    "face":     ("面值", "face", "par"),
    "dv01":     ("dv01", "基点价值"),
    "spread":   ("利差", "spread", "_bp", "bp", "点子", "与中位"),
    "zscore":   ("z分位", "zscore", "z_score", "z-score", "异常分位"),
    "count":    ("只数", "样本数", "数量", "count"),
    "ratio":    ("覆盖率", "_full_ratio", "coverage_ratio", "fill_ratio"),
}


# Maximum chars between a domain keyword and the claim token for the keyword
# to count as the claim's anchor. Bond-domain keywords virtually always sit
# within 6-8 chars of the number; a keyword 12+ chars away usually belongs to
# a different claim in the same sentence (e.g. coverage ratio numerator).
_CLAIM_KW_MAX_DIST = 9

def _claim_category(item: dict) -> str:
    """Infer a claim's semantic domain from words appearing BEFORE the token.

    Domain keywords like \"久期 / duration / 收益率 / yield\" virtually always
    precede the numeric claim in both Chinese and English bond-domain copy
    (\"久期 7.8 年\", \"modified duration 7.8 yrs\"). We scan only the context
    *before* the token, picking the closest preceding domain keyword, so a
    sentence that mentions two domains (e.g. \"该券久期 7.8 年，中位收益率\")
    still anchors the bare 7.8 to duration rather than yield.

    Returns \"unknown\" when no keyword precedes the claim; _matches_evidence
    then falls back to all number-unit candidates, preserving the historical
    behavior for un-anchored bare-numeric claims.
    """
    ctx = item.get("context") or ""
    if not ctx:
        return "unknown"
    # Context is text[start-12:end+12]; the token starts at offset 12 (or 0
    # when start < 12). We scan only the prefix up to the token start.
    # token_offset is the token's exact index inside ctx (start - ctx_start).
    # Using it avoids the min(16, len(ctx)) heuristic that mis-anchors a token
    # near the start of the sentence ("收益率 7.8" -> anchor=16 -> dist=14 -> unknown).
    anchor = item.get("token_offset")
    if anchor is None:
        anchor = min(16, len(ctx))
    anchor = max(0, min(anchor, len(ctx)))
    prefix_low = ctx[:anchor].lower()
    if not prefix_low:
        return "unknown"
    best_cat = "unknown"
    best_dist = None
    for category, keywords in _CLAIM_KEYWORDS_BY_CATEGORY.items():
        for kw in keywords:
            kw_low = kw.lower()
            idx = prefix_low.rfind(kw_low)  # right-most occurrence == closest to token
            if idx < 0:
                continue
            dist = anchor - idx
            # Only treat a keyword as the anchor when it sits within
            # _CLAIM_KW_MAX_DIST chars of the token. Bond-domain keywords
            # virtually always sit within 6-8 chars of the number; a keyword
            # 12+ chars away usually belongs to a different claim in the same
            # sentence (e.g. coverage ratio numerator 3363 vs 覆盖率 16 chars above).
            if dist > _CLAIM_KW_MAX_DIST:
                continue
            if best_dist is None or dist < best_dist:
                best_cat = category
                best_dist = dist
    return best_cat


def _evidence_category(number: dict) -> str:
    """Map an evidence number's dotted path to a semantic category.

    Uses the PATH LEAF (the last segment after the dot) for the domain check,
    so a container parent like `maturity_coverage` doesn't drag its sub-fields
    (`coverage_ratio`, `filled_count`) into the duration bucket. Falls back to
    "number_generic" for paths we cannot classify — these remain candidates
    for any non-percent bare-numeric claim (legacy behavior).
    """
    path = str(number.get("path") or "").lower()
    if not path:
        return "number_generic"
    leaf = path.rsplit(".", 1)[-1]
    # Path-substring checks are reserved for compound leaf names that do encode
    # the domain (e.g. modified_duration / macaulay_duration / spread_vs_peer*),
    # to avoid false matches on container parents (maturity_coverage.* etc).
    if leaf in {"modified_duration", "macaulay_duration", "durationyrs",
                "maturity", "residual_maturity", "残期", "久期",
                "剩余期限", "到期年限", "年限"}:
        return "duration"
    if leaf == "dv01" or "基点价值" in leaf:
        return "dv01"
    if leaf == "coupon" or "票面" in leaf or "票息" in leaf:
        return "coupon"
    if "spread" in leaf or "_bp" in leaf or "vs_peer" in leaf or "peer_mean" in leaf:
        return "spread"
    if "zscore" in leaf or "z_score" in leaf or "z-score" in leaf:
        return "zscore"
    if "净价" in leaf or "全价" in leaf or "收盘价" in leaf or leaf == "price":
        return "price"
    if "面值" in leaf or leaf in {"face", "par"}:
        return "face"
    if leaf == "ratio" or leaf.endswith("_ratio"):
        return "ratio"
    if leaf in {"count", "sample_count", "row_count", "match_count", "filled_count",
                "missing_count", "unmatched_count", "valid_yield_count",
                "missing_yield_count", "extreme_yield_count", "outlier_count",
                "ledger_item_count"}:
        return "count"
    return "number_generic"


def _matches_evidence(item: dict, evidence_numbers: list[dict]) -> bool:
    if item["unit"] == "percent":
        if abs(item["value"]) > 100:
            return False
        # Percent claims must match percent-tagged evidence only.
        # (Yield fields and explicit "7.8%" notes are tagged percent by unit helpers.)
        candidates = [number for number in evidence_numbers if number["unit"] == "percent"]
    else:
        # Filter by semantic category to prevent cross-domain magnitude laundering.
        # When a claim anchors to a domain keyword (e.g. 收益率 / 久期), only
        # evidence of the SAME category can match. Unknown-context claims keep
        # the LEGACY LOOSE behavior: any evidence number (percent or number unit)
        # is a candidate — this preserves the historical acceptance of bare
        # "均值 2.7709" against yield-summary percent evidence.
        cat = _claim_category(item)
        if cat == "unknown":
            candidates = list(evidence_numbers)
        else:
            # Un-categorized evidence (e.g. numbers embedded in data_quality
            # message strings like "Missing yield on 263/3365") still serves as
            # a truthful supporting source for any categorized claim — it can't
            # carry a cross-domain magnitude attack because the path leaf doesn't
            # encode a numeric domain (it's message text, not a value field).
            number_generic_number = [
                n for n in evidence_numbers
                if _evidence_category(n) == "number_generic" and n["unit"] == "number"
            ]
            same_cat_candidates = [
                n for n in evidence_numbers if _evidence_category(n) == cat
            ]
            # When the claim is clearly yield (percent-domain) but written as a
            # bare numeric without %, percent-unit evidence may be the only
            # real target. Allow a cross-unit match ONLY for yield-category
            # claims, so "收益率7.8" can still be honest-to-evidence 7.8%.
            if cat == "yield":
                same_cat_candidates += [
                    n for n in evidence_numbers if n["unit"] == "percent"
                ]
            if same_cat_candidates:
                candidates = same_cat_candidates + number_generic_number
            else:
                # No evidence filed under the claimed category. Fall back to
                # number_generic + unit=number evidence only — NOT percent
                # evidence — so a duration claim cannot launder through a
                # coincidentally-equal yield value. "久期 7.8" against a report
                # with median:7.8% but no duration field must FAIL.
                candidates = number_generic_number

    claimed = item["value"]
    decimals = item["decimal_places"]
    for number in candidates:
        if _values_match(claimed, number["value"], decimals):
            return True
        # coverage_ratio / *_ratio often stored in 0–1 form but cited as percent
        # (0.9994 → 99.94% or bare 99.94). Only ratio leaves get this scale bridge.
        if _is_ratio_field(number):
            ratio_value = float(number["value"])
            if 0.0 <= abs(ratio_value) <= 1.0:
                scaled = ratio_value * 100.0
                if _values_match(claimed, scaled, decimals):
                    return True
                if item["unit"] != "percent" and _values_match(claimed, abs(scaled), decimals):
                    return True
        # Signed peer-spread / z-score / bp fields are often written without the
        # minus sign ("5.95 bp vs peer mean") while evidence stores -5.95.
        # Allow magnitude-only match for those paths only — inventing the same
        # magnitude without such evidence still fails.
        if item["unit"] != "percent" and _is_signed_magnitude_field(number) and _values_match(
            claimed, abs(float(number["value"])), decimals
        ):
            return True
    # bp fields are unit=number, so the percent-candidate loop never sees them.
    # Models sometimes rewrite -5.95 bp as -0.0595%; match that conversion only
    # for signed magnitude fields, scanning the full evidence set.
    if item["unit"] == "percent":
        for number in evidence_numbers:
            if not _is_signed_magnitude_field(number):
                continue
            bp_value = float(number["value"])
            if _values_match(claimed, bp_value / 100.0, decimals) or _values_match(
                claimed, abs(bp_value) / 100.0, decimals
            ):
                return True
    return False



def _is_signed_magnitude_field(number: dict) -> bool:
    path = str(number.get("path") or "").lower()
    if not path:
        return False
    markers = (
        "spread",
        "_bp",
        "bp.",
        "zscore",
        "z_score",
        "vs_peer",
        "peer_mean",
        "涨跌",
    )
    return any(marker in path for marker in markers)


def _is_ratio_field(number: dict) -> bool:
    """True for 0–1 ratio leaves (coverage_ratio, fill_ratio, …)."""
    path = str(number.get("path") or "")
    if not path:
        return False
    leaf = path.rsplit(".", 1)[-1].lower()
    return leaf == "ratio" or leaf.endswith("_ratio") or leaf.endswith("ratio")


def _values_match(claimed: float, evidence: float, decimal_places: int) -> bool:
    if math.isclose(claimed, evidence, rel_tol=0, abs_tol=1e-9):
        return True

    if decimal_places == 0:
        return math.isclose(claimed, round(evidence), rel_tol=0, abs_tol=1e-9)

    # Accept both banker's rounding and truncation to the claimed precision
    # (e.g. evidence 0.109372 cited as 0.1094 or 0.1093).
    scale = 10 ** decimal_places
    rounded = round(evidence, decimal_places)
    if math.isclose(claimed, rounded, rel_tol=0, abs_tol=10 ** (-decimal_places)):
        return True
    truncated = math.trunc(evidence * scale) / scale
    if math.isclose(claimed, truncated, rel_tol=0, abs_tol=10 ** (-decimal_places)):
        return True
    # Also allow truncation toward zero of the absolute value with original sign.
    abs_truncated = math.copysign(math.trunc(abs(evidence) * scale) / scale, evidence)
    return math.isclose(claimed, abs_truncated, rel_tol=0, abs_tol=10 ** (-decimal_places))


_COUNT_LEAVES = {
    "count",
    "sample_count",
    "row_count",
    "match_count",
    "filled_count",
    "missing_count",
    "unmatched_count",
    "valid_yield_count",
    "missing_yield_count",
    "extreme_yield_count",
    "outlier_count",
    "ledger_item_count",
}


def _unit_for_path(path: list[str]) -> str:
    """Decide unit from the *leaf* field, not parent containers like high_yield."""
    if not path:
        return "number"
    leaf = str(path[-1])
    leaf_l = leaf.lower()

    # Explicit percent markers on the field name (common in CN bond columns).
    if "%" in leaf or "％" in leaf or "收益率" in leaf:
        return "percent"
    if any(word in leaf_l for word in ["yield", "percentile", "percent", "zscore"]):
        return "percent"
    if leaf_l in {"score", "outlier_score"} or leaf_l.endswith("_score") or leaf_l.endswith("ratio"):
        return "percent"
    # yield_summary / weighted_yield_summary stats (except counts)
    if len(path) >= 2 and str(path[-2]).lower() in {"yield_summary", "weighted_yield_summary"}:
        if leaf_l in _COUNT_LEAVES:
            return "number"
        if leaf_l in {"mean", "std", "min", "max", "median", "p25", "p75"}:
            return "percent"
    return "number"


def _unit_for_numeric_path(path: list[str]) -> str:
    lowered = ".".join(path).lower()
    leaf = str(path[-1]).lower() if path else ""
    if leaf in _COUNT_LEAVES:
        return "number"
    if "yield_distribution" in lowered:
        # Bin *counts* are cardinalities, not percents; bin edges live in keys.
        return "number"
    return _unit_for_path(path)


def _is_list_marker(text: str, match: re.Match) -> bool:
    start = match.start()
    end = match.end()
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start].strip()
    next_char = text[end : end + 1]
    return not prefix and next_char in {".", "、"} and abs(_to_float(match.group(0))) <= 20


def _is_quantile_label(text: str, match: re.Match) -> bool:
    """Ignore harmless quantile label tokens like 25分位 / p25 / 75th.

    Evidence stores keys such as p25/p75. Models often write "25分位" or "p25="
    as labels; the bare 25/75 is not a fabricated market statistic.
    Fabricated *values* after the label are still checked separately.
    """
    token = match.group(0)
    if token.replace(",", "") not in {"25", "75"}:
        return False

    start = match.start()
    end = match.end()
    before = text[max(0, start - 2) : start].lower()
    after = text[end : end + 8].lower()

    # p25 / P75 as field labels
    if before.endswith("p") or before.endswith("P"):
        return True
    # 25分位 / 75分位 / 25th percentile / 75th
    # "25%" as a bare percentile-rank claim is NOT ignored (no label suffix)
    return (
        after.startswith("分位")
        or after.startswith("th")
        or after.startswith("st")
        or after.startswith("nd")
        or after.startswith("rd")
    )


def _normalize_number_token(token: str) -> str:
    cleaned = token.replace(",", "")
    for ch in ("\u2212", "\u2013", "\uff0d"):
        cleaned = cleaned.replace(ch, "-")
    return cleaned


def _to_float(token: str) -> float:
    return float(_normalize_number_token(token))


def _decimal_places(token: str) -> int:
    normalized = _normalize_number_token(token).lstrip("-")
    if "." not in normalized:
        return 0
    return len(normalized.rsplit(".", 1)[1])
