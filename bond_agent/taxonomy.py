"""Bond name taxonomy for Chinese market samples.

Rules are intentionally conservative and pattern-based. They do not invent
issuer ratings or credit conclusions — only bucket labels for analytics.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .data_loader import BOND_NAME, MATURITY_YEARS, YIELD

BOND_TYPE_ORDER = [
    "国债",
    "政策性金融债",
    "地方政府债",
    "同业存单",
    "金融债",
    "信用债",
    "其他",
]

MATURITY_BUCKETS = [
    ("0-1Y", 0.0, 1.0),
    ("1-3Y", 1.0, 3.0),
    ("3-5Y", 3.0, 5.0),
    ("5-10Y", 5.0, 10.0),
    ("10Y+", 10.0, None),
]

# Province / city tokens frequently used in local-gov bond short names.
_LOCAL_GOV_GEO = (
    "北京",
    "天津",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "上海",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "重庆",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
    "深圳",
    "厦门",
    "青岛",
    "大连",
    "宁波",
)

_BANK_ISSUER = (
    "银行",
    "工行",
    "农行",
    "中行",
    "建行",
    "交行",
    "邮储",
    "国开",
    "农发",
    "进出",
    "农商",
    "农信",
    "城商",
)


def classify_bond_type(name: object) -> str:
    text = str(name or "").strip()
    if not text:
        return "其他"

    # Order matters: more specific product types first.
    if any(token in text for token in ("同业存单", "NCD")) or re.search(r"CD\d*", text, re.I):
        return "同业存单"

    if any(token in text for token in ("附息国债", "记账式国债", "国库券")) or (
        "国债" in text and "国开" not in text
    ):
        return "国债"

    if any(token in text for token in ("国开", "农发", "进出口", "政策性金融")) or re.search(
        r"进出\d*", text
    ):
        return "政策性金融债"

    if any(
        token in text
        for token in (
            "二级资本",
            "二级债",
            "永续债",
            "商金债",
            "金融债",
            "小微债",
            "绿色金融",
            "银行债",
            "银行二级",
            "银行永续",
        )
    ):
        return "金融债"

    # Bank issuer bonds that are not CDs / policy banks (e.g. 24北京银行01, 22上海银行).
    if any(token in text for token in _BANK_ISSUER) and any(
        token in text for token in ("债", "资本", "永续", "小微")
    ):
        if not any(token in text for token in ("地方债", "专项债", "一般债")):
            return "金融债"
    if re.search(r"(银行|农商行|农信)\d{2}", text) or re.search(
        r"(工行|农行|中行|建行|交行|邮储)\d{2}", text
    ):
        return "金融债"

    if any(
        token in text
        for token in (
            "地方债",
            "专项债",
            "一般债",
            "地方政府",
            "城投",
            "专项",
            "再融资债",
        )
    ):
        return "地方政府债"

    # Geo + 债 / bare numeric local-gov series (e.g. 23湖南101, 24江苏债05).
    if any(geo in text for geo in _LOCAL_GOV_GEO) and (
        "债" in text or re.search(r"\d{2,}$", text) or re.search(r"[省市]\d+", text)
    ):
        if not any(token in text for token in ("银行", "国债", "国开", "农发", "进出")):
            return "地方政府债"

    if any(
        token in text
        for token in (
            "公司债",
            "企业债",
            "中票",
            "短融",
            "超短融",
            "SCP",
            "MTN",
            "PPN",
            "ABN",
            "ABS",
            "中期票据",
            "超短期",
            "铁道",
            "铁路",
            "人寿",
            "证券",
            "租赁",
            "经开",
        )
    ) or re.search(r"CP\d*", text, re.I):
        return "信用债"

    # Bank short names without explicit 债/CD token (e.g. 22上海银行, 23星展银行).
    if any(token in text for token in ("银行", "农商行", "农信")) and not any(
        token in text for token in ("地方债", "专项债", "国债")
    ):
        return "金融债"

    # Residual *债* without treasury/policy/bank markers → local-gov series heuristic.
    if "债" in text and not any(
        token in text for token in ("国债", "国开", "农发", "进出", "金融", "公司", "企业", "银行")
    ):
        if any(token in text for token in ("省", "市", "专项", "一般")) or (
            len(text) >= 4 and text[-1].isdigit()
        ):
            return "地方政府债"

    return "其他"


def maturity_bucket(years: object) -> str | None:
    if years is None or (isinstance(years, float) and pd.isna(years)):
        return None
    try:
        value = float(years)
    except (TypeError, ValueError):
        return None
    for label, low, high in MATURITY_BUCKETS:
        if high is None:
            if value >= low:
                return label
        elif low <= value < high:
            return label
    return None


def annotate_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["券种"] = out[BOND_NAME].map(classify_bond_type) if BOND_NAME in out.columns else "其他"
    if MATURITY_YEARS in out.columns:
        out["期限分桶"] = out[MATURITY_YEARS].map(maturity_bucket)
    else:
        out["期限分桶"] = None
    return out


def summarize_segments(df: pd.DataFrame) -> dict[str, Any]:
    frame = annotate_frame(df)
    type_counts = frame["券种"].value_counts().to_dict() if "券种" in frame.columns else {}
    by_type = []
    for bond_type in BOND_TYPE_ORDER:
        subset = frame[frame["券种"] == bond_type]
        if subset.empty:
            continue
        by_type.append(
            {
                "bond_type": bond_type,
                "count": int(len(subset)),
                "yield_median": _median(subset),
                "yield_mean": _mean(subset),
            }
        )
    for bond_type, count in type_counts.items():
        if bond_type not in BOND_TYPE_ORDER:
            subset = frame[frame["券种"] == bond_type]
            by_type.append(
                {
                    "bond_type": bond_type,
                    "count": int(count),
                    "yield_median": _median(subset),
                    "yield_mean": _mean(subset),
                }
            )

    by_bucket = []
    for label, _low, _high in MATURITY_BUCKETS:
        subset = frame[frame["期限分桶"] == label]
        if subset.empty:
            continue
        by_bucket.append(
            {
                "bucket": label,
                "count": int(len(subset)),
                "yield_median": _median(subset),
                "yield_mean": _mean(subset),
            }
        )
    missing_maturity = (
        int(frame["期限分桶"].isna().sum()) if "期限分桶" in frame.columns else int(len(frame))
    )
    other_count = int(type_counts.get("其他", 0))
    return {
        "by_bond_type": by_type,
        "by_maturity_bucket": by_bucket,
        "missing_maturity_count": missing_maturity,
        "other_type_count": other_count,
        "type_counts": {str(k): int(v) for k, v in type_counts.items()},
    }


def peer_bucket_frame(
    df: pd.DataFrame, target_years: float | None, bond_type: str | None = None
) -> pd.DataFrame:
    frame = annotate_frame(df)
    bucket = maturity_bucket(target_years) if target_years is not None else None
    if bucket:
        frame = frame[frame["期限分桶"] == bucket]
    if bond_type and bond_type != "其他":
        typed = frame[frame["券种"] == bond_type]
        if len(typed) >= 5:
            frame = typed
    return frame


def assess_data_quality(
    df: pd.DataFrame,
    maturity_coverage: dict[str, Any] | None = None,
    runtime_mode: str | None = None,
) -> dict[str, Any]:
    """Score data usability with ratio-based penalties and explicit diagnostics."""
    frame = annotate_frame(df)
    issues: list[dict[str, Any]] = []
    total = int(len(frame))
    missing_yield = int(frame[YIELD].isna().sum()) if YIELD in frame.columns else total
    missing_maturity = (
        int(frame[MATURITY_YEARS].isna().sum()) if MATURITY_YEARS in frame.columns else total
    )
    extreme = (
        frame[(frame[YIELD] < -5) | (frame[YIELD] > 30)]
        if YIELD in frame.columns
        else frame.iloc[0:0]
    )
    duplicates = int(frame[BOND_NAME].duplicated().sum()) if BOND_NAME in frame.columns else 0
    other_count = int((frame["券种"] == "其他").sum()) if "券种" in frame.columns else 0

    yield_missing_ratio = missing_yield / max(total, 1)
    maturity_missing_ratio = missing_maturity / max(total, 1)
    other_ratio = other_count / max(total, 1)
    extreme_ratio = len(extreme) / max(total, 1)

    if missing_yield:
        severity = "high" if yield_missing_ratio > 0.15 else "medium" if yield_missing_ratio > 0.05 else "low"
        issues.append(
            {
                "id": "missing_yield",
                "severity": severity,
                "message_zh": f"收益率缺失 {missing_yield}/{total}（{yield_missing_ratio:.1%}）。",
                "message_en": f"Missing yield on {missing_yield}/{total} ({yield_missing_ratio:.1%}).",
            }
        )
    if missing_maturity:
        severity = (
            "high"
            if maturity_missing_ratio > 0.4
            else "medium"
            if maturity_missing_ratio > 0.1
            else "low"
        )
        issues.append(
            {
                "id": "missing_maturity",
                "severity": severity,
                "message_zh": (
                    f"期限缺失 {missing_maturity}/{total}（{maturity_missing_ratio:.1%}），"
                    "分桶与同业可比会变弱。"
                ),
                "message_en": (
                    f"Missing maturity on {missing_maturity}/{total} "
                    f"({maturity_missing_ratio:.1%}); peer buckets weaken."
                ),
            }
        )
    if len(extreme):
        issues.append(
            {
                "id": "extreme_yield",
                "severity": "medium" if extreme_ratio > 0.005 else "low",
                "message_zh": f"发现 {len(extreme)} 条极端收益率样本（<-5% 或 >30%），可能是数据异常。",
                "message_en": f"Found {len(extreme)} extreme yield rows (<-5% or >30%).",
            }
        )
    if duplicates:
        issues.append(
            {
                "id": "duplicate_names",
                "severity": "low",
                "message_zh": f"债券简称重复 {duplicates} 条。",
                "message_en": f"{duplicates} duplicate bond names.",
            }
        )
    if other_ratio > 0.08:
        issues.append(
            {
                "id": "unclassified_type",
                "severity": "medium" if other_ratio > 0.2 else "low",
                "message_zh": f"券种无法归类为“其他” {other_count}/{total}（{other_ratio:.1%}）。",
                "message_en": f"Unclassified bond type on {other_count}/{total} ({other_ratio:.1%}).",
            }
        )

    coverage = maturity_coverage or {}
    coverage_ratio = float(coverage.get("coverage_ratio") or (1 - maturity_missing_ratio))
    if runtime_mode in {"live", "live_snapshot"} and coverage_ratio < 0.7:
        issues.append(
            {
                "id": "live_maturity_enrichment_gap",
                "severity": "high" if coverage_ratio < 0.4 else "medium",
                "message_zh": (
                    f"实时/快照源原生无期限字段，当前补全覆盖率 {coverage_ratio:.1%}；"
                    "未匹配简称的债券无法做可靠同业分桶。"
                ),
                "message_en": (
                    f"Live/snapshot feed has no native maturity; enrichment coverage "
                    f"{coverage_ratio:.1%}. Unmatched names cannot form reliable peer buckets."
                ),
            }
        )

    # Ratio-based score so large samples are not over-penalized by absolute counts.
    score = 100.0
    score -= min(35.0, yield_missing_ratio * 100)
    score -= min(30.0, maturity_missing_ratio * 80)
    score -= min(15.0, extreme_ratio * 400)
    score -= min(10.0, other_ratio * 40)
    if duplicates:
        score -= min(5.0, duplicates / max(total, 1) * 50)
    if runtime_mode in {"live", "live_snapshot"} and coverage_ratio < 0.7:
        score -= min(15.0, (0.7 - coverage_ratio) * 40)
    score = int(max(0, min(100, round(score))))
    level = "high" if score >= 80 else "medium" if score >= 55 else "low"

    diagnostics = {
        "yield_missing_ratio": round(yield_missing_ratio, 4),
        "maturity_missing_ratio": round(maturity_missing_ratio, 4),
        "other_type_ratio": round(other_ratio, 4),
        "extreme_yield_ratio": round(extreme_ratio, 4),
        "maturity_coverage_ratio": round(coverage_ratio, 4),
        "runtime_mode": runtime_mode,
    }

    return {
        "tool": "assess_data_quality",
        "score": score,
        "level": level,
        "row_count": total,
        "missing_yield_count": missing_yield,
        "missing_maturity_count": missing_maturity,
        "extreme_yield_count": int(len(extreme)),
        "duplicate_name_count": duplicates,
        "other_type_count": other_count,
        "diagnostics": diagnostics,
        "issues": issues,
        "summary_zh": f"数据质量 {score}/100（{level}），共 {len(issues)} 类问题。",
        "summary_en": f"Data quality {score}/100 ({level}) with {len(issues)} issue types.",
    }


def _median(df: pd.DataFrame) -> float | None:
    if YIELD not in df.columns:
        return None
    series = pd.to_numeric(df[YIELD], errors="coerce").dropna()
    if series.empty:
        return None
    return round(float(series.median()), 4)


def _mean(df: pd.DataFrame) -> float | None:
    if YIELD not in df.columns:
        return None
    series = pd.to_numeric(df[YIELD], errors="coerce").dropna()
    if series.empty:
        return None
    return round(float(series.mean()), 4)
