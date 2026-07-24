"""Trust score for a single agent answer.

Combines data freshness, evidence quality, guardrail / judge outcomes, and
runtime degradation into one 0-100 score plus explicit adjustments.
"""

from __future__ import annotations

from typing import Any


def compute_trust_score(
    *,
    data_source: dict[str, Any],
    evidence_quality: dict[str, Any],
    llm_guardrail: dict[str, Any],
    answer_judge: dict[str, Any],
    final_answer_source: str,
    evidence_ledger: list[dict[str, Any]] | None = None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a reviewer-facing trust score.

    Design goals:
    - Prefer deterministic evidence over model fluency.
    - Make every large adjustment explainable in Chinese and English.
    - Stay honest when live data degraded to snapshot / static sample.
    - Trust Score is process/evidence trust, not trade confidence.
    """
    adjustments: list[dict[str, Any]] = []
    score = 55  # neutral baseline before evidence / data / trust layers
    plan = plan or {}
    intent = str(plan.get("intent") or "")

    eq_score = int(evidence_quality.get("score") or 0)
    evidence_boost = max(-15, min(25, round((eq_score - 55) * 0.5)))
    score += evidence_boost
    adjustments.append(
        _adj(
            "evidence_quality",
            evidence_boost,
            f"证据质量 {eq_score}/100（等级 {evidence_quality.get('level')}）。",
            f"Evidence quality {eq_score}/100 (level {evidence_quality.get('level')}).",
        )
    )

    runtime_mode = str(data_source.get("runtime_mode") or "unknown")
    freshness_delta, freshness_zh, freshness_en = _freshness_delta(runtime_mode, data_source)
    score += freshness_delta
    adjustments.append(_adj("data_freshness", freshness_delta, freshness_zh, freshness_en))

    maturity_delta, maturity_zh, maturity_en, maturity_ratio = _maturity_coverage_delta(
        runtime_mode, data_source
    )
    if maturity_delta != 0 or maturity_ratio is not None:
        score += maturity_delta
        adjustments.append(_adj("maturity_coverage", maturity_delta, maturity_zh, maturity_en))

    ledger_count = len(evidence_ledger or [])
    if ledger_count >= 4:
        score += 8
        adjustments.append(
            _adj(
                "ledger_coverage",
                8,
                f"证据账本覆盖 {ledger_count} 条 claim。",
                f"Evidence ledger covers {ledger_count} claims.",
            )
        )
    elif ledger_count >= 1:
        score += 4
        adjustments.append(
            _adj(
                "ledger_coverage",
                4,
                f"证据账本覆盖 {ledger_count} 条 claim。",
                f"Evidence ledger covers {ledger_count} claims.",
            )
        )
    else:
        score -= 10
        adjustments.append(
            _adj(
                "ledger_coverage",
                -10,
                "证据账本为空，可审查 claim 不足。",
                "Evidence ledger is empty; claim auditability is weak.",
            )
        )

    guardrail_status = str(llm_guardrail.get("status") or "not_run")
    if guardrail_status == "passed":
        score += 8
        adjustments.append(
            _adj(
                "llm_guardrail",
                8,
                "LLM 护栏通过（数字一致 + 投资语言安全）。",
                "LLM guardrail passed (numeric + investment-language safety).",
            )
        )
    elif guardrail_status == "failed":
        score -= 18
        adjustments.append(
            _adj(
                "llm_guardrail",
                -18,
                "LLM 护栏失败；最终答案应优先看确定性回退。",
                "LLM guardrail failed; prefer the deterministic fallback answer.",
            )
        )
    else:
        adjustments.append(
            _adj(
                "llm_guardrail",
                0,
                "未运行 LLM 护栏（LLM 关闭或调用失败前跳过）。",
                "LLM guardrail not run (LLM disabled or skipped).",
            )
        )

    judge_status = str(answer_judge.get("status") or "not_applicable")
    judge_delta, judge_zh, judge_en = _judge_delta(judge_status, final_answer_source)
    score += judge_delta
    adjustments.append(_adj("answer_judge", judge_delta, judge_zh, judge_en))

    # Always reinforce non-advisory boundary: never reward "investment confidence".
    score -= 2
    adjustments.append(
        _adj(
            "non_advisory_boundary",
            -2,
            "强制非投资建议边界：决策置信度保持克制。",
            "Non-advisory boundary enforced: decision confidence stays conservative.",
        )
    )

    # Policy refusals must not look like high investment confidence.
    if intent == "advisory_refusal" and score > 72:
        cap_delta = 72 - int(score)
        score = 72
        adjustments.append(
            _adj(
                "advisory_refusal_cap",
                cap_delta,
                "输入政策拦截：信任分封顶 72，避免被误读为买卖置信度。",
                "Advisory refusal cap: trust score capped at 72 to avoid trade-confidence misread.",
            )
        )

    score = max(0, min(100, int(score)))
    level = "high" if score >= 75 else "medium" if score >= 50 else "low"
    reasons = [item for item in adjustments if item["delta"] != 0]
    reasons_sorted = sorted(reasons, key=lambda item: abs(item["delta"]), reverse=True)

    summary_zh = (
        f"信任分 {score}/100（{level}）。"
        + (" 主要因素：" + "；".join(item["reason_zh"] for item in reasons_sorted[:3]) if reasons_sorted else "")
    )
    summary_en = (
        f"Trust score {score}/100 ({level})."
        + (" Top factors: " + "; ".join(item["reason_en"] for item in reasons_sorted[:3]) if reasons_sorted else "")
    )

    return {
        "score": score,
        "level": level,
        "summary_zh": summary_zh,
        "summary_en": summary_en,
        "components": {
            "evidence_quality_score": eq_score,
            "runtime_mode": runtime_mode,
            "guardrail_status": guardrail_status,
            "judge_status": judge_status,
            "final_answer_source": final_answer_source,
            "ledger_item_count": ledger_count,
            "maturity_coverage_ratio": maturity_ratio,
            "intent": intent or None,
        },
        "adjustments": adjustments,
        "headline_reasons": reasons_sorted[:4],
    }


def _maturity_coverage_delta(
    runtime_mode: str, data_source: dict[str, Any]
) -> tuple[int, str, str, float | None]:
    """Penalize incomplete live maturity enrichment.

    Live feeds often lack native maturity fields. Low coverage after static-master
    enrichment should reduce process trust, not be hidden behind high scores.
    """
    coverage = data_source.get("maturity_coverage") or {}
    raw_ratio = coverage.get("coverage_ratio")
    if raw_ratio is None:
        return (
            0,
            "未提供期限覆盖率，未做 maturity 调整。",
            "Maturity coverage unavailable; no maturity adjustment.",
            None,
        )

    ratio = float(raw_ratio)
    # Static samples are usually security-master complete; only penalize live paths.
    if runtime_mode not in {"live", "live_snapshot"}:
        return (
            0,
            f"非实时路径期限覆盖率 {ratio:.1%}，不额外惩罚。",
            f"Non-live maturity coverage {ratio:.1%}; no extra penalty.",
            ratio,
        )

    if ratio < 0.30:
        return (
            -18,
            f"实时期限补全仅 {ratio:.1%}，同业/久期结论不可靠。",
            f"Live maturity enrichment only {ratio:.1%}; peer/duration conclusions are unreliable.",
            ratio,
        )
    if ratio < 0.70:
        return (
            -10,
            f"实时期限补全 {ratio:.1%}，覆盖不足。",
            f"Live maturity enrichment {ratio:.1%}; coverage is incomplete.",
            ratio,
        )
    if ratio < 0.90:
        return (
            -4,
            f"实时期限补全 {ratio:.1%}，仍有缺口。",
            f"Live maturity enrichment {ratio:.1%}; residual gaps remain.",
            ratio,
        )
    return (
        0,
        f"实时期限补全 {ratio:.1%}，覆盖较好。",
        f"Live maturity enrichment {ratio:.1%}; coverage is healthy.",
        ratio,
    )


def _freshness_delta(runtime_mode: str, data_source: dict[str, Any]) -> tuple[int, str, str]:
    fallback = data_source.get("fallback_reason")
    if runtime_mode == "live":
        return (
            12,
            "使用实时行情源。",
            "Live market feed is active.",
        )
    if runtime_mode == "live_snapshot":
        reason = f"（{fallback}）" if fallback else ""
        return (
            -8,
            f"实时失败后使用缓存快照{reason}。",
            f"Using cached live snapshot after live failure{(': ' + str(fallback)) if fallback else ''}.",
        )
    if runtime_mode == "static_fallback":
        reason = f"（{fallback}）" if fallback else ""
        return (
            -15,
            f"已降级到本地备用样本{reason}。",
            f"Degraded to local static fallback sample{(': ' + str(fallback)) if fallback else ''}.",
        )
    if runtime_mode in {"static_sample", "static"}:
        return (
            -12,
            "使用静态样本，新鲜度受限。",
            "Static sample in use; freshness is limited.",
        )
    return (
        -5,
        f"数据运行模式未知：{runtime_mode}。",
        f"Unknown data runtime mode: {runtime_mode}.",
    )


def _judge_delta(judge_status: str, final_answer_source: str) -> tuple[int, str, str]:
    if judge_status == "passed":
        return (
            10,
            "答案评审通过，允许使用护栏后的 LLM 叙述。",
            "Answer judge passed; guardrailed LLM narration accepted.",
        )
    if judge_status in {"rejected", "failed"}:
        return (
            -12,
            "答案评审拒绝 LLM 输出，已回退确定性报告。",
            "Answer judge rejected LLM output; deterministic report used.",
        )
    if judge_status == "safe_fallback":
        return (
            4,
            "LLM 不可用，安全回退到确定性报告。",
            "LLM unavailable; safe fallback to deterministic report.",
        )
    if judge_status == "not_applicable":
        if final_answer_source == "deterministic_fallback":
            return (
                6,
                "未使用 LLM；最终答案完全来自确定性工具链。",
                "LLM not used; final answer is fully deterministic.",
            )
        return (
            0,
            "答案评审不适用。",
            "Answer judge not applicable.",
        )
    return (
        0,
        f"答案评审状态：{judge_status}。",
        f"Answer judge status: {judge_status}.",
    )


def _adj(kind: str, delta: int, reason_zh: str, reason_en: str) -> dict[str, Any]:
    return {
        "id": kind,
        "delta": int(delta),
        "reason_zh": reason_zh,
        "reason_en": reason_en,
    }
