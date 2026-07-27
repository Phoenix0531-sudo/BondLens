from __future__ import annotations

import json
import math
import os
import re
import time

from .answer_judge import judge_answer
from .data_loader import resolve_bond_data
from .evidence_ledger import build_evidence_ledger
from .evidence_pack import export_evidence_pack
from .evidence_quality import assess_evidence_quality
from .llm_guardrail import assess_llm_faithfulness
from .planner import classify_intent
from .replay_store import save_replay
from .risk_knowledge import retrieve_risk_explanations
from .risk_profile import build_risk_profile
from .schemas import AgentResponse
from .stress_view import build_stress_view
from .tools import (
    build_market_monitor,
    compare_bond_to_market,
    describe_market,
    detect_yield_outliers,
    generate_bond_report,
    rank_bonds,
    search_bonds,
)
from .trust_score import compute_trust_score

DISCLAIMER = "非投资建议，仅用于学习和研究。"
LIMITATIONS_TEMPLATE = [
    "非投资建议，仅用于学习和研究。",
    "当前行情源不包含主体评级、财务报表、担保与信用事件。",
    "收益率高低是风险信号，不是买卖依据。",
    "实时链路可能降级到快照或本地样本，请以 data_source 血缘为准。",
]


class BondAnalystAgent:
    name = "BondLens"

    def __init__(self, data_path: str | None = None, data_mode: str | None = None, live_fetcher=None) -> None:
        self.data_path = data_path
        self.data_mode = data_mode or os.environ.get("BOND_DATA_MODE", "auto")
        self.live_fetcher = live_fetcher

    def answer(self, question: str) -> dict:
        question = question.strip() or "请概览当前债券市场样本。"
        data_frame, data_source = resolve_bond_data(
            mode=self.data_mode,
            path=self.data_path or None,
            live_fetcher=self.live_fetcher,
        )
        plan = classify_intent(question, data_path=self.data_path, data_frame=data_frame)
        tool_outputs: list[dict] = []
        tool_trace: list[str] = [
            f"User question: {question}",
            f"-> data_source(mode={data_source['runtime_mode']}, source={data_source['source_id']})",
            f"-> planner(intent={plan['intent']})",
        ]

        report = None
        for tool_name in plan["requested_tools"]:
            if tool_name == "search_bonds":
                result = search_bonds(**plan["search_params"], data_frame=data_frame)
                tool_outputs.append(result)
                tool_trace.append(f"-> search_bonds({self._compact_args(plan['search_params'])})")
            elif tool_name == "compare_bond_to_market":
                search_result = self._find_tool_output(tool_outputs, "search_bonds")
                first_record = (search_result.get("records") or [None])[0] if search_result else None
                result = compare_bond_to_market(
                    bond_name=plan["search_params"].get("name"),
                    record=first_record,
                    data_frame=data_frame,
                )
                tool_outputs.append(result)
                tool_trace.append("-> compare_bond_to_market()")
            elif tool_name == "describe_market":
                result = describe_market(
                    data_frame=data_frame,
                    maturity_coverage=data_source.get("maturity_coverage"),
                    runtime_mode=data_source.get("runtime_mode"),
                )
                tool_outputs.append(result)
                tool_trace.append("-> describe_market()")
            elif tool_name == "rank_bonds":
                result = rank_bonds(
                    by=plan["rank_by"] or "yield",
                    top_n=5,
                    ascending=plan["ascending"],
                    data_frame=data_frame,
                )
                tool_outputs.append(result)
                tool_trace.append(f"-> rank_bonds(by={plan['rank_by'] or 'yield'}, top_n=5)")
            elif tool_name == "detect_yield_outliers":
                result = detect_yield_outliers(method="zscore", threshold=3.0, top_n=5, data_frame=data_frame)
                tool_outputs.append(result)
                tool_trace.append("-> detect_yield_outliers(method=zscore, threshold=3.0)")
            elif tool_name == "build_market_monitor":
                result = build_market_monitor(top_n=5, data_frame=data_frame)
                tool_outputs.append(result)
                tool_trace.append("-> build_market_monitor(top_n=5)")
            elif tool_name == "generate_bond_report":
                report = generate_bond_report(question, tool_outputs, plan=plan)
                tool_trace.append("-> generate_bond_report()")

        if report is None:
            report = generate_bond_report(question, tool_outputs, plan=plan)
            tool_trace.append("-> generate_bond_report()")

        risk_explanations = retrieve_risk_explanations(question, report)
        evidence_quality = assess_evidence_quality(plan, report, data_source, risk_explanations)
        report["data_source"] = data_source
        report["risk_explanations"] = risk_explanations
        report["evidence_quality"] = evidence_quality

        is_advisory_refusal = plan.get("intent") == "advisory_refusal"
        if is_advisory_refusal:
            fallback_answer = self._format_advisory_refusal(question, report, plan)
            llm_result = {"text": None, "status": "disabled", "error": "advisory_policy_block"}
            llm_guardrail = assess_llm_faithfulness(None, report)
            tool_trace.append("-> input_policy(status=advisory_refusal)")
            tool_trace.append("-> llm_guardrail(skipped: advisory_policy_block)")
            use_llm_final = False
            final_answer = fallback_answer
            final_answer_source = "deterministic_fallback"
        else:
            fallback_answer = self._format_report(report, plan)
            llm_result = self._try_llm_answer(question, plan, report)
            llm_guardrail = (
                assess_llm_faithfulness(llm_result["text"], report)
                if llm_result["status"] == "success"
                else assess_llm_faithfulness(None, report)
            )
            if (
                llm_result["status"] == "success"
                and llm_guardrail["status"] != "passed"
                and llm_result.get("text")
            ):
                repaired = self._try_repair_llm_answer(
                    question, plan, report, llm_result["text"], llm_guardrail
                )
                if repaired.get("status") == "success" and repaired.get("text"):
                    repaired_guard = assess_llm_faithfulness(repaired["text"], report)
                    tool_trace.append(
                        f"-> llm_guardrail_repair(status={repaired_guard['status']})"
                    )
                    if repaired_guard["status"] == "passed":
                        llm_result = repaired
                        llm_guardrail = repaired_guard
            if llm_result["status"] == "success":
                tool_trace.append(f"-> llm_guardrail(status={llm_guardrail['status']})")
            else:
                tool_trace.append(f"-> llm_guardrail(skipped: llm_{llm_result['status']})")

            use_llm_final = llm_result["status"] == "success" and llm_guardrail["status"] == "passed"
            final_answer = llm_result["text"] if use_llm_final else fallback_answer
            final_answer_source = "llm" if use_llm_final else "deterministic_fallback"

        answer_judge = judge_answer(
            llm_status=llm_result["status"],
            llm_guardrail=llm_guardrail,
            evidence_quality=evidence_quality,
            final_answer_source=final_answer_source,
        )
        risk_profile = build_risk_profile(report, data_source, evidence_quality, llm_guardrail)
        evidence_ledger = build_evidence_ledger(
            plan=plan,
            report=report,
            data_source=data_source,
            evidence_quality=evidence_quality,
            llm_guardrail=llm_guardrail,
            final_answer_source=final_answer_source,
        )
        limitations = self._merge_limitations(report.get("limitations") or [])
        limitations = self._append_data_source_limitations(limitations, data_source, report)
        if is_advisory_refusal:
            policy_note = "输入政策：问题含买卖/保证收益等投资建议诉求，已拦截推荐路径。"
            if policy_note not in limitations:
                limitations.insert(0, policy_note)
        trust_score = compute_trust_score(
            data_source=data_source,
            evidence_quality=evidence_quality,
            llm_guardrail=llm_guardrail,
            answer_judge=answer_judge,
            final_answer_source=final_answer_source,
            evidence_ledger=evidence_ledger,
            plan=plan,
        )
        stress_view = build_stress_view(
            data_source=data_source,
            trust_score=trust_score,
            llm_guardrail=llm_guardrail,
            answer_judge=answer_judge,
            final_answer_source=final_answer_source,
            evidence_quality=evidence_quality,
        )
        tool_trace.append("-> final answer")

        response = {
            "agent": self.name,
            "subtitle": "Explainable Bond Analysis Agent",
            "question": question,
            "plan": plan,
            "tools_used": report["tools_used"],
            "tool_trace": tool_trace,
            "data_evidence": report["data_evidence"],
            "data_source": data_source,
            "risk_explanations": risk_explanations,
            "evidence_quality": evidence_quality,
            "evidence_ledger": evidence_ledger,
            "answer_judge": answer_judge,
            "risk_profile": risk_profile,
            "trust_score": trust_score,
            "stress_view": stress_view,
            "analysis": report["analysis"],
            "risk_notes": report["risk_notes"],
            "limitations": limitations,
            "final_answer": final_answer,
            "final_answer_source": final_answer_source,
            "llm_enhanced_answer": llm_result["text"],
            "llm_guardrail": llm_guardrail,
            "used_llm": llm_result["status"] == "success",
            "used_llm_in_final": use_llm_final,
            "llm_status": llm_result["status"],
            "llm_error": llm_result["error"],
            "disclaimer": DISCLAIMER,
            "replay_id": None,
            "evidence_pack_id": None,
            "evidence_pack_paths": None,
        }
        validated = AgentResponse.model_validate(response).model_dump(mode="json")
        pack_meta = self._maybe_export_evidence_pack(validated)
        if pack_meta:
            validated["evidence_pack_id"] = pack_meta.get("id")
            validated["evidence_pack_paths"] = {
                "json_path": pack_meta.get("json_path"),
                "html_path": pack_meta.get("html_path"),
            }
        replay_record = save_replay(validated)
        if replay_record:
            validated["replay_id"] = replay_record["id"]
            if not validated.get("evidence_pack_id"):
                validated["evidence_pack_id"] = replay_record["id"]
        return AgentResponse.model_validate(validated).model_dump(mode="json")

    def _merge_limitations(self, limitations: list[str]) -> list[str]:
        merged = list(limitations)
        for item in LIMITATIONS_TEMPLATE:
            if item not in merged:
                merged.append(item)
        return merged

    def _append_data_source_limitations(self, limitations: list[str], data_source: dict, report: dict) -> list[str]:
        merged = list(limitations)
        coverage = data_source.get("maturity_coverage") or {}
        ratio = float(coverage.get("coverage_ratio") or 0)
        runtime = data_source.get("runtime_mode") or ""
        if runtime in {"live", "live_snapshot"}:
            source_counts = coverage.get("source_counts") or {}
            native = sum(int(v) for k, v in source_counts.items() if str(k).startswith("chinamoney"))
            if native > 0 and ratio >= 0.9:
                note = (
                    f"实时/快照源已保留 ChinaMoney 原生待偿期（覆盖率 {ratio:.1%}）；"
                    "永续风格 +N 主分析取首段行权窗口，并给出理论永续情景；不是完整含权永续定价。"
                )
            else:
                note = (
                    f"实时/快照源期限覆盖率 {ratio:.1%}；"
                    "未匹配或不可解析残期的债券同业分桶不可靠。"
                )
            if note not in merged:
                merged.append(note)
            perpetual_note = "永续风格残期（如 5Y+…+N）主分析取首段行权窗口，并并行理论永续 consol 久期；非完整含权定价。"
            if perpetual_note not in merged:
                merged.append(perpetual_note)
        quality = ((report.get("data_evidence") or {}).get("market") or {}).get("data_quality") or {}
        for issue in quality.get("issues") or []:
            msg = issue.get("message_zh")
            if msg and msg not in merged and issue.get("severity") in {"high", "medium"}:
                merged.append(f"数据质量：{msg}")
        return merged

    def _maybe_export_evidence_pack(self, response: dict) -> dict | None:
        if os.environ.get("BOND_EVIDENCE_PACK_ENABLED", "true").strip().lower() in {
            "0",
            "false",
            "no",
            "off",
        }:
            return None
        try:
            return export_evidence_pack(response, pack_id=response.get("replay_id"))
        except OSError:
            return None

    def _try_llm_answer(self, question: str, plan: dict, report: dict) -> dict:
        base_url = os.environ.get("OPENAI_BASE_URL")
        api_key = os.environ.get("OPENAI_API_KEY") or ("local-not-needed" if base_url else None)
        if not api_key:
            return {"text": None, "status": "disabled", "error": None}

        client = self._create_openai_client(api_key, base_url=base_url)
        models = self._filter_models_by_probe(client, self._llm_model_candidates())
        attempts = max(1, int(os.environ.get("OPENAI_RETRY_ATTEMPTS", "3")))
        last_error: Exception | None = None
        last_error_name = "UnknownError"
        for model in models:
            for attempt in range(attempts):
                try:
                    lang = self._detect_answer_lang(question)
                    instructions = self._llm_instructions(lang)
                    evidence_payload = self._build_llm_evidence(question, plan, report)
                    evidence_json = json.dumps(evidence_payload, ensure_ascii=False)
                    api_style = os.environ.get("OPENAI_API_STYLE", "auto").lower()
                    text_out = self._call_llm(
                        client, model, instructions, evidence_json, api_style, prefer_chat=bool(base_url)
                    )
                    if not text_out:
                        last_error_name = "empty_output"
                        continue
                    return {
                        "text": self._ensure_disclaimer(text_out.strip()),
                        "status": "success",
                        "error": None,
                        "model": model,
                    }
                except Exception as exc:  # noqa: BLE001 - vendor SDK/network surface is broad
                    last_error = exc
                    last_error_name = type(exc).__name__
                    # Retry transport failures with backoff; advance model on hard channel/auth errors.
                    if attempt + 1 < attempts and self._is_transient_llm_error(last_error_name, exc):
                        self._sleep_llm_retry(attempt, last_error_name)
                        continue
                    if self._is_hard_channel_error(last_error_name, exc):
                        break
                    return {
                        "text": None,
                        "status": "failed",
                        "error": f"OpenAI request failed: {last_error_name}",
                        "model": model,
                    }
        err_name = type(last_error).__name__ if last_error else last_error_name
        return {
            "text": None,
            "status": "failed",
            "error": f"OpenAI request failed: {err_name}",
            "model": models[-1] if models else None,
        }

    def _try_repair_llm_answer(
        self,
        question: str,
        plan: dict,
        report: dict,
        draft_text: str,
        guardrail: dict,
    ) -> dict:
        """One-shot rewrite when numeric guardrail fails on residual unit/invention issues."""
        base_url = os.environ.get("OPENAI_BASE_URL")
        api_key = os.environ.get("OPENAI_API_KEY") or ("local-not-needed" if base_url else None)
        if not api_key or not draft_text:
            return {"text": None, "status": "disabled", "error": None}

        unsupported = guardrail.get("unsupported_numbers") or []
        bad = ", ".join(
            str(item.get("text") or item.get("value")) for item in unsupported[:12]
        ) or "(unknown)"
        lang = self._detect_answer_lang(question)
        lang_rule = (
            "Write the entire repaired answer in Chinese (简体中文)."
            if lang == "zh"
            else "Write the entire repaired answer in English."
        )
        repair_instructions = (
            "You are repairing a fixed-income analysis draft that failed numeric evidence checks. "
            "Rewrite the draft so EVERY number appears in the JSON evidence. "
            f"Remove or correct these unsupported tokens: {bad}. "
            "Hard rules: "
            "1) 交易量(亿元)/volume is already in 亿元 — cite 3.8 or 4.934 as-is, never 380/493.4. "
            "2) modified_duration is years (e.g. 10.9372), never percent (never 10.94%). "
            "3) Do not invent peer percentiles such as 35%; use evidence values only "
            "(e.g. 28.57 / 63.8 / 71.43 / 83.2 / 96.7 when present). "
            "4) Prefer focused_numbers values exactly when present. "
            "5) Do not invent calendar dates or unit conversions. "
            "6) Keep the same bond focus and structure; no buy/sell advice. "
            "7) For market overviews: never invent bare percentages like 5%/10%/15%; "
            "use market_focus_numbers and quality-issue percents only. "
            f"{lang_rule} "
            f"Always include this disclaimer in Chinese: {DISCLAIMER}"
        )
        evidence_payload = self._build_llm_evidence(question, plan, report)
        evidence_payload = {
            **evidence_payload,
            "draft_to_repair": draft_text[:6000],
            "unsupported_tokens": [item.get("text") for item in unsupported[:12]],
        }
        evidence_json = json.dumps(evidence_payload, ensure_ascii=False)
        try:
            client = self._create_openai_client(api_key, base_url=base_url)
            models = self._filter_models_by_probe(client, self._llm_model_candidates())
            model = (
                models[0]
                if models
                else (os.environ.get("OPENAI_MODEL") or "deepseek-v4-flash-search").strip()
            )
            api_style = os.environ.get("OPENAI_API_STYLE", "auto").lower()
            text_out = self._call_llm(
                client, model, repair_instructions, evidence_json, api_style, prefer_chat=bool(base_url)
            )
            if not text_out:
                return {
                    "text": None,
                    "status": "failed",
                    "error": "empty_repair_output",
                    "model": model,
                }
            return {
                "text": self._ensure_disclaimer(text_out.strip()),
                "status": "success",
                "error": None,
                "model": model,
                "repaired": True,
            }
        except Exception as exc:  # noqa: BLE001 - vendor SDK/network surface is broad
            return {
                "text": None,
                "status": "failed",
                "error": f"OpenAI repair failed: {type(exc).__name__}",
                "model": model,
            }

    # Process-local cache: probed model ids from OpenAI-compatible /models.
    _probed_model_ids: set[str] | None = None
    _probed_model_base: str | None = None

    def _llm_model_candidates(self) -> list[str]:
        """Ordered model list: env primary -> env fallbacks -> host-sensible defaults.

        When OPENAI_BASE_URL is set, optionally reorder by a one-shot /models probe
        (see _filter_models_by_probe) so dead gpt/grok channels are skipped when a
        live deepseek (or other) id is available.
        """
        primary = (os.environ.get("OPENAI_MODEL") or "deepseek-v4-flash-search").strip()
        fallback_raw = (
            os.environ.get("OPENAI_MODEL_FALLBACKS")
            or os.environ.get("OPENAI_FALLBACK_MODEL")
            or ""
        )
        fallbacks = [part.strip() for part in fallback_raw.split(",") if part.strip()]
        default_pool = [
            "deepseek-v4-flash-search",
            "deepseek-chat",
            "gpt-5.4-mini",
            "gpt-5.4",
            "grok-4.5",
        ]
        if not fallbacks:
            if primary == "gpt-5.4":
                fallbacks = ["gpt-5.4-mini", "deepseek-v4-flash-search", "grok-4.5"]
            elif primary == "gpt-5.4-mini":
                fallbacks = ["deepseek-v4-flash-search", "grok-4.5"]
            elif primary == "grok-4.5":
                fallbacks = ["deepseek-v4-flash-search", "gpt-5.4-mini"]
            elif primary.startswith("deepseek"):
                fallbacks = ["gpt-5.4-mini", "grok-4.5", "gpt-5.4"]
            else:
                fallbacks = [m for m in default_pool if m != primary]
        ordered: list[str] = []
        for model in [primary, *fallbacks]:
            if model and model not in ordered:
                ordered.append(model)
        for model in default_pool:
            if model not in ordered:
                ordered.append(model)
        return ordered or [primary]

    def _probe_provider_model_ids(self, client) -> set[str] | None:
        """Best-effort list of model ids from an OpenAI-compatible gateway."""
        base_url = (os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
        if not base_url:
            return None
        if (
            BondAnalystAgent._probed_model_ids is not None
            and BondAnalystAgent._probed_model_base == base_url
        ):
            return BondAnalystAgent._probed_model_ids
        disable = (os.environ.get("OPENAI_MODEL_PROBE") or "1").strip().lower()
        if disable in {"0", "false", "no", "off"}:
            return None
        ids: set[str] = set()
        try:
            listed = client.models.list()
            data = getattr(listed, "data", None) or []
            for item in data:
                mid = getattr(item, "id", None)
                if mid is None and isinstance(item, dict):
                    mid = item.get("id")
                if mid:
                    ids.add(str(mid).strip())
        except Exception:  # noqa: BLE001 - probe is best-effort only
            BondAnalystAgent._probed_model_ids = set()
            BondAnalystAgent._probed_model_base = base_url
            return set()
        BondAnalystAgent._probed_model_ids = ids
        BondAnalystAgent._probed_model_base = base_url
        return ids

    def _filter_models_by_probe(self, client, models: list[str]) -> list[str]:
        """Prefer candidates that appear in /models; keep full list if probe empty."""
        available = self._probe_provider_model_ids(client)
        if not available:
            return models
        preferred = [m for m in models if m in available]
        if not preferred:
            for m in models:
                if any(m in a or a in m for a in available):
                    preferred.append(m)
        tail = [m for m in models if m not in preferred]
        return preferred + tail if preferred else models

    def _detect_answer_lang(self, question: str) -> str:
        if re.search(r"[\u4e00-\u9fff]", question or ""):
            return "zh"
        return "en"

    def _llm_instructions(self, lang: str) -> str:
        lang_rule = (
            "Write the entire answer in Chinese (简体中文)."
            if lang == "zh"
            else "Write the entire answer in English."
        )
        overview_en_rule = ""
        if lang == "en":
            overview_en_rule = (
                "English market-overview hard rules: "
                "Prefer market_focus_numbers for sample_count / yield_* / volume_* / coverage_percent. "
                "Cite yields as the evidence values with a trailing % only when the field is a yield "
                "(e.g. mean 2.7709%). Do NOT invent bare share percentages such as 5%, 10%, 15%, "
                "20%, 25%, 30% for 'about X% of the sample', growth, coverage, or type mix unless "
                "that exact percentage token already appears in evidence "
                "(quality issues or market_focus_numbers.allowed_quality_percents). "
                "Do not paraphrase p25/p75 into '25th/75th percentile share of the market'. "
                "If you need a missing-yield share, copy the quality issue text (e.g. 7.8%) exactly. "
                "Never invent round textbook shares (5/10/15/20/25/30) when evidence lacks them. "
                "When unsure, omit the percentage rather than inventing one. "
            )
        return (
            "You are a fixed-income analysis assistant. Use only the provided JSON evidence. "
            "Copy numeric evidence exactly when citing it. Do not invent any number that is not "
            "present in the JSON (including percentiles, spreads, counts, and percentages). "
            "When citing quartiles, prefer labels p25/p75 with their evidence values "
            "(e.g. p25=2.255). Do not invent 25%/75% market-share claims. "
            "Yield fields like 收盘到期收益率(%) are already in percent units: cite them as "
            "2.5647% only when that exact value appears in evidence. "
            "coverage_ratio is 0–1; prefer maturity_coverage.coverage_percent (e.g. 99.94) "
            "or write coverage_ratio=0.9994. Do not invent other coverage percentages. "
            "volume_summary and 交易量(亿元) are already in 亿元. Cite 4.934 or 3.8 as-is; "
            "never convert units (do not invent 493.4 or 380). "
            "Quality notes may already contain percentages such as 7.8% / 7.4%; copy them "
            "verbatim if needed, never recompute shares. "
            "For signed peer spreads / z-scores / bp fields, prefer ASCII hyphen-minus "
            "(e.g. -5.95 bp). If you describe magnitude only, keep the same absolute value "
            "that appears in evidence and say it is the absolute peer-mean spread. "
            "Never invent spreads that are not present. "
            "Do not invent calendar dates, years, or report timestamps. "
            "Do not create new percentages, ranges, ratings, issuer details, market facts, "
            "or investment advice. "
            "Do not recommend buying, selling, adding position, guaranteed returns, "
            "or very safe conclusions. "
            "Avoid the bare phrase risk-free entirely; if needed, write 'not free of risk' "
            "or 'no investment advice' instead of 'not risk-free'. "
            "If evidence includes modified_duration / dv01 / macaulay_duration, quote them as "
            "cashflow teaching values under annual coupon=YTM assumptions, not OAS. "
            "Write durations as years (e.g. 10.9372), never as percent (never 10.94%). "
            "Do not invent peer percentiles such as 35% unless that exact value is in evidence. "
            "When focused_numbers is present, prefer those exact values for the subject bond. "
            "When market_focus_numbers is present (overviews), prefer those exact market stats. "
            "Cite peer_yield_percentile as 28.57% only if evidence has 28.57; never invent 35%. "
            "Cite volume as 3.8 (亿元), never 380. Cite DV01 as 0.109372 or a 4-dp truncation, not percent. "
            "For perpetual-style, mention first-leg vs theoretical consol scenarios when present. "
            "The yield_distribution values are counts, not percentages. "
            "If a requested bond is present under data_evidence.search.records, focus on that bond "
            "and quote its 收盘到期收益率(%) / 加权收益率(%) / 待偿期 exactly. "
            "If the evidence is insufficient, say so directly. "
            f"{overview_en_rule}"
            f"{lang_rule} "
            f"Always include this disclaimer in Chinese: {DISCLAIMER}"
        )

    def _is_transient_llm_error(self, error_name: str, exc: Exception | None = None) -> bool:
        if error_name in {
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "InternalServerError",
            "TimeoutError",
            "ConnectTimeout",
            "ReadTimeout",
            "APIStatusError",
            "ServiceUnavailableError",
        }:
            return True
        message = str(exc or "").lower()
        return any(
            token in message
            for token in (
                "rate limit",
                "too many requests",
                "429",
                "500",
                "502",
                "503",
                "504",
                "timeout",
                "temporar",
                "overloaded",
                "upstream",
            )
        ) and not self._is_hard_channel_error(error_name, exc)

    def _is_hard_channel_error(self, error_name: str, exc: Exception | None = None) -> bool:
        if error_name in {
            "AuthenticationError",
            "PermissionDeniedError",
            "NotFoundError",
            "BadRequestError",
        }:
            return True
        message = str(exc or "").lower()
        return any(
            token in message
            for token in (
                "no available channel",
                "model not found",
                "does not exist",
                "invalid model",
                "invalid api key",
                "incorrect api key",
            )
        )

    def _sleep_llm_retry(self, attempt: int, error_name: str) -> None:
        """Backoff before retrying 429/5xx/timeout. Tests can set OPENAI_RETRY_BACKOFF=0."""
        base = float(os.environ.get("OPENAI_RETRY_BACKOFF", "0.8"))
        if base <= 0:
            return
        # Rate limits get a slightly longer pause.
        factor = 1.8 if error_name == "RateLimitError" else 1.0
        delay = min(8.0, base * factor * (2 ** max(0, attempt)))
        time.sleep(delay)

    def _build_llm_evidence(self, question: str, plan: dict, report: dict) -> dict:
        """Compact evidence payload for the LLM to reduce hallucinated numbers."""
        evidence = report.get("data_evidence") or {}
        market = evidence.get("market") or {}
        search = evidence.get("search") or {}
        comparison = evidence.get("comparison") or {}
        ranking = evidence.get("ranking") or {}
        outliers = evidence.get("outliers") or {}
        data_source = report.get("data_source") or {}
        quality = market.get("data_quality") or {}
        coverage = data_source.get("maturity_coverage") or {}
        coverage_ratio = coverage.get("coverage_ratio")
        coverage_percent = None
        if isinstance(coverage_ratio, int | float) and math.isfinite(float(coverage_ratio)):
            # Explicit percent form so models can cite 99.94% without inventing scale.
            coverage_percent = round(float(coverage_ratio) * 100.0, 4)

        compact_market = {
            "sample_count": market.get("sample_count"),
            "yield_summary": market.get("yield_summary"),
            # Volume stays in 亿元 — never convert units in the answer.
            "volume_summary": market.get("volume_summary"),
            "volume_unit": "亿元",
            "volume_unit_note": "Values are already in 亿元; do not convert to million/100m CNY.",
            "maturity_summary_years": market.get("maturity_summary_years"),
            "segments": {
                "by_bond_type": ((market.get("segments") or {}).get("by_bond_type") or [])[:6],
                "by_maturity_bucket": ((market.get("segments") or {}).get("by_maturity_bucket") or [])[:6],
            }
            if market.get("segments")
            else None,
            "data_quality": {
                "score": quality.get("score"),
                "level": quality.get("level"),
                "missing_yield_count": quality.get("missing_yield_count"),
                "extreme_yield_count": quality.get("extreme_yield_count"),
                # Keep issue messages: they already contain audited percent shares like 7.8%.
                "issues": [
                    {
                        "id": issue.get("id"),
                        "severity": issue.get("severity"),
                        "message_zh": issue.get("message_zh"),
                        "message_en": issue.get("message_en"),
                    }
                    for issue in (quality.get("issues") or [])[:4]
                    if isinstance(issue, dict)
                ],
            },
        }
        compact_search = {
            "criteria": search.get("criteria"),
            "match_count": search.get("match_count"),
            "records": (search.get("records") or [])[:3],
        }
        compact_comparison = comparison
        if isinstance(comparison, dict):
            rate = comparison.get("rate_sensitivity") or {}
            peer = comparison.get("peer_comparison") or {}
            compact_comparison = {
                "bond_name": comparison.get("bond_name") or comparison.get("name"),
                "record": comparison.get("record"),
                "market_median": comparison.get("market_median") or comparison.get("median"),
                "vs_market": comparison.get("vs_market") or comparison.get("comparison"),
                "notes": comparison.get("notes") or comparison.get("analysis"),
                "peer_comparison": {
                    "bond_type": peer.get("bond_type"),
                    "maturity_bucket": peer.get("maturity_bucket"),
                    "peer_count": peer.get("peer_count"),
                    "peer_yield_mean": peer.get("peer_yield_mean"),
                    "peer_yield_median": peer.get("peer_yield_median"),
                    "peer_yield_percentile": peer.get("peer_yield_percentile"),
                    "peer_yield_zscore": peer.get("peer_yield_zscore"),
                    "spread_vs_peer_mean_bp": peer.get("spread_vs_peer_mean_bp"),
                    "note_zh": peer.get("note_zh"),
                    "note_en": peer.get("note_en"),
                }
                if peer
                else None,
                "rate_sensitivity": {
                    "modified_duration": rate.get("modified_duration") or rate.get("modified_duration_approx"),
                    "macaulay_duration": rate.get("macaulay_duration"),
                    "dv01": rate.get("dv01") or rate.get("dv01_approx"),
                    "perpetual_modified_duration": rate.get("perpetual_modified_duration"),
                    "method": rate.get("method"),
                    "is_perpetual_style": rate.get("is_perpetual_style"),
                    "scenarios": rate.get("scenarios") or [],
                    "assumptions_zh": rate.get("assumptions_zh"),
                    "assumptions_en": rate.get("assumptions_en"),
                },
                "credit_context": comparison.get("credit_context"),
            }
        return {
            "question": question,
            "plan": {
                "intent": plan.get("intent"),
                "search_params": plan.get("search_params"),
                "requested_tools": plan.get("requested_tools"),
            },
            "data_source": {
                "source_name": data_source.get("source_name"),
                "runtime_mode": data_source.get("runtime_mode"),
                "row_count": data_source.get("row_count"),
                "valid_yield_count": data_source.get("valid_yield_count"),
                "maturity_coverage": {
                    "filled_count": coverage.get("filled_count"),
                    "missing_count": coverage.get("missing_count"),
                    "coverage_ratio": coverage_ratio,
                    "coverage_percent": coverage_percent,
                },
            },
            "data_evidence": {
                "market": compact_market,
                "search": compact_search,
                "comparison": compact_comparison,
                "ranking": {
                    "rank_by": ranking.get("rank_by"),
                    "items": (ranking.get("items") or ranking.get("records") or [])[:5],
                },
                "outliers": {
                    "outlier_count": outliers.get("outlier_count"),
                    "items": (outliers.get("items") or outliers.get("records") or [])[:5],
                },
            },
            "citation_rules": {
                "volume_unit": "亿元",
                "do_not_convert_volume": True,
                "duration_is_years_not_percent": True,
                "only_cite_evidence_numbers": True,
                "forbid_invented_percentiles": True,
            },
            # Preferred citation targets for single-bond reports (copy exactly).
            "focused_numbers": {
                "bond_name": ((search.get("records") or [{}])[0] or {}).get("债券简称")
                or comparison.get("bond_name"),
                "yield_pct": ((search.get("records") or [{}])[0] or {}).get("收盘到期收益率(%)"),
                "volume_yi": ((search.get("records") or [{}])[0] or {}).get("交易量(亿元)"),
                "maturity_years": ((search.get("records") or [{}])[0] or {}).get("待偿期(年)"),
                "clean_price": ((search.get("records") or [{}])[0] or {}).get("收盘净价(元)"),
                "modified_duration_years": (comparison.get("rate_sensitivity") or {}).get(
                    "modified_duration"
                ),
                "dv01": (comparison.get("rate_sensitivity") or {}).get("dv01"),
                "yield_percentile": comparison.get("yield_percentile"),
                "volume_percentile": comparison.get("volume_percentile"),
                "maturity_percentile": comparison.get("maturity_percentile"),
                "peer_yield_percentile": (comparison.get("peer_comparison") or {}).get(
                    "peer_yield_percentile"
                ),
                "spread_vs_peer_mean_bp": (comparison.get("peer_comparison") or {}).get(
                    "spread_vs_peer_mean_bp"
                ),
                "coverage_percent": coverage_percent
                if coverage_percent is not None
                else coverage.get("coverage_percent"),
            },
            # Preferred citation targets for market overviews (copy exactly; no invented %).
            "market_focus_numbers": {
                "sample_count": market.get("sample_count"),
                "yield_count": (market.get("yield_summary") or {}).get("count"),
                "yield_mean": (market.get("yield_summary") or {}).get("mean"),
                "yield_median": (market.get("yield_summary") or {}).get("median"),
                "yield_p25": (market.get("yield_summary") or {}).get("p25"),
                "yield_p75": (market.get("yield_summary") or {}).get("p75"),
                "yield_min": (market.get("yield_summary") or {}).get("min"),
                "yield_max": (market.get("yield_summary") or {}).get("max"),
                "volume_mean_yi": (market.get("volume_summary") or {}).get("mean"),
                "volume_median_yi": (market.get("volume_summary") or {}).get("median"),
                "coverage_percent": coverage_percent
                if coverage_percent is not None
                else coverage.get("coverage_percent"),
                "quality_score": quality.get("score"),
                "allowed_quality_percents": [
                    issue.get("message_en") or issue.get("message_zh")
                    for issue in (quality.get("issues") or [])[:4]
                    if isinstance(issue, dict)
                ],
            },
            # Keep short analysis bullets; they already carry audited numbers.
            "analysis": (report.get("analysis") or [])[:8],
            "risk_notes": (report.get("risk_notes") or [])[:6],
            "limitations": (report.get("limitations") or [])[:6],
        }

    def _create_openai_client(self, api_key: str, base_url: str | None = None):
        from openai import OpenAI

        # Gateways (new-api / deepseek) often need longer than the old 20s default.
        timeout = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "90"))
        kwargs = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    def _call_llm(self, client, model: str, instructions: str, evidence_json: str, api_style: str, prefer_chat: bool = False) -> str | None:
        if api_style not in {"auto", "responses", "chat"}:
            raise ValueError("OPENAI_API_STYLE must be one of: auto, responses, chat")

        if api_style == "chat" or (api_style == "auto" and prefer_chat):
            return self._call_chat_completions(client, model, instructions, evidence_json)

        try:
            return self._call_responses_api(client, model, instructions, evidence_json)
        except Exception:
            if api_style == "responses":
                raise
            return self._call_chat_completions(client, model, instructions, evidence_json)

    def _call_responses_api(self, client, model: str, instructions: str, evidence_json: str) -> str | None:
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=evidence_json,
        )
        return getattr(response, "output_text", None)

    def _call_chat_completions(self, client, model: str, instructions: str, evidence_json: str) -> str | None:
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": evidence_json},
            ],
        )
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        return getattr(message, "content", None)

    def _call_chat_completions_stream(self, client, model: str, instructions: str, evidence_json: str):
        """Yield text deltas from chat.completions stream=True."""
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": evidence_json},
            ],
            stream=True,
        )
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta is not None else None
            if content:
                yield content

    def iter_answer_events(self, question: str):
        """Yield structured progress events for SSE/token streaming UI.

        Event types:
        - status: pipeline stage
        - token: LLM text delta (only when streaming LLM path is used)
        - final: complete AgentResponse-compatible dict
        - error: fatal
        """
        question = question.strip() or "请概览当前债券市场样本。"
        yield {"type": "status", "stage": "resolve_data", "message_zh": "解析数据源（auto/live/static）…", "message_en": "Resolving data source (auto/live/static)…"}
        data_frame, data_source = resolve_bond_data(
            mode=self.data_mode,
            path=self.data_path or None,
            live_fetcher=self.live_fetcher,
        )
        yield {
            "type": "status",
            "stage": "plan",
            "message_zh": f"规划意图与工具…（模式 {data_source.get('runtime_mode')}）",
            "message_en": f"Planning intent/tools… (mode {data_source.get('runtime_mode')})",
        }
        plan = classify_intent(question, data_path=self.data_path, data_frame=data_frame)
        tool_outputs: list[dict] = []
        tool_trace: list[str] = [
            f"User question: {question}",
            f"-> data_source(mode={data_source['runtime_mode']}, source={data_source['source_id']})",
            f"-> planner(intent={plan['intent']})",
        ]
        report = None
        tool_labels = {
            "search_bonds": ("检索债券", "Search bonds"),
            "compare_bond_to_market": ("同业/市场对比", "Peer/market compare"),
            "describe_market": ("市场概览统计", "Market overview stats"),
            "rank_bonds": ("收益率排序", "Rank yields"),
            "detect_yield_outliers": ("异常收益检测", "Detect yield outliers"),
            "build_market_monitor": ("截面监控面板", "Market monitor panel"),
            "generate_bond_report": ("组装证据报告", "Assemble evidence report"),
        }
        for tool_name in plan["requested_tools"]:
            zh_label, en_label = tool_labels.get(tool_name, (tool_name, tool_name))
            yield {
                "type": "status",
                "stage": "tool",
                "tool": tool_name,
                "message_zh": f"执行工具：{zh_label}（{tool_name}）…",
                "message_en": f"Running tool: {en_label} ({tool_name})…",
            }
            if tool_name == "search_bonds":
                result = search_bonds(**plan["search_params"], data_frame=data_frame)
                tool_outputs.append(result)
                tool_trace.append(f"-> search_bonds({self._compact_args(plan['search_params'])})")
            elif tool_name == "compare_bond_to_market":
                search_result = self._find_tool_output(tool_outputs, "search_bonds")
                first_record = (search_result.get("records") or [None])[0] if search_result else None
                result = compare_bond_to_market(
                    bond_name=plan["search_params"].get("name"),
                    record=first_record,
                    data_frame=data_frame,
                )
                tool_outputs.append(result)
                tool_trace.append("-> compare_bond_to_market()")
            elif tool_name == "describe_market":
                result = describe_market(
                    data_frame=data_frame,
                    maturity_coverage=data_source.get("maturity_coverage"),
                    runtime_mode=data_source.get("runtime_mode"),
                )
                tool_outputs.append(result)
                tool_trace.append("-> describe_market()")
            elif tool_name == "rank_bonds":
                result = rank_bonds(
                    by=plan["rank_by"] or "yield",
                    top_n=5,
                    ascending=plan["ascending"],
                    data_frame=data_frame,
                )
                tool_outputs.append(result)
                tool_trace.append(f"-> rank_bonds(by={plan['rank_by'] or 'yield'}, top_n=5)")
            elif tool_name == "detect_yield_outliers":
                result = detect_yield_outliers(method="zscore", threshold=3.0, top_n=5, data_frame=data_frame)
                tool_outputs.append(result)
                tool_trace.append("-> detect_yield_outliers(method=zscore, threshold=3.0)")
            elif tool_name == "build_market_monitor":
                result = build_market_monitor(top_n=5, data_frame=data_frame)
                tool_outputs.append(result)
                tool_trace.append("-> build_market_monitor(top_n=5)")
            elif tool_name == "generate_bond_report":
                report = generate_bond_report(question, tool_outputs, plan=plan)
                tool_trace.append("-> generate_bond_report()")
        if report is None:
            report = generate_bond_report(question, tool_outputs, plan=plan)
            tool_trace.append("-> generate_bond_report()")

        risk_explanations = retrieve_risk_explanations(question, report)
        evidence_quality = assess_evidence_quality(plan, report, data_source, risk_explanations)
        report["data_source"] = data_source
        report["risk_explanations"] = risk_explanations
        report["evidence_quality"] = evidence_quality

        is_advisory_refusal = plan.get("intent") == "advisory_refusal"
        llm_text_parts: list[str] = []  # optional accumulate
        if is_advisory_refusal:
            yield {"type": "status", "stage": "policy", "message_zh": "投资建议政策拦截…", "message_en": "Advisory policy block…"}
            fallback_answer = self._format_advisory_refusal(question, report, plan)
            llm_result = {"text": None, "status": "disabled", "error": "advisory_policy_block"}
            llm_guardrail = assess_llm_faithfulness(None, report)
            tool_trace.append("-> input_policy(status=advisory_refusal)")
            tool_trace.append("-> llm_guardrail(skipped: advisory_policy_block)")
            use_llm_final = False
            final_answer = fallback_answer
            final_answer_source = "deterministic_fallback"
        else:
            fallback_answer = self._format_report(report, plan)
            yield {
                "type": "status",
                "stage": "llm",
                "message_zh": "准备调用模型通道（可流式；约 8–40 秒；超时/无通道将诚实回退确定性报告）…",
                "message_en": "Preparing model channel (streamable; often 8–40s; timeout/no-channel fails over to deterministic report)…",
            }
            # Live token path: consume streaming generator and re-yield token events immediately.
            llm_result = {"text": None, "status": "disabled", "error": None}
            for event in self._iter_llm_answer_events(question, plan, report):
                if event.get("type") == "token":
                    yield event
                    llm_text_parts.append(event.get("text") or "")
                elif event.get("type") == "status":
                    yield event
                elif event.get("type") == "llm_result":
                    llm_result = event.get("result") or llm_result
            if llm_result.get("status") == "failed":
                err = llm_result.get("error") or "unknown"
                err_l = str(err).lower()
                if any(tok in err_l for tok in ("no available channel", "model not found", "does not exist")):
                    zh_msg = f"模型通道不可用（{err}）→ 已改用确定性报告（护栏未放行编造数字）。"
                    en_msg = f"Model channel unavailable ({err}) → using deterministic report (guardrail still closed)."
                elif any(tok in err_l for tok in ("timeout", "timed out", "429", "rate limit", "503", "502", "504")):
                    zh_msg = f"模型通道抖动/超时（{err}）→ 已改用确定性报告。"
                    en_msg = f"Model channel flaky/timeout ({err}) → using deterministic report."
                else:
                    zh_msg = f"模型通道失败（{err}）→ 已改用确定性报告。"
                    en_msg = f"Model channel failed ({err}) → using deterministic report."
                yield {
                    "type": "status",
                    "stage": "llm_fallback",
                    "message_zh": zh_msg,
                    "message_en": en_msg,
                }
            elif llm_result.get("status") == "success":
                yield {
                    "type": "status",
                    "stage": "guardrail",
                    "message_zh": "模型文本已返回，正在跑数值/语言护栏…",
                    "message_en": "Model text returned; running numeric/language guardrail…",
                }
            llm_guardrail = (
                assess_llm_faithfulness(llm_result.get("text"), report)
                if llm_result.get("status") == "success"
                else assess_llm_faithfulness(None, report)
            )
            if (
                llm_result.get("status") == "success"
                and llm_guardrail.get("status") != "passed"
                and llm_result.get("text")
            ):
                yield {
                    "type": "status",
                    "stage": "llm_repair",
                    "message_zh": "护栏未通过，尝试一次数值修复重写（仍须再次通过护栏；可能额外 10–30 秒）…",
                    "message_en": "Guardrail failed; one numeric repair rewrite (must re-pass; may add 10–30s)…",
                }
                repaired = self._try_repair_llm_answer(
                    question, plan, report, llm_result.get("text") or "", llm_guardrail
                )
                if repaired.get("status") == "success" and repaired.get("text"):
                    repaired_guard = assess_llm_faithfulness(repaired["text"], report)
                    tool_trace.append(
                        f"-> llm_guardrail_repair(status={repaired_guard['status']})"
                    )
                    if repaired_guard["status"] == "passed":
                        llm_result = repaired
                        llm_guardrail = repaired_guard
            if llm_result["status"] == "success":
                tool_trace.append(f"-> llm_guardrail(status={llm_guardrail['status']})")
                if llm_guardrail["status"] != "passed":
                    yield {
                        "type": "status",
                        "stage": "llm_fallback",
                        "message_zh": "护栏未通过 → 已改用确定性报告。",
                        "message_en": "Guardrail rejected model text → using deterministic report.",
                    }
            else:
                tool_trace.append(f"-> llm_guardrail(skipped: llm_{llm_result['status']})")
            use_llm_final = llm_result["status"] == "success" and llm_guardrail["status"] == "passed"
            final_answer = llm_result["text"] if use_llm_final else fallback_answer
            final_answer_source = "llm" if use_llm_final else "deterministic_fallback"

        answer_judge = judge_answer(
            llm_status=llm_result["status"],
            llm_guardrail=llm_guardrail,
            evidence_quality=evidence_quality,
            final_answer_source=final_answer_source,
        )
        risk_profile = build_risk_profile(report, data_source, evidence_quality, llm_guardrail)
        evidence_ledger = build_evidence_ledger(
            plan=plan,
            report=report,
            data_source=data_source,
            evidence_quality=evidence_quality,
            llm_guardrail=llm_guardrail,
            final_answer_source=final_answer_source,
        )
        limitations = self._merge_limitations(report.get("limitations") or [])
        limitations = self._append_data_source_limitations(limitations, data_source, report)
        if is_advisory_refusal:
            policy_note = "输入政策：问题含买卖/保证收益等投资建议诉求，已拦截推荐路径。"
            if policy_note not in limitations:
                limitations.insert(0, policy_note)
        trust_score = compute_trust_score(
            data_source=data_source,
            evidence_quality=evidence_quality,
            llm_guardrail=llm_guardrail,
            answer_judge=answer_judge,
            final_answer_source=final_answer_source,
            evidence_ledger=evidence_ledger,
            plan=plan,
        )
        stress_view = build_stress_view(
            data_source=data_source,
            trust_score=trust_score,
            llm_guardrail=llm_guardrail,
            answer_judge=answer_judge,
            final_answer_source=final_answer_source,
            evidence_quality=evidence_quality,
        )
        tool_trace.append("-> final answer")
        response = {
            "agent": self.name,
            "subtitle": "Explainable Bond Analysis Agent",
            "question": question,
            "plan": plan,
            "tools_used": report["tools_used"],
            "tool_trace": tool_trace,
            "data_evidence": report["data_evidence"],
            "data_source": data_source,
            "risk_explanations": risk_explanations,
            "evidence_quality": evidence_quality,
            "evidence_ledger": evidence_ledger,
            "answer_judge": answer_judge,
            "risk_profile": risk_profile,
            "trust_score": trust_score,
            "stress_view": stress_view,
            "analysis": report["analysis"],
            "risk_notes": report["risk_notes"],
            "limitations": limitations,
            "final_answer": final_answer,
            "final_answer_source": final_answer_source,
            "llm_enhanced_answer": llm_result["text"],
            "llm_guardrail": llm_guardrail,
            "used_llm": llm_result["status"] == "success",
            "used_llm_in_final": use_llm_final,
            "llm_status": llm_result["status"],
            "llm_error": llm_result["error"],
            "disclaimer": DISCLAIMER,
            "replay_id": None,
            "evidence_pack_id": None,
            "evidence_pack_paths": None,
        }
        validated = AgentResponse.model_validate(response).model_dump(mode="json")
        pack_meta = self._maybe_export_evidence_pack(validated)
        if pack_meta:
            validated["evidence_pack_id"] = pack_meta.get("id")
            validated["evidence_pack_paths"] = {
                "json_path": pack_meta.get("json_path"),
                "html_path": pack_meta.get("html_path"),
            }
        replay_record = save_replay(validated)
        if replay_record:
            validated["replay_id"] = replay_record["id"]
            if not validated.get("evidence_pack_id"):
                validated["evidence_pack_id"] = replay_record["id"]
        validated = AgentResponse.model_validate(validated).model_dump(mode="json")
        yield {"type": "final", "result": validated}

    def _iter_llm_answer_events(self, question: str, plan: dict, report: dict):
        """Yield token events then a final llm_result event for streaming UIs."""
        base_url = os.environ.get("OPENAI_BASE_URL")
        api_key = os.environ.get("OPENAI_API_KEY") or ("local-not-needed" if base_url else None)
        if not api_key:
            yield {"type": "llm_result", "result": {"text": None, "status": "disabled", "error": None}}
            return

        client = self._create_openai_client(api_key, base_url=base_url)
        models = self._filter_models_by_probe(client, self._llm_model_candidates())
        attempts = max(1, int(os.environ.get("OPENAI_RETRY_ATTEMPTS", "3")))
        last_error: Exception | None = None
        last_error_name = "UnknownError"
        for model in models:
            for attempt in range(attempts):
                try:
                    lang = self._detect_answer_lang(question)
                    instructions = self._llm_instructions(lang)
                    evidence_payload = self._build_llm_evidence(question, plan, report)
                    evidence_json = json.dumps(evidence_payload, ensure_ascii=False)
                    if attempt == 0:
                        yield {
                            "type": "status",
                            "stage": "llm",
                            "message_zh": f"调用模型 {model}（流式）…",
                            "message_en": f"Calling model {model} (streaming)…",
                            "model": model,
                        }
                    else:
                        yield {
                            "type": "status",
                            "stage": "llm_retry",
                            "message_zh": f"模型通道瞬时失败，正在重试 {model}（第 {attempt + 1}/{attempts} 次）…",
                            "message_en": f"Transient model failure; retrying {model} ({attempt + 1}/{attempts})…",
                            "model": model,
                            "attempt": attempt + 1,
                        }
                    parts: list[str] = []
                    try:
                        for delta in self._call_chat_completions_stream(client, model, instructions, evidence_json):
                            parts.append(delta)
                            yield {"type": "token", "text": delta, "model": model}
                        text_out = "".join(parts).strip()
                    except Exception as stream_exc:
                        # Provider may not support stream; fall back once.
                        message = str(stream_exc).lower()
                        if "stream" in message or type(stream_exc).__name__ in {
                            "TypeError",
                            "AttributeError",
                            "BadRequestError",
                            "APIError",
                        }:
                            yield {
                                "type": "status",
                                "stage": "llm",
                                "message_zh": f"流式不可用，改用非流式补全（{model}）…",
                                "message_en": f"Streaming unavailable; falling back to non-stream completion ({model})…",
                                "model": model,
                            }
                            text_out = self._call_chat_completions(client, model, instructions, evidence_json)
                        else:
                            raise
                    if not text_out:
                        last_error_name = "empty_output"
                        continue
                    yield {
                        "type": "llm_result",
                        "result": {
                            "text": self._ensure_disclaimer(text_out.strip()),
                            "status": "success",
                            "error": None,
                            "model": model,
                        },
                    }
                    return
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    last_error_name = type(exc).__name__
                    if attempt + 1 < attempts and self._is_transient_llm_error(last_error_name, exc):
                        self._sleep_llm_retry(attempt, last_error_name)
                        continue
                    if self._is_hard_channel_error(last_error_name, exc):
                        break
                    yield {
                        "type": "llm_result",
                        "result": {
                            "text": None,
                            "status": "failed",
                            "error": f"OpenAI request failed: {last_error_name}",
                            "model": model,
                        },
                    }
                    return
        err_name = type(last_error).__name__ if last_error else last_error_name
        yield {
            "type": "llm_result",
            "result": {
                "text": None,
                "status": "failed",
                "error": f"OpenAI request failed: {err_name}",
                "model": models[-1] if models else None,
            },
        }

    def _ensure_disclaimer(self, text: str) -> str:
        if DISCLAIMER in text:
            return text
        return f"{text}\n\n{DISCLAIMER}"

    def _format_advisory_refusal(self, question: str, report: dict, plan: dict) -> str:
        """Deterministic non-advisory boundary response for buy/sell solicitations."""
        evidence = report.get("data_evidence") or {}
        market = evidence.get("market") or {}
        data_source = report.get("data_source") or {}
        yield_summary = market.get("yield_summary") or {}
        median = yield_summary.get("median")
        rows = data_source.get("row_count")
        coverage = data_source.get("maturity_coverage") or {}
        coverage_ratio = coverage.get("coverage_ratio")
        lang = self._detect_answer_lang(question)
        coverage_text = (
            f"{round(float(coverage_ratio) * 100, 1)}%"
            if coverage_ratio is not None
            else ("unknown" if lang == "en" else "未知")
        )
        quality = market.get("data_quality") or {}
        quality_score = quality.get("score")

        if lang == "en":
            lines = [
                f"Question: {question}",
                "Intent: advisory_refusal (investment-advice blocked)",
                "",
                "Policy decision:",
                "- The question asks for buy/sell advice, guaranteed returns, or safety promises.",
                "- BondLens only provides auditable market evidence and risk boundaries, not investment advice.",
                "",
                "Factual context only (not a trading basis):",
                f"- Data source: {data_source.get('source_name')} ({data_source.get('runtime_mode')})",
                f"- Sample rows: {rows if rows is not None else '—'}",
            ]
            if median is not None:
                lines.append(f"- Sample median yield: {median}%")
            if quality_score is not None:
                lines.append(f"- Data quality: {quality_score}/100")
            lines.append(f"- Maturity coverage: {coverage_text}")
            lines.extend(
                [
                    "",
                    "Hard boundary:",
                    "- No specific buy, sell, add-position, or allocation recommendation.",
                    "- No return guarantee and no safety conclusion on any bond.",
                    "- Higher or lower yield is a risk signal, not a trading basis.",
                    "",
                    "Ask research questions instead, for example:",
                    "- What does the current sample yield distribution look like?",
                    "- How does a specific bond compare with market percentiles and peers?",
                    "- Which yields look like outliers, and where is maturity coverage missing?",
                    "",
                    f"{DISCLAIMER}",
                ]
            )
            return "\n".join(lines)

        lines = [
            f"问题：{question}",
            "意图：投资建议拦截（advisory_refusal）",
            "",
            "输入政策判定：",
            "- 该问题包含买卖建议、保证收益或安全承诺类诉求。",
            "- BondLens 只提供可审查的市场证据与风险边界，不提供投资建议。",
            "",
            "可提供的事实上下文（非买卖依据）：",
            f"- 数据源：{data_source.get('source_name')}（{data_source.get('runtime_mode')}）",
            f"- 样本行数：{rows if rows is not None else '—'}",
        ]
        if median is not None:
            lines.append(f"- 样本中位收益率：{median}%")
        if quality_score is not None:
            lines.append(f"- 数据质量：{quality_score}/100")
        lines.append(f"- 期限覆盖率：{coverage_text}")
        lines.extend(
            [
                "",
                "明确边界：",
                "- 不给出具体买入、卖出、加仓或配置标的。",
                "- 不提供任何收益担保，也不对债券安全性作结论。",
                "- 收益率高低是风险信号，不是买卖依据。",
                "",
                "如需研究支持，请改问：",
                "- 当前样本收益率分布如何？",
                "- 某只具体债券相对市场的分位数与可比同业如何？",
                "- 哪些样本收益率异常、期限覆盖缺口在哪里？",
                "",
                f"{DISCLAIMER}",
            ]
        )
        return "\n".join(lines)

    def _format_report(self, report: dict, plan: dict) -> str:
        evidence = report["data_evidence"]
        market = evidence.get("market") or {}
        ranking = evidence.get("ranking") or {}
        outliers = evidence.get("outliers") or {}
        comparison = evidence.get("comparison") or {}
        lang = self._detect_answer_lang(report.get("question") or "")
        en = lang == "en"

        def L(zh: str, english: str) -> str:
            return english if en else zh

        lines = [
            f"{L('问题', 'Question')}: {report['question']}",
            f"{L('意图', 'Intent')}: {plan['intent']}",
            "",
            f"{L('使用的工具', 'Tools Used')}:",
            *[f"- {tool}" for tool in report["tools_used"]],
            "",
            f"{L('数据证据', 'Data Evidence')}:",
        ]

        data_source = report.get("data_source") or {}
        evidence_quality = report.get("evidence_quality") or {}
        risk_explanations = report.get("risk_explanations") or []
        if data_source:
            lines.append(
                f"- {L('数据源', 'Data source')}: {data_source.get('source_name')} ({data_source.get('runtime_mode')})"
            )
            if data_source.get("fetched_at"):
                lines.append(f"- {L('获取时间', 'Fetched at')}: {data_source.get('fetched_at')}")
            if data_source.get("fallback_reason"):
                lines.append(
                    f"- {L('实时数据降级原因', 'Live fallback reason')}: {data_source.get('fallback_reason')}"
                )
            lines.append(
                f"- {L('样本行数', 'Sample rows')}: {data_source.get('row_count')}"
                f"{L('，有效收益率记录', ', valid yield records')}: {data_source.get('valid_yield_count')}"
            )
            if data_source.get("maturity_coverage"):
                coverage = data_source["maturity_coverage"]
                ratio = round(float(coverage.get("coverage_ratio", 0)) * 100, 1)
                lines.append(
                    f"- {L('期限覆盖率', 'Maturity coverage')}: {ratio}%"
                    f"{L('，已补全', ', filled')} {coverage.get('filled_count')}"
                    f"{L(' 条，缺失', ', missing')} {coverage.get('missing_count')}"
                )

        if market:
            lines.append(f"- {L('样本数量', 'Sample count')}: {market.get('sample_count', 0)}")
            lines.append(f"- {L('收益率摘要', 'Yield summary')}: {market.get('yield_summary', {})}")
            quality = market.get("data_quality") or {}
            if quality:
                lines.append(
                    f"- {L('数据质量', 'Data quality')}: {quality.get('score')}/100 ({quality.get('level')})"
                )
        if ranking:
            lines.append(f"- {L('排序字段', 'Rank by')}: {ranking.get('rank_by')}")
        if outliers:
            lines.append(f"- {L('异常样本数量', 'Outlier count')}: {outliers.get('outlier_count', 0)}")
        monitor = evidence.get("monitor") or {}
        if monitor:
            summary = monitor.get("summary_en") if en else monitor.get("summary_zh")
            summary = summary or monitor.get("summary_zh") or monitor.get("summary_en")
            lines.append(f"- {L('监控面板', 'Monitor')}: {summary}")
        search = evidence.get("search") or {}
        if search:
            lines.append(f"- {L('检索条件', 'Search criteria')}: {search.get('criteria', {})}")
            lines.append(f"- {L('检索命中数量', 'Match count')}: {search.get('match_count', 0)}")
            for index, record in enumerate(search.get("records", [])[:5], start=1):
                maturity = self._display_maturity(record, lang=lang)
                if en:
                    lines.append(
                        f"  {index}. {record.get('债券简称')} | maturity {maturity} | "
                        f"YTM {record.get('收盘到期收益率(%)')}% | volume {record.get('交易量(亿元)')} bn CNY"
                    )
                else:
                    lines.append(
                        f"  {index}. {record.get('债券简称')} | 待偿期 {maturity} | "
                        f"收益率 {record.get('收盘到期收益率(%)')}% | 成交量 {record.get('交易量(亿元)')} 亿元"
                    )
        if comparison:
            lines.append(
                f"- {L('债券相对市场', 'Bond vs market')}: yield_percentile={comparison.get('yield_percentile')}, "
                f"volume_percentile={comparison.get('volume_percentile')}, "
                f"is_yield_outlier={comparison.get('is_yield_outlier')}"
            )
            peer = comparison.get("peer_comparison") or {}
            if peer:
                lines.append(
                    f"- {L('同业可比', 'Peer comparison')}: type={peer.get('bond_type')}, "
                    f"bucket={peer.get('maturity_bucket')}, n={peer.get('peer_count')}, "
                    f"spread_bp={peer.get('spread_vs_peer_mean_bp')}"
                )

        if risk_explanations:
            lines.extend(["", f"{L('风险解释层', 'Risk Explanation Layer')}" + ":"])
            for item in risk_explanations:
                lines.append(f"- {item.get('title')}: {item.get('summary')}")

        if evidence_quality:
            lines.extend(
                [
                    "",
                    f"{L('证据质量', 'Evidence Quality')}:",
                    f"- {L('评分', 'Score')}: {evidence_quality.get('score')}/100",
                    f"- {L('等级', 'Level')}: {evidence_quality.get('level')}",
                    f"- {L('数据新鲜度', 'Data Freshness')}: {evidence_quality.get('data_freshness')}",
                    f"- {L('决策置信度', 'Decision Confidence')}: {evidence_quality.get('decision_confidence')}",
                    f"- {L('摘要', 'Summary')}: {evidence_quality.get('summary')}",
                ]
            )

        lines.extend(
            [
                "",
                f"{L('分析', 'Analysis')}:",
                *[f"- {item}" for item in report["analysis"]],
                "",
                f"{L('风险提示', 'Risk Notes')}:",
                *[f"- {item}" for item in report["risk_notes"]],
                "",
                f"{L('局限', 'Limitations')}:",
                *[f"- {item}" for item in report["limitations"]],
            ]
        )
        return "\n".join(lines)

    def _find_tool_output(self, tool_outputs: list[dict], tool_name: str) -> dict | None:
        return next((item for item in tool_outputs if item.get("tool") == tool_name), None)

    def _compact_args(self, params: dict) -> str:
        visible = [f"{key}={value}" for key, value in params.items() if key != "limit"]
        return ", ".join(visible) if visible else "no filters"

    def _display_maturity(self, record: dict, lang: str | None = None) -> str:
        maturity = record.get("待偿期")
        if maturity is not None and str(maturity).strip():
            return str(maturity)
        if lang == "en":
            return "missing in current source"
        return "当前数据源暂缺"
