from __future__ import annotations

import contextlib
import csv
import io
import json
import math
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
from bond_agent.agent import LIMITATIONS_TEMPLATE as _LLM_LIMITATIONS
from bond_agent.data_loader import (
    BOND_CODE,
    MATURITY_YEARS,
    PRICE,
    VOLUME,
    YIELD,
    LiveDataUnavailable,
    records_from_frame,
    resolve_bond_data,
)
from bond_agent.evidence_pack import DEFAULT_PACK_DIR, DEMO_PACK_DIR
from bond_agent.i18n import FIELD_LABELS, INTENT_LABELS, RISK_TRANSLATIONS, TOOL_LABELS
from bond_agent.importer import (
    SUPPORTED_EXTENSIONS,
    _derive_quote_date,
    describe_import_source,
    prepare_import_frame,
    read_table,
)
from bond_agent.replay_store import list_replays, load_replay
from bond_agent.schemas import AgentQueryRequest, ApiError, HealthResponse, api_schema_bundle
from bond_agent.taxonomy import annotate_frame
from bond_agent.tools import compare_bond_to_market, describe_market, search_bonds

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
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

# On-demand AI interpretation: one in-flight explain call per target, so a
# double click cannot trigger duplicate billed model calls.
_EXPLAIN_INFLIGHT: set[str] = set()
_EXPLAIN_INFLIGHT_LOCK = Lock()

# Successful bond-path (no result_id) interpretations are cached by target
# key so a repeat click on the same bond page reuses the earlier call instead
# of billing the model twice. Same lifetime policy as the result cache;
# failures are never cached and an explicit retry is always allowed.
_BOND_EXPLAIN_CACHE: OrderedDict[str, dict] = OrderedDict()
_BOND_EXPLAIN_CACHE_LIMIT = 32
_BOND_EXPLAIN_CACHE_TTL_SECONDS = _RESULT_CACHE_TTL_SECONDS
_BOND_EXPLAIN_CACHE_LOCK = Lock()

# Uploaded tables (import mode). Kept in memory only; files land in a temp dir
# and are deleted right after parsing — never committed to the repository.
_IMPORT_STORE: OrderedDict[str, dict] = OrderedDict()
_IMPORT_STORE_LIMIT = int(os.environ.get("BOND_IMPORT_STORE_LIMIT", "8"))
_IMPORT_STORE_TTL_SECONDS = float(os.environ.get("BOND_IMPORT_STORE_TTL_SECONDS", "3600"))
_IMPORT_STORE_LOCK = Lock()
_UPLOAD_DIR = Path(os.environ.get("BOND_UPLOAD_DIR", ".tmp/uploads"))


def _store_import(frame, source: dict) -> str:
    import_id = uuid4().hex
    now = _wall_time()
    with _IMPORT_STORE_LOCK:
        while len(_IMPORT_STORE) >= _IMPORT_STORE_LIMIT:
            _IMPORT_STORE.popitem(last=False)
        _IMPORT_STORE[import_id] = {"frame": frame, "source": source, "stored_at": now}
    return import_id


def _load_import(import_id: str):
    if not import_id:
        return None
    now = _wall_time()
    with _IMPORT_STORE_LOCK:
        entry = _IMPORT_STORE.get(import_id)
        if entry is None:
            return None
        if now - entry["stored_at"] > _IMPORT_STORE_TTL_SECONDS:
            _IMPORT_STORE.pop(import_id, None)
            return None
        _IMPORT_STORE.move_to_end(import_id)
        return entry


def _parse_column_overrides(raw: str | None) -> dict[str, str] | None:
    """Parse the optional column_overrides form field (JSON: canonical -> source).

    Returns None when absent; raises ValueError with a bilingual message on
    malformed JSON or non-object payloads so the caller can answer with 400.
    """
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ValueError(
            "column_overrides 必须是 JSON 对象（字段→源列名）。/ "
            "column_overrides must be a JSON object (field -> source column)."
        )
    if not isinstance(parsed, dict):
        raise ValueError(
            "column_overrides 必须是 JSON 对象（字段→源列名）。/ "
            "column_overrides must be a JSON object (field -> source column)."
        )
    return {str(key): str(value) for key, value in parsed.items()}


def _import_error_response(import_id: str, lang: str = "zh"):
    if lang == "en":
        return jsonify(ApiError(error="Uploaded dataset expired or unknown; please upload the file again.", import_id=import_id).model_dump(mode="json", exclude_none=True)), 400
    return jsonify(ApiError(error="上传数据已过期或不存在，请重新上传文件。", import_id=import_id).model_dump(mode="json", exclude_none=True)), 400


def _resolve_import_request(payload: dict | None):
    """Extract (frame, source, import_id) for a query request; error tuple if invalid."""
    import_id = str((payload or {}).get("import_id") or "").strip()
    if not import_id:
        return None
    entry = _load_import(import_id)
    if entry is None:
        return ("error", import_id)
    return entry["frame"], entry["source"], import_id


@app.route("/api/import", methods=["POST"])
def import_dataset():
    """Upload a local CSV/XLS(X) snapshot for analysis.

    The file is parsed immediately and deleted; only the parsed table stays in
    process memory. Column mapping, missing fields, and disabled analyses are
    reported honestly. Same-name rows are never deduplicated.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify(ApiError(error="请选择要上传的 CSV/XLSX 文件。/ Choose a CSV/XLSX file to upload.").model_dump(mode="json", exclude_none=True)), 400
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        return jsonify(ApiError(error=f"不支持的文件类型 {suffix or '(none)'}；仅支持 CSV/XLSX/XLS。/ Unsupported file type; use CSV, XLSX or XLS.").model_dump(mode="json", exclude_none=True)), 400
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = _UPLOAD_DIR / f"{uuid4().hex}{suffix}"
    try:
        upload.save(temp_path)
        raw = read_table(temp_path)
    except ValueError as exc:
        return jsonify(ApiError(error=str(exc)).model_dump(mode="json", exclude_none=True)), 400
    except Exception as exc:  # noqa: BLE001 - unreadable file (corrupt xlsx, wrong encoding…)
        return jsonify(ApiError(error=f"文件解析失败：{exc} / Failed to parse the uploaded file.").model_dump(mode="json", exclude_none=True)), 400
    finally:
        with contextlib.suppress(OSError):
            temp_path.unlink(missing_ok=True)
    try:
        overrides = _parse_column_overrides(request.form.get("column_overrides"))
    except ValueError as exc:
        return jsonify(ApiError(error=str(exc)).model_dump(mode="json", exclude_none=True)), 400
    try:
        frame, report = prepare_import_frame(
            raw, file_name=Path(upload.filename).name, column_overrides=overrides
        )
    except ValueError as exc:
        return jsonify(ApiError(error=str(exc)).model_dump(mode="json", exclude_none=True)), 400
    source = describe_import_source(frame, report)
    source["source_name"] = "uploaded dataset"
    import_id = _store_import(frame, source)
    preview = records_from_frame(frame.head(10))
    return jsonify({
        "import_id": import_id,
        "report": report,
        "source": source,
        "preview": preview,
    })




def _request_payload():
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not isinstance(payload, dict):
        return None, (jsonify(error="Request body must be a valid JSON object."), 400)
    return payload, None


@app.errorhandler(404)
def page_not_found(error):
    # Preserve the existing HTTP exception response for API and file routes.
    if request.path.startswith(("/api/", "/packs/", "/static/")):
        return error
    return _with_language_cookie(render_template("404.html"), _resolve_language()), 404


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="Request exceeds the 1 MiB limit."), 413


@app.route("/")
def index():
    # The research workbench is the product home; the agent console stays at /agent.
    return redirect(url_for("market_page"))


@app.route("/agen")
def agent_page_typo_redirect():
    """Redirect the common missing-`t` demo URL typo back to the agent page."""
    target = url_for("agent_page")
    if request.query_string:
        target = f"{target}?{request.query_string.decode('utf-8', errors='ignore')}"
    return redirect(target)


@app.context_processor
def inject_language_context():
    return {"current_lang": _resolve_language()}


@app.route("/healthz")
def healthz():
    response = HealthResponse(status="ok", service="BondLens", checks={"app": "ok"})
    return jsonify(response.model_dump(mode="json"))


@app.errorhandler(LiveDataUnavailable)
def handle_live_unavailable(exc):
    if request.path.startswith("/api/"):
        return jsonify(ApiError(error=str(exc)).model_dump(mode="json", exclude_none=True)), 502
    return str(exc), 502


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
        try:
            query = AgentQueryRequest.model_validate(request.form.to_dict())
            question = query.question
        except ValidationError:
            form_error = "问题不能超过 4000 字。" if lang == "zh" else "Keep your question within 4,000 characters."
        if not form_error:
            import_entry = _resolve_import_request(request.form.to_dict())
            # Error marker is ("error", import_id) — exactly 2 items; a valid
            # resolution is a 3-item (frame, source, import_id) tuple. Length
            # check avoids boolean-comparing the frame (a DataFrame).
            if isinstance(import_entry, tuple) and len(import_entry) == 2:
                form_error = "上传数据已过期或不存在，请重新上传文件。" if lang == "zh" else "Uploaded dataset expired; please upload again."
            else:
                if import_entry is None:
                    frame_override = None
                    source_override = None
                else:
                    frame_override, source_override, _ = import_entry
                result = _get_agent(data_mode).answer(
                    question, data_frame=frame_override, data_source=source_override
                )
                result_id = _store_result(result, question=question, data_mode=data_mode)
                view = _build_agent_view_model(result, lang=lang)
    else:
        # Async form path: after /api/agent/query, browser reloads with result_id to render full HTML once.
        cached_id = (request.args.get("result_id") or "").strip()
        cached = _load_result(cached_id) if cached_id else None
        if cached_id and cached is None:
            form_error = "结果已过期或服务已重启，请重新分析。" if lang == "zh" else "This result expired or the service restarted. Run the analysis again."
        if cached:
            result = cached.get("result")
            question = cached.get("question") or ""
            data_mode = cached.get("data_mode") or data_mode
            result_id = cached_id
            view = _build_agent_view_model(result, lang=lang)
        else:
            # Quick-ask prefill from the workbench pages (/agent?question=…).
            question = (request.args.get("question") or "").strip()
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
    payload, error = _request_payload()
    if error:
        return error
    try:
        query = AgentQueryRequest.model_validate(payload)
    except ValidationError as exc:
        return jsonify(ApiError(error="Invalid agent query request.", details=exc.errors(include_context=False)).model_dump(mode="json")), 400
    question = query.question
    try:
        data_mode = _normalize_data_mode(query.data_mode or request.form.get("data_mode") or os.environ.get("BOND_DATA_MODE", "auto"))
    except ValueError as exc:
        error = ApiError(error=str(exc), allowed_data_modes=sorted(DATA_MODES))
        return jsonify(error.model_dump(mode="json", exclude_none=True)), 400
    lang = _resolve_language(payload.get("lang") if isinstance(payload, dict) else None)
    import_entry = _resolve_import_request(payload)
    # Error marker is ("error", import_id) — exactly 2 items; a valid
    # resolution is a 3-item (frame, source, import_id) tuple. Length check
    # avoids boolean-comparing the frame (a DataFrame).
    if isinstance(import_entry, tuple) and len(import_entry) == 2:
        return _import_error_response(import_entry[1], lang)
    if import_entry is None:
        frame_override = None
        source_override = None
    else:
        frame_override, source_override, _ = import_entry
    result = _get_agent(data_mode).answer(
        question, data_frame=frame_override, data_source=source_override
    )
    result_id = _store_result(result, question=question, data_mode=data_mode)
    include_view = bool(payload.get("include_view")) if isinstance(payload, dict) else False
    body = dict(result)
    body["result_id"] = result_id
    body["result_url"] = url_for("agent_page", result_id=result_id, lang=lang, data_mode=data_mode, import_id=(import_entry[2] if import_entry else None))
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
    payload, error = _request_payload()
    if error:
        return error
    try:
        query = AgentQueryRequest.model_validate(payload)
    except ValidationError as exc:
        return jsonify(ApiError(error="Invalid agent query request.", details=exc.errors(include_context=False)).model_dump(mode="json")), 400
    question = query.question
    try:
        data_mode = _normalize_data_mode(query.data_mode or request.form.get("data_mode") or os.environ.get("BOND_DATA_MODE", "auto"))
    except ValueError as exc:
        error = ApiError(error=str(exc), allowed_data_modes=sorted(DATA_MODES))
        return jsonify(error.model_dump(mode="json", exclude_none=True)), 400
    lang = _resolve_language(payload.get("lang") if isinstance(payload, dict) else None)
    import_entry = _resolve_import_request(payload)
    # Error marker is ("error", import_id) — exactly 2 items; a valid
    # resolution is a 3-item (frame, source, import_id) tuple. Length check
    # avoids boolean-comparing the frame (a DataFrame).
    if isinstance(import_entry, tuple) and len(import_entry) == 2:
        return _import_error_response(import_entry[1], lang)
    if import_entry is None:
        frame_override = None
        source_override = None
    else:
        frame_override, source_override, _ = import_entry

    def generate():
        import json as _json
        agent = _get_agent(data_mode)
        try:
            for event in agent.iter_answer_events(
                question, data_frame=frame_override, data_source=source_override
            ):
                etype = event.get("type") or "status"
                if etype == "final":
                    result = event.get("result") or {}
                    result_id = _store_result(result, question=question, data_mode=data_mode)
                    view = _build_agent_view_model(result, lang=lang)
                    body = {
                        "type": "final",
                        "result_id": result_id,
                        "result_url": url_for("agent_page", result_id=result_id, lang=lang, data_mode=data_mode, import_id=(import_entry[2] if import_entry else None)),
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
        except LiveDataUnavailable as exc:
            body = {"type": "error", "error": str(exc)}
            yield f"event: error\ndata: {_json.dumps(body, ensure_ascii=False)}\n\n"
        except Exception:  # Log failures without leaking server details.
            app.logger.exception("Agent stream failed")
            body = {"type": "error", "error": "Analysis failed. Please retry or use local sample mode."}
            yield f"event: error\ndata: {_json.dumps(body, ensure_ascii=False)}\n\n"

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@app.route("/api/agent/schema")
def agent_schema():
    return jsonify(api_schema_bundle())


def _explain_in_progress(lock_key: str) -> bool:
    with _EXPLAIN_INFLIGHT_LOCK:
        if lock_key in _EXPLAIN_INFLIGHT:
            return True
        _EXPLAIN_INFLIGHT.add(lock_key)
        return False


def _explain_done(lock_key: str) -> None:
    with _EXPLAIN_INFLIGHT_LOCK:
        _EXPLAIN_INFLIGHT.discard(lock_key)


def _cached_explanation_response(cached: dict):
    """Return a stored successful interpretation without touching the model."""
    return jsonify(
        _json_safe(
            {
                "status": "success",
                "text": cached.get("text"),
                "model": cached.get("model"),
                "guardrail_status": cached.get("guardrail_status"),
                "generated_at": cached.get("generated_at"),
                "cached": True,
                "message_zh": "已返回此前生成的 AI 解读（未再次调用模型）。",
                "message_en": "Returning the previously generated AI interpretation (no new model call).",
            }
        )
    )


def _cached_bond_explanation(lock_key: str) -> dict | None:
    if not lock_key:
        return None
    now = _wall_time()
    with _BOND_EXPLAIN_CACHE_LOCK:
        entry = _BOND_EXPLAIN_CACHE.get(lock_key)
        if entry is None:
            return None
        if _BOND_EXPLAIN_CACHE_TTL_SECONDS > 0 and now - float(entry.get("stored_at") or 0.0) > _BOND_EXPLAIN_CACHE_TTL_SECONDS:
            _BOND_EXPLAIN_CACHE.pop(lock_key, None)
            return None
        _BOND_EXPLAIN_CACHE.move_to_end(lock_key)
        return entry


def _remember_bond_explanation(lock_key: str, explanation: dict) -> None:
    if not lock_key or not isinstance(explanation, dict):
        return
    with _BOND_EXPLAIN_CACHE_LOCK:
        _BOND_EXPLAIN_CACHE[lock_key] = {
            "text": explanation.get("text"),
            "model": explanation.get("model"),
            "guardrail_status": explanation.get("guardrail_status"),
            "generated_at": _wall_time(),
            "stored_at": _wall_time(),
        }
        _BOND_EXPLAIN_CACHE.move_to_end(lock_key)
        while len(_BOND_EXPLAIN_CACHE) > max(_BOND_EXPLAIN_CACHE_LIMIT, 1):
            _BOND_EXPLAIN_CACHE.popitem(last=False)


@app.route("/api/agent/explain", methods=["POST"])
def agent_explain():
    """On-demand AI interpretation of an already-computed research result.

    Explicit user action only — never triggered by page loads or the normal
    query flow. It reuses the stored deterministic result (result_id) or the
    deterministic bond-comparison computation (code/name + data_mode):
    no tool re-execution, no data re-fetch, no invented numbers. One
    in-flight call per target so double clicks cannot double-bill (409
    in_progress). A successful interpretation is attached to the cached
    result so reloading the page does not re-bill. Interpretations derived
    from an uploaded table require confirm_send=true after the client has
    shown what will be shared with the external model.
    """
    payload, error = _request_payload()
    if error:
        return error
    if not isinstance(payload, dict):
        payload = {}
    lang = _resolve_language(payload.get("lang"))
    # Privacy consent must be the JSON boolean true — 'true'/'false' strings,
    # numbers, null or missing values never count as confirmation.
    confirm_send = payload.get("confirm_send") is True
    result_id = str(payload.get("result_id") or "").strip()
    code = str(payload.get("code") or "").strip() or None
    name = str(payload.get("name") or "").strip() or None
    bond_key = str(payload.get("bond_key") or "").strip()
    if bond_key and not code and not name:
        code = bond_key
    if not result_id and not code and not name:
        return (
            jsonify(
                ApiError(
                    error="请提供 result_id 或债券标识（code/name）。/ Provide a result_id or a bond identifier (code/name)."
                ).model_dump(mode="json")
            ),
            400,
        )

    if result_id:
        entry = _load_result(result_id)
        if entry is None:
            return (
                jsonify(
                    ApiError(
                        error="结果已过期或不存在，无法生成 AI 解读；确定性结果不受影响。/ This result expired or is unknown; AI interpretation is unavailable. The deterministic result is unaffected."
                    ).model_dump(mode="json")
                ),
                410,
            )
        result = entry.get("result") or {}
        question = (entry.get("question") or "").strip() or (
            "请基于已计算的研究结果进行解读。/ Interpret the already-computed research result."
        )
        plan = result.get("plan") or {}
        report = {
            "data_evidence": result.get("data_evidence") or {},
            "data_source": result.get("data_source") or {},
            "analysis": result.get("analysis") or {},
            "risk_notes": result.get("risk_notes") or [],
            "limitations": result.get("limitations") or [],
            "tools_used": result.get("tools_used") or [],
        }
        imported = (result.get("data_source") or {}).get("runtime_mode") == "import"
        data_mode = entry.get("data_mode") or payload.get("data_mode") or os.environ.get("BOND_DATA_MODE", "auto")
        lock_key = f"result:{result_id}"
        cached_expl = result.get("ai_explanation") if isinstance(result, dict) else None
        if isinstance(cached_expl, dict) and cached_expl.get("text"):
            # Server-side idempotency: a stored successful interpretation is
            # returned as-is — never a second billed model call for the same
            # result, no matter how often the client re-submits.
            return _cached_explanation_response(cached_expl)
    else:
        values = {
            "data_mode": payload.get("data_mode") or os.environ.get("BOND_DATA_MODE", "auto"),
            "import_id": str(payload.get("import_id") or "").strip(),
            "lang": lang,
        }
        frame, source, resolved_mode, error = _resolve_workbench_data(values)
        if error:
            return error
        try:
            active_frame, active_source = _resolve_active_market(frame, source, resolved_mode)
        except Exception as exc:  # noqa: BLE001 - data-source boundary -> honest structured error
            return _market_resolution_error(exc)
        search_result = search_bonds(code=code, name=name, limit=50, data_frame=active_frame)
        if search_result["match_count"] == 0:
            message = (
                f"当前样本中未找到「{name or code}」。"
                if lang == "zh"
                else f"No bond matching '{name or code}' in the active sample."
            )
            return (jsonify(ApiError(error=message).model_dump(mode="json")), 404)
        target = search_result["records"][0]
        comparison = compare_bond_to_market(record=target, data_frame=active_frame)
        display_name = str(target.get("债券简称") or name or code or "")
        question = (
            f"请解读债券「{display_name}」与同业及市场的确定性对比结果，不要重算数据，不要给出投资建议。"
            if lang == "zh"
            else f"Interpret the deterministic peer/market comparison of bond '{display_name}'; do not recompute data and do not give investment advice."
        )
        plan = {
            "intent": "bond_comparison",
            "search_params": {"code": code or "", "name": name or ""},
            "requested_tools": ["search_bonds", "compare_bond_to_market"],
        }
        report = {
            "data_evidence": {"search": search_result, "comparison": comparison},
            "data_source": active_source,
            "analysis": {},
            "risk_notes": [],
            "limitations": list(_LLM_LIMITATIONS),
            "tools_used": ["search_bonds", "compare_bond_to_market"],
        }
        imported = (active_source or {}).get("runtime_mode") == "import"
        data_mode = resolved_mode
        lock_key = "bond:" + "|".join(
            part for part in (str(code or ""), str(name or ""), str(resolved_mode), str(values["import_id"])) if part
        )
        cached_expl = _cached_bond_explanation(lock_key)
        if cached_expl:
            # Same idempotency for the no-result_id bond path: a repeat click
            # on the same bond page reuses the earlier successful call.
            return _cached_explanation_response(cached_expl)

    if imported and not confirm_send:
        return jsonify(
            _json_safe(
                {
                    "status": "needs_confirmation",
                    "message_zh": "将把该研究的结构化摘要（含少量样本记录，不含完整上传表格）发送给外部模型生成解读。确认继续？",
                    "message_en": "This will send the structured summary of the study (a few sample records, never the full uploaded table) to the external model. Continue?",
                }
            )
        )
    if _explain_in_progress(lock_key):
        return (
            jsonify(
                _json_safe(
                    {
                        "status": "in_progress",
                        "message_zh": "同一目标的 AI 解读正在生成中，请稍候。",
                        "message_en": "An AI interpretation for this target is already in progress; please wait.",
                    }
                )
            ),
            409,
        )
    try:
        explanation = _get_agent(data_mode).explain_result(question, plan, report)
    finally:
        _explain_done(lock_key)
    if explanation.get("status") == "success":
        if result_id:
            # Attach the interpretation to the cached entry so a reload reuses it
            # without a second billed call; the deterministic numbers are untouched.
            with _RESULT_CACHE_LOCK:
                entry = _RESULT_CACHE.get(result_id)
                if entry is not None and isinstance(entry.get("result"), dict):
                    entry["result"]["ai_explanation"] = {
                        "text": explanation.get("text"),
                        "model": explanation.get("model"),
                        "guardrail_status": explanation.get("guardrail_status"),
                        "generated_at": _wall_time(),
                    }
        else:
            # Bond path: remember the successful interpretation so repeat clicks
            # on the same bond page do not bill the model twice.
            _remember_bond_explanation(lock_key, explanation)
    body = dict(explanation)
    body["status"] = explanation.get("status") or "failed"
    return jsonify(_json_safe(body))



# ---------------------------------------------------------------------------
# Research workbench (read-only): market observation, screener, single-bond study
# ---------------------------------------------------------------------------


def _resolve_workbench_data(values):
    """Resolve (frame, source, data_mode, error) for read-only workbench requests.

    ``values`` is a mapping of query/form parameters. An explicit ``import_id``
    overrides the configured data_mode with the uploaded snapshot; a stale or
    unknown import_id yields an error response ready to return. All analysis
    on these paths is read-only: nothing mutates the frame or the cache.
    """
    raw_mode = (values.get("data_mode") or os.environ.get("BOND_DATA_MODE", "auto")).strip() or "auto"
    try:
        data_mode = _normalize_data_mode(raw_mode)
    except ValueError as exc:
        error = ApiError(error=str(exc), allowed_data_modes=sorted(DATA_MODES))
        return None, None, None, (jsonify(error.model_dump(mode="json", exclude_none=True)), 400)
    import_entry = _resolve_import_request(values)
    # Error marker is ("error", import_id) — exactly 2 items; a valid
    # resolution is a 3-item (frame, source, import_id) tuple.
    if isinstance(import_entry, tuple) and len(import_entry) == 2:
        return None, None, None, _import_error_response(import_entry[1], _resolve_language(values.get("lang")))
    if import_entry is None:
        return None, None, data_mode, None
    frame, source, _ = import_entry
    return frame, source, data_mode, None


def _resolve_active_market(frame, source, data_mode):
    """Resolve the active (frame, source_profile) honoring imports and live fallback."""
    if frame is not None:
        return frame, source
    agent = _get_agent(data_mode)
    resolved_frame, resolved_source = resolve_bond_data(
        mode=agent.data_mode,
        path=agent.data_path or None,
        live_fetcher=agent.live_fetcher,
    )
    # Honest date caliber for every workbench endpoint: derive from the actual
    # frame rather than trusting the profile (imports already carry it).
    resolved_source = dict(resolved_source)
    if "mixed_timepoints" not in resolved_source:
        for key, value in _derive_quote_date(resolved_frame).items():
            resolved_source.setdefault(key, value)
    return resolved_frame, resolved_source


def _market_resolution_error(exc: Exception):
    if isinstance(exc, LiveDataUnavailable):
        return jsonify(ApiError(error=str(exc)).model_dump(mode="json", exclude_none=True)), 502
    app.logger.exception("Workbench data resolution failed")
    return (
        jsonify(
            ApiError(
                error=f"市场数据加载失败：{exc} / Failed to load the market snapshot."
            ).model_dump(mode="json", exclude_none=True)
        ),
        502,
    )


def _json_safe(value):
    """NaN/Inf 不是合法 JSON：浏览器 JSON.parse 会抛错，统一净化为 None。"""
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _query_float(args, key: str) -> float | None:
    raw = (args.get(key) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"参数 {key} 必须是数字。/ Parameter {key} must be numeric.")


@app.route("/market")
def market_page():
    """市场观察：数据身份、样本结构、口径一致的分桶统计。"""
    lang = _resolve_language()
    data_mode, _mode_error = _resolve_page_data_mode(
        request.values.get("data_mode", os.environ.get("BOND_DATA_MODE", "auto"))
    )
    html = render_template("market.html", lang=lang, data_mode=data_mode)
    return _with_language_cookie(html, lang)


@app.route("/screen")
def screen_page():
    """债券筛选：可过滤、排序、分级的表格，行点击进入单券研究。"""
    lang = _resolve_language()
    data_mode, _mode_error = _resolve_page_data_mode(
        request.values.get("data_mode", os.environ.get("BOND_DATA_MODE", "auto"))
    )
    html = render_template("screen.html", lang=lang, data_mode=data_mode)
    return _with_language_cookie(html, lang)


@app.route("/bond", endpoint="bond_page_index")
@app.route("/bond/<path:bond_key>")
def bond_page(bond_key: str = ""):
    """单券研究：复用 search_bonds / compare_bond_to_market，不做趋势/建议虚构。"""
    lang = _resolve_language()
    data_mode, _mode_error = _resolve_page_data_mode(
        request.values.get("data_mode", os.environ.get("BOND_DATA_MODE", "auto"))
    )
    html = render_template(
        "bond.html", lang=lang, data_mode=data_mode, bond_key=(bond_key or "").strip()
    )
    return _with_language_cookie(html, lang)


@app.route("/api/market/overview")
def api_market_overview():
    """市场观察数据：数据源身份 + 样本结构 + 口径一致的分布统计。"""
    frame, source, data_mode, error = _resolve_workbench_data(request.args)
    if error:
        return error
    try:
        active_frame, active_source = _resolve_active_market(frame, source, data_mode)
        overview = describe_market(
            data_frame=active_frame, runtime_mode=active_source.get("runtime_mode")
        )
    except Exception as exc:  # noqa: BLE001 - data-source boundary -> honest structured error
        return _market_resolution_error(exc)
    overview["data_source"] = active_source
    return jsonify(_json_safe(overview))


@app.route("/api/bonds/search")
def api_bonds_search():
    """债券筛选数据：真实过滤 + 排序 + 分页，全部基于当前活跃样本。"""
    args = request.args
    frame, source, data_mode, error = _resolve_workbench_data(args)
    if error:
        return error
    try:
        filters = {
            "name": (args.get("name") or "").strip() or None,
            "code": (args.get("code") or "").strip() or None,
            "bond_type": (args.get("bond_type") or "").strip() or None,
            "min_maturity": _query_float(args, "min_maturity"),
            "max_maturity": _query_float(args, "max_maturity"),
            "min_yield": _query_float(args, "min_yield"),
            "max_yield": _query_float(args, "max_yield"),
            "min_volume": _query_float(args, "min_volume"),
            "max_volume": _query_float(args, "max_volume"),
        }
    except ValueError as exc:
        return jsonify(ApiError(error=str(exc)).model_dump(mode="json")), 400
    sort_key = (args.get("sort") or "yield").strip().lower()
    sort_keys = {"yield": YIELD, "maturity": MATURITY_YEARS, "volume": VOLUME, "price": PRICE, "name": "债券简称"}
    if sort_key not in sort_keys:
        return (
            jsonify(
                ApiError(
                    error=f"不支持的排序键 {sort_key}。/ Unsupported sort key: {sort_key}."
                ).model_dump(mode="json"))
        ), 400
    direction = (args.get("direction") or "desc").strip().lower()
    if direction not in {"asc", "desc"}:
        return jsonify(ApiError(error="direction 必须是 asc 或 desc。/ direction must be asc or desc.").model_dump(mode="json")), 400
    try:
        page = max(1, int(args.get("page") or 1))
        page_size = min(100, max(1, int(args.get("page_size") or 20)))
    except ValueError:
        return jsonify(ApiError(error="page / page_size 必须是正整数。/ page and page_size must be positive integers.").model_dump(mode="json")), 400
    try:
        active_frame, active_source = _resolve_active_market(frame, source, data_mode)
    except Exception as exc:  # noqa: BLE001 - data-source boundary -> honest structured error
        return _market_resolution_error(exc)

    def _run_search(limit):
        return search_bonds(
            name=filters["name"],
            code=filters["code"],
            bond_type=filters["bond_type"],
            min_maturity=filters["min_maturity"],
            max_maturity=filters["max_maturity"],
            min_yield=filters["min_yield"],
            max_yield=filters["max_yield"],
            min_volume=filters["min_volume"],
            max_volume=filters["max_volume"],
            limit=limit,
            data_frame=active_frame,
        )

    probe = _run_search(1)
    total = int(probe["match_count"])
    fetch_limit = min(total, 5000)
    result = _run_search(fetch_limit) if fetch_limit else probe
    records = result["records"]

    # Server-side sort on the requested key; missing values always sink to the end.
    sort_column = sort_keys[sort_key]

    def _sort_value(record):
        raw = record.get(sort_column)
        if raw is None or raw == "":
            return None
        try:
            return (0, float(raw)) if sort_key != "name" else (0, str(raw))
        except (TypeError, ValueError):
            return None

    present = [r for r in records if _sort_value(r) is not None]
    missing = [r for r in records if _sort_value(r) is None]
    present.sort(key=_sort_value, reverse=(direction == "desc"))
    ordered = present + missing
    page_count = max(1, math.ceil(total / page_size))
    start = (page - 1) * page_size
    page_records = ordered[start : start + page_size]

    lang = _resolve_language(args.get("lang"))
    notes = []
    missing_column_notes = {
        "code_column_missing": "样本缺少「债券代码」列：代码筛选未生效。/ No bond-code column in this sample; the code filter cannot apply.",
        "yield_column_missing": "样本缺少收益率列：收益率区间筛选未生效。/ No yield column in this sample; the yield-range filter cannot apply.",
        "maturity_column_missing": "样本缺少待偿期列：期限区间筛选未生效。/ No maturity column in this sample; the maturity-range filter cannot apply.",
        "volume_column_missing": "样本缺少交易量列：交易量筛选未生效。/ No volume column in this sample; the volume filter cannot apply.",
    }
    for marker, note in missing_column_notes.items():
        if marker in (result.get("matched_by") or []):
            notes.append(note)
    if total > fetch_limit:
        notes.append(
            f"命中 {total} 条，仅返回前 {fetch_limit} 条用于排序分页。/ "
            f"{total} matches; only the first {fetch_limit} feed the sort/page window."
        )

    annotated = annotate_frame(active_frame)
    type_counts = annotated["券种"].value_counts()
    available_filters = {
        "code": BOND_CODE in active_frame.columns,
        "yield": YIELD in active_frame.columns and bool(active_frame[YIELD].notna().any()),
        "maturity": MATURITY_YEARS in active_frame.columns and bool(active_frame[MATURITY_YEARS].notna().any()),
        "volume": VOLUME in active_frame.columns and bool(active_frame[VOLUME].notna().any()),
        "bond_types": [
            {"bond_type": str(label), "count": int(count)}
            for label, count in type_counts.items()
        ],
    }
    return jsonify(
        _json_safe(
            {
                "data_source": active_source,
                "filters": filters,
                "sort": {"key": sort_key, "direction": direction},
                "page": page,
                "page_size": page_size,
                "page_count": page_count,
                "match_count": total,
                "distinct_bond_count": result.get("distinct_bond_count"),
                "matched_by": result.get("matched_by"),
                "ambiguity_note": result.get("ambiguity_note"),
                "available_filters": available_filters,
                "records": _localize_bond_records(page_records, lang),
                "sort_truncated": total > fetch_limit,
                "notes": notes,
            }
        )
    )


@app.route("/api/bond/detail")
def api_bond_detail():
    """单券研究数据：目标券行情 + 同券多行提示 + 同组对比（无虚构趋势/建议）。"""
    args = request.args
    lang = _resolve_language(args.get("lang"))
    key = (args.get("key") or "").strip()
    code = (args.get("code") or "").strip() or None
    name = (args.get("name") or "").strip() or None
    if key and not code and not name:
        # /bond/<key> links resolve code first (precise), then fall back to name.
        code = key
    if not code and not name:
        return (
            jsonify(
                ApiError(
                    error="请提供债券代码或名称。/ Provide a bond code or name."
                ).model_dump(mode="json"))
        ), 400
    frame, source, data_mode, error = _resolve_workbench_data(args)
    if error:
        return error
    try:
        active_frame, active_source = _resolve_active_market(frame, source, data_mode)
    except Exception as exc:  # noqa: BLE001 - data-source boundary -> honest structured error
        return _market_resolution_error(exc)

    search_result = None
    if code:
        search_result = search_bonds(code=code, limit=50, data_frame=active_frame)
        if search_result["match_count"] == 0:
            search_result = None
            if key:
                name = name or key
    if search_result is None:
        if not name:
            return (
                jsonify(
                    ApiError(
                        error=f"未找到代码为「{code}」的债券。/ No bond with code '{code}'."
                    ).model_dump(mode="json"))
            ), 404
        search_result = search_bonds(name=name, limit=50, data_frame=active_frame)
    if search_result["match_count"] == 0:
        message = (
            f"当前样本中未找到「{name or code}」；样本与查询口径可能不同。"
            if lang == "zh"
            else f"No bond matching '{name or code}' in the active sample."
        )
        return (
            jsonify(ApiError(error=message).model_dump(mode="json"))
        ), 404
    records = search_result["records"]
    target = records[0]
    comparison = compare_bond_to_market(record=target, data_frame=active_frame)
    multi_row = len(records) > 1
    notes = []
    if multi_row:
        notes.append(
            f"同一债券有 {len(records)} 行记录（如多期限多只挂牌），对比基于第一行。/ "
            f"{len(records)} rows share this bond; the comparison uses the first row."
        )
    if "code_column_missing" in (search_result.get("matched_by") or []):
        notes.append(
            "样本缺少「债券代码」列，仅按名称定位。/ "
            "No bond-code column in this sample; matched by name only."
        )
    for marker in ("yield_column_missing", "maturity_column_missing", "volume_column_missing"):
        if marker in (search_result.get("matched_by") or []):
            notes.append(
                "样本缺少对应数据列，相关分析不可用。/ "
                "This sample lacks the related column; that analysis is unavailable."
            )
    return jsonify(
        _json_safe(
            {
                "data_source": active_source,
                "search": {
                    "match_count": search_result["match_count"],
                    "matched_by": search_result["matched_by"],
                    "ambiguity_note": search_result.get("ambiguity_note"),
                },
                "target": _localize_bond_records([target], lang)[0],
                "records": _localize_bond_records(records, lang),
                "multi_row": multi_row,
                "comparison": comparison,
                "notes": notes,
            }
        )
    )


@app.route("/api/data/source")
def api_data_source():
    """当前数据源身份：模式、来源、时间、可用分析（导入与回退诚实展示）。"""
    frame, source, data_mode, error = _resolve_workbench_data(request.args)
    if error:
        return error
    try:
        active_frame, active_source = _resolve_active_market(frame, source, data_mode)
    except Exception as exc:  # noqa: BLE001 - data-source boundary -> honest structured error
        return _market_resolution_error(exc)
    active_source = dict(active_source)
    active_source["row_count"] = len(active_frame)
    active_source["available_columns"] = [
        str(column) for column in active_frame.columns
    ]
    return jsonify(_json_safe(active_source))


@app.route("/replay/<replay_id>")
def replay_result(replay_id):
    record = load_replay(replay_id)
    if not record or record.get("schema_version") != 2 or not isinstance(record.get("result"), dict):
        abort(404)
    result = record["result"]
    lang = _resolve_language()
    view = _build_agent_view_model(result, lang=lang)
    return _with_language_cookie(render_template(
        "agent.html", result=result, view=view, question=record.get("question") or "",
        data_mode=(record.get("data_source") or {}).get("requested_mode") or "auto",
        form_error=None, lang=lang, result_id=None, historical=True,
    ), lang)


@app.route("/replay")
def replay_page():
    lang = _resolve_language()
    replays = [_build_replay_view(record, lang) for record in list_replays(limit=None)]
    html = render_template("replay.html", replays=replays, lang=lang)
    return _with_language_cookie(html, lang)


def _find_evidence_pack(pack_id: str, ext: str) -> Path | None:
    """Find a pack using the same server-controlled roots as the download route."""
    if ext not in {"json", "html"}:
        return None
    safe_id = "".join(ch if ch.isascii() and (ch.isalnum() or ch in {"-", "_"}) else "" for ch in pack_id)
    if not safe_id or safe_id != pack_id:
        return None
    filename = f"{safe_id}.{ext}"
    search_dirs = []
    # Download roots are server-controlled; never accept filesystem paths from clients.
    env_dir = os.environ.get("BOND_EVIDENCE_PACK_DIR")
    for candidate in [env_dir, str(DEFAULT_PACK_DIR), str(DEMO_PACK_DIR)]:
        if candidate and candidate not in search_dirs:
            search_dirs.append(candidate)
    for directory in search_dirs:
        root = Path(directory).resolve()
        path = (root / filename).resolve()
        if path.parent == root and path.is_file():
            return path
    return None


@app.route("/packs/<pack_id>.<ext>")
def evidence_pack_file(pack_id: str, ext: str):
    """Serve a previously exported Evidence Pack (JSON or HTML)."""
    path = _find_evidence_pack(pack_id, ext)
    if path is None:
        abort(404)
    mimetype = "application/json" if ext == "json" else "text/html"
    return send_file(path, mimetype=mimetype, download_name=path.name, as_attachment=ext == "json")


@app.route("/api/maturity/unmatched", methods=["GET", "POST"])
def export_unmatched_maturity():
    """Export unmatched-maturity bond names as CSV or JSON (live enrichment gap list)."""
    payload, error = _request_payload()
    if error:
        return error
    fmt = str(request.args.get("format") or payload.get("format") or "csv").strip().lower()
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
    if not isinstance(coverage, dict):
        return jsonify(error="maturity_coverage must be an object."), 400
    if not isinstance(records, list):
        import_entry = _resolve_import_request({"import_id": request.args.get("import_id") or payload.get("import_id")})
        if isinstance(import_entry, tuple) and len(import_entry) == 2:
            return _import_error_response(import_entry[1], _resolve_language())
        frame_override, source_override = (import_entry[0], import_entry[1]) if import_entry else (None, None)
        result = _get_agent(data_mode).answer(
            payload.get("question") or "打开今日市场监控面板：高收益、低成交与异常",
            data_frame=frame_override, data_source=source_override,
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
        # Spreadsheet applications execute formula-prefixed text even inside quoted CSV cells.
        row = {key: record.get(key) for key in fieldnames}
        for key, value in row.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                row[key] = "'" + value
        writer.writerow(row)
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
    mode = str(value or "auto").strip().lower()
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
    data_lineage = _build_data_lineage_view(data_source, lang)
    data_lineage_by_lang = {
        "zh": _build_data_lineage_view(data_source, "zh"),
        "en": _build_data_lineage_view(data_source, "en"),
    }
    maturity_board = _build_maturity_board(data_source, lang)
    maturity_board_by_lang = {
        "zh": _build_maturity_board(data_source, "zh"),
        "en": _build_maturity_board(data_source, "en"),
    }
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
            _metric("LLM model", "LLM 模型", result.get("llm_model") or "N/A", lang),
        ],
        "data_lineage": data_lineage,
        "data_lineage_by_lang": data_lineage_by_lang,
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
        "pack_html_url": f"/packs/{pack_id}.html" if pack_id and _find_evidence_pack(str(pack_id), "html") else None,
        "pack_json_url": f"/packs/{pack_id}.json" if pack_id and _find_evidence_pack(str(pack_id), "json") else None,
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
        "maturity_board": maturity_board,
        "maturity_board_by_lang": maturity_board_by_lang,
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
    pack_id = record.get("evidence_pack_id")
    replay["restorable"] = record.get("schema_version") == 2 and isinstance(record.get("result"), dict)
    replay["pack_html_available"] = bool(pack_id and _find_evidence_pack(str(pack_id), "html"))
    replay["pack_json_available"] = bool(pack_id and _find_evidence_pack(str(pack_id), "json"))
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
    # Reading-first card fields: a compact run timestamp plus one bilingual
    # source line (source name, row count, quote/fetch time) so researchers see
    # what data a past run used without opening the technical details.
    created = str(record.get("created_at") or "")
    replay["created_display"] = f"{created[:16].replace('T', ' ')} UTC" if created else "—"
    source_name = str(data_source.get("source_name") or "").strip()
    row_count = data_source.get("row_count")
    fetched_at = data_source.get("quote_time") or data_source.get("sample_date") or data_source.get("fetched_at")
    runtime_zh = replay["data_runtime_label_zh"]
    runtime_en = replay["data_runtime_label_en"]
    parts_zh = []
    parts_en = []
    for value in (source_name,):
        if value:
            parts_zh.append(value)
            parts_en.append(value)
    if row_count is not None:
        parts_zh.append(f"样本 {row_count} 行")
        parts_en.append(f"{row_count} rows")
    if fetched_at:
        parts_zh.append(f"行情时间 {fetched_at}")
        parts_en.append(f"quote date {fetched_at}")
    if parts_zh:
        replay["source_line_zh"] = " · ".join(parts_zh)
        replay["source_line_en"] = " · ".join(parts_en)
    else:
        replay["source_line_zh"] = f"数据源 {runtime_zh}"
        replay["source_line_en"] = f"{runtime_en} sample"
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
            "import": "Uploaded data",
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
        "import": "上传数据",
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
    ]
    bond_count = data_source.get("bond_count")
    if bond_count is not None and bond_count != rows:
        key_metrics.append(_metric("Distinct Bonds", "债券只数", bond_count, lang))
    key_metrics.extend([
        _metric("Median Yield", "中位收益", median, lang, "%"),
        _metric("Maturity Coverage", "期限覆盖", coverage_text, lang),
        _metric("Data Quality", "数据质量", quality_score, lang, "/100"),
    ])

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
        elif runtime == "import":
            freshness = "User-uploaded snapshot; freshness is exactly as provided in the file."
        else:
            freshness = "Freshness depends on the active data mode."
        mode_explain = {
            "live": "Live ChinaMoney/AkShare-compatible spot deal feed.",
            "live_snapshot": "Local snapshot because live fetch failed or timed out.",
            "static_sample": "Committed Excel sample for reproducible demos.",
            "static_fallback": "Static Excel used after live and snapshot both failed.",
            "import": "User-uploaded CSV/Excel snapshot parsed in memory; never stored in the repository.",
        }.get(str(runtime), f"Runtime mode: {runtime}")
        lines = [
            f"Mode: {_localized_status(runtime, 'en')}",
            f"Source: {source_name}",
            f"Fetched at: {_format_fetched_at(fetched_at, 'en')}",
            f"Rows: {data_source.get('row_count')}",
        ]
        if data_source.get("bond_count") is not None:
            lines.append(f"Distinct bonds: {data_source.get('bond_count')}")
        if data_source.get("sample_date"):
            lines.append(f"Sample date: {data_source.get('sample_date')}")
        if data_source.get("quote_time"):
            lines.append(f"Quote time range: {data_source.get('quote_time')}")
        lines.extend([freshness, mode_explain])
        if data_source.get("record_granularity_en"):
            lines.append(data_source["record_granularity_en"])
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
    elif runtime == "import":
        freshness = "用户上传快照；新鲜度即文件中提供的时间。"
    else:
        freshness = "新鲜度取决于当前数据模式。"
    mode_explain = {
        "live": "中国货币网现券成交（保留原生待偿期）。",
        "live_snapshot": "本地 live 快照（因实时失败或超时）。",
        "static_sample": "仓库 Excel 样本，便于复现演示。",
        "static_fallback": "实时与快照都失败后的静态兜底。",
        "import": "用户上传的 CSV/Excel 快照，仅在内存中解析，不入仓。",
    }.get(str(runtime), f"运行模式：{runtime}")
    lines = [
        f"模式：{_localized_status(runtime, 'zh')}",
        f"来源：{source_name}",
        f"获取时间：{_format_fetched_at(fetched_at, 'zh')}",
        f"样本行数：{data_source.get('row_count')}",
    ]
    if data_source.get("bond_count") is not None:
        lines.append(f"债券只数：{data_source.get('bond_count')}")
    if data_source.get("sample_date"):
        lines.append(f"样本日期：{data_source.get('sample_date')}")
    if data_source.get("quote_time"):
        lines.append(f"行情时间范围：{data_source.get('quote_time')}")
    lines.extend([freshness, mode_explain])
    if data_source.get("record_granularity_zh"):
        lines.append(data_source["record_granularity_zh"])
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
        headline_zh = "本次未请求 AI 解读（模型仅按需显式触发）；最终答案来自确定性报告。"
        headline_en = "AI interpretation was not requested (the model only runs on explicit demand); the deterministic report is the final answer."
    elif guardrail.get("status") == "failed":
        headline_zh = "模型已生成文本，但护栏未通过；已改用确定性报告（非模型最终答案）。"
        headline_en = "Model drafted text but guardrail rejected it; deterministic report is final (not the model text)."
    elif llm_status == "failed":
        headline_zh = "模型通道失败 → 已改用确定性报告（非模型最终答案）。"
        headline_en = "Model channel failed → using deterministic report (not a model final answer)."
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
    if not used_final and final_source == "deterministic_fallback":
        if llm_status == "failed":
            lines_zh.append("说明：模型通道失败，页面未把 LLM 文本当作最终答案。")
            lines_en.append("Note: model channel failed; LLM text was not used as the final answer.")
        elif guardrail.get("status") == "failed":
            lines_zh.append("说明：护栏拒绝了模型文本中的不受支持数字/风险语言。")
            lines_en.append("Note: guardrail rejected unsupported numbers or unsafe risk language.")
        elif llm_status in {None, "disabled"}:
            lines_zh.append("说明：AI 解读为可选的按需功能，未调用时确定性结果独立可用。")
            lines_en.append("Note: AI interpretation is an optional on-demand feature; without it the deterministic result stands alone.")
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
