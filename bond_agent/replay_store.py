from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .data_loader import PROJECT_ROOT

DEFAULT_REPLAY_DIR = PROJECT_ROOT / ".tmp" / "replays"


_REPLAY_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{8}\Z")
_MAX_RECORD_BYTES = 2 * 1024 * 1024
_RESULT_FIELDS = {
    "agent", "subtitle", "question", "plan", "tools_used", "tool_trace", "data_evidence",
    "data_source", "risk_explanations", "evidence_quality", "evidence_ledger", "answer_judge",
    "risk_profile", "trust_score", "stress_view", "analysis", "risk_notes", "limitations",
    "final_answer", "final_answer_source", "llm_guardrail", "used_llm", "used_llm_in_final",
    "llm_status", "disclaimer", "replay_id", "evidence_pack_id",
}
_BOND_ROW_FIELDS = {
    "债券简称", "债券代码", "待偿期", "待偿期(年)", "待偿期来源", "待偿期原文",
    "是否永续风格", "待偿期解析说明", "首段期限(年)", "远端期限(年)",
    "收盘净价(元)", "收盘到期收益率(%)", "加权收益率(%)", "交易量(亿元)",
    "涨跌(BP)", "行情时间", "券种", "期限分桶", "组内均值", "组内标准差", "组内样本数",
    "偏离(BP)", "修正久期(现金流假设)", "麦考利久期(现金流假设)", "DV01(现金流假设)",
    "理论永续修正久期", "久期方法",
}
_SECRET_KEY = re.compile(r"password|secret|token|api.?key|authorization|file.?name|path|prompt", re.IGNORECASE)
_SECRET_TEXT = re.compile(r"(?i)(?:bearer\s+|(?:api[_ -]?key|token|password|secret|authorization)\s*[:=]\s*)[^\s,;]+|\bsk-[A-Za-z0-9_-]{12,}|[A-Za-z]:[\\/][^\s,;]+|(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|https?://[^\s/@]+:[^\s/@]+@[^\s]+|(?<![\w:])/(?:[\w.-]+/)+[\w.-]+")
_EVIDENCE_KEYS = set(['ambiguity_note', 'analysis', 'ascending', 'assumptions_en', 'assumptions_zh', 'available', 'bond_name', 'bond_type', 'bucket', 'by_bond_type', 'by_maturity_bucket', 'code', 'columns', 'comparison', 'count', 'credit_context', 'criteria', 'data_evidence', 'data_quality', 'diagnostics', 'distinct_bond_count', 'duplicate_name_count', 'dv01', 'dv01_approx', 'extreme_yield_count', 'extreme_yield_ratio', 'first_leg_years', 'found', 'grouping', 'groups_evaluated', 'high_yield', 'id', 'is_perpetual_style', 'is_yield_outlier', 'issuer_rating', 'issues', 'level', 'limit', 'limitations', 'low_volume', 'macaulay_duration', 'market', 'match_count', 'matched_by', 'maturity', 'maturity_bucket', 'maturity_coverage_ratio', 'maturity_missing_ratio', 'maturity_parse_note', 'maturity_percentile', 'maturity_summary_years', 'max', 'max_maturity', 'max_yield', 'mean', 'median', 'message', 'message_en', 'message_zh', 'metadata', 'method', 'min', 'min_group_size', 'min_maturity', 'min_yield', 'missing_maturity', 'missing_maturity_count', 'missing_yield_count', 'modified_duration', 'modified_duration_approx', 'monitor', 'name', 'nearest_market_context', 'note_en', 'note_zh', 'other_type_count', 'other_type_ratio', 'outlier_count', 'outlier_metadata', 'outliers', 'p25', 'p75', 'peer_comparison', 'peer_count', 'peer_definition_en', 'peer_definition_zh', 'peer_status', 'peer_volume_percentile', 'peer_yield_mean', 'peer_yield_median', 'peer_yield_percentile', 'peer_yield_zscore', 'perpetual_modified_duration', 'price', 'question', 'quote_date', 'rank_by', 'ranking', 'rate_sensitivity', 'record', 'records', 'remote_leg_years', 'risk_notes', 'row_count', 'runtime_mode', 'sample_count', 'scenarios', 'score', 'search', 'segments', 'severity', 'skipped_missing_bucket_count', 'skipped_unreliable_type_count', 'small_groups_skipped', 'spread_vs_peer_mean_bp', 'std', 'summary_en', 'summary_zh', 'threshold', 'tool', 'tools_used', 'top_n', 'type_counts', 'unclassified_type_count', 'volume', 'volume_percentile', 'volume_summary', 'weighted_yield_summary', 'yield', 'yield_distribution', 'yield_mean', 'yield_median', 'yield_missing_ratio', 'yield_outliers', 'yield_percentile', 'yield_summary', 'yield_zscore', '净价', '成交量', '收益率', '期限']) | _BOND_ROW_FIELDS
_EVIDENCE_KEYS.add('outlier_score')
_SCHEMA_KEYS = {
    'data_source': set(['source_id', 'source_name', 'storage', 'runtime_mode', 'requested_mode', 'fetched_at', 'fallback_reason', 'row_count', 'valid_yield_count', 'columns', 'maturity_coverage', 'active_live_feed', 'active_live_snapshot', 'provider', 'legacy_crawler', 'limitations', 'sample_date', 'quote_time', 'bond_count', 'record_granularity_zh', 'record_granularity_en', 'has_code_column']),
    'maturity_coverage': set(['filled_count', 'missing_count', 'coverage_ratio', 'source_counts', 'unmatched_count', 'unmatched_limit', 'unmatched_records', 'enrichment_note']),
    'source_counts': set(['source_field', 'static_lookup', 'missing', 'imported']),
    'legacy_crawler': set(['status', 'targets', 'notes']),
    'plan': set(['intent', 'requested_tools', 'search_params', 'rank_by', 'ascending', 'flags', 'explanation']),
    'search_params': set(['bond_name', 'bond_type', 'min_yield', 'max_yield', 'min_maturity', 'max_maturity', 'limit']),
    'flags': set(['composite', 'wants_outliers', 'wants_ranking', 'wants_overview', 'wants_structure', 'wants_monitor', 'wants_report', 'wants_peers', 'wants_advisory']),
    'data_evidence': set(['market', 'search', 'ranking', 'outliers', 'comparison', 'monitor', 'report', 'peers']),
    'market': set(['tool', 'sample_count', 'columns', 'yield_summary', 'weighted_yield_summary', 'volume_summary', 'maturity_summary_years', 'yield_distribution', 'segments', 'data_quality']),
    'monitor': set(['tool', 'top_n', 'high_yield', 'low_volume', 'yield_outliers', 'outlier_metadata', 'missing_maturity', 'summary_zh', 'summary_en']),
    'segments': set(['by_bond_type', 'by_maturity_bucket', 'missing_maturity_count', 'other_type_count', 'unclassified_type_count', 'type_counts']),
    'evidence_quality': set(['score', 'level', 'analysis_confidence', 'decision_confidence', 'data_freshness', 'coverage', 'checks', 'penalties', 'summary']),
    'coverage': set(['intent', 'tools_used_count', 'has_market_summary', 'has_search_results', 'has_bond_comparison', 'has_risk_explanations', 'maturity_coverage_ratio']),
    'risk_explanations': set(['id', 'title', 'summary', 'watch_points', 'source', 'retrieval_score']),
    'evidence_ledger': set(['id', 'claim_zh', 'claim_en', 'evidence_zh', 'evidence_en', 'source', 'tool', 'confidence', 'limitations', 'fields']),
    'answer_judge': set(['status', 'score', 'judge_type', 'verdict_zh', 'verdict_en', 'recommended_action', 'checks']),
    'risk_profile': set(['overall_level', 'cards', 'summary_zh', 'summary_en']),
    'cards': set(['id', 'title_zh', 'title_en', 'severity', 'signal_zh', 'signal_en', 'evidence_zh', 'evidence_en', 'action_boundary_zh', 'action_boundary_en']),
    'trust_score': set(['score', 'level', 'summary_zh', 'summary_en', 'components', 'adjustments', 'headline_reasons']),
    'components': set(['evidence_quality_score', 'runtime_mode', 'guardrail_status', 'judge_status', 'final_answer_source', 'ledger_item_count', 'maturity_coverage_ratio', 'intent']),
    'adjustments': set(['id', 'delta', 'reason_zh', 'reason_en']),
    'headline_reasons': set(['id', 'delta', 'reason_zh', 'reason_en']),
    'stress_view': set(['severity', 'summary_zh', 'summary_en', 'runtime_mode', 'fallback_reason', 'trust_score', 'trust_level', 'guardrail_status', 'judge_status', 'final_answer_source', 'signals', 'active_signal_count', 'requires_review']),
    'signals': set(['id', 'severity', 'message_zh', 'message_en']),
    'llm_guardrail': set(['status', 'numeric_status', 'language_status', 'score', 'used_for_final', 'unsupported_numbers', 'unsafe_phrases', 'supported_number_count', 'checked_number_count', 'summary', 'applicability']),
    'tool_trace': set(['tool', 'status', 'duration_ms', 'message', 'message_zh', 'message_en']),
    'checks': set(['id', 'label_zh', 'label_en', 'status', 'detail_zh', 'detail_en']),
}
_EVIDENCE_MAP_KEYS = {'type_counts', 'yield_distribution'}
_IMPORT_TEXT_FIELDS = {'question', 'final_answer', 'analysis', 'risk_notes', 'tool_trace', '债券简称', '债券代码', 'bond_name', 'name', 'code', '待偿期原文', 'message', 'message_zh', 'message_en', 'note_zh', 'note_en', 'summary', 'summary_zh', 'summary_en', 'claim_zh', 'claim_en', 'evidence_zh', 'evidence_en', 'fallback_reason', 'source_name'}
_EVIDENCE_MAP_VALUES = {'国债', '政策性金融债', '地方政府债', '同业存单', '信用债', '金融债', '待分类', '其他', '未知', '<2%', '2–3%', '3–4%', '4–5%', '5–6%', '≥6%'}


def _sanitize(value: Any, key: str = "", *, evidence: bool = False, imported: bool = False) -> Any:
    if isinstance(value, dict):
        bond_row = key in {"records", "record", "target"} and ("债券简称" in value or "债券代码" in value)
        allowed = _SCHEMA_KEYS.get(key, _EVIDENCE_KEYS if evidence else (_RESULT_FIELDS if not key else set()))
        clean = {str(k): _sanitize(v, str(k), evidence=evidence or str(k) == 'data_evidence', imported=imported)
                 for k, v in value.items() if not _SECRET_KEY.search(str(k))
                 and (str(k) in allowed or (key in _EVIDENCE_MAP_KEYS and str(k) in _EVIDENCE_MAP_VALUES))
                 and (not bond_row or str(k) in _BOND_ROW_FIELDS)
                 and (not evidence or (key in _EVIDENCE_MAP_KEYS and str(k) in _EVIDENCE_MAP_VALUES) or str(k) in _EVIDENCE_KEYS)}
        if value.get("runtime_mode") == "import":
            clean["source_name"] = "uploaded dataset"
            clean["storage"] = "memory only"
            clean["columns"] = [column for column in clean.get("columns", []) if column in _BOND_ROW_FIELDS]
            clean.pop("import_notes_zh", None)
            clean.pop("import_notes_en", None)
        return clean
    if isinstance(value, list):
        return [_sanitize(item, key, evidence=evidence, imported=imported) for item in value[:1000]]
    if isinstance(value, str):
        if imported and key in _IMPORT_TEXT_FIELDS:
            return '[为隐私省略 / Omitted for privacy]'
        text = _SECRET_TEXT.sub('[redacted]', value[:16000])
        if imported:
            enums = {'import', 'static', 'auto', 'live', 'uploaded dataset', 'memory only', 'disabled', 'not_run', 'not_applicable', 'deterministic_fallback', 'BondLens', 'low', 'medium', 'high', 'unknown', 'ok', 'pass', 'warn', 'fail', '国债', '政策性金融债', '地方政府债', '同业存单', '信用债', '金融债', '待分类', '其他', '未知'}
            safe_id = key in {'replay_id', 'evidence_pack_id'} and _REPLAY_ID.fullmatch(text)
            safe_date = key in {'sample_date', 'quote_time', 'quote_date', 'fetched_at'} and re.fullmatch(r'[0-9TZ:+.\- ]{4,40}', text)
            safe_tool = key in {'tool', 'tools_used', 'requested_tools'} and text in {'describe_market', 'search_bonds', 'rank_bonds', 'find_yield_outliers', 'compare_bonds', 'build_market_monitor', 'generate_bond_report'}
            if not (text in enums or safe_id or safe_date or safe_tool):
                return '[为隐私省略 / Omitted for privacy]'
        return text
    return value


def project_result(response: dict[str, Any]) -> dict[str, Any]:
    imported = (response.get('data_source') or {}).get('runtime_mode') == 'import'
    return _sanitize({key: value for key, value in response.items() if key in _RESULT_FIELDS}, imported=imported)


def load_replay(replay_id: str) -> dict[str, Any] | None:
    if not _enabled() or not _REPLAY_ID.fullmatch(replay_id or ""):
        return None
    path = _replay_dir() / f"{replay_id}.json"
    try:
        if path.is_symlink() or path.stat().st_size > _MAX_RECORD_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("id") != replay_id:
            return None
        for key in ("data_source", "llm", "risk", "trust_score", "evidence_quality", "stress_view"):
            if key in value and value[key] is not None and not isinstance(value[key], dict):
                return None
        if value.get("schema_version") not in (None, 2):
            return None
        if value.get("schema_version") == 2:
            result = value.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("data_source"), dict):
                return None
            if result.get('replay_id') not in (None, replay_id):
                return None
            for key in ('plan', 'data_evidence', 'risk_profile', 'evidence_quality'):
                if key in result and not isinstance(result[key], dict):
                    return None
        return value
    except (OSError, ValueError):
        return None


def save_replay(response: dict[str, Any]) -> dict[str, Any] | None:
    if not _enabled():
        return None

    replay_id = response.get("replay_id") or _new_replay_id()
    if not _REPLAY_ID.fullmatch(replay_id):
        return None
    projected = project_result(response)
    record = _summarize_response(projected, replay_id=replay_id)
    if response.get("final_answer") is not None:
        record["schema_version"] = 2
        record["privacy_limited"] = True
        record["result"] = projected
    temporary = None
    try:
        directory = _replay_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{replay_id}.json"
        encoded = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        if len(encoded) > _MAX_RECORD_BYTES:
            return None
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".replay-", delete=False) as tmp:
            temporary = Path(tmp.name)
            tmp.write(encoded)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        return None
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
    return record


def list_replays(limit: int | None = 30) -> list[dict[str, Any]]:
    if not _enabled():
        return []
    directory = _replay_dir()
    if not directory.exists():
        return []

    records = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        record = load_replay(path.stem)
        if record is not None:
            records.append(record)
            if limit is not None and len(records) >= limit:
                break
    return records


def page_replays(*, cursor: str = '', direction: str = 'next', order: str = 'desc',
                 query: str = '', source: str = '', status: str = '', start: str = '', end: str = '',
                 page_size: int = 20) -> tuple[list[dict[str, Any]], bool, bool]:
    """Keyset pagination; only read records needed to find this page and its neighbors."""
    if not _enabled():
        return [], False, False
    if cursor and not _REPLAY_ID.fullmatch(cursor):
        raise ValueError('Invalid cursor')
    if direction not in {'next', 'prev'} or order not in {'asc', 'desc'} or not 1 <= page_size <= 50:
        raise ValueError('Invalid pagination')
    directory = _replay_dir()
    try:
        names = sorted((p.stem for p in directory.glob('*.json') if _REPLAY_ID.fullmatch(p.stem)), reverse=order == 'desc')
    except OSError:
        return [], False, False
    if cursor:
        names = [name for name in names if (name < cursor if order == 'desc' else name > cursor) == (direction == 'next') and name != cursor]
    if direction == 'prev':
        names.reverse()
    rows = []
    for name in names:
        record = load_replay(name)
        if not record:
            continue
        date = str(record.get('created_at') or '')[:10]
        data_source = record.get('data_source') or {}
        llm = record.get('llm') or {}
        if query and query.casefold() not in str(record.get('question') or '').casefold():
            continue
        if source and (data_source.get('runtime_mode') or 'unknown') != source:
            continue
        if status and (llm.get('status') or 'unknown') != status:
            continue
        if (start and date < start) or (end and (not date or date > end)):
            continue
        rows.append(record)
        if len(rows) > page_size:
            break
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    if direction == 'prev':
        rows.reverse()
        return rows, has_more, bool(cursor)
    return rows, bool(cursor), has_more


def _summarize_response(response: dict[str, Any], replay_id: str) -> dict[str, Any]:
    data_source = response.get("data_source") or {}
    evidence_quality = response.get("evidence_quality") or {}
    llm_guardrail = response.get("llm_guardrail") or {}
    answer_judge = response.get("answer_judge") or {}
    risk_profile = response.get("risk_profile") or {}
    trust_score = response.get("trust_score") or {}
    return {
        "id": replay_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "question": response.get("question"),
        "intent": (response.get("plan") or {}).get("intent"),
        "tools_used": response.get("tools_used", []),
        "data_source": {
            "source_id": data_source.get("source_id"),
            "source_name": data_source.get("source_name"),
            "runtime_mode": data_source.get("runtime_mode"),
            "requested_mode": data_source.get("requested_mode"),
            "sample_date": data_source.get("sample_date"),
            "quote_time": data_source.get("quote_time"),
            "fallback_reason": data_source.get("fallback_reason"),
            "row_count": data_source.get("row_count"),
            "valid_yield_count": data_source.get("valid_yield_count"),
            "fetched_at": data_source.get("fetched_at"),
        },
        "evidence_quality": {
            "score": evidence_quality.get("score"),
            "level": evidence_quality.get("level"),
            "data_freshness": evidence_quality.get("data_freshness"),
            "decision_confidence": evidence_quality.get("decision_confidence"),
        },
        "trust_score": {
            "score": trust_score.get("score"),
            "level": trust_score.get("level"),
            "summary_zh": trust_score.get("summary_zh"),
            "summary_en": trust_score.get("summary_en"),
        },
        "llm": {
            "status": response.get("llm_status"),
            "guardrail_status": llm_guardrail.get("status"),
            "judge_status": answer_judge.get("status"),
            "final_answer_source": response.get("final_answer_source"),
        },
        "risk": {
            "overall_level": risk_profile.get("overall_level"),
            "summary_zh": risk_profile.get("summary_zh"),
            "summary_en": risk_profile.get("summary_en"),
        },
        "evidence_ledger": response.get("evidence_ledger", [])[:6],
        "evidence_pack_id": response.get("evidence_pack_id"),
        "stress_view": {
            "severity": (response.get("stress_view") or {}).get("severity"),
            "summary_zh": (response.get("stress_view") or {}).get("summary_zh"),
            "summary_en": (response.get("stress_view") or {}).get("summary_en"),
            "active_signal_count": (response.get("stress_view") or {}).get("active_signal_count"),
            "requires_review": (response.get("stress_view") or {}).get("requires_review"),
        },
    }


def _enabled() -> bool:
    return os.environ.get("BOND_REPLAY_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _replay_dir() -> Path:
    configured = os.environ.get("BOND_REPLAY_DIR")
    return Path(configured) if configured else DEFAULT_REPLAY_DIR


def _new_replay_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid4().hex[:8]}"
