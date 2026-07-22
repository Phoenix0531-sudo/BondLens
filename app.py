from __future__ import annotations

import os

from flask import Flask, jsonify, redirect, render_template, request, url_for
from pydantic import ValidationError

from bond_agent import BondAnalystAgent
from bond_agent.replay_store import list_replays
from bond_agent.schemas import AgentQueryRequest, ApiError, HealthResponse, api_schema_bundle


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
DATA_MODES = {"auto", "live", "static"}
LANGUAGES = {"zh", "en"}

INTENT_LABELS = {
    "bond_report": {"zh": "单券分析", "en": "Bond report"},
    "bond_search": {"zh": "债券筛选", "en": "Bond search"},
    "market_overview": {"zh": "市场概览", "en": "Market overview"},
    "ranking": {"zh": "排序分析", "en": "Ranking"},
    "outlier_detection": {"zh": "异常检测", "en": "Outlier detection"},
}

TOOL_LABELS = {
    "data_source": {"zh": "数据源解析", "en": "Data source resolver"},
    "search_bonds": {"zh": "债券检索", "en": "Bond search"},
    "compare_bond_to_market": {"zh": "单券对比市场", "en": "Bond vs market"},
    "describe_market": {"zh": "市场概览", "en": "Market overview"},
    "rank_bonds": {"zh": "债券排序", "en": "Bond ranking"},
    "detect_yield_outliers": {"zh": "收益率异常检测", "en": "Yield outlier detection"},
    "generate_bond_report": {"zh": "生成分析报告", "en": "Report composition"},
    "answer_selection": {"zh": "答案选择", "en": "Answer selection"},
}

RISK_TRANSLATIONS = {
    "yield_risk": {
        "title": "收益率是风险信号，不是投资建议",
        "summary": "较高收益率通常是在补偿信用风险、流动性风险、久期暴露或定价不确定性。",
        "watch_points": ["应与相近期限债券比较收益率。", "把高收益样本视为需要核查的信号，而不是直接机会。"],
    },
    "liquidity_risk": {
        "title": "成交量是流动性代理指标",
        "summary": "低成交量可能意味着买卖价差更宽、执行更困难；样本内看起来有吸引力的债券也可能不易交易。",
        "watch_points": ["结合市场样本比较成交量分位数。", "把低成交量排名视为流动性提醒，而不是交易机会。"],
    },
    "duration_risk": {
        "title": "更长期限会提高利率敏感性",
        "summary": "长期债券通常对利率变化更敏感；收益率比较在期限区间相近时更有意义。",
        "watch_points": ["比较收益率前先看期限分位数。", "区分短期限存单、长期国债和政策性金融债等不同类型。"],
    },
    "outlier_risk": {
        "title": "收益率异常需要结合数据与信用核查",
        "summary": "收益率异常可能来自真实风险、陈旧报价、数据质量问题或债券类型差异，应触发复核而不是直接行动。",
        "watch_points": ["先检查命中的债券记录。", "判断异常来自收益率、期限、成交量还是缺失上下文。"],
    },
    "credit_risk": {
        "title": "信用上下文不在当前行情源内",
        "summary": "当前行情源不包含主体评级、财务报表、担保或信用事件，因此信用结论必须保持克制。",
        "watch_points": ["不要只根据收益率推断评级。", "做信用判断前应补充主体、评级和事件数据。"],
    },
    "data_boundary": {
        "title": "数据覆盖范围限制决策置信度",
        "summary": "Agent 可使用 AkShare 实时债券数据和本地 Excel 备用样本；每个回答都应说明当前数据源，并避免超出字段范围的结论。",
        "watch_points": ["讨论时效性前先检查 data_source。", "做信用或投资判断前应补充主体、评级、曲线和新闻数据。"],
    },
}


@app.route("/")
def index():
    return redirect(url_for("agent_page"))


@app.context_processor
def inject_language_context():
    return {"current_lang": _resolve_language(request.values.get("lang"))}


@app.route("/healthz")
def healthz():
    response = HealthResponse(status="ok", service="BondLens AI", checks={"app": "ok"})
    return jsonify(response.model_dump(mode="json"))


@app.route("/agent", methods=["GET", "POST"])
def agent_page():
    result = None
    view = None
    question = ""
    form_error = None
    lang = _resolve_language(request.values.get("lang"))
    data_mode, form_error = _resolve_page_data_mode(request.values.get("data_mode", os.environ.get("BOND_DATA_MODE", "auto")))
    if request.method == "POST":
        question = request.form.get("question", "").strip()
        result = BondAnalystAgent(data_mode=data_mode).answer(question)
        view = _build_agent_view_model(result, lang=lang)
    return render_template(
        "agent.html",
        result=result,
        view=view,
        question=question,
        data_mode=data_mode,
        form_error=form_error,
        lang=lang,
    )


@app.route("/api/agent/query", methods=["POST"])
def agent_query():
    payload = request.get_json(silent=True) or {}
    try:
        query = AgentQueryRequest.model_validate(payload) if payload else AgentQueryRequest(question=request.form.get("question", ""))
    except ValidationError as exc:
        return jsonify(ApiError(error="Invalid agent query request.", details=exc.errors()).model_dump(mode="json")), 400
    question = query.question or request.form.get("question", "")
    try:
        data_mode = _normalize_data_mode(query.data_mode or request.form.get("data_mode") or os.environ.get("BOND_DATA_MODE", "auto"))
    except ValueError as exc:
        error = ApiError(error=str(exc), allowed_data_modes=sorted(DATA_MODES))
        return jsonify(error.model_dump(mode="json", exclude_none=True)), 400
    result = BondAnalystAgent(data_mode=data_mode).answer(question)
    return jsonify(result)


@app.route("/api/agent/schema")
def agent_schema():
    return jsonify(api_schema_bundle())


@app.route("/replay")
def replay_page():
    lang = _resolve_language(request.values.get("lang"))
    replays = [_build_replay_view(record, lang) for record in list_replays()]
    return render_template("replay.html", replays=replays, lang=lang)


def _normalize_data_mode(value: str | None) -> str:
    mode = (value or "auto").strip().lower()
    if mode not in DATA_MODES:
        allowed = ", ".join(sorted(DATA_MODES))
        raise ValueError(f"Unsupported data_mode: {value}. Choose from: {allowed}.")
    return mode


def _resolve_page_data_mode(value: str | None) -> tuple[str, str | None]:
    try:
        return _normalize_data_mode(value), None
    except ValueError as exc:
        return "auto", str(exc)


def _resolve_language(value: str | None) -> str:
    lang = (value or "zh").strip().lower()
    return lang if lang in LANGUAGES else "zh"


def _build_agent_view_model(result: dict, lang: str = "zh") -> dict:
    evidence = result.get("data_evidence", {})
    market = evidence.get("market") or {}
    ranking = evidence.get("ranking") or {}
    outliers = evidence.get("outliers") or {}
    summary = market.get("yield_summary") or {}
    volume = market.get("volume_summary") or {}
    data_source = result.get("data_source", {})
    maturity_coverage = data_source.get("maturity_coverage") or {}

    trust = result.get("trust_score") or {}
    return {
        "metrics": [
            _metric("Trust Score", "信任分", trust.get("score"), lang, "/100"),
            _metric("Data Source", "数据源", _localized_status(data_source.get("runtime_mode", "unknown"), lang), lang),
            _metric("Rows", "样本行数", data_source.get("row_count"), lang),
            _metric("Maturity Coverage", "期限覆盖率", _coverage_ratio_text(maturity_coverage), lang),
            _metric("Median Yield", "收益率中位数", summary.get("median"), lang, "%"),
            _metric("Evidence Score", "证据评分", result.get("evidence_quality", {}).get("score"), lang, "/100"),
            _metric("Final Source", "最终来源", _localized_status(result.get("final_answer_source", "unknown"), lang), lang),
        ],
        "trust_score": trust.get("score"),
        "trust_level": trust.get("level"),
        "trust_level_label": _localized_status(trust.get("level"), lang),
        "trust_summary": trust.get("summary_zh") if lang == "zh" else trust.get("summary_en"),
        "trust_summary_by_lang": {
            "zh": trust.get("summary_zh") or "",
            "en": trust.get("summary_en") or "",
        },
        "trust_reasons": [
            {
                "delta": item.get("delta"),
                "reason_zh": item.get("reason_zh"),
                "reason_en": item.get("reason_en"),
                "reason": item.get("reason_zh") if lang == "zh" else item.get("reason_en"),
            }
            for item in (trust.get("headline_reasons") or trust.get("adjustments") or [])[:5]
            if item.get("delta")
        ],
        "evidence_pack_id": result.get("evidence_pack_id"),
        "yield_bars": _distribution_bars(market.get("yield_distribution") or {}),
        "ranking_records": (ranking.get("records") or [])[:5],
        "outlier_records": (outliers.get("records") or [])[:5],
        "market_summary": [
            _metric("Yield Mean", "收益率均值", summary.get("mean"), lang, "%"),
            _metric("Yield Range", "收益率区间", _range_text(summary.get("min"), summary.get("max")), lang, "%"),
            _metric("Volume Median", "成交量中位数", volume.get("median"), lang, "bn CNY" if lang == "en" else " 亿元"),
        ],
        "tool_trace": [_localize_trace_item(item, lang) for item in result.get("tool_trace", [])],
        "tool_trace_by_lang": {
            "zh": [_localize_trace_item(item, "zh") for item in result.get("tool_trace", [])],
            "en": [_localize_trace_item(item, "en") for item in result.get("tool_trace", [])],
        },
        "final_answer": _format_display_answer(result, lang),
        "final_answer_by_lang": {
            "zh": _format_display_answer(result, "zh"),
            "en": _format_display_answer(result, "en"),
        },
        "risk_explanations": [_risk_item_view(item, lang) for item in result.get("risk_explanations", [])],
        "risk_profile_cards": [_risk_profile_card_view(item, lang) for item in result.get("risk_profile", {}).get("cards", [])],
        "risk_profile_summary": _risk_profile_summary(result.get("risk_profile", {}), lang),
        "risk_profile_summary_by_lang": {
            "zh": _risk_profile_summary(result.get("risk_profile", {}), "zh"),
            "en": _risk_profile_summary(result.get("risk_profile", {}), "en"),
        },
        "evidence_ledger": [_ledger_item_view(item, lang) for item in result.get("evidence_ledger", [])],
        "answer_judge_summary": _answer_judge_summary(result.get("answer_judge", {}), lang),
        "answer_judge_summary_by_lang": {
            "zh": _answer_judge_summary(result.get("answer_judge", {}), "zh"),
            "en": _answer_judge_summary(result.get("answer_judge", {}), "en"),
        },
        "answer_judge_checks": [_judge_check_view(item, lang) for item in result.get("answer_judge", {}).get("checks", [])],
        "answer_judge_status_label": _localized_status(result.get("answer_judge", {}).get("status"), lang),
        "risk_overall_label": _localized_status(result.get("risk_profile", {}).get("overall_level"), lang),
        "evidence_quality_summary": _evidence_quality_summary(result.get("evidence_quality", {}), lang),
        "evidence_quality_summary_by_lang": {
            "zh": _evidence_quality_summary(result.get("evidence_quality", {}), "zh"),
            "en": result.get("evidence_quality", {}).get("summary", ""),
        },
        "llm_guardrail_summary": _llm_guardrail_summary(result.get("llm_guardrail", {}), lang),
        "llm_guardrail_summary_by_lang": {
            "zh": _llm_guardrail_summary(result.get("llm_guardrail", {}), "zh"),
            "en": result.get("llm_guardrail", {}).get("summary", ""),
        },
        "intent_label": _intent_label(result.get("plan", {}).get("intent"), lang),
        "llm_status_label": _localized_status(result.get("llm_status"), lang),
        "guardrail_status_label": _localized_status(result.get("llm_guardrail", {}).get("status"), lang),
        "guardrail_numeric_label": _localized_status(result.get("llm_guardrail", {}).get("numeric_status"), lang),
        "guardrail_language_label": _localized_status(result.get("llm_guardrail", {}).get("language_status"), lang),
        "evidence_level_label": _localized_status(result.get("evidence_quality", {}).get("level"), lang),
        "final_source_label": _localized_status(result.get("final_answer_source", "unknown"), lang),
        "data_source_subtitle": _data_source_subtitle(data_source, lang),
    }


def _build_replay_view(record: dict, lang: str) -> dict:
    replay = {**record}
    tools = record.get("tools_used") or []
    replay["tool_labels"] = "、".join(_tool_label(tool, lang) for tool in tools)
    replay["tool_labels_zh"] = "、".join(_tool_label(tool, "zh") for tool in tools)
    replay["tool_labels_en"] = ", ".join(_tool_label(tool, "en") for tool in tools)
    replay["intent_label"] = _intent_label(record.get("intent"), lang)
    replay["intent_label_zh"] = _intent_label(record.get("intent"), "zh")
    replay["intent_label_en"] = _intent_label(record.get("intent"), "en")
    data_source = record.get("data_source") or {}
    replay["data_runtime_label"] = _localized_status(data_source.get("runtime_mode"), lang)
    replay["data_runtime_label_zh"] = _localized_status(data_source.get("runtime_mode"), "zh")
    replay["data_runtime_label_en"] = _localized_status(data_source.get("runtime_mode"), "en")
    return replay


def _metric(label_en: str, label_zh: str, value: object, lang: str, suffix: str = "") -> dict:
    if value is None:
        display = "N/A"
    else:
        display = f"{value}{suffix}" if suffix and isinstance(value, int | float) else str(value)
    return {
        "label": label_zh if lang == "zh" else label_en,
        "label_zh": label_zh,
        "label_en": label_en,
        "value": display,
    }


def _range_text(low: object, high: object) -> str:
    if low is None or high is None:
        return "N/A"
    return f"{low} - {high}"


def _yield_summary_sentence(summary: dict, lang: str) -> str:
    if not summary:
        return "收益率摘要暂缺。" if lang == "zh" else "Yield summary is not available."
    if lang == "en":
        return (
            f"Yield median {summary.get('median')}%, mean {summary.get('mean')}%, "
            f"range {summary.get('min')}% to {summary.get('max')}%."
        )
    return (
        f"收益率中位数 {summary.get('median')}%，均值 {summary.get('mean')}%，"
        f"区间 {summary.get('min')}% 到 {summary.get('max')}%。"
    )


def _rank_label(column: object, lang: str) -> str:
    mapping = {
        "收盘到期收益率(%)": {"zh": "收盘到期收益率", "en": "closing yield"},
        "交易量(亿元)": {"zh": "交易量", "en": "trading volume"},
        "待偿期(年)": {"zh": "待偿期", "en": "maturity"},
        "收盘净价(元)": {"zh": "收盘净价", "en": "clean price"},
    }
    return mapping.get(str(column), {}).get(lang, str(column or "N/A"))


def _format_search_criteria(criteria: dict, lang: str) -> str:
    if not criteria:
        return "无额外筛选条件" if lang == "zh" else "no additional filters"

    labels = {
        "name": {"zh": "名称包含", "en": "name contains"},
        "min_maturity": {"zh": "最短待偿期", "en": "minimum maturity"},
        "max_maturity": {"zh": "最长待偿期", "en": "maximum maturity"},
        "min_yield": {"zh": "最低收益率", "en": "minimum yield"},
        "max_yield": {"zh": "最高收益率", "en": "maximum yield"},
    }
    parts = []
    for key in ["name", "min_maturity", "max_maturity", "min_yield", "max_yield"]:
        value = criteria.get(key)
        if value is not None:
            parts.append(f"{labels[key][lang]} {value}")
    return "；".join(parts) if parts and lang == "zh" else ", ".join(parts) if parts else ("无额外筛选条件" if lang == "zh" else "no additional filters")


def _yes_no(value: object, lang: str) -> str:
    if value is True:
        return "是" if lang == "zh" else "yes"
    if value is False:
        return "否" if lang == "zh" else "no"
    return "未知" if lang == "zh" else "unknown"


def _distribution_bars(distribution: dict) -> list[dict]:
    max_count = max(distribution.values(), default=0)
    bars = []
    for label, count in distribution.items():
        width = 0 if max_count == 0 else round(float(count) / max_count * 100, 2)
        bars.append({"label": label, "count": count, "width": width})
    return bars


def _format_display_answer(result: dict, lang: str) -> str:
    evidence = result.get("data_evidence", {})
    market = evidence.get("market") or {}
    ranking = evidence.get("ranking") or {}
    outliers = evidence.get("outliers") or {}
    comparison = evidence.get("comparison") or {}
    search = evidence.get("search") or {}
    data_source = result.get("data_source") or {}
    plan = result.get("plan") or {}
    evidence_quality = result.get("evidence_quality") or {}

    if lang == "en":
        return _format_display_answer_en(result, market, ranking, outliers, comparison, search, data_source, plan, evidence_quality)

    lines = [
        f"问题：{result.get('question')}",
        f"本次任务：{_intent_label(plan.get('intent'), 'zh')}",
        "",
        "使用工具：",
        *[f"- {_tool_label(tool, 'zh')}" for tool in result.get("tools_used", [])],
        "",
        "数据证据：",
    ]

    if data_source:
        lines.append(
            f"- 数据源：{data_source.get('source_name')}（{_localized_status(data_source.get('runtime_mode'), 'zh')}）"
        )
        if data_source.get("fetched_at"):
            lines.append(f"- 获取时间：{data_source.get('fetched_at')}")
        if data_source.get("fallback_reason"):
            lines.append(f"- 实时数据降级原因：{data_source.get('fallback_reason')}")
        lines.append(f"- 样本行数：{data_source.get('row_count')}，有效收益率记录：{data_source.get('valid_yield_count')}")
        if data_source.get("maturity_coverage"):
            coverage = data_source["maturity_coverage"]
            lines.append(
                f"- 期限覆盖率：{_coverage_ratio_text(coverage)}，"
                f"已补全 {coverage.get('filled_count')} 条，缺失 {coverage.get('missing_count')} 条"
            )

    if market:
        lines.append(f"- 样本数量：{market.get('sample_count', 0)}")
        lines.append(f"- {_yield_summary_sentence(market.get('yield_summary', {}), 'zh')}")
    if ranking:
        lines.append(f"- 排序依据：{_rank_label(ranking.get('rank_by'), 'zh')}")
    if outliers:
        lines.append(f"- 异常样本数量：{outliers.get('outlier_count', 0)}")
    if search:
        lines.append(f"- 检索条件：{_format_search_criteria(search.get('criteria', {}), 'zh')}")
        lines.append(f"- 检索命中数量：{search.get('match_count', 0)}")
        for index, record in enumerate(search.get("records", [])[:5], start=1):
            lines.append(
                f"  {index}. {record.get('债券简称')} | 待偿期 {_display_maturity(record)} | "
                f"收益率 {record.get('收盘到期收益率(%)')}% | 成交量 {record.get('交易量(亿元)')} 亿元"
            )
    if comparison:
        lines.append(
            f"- 债券相对市场：收益率处于样本第 {comparison.get('yield_percentile')} 分位，"
            f"成交量处于第 {comparison.get('volume_percentile')} 分位，"
            f"是否收益率异常：{_yes_no(comparison.get('is_yield_outlier'), 'zh')}"
        )

    if result.get("risk_explanations"):
        lines.extend(["", "风险解释层："])
        for item in result["risk_explanations"]:
            localized = _localize_risk_item(item, "zh")
            lines.append(f"- {localized['title']}：{localized['summary']}")

    if evidence_quality:
        lines.extend(
            [
                "",
                "证据质量：",
                f"- 评分：{evidence_quality.get('score')}/100",
                f"- 等级：{_localized_status(evidence_quality.get('level'), 'zh')}",
                f"- 数据新鲜度：{_localized_status(evidence_quality.get('data_freshness'), 'zh')}",
                f"- 决策置信度：{_localized_status(evidence_quality.get('decision_confidence'), 'zh')}",
                f"- 摘要：{_evidence_quality_summary(evidence_quality, 'zh')}",
            ]
        )

    lines.extend(
        [
            "",
            "分析结论：",
            *[f"- {item}" for item in result.get("analysis", [])],
            "",
            "风险提示：",
            *[f"- {item}" for item in result.get("risk_notes", [])],
            "",
            "局限性：",
            *[f"- {item}" for item in result.get("limitations", [])],
        ]
    )
    return "\n".join(lines)


def _format_display_answer_en(
    result: dict,
    market: dict,
    ranking: dict,
    outliers: dict,
    comparison: dict,
    search: dict,
    data_source: dict,
    plan: dict,
    evidence_quality: dict,
) -> str:
    lines = [
        f"Question: {result.get('question')}",
        f"Task: {_intent_label(plan.get('intent'), 'en')}",
        "",
        "Tools used:",
        *[f"- {_tool_label(tool, 'en')}" for tool in result.get("tools_used", [])],
        "",
        "Data evidence:",
    ]

    if data_source:
        lines.append(f"- Source: {data_source.get('source_name')} ({_localized_status(data_source.get('runtime_mode'), 'en')})")
        if data_source.get("fetched_at"):
            lines.append(f"- Fetched at: {data_source.get('fetched_at')}")
        if data_source.get("fallback_reason"):
            lines.append(f"- Live-data fallback reason: {data_source.get('fallback_reason')}")
        lines.append(f"- Rows: {data_source.get('row_count')}; valid yield records: {data_source.get('valid_yield_count')}")
        if data_source.get("maturity_coverage"):
            coverage = data_source["maturity_coverage"]
            lines.append(
                f"- Maturity coverage: {_coverage_ratio_text(coverage)}; "
                f"{coverage.get('filled_count')} filled and {coverage.get('missing_count')} missing."
            )

    if market:
        lines.append(f"- Sample size: {market.get('sample_count', 0)}")
        lines.append(f"- {_yield_summary_sentence(market.get('yield_summary', {}), 'en')}")
    if ranking:
        lines.append(f"- Ranking basis: {_rank_label(ranking.get('rank_by'), 'en')}")
    if outliers:
        lines.append(f"- Yield outlier count: {outliers.get('outlier_count', 0)}")
    if search:
        lines.append(f"- Search criteria: {_format_search_criteria(search.get('criteria', {}), 'en')}")
        lines.append(f"- Search matches: {search.get('match_count', 0)}")
        for index, record in enumerate(search.get("records", [])[:5], start=1):
            lines.append(
                f"  {index}. {record.get('债券简称')} | maturity {_display_maturity(record)} | "
                f"yield {record.get('收盘到期收益率(%)')}% | volume {record.get('交易量(亿元)')} bn CNY"
            )
    if comparison:
        lines.append(
            f"- Bond vs market: yield percentile {comparison.get('yield_percentile')}, "
            f"volume percentile {comparison.get('volume_percentile')}, "
            f"yield outlier: {_yes_no(comparison.get('is_yield_outlier'), 'en')}."
        )

    if result.get("risk_explanations"):
        lines.extend(["", "Risk context:"])
        for item in result["risk_explanations"]:
            localized = _localize_risk_item(item, "en")
            lines.append(f"- {localized['title']}: {localized['summary']}")

    if evidence_quality:
        lines.extend(
            [
                "",
                "Evidence quality:",
                f"- Score: {evidence_quality.get('score')}/100",
                f"- Level: {_localized_status(evidence_quality.get('level'), 'en')}",
                f"- Data freshness: {_localized_status(evidence_quality.get('data_freshness'), 'en')}",
                f"- Decision confidence: {_localized_status(evidence_quality.get('decision_confidence'), 'en')}",
                f"- Summary: {_evidence_quality_summary(evidence_quality, 'en')}",
            ]
        )

    lines.extend(
        [
            "",
            "Analysis:",
            *[f"- {item}" for item in result.get("analysis", [])],
            "",
            "Risk notes:",
            *[f"- {item}" for item in result.get("risk_notes", [])],
            "",
            "Limitations:",
            *[f"- {item}" for item in result.get("limitations", [])],
        ]
    )
    return "\n".join(lines)


def _localize_trace_item(item: str, lang: str) -> str:
    if item.startswith("User question:"):
        label = "User question:" if lang == "en" else "用户问题："
        return item.replace("User question:", label, 1)
    if item == "-> final answer":
        return "Final answer selected" if lang == "en" else "最终回答已生成"
    if item.startswith("-> data_source"):
        return "Data source resolved" if lang == "en" else "数据源已确定"
    if item.startswith("-> planner"):
        return "Planner selected the analysis path" if lang == "en" else "规划器已选择分析路径"
    if item.startswith("-> search_bonds"):
        return "Bond search executed" if lang == "en" else "已执行债券检索"
    if item.startswith("-> compare_bond_to_market"):
        return "Bond-to-market comparison executed" if lang == "en" else "已执行单券市场对比"
    if item.startswith("-> describe_market"):
        return "Market overview generated" if lang == "en" else "已生成市场概览"
    if item.startswith("-> rank_bonds"):
        return "Bond ranking generated" if lang == "en" else "已生成债券排序"
    if item.startswith("-> detect_yield_outliers"):
        return "Yield outlier scan completed" if lang == "en" else "已完成收益率异常扫描"
    if item.startswith("-> generate_bond_report"):
        return "Evidence-based report composed" if lang == "en" else "已组合证据报告"
    if item.startswith("-> llm_guardrail"):
        if "llm_disabled" in item:
            return "LLM guardrail: skipped, LLM disabled" if lang == "en" else "LLM 护栏：跳过：LLM 未启用"
        if "llm_failed" in item:
            return "LLM guardrail: skipped, LLM call failed" if lang == "en" else "LLM 护栏：跳过：LLM 调用失败"
        if "status=passed" in item:
            return "LLM guardrail: passed" if lang == "en" else "LLM 护栏：通过"
        if "status=failed" in item:
            return "LLM guardrail: failed" if lang == "en" else "LLM 护栏：失败"
        return "LLM guardrail completed" if lang == "en" else "LLM 护栏已完成"
    return item


def _localize_risk_item(item: dict, lang: str) -> dict:
    if lang == "en":
        return {
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "watch_points": item.get("watch_points", []),
        }
    translation = RISK_TRANSLATIONS.get(item.get("id"), {})
    return {
        "title": translation.get("title", item.get("title", "")),
        "summary": translation.get("summary", item.get("summary", "")),
        "watch_points": translation.get("watch_points", item.get("watch_points", [])),
    }


def _risk_item_view(item: dict, lang: str) -> dict:
    zh = _localize_risk_item(item, "zh")
    en = _localize_risk_item(item, "en")
    active = zh if lang == "zh" else en
    return {
        "title": active["title"],
        "summary": active["summary"],
        "watch_points": active["watch_points"],
        "title_zh": zh["title"],
        "title_en": en["title"],
        "summary_zh": zh["summary"],
        "summary_en": en["summary"],
        "watch_points_zh": zh["watch_points"],
        "watch_points_en": en["watch_points"],
    }


def _risk_profile_card_view(item: dict, lang: str) -> dict:
    title = item.get(f"title_{lang}", item.get("title_zh", ""))
    signal = item.get(f"signal_{lang}", item.get("signal_zh", ""))
    evidence = item.get(f"evidence_{lang}", item.get("evidence_zh", ""))
    boundary = item.get(f"action_boundary_{lang}", item.get("action_boundary_zh", ""))
    return {
        "id": item.get("id"),
        "severity": item.get("severity"),
        "severity_label": _localized_status(item.get("severity"), lang),
        "title": title,
        "title_zh": item.get("title_zh", ""),
        "title_en": item.get("title_en", ""),
        "signal": signal,
        "signal_zh": item.get("signal_zh", ""),
        "signal_en": item.get("signal_en", ""),
        "evidence": evidence,
        "evidence_zh": item.get("evidence_zh", ""),
        "evidence_en": item.get("evidence_en", ""),
        "boundary": boundary,
        "boundary_zh": item.get("action_boundary_zh", ""),
        "boundary_en": item.get("action_boundary_en", ""),
    }


def _ledger_item_view(item: dict, lang: str) -> dict:
    return {
        "id": item.get("id"),
        "claim": item.get(f"claim_{lang}", item.get("claim_zh", "")),
        "claim_zh": item.get("claim_zh", ""),
        "claim_en": item.get("claim_en", ""),
        "evidence": item.get(f"evidence_{lang}", item.get("evidence_zh", "")),
        "evidence_zh": item.get("evidence_zh", ""),
        "evidence_en": item.get("evidence_en", ""),
        "source": item.get("source", ""),
        "tool": item.get("tool", ""),
        "tool_label": _tool_label(item.get("tool", ""), lang) if item.get("tool") else item.get("tool", ""),
        "confidence": item.get("confidence", ""),
        "confidence_label": _localized_status(item.get("confidence"), lang),
    }


def _judge_check_view(item: dict, lang: str) -> dict:
    return {
        "id": item.get("id"),
        "label": item.get(f"label_{lang}", item.get("label_zh", "")),
        "label_zh": item.get("label_zh", ""),
        "label_en": item.get("label_en", ""),
        "status": item.get("status"),
        "status_label": _localized_status(item.get("status"), lang),
        "detail": item.get(f"detail_{lang}", item.get("detail_zh", "")),
        "detail_zh": item.get("detail_zh", ""),
        "detail_en": item.get("detail_en", ""),
    }


def _intent_label(intent: str | None, lang: str) -> str:
    return INTENT_LABELS.get(intent or "", {}).get(lang, intent or "unknown")


def _tool_label(tool: str, lang: str) -> str:
    return TOOL_LABELS.get(tool, {}).get(lang, tool)


def _localized_status(value: object, lang: str) -> str:
    if value is None:
        return "N/A"
    if lang == "en":
        mapping_en = {
            "live": "Live",
            "live_snapshot": "Live snapshot",
            "static_sample": "Local sample",
            "static_fallback": "Local fallback",
            "deterministic_fallback": "Rule fallback",
            "success": "Success",
            "failed": "Failed",
            "disabled": "Disabled",
            "passed": "Passed",
            "not_run": "Not triggered",
            "high": "High",
            "medium": "Medium",
            "low": "Low",
            "live_fetch": "Live fetch",
            "cached_live_snapshot": "Cached snapshot",
            "static_snapshot": "Static snapshot",
            "safe_fallback": "Safe fallback",
            "failed_guardrail": "Guardrail failed",
            "not_applicable": "Not applicable",
            "warning": "Warning",
        }
        return mapping_en.get(str(value), str(value))
    mapping = {
        "live": "实时行情",
        "live_snapshot": "实时快照",
        "static_sample": "本地样本",
        "static_fallback": "本地兜底",
        "deterministic_fallback": "规则兜底",
        "success": "成功",
        "failed": "失败",
        "disabled": "未启用",
        "passed": "通过",
        "not_run": "未触发",
        "high": "高",
        "medium": "中",
        "low": "低",
        "live_fetch": "实时获取",
        "cached_live_snapshot": "缓存快照",
        "static_snapshot": "静态快照",
        "safe_fallback": "安全回退",
        "failed_guardrail": "护栏失败",
        "not_applicable": "不适用",
        "warning": "提醒",
    }
    return mapping.get(str(value), str(value))


def _evidence_quality_summary(evidence_quality: dict, lang: str) -> str:
    if lang == "en":
        return evidence_quality.get("summary", "")
    level = _localized_status(evidence_quality.get("level"), "zh")
    return f"当前数据源的证据质量为{level}，但因为尚未接入主体信用、宏观曲线和完整证券主数据，决策置信度仍保持为低。"


def _llm_guardrail_summary(guardrail: dict, lang: str) -> str:
    if lang == "en":
        return guardrail.get("summary", "")
    status = guardrail.get("status")
    if status == "not_run":
        return "未调用 LLM 输出，因此没有运行数值一致性和投资建议语言检查。"
    if status == "passed":
        return "LLM 输出已通过数值一致性和风险语言检查。"
    return "LLM 输出未通过可信度检查，页面使用规则兜底报告作为最终答案。"


def _answer_judge_summary(answer_judge: dict, lang: str) -> str:
    if lang == "en":
        return answer_judge.get("verdict_en", "")
    return answer_judge.get("verdict_zh", "")


def _risk_profile_summary(risk_profile: dict, lang: str) -> str:
    if lang == "en":
        return risk_profile.get("summary_en", "")
    return risk_profile.get("summary_zh", "")


def _coverage_ratio_text(coverage: dict) -> str:
    ratio = coverage.get("coverage_ratio")
    if ratio is None:
        return "N/A"
    return f"{round(float(ratio) * 100, 1)}%"


def _display_maturity(record: dict) -> str:
    maturity = record.get("待偿期")
    if maturity is not None and str(maturity).strip():
        return str(maturity)
    return "当前数据源暂缺"


def _data_source_subtitle(data_source: dict, lang: str) -> str:
    if lang == "en":
        return f"{data_source.get('source_name')} · {data_source.get('runtime_mode')} · {data_source.get('row_count')} rows"
    return (
        f"{data_source.get('source_name')} · "
        f"{_localized_status(data_source.get('runtime_mode'), 'zh')} · "
        f"{data_source.get('row_count')} 行"
    )


if __name__ == "__main__":
    app.run(
        host=os.environ.get("FLASK_RUN_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_ENV") == "development",
    )
