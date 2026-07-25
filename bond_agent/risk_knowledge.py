from __future__ import annotations

RISK_SNIPPETS = [
    {
        "id": "yield_risk",
        "title": "Yield is a risk signal, not a recommendation",
        "keywords": ["收益率", "yield", "到期收益率", "高收益", "低收益", "分位数"],
        "summary": "Higher yield usually reflects compensation for one or more risks, such as credit risk, liquidity risk, duration exposure, or pricing uncertainty.",
        "watch_points": ["Compare yield with similar maturity bonds.", "Check whether the yield is an outlier before treating it as attractive."],
    },
    {
        "id": "liquidity_risk",
        "title": "Trading volume is a liquidity proxy",
        "keywords": ["成交量", "volume", "流动性", "活跃"],
        "summary": "Low trading volume can mean wider bid-ask spreads and harder execution. A bond can look attractive in sample data but still be difficult to trade.",
        "watch_points": ["Compare volume percentile with the market sample.", "Treat low-volume rankings as liquidity warnings, not opportunities."],
    },
    {
        "id": "duration_risk",
        "title": "Longer maturity increases rate sensitivity",
        "keywords": ["待偿期", "期限", "maturity", "久期", "利率"],
        "summary": "Longer maturity bonds are usually more sensitive to interest-rate changes. Yield comparisons are more meaningful when maturity buckets are similar.",
        "watch_points": ["Compare maturity percentile before comparing yield.", "Separate short-term CDs from long-term treasury or policy-bank bonds."],
    },
    {
        "id": "outlier_risk",
        "title": "Yield outliers need data and credit checks",
        "keywords": ["异常", "outlier", "z-score", "zscore", "极端"],
        "summary": "A yield outlier may reflect real risk, stale pricing, data quality issues, or a different bond type. It should trigger review rather than direct action.",
        "watch_points": ["Inspect the matched bond record.", "Check whether the outlier is driven by yield, maturity, volume, or missing context."],
    },
    {
        "id": "credit_risk",
        "title": "Credit context is outside the active market feed",
        "keywords": ["信用", "评级", "发行人", "违约", "credit", "rating"],
        "summary": "The active market feed does not include issuer ratings, financial statements, guarantees, or credit events, so credit conclusions must stay limited.",
        "watch_points": ["Do not infer ratings from yield alone.", "Use external issuer and rating data before making credit judgments."],
    },
    {
        "id": "data_boundary",
        "title": "Data coverage limits decision confidence",
        "keywords": ["数据", "样本", "来源", "实时", "crawler", "爬虫", "testdata"],
        "summary": "The Agent can use ChinaMoney spot deals and a local Excel fallback. Each answer should state which source was active and avoid conclusions outside available fields.",
        "watch_points": ["Check the response data_source before discussing freshness.", "Use issuer, rating, curve, and news data before making credit or investment judgments."],
    },
]


def retrieve_risk_explanations(question: str, report: dict, top_k: int = 4) -> list[dict]:
    context = _build_context(question, report)
    scored = []
    for snippet in RISK_SNIPPETS:
        score = sum(1 for keyword in snippet["keywords"] if keyword.lower() in context)
        score += _evidence_bonus(snippet["id"], report)
        if score > 0:
            scored.append((score, snippet))

    if not scored:
        scored.append((1, RISK_SNIPPETS[-1]))

    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [
        {
            "id": snippet["id"],
            "title": snippet["title"],
            "summary": snippet["summary"],
            "watch_points": snippet["watch_points"],
            "source": "local_fixed_income_risk_knowledge_base",
            "retrieval_score": score,
        }
        for score, snippet in scored[:top_k]
    ]


def _build_context(question: str, report: dict) -> str:
    pieces: list[str] = [question]
    pieces.extend(report.get("analysis", []))
    pieces.extend(report.get("risk_notes", []))
    pieces.extend(report.get("limitations", []))
    pieces.extend(str(tool) for tool in report.get("tools_used", []))
    return " ".join(pieces).lower()


def _evidence_bonus(snippet_id: str, report: dict) -> int:
    evidence = report.get("data_evidence", {})
    comparison = evidence.get("comparison") or {}
    outliers = evidence.get("outliers") or {}
    ranking = evidence.get("ranking") or {}

    if snippet_id == "yield_risk" and comparison.get("yield_percentile") is not None:
        return 2
    if snippet_id == "liquidity_risk" and comparison.get("volume_percentile") is not None:
        return 2
    if snippet_id == "duration_risk" and comparison.get("maturity_percentile") is not None:
        return 2
    if snippet_id == "outlier_risk" and (outliers.get("records") or comparison.get("is_yield_outlier") is not None):
        return 2
    if snippet_id == "data_boundary":
        return 1
    if snippet_id == "yield_risk" and ranking.get("rank_by"):
        return 1
    return 0
