from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .data_loader import PROJECT_ROOT


DEFAULT_REPLAY_DIR = PROJECT_ROOT / ".tmp" / "replays"


def save_replay(response: dict[str, Any]) -> dict[str, Any] | None:
    if not _enabled():
        return None

    replay_id = _new_replay_id()
    record = _summarize_response(response, replay_id=replay_id)
    try:
        directory = _replay_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{replay_id}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return None
    return record


def list_replays(limit: int = 30) -> list[dict[str, Any]]:
    directory = _replay_dir()
    if not directory.exists():
        return []

    records = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return records


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
    }


def _enabled() -> bool:
    return os.environ.get("BOND_REPLAY_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _replay_dir() -> Path:
    configured = os.environ.get("BOND_REPLAY_DIR")
    return Path(configured) if configured else DEFAULT_REPLAY_DIR


def _new_replay_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid4().hex[:8]}"
