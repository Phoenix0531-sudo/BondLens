"""Stress / degradation view for one agent answer.

Surfaces honest runtime stress signals: data fallback, low trust, guardrail
failures, and missing evidence — without inventing market-stress analytics.
"""

from __future__ import annotations

from typing import Any


def build_stress_view(
    *,
    data_source: dict[str, Any],
    trust_score: dict[str, Any],
    llm_guardrail: dict[str, Any],
    answer_judge: dict[str, Any],
    final_answer_source: str,
    evidence_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact stress profile for UI and Evidence Pack."""
    signals: list[dict[str, Any]] = []
    runtime_mode = str(data_source.get("runtime_mode") or "unknown")
    fallback_reason = data_source.get("fallback_reason")
    trust_level = str(trust_score.get("level") or "medium")
    trust_value = int(trust_score.get("score") or 0)
    guardrail_status = str(llm_guardrail.get("status") or "not_run")
    judge_status = str(answer_judge.get("status") or "not_applicable")
    eq = evidence_quality or {}
    eq_score = int(eq.get("score") or 0)

    if runtime_mode in {"static_sample", "static", "static_fallback"}:
        signals.append(
            _signal(
                "data_static",
                "high" if runtime_mode == "static_fallback" else "medium",
                "数据为静态/备用样本，新鲜度受限。",
                "Static or fallback sample in use; freshness is limited.",
            )
        )
    elif runtime_mode == "live_snapshot":
        signals.append(
            _signal(
                "data_snapshot",
                "medium",
                "实时失败后使用缓存快照。",
                "Using cached live snapshot after live failure.",
            )
        )
    elif runtime_mode == "live":
        signals.append(
            _signal(
                "data_live",
                "low",
                "当前使用实时行情源。",
                "Live market feed is active.",
            )
        )
    else:
        signals.append(
            _signal(
                "data_unknown",
                "medium",
                f"数据运行模式未知：{runtime_mode}。",
                f"Unknown data runtime mode: {runtime_mode}.",
            )
        )

    if fallback_reason:
        signals.append(
            _signal(
                "data_fallback",
                "high",
                f"发生数据降级：{fallback_reason}",
                f"Data degraded with fallback reason: {fallback_reason}",
            )
        )

    if trust_value < 50 or trust_level == "low":
        signals.append(
            _signal(
                "trust_low",
                "high",
                f"信任分偏低（{trust_value}/100），审查应更严格。",
                f"Trust score is low ({trust_value}/100); review more carefully.",
            )
        )
    elif trust_value < 70:
        signals.append(
            _signal(
                "trust_medium",
                "medium",
                f"信任分中等（{trust_value}/100）。",
                f"Trust score is medium ({trust_value}/100).",
            )
        )

    if guardrail_status == "failed":
        signals.append(
            _signal(
                "guardrail_failed",
                "high",
                "LLM 护栏失败，最终答案应优先看确定性回退。",
                "LLM guardrail failed; prefer the deterministic fallback answer.",
            )
        )

    if judge_status in {"rejected", "failed", "failed_guardrail"}:
        signals.append(
            _signal(
                "judge_rejected",
                "high",
                "答案评审拒绝模型输出。",
                "Answer judge rejected model output.",
            )
        )

    if final_answer_source == "deterministic_fallback":
        signals.append(
            _signal(
                "deterministic_final",
                "low",
                "最终答案来自确定性工具链（未采用 LLM 叙述，或已回退）。",
                "Final answer is deterministic (LLM unused or rejected).",
            )
        )

    if eq_score and eq_score < 55:
        signals.append(
            _signal(
                "evidence_weak",
                "medium",
                f"证据质量偏弱（{eq_score}/100）。",
                f"Evidence quality is weak ({eq_score}/100).",
            )
        )

    severity = _overall_severity(signals)
    active = [item for item in signals if item["severity"] in {"medium", "high"}]
    summary_zh, summary_en = _summaries(severity, active, runtime_mode)

    return {
        "severity": severity,
        "summary_zh": summary_zh,
        "summary_en": summary_en,
        "runtime_mode": runtime_mode,
        "fallback_reason": fallback_reason,
        "trust_score": trust_value,
        "trust_level": trust_level,
        "guardrail_status": guardrail_status,
        "judge_status": judge_status,
        "final_answer_source": final_answer_source,
        "signals": signals,
        "active_signal_count": len(active),
        "requires_review": severity in {"medium", "high"},
    }


def _overall_severity(signals: list[dict[str, Any]]) -> str:
    ranks = {"low": 0, "medium": 1, "high": 2}
    level = 0
    for item in signals:
        level = max(level, ranks.get(str(item.get("severity")), 0))
    return {0: "low", 1: "medium", 2: "high"}[level]


def _summaries(severity: str, active: list[dict[str, Any]], runtime_mode: str) -> tuple[str, str]:
    if not active:
        return (
            f"运行压力低：数据模式 {runtime_mode}，未见显著降级。",
            f"Runtime stress is low: data mode {runtime_mode}, no major degradation.",
        )
    top = "；".join(item["message_zh"] for item in active[:3])
    top_en = "; ".join(item["message_en"] for item in active[:3])
    return (
        f"运行压力 {severity}：{top}",
        f"Runtime stress {severity}: {top_en}",
    )


def _signal(kind: str, severity: str, message_zh: str, message_en: str) -> dict[str, Any]:
    return {
        "id": kind,
        "severity": severity,
        "message_zh": message_zh,
        "message_en": message_en,
    }
