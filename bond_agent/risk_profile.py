from __future__ import annotations

from typing import Any

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}


def build_risk_profile(report: dict, data_source: dict, evidence_quality: dict, llm_guardrail: dict) -> dict[str, Any]:
    evidence = report.get("data_evidence", {})
    comparison = evidence.get("comparison") or {}
    outliers = evidence.get("outliers") or {}
    maturity_coverage = data_source.get("maturity_coverage") or {}

    cards = [
        _data_quality_card(data_source, evidence_quality, maturity_coverage),
        _credit_context_card(),
        _liquidity_card(comparison),
        _duration_card(comparison, maturity_coverage),
        _outlier_card(comparison, outliers),
        _model_card(llm_guardrail),
    ]
    overall = max(cards, key=lambda item: SEVERITY_ORDER.get(item["severity"], 0))["severity"]
    return {
        "overall_level": overall,
        "cards": cards,
        "summary_zh": _summary_zh(overall),
        "summary_en": _summary_en(overall),
    }


def _data_quality_card(data_source: dict, evidence_quality: dict, maturity_coverage: dict) -> dict[str, Any]:
    ratio = maturity_coverage.get("coverage_ratio")
    if evidence_quality.get("level") == "low" or (ratio is not None and ratio < 0.5):
        severity = "high"
    elif data_source.get("runtime_mode") in {"static_sample", "static_fallback"}:
        severity = "medium"
    else:
        severity = "low"
    return _card(
        "data_quality",
        "数据质量风险",
        "Data Quality Risk",
        severity,
        f"当前数据源 {data_source.get('source_name')}，运行模式 {data_source.get('runtime_mode')}，证据评分 {evidence_quality.get('score')}/100。",
        f"Active source {data_source.get('source_name')}, runtime mode {data_source.get('runtime_mode')}, evidence score {evidence_quality.get('score')}/100.",
        f"待偿期覆盖率 {_percent(ratio)}；有效收益率 {data_source.get('valid_yield_count')} 条。",
        f"Maturity coverage {_percent(ratio)}; {data_source.get('valid_yield_count')} valid yield records.",
        "需要更完整的证券主数据、评级、发行人信息和曲线数据。",
        "Full security master data, ratings, issuer information, and curve data are still needed.",
    )


def _credit_context_card() -> dict[str, Any]:
    return _card(
        "credit_context",
        "信用上下文风险",
        "Credit Context Risk",
        "medium",
        "当前行情源不包含主体评级、财务报表、担保条款或信用事件。",
        "The active market feed does not include issuer ratings, financials, guarantees, or credit events.",
        "系统只做行情和样本内相对分析，不直接给信用结论。",
        "The system performs market and sample-relative analysis only, not credit opinions.",
        "若要做信用判断，需要接入评级、主体、公告和事件数据。",
        "Credit interpretation requires ratings, issuer profiles, announcements, and event data.",
    )


def _liquidity_card(comparison: dict) -> dict[str, Any]:
    percentile = comparison.get("volume_percentile")
    severity = "medium"
    if percentile is not None:
        severity = "high" if percentile < 20 else "medium" if percentile < 40 else "low"
    return _card(
        "liquidity",
        "流动性风险",
        "Liquidity Risk",
        severity,
        f"成交量分位数：{percentile if percentile is not None else 'N/A'}%。",
        f"Volume percentile: {percentile if percentile is not None else 'N/A'}%.",
        comparison.get("nearest_market_context") or "本次未命中单券成交量相对位置。",
        comparison.get("nearest_market_context") or "No bond-specific volume context was available for this run.",
        "低成交量可能带来价差扩大和执行难度，排序结果不等于可交易性。",
        "Low volume can imply wider spreads and harder execution; rankings do not equal tradability.",
    )


def _duration_card(comparison: dict, maturity_coverage: dict) -> dict[str, Any]:
    percentile = comparison.get("maturity_percentile")
    ratio = maturity_coverage.get("coverage_ratio")
    severity = "medium"
    if ratio is not None and ratio < 0.5:
        severity = "high"
    elif percentile is not None:
        severity = "medium" if percentile >= 75 else "low"
    return _card(
        "duration",
        "久期/利率敏感性风险",
        "Duration and Rate-Sensitivity Risk",
        severity,
        f"期限分位数：{percentile if percentile is not None else 'N/A'}%；期限覆盖率 {_percent(ratio)}。",
        f"Maturity percentile: {percentile if percentile is not None else 'N/A'}%; maturity coverage {_percent(ratio)}.",
        "长期债券通常对利率变化更敏感，收益率比较应放在相近期限区间内。",
        "Longer bonds are usually more sensitive to rate changes; yield comparisons are cleaner within similar maturity buckets.",
        "下一步可接入收益率曲线或久期字段，让利率风险解释更精确。",
        "Curve data or duration fields would make the rate-risk explanation more precise.",
    )


def _outlier_card(comparison: dict, outliers: dict) -> dict[str, Any]:
    is_target_outlier = comparison.get("is_yield_outlier")
    outlier_count = outliers.get("outlier_count", 0)
    severity = "high" if is_target_outlier else "medium" if outlier_count else "low"
    return _card(
        "yield_outlier",
        "收益率异常风险",
        "Yield Outlier Risk",
        severity,
        f"目标债券异常：{is_target_outlier if is_target_outlier is not None else 'N/A'}；样本异常数量：{outlier_count}。",
        f"Target outlier: {is_target_outlier if is_target_outlier is not None else 'N/A'}; sample outliers: {outlier_count}.",
        "异常收益率可能来自真实风险、陈旧报价、数据质量问题或债券类型差异。",
        "Yield outliers may reflect real risk, stale pricing, data quality issues, or bond-type differences.",
        "异常样本应触发复核，而不是直接交易动作。",
        "Outliers should trigger review, not direct trading action.",
    )


def _model_card(llm_guardrail: dict) -> dict[str, Any]:
    status = llm_guardrail.get("status")
    severity = "high" if status == "failed" else "low" if status == "passed" else "medium"
    return _card(
        "model",
        "模型输出风险",
        "Model Output Risk",
        severity,
        f"LLM 护栏状态：{status}；数字检查：{llm_guardrail.get('numeric_status')}；语言检查：{llm_guardrail.get('language_status')}。",
        f"LLM guardrail: {status}; numeric check: {llm_guardrail.get('numeric_status')}; language check: {llm_guardrail.get('language_status')}.",
        "最终答案只有在 LLM 输出通过检查时才使用模型文本。",
        "The final answer uses model text only when the LLM output passes checks.",
        "本地小模型适合冒烟测试，不应被当作金融判断来源。",
        "Small local models are useful for smoke tests, not as a source of financial judgment.",
    )


def _card(
    card_id: str,
    title_zh: str,
    title_en: str,
    severity: str,
    signal_zh: str,
    signal_en: str,
    evidence_zh: str,
    evidence_en: str,
    action_boundary_zh: str,
    action_boundary_en: str,
) -> dict[str, str]:
    return {
        "id": card_id,
        "title_zh": title_zh,
        "title_en": title_en,
        "severity": severity,
        "signal_zh": signal_zh,
        "signal_en": signal_en,
        "evidence_zh": evidence_zh,
        "evidence_en": evidence_en,
        "action_boundary_zh": action_boundary_zh,
        "action_boundary_en": action_boundary_en,
    }


def _summary_zh(level: str) -> str:
    if level == "high":
        return "本次分析存在高优先级风险信号，应先补充数据或复核异常。"
    if level == "medium":
        return "本次分析适合研究展示，但仍缺少信用和曲线上下文。"
    return "本次分析没有触发高优先级风险信号，但仍不构成投资建议。"


def _summary_en(level: str) -> str:
    if level == "high":
        return "This run contains high-priority risk signals and should be reviewed with more data."
    if level == "medium":
        return "This run is suitable for research demonstration, but credit and curve context are still missing."
    return "No high-priority risk signal was triggered, but the output is still not investment advice."


def _percent(value: object) -> str:
    if value is None:
        return "N/A"
    return f"{round(float(value) * 100, 1)}%"
