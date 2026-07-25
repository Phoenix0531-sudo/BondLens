from __future__ import annotations

import json
import os
import re

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
    name = "BondLens AI"

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

        models = self._llm_model_candidates()
        attempts = max(1, int(os.environ.get("OPENAI_RETRY_ATTEMPTS", "2")))
        last_error: Exception | None = None
        last_error_name = "UnknownError"
        for model in models:
            for attempt in range(attempts):
                try:
                    client = self._create_openai_client(api_key, base_url=base_url)
                    lang = self._detect_answer_lang(question)
                    lang_rule = (
                        "Write the entire answer in Chinese (简体中文)."
                        if lang == "zh"
                        else "Write the entire answer in English."
                    )
                    instructions = (
                        "You are a fixed-income analysis assistant. Use only the provided JSON evidence. "
                        "Copy numeric evidence exactly when citing it. Do not invent any number that is not "
                        "present in the JSON (including percentiles, spreads, counts, and percentages). "
                        "When citing quartiles, prefer labels p25/p75 with their evidence values "
                        "(e.g. p25=2.255). Do not invent 25%/75% market-share claims. "
                        "Yield fields like 收盘到期收益率(%) are already in percent units: cite them as "
                        "2.5647% only when that exact value appears in evidence. "
                        "Quality notes may already contain percentages such as 7.8% / 7.4%; copy them "
                        "verbatim if needed, never recompute shares. "
                        "For negative spreads/z-scores, use ASCII hyphen-minus only (e.g. -5.95 bp), "
                        "never Unicode minus (U+2212) or en-dash. Keep the sign exactly as in evidence. "
                        "Do not create new percentages, ranges, ratings, issuer details, market facts, "
                        "or investment advice. "
                        "Do not recommend buying, selling, adding position, guaranteed returns, "
                        "or very safe conclusions. "
                        "Avoid the bare phrase risk-free entirely; if needed, write 'not free of risk' "
                        "or 'no investment advice' instead of 'not risk-free'. "
                        "If evidence includes modified_duration / dv01 / macaulay_duration, quote them as cashflow "
                        "teaching values under annual coupon=YTM assumptions, not OAS. For perpetual-style, "
                        "mention first-leg vs theoretical consol scenarios when present. "
                        "The yield_distribution values are counts, not percentages. "
                        "If a requested bond is present under data_evidence.search.records, focus on that bond "
                        "and quote its 收盘到期收益率(%) / 加权收益率(%) / 待偿期 exactly. "
                        "If the evidence is insufficient, say so directly. "
                        f"{lang_rule} "
                        f"Always include this disclaimer in Chinese: {DISCLAIMER}"
                    )
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
                    # Retry transport failures; advance to fallback model on hard channel/auth errors.
                    if attempt + 1 < attempts and last_error_name in {
                        "APIConnectionError",
                        "APITimeoutError",
                        "RateLimitError",
                        "InternalServerError",
                        "TimeoutError",
                        "ConnectTimeout",
                        "ReadTimeout",
                    }:
                        continue
                    message = str(exc).lower()
                    hard_channel = last_error_name in {
                        "AuthenticationError",
                        "PermissionDeniedError",
                        "NotFoundError",
                        "BadRequestError",
                    } or any(
                        token in message
                        for token in (
                            "no available channel",
                            "model not found",
                            "does not exist",
                            "invalid model",
                            "403",
                            "401",
                        )
                    )
                    if hard_channel:
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

    def _llm_model_candidates(self) -> list[str]:
        primary = (os.environ.get("OPENAI_MODEL") or "gpt-5.4-mini").strip()
        fallback_raw = os.environ.get("OPENAI_MODEL_FALLBACKS") or os.environ.get("OPENAI_FALLBACK_MODEL") or ""
        fallbacks = [part.strip() for part in fallback_raw.split(",") if part.strip()]
        # Sensible local defaults when only one model is configured.
        if not fallbacks:
            if primary == "gpt-5.4":
                fallbacks = ["gpt-5.4-mini", "grok-4.5"]
            elif primary == "gpt-5.4-mini":
                fallbacks = ["grok-4.5"]
            elif primary == "grok-4.5":
                fallbacks = ["gpt-5.4-mini"]
        ordered: list[str] = []
        for model in [primary, *fallbacks]:
            if model and model not in ordered:
                ordered.append(model)
        return ordered or [primary]

    def _detect_answer_lang(self, question: str) -> str:
        if re.search(r"[\u4e00-\u9fff]", question or ""):
            return "zh"
        return "en"

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
        compact_market = {
            "sample_count": market.get("sample_count"),
            "yield_summary": market.get("yield_summary"),
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
            compact_comparison = {
                "bond_name": comparison.get("bond_name") or comparison.get("name"),
                "record": comparison.get("record"),
                "market_median": comparison.get("market_median") or comparison.get("median"),
                "vs_market": comparison.get("vs_market") or comparison.get("comparison"),
                "notes": comparison.get("notes") or comparison.get("analysis"),
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
                    "coverage_ratio": coverage.get("coverage_ratio"),
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
            # Keep short analysis bullets; they already carry audited numbers.
            "analysis": (report.get("analysis") or [])[:8],
            "risk_notes": (report.get("risk_notes") or [])[:6],
            "limitations": (report.get("limitations") or [])[:6],
        }

    def _create_openai_client(self, api_key: str, base_url: str | None = None):
        from openai import OpenAI

        timeout = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "20"))
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
        yield {"type": "status", "stage": "resolve_data", "message_zh": "解析数据源…", "message_en": "Resolving data…"}
        data_frame, data_source = resolve_bond_data(
            mode=self.data_mode,
            path=self.data_path or None,
            live_fetcher=self.live_fetcher,
        )
        yield {"type": "status", "stage": "plan", "message_zh": "规划意图与工具…", "message_en": "Planning intent/tools…"}
        plan = classify_intent(question, data_path=self.data_path, data_frame=data_frame)
        tool_outputs: list[dict] = []
        tool_trace: list[str] = [
            f"User question: {question}",
            f"-> data_source(mode={data_source['runtime_mode']}, source={data_source['source_id']})",
            f"-> planner(intent={plan['intent']})",
        ]
        report = None
        for tool_name in plan["requested_tools"]:
            yield {
                "type": "status",
                "stage": "tool",
                "tool": tool_name,
                "message_zh": f"执行工具 {tool_name}…",
                "message_en": f"Running tool {tool_name}…",
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
            yield {"type": "status", "stage": "llm", "message_zh": "调用 LLM（可流式）…", "message_en": "Calling LLM (streamable)…"}
            # Live token path: consume streaming generator and re-yield token events immediately.
            llm_result = {"text": None, "status": "disabled", "error": None}
            for event in self._iter_llm_answer_events(question, plan, report):
                if event.get("type") == "token":
                    yield event
                    llm_text_parts.append(event.get("text") or "")
                elif event.get("type") == "llm_result":
                    llm_result = event.get("result") or llm_result
            llm_guardrail = (
                assess_llm_faithfulness(llm_result.get("text"), report)
                if llm_result.get("status") == "success"
                else assess_llm_faithfulness(None, report)
            )
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
        validated = AgentResponse.model_validate(validated).model_dump(mode="json")
        yield {"type": "final", "result": validated}

    def _iter_llm_answer_events(self, question: str, plan: dict, report: dict):
        """Yield token events then a final llm_result event for streaming UIs."""
        base_url = os.environ.get("OPENAI_BASE_URL")
        api_key = os.environ.get("OPENAI_API_KEY") or ("local-not-needed" if base_url else None)
        if not api_key:
            yield {"type": "llm_result", "result": {"text": None, "status": "disabled", "error": None}}
            return

        models = self._llm_model_candidates()
        attempts = max(1, int(os.environ.get("OPENAI_RETRY_ATTEMPTS", "2")))
        last_error: Exception | None = None
        last_error_name = "UnknownError"
        for model in models:
            for attempt in range(attempts):
                try:
                    client = self._create_openai_client(api_key, base_url=base_url)
                    lang = self._detect_answer_lang(question)
                    lang_rule = (
                        "Write the entire answer in Chinese (简体中文)."
                        if lang == "zh"
                        else "Write the entire answer in English."
                    )
                    instructions = (
                        "You are a fixed-income analysis assistant. Use only the provided JSON evidence. "
                        "Copy numeric evidence exactly when citing it. Do not invent any number that is not "
                        "present in the JSON (including percentiles, spreads, counts, and percentages). "
                        "When citing quartiles, prefer labels p25/p75 with their evidence values "
                        "(e.g. p25=2.255). Do not invent 25%/75% market-share claims. "
                        "Yield fields like 收盘到期收益率(%) are already in percent units: cite them as "
                        "2.5647% only when that exact value appears in evidence. "
                        "Quality notes may already contain percentages such as 7.8% / 7.4%; copy them "
                        "verbatim if needed, never recompute shares. "
                        "For negative spreads/z-scores, use ASCII hyphen-minus only (e.g. -5.95 bp), "
                        "never Unicode minus (U+2212) or en-dash. Keep the sign exactly as in evidence. "
                        "Do not create new percentages, ranges, ratings, issuer details, market facts, "
                        "or investment advice. "
                        "Do not recommend buying, selling, adding position, guaranteed returns, "
                        "or very safe conclusions. "
                        "Avoid the bare phrase risk-free entirely; if needed, write 'not free of risk' "
                        "or 'no investment advice' instead of 'not risk-free'. "
                        "If evidence includes modified_duration / dv01 / macaulay_duration, quote them as cashflow "
                        "teaching values under annual coupon=YTM assumptions, not OAS. For perpetual-style, "
                        "mention first-leg vs theoretical consol scenarios when present. "
                        "The yield_distribution values are counts, not percentages. "
                        "If a requested bond is present under data_evidence.search.records, focus on that bond "
                        "and quote its 收盘到期收益率(%) / 加权收益率(%) / 待偿期 exactly. "
                        "If the evidence is insufficient, say so directly. "
                        f"{lang_rule} "
                        f"Always include this disclaimer in Chinese: {DISCLAIMER}"
                    )
                    evidence_payload = self._build_llm_evidence(question, plan, report)
                    evidence_json = json.dumps(evidence_payload, ensure_ascii=False)
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
                    if attempt + 1 < attempts and last_error_name in {
                        "APIConnectionError",
                        "APITimeoutError",
                        "RateLimitError",
                        "InternalServerError",
                        "TimeoutError",
                        "ConnectTimeout",
                        "ReadTimeout",
                    }:
                        continue
                    message = str(exc).lower()
                    hard_channel = last_error_name in {
                        "AuthenticationError",
                        "PermissionDeniedError",
                        "NotFoundError",
                        "BadRequestError",
                    } or any(
                        token in message
                        for token in (
                            "no available channel",
                            "model not found",
                            "does not exist",
                            "invalid model",
                            "403",
                            "401",
                        )
                    )
                    if hard_channel:
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
