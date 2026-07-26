from __future__ import annotations

import csv
import io
import json
import os
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from time import time as _wall_time
from uuid import uuid4

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    stream_with_context,
    url_for,
)
from pydantic import ValidationError

from bond_agent import BondAnalystAgent
from bond_agent.evidence_pack import DEFAULT_PACK_DIR, DEMO_PACK_DIR
from bond_agent.i18n import FIELD_LABELS, INTENT_LABELS, RISK_TRANSLATIONS, TOOL_LABELS
from bond_agent.replay_store import list_replays
from bond_agent.schemas import AgentQueryRequest, ApiError, HealthResponse, api_schema_bundle

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
DATA_MODES = {"auto", "live", "static"}
LANGUAGES = {"zh", "en"}


_AGENT_LOCK = Lock()
_AGENT_POOL: dict[tuple, BondAnalystAgent] = {}


def _get_agent(data_mode: str = "auto") -> BondAnalystAgent:
    """Reuse BondAnalystAgent instances per data_mode (stateless tools; DF cache lives in data_loader)."""
    key = (data_mode or "auto",)
    with _AGENT_LOCK:
        agent = _AGENT_POOL.get(key)
        if agent is None:
            agent = BondAnalystAgent(data_mode=data_mode)
            _AGENT_POOL[key] = agent
        return agent

# Short-lived in-memory cache so the async agent form can re-render HTML without a second LLM call.
_RESULT_CACHE: OrderedDict[str, dict] = OrderedDict()
_RESULT_CACHE_LIMIT = int(os.environ.get("BOND_RESULT_CACHE_LIMIT", "32"))
_RESULT_CACHE_TTL_SECONDS = float(os.environ.get("BOND_RESULT_CACHE_TTL_SECONDS", "1800"))
_RESULT_CACHE_LOCK = Lock()




@app.route("/")
def index():
    return redirect(url_for("agent_page"))


@app.context_processor
def inject_language_context():
    return {"current_lang": _resolve_language()}


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
    result_id = None
    lang = _resolve_language()
    data_mode, form_error = _resolve_page_data_mode(request.values.get("data_mode", os.environ.get("BOND_DATA_MODE", "auto")))
    if request.method == "POST":
        question = request.form.get("question", "").strip()
        result = _get_agent(data_mode).answer(question)
        result_id = _store_result(result, question=question, data_mode=data_mode)
        view = _build_agent_view_model(result, lang=lang)
    else:
        # Async form path: after /api/agent/query, browser reloads with result_id to render full HTML once.
        cached_id = (request.args.get("result_id") or "").strip()
        cached = _load_result(cached_id) if cached_id else None
        if cached:
            result = cached.get("result")
            question = cached.get("question") or ""
            data_mode = cached.get("data_mode") or data_mode
            result_id = cached_id
            view = _build_agent_view_model(result, lang=lang)
    html = render_template(
        "agent.html",
        result=result,
        view=view,
        question=question,
        data_mode=data_mode,
        form_error=form_error,
        lang=lang,
        result_id=result_id,
    )
    return _with_language_cookie(html, lang)


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
    lang = _resolve_language(payload.get("lang") if isinstance(payload, dict) else None)
    result = _get_agent(data_mode).answer(question)
    result_id = _store_result(result, question=question, data_mode=data_mode)
    include_view = bool(payload.get("include_view")) if isinstance(payload, dict) else False
    body = dict(result)
    body["result_id"] = result_id
    body["result_url"] = url_for("agent_page", result_id=result_id, lang=lang, data_mode=data_mode)
    if include_view:
        body["view"] = _build_agent_view_model(result, lang=lang)
    return jsonify(body)


@app.route("/api/agent/stream", methods=["POST"])
def agent_stream():
    """SSE token/status stream for agent answers.

    Emits text/event-stream events:
      event: status|token|final|error
      data: JSON
    """
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
    lang = _resolve_language(payload.get("lang") if isinstance(payload, dict) else None)

    def generate():
        import json as _json
        agent = _get_agent(data_mode)
        try:
            for event in agent.iter_answer_events(question):
                etype = event.get("type") or "status"
                if etype == "final":
                    result = event.get("result") or {}
                    result_id = _store_result(result, question=question, data_mode=data_mode)
                    view = _build_agent_view_model(result, lang=lang)
                    body = {
                        "type": "final",
                        "result_id": result_id,
                        "result_url": url_for("agent_page", result_id=result_id, lang=lang, data_mode=data_mode),
                        "result": result,
                        "view": view,
                        "question": question,
                        "data_mode": data_mode,
                        "lang": lang,
                    }
                    yield "event: final\ndata: " + _json.dumps(body, ensure_ascii=False) + "\n\n"
                elif etype == "token":
                    body = {"type": "token", "text": event.get("text") or "", "model": event.get("model")}
                    yield f"event: token\ndata: {_json.dumps(body, ensure_ascii=False)}\n\n"
                elif etype == "error":
                    body = {"type": "error", "error": event.get("error") or "stream error"}
                    yield f"event: error\ndata: {_json.dumps(body, ensure_ascii=False)}\n\n"
                else:
                    body = {
                        "type": "status",
                        "stage": event.get("stage"),
                        "tool": event.get("tool"),
                        "message": event.get("message_en") if lang == "en" else event.get("message_zh"),
                        "message_zh": event.get("message_zh"),
                        "message_en": event.get("message_en"),
                    }
                    yield f"event: status\ndata: {_json.dumps(body, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 - surface stream failures to client
            body = {"type": "error", "error": f"{type(exc).__name__}: {exc}"}
            yield f"event: error\ndata: {_json.dumps(body, ensure_ascii=False)}\n\n"

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@app.route("/api/agent/schema")
def agent_schema():
    return jsonify(api_schema_bundle())


@app.route("/replay")
def replay_page():
    lang = _resolve_language()
    replays = [_build_replay_view(record, lang) for record in list_replays()]
    html = render_template("replay.html", replays=replays, lang=lang)
    return _with_language_cookie(html, lang)


@app.route("/packs/<pack_id>.<ext>")
def evidence_pack_file(pack_id: str, ext: str):
    """Serve a previously exported Evidence Pack (JSON or HTML)."""
    if ext not in {"json", "html"}:
        abort(404)
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in pack_id).strip("-")
    if not safe_id:
        abort(404)
    filename = f"{safe_id}.{ext}"
    search_dirs = []
    configured = request.args.get("dir") or None
    env_dir = os.environ.get("BOND_EVIDENCE_PACK_DIR")
    for candidate in [configured, env_dir, str(DEFAULT_PACK_DIR), str(DEMO_PACK_DIR)]:
        if candidate and candidate not in search_dirs:
            search_dirs.append(candidate)
    for directory in search_dirs:
        path = Path(directory) / filename
        if path.is_file():
            mimetype = "application/json" if ext == "json" else "text/html"
            return send_file(path, mimetype=mimetype, download_name=filename, as_attachment=ext == "json")
    abort(404)


@app.route("/api/maturity/unmatched", methods=["GET", "POST"])
def export_unmatched_maturity():
    """Export unmatched-maturity bond names as CSV or JSON (live enrichment gap list)."""
    payload = request.get_json(silent=True) or {}
    fmt = (request.args.get("format") or payload.get("format") or "csv").strip().lower()
    if fmt not in {"csv", "json"}:
        return jsonify(ApiError(error="Unsupported format. Use csv or json.").model_dump(mode="json")), 400

    try:
        data_mode = _normalize_data_mode(
            request.args.get("data_mode")
            or payload.get("data_mode")
            or request.form.get("data_mode")
            or os.environ.get("BOND_DATA_MODE", "auto")
        )
    except ValueError as exc:
        return jsonify(ApiError(error=str(exc), allowed_data_modes=sorted(DATA_MODES)).model_dump(mode="json", exclude_none=True)), 400

    # Prefer records posted from the latest agent run (same snapshot as the UI board).
    records = payload.get("records")
    coverage = payload.get("maturity_coverage") or {}
    if not isinstance(records, list):
        result = _get_agent(data_mode).answer(
            payload.get("question") or "打开今日市场监控面板：高收益、低成交与异常"
        )
        coverage = (result.get("data_source") or {}).get("maturity_coverage") or {}
        records = coverage.get("unmatched_records") or []

    export_body = {
        "data_mode": data_mode,
        "filled_count": coverage.get("filled_count"),
        "missing_count": coverage.get("missing_count"),
        "coverage_ratio": coverage.get("coverage_ratio"),
        "unmatched_count": coverage.get("unmatched_count", len(records)),
        "records": records,
    }

    if fmt == "json":
        raw = json.dumps(export_body, ensure_ascii=False, indent=2)
        return send_file(
            io.BytesIO(raw.encode("utf-8")),
            mimetype="application/json",
            download_name="maturity-unmatched.json",
            as_attachment=True,
        )

    buffer = io.StringIO()
    fieldnames = ["债券简称", "收盘到期收益率(%)", "交易量(亿元)", "成交净价(元)", "加权收益率(%)", "涨跌(BP)", "待偿期来源"]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        if not isinstance(record, dict):
            continue
        writer.writerow({key: record.get(key) for key in fieldnames})
    return send_file(
        io.BytesIO(buffer.getvalue().encode("utf-8-sig")),
        mimetype="text/csv; charset=utf-8",
        download_name="maturity-unmatched.csv",
        as_attachment=True,
    )



def _store_result(result: dict, question: str = "", data_mode: str = "auto") -> str:
    result_id = uuid4().hex
    entry = {
        "result": result,
        "question": question,
        "data_mode": data_mode,
        "stored_at": _wall_time(),
    }
    with _RESULT_CACHE_LOCK:
        _RESULT_CACHE[result_id] = entry
        _RESULT_CACHE.move_to_end(result_id)
        while len(_RESULT_CACHE) > max(_RESULT_CACHE_LIMIT, 1):
            _RESULT_CACHE.popitem(last=False)
    return result_id


def _load_result(result_id: str | None) -> dict | None:
    if not result_id:
        return None
    now = _wall_time()
    with _RESULT_CACHE_LOCK:
        entry = _RESULT_CACHE.get(result_id)
        if entry is None:
            return None
        age = now - float(entry.get("stored_at") or 0.0)
        if _RESULT_CACHE_TTL_SECONDS > 0 and age > _RESULT_CACHE_TTL_SECONDS:
            _RESULT_CACHE.pop(result_id, None)
            return None
        _RESULT_CACHE.move_to_end(result_id)
        return entry


def _field_label(key: str, lang: str = "zh") -> str:
    entry = FIELD_LABELS.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry.get("zh") or key


def _localize_bond_records(records: list | None, lang: str = "zh") -> list[dict]:
    """Project raw bond dicts into UI-friendly bilingual display fields."""
    localized: list[dict] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        item = dict(record)
        name = record.get("债券简称")
        yld = record.get("收盘到期收益率(%)")
        vol = record.get("交易量(亿元)")
        maturity = record.get("待偿期")
        bond_type = record.get("券种") or record.get("bond_type")
        item.update(
            {
                "name": name,
                "name_label": _field_label("债券简称", lang),
                "yield_value": yld,
                "yield_label": _field_label("收盘到期收益率(%)", lang),
                "volume_value": vol,
                "volume_label": _field_label("交易量(亿元)", lang),
                "maturity_value": maturity,
                "maturity_label": _field_label("待偿期", lang),
                "type_value": bond_type,
                "type_label": _field_label("券种", lang),
                "duration_value": record.get("修正久期(现金流假设)", record.get("修正久期(近似)")),
                "duration_label": _field_label("修正久期(现金流假设)", lang),
                "macaulay_value": record.get("麦考利久期(现金流假设)"),
                "dv01_value": record.get("DV01(现金流假设)", record.get("DV01(近似)")),
                "dv01_label": _field_label("DV01(现金流假设)", lang),
                "perpetual_duration_value": record.get("理论永续修正久期"),
                "is_perpetual": bool(record.get("是否永续风格")),
                "score_value": record.get("分数") or record.get("score") or record.get("zscore"),
            }
        )
        localized.append(item)
    return localized


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


def _resolve_language(value: str | None = None) -> str:
    """Resolve UI language: explicit value > query/form > cookie > default zh."""
    candidates = [
        value,
        request.values.get("lang") if request else None,
        request.cookies.get("bondlens_lang") if request else None,
    ]
    for candidate in candidates:
        lang = (candidate or "").strip().lower()
        if lang in LANGUAGES:
            return lang
    return "zh"


def _with_language_cookie(body: str, lang: str):
    response = make_response(body)
    response.set_cookie(
        "bondlens_lang",
        lang if lang in LANGUAGES else "zh",
        max_age=60 * 60 * 24 * 365,
        samesite="Lax",
    )
    return response


def _build_agent_view_model(result: dict, lang: str = "zh") -> dict:
    evidence = result.get("data_evidence", {})
    market = evidence.get("market") or {}
    ranking = evidence.get("ranking") or {}
    outliers = evidence.get("outliers") or {}
    comparison = evidence.get("comparison") or {}
    monitor = evidence.get("monitor") or {}
    summary = market.get("yield_summary") or {}
    volume = market.get("volume_summary") or {}
    data_source = result.get("data_source", {})
    maturity_coverage = data_source.get("maturity_coverage") or {}
    data_quality = market.get("data_quality") or {}

    trust = result.get("trust_score") or {}
    stress = result.get("stress_view") or {}
    pack_id = result.get("evidence_pack_id")
    return {
        "metrics": [
            _metric("Trust Score", "信任分", trust.get("score"), lang, "/100"),
            _metric("Data Source", "数据源", _localized_status(data_source.get("runtime_mode", "unknown"), lang), lang),
            _metric("Fetched At", "获取时间", _format_fetched_at(data_source.get("fetched_at"), lang), lang),
            _metric("Rows", "样本行数", data_source.get("row_count"), lang),
            _metric("Maturity Coverage", "期限覆盖率", _coverage_ratio_text(maturity_coverage), lang),
            _metric("Median Yield", "收益率中位数", summary.get("median"), lang, "%"),
            _metric("Evidence Score", "证据评分", result.get("evidence_quality", {}).get("score"), lang, "/100"),
            _metric("Final Source", "最终来源", _localized_status(result.get("final_answer_source", "unknown"), lang), lang),
        ],
        "data_lineage": _build_data_lineage_view(data_source, lang),
        "answer_provenance": _build_answer_provenance_view(result, lang),
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
        "stress_view": stress,
        "stress_severity": stress.get("severity"),
        "stress_severity_label": _localized_status(stress.get("severity"), lang),
        "stress_summary": stress.get("summary_zh") if lang == "zh" else stress.get("summary_en"),
        "stress_summary_by_lang": {
            "zh": stress.get("summary_zh") or "",
            "en": stress.get("summary_en") or "",
        },
        "stress_signals": [
            {
                "id": item.get("id"),
                "severity": item.get("severity"),
                "severity_label": _localized_status(item.get("severity"), lang),
                "message": item.get("message_zh") if lang == "zh" else item.get("message_en"),
                "message_zh": item.get("message_zh"),
                "message_en": item.get("message_en"),
            }
            for item in (stress.get("signals") or [])
        ],
        "evidence_pack_id": pack_id,
        "pack_html_url": f"/packs/{pack_id}.html" if pack_id else None,
        "pack_json_url": f"/packs/{pack_id}.json" if pack_id else None,
        "yield_bars": _distribution_bars(market.get("yield_distribution") or {}),
        "segment_type_rows": (market.get("segments") or {}).get("by_bond_type") or [],
        "segment_bucket_rows": (market.get("segments") or {}).get("by_maturity_bucket") or [],
        "data_quality": data_quality,
        "data_quality_issues": data_quality.get("issues") or [],
        "data_quality_diagnostics": data_quality.get("diagnostics") or {},
        "peer_comparison": (comparison.get("peer_comparison") if comparison else None) or {},
        "rate_sensitivity": (comparison.get("rate_sensitivity") if comparison else None) or {},
        "credit_context": (comparison.get("credit_context") if comparison else None) or {},
        "monitor": monitor,
        "monitor_high_yield": _localize_bond_records((monitor.get("high_yield") or [])[:5], lang),
        "monitor_low_volume": _localize_bond_records((monitor.get("low_volume") or [])[:5], lang),
        "monitor_outliers": _localize_bond_records((monitor.get("yield_outliers") or [])[:5], lang),
        "monitor_missing_maturity": _localize_bond_records((monitor.get("missing_maturity") or [])[:5], lang),
        "monitor_summary": monitor.get("summary_zh") if lang == "zh" else monitor.get("summary_en"),
        "monitor_summary_by_lang": {
            "zh": monitor.get("summary_zh") or "",
            "en": monitor.get("summary_en") or "",
        },
        "maturity_coverage": maturity_coverage,
        "maturity_coverage_text": _coverage_ratio_text(maturity_coverage),
        "maturity_note": _maturity_honesty_note(data_source, lang),
        "maturity_note_by_lang": {
            "zh": _maturity_honesty_note(data_source, "zh"),
            "en": _maturity_honesty_note(data_source, "en"),
        },
        "maturity_board": _build_maturity_board(data_source, lang),
        "maturity_unmatched_records": _localize_bond_records((maturity_coverage.get("unmatched_records") or [])[:20], lang),
        "maturity_export_csv_url": _maturity_export_url("csv", data_source),
        "maturity_export_json_url": _maturity_export_url("json", data_source),
        "ranking_records": _localize_bond_records((ranking.get("records") or [])[:5], lang),
        "outlier_records": _localize_bond_records((outliers.get("records") or [])[:5], lang),
        "field_labels": {key: _field_label(key, lang) for key in FIELD_LABELS},
        "market_summary": [
            _metric("Yield Mean", "收益率均值", summary.get("mean"), lang, "%"),
            _metric("Yield Range", "收益率区间", _range_text(summary.get("min"), summary.get("max")), lang, "%"),
            _metric("Volume Median", "成交量中位数", volume.get("median"), lang, "bn CNY" if lang == "en" else " 亿元"),
            _metric(
                "Data Quality",
                "数据质量",
                data_quality.get("score"),
                lang,
                "/100",
            ),
        ],
        "tool_trace": [_localize_trace_item(item, lang) for item in result.get("tool_trace", [])],
        "tool_trace_by_lang": {
            "zh": [_localize_trace_item(item, "zh") for item in result.get("tool_trace", [])],
            "en": [_localize_trace_item(item, "en") for item in result.get("tool_trace", [])],
        },
        "answer_summary": _build_answer_summary(result, lang),
        "answer_summary_by_lang": {
            "zh": _build_answer_summary(result, "zh"),
            "en": _build_answer_summary(result, "en"),
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
    if item.startswith("-> build_market_monitor"):
        return "Market monitor board built" if lang == "en" else "已生成市场监控面板"
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
    translation = RISK_TRANSLATIONS.get(item.get("id"), {})
    localized = translation.get(lang) or translation.get("zh") or {}
    return {
        "title": localized.get("title") or item.get("title", ""),
        "summary": localized.get("summary") or item.get("summary", ""),
        "watch_points": localized.get("watch_points") or item.get("watch_points", []),
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


def _maturity_export_url(fmt: str, data_source: dict) -> str:
    requested = (data_source.get("requested_mode") or "").strip().lower()
    if requested in DATA_MODES:
        mode = requested
    else:
        runtime = (data_source.get("runtime_mode") or "").strip().lower()
        if runtime == "live":
            mode = "live"
        elif runtime == "live_snapshot":
            mode = "auto"
        else:
            mode = "static"
    return f"/api/maturity/unmatched?format={fmt}&data_mode={mode}"


def _coverage_ratio_text(coverage: dict) -> str:
    ratio = coverage.get("coverage_ratio")
    if ratio is None:
        return "N/A"
    return f"{round(float(ratio) * 100, 1)}%"


def _build_answer_summary(result: dict, lang: str = "zh") -> dict:
    """Build a 3-sentence headline + key metrics for the answer-first hero."""
    evidence = result.get("data_evidence") or {}
    market = evidence.get("market") or {}
    comparison = evidence.get("comparison") or {}
    monitor = evidence.get("monitor") or {}
    search = evidence.get("search") or {}
    data_source = result.get("data_source") or {}
    trust = result.get("trust_score") or {}
    plan = result.get("plan") or {}
    intent = plan.get("intent") or "market_overview"
    coverage = data_source.get("maturity_coverage") or {}
    yield_summary = market.get("yield_summary") or {}
    quality = market.get("data_quality") or {}
    peer = comparison.get("peer_comparison") or {}
    rows = data_source.get("row_count")
    mode = data_source.get("runtime_mode") or "unknown"
    trust_score = trust.get("score")
    median = yield_summary.get("median")
    quality_score = quality.get("score")
    coverage_text = _coverage_ratio_text(coverage)

    key_metrics = [
        _metric("Trust", "信任分", trust_score, lang, "/100"),
        _metric("Rows", "样本行数", rows, lang),
        _metric("Median Yield", "中位收益", median, lang, "%"),
        _metric("Maturity Coverage", "期限覆盖", coverage_text, lang),
        _metric("Data Quality", "数据质量", quality_score, lang, "/100"),
    ]

    if peer.get("peer_count"):
        key_metrics.append(
            _metric(
                "Peer Spread",
                "同业利差",
                peer.get("spread_vs_peer_mean_bp"),
                lang,
                " bp",
            )
        )

    if lang == "en":
        sentences = _answer_summary_sentences_en(
            intent=intent,
            mode=mode,
            rows=rows,
            median=median,
            trust_score=trust_score,
            coverage_text=coverage_text,
            quality_score=quality_score,
            peer=peer,
            monitor=monitor,
            search=search,
            data_source=data_source,
        )
    else:
        sentences = _answer_summary_sentences_zh(
            intent=intent,
            mode=mode,
            rows=rows,
            median=median,
            trust_score=trust_score,
            coverage_text=coverage_text,
            quality_score=quality_score,
            peer=peer,
            monitor=monitor,
            search=search,
            data_source=data_source,
        )

    return {
        "headline": sentences[0] if sentences else "",
        "sentences": sentences[:3],
        "key_metrics": key_metrics[:6],
        "intent": intent,
        "intent_label": _intent_label(intent, lang),
    }


def _answer_summary_sentences_zh(
    *,
    intent: str,
    mode: str,
    rows,
    median,
    trust_score,
    coverage_text: str,
    quality_score,
    peer: dict,
    monitor: dict,
    search: dict,
    data_source: dict,
) -> list[str]:
    mode_label = _localized_status(mode, "zh")
    sentence_1 = f"本次意图为{_intent_label(intent, 'zh')}，数据源运行模式 {mode_label}，样本 {rows if rows is not None else '—'} 行。"
    if median is not None:
        sentence_2 = f"样本中位收益率 {median}%，数据质量 {quality_score if quality_score is not None else '—'} /100，期限覆盖率 {coverage_text}。"
    else:
        sentence_2 = f"数据质量 {quality_score if quality_score is not None else '—'} /100，期限覆盖率 {coverage_text}；部分统计字段缺失。"

    if peer.get("peer_count"):
        spread = peer.get("spread_vs_peer_mean_bp")
        sentence_3 = (
            f"同业可比 n={peer.get('peer_count')}，相对同业利差 "
            f"{'—' if spread is None else str(spread) + ' bp'}；信任分 {trust_score if trust_score is not None else '—'}/100。"
        )
    elif monitor:
        hy = len(monitor.get("high_yield") or [])
        lv = len(monitor.get("low_volume") or [])
        out = len(monitor.get("yield_outliers") or [])
        sentence_3 = f"监控面板已生成：高收益 {hy}、低成交 {lv}、收益异常 {out} 条观察清单；信任分 {trust_score if trust_score is not None else '—'}/100。"
    elif search.get("match_count") is not None:
        sentence_3 = f"检索命中 {search.get('match_count')} 条；信任分 {trust_score if trust_score is not None else '—'}/100，完整证据见下方展开区。"
    else:
        filled = (data_source.get("maturity_coverage") or {}).get("filled_count")
        missing = (data_source.get("maturity_coverage") or {}).get("missing_count")
        sentence_3 = (
            f"期限补全 {filled if filled is not None else '—'} 条、缺失 {missing if missing is not None else '—'} 条；"
            f"信任分 {trust_score if trust_score is not None else '—'}/100，完整正文可展开查看。"
        )
    return [sentence_1, sentence_2, sentence_3]


def _answer_summary_sentences_en(
    *,
    intent: str,
    mode: str,
    rows,
    median,
    trust_score,
    coverage_text: str,
    quality_score,
    peer: dict,
    monitor: dict,
    search: dict,
    data_source: dict,
) -> list[str]:
    sentence_1 = (
        f"Intent is {_intent_label(intent, 'en')}; runtime mode {mode}; "
        f"sample size {rows if rows is not None else '—'} rows."
    )
    if median is not None:
        sentence_2 = (
            f"Median yield {median}%; data quality {quality_score if quality_score is not None else '—'}/100; "
            f"maturity coverage {coverage_text}."
        )
    else:
        sentence_2 = (
            f"Data quality {quality_score if quality_score is not None else '—'}/100; "
            f"maturity coverage {coverage_text}; some stats are incomplete."
        )

    if peer.get("peer_count"):
        spread = peer.get("spread_vs_peer_mean_bp")
        sentence_3 = (
            f"Peer set n={peer.get('peer_count')}, spread vs peers "
            f"{'—' if spread is None else str(spread) + ' bp'}; "
            f"trust score {trust_score if trust_score is not None else '—'}/100."
        )
    elif monitor:
        hy = len(monitor.get("high_yield") or [])
        lv = len(monitor.get("low_volume") or [])
        out = len(monitor.get("yield_outliers") or [])
        sentence_3 = (
            f"Monitor board ready: high-yield {hy}, low-volume {lv}, outliers {out}; "
            f"trust score {trust_score if trust_score is not None else '—'}/100."
        )
    elif search.get("match_count") is not None:
        sentence_3 = (
            f"Search matched {search.get('match_count')} bonds; "
            f"trust score {trust_score if trust_score is not None else '—'}/100. Expand for full evidence."
        )
    else:
        filled = (data_source.get("maturity_coverage") or {}).get("filled_count")
        missing = (data_source.get("maturity_coverage") or {}).get("missing_count")
        sentence_3 = (
            f"Maturity filled {filled if filled is not None else '—'}, missing {missing if missing is not None else '—'}; "
            f"trust score {trust_score if trust_score is not None else '—'}/100. Expand for full answer."
        )
    return [sentence_1, sentence_2, sentence_3]


def _build_maturity_board(data_source: dict, lang: str = "zh") -> dict:
    coverage = data_source.get("maturity_coverage") or {}
    ratio = coverage.get("coverage_ratio")
    source_counts = coverage.get("source_counts") or {}
    unmatched = coverage.get("unmatched_records") or []
    runtime = data_source.get("runtime_mode") or ""
    if lang == "en":
        title = "Maturity Enrichment Board"
        subtitle = (
            "Live/snapshot feeds have no native maturity. "
            f"Coverage {_coverage_ratio_text(coverage)} "
            f"({coverage.get('filled_count')} filled / {coverage.get('missing_count')} missing)."
        )
        if runtime not in {"live", "live_snapshot"}:
            subtitle = (
                f"Maturity coverage {_coverage_ratio_text(coverage)} "
                f"({coverage.get('filled_count')} filled / {coverage.get('missing_count')} missing)."
            )
    else:
        title = "期限补全看板"
        subtitle = (
            "实时/快照源原生无期限字段。"
            f"当前补全覆盖率 {_coverage_ratio_text(coverage)} "
            f"（已补全 {coverage.get('filled_count')}，缺失 {coverage.get('missing_count')}）。"
        )
        if runtime not in {"live", "live_snapshot"}:
            subtitle = (
                f"期限覆盖率 {_coverage_ratio_text(coverage)} "
                f"（已补全 {coverage.get('filled_count')}，缺失 {coverage.get('missing_count')}）。"
            )

    source_rows = [
        {"source": source, "count": count}
        for source, count in sorted(source_counts.items(), key=lambda item: (-item[1], str(item[0])))
    ]
    return {
        "title": title,
        "subtitle": subtitle,
        "coverage_ratio": ratio,
        "coverage_text": _coverage_ratio_text(coverage),
        "filled_count": coverage.get("filled_count"),
        "missing_count": coverage.get("missing_count"),
        "unmatched_count": coverage.get("unmatched_count", coverage.get("missing_count")),
        "source_rows": source_rows,
        "unmatched_preview": unmatched[:10],
        "note": _maturity_honesty_note(data_source, lang),
    }


def _maturity_honesty_note(data_source: dict, lang: str) -> str:
    coverage = data_source.get("maturity_coverage") or {}
    ratio = coverage.get("coverage_ratio")
    ratio_text = _coverage_ratio_text(coverage)
    runtime = data_source.get("runtime_mode") or ""
    filled = coverage.get("filled_count")
    missing = coverage.get("missing_count")
    source_counts = coverage.get("source_counts") or {}
    native_count = int(source_counts.get("chinamoney_term_to_maturity") or 0)
    if runtime in {"live", "live_snapshot"}:
        if native_count > 0:
            if lang == "en":
                return (
                    f"Native ChinaMoney residual maturity covers most live rows. "
                    f"Coverage {ratio_text} ({filled} filled / {missing} missing; "
                    f"{native_count} native). Unmatched names still weaken peer buckets."
                )
            return (
                f"中国货币网原生待偿期已覆盖大部分实时样本。"
                f"当前覆盖率 {ratio_text}（已填 {filled}，缺失 {missing}；"
                f"原生 {native_count}）。未匹配简称的同业分桶仍会变弱。"
            )
        if lang == "en":
            return (
                f"Live/snapshot feed is missing residual maturity on many rows. "
                f"Enrichment coverage {ratio_text} ({filled} filled / {missing} missing). "
                "Peer buckets are weaker for unmatched names."
            )
        return (
            f"实时/快照源大量缺失待偿期。当前补全覆盖率 {ratio_text}"
            f"（已补全 {filled}，缺失 {missing}）。"
            "未匹配简称的债券同业分桶会变弱。"
        )
    if ratio is not None and float(ratio) < 0.95:
        if lang == "en":
            return f"Maturity coverage {ratio_text}; some peer and bucket analytics are incomplete."
        return f"期限覆盖率 {ratio_text}；部分同业/分桶分析不完整。"
    if lang == "en":
        return f"Maturity coverage {ratio_text}."
    return f"期限覆盖率 {ratio_text}。"


def _display_maturity(record: dict) -> str:
    maturity = record.get("待偿期")
    if maturity is not None and str(maturity).strip():
        return str(maturity)
    return "当前数据源暂缺"


def _format_fetched_at(value: object, lang: str = "zh") -> str:
    if value in (None, ""):
        return "N/A" if lang == "en" else "无（本地样本）"
    text = str(value).strip()
    if text.endswith("+00:00"):
        text = text[:-6] + "Z"
    return text


def _build_data_lineage_view(data_source: dict, lang: str = "zh") -> dict:
    runtime = data_source.get("runtime_mode") or "unknown"
    fetched_at = data_source.get("fetched_at")
    fallback_reason = data_source.get("fallback_reason")
    source_name = data_source.get("source_name") or "unknown"
    storage = data_source.get("storage")
    active_live = bool(data_source.get("active_live_feed"))
    active_snapshot = bool(data_source.get("active_live_snapshot"))

    if lang == "en":
        if active_live:
            freshness = "Fetched on this request (live)."
        elif active_snapshot:
            freshness = "Using cached live snapshot from the last successful fetch."
        elif runtime in {"static_sample", "static_fallback"}:
            freshness = "Repository static sample; not a live market clock."
        else:
            freshness = "Freshness depends on the active data mode."
        mode_explain = {
            "live": "Live ChinaMoney/AkShare-compatible spot deal feed.",
            "live_snapshot": "Local snapshot because live fetch failed or timed out.",
            "static_sample": "Committed Excel sample for reproducible demos.",
            "static_fallback": "Static Excel used after live and snapshot both failed.",
        }.get(str(runtime), f"Runtime mode: {runtime}")
        lines = [
            f"Mode: {_localized_status(runtime, 'en')}",
            f"Source: {source_name}",
            f"Fetched at: {_format_fetched_at(fetched_at, 'en')}",
            f"Rows: {data_source.get('row_count')}",
            freshness,
            mode_explain,
        ]
        if fallback_reason:
            lines.append(f"Fallback reason: {fallback_reason}")
        if storage:
            lines.append(f"Storage: {storage}")
        return {
            "title": "Data freshness",
            "runtime_label": _localized_status(runtime, "en"),
            "fetched_at_label": _format_fetched_at(fetched_at, "en"),
            "freshness": freshness,
            "lines": lines,
            "fallback_reason": fallback_reason,
        }

    if active_live:
        freshness = "本次请求实时拉取（live）。"
    elif active_snapshot:
        freshness = "实时拉取失败，使用最近一次成功缓存的 live 快照。"
    elif runtime in {"static_sample", "static_fallback"}:
        freshness = "仓库内静态样本，不是实时行情时钟。"
    else:
        freshness = "新鲜度取决于当前数据模式。"
    mode_explain = {
        "live": "中国货币网现券成交（保留原生待偿期）。",
        "live_snapshot": "本地 live 快照（因实时失败或超时）。",
        "static_sample": "仓库 Excel 样本，便于复现演示。",
        "static_fallback": "实时与快照都失败后的静态兜底。",
    }.get(str(runtime), f"运行模式：{runtime}")
    lines = [
        f"模式：{_localized_status(runtime, 'zh')}",
        f"来源：{source_name}",
        f"获取时间：{_format_fetched_at(fetched_at, 'zh')}",
        f"样本行数：{data_source.get('row_count')}",
        freshness,
        mode_explain,
    ]
    if fallback_reason:
        lines.append(f"降级原因：{fallback_reason}")
    if storage:
        lines.append(f"存储：{storage}")
    return {
        "title": "数据新鲜度",
        "runtime_label": _localized_status(runtime, "zh"),
        "fetched_at_label": _format_fetched_at(fetched_at, "zh"),
        "freshness": freshness,
        "lines": lines,
        "fallback_reason": fallback_reason,
    }


def _build_answer_provenance_view(result: dict, lang: str = "zh") -> dict:
    """Explain why the final answer is LLM or deterministic fallback."""
    final_source = result.get("final_answer_source") or "unknown"
    llm_status = result.get("llm_status")
    llm_error = result.get("llm_error")
    guardrail = result.get("llm_guardrail") or {}
    unsupported = guardrail.get("unsupported_numbers") or []
    unsafe = guardrail.get("unsafe_phrases") or []
    used_final = bool(result.get("used_llm_in_final"))

    if used_final and final_source == "llm":
        headline_zh = "最终答案采用 LLM 叙述（护栏已通过）。"
        headline_en = "Final answer uses the LLM narrative (guardrail passed)."
    elif llm_status == "disabled" and llm_error == "advisory_policy_block":
        headline_zh = "投资建议类问题被政策拦截；仅返回确定性拒绝说明，不调用 LLM。"
        headline_en = "Investment-advice request blocked by policy; deterministic refusal only."
    elif llm_status in {None, "disabled"} and not llm_error:
        headline_zh = "本次未启用 LLM；最终答案来自确定性报告。"
        headline_en = "LLM not enabled for this run; deterministic report is the final answer."
    elif guardrail.get("status") == "failed":
        headline_zh = "LLM 已生成文本，但护栏未通过；页面回退到规则报告。"
        headline_en = "LLM drafted text but guardrail rejected it; page fell back to the rule report."
    elif llm_status == "failed":
        headline_zh = "LLM 调用失败；页面回退到确定性报告。"
        headline_en = "LLM call failed; page fell back to the deterministic report."
    else:
        headline_zh = "最终答案来自确定性路径。"
        headline_en = "Final answer comes from the deterministic path."

    lines_zh = [
        f"最终来源：{_localized_status(final_source, 'zh')}",
        f"LLM 状态：{_localized_status(llm_status, 'zh')}",
        f"护栏：{_localized_status(guardrail.get('status'), 'zh')}",
    ]
    lines_en = [
        f"Final source: {_localized_status(final_source, 'en')}",
        f"LLM status: {_localized_status(llm_status, 'en')}",
        f"Guardrail: {_localized_status(guardrail.get('status'), 'en')}",
    ]
    if llm_error:
        lines_zh.append(f"LLM 错误/拦截原因：{llm_error}")
        lines_en.append(f"LLM error / block reason: {llm_error}")
    for item in unsupported[:8]:
        text_item = item.get("text") if isinstance(item, dict) else item
        lines_zh.append(f"未被证据支持的数字：{text_item}")
        lines_en.append(f"Unsupported number: {text_item}")
    for item in unsafe[:8]:
        text_item = item.get("text") if isinstance(item, dict) else item
        lines_zh.append(f"不安全风险语言：{text_item}")
        lines_en.append(f"Unsafe phrase: {text_item}")
    summary_zh = _llm_guardrail_summary(guardrail, "zh")
    if summary_zh:
        lines_zh.append(summary_zh)
    if guardrail.get("summary"):
        lines_en.append(guardrail.get("summary"))

    headline = headline_en if lang == "en" else headline_zh
    lines = lines_en if lang == "en" else lines_zh
    paired = []
    for i in range(max(len(lines_zh), len(lines_en))):
        paired.append({
            "zh": lines_zh[i] if i < len(lines_zh) else "",
            "en": lines_en[i] if i < len(lines_en) else "",
        })
    return {
        "title": "Answer provenance" if lang == "en" else "答案来源说明",
        "title_zh": "答案来源说明",
        "title_en": "Answer provenance",
        "headline": headline,
        "headline_zh": headline_zh,
        "headline_en": headline_en,
        "lines": lines,
        "line_items": paired,
        "tone": "good" if used_final else "warn" if final_source == "deterministic_fallback" else "",
        "final_source": final_source,
        "llm_status": llm_status,
        "guardrail_status": guardrail.get("status"),
        "used_llm_in_final": used_final,
    }


def _data_source_subtitle(data_source: dict, lang: str) -> str:
    fetched = _format_fetched_at(data_source.get("fetched_at"), lang)
    if lang == "en":
        base = (
            f"{data_source.get('source_name')} · {data_source.get('runtime_mode')} · "
            f"{data_source.get('row_count')} rows · fetched {fetched}"
        )
        if data_source.get("fallback_reason"):
            return f"{base} · fallback active"
        return base
    base = (
        f"{data_source.get('source_name')} · "
        f"{_localized_status(data_source.get('runtime_mode'), 'zh')} · "
        f"{data_source.get('row_count')} 行 · 获取 {fetched}"
    )
    if data_source.get("fallback_reason"):
        return f"{base} · 已降级"
    return base


if __name__ == "__main__":
    # threaded=True is required for local demos: the agent form is synchronous and
    # LLM/live fetches can take tens of seconds. Without threading, a single query
    # freezes health checks and other page loads, which looks like a hard hang.
    app.run(
        host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_ENV") == "development",
        threaded=os.environ.get("FLASK_THREADED", "1").lower() not in {"0", "false", "no"},
    )
