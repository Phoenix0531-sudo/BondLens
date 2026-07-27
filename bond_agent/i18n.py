"""UI bilingual label tables extracted from the Flask app layer."""

from __future__ import annotations

FIELD_LABELS = {
    "债券简称": {"zh": "债券简称", "en": "Bond"},
    "收盘到期收益率(%)": {"zh": "收益率", "en": "Yield (%)"},
    "加权收益率(%)": {"zh": "加权收益率", "en": "Weighted yield (%)"},
    "交易量(亿元)": {"zh": "成交量(亿元)", "en": "Volume (bn CNY)"},
    "收盘净价(元)": {"zh": "净价(元)", "en": "Clean price"},
    "待偿期": {"zh": "待偿期", "en": "Residual maturity"},
    "待偿期(年)": {"zh": "待偿期(年)", "en": "Maturity (years)"},
    "待偿期原文": {"zh": "待偿期原文", "en": "Maturity raw"},
    "是否永续风格": {"zh": "永续风格", "en": "Perpetual-style"},
    "修正久期(近似)": {"zh": "修正久期(现金流)", "en": "Mod. duration (CF)"},
    "修正久期(现金流假设)": {"zh": "修正久期(现金流)", "en": "Mod. duration (CF)"},
    "麦考利久期(现金流假设)": {"zh": "麦考利久期(现金流)", "en": "Macaulay duration (CF)"},
    "DV01(近似)": {"zh": "DV01(现金流)", "en": "DV01 (CF)"},
    "DV01(现金流假设)": {"zh": "DV01(现金流)", "en": "DV01 (CF)"},
    "理论永续修正久期": {"zh": "理论永续修正久期", "en": "Consol mod. duration"},
    "久期方法": {"zh": "久期方法", "en": "Duration method"},
    "首段期限(年)": {"zh": "首段期限(年)", "en": "First-leg years"},
    "远端期限(年)": {"zh": "远端期限(年)", "en": "Remote-leg years"},
    "券种": {"zh": "券种", "en": "Type"},
    "涨跌(BP)": {"zh": "涨跌(BP)", "en": "Change (bp)"},
    "分数": {"zh": "分数", "en": "Score"},
    "成交量": {"zh": "成交量", "en": "Volume"},
    "成交量(亿)": {"zh": "成交量(亿)", "en": "Volume (100m CNY)"},
    "到期收益率": {"zh": "到期收益率", "en": "YTM"},
    "到期收益率(%)": {"zh": "到期收益率(%)", "en": "YTM (%)"},
    "剩余期限": {"zh": "剩余期限", "en": "Residual tenor"},
    "剩余期限(年)": {"zh": "剩余期限(年)", "en": "Residual tenor (years)"},
    "发行人": {"zh": "发行人", "en": "Issuer"},
    "债券代码": {"zh": "债券代码", "en": "Bond code"},
    "交易日": {"zh": "交易日", "en": "Trade date"},
    "全价(元)": {"zh": "全价(元)", "en": "Dirty price"},
}


INTENT_LABELS = {
    "bond_report": {"zh": "单券分析", "en": "Bond report"},
    "bond_search": {"zh": "债券筛选", "en": "Bond search"},
    "market_overview": {"zh": "市场概览", "en": "Market overview"},
    "market_monitor": {"zh": "市场监控", "en": "Market monitor"},
    "composite_market": {"zh": "组合市场分析", "en": "Composite market analysis"},
    "ranking": {"zh": "排序分析", "en": "Ranking"},
    "outlier_detection": {"zh": "异常检测", "en": "Outlier detection"},
    "advisory_refusal": {"zh": "投资建议拦截", "en": "Advisory refusal"},
}

TOOL_LABELS = {
    "data_source": {"zh": "数据源解析", "en": "Data source resolver"},
    "search_bonds": {"zh": "债券检索", "en": "Bond search"},
    "compare_bond_to_market": {"zh": "单券对比市场", "en": "Bond vs market"},
    "describe_market": {"zh": "市场概览", "en": "Market overview"},
    "rank_bonds": {"zh": "债券排序", "en": "Bond ranking"},
    "detect_yield_outliers": {"zh": "收益率异常检测", "en": "Yield outlier detection"},
    "build_market_monitor": {"zh": "市场监控面板", "en": "Market monitor board"},
    "generate_bond_report": {"zh": "生成分析报告", "en": "Report composition"},
    "answer_selection": {"zh": "答案选择", "en": "Answer selection"},
}

RISK_TRANSLATIONS = {
    "yield_risk": {
        "zh": {
            "title": "收益率是风险信号，不是投资建议",
            "summary": "较高收益率通常是在补偿信用风险、流动性风险、久期暴露或定价不确定性。",
            "watch_points": ["应与相近期限债券比较收益率。", "把高收益样本视为需要核查的信号，而不是直接机会。"],
        },
        "en": {
            "title": "Yield is a risk signal, not investment advice",
            "summary": "Higher yields usually compensate credit, liquidity, duration exposure, or pricing uncertainty.",
            "watch_points": ["Compare yields with nearby-maturity peers.", "Treat high-yield hits as review signals, not opportunities."],
        },
    },
    "liquidity_risk": {
        "zh": {
            "title": "成交量是流动性代理指标",
            "summary": "低成交量可能意味着买卖价差更宽、执行更困难；样本内看起来有吸引力的债券也可能不易交易。",
            "watch_points": ["结合市场样本比较成交量分位数。", "把低成交量排名视为流动性提醒，而不是交易机会。"],
        },
        "en": {
            "title": "Volume is a liquidity proxy",
            "summary": "Low volume can mean wider spreads and harder execution; attractive sample ranks may still be hard to trade.",
            "watch_points": ["Compare volume percentiles within the sample.", "Treat low-volume ranks as liquidity alerts, not trade ideas."],
        },
    },
    "duration_risk": {
        "zh": {
            "title": "更长期限会提高利率敏感性",
            "summary": "长期债券通常对利率变化更敏感；收益率比较在期限区间相近时更有意义。",
            "watch_points": ["比较收益率前先看期限分位数。", "区分短期限存单、长期国债和政策性金融债等不同类型。"],
        },
        "en": {
            "title": "Longer residual maturity raises rate sensitivity",
            "summary": "Longer bonds are usually more rate-sensitive; yield comparisons are more meaningful in nearby maturity buckets.",
            "watch_points": ["Check maturity percentiles before comparing yields.", "Separate short CDs, long Treasuries, and policy-bank bonds."],
        },
    },
    "outlier_risk": {
        "zh": {
            "title": "收益率异常需要结合数据与信用核查",
            "summary": "收益率异常可能来自真实风险、陈旧报价、数据质量问题或债券类型差异，应触发复核而不是直接行动。",
            "watch_points": ["先检查命中的债券记录。", "判断异常来自收益率、期限、成交量还是缺失上下文。"],
        },
        "en": {
            "title": "Yield outliers need data and credit review",
            "summary": "Outliers can reflect real risk, stale quotes, data quality, or type differences; they trigger review, not action.",
            "watch_points": ["Inspect the matched bond record first.", "Separate yield, maturity, volume, and missing-context causes."],
        },
    },
    "credit_risk": {
        "zh": {
            "title": "信用上下文不在当前行情源内",
            "summary": "当前行情源不包含主体评级、财务报表、担保或信用事件，因此信用结论必须保持克制。",
            "watch_points": ["不要只根据收益率推断评级。", "做信用判断前应补充主体、评级和事件数据。"],
        },
        "en": {
            "title": "Credit context is outside the active feed",
            "summary": "The active feed has no issuer ratings, financials, guarantees, or credit events, so credit claims stay conservative.",
            "watch_points": ["Do not infer ratings from yield alone.", "Add issuer, rating, and event data before credit judgments."],
        },
    },
    "data_boundary": {
        "zh": {
            "title": "数据覆盖范围限制决策置信度",
            "summary": "Agent 使用中国货币网现券成交与本地 Excel 备用样本；每个回答都应说明当前数据源，并避免超出字段范围的结论。",
            "watch_points": ["讨论时效性前先检查 data_source。", "做信用或投资判断前应补充主体、评级、曲线和新闻数据。"],
        },
        "en": {
            "title": "Data coverage limits decision confidence",
            "summary": "The agent uses ChinaMoney spot deals and a local Excel fallback; every answer should state the active source and stay inside field scope.",
            "watch_points": ["Check data_source before discussing freshness.", "Add issuer, rating, curve, and news data before credit or investment claims."],
        },
    },
}
