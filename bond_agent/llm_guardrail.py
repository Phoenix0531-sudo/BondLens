from __future__ import annotations

import math
import re
from typing import Any

NUMBER_RE = re.compile(r"(?<![\w.%％])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
UNSAFE_LANGUAGE_RULES = [
    ("buy_recommendation", re.compile(r"(建议|推荐|应该|可以|适合).{0,10}(买入|购买|配置|加仓|投资)")),
    ("sell_recommendation", re.compile(r"(建议|推荐|应该|可以).{0,10}(卖出|减仓|清仓)")),
    ("guaranteed_return", re.compile(r"(保证收益|收益保证|稳赚|保本保收益|不会亏)")),
    ("risk_free_claim", re.compile(r"(无风险|非常安全|绝对安全|放心买|低风险高收益)")),
    ("rating_opinion", re.compile(r"(买入评级|卖出评级|强烈推荐|目标价)")),
    ("english_buy_recommendation", re.compile(r"\b(strong buy|buy recommendation|you should buy|safe investment)\b", re.IGNORECASE)),
    ("english_guarantee", re.compile(r"\b(guaranteed return|risk-free|no downside)\b", re.IGNORECASE)),
]


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
            findings.append({"rule_id": rule_id, "text": match.group(0)})
    return findings


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
        unit = "percent" if source == "key" and "yield_distribution" in ".".join(path).lower() else _unit_for_path(path)
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
        candidates = [number for number in evidence_numbers if number["unit"] == "percent"]
        if abs(item["value"]) > 100:
            return False
    else:
        candidates = evidence_numbers

    return any(_values_match(item["value"], number["value"], item["decimal_places"]) for number in candidates)


def _values_match(claimed: float, evidence: float, decimal_places: int) -> bool:
    if math.isclose(claimed, evidence, rel_tol=0, abs_tol=1e-9):
        return True

    if decimal_places == 0:
        return math.isclose(claimed, round(evidence), rel_tol=0, abs_tol=1e-9)

    return math.isclose(claimed, round(evidence, decimal_places), rel_tol=0, abs_tol=10 ** (-decimal_places))


def _unit_for_path(path: list[str]) -> str:
    lowered = ".".join(path).lower()
    if any(word in lowered for word in ["yield", "percentile", "percent", "score", "zscore"]):
        return "percent"
    return "number"


def _unit_for_numeric_path(path: list[str]) -> str:
    lowered = ".".join(path).lower()
    if "yield_distribution" in lowered:
        return "number"
    return _unit_for_path(path)


def _is_list_marker(text: str, match: re.Match) -> bool:
    start = match.start()
    end = match.end()
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start].strip()
    next_char = text[end : end + 1]
    return not prefix and next_char in {".", "、"} and abs(_to_float(match.group(0))) <= 20


def _to_float(token: str) -> float:
    return float(token.replace(",", ""))


def _decimal_places(token: str) -> int:
    normalized = token.replace(",", "")
    if "." not in normalized:
        return 0
    return len(normalized.rsplit(".", 1)[1])
