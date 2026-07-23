from __future__ import annotations

import re

import pandas as pd

from .data_loader import BOND_NAME, load_bond_data

RANK_KEYWORDS = {
    "volume": ["成交量", "交易量", "活跃"],
    "maturity": ["期限", "待偿期", "久期", "最长"],
    "price": ["净价", "价格"],
    "yield": ["收益率", "最高", "高收益", "最低", "低收益"],
}


def classify_intent(
    question: str, data_path: str | None = None, data_frame: pd.DataFrame | None = None
) -> dict:
    """Plan tools for a natural-language bond question.

    Supports multi-intent composition: a single question can request
    overview + ranking + outliers + bond report in one run.
    """
    normalized = (question or "").strip()
    search_params = _extract_search_params(normalized, data_path=data_path, data_frame=data_frame)
    rank_by, ascending = _choose_rank(normalized)

    wants_outliers = _is_outlier_question(normalized)
    wants_ranking = _is_ranking_question(normalized)
    wants_overview = _is_market_overview_question(normalized)
    wants_structure = _is_structure_question(normalized)
    wants_monitor = _is_monitor_question(normalized)
    wants_report = _needs_report(normalized)
    wants_peers = _needs_peer_compare(normalized)

    if not normalized:
        return {
            "intent": "market_overview",
            "requested_tools": ["describe_market", "build_market_monitor"],
            "rank_by": None,
            "ascending": False,
            "search_params": {},
            "flags": {
                "composite": False,
                "wants_outliers": False,
                "wants_ranking": False,
                "wants_overview": True,
                "wants_structure": False,
                "wants_monitor": True,
                "wants_report": False,
                "wants_peers": False,
            },
            "explanation": "Empty question falls back to a market overview.",
        }

    flags = {
        "composite": False,
        "wants_outliers": wants_outliers,
        "wants_ranking": wants_ranking,
        "wants_overview": wants_overview or wants_structure,
        "wants_structure": wants_structure,
        "wants_monitor": wants_monitor,
        "wants_report": wants_report,
        "wants_peers": wants_peers,
    }

    # Named bond / filter + analysis → full evidence report (optionally + extras).
    if search_params and (wants_report or wants_peers or wants_ranking or wants_outliers or wants_overview):
        tools = [
            "search_bonds",
            "compare_bond_to_market",
            "describe_market",
            "rank_bonds",
            "detect_yield_outliers",
            "build_market_monitor",
            "generate_bond_report",
        ]
        flags["composite"] = True
        return {
            "intent": "bond_report",
            "requested_tools": tools,
            "rank_by": rank_by or "yield",
            "ascending": ascending,
            "search_params": search_params,
            "flags": flags,
            "explanation": (
                "Question names or filters bonds and asks for analysis; "
                "a multi-tool evidence report is used."
            ),
        }

    if search_params:
        return {
            "intent": "bond_search",
            "requested_tools": ["search_bonds"],
            "rank_by": None,
            "ascending": False,
            "search_params": search_params,
            "flags": flags,
            "explanation": "Question asks to find bonds matching explicit search criteria.",
        }

    # Composite market questions without a named bond.
    signal_count = sum(
        [
            wants_outliers,
            wants_ranking,
            wants_overview or wants_structure,
            wants_monitor,
        ]
    )
    if signal_count >= 2 or (wants_monitor and (wants_outliers or wants_ranking or wants_overview)):
        tools: list[str] = ["describe_market", "build_market_monitor"]
        if wants_ranking or wants_monitor:
            tools.append("rank_bonds")
        if wants_outliers or wants_monitor:
            tools.append("detect_yield_outliers")
        tools.append("generate_bond_report")
        # de-dupe preserve order
        tools = list(dict.fromkeys(tools))
        flags["composite"] = True
        return {
            "intent": "market_monitor" if wants_monitor else "composite_market",
            "requested_tools": tools,
            "rank_by": rank_by or "yield",
            "ascending": ascending,
            "search_params": search_params,
            "flags": flags,
            "explanation": "Multi-intent market question uses a composite tool plan.",
        }

    if wants_outliers:
        return {
            "intent": "outlier_detection",
            "requested_tools": ["detect_yield_outliers"],
            "rank_by": None,
            "ascending": False,
            "search_params": search_params,
            "flags": flags,
            "explanation": "Question asks about abnormal yield observations.",
        }

    if wants_ranking:
        return {
            "intent": "ranking",
            "requested_tools": ["rank_bonds"],
            "rank_by": rank_by or "yield",
            "ascending": ascending,
            "search_params": search_params,
            "flags": flags,
            "explanation": f"Question asks for sorted bonds by {rank_by or 'yield'}.",
        }

    if wants_monitor:
        return {
            "intent": "market_monitor",
            "requested_tools": [
                "describe_market",
                "rank_bonds",
                "detect_yield_outliers",
                "build_market_monitor",
                "generate_bond_report",
            ],
            "rank_by": rank_by or "yield",
            "ascending": ascending,
            "search_params": {},
            "flags": flags,
            "explanation": "Question asks for a cross-section market monitor board.",
        }

    if wants_overview or wants_structure:
        tools = ["describe_market", "build_market_monitor"]
        if wants_structure:
            # structure already covered by describe_market segments
            pass
        return {
            "intent": "market_overview",
            "requested_tools": tools,
            "rank_by": None,
            "ascending": False,
            "search_params": {},
            "flags": flags,
            "explanation": "Question asks for aggregate market sample statistics.",
        }

    return {
        "intent": "bond_report",
        "requested_tools": [
            "describe_market",
            "rank_bonds",
            "detect_yield_outliers",
            "build_market_monitor",
            "generate_bond_report",
        ],
        "rank_by": rank_by or "yield",
        "ascending": ascending,
        "search_params": search_params,
        "flags": flags,
        "explanation": "General analysis question uses a compact market report plan.",
    }


def _extract_search_params(
    question: str, data_path: str | None = None, data_frame: pd.DataFrame | None = None
) -> dict:
    params: dict = {"limit": 10}

    quoted = re.search(r"[“\"']([^“\"']+)[”\"']", question)
    if quoted:
        params["name"] = quoted.group(1).strip()
    else:
        bond_name = _find_bond_name(question, data_path=data_path, data_frame=data_frame)
        if bond_name:
            params["name"] = bond_name

    yield_range = re.search(
        r"收益率.*?([0-9]+(?:\.[0-9]+)?)\s*[-到至~]\s*([0-9]+(?:\.[0-9]+)?)", question
    )
    if yield_range:
        params["min_yield"] = float(yield_range.group(1))
        params["max_yield"] = float(yield_range.group(2))

    min_yield = re.search(r"收益率.*?(?:大于|高于|超过|>=)\s*([0-9]+(?:\.[0-9]+)?)", question)
    if min_yield:
        params["min_yield"] = float(min_yield.group(1))

    max_yield = re.search(r"收益率.*?(?:小于|低于|不超过|<=)\s*([0-9]+(?:\.[0-9]+)?)", question)
    if max_yield:
        params["max_yield"] = float(max_yield.group(1))

    maturity_range = re.search(
        r"(?:期限|待偿期).*?([0-9]+(?:\.[0-9]+)?)\s*[-到至~]\s*([0-9]+(?:\.[0-9]+)?)", question
    )
    if maturity_range:
        params["min_maturity"] = float(maturity_range.group(1))
        params["max_maturity"] = float(maturity_range.group(2))

    bond_type = _extract_bond_type(question)
    if bond_type:
        params["bond_type"] = bond_type

    return params if len(params) > 1 else {}


def _extract_bond_type(question: str) -> str | None:
    mapping = [
        ("同业存单", "同业存单"),
        ("NCD", "同业存单"),
        ("政策性金融", "政策性金融债"),
        ("国开", "政策性金融债"),
        ("农发", "政策性金融债"),
        ("地方债", "地方政府债"),
        ("专项债", "地方政府债"),
        ("金融债", "金融债"),
        ("二级资本", "金融债"),
        ("永续债", "金融债"),
        ("信用债", "信用债"),
        ("公司债", "信用债"),
        ("国债", "国债"),
    ]
    for token, bond_type in mapping:
        if token in question:
            return bond_type
    return None


def _choose_rank(question: str) -> tuple[str | None, bool]:
    for rank_by, keywords in RANK_KEYWORDS.items():
        if any(keyword in question for keyword in keywords):
            ascending = rank_by == "yield" and any(
                word in question for word in ["低收益", "最低", "较低"]
            )
            return rank_by, ascending
    return None, False


def _find_bond_name(
    question: str, data_path: str | None = None, data_frame: pd.DataFrame | None = None
) -> str | None:
    try:
        df = (
            data_frame
            if data_frame is not None
            else load_bond_data(data_path)
            if data_path
            else load_bond_data()
        )
        names = df[BOND_NAME].dropna().astype(str).unique()
    except Exception:
        names = []

    for name in sorted(names, key=len, reverse=True):
        if name and name in question:
            return name

    bond_like = re.search(r"(\d{2}[A-Za-z0-9\u4e00-\u9fff]+(?:CD\d+)?)", question)
    return bond_like.group(1).strip() if bond_like else None


def _is_outlier_question(question: str) -> bool:
    return any(word in question for word in ["异常", "离群", "极端", "outlier"])


def _is_ranking_question(question: str) -> bool:
    if any(word in question for word in ["排序", "排名", "最高", "最低", "最活跃", "最长", "前几"]):
        return True
    return bool(re.search(r"(?:前|Top|top)\s*\d+", question))


def _is_market_overview_question(question: str) -> bool:
    return any(
        word in question
        for word in ["概览", "整体", "市场", "分布", "摘要", "样本", "统计", "全市场"]
    )


def _is_structure_question(question: str) -> bool:
    return any(
        word in question
        for word in ["券种", "结构", "分桶", "期限结构", "类型分布", "构成", "占比"]
    )


def _is_monitor_question(question: str) -> bool:
    return any(
        word in question
        for word in ["监控", "看板", "面板", "今日", "盯盘", "截面", "清单", "工作台"]
    )


def _needs_report(question: str) -> bool:
    return any(
        word in question
        for word in ["分析", "报告", "说明", "解释", "怎么看", "评价", "研报", "点评"]
    )


def _needs_peer_compare(question: str) -> bool:
    return any(word in question for word in ["同业", "可比", "利差", "相对", "分位", "peer"])
