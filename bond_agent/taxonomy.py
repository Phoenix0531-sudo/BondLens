"""Bond name taxonomy for Chinese market samples.

Rules are intentionally conservative and pattern-based. They do not invent
issuer ratings or credit conclusions — only bucket labels for analytics.
"""

from __future__ import annotations

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


def classify_bond_type(name: object) -> str:
    text = str(name or "").strip()
    if not text:
        return "其他"
    if any(token in text for token in ("同业存单", "NCD", "CD")):
        return "同业存单"
    if any(token in text for token in ("地方债", "专项债", "一般债", "专项", "地方政府", "城投")):
        return "地方政府债"
    if any(token in text for token in ("国开", "农发", "进出口", "口行", "政策性金融")):
        return "政策性金融债"
    if any(token in text for token in ("附息国债", "记账式国债", "国债", "国库券")):
        return "国债"
    if any(token in text for token in ("金融债", "银行次级", "二级资本", "永续债", "商金债")):
        return "金融债"
    if any(
        token in text
        for token in ("公司债", "企业债", "中票", "短融", "超短融", "SCP", "MTN", "PPN", "ABN", "ABS")
    ):
        return "信用债"
    # Common local-gov naming: 23江苏债01 / 24浙江专项01
    if "债" in text and not any(token in text for token in ("国债", "国开", "农发", "金融", "公司", "企业")):
        if any(token in text for token in ("省", "市", "专项", "一般")) or (
            len(text) >= 4 and text[-1].isdigit()
        ):
            # Prefer local-gov over bare "其他" for *债* series without treasury/policy markers.
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
    missing_maturity = int(frame["期限分桶"].isna().sum()) if "期限分桶" in frame.columns else int(len(frame))
    return {
        "by_bond_type": by_type,
        "by_maturity_bucket": by_bucket,
        "missing_maturity_count": missing_maturity,
        "type_counts": {str(k): int(v) for k, v in type_counts.items()},
    }


def peer_bucket_frame(df: pd.DataFrame, target_years: float | None, bond_type: str | None = None) -> pd.DataFrame:
    frame = annotate_frame(df)
    bucket = maturity_bucket(target_years) if target_years is not None else None
    if bucket:
        frame = frame[frame["期限分桶"] == bucket]
    if bond_type and bond_type != "其他":
        typed = frame[frame["券种"] == bond_type]
        if len(typed) >= 5:
            frame = typed
    return frame


def assess_data_quality(df: pd.DataFrame) -> dict[str, Any]:
    frame = annotate_frame(df)
    issues: list[dict[str, Any]] = []
    total = int(len(frame))
    missing_yield = int(frame[YIELD].isna().sum()) if YIELD in frame.columns else total
    missing_maturity = int(frame[MATURITY_YEARS].isna().sum()) if MATURITY_YEARS in frame.columns else total
    extreme = frame[(frame[YIELD] < -5) | (frame[YIELD] > 30)] if YIELD in frame.columns else frame.iloc[0:0]
    duplicates = int(frame[BOND_NAME].duplicated().sum()) if BOND_NAME in frame.columns else 0

    if missing_yield:
        issues.append(
            {
                "id": "missing_yield",
                "severity": "high" if missing_yield / max(total, 1) > 0.1 else "medium",
                "message_zh": f"收益率缺失 {missing_yield}/{total} 条。",
                "message_en": f"Missing yield on {missing_yield}/{total} rows.",
            }
        )
    if missing_maturity:
        issues.append(
            {
                "id": "missing_maturity",
                "severity": "high" if missing_maturity / max(total, 1) > 0.3 else "medium",
                "message_zh": f"期限缺失 {missing_maturity}/{total} 条（分桶与同业可比会变弱）。",
                "message_en": f"Missing maturity on {missing_maturity}/{total} rows (peer buckets weaken).",
            }
        )
    if len(extreme):
        issues.append(
            {
                "id": "extreme_yield",
                "severity": "medium",
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

    score = 100
    score -= min(40, missing_yield * 2)
    score -= min(30, int(missing_maturity / max(total, 1) * 40))
    score -= min(20, len(extreme) * 2)
    score = max(0, min(100, score))
    level = "high" if score >= 80 else "medium" if score >= 55 else "low"
    return {
        "tool": "assess_data_quality",
        "score": score,
        "level": level,
        "row_count": total,
        "missing_yield_count": missing_yield,
        "missing_maturity_count": missing_maturity,
        "extreme_yield_count": int(len(extreme)),
        "duplicate_name_count": duplicates,
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
