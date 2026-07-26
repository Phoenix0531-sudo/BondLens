from __future__ import annotations

import math
import re
from typing import Any

# ASCII hyphen-minus, Unicode minus (U+2212), en-dash, fullwidth hyphen-minus.
# Models often emit "−5.95" (U+2212); treating it as bare 5.95 falsely fails numeric checks.
_SIGN_CLASS = r"[\-\u2212\u2013\uff0d]"
NUMBER_RE = re.compile(rf"(?<![\w.%％]){_SIGN_CLASS}?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?")
UNSAFE_LANGUAGE_RULES = [
    ("buy_recommendation", re.compile(r"(建议|推荐|应该|可以|适合).{0,10}(买入|购买|配置|加仓|投资)")),
    ("sell_recommendation", re.compile(r"(建议|推荐|应该|可以).{0,10}(卖出|减仓|清仓)")),
    ("guaranteed_return", re.compile(r"(保证收益|收益保证|稳赚|保本保收益|不会亏)")),
    ("risk_free_claim", re.compile(r"(无风险|非常安全|绝对安全|放心买|低风险高收益)")),
    ("rating_opinion", re.compile(r"(买入评级|卖出评级|强烈推荐|目标价)")),
    ("english_buy_recommendation", re.compile(r"\b(strong buy|buy recommendation|you should buy|safe investment)\b", re.IGNORECASE)),
    ("english_guarantee", re.compile(r"\b(guaranteed return|risk-free|no downside)\b", re.IGNORECASE)),
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


def _extract_text_numbers(text: str) -> list[dict]:
    items = []
    for match in NUMBER_RE.finditer(text):
        if _is_list_marker(text, match):
            continue
        if _is_quantile_label(text, match):
            # "25分位" / "p25" are labels for evidence keys, not free-standing claims.
            continue

        token = match.group(0)
        end = match.end()
        unit = "percent" if end < len(text) and text[end] in {"%", "％"} else "number"
        items.append(
            {
                "text": token + ("%" if unit == "percent" else ""),
                "value": _to_float(token),
                "unit": unit,
                "decimal_places": _decimal_places(token),
            }
        )
    return items


def _matches_evidence(item: dict, evidence_numbers: list[dict]) -> bool:
    if item["unit"] == "percent":
        if abs(item["value"]) > 100:
            return False
        # Percent claims must match percent-tagged evidence only.
        # (Yield fields and explicit "7.8%" notes are tagged percent by unit helpers.)
        candidates = [number for number in evidence_numbers if number["unit"] == "percent"]
    else:
        candidates = evidence_numbers

    claimed = item["value"]
    decimals = item["decimal_places"]
    for number in candidates:
        if _values_match(claimed, number["value"], decimals):
            return True
        # Signed peer-spread / z-score / bp fields are often written without the
        # minus sign ("5.95 bp vs peer mean") while evidence stores -5.95.
        # Allow magnitude-only match for those paths only — inventing the same
        # magnitude without such evidence still fails.
        if item["unit"] != "percent" and _is_signed_magnitude_field(number) and _values_match(
            claimed, abs(float(number["value"])), decimals
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


def _values_match(claimed: float, evidence: float, decimal_places: int) -> bool:
    if math.isclose(claimed, evidence, rel_tol=0, abs_tol=1e-9):
        return True

    if decimal_places == 0:
        return math.isclose(claimed, round(evidence), rel_tol=0, abs_tol=1e-9)

    return math.isclose(claimed, round(evidence, decimal_places), rel_tol=0, abs_tol=10 ** (-decimal_places))


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
