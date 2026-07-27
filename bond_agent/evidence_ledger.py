from __future__ import annotations

from typing import Any


def build_evidence_ledger(
    plan: dict,
    report: dict,
    data_source: dict,
    evidence_quality: dict,
    llm_guardrail: dict,
    final_answer_source: str,
) -> list[dict[str, Any]]:
    evidence = report.get("data_evidence", {})
    ledger: list[dict[str, Any]] = []

    _append(
        ledger,
        claim_id="data_source",
        claim_zh="本次回答使用了明确的数据源画像。",
        claim_en="This answer used an explicit data source profile.",
        evidence_zh=(
            f"{data_source.get('source_name')}，运行模式 {data_source.get('runtime_mode')}，"
            f"样本 {data_source.get('row_count')} 行，有效收益率 {data_source.get('valid_yield_count')} 条。"
        ),
        evidence_en=(
            f"{data_source.get('source_name')}, runtime mode {data_source.get('runtime_mode')}, "
            f"{data_source.get('row_count')} rows, {data_source.get('valid_yield_count')} valid yield records."
        ),
        source=data_source.get("source_name"),
        tool="data_source",
        confidence=_source_confidence(data_source),
        limitations=data_source.get("limitations", []),
        fields=["source_name", "runtime_mode", "row_count", "valid_yield_count"],
    )

    maturity_coverage = data_source.get("maturity_coverage") or {}
    if maturity_coverage:
        ratio = _percent(maturity_coverage.get("coverage_ratio"))
        _append(
            ledger,
            claim_id="maturity_coverage",
            claim_zh="待偿期字段有覆盖率记录，补全来源可追踪。",
            claim_en="Maturity coverage is tracked and enrichment sources are visible.",
            evidence_zh=(
                f"覆盖率 {ratio}，已填充 {maturity_coverage.get('filled_count')} 条，"
                f"缺失 {maturity_coverage.get('missing_count')} 条。"
            ),
            evidence_en=(
                f"Coverage {ratio}, {maturity_coverage.get('filled_count')} filled, "
                f"{maturity_coverage.get('missing_count')} missing."
            ),
            source=data_source.get("source_name"),
            tool="data_source",
            confidence="medium" if (maturity_coverage.get("coverage_ratio") or 0) >= 0.5 else "low",
            limitations=["Live AkShare bond_spot_deal does not provide native maturity."],
            fields=["maturity_coverage", "待偿期", "待偿期(年)", "待偿期来源"],
        )

    market = evidence.get("market") or {}
    summary = market.get("yield_summary") or {}
    if summary:
        _append(
            ledger,
            claim_id="market_yield_distribution",
            claim_zh="市场概览来自当前样本的收益率统计。",
            claim_en="The market overview comes from yield statistics over the active sample.",
            evidence_zh=(
                f"样本 {market.get('sample_count')} 条；收益率中位数 {summary.get('median')}%，"
                f"均值 {summary.get('mean')}%，区间 {summary.get('min')}%-{summary.get('max')}%。"
            ),
            evidence_en=(
                f"{market.get('sample_count')} records; median yield {summary.get('median')}%, "
                f"mean {summary.get('mean')}%, range {summary.get('min')}%-{summary.get('max')}%."
            ),
            source=data_source.get("source_name"),
            tool="describe_market",
            confidence=evidence_quality.get("level", "medium"),
            limitations=["Issuer credit context and macro curve data are not attached."],
            fields=["yield_summary", "sample_count"],
        )

    search = evidence.get("search") or {}
    if search:
        _append(
            ledger,
            claim_id="search_result",
            claim_zh="债券检索只使用当前数据源中的命中记录。",
            claim_en="Bond search only uses matched records from the active data source.",
            evidence_zh=f"检索条件 {search.get('criteria')}，命中 {search.get('match_count', 0)} 条。",
            evidence_en=f"Criteria {search.get('criteria')}, {search.get('match_count', 0)} matches.",
            source=data_source.get("source_name"),
            tool="search_bonds",
            confidence="high" if search.get("match_count", 0) > 0 else "medium",
            limitations=["A zero-match result does not imply the bond does not exist outside the active source."],
            fields=["criteria", "match_count", "records"],
        )

    comparison = evidence.get("comparison") or {}
    if comparison.get("found"):
        _append(
            ledger,
            claim_id="bond_vs_market",
            claim_zh="命中债券已与当前市场样本做相对位置比较。",
            claim_en="The matched bond was compared against the active market sample.",
            evidence_zh=(
                f"{comparison.get('bond_name')}：收益率分位 {comparison.get('yield_percentile')}%，"
                f"成交量分位 {comparison.get('volume_percentile')}%，"
                f"期限分位 {comparison.get('maturity_percentile')}%，"
                f"收益率异常={comparison.get('is_yield_outlier')}。"
            ),
            evidence_en=(
                f"{comparison.get('bond_name')}: yield percentile {comparison.get('yield_percentile')}%, "
                f"volume percentile {comparison.get('volume_percentile')}%, "
                f"maturity percentile {comparison.get('maturity_percentile')}%, "
                f"yield outlier={comparison.get('is_yield_outlier')}."
            ),
            source=data_source.get("source_name"),
            tool="compare_bond_to_market",
            confidence="high",
            limitations=["Percentiles describe the active sample only, not the full investable universe."],
            fields=["yield_percentile", "volume_percentile", "maturity_percentile", "is_yield_outlier"],
        )

    ranking = evidence.get("ranking") or {}
    records = ranking.get("records") or []
    if records:
        first = records[0]
        _append(
            ledger,
            claim_id="ranking_top_record",
            claim_zh="排序结果来自当前样本字段，不是投资推荐。",
            claim_en="The ranking result comes from active sample fields and is not an investment recommendation.",
            evidence_zh=(
                f"按 {ranking.get('rank_by')} 排序，首位 {first.get('债券简称')}，"
                f"收益率 {first.get('收盘到期收益率(%)')}%，成交量 {first.get('交易量(亿元)')} 亿元。"
            ),
            evidence_en=(
                f"Ranked by {ranking.get('rank_by')}; top record {first.get('债券简称')}, "
                f"yield {first.get('收盘到期收益率(%)')}%, volume {first.get('交易量(亿元)')} bn CNY."
            ),
            source=data_source.get("source_name"),
            tool="rank_bonds",
            confidence="medium",
            limitations=["Ranking is descriptive and does not account for issuer credit or tradability."],
            fields=["rank_by", "records"],
        )

    outliers = evidence.get("outliers") or {}
    if outliers:
        _append(
            ledger,
            claim_id="yield_outliers",
            claim_zh="收益率异常检测使用固定阈值规则。",
            claim_en="Yield outlier detection uses a fixed-threshold rule.",
            evidence_zh=f"方法 {outliers.get('metadata', {}).get('method')}，异常样本 {outliers.get('outlier_count', 0)} 条。",
            evidence_en=f"Method {outliers.get('metadata', {}).get('method')}, {outliers.get('outlier_count', 0)} outliers.",
            source=data_source.get("source_name"),
            tool="detect_yield_outliers",
            confidence="medium",
            limitations=["Outliers require issuer, liquidity, and data-quality review before interpretation."],
            fields=["metadata", "outlier_count", "records"],
        )

    _append(
        ledger,
        claim_id="answer_safety",
        claim_zh="最终答案经过安全边界选择。",
        claim_en="The final answer was selected through a safety boundary.",
        evidence_zh=(
            f"最终来源 {final_answer_source}；LLM 护栏状态 {llm_guardrail.get('status')}；"
            f"证据评分 {evidence_quality.get('score')}/100。"
        ),
        evidence_en=(
            f"Final source {final_answer_source}; LLM guardrail {llm_guardrail.get('status')}; "
            f"evidence score {evidence_quality.get('score')}/100."
        ),
        source="BondLens response contract",
        tool="answer_selection",
        confidence=evidence_quality.get("level", "medium"),
        limitations=evidence_quality.get("penalties", []),
        fields=["final_answer_source", "llm_guardrail", "evidence_quality"],
    )

    return ledger


def _append(
    ledger: list[dict[str, Any]],
    *,
    claim_id: str,
    claim_zh: str,
    claim_en: str,
    evidence_zh: str,
    evidence_en: str,
    source: str | None,
    tool: str,
    confidence: str,
    limitations: list[str],
    fields: list[str],
) -> None:
    ledger.append(
        {
            "id": claim_id,
            "claim_zh": claim_zh,
            "claim_en": claim_en,
            "evidence_zh": evidence_zh,
            "evidence_en": evidence_en,
            "source": source or "active data source",
            "tool": tool,
            "confidence": confidence,
            "limitations": limitations,
            "fields": fields,
        }
    )


def _source_confidence(data_source: dict) -> str:
    if data_source.get("runtime_mode") == "live":
        return "high"
    if data_source.get("runtime_mode") == "live_snapshot":
        return "medium"
    return "medium"


def _percent(value: object) -> str:
    if value is None:
        return "N/A"
    return f"{round(float(value) * 100, 1)}%"
