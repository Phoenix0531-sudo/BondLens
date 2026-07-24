from __future__ import annotations


def assess_evidence_quality(plan: dict, report: dict, data_source: dict, risk_explanations: list[dict]) -> dict:
    evidence = report.get("data_evidence", {})
    tools_used = report.get("tools_used", [])
    checks = []
    score = 0

    runtime_mode = data_source.get("runtime_mode")
    source_label = _source_label(runtime_mode)

    if data_source.get("row_count", 0) > 0:
        score += 25
        checks.append(f"{source_label} loaded successfully.")
    else:
        checks.append("No usable local rows were loaded.")

    if tools_used:
        score += min(20, len(tools_used) * 4)
        checks.append(f"{len(tools_used)} tool steps produced structured evidence.")

    if report.get("analysis"):
        score += 15
        checks.append("Final analysis was generated from tool outputs.")

    intent = plan.get("intent")
    search = evidence.get("search") or {}
    comparison = evidence.get("comparison") or {}
    market = evidence.get("market") or {}

    if intent == "advisory_refusal":
        # Policy refusals intentionally avoid full recommendation tooling.
        score += 10
        checks.append("Advisory solicitation was intercepted before recommendation tooling.")
    elif intent in {"bond_report", "bond_search"}:
        if search.get("match_count", 0) > 0:
            score += 15
            checks.append("Search criteria matched local bond records.")
        else:
            checks.append("Search criteria did not match local bond records.")
    elif market.get("sample_count", 0) > 0:
        score += 15
        checks.append("Market-level summary covers the loaded sample.")

    if comparison.get("found"):
        score += 10
        checks.append("Matched bond was compared against market percentiles.")

    if risk_explanations:
        score += 10
        checks.append("Risk explanation snippets were retrieved from the local knowledge base.")

    penalties = []
    if runtime_mode == "live":
        score += 10
        checks.append("Live public bond market data was fetched for this answer.")
    elif runtime_mode == "live_snapshot":
        checks.append("Most recent cached live-data snapshot was used after live fetch failed.")
    elif runtime_mode == "static_fallback":
        score -= 15
        penalties.append("Live data fetch failed and the answer used the local fallback sample.")
    elif runtime_mode == "static_sample":
        score -= 10
        penalties.append("Static sample limits data freshness.")
    if not data_source.get("active_live_feed") and not data_source.get("active_live_snapshot"):
        penalties.append("No live crawler or market feed is active in the Agent path.")
    penalties.append("Issuer ratings, credit events, and macro curves are not attached.")

    coverage = data_source.get("maturity_coverage") or {}
    coverage_ratio = coverage.get("coverage_ratio")
    if runtime_mode in {"live", "live_snapshot"} and coverage_ratio is not None:
        ratio = float(coverage_ratio)
        if ratio < 0.30:
            score -= 12
            penalties.append(
                f"Live maturity enrichment coverage is only {ratio:.1%}; peer/duration context is weak."
            )
        elif ratio < 0.70:
            score -= 6
            penalties.append(
                f"Live maturity enrichment coverage is incomplete ({ratio:.1%})."
            )

    score = max(0, min(100, score))
    level = "high" if score >= 80 else "medium" if score >= 55 else "low"

    return {
        "score": score,
        "level": level,
        "analysis_confidence": level,
        "decision_confidence": "low",
        "data_freshness": _data_freshness(runtime_mode),
        "coverage": {
            "intent": intent,
            "tools_used_count": len(tools_used),
            "has_market_summary": bool(market),
            "has_search_results": bool(search),
            "has_bond_comparison": bool(comparison.get("found")),
            "has_risk_explanations": bool(risk_explanations),
            "maturity_coverage_ratio": coverage_ratio,
        },
        "checks": checks,
        "penalties": penalties,
        "summary": (
            f"Evidence quality is {level} for the active data source, but decision confidence remains low "
            "because issuer credit context, macro curve data, and full security master fields are not attached."
        ),
    }


def _source_label(runtime_mode: str | None) -> str:
    if runtime_mode == "live":
        return "Live data"
    if runtime_mode == "live_snapshot":
        return "Cached live snapshot"
    return "Local Excel sample"


def _data_freshness(runtime_mode: str | None) -> str:
    if runtime_mode == "live":
        return "live_fetch"
    if runtime_mode == "live_snapshot":
        return "cached_live_snapshot"
    return "static_snapshot"
