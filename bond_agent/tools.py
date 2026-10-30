from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from .data_loader import (
    BOND_CODE,
    BOND_NAME,
    DURATION_METHOD,
    DV01,
    IS_PERPETUAL,
    MACAULAY_DURATION,
    MATURITY,
    MATURITY_PARSE_NOTE,
    MATURITY_RAW,
    MATURITY_YEARS,
    MODIFIED_DURATION,
    PERPETUAL_MOD_DURATION,
    PRICE,
    QUOTE_TIME,
    VOLUME,
    WEIGHTED_YIELD,
    YIELD,
    cashflow_modified_duration,
    load_bond_data,
    records_from_frame,
)
from .taxonomy import (
    annotate_frame,
    assess_data_quality,
    classify_bond_type,
    maturity_bucket,
    peer_bucket_frame,
    summarize_segments,
)

# Fixed yield-distribution buckets: breakpoints researchers actually use,
# plus a single ≥6% tail column (see _yield_distribution).
_YIELD_BIN_EDGES = [-float("inf"), 2.0, 3.0, 4.0, 5.0, 6.0, float("inf")]
_YIELD_BIN_LABELS = ["<2%", "2–3%", "3–4%", "4–5%", "5–6%", "≥6%"]


RANK_COLUMNS = {
    "yield": YIELD,
    "收益率": YIELD,
    "volume": VOLUME,
    "成交量": VOLUME,
    "maturity": MATURITY_YEARS,
    "期限": MATURITY_YEARS,
    "price": PRICE,
    "净价": PRICE,
}

# Minimum same-type, same-bucket records before a peer comparison is "ok";
# below it we still report numbers but flag a small-sample caveat.
PEER_MIN_SAMPLE = 5
# Minimum records before a (券种, 期限桶) group is scored for statistical deviation.
OUTLIER_MIN_GROUP_SIZE = 5


def _summary(series: pd.Series) -> dict:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return {"count": 0}

    return {
        "count": int(clean.count()),
        "mean": round(float(clean.mean()), 4),
        "std": round(float(clean.std(ddof=0)), 4),
        "min": round(float(clean.min()), 4),
        "p25": round(float(clean.quantile(0.25)), 4),
        "median": round(float(clean.median()), 4),
        "p75": round(float(clean.quantile(0.75)), 4),
        "max": round(float(clean.max()), 4),
    }


def _resolve_frame(data_frame: pd.DataFrame | None = None, data_path: str | None = None) -> pd.DataFrame:
    frame = data_frame.copy() if data_frame is not None else load_bond_data(data_path) if data_path else load_bond_data()
    for column in (YIELD, WEIGHTED_YIELD, VOLUME, PRICE, MATURITY_YEARS):
        if column in frame:
            numeric = pd.to_numeric(frame[column], errors='coerce').replace([float('inf'), -float('inf')], float('nan'))
            if column in (VOLUME, MATURITY_YEARS):
                numeric = numeric.where(numeric >= 0)
            frame[column] = numeric
    return frame


def search_bonds(
    name: str | None = None,
    code: str | None = None,
    min_maturity: float | None = None,
    max_maturity: float | None = None,
    min_yield: float | None = None,
    max_yield: float | None = None,
    min_volume: float | None = None,
    max_volume: float | None = None,
    bond_type: str | None = None,
    limit: int = 20,
    data_path: str | None = None,
    data_frame: pd.DataFrame | None = None,
) -> dict:
    """Search by bond code and/or name over one quote snapshot.

    - Code matches the 债券代码 column when the active source provides one
      (live/imports do; the static sample has no code column and says so).
    - Name matching prefers exact hits, then substring contains.
    - Numeric filters are skipped with an explicit ``*_column_missing`` marker
      when the sample lacks the column (imports may legitimately miss fields).
    - Same-name rows are kept separately (multi-deal / duplicate snapshots);
      distinct bond count is reported alongside record count.
    """
    df = _resolve_frame(data_frame=data_frame, data_path=data_path)
    df = annotate_frame(df)

    matched_by: list[str] = []
    name_exact = False
    if code:
        code_text = str(code).strip()
        if BOND_CODE in df.columns:
            codes = df[BOND_CODE].astype("string").str.strip()
            exact = df[codes == code_text]
            if not exact.empty:
                df = exact
                matched_by.append("code_exact")
            else:
                partial = df[codes.str.contains(code_text, regex=False, na=False)]
                df = partial
                matched_by.append("code_contains" if not partial.empty else "code_no_match")
        else:
            matched_by.append("code_column_missing")
            df = df.iloc[0:0]
    if name and len(df):
        name_text = str(name).strip()
        exact = df[df[BOND_NAME] == name_text]
        if not exact.empty:
            df = exact
            name_exact = True
            matched_by.append("name_exact")
        else:
            partial = df[df[BOND_NAME].str.contains(name_text, case=False, na=False, regex=False)]
            df = partial
            matched_by.append("name_contains" if not partial.empty else "name_no_match")
    elif name:
        matched_by.append("name_no_match")

    if bond_type:
        df = df[df["券种"] == bond_type]
        matched_by.append("bond_type")
    numeric_filters = [
        (MATURITY_YEARS, (min_maturity, max_maturity), "maturity_column_missing"),
        (YIELD, (min_yield, max_yield), "yield_column_missing"),
        (VOLUME, (min_volume, max_volume), "volume_column_missing"),
    ]
    for column, (low, high), missing_marker in numeric_filters:
        if low is None and high is None:
            continue
        if column not in df.columns:
            matched_by.append(missing_marker)
            df = df.iloc[0:0]
            continue
        if low is not None:
            df = df[df[column] >= low]
        if high is not None:
            df = df[df[column] <= high]

    matched = df.copy()
    # Sort by yield only when the column exists (imports may legitimately lack it).
    if len(matched) and YIELD in matched.columns:
        matched = matched.sort_values(YIELD, ascending=False)
    distinct_bonds = int(matched[BOND_NAME].nunique()) if len(matched) else 0
    ambiguity_note = None
    if name and not name_exact and distinct_bonds > 1:
        ambiguity_note = (
            f"名称关键词匹配到 {distinct_bonds} 只不同债券，请用债券代码或更长的简称缩小范围。"
        )

    return {
        "tool": "search_bonds",
        "criteria": {
            "name": name,
            "code": code,
            "bond_type": bond_type,
            "min_maturity": min_maturity,
            "max_maturity": max_maturity,
            "min_yield": min_yield,
            "max_yield": max_yield,
            "limit": limit,
        },
        "match_count": len(matched),
        "distinct_bond_count": distinct_bonds,
        "matched_by": matched_by,
        "ambiguity_note": ambiguity_note,
        "records": records_from_frame(matched, limit),
    }


def _optional_numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    """Numeric column accessor that tolerates absent columns (import mode).

    Uploaded tables may legitimately lack e.g. volume or weighted yield; the
    affected summaries degrade to count=0 instead of crashing the analysis.
    """
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(dtype=float, index=frame.index)


def _yield_distribution(series: pd.Series) -> dict[str, int]:
    """Fixed, financially meaningful yield buckets + a separate tail column.

    Equal-width bins over the observed range produce intervals like
    "(0.0814, 11.515]" that carry no market meaning and lump almost the whole
    sample into one bar. Fixed breakpoints (2/3/4/5/6%) mirror how credit-bond
    researchers actually bucket yields; the ≥6% tail stays a single labeled
    column so extreme values never stretch the scale.
    """
    if series.empty:
        return {}
    cut = pd.cut(series, bins=_YIELD_BIN_EDGES, labels=_YIELD_BIN_LABELS)
    counts = cut.value_counts().reindex(_YIELD_BIN_LABELS, fill_value=0)
    return {str(label): int(count) for label, count in counts.items()}


def describe_market(
    data_path: str | None = None,
    data_frame: pd.DataFrame | None = None,
    maturity_coverage: dict | None = None,
    runtime_mode: str | None = None,
) -> dict:
    df = _resolve_frame(data_frame=data_frame, data_path=data_path)
    annotated = annotate_frame(df)
    yield_data = _optional_numeric(annotated, YIELD).dropna()
    bins = _yield_distribution(yield_data)
    segments = summarize_segments(annotated)
    quality = assess_data_quality(
        annotated,
        maturity_coverage=maturity_coverage,
        runtime_mode=runtime_mode,
    )

    return {
        "tool": "describe_market",
        "sample_count": len(annotated),
        "columns": [BOND_NAME, MATURITY, PRICE, YIELD, WEIGHTED_YIELD, VOLUME],
        "yield_summary": _summary(_optional_numeric(annotated, YIELD)),
        "weighted_yield_summary": _summary(_optional_numeric(annotated, WEIGHTED_YIELD)),
        "volume_summary": _summary(_optional_numeric(annotated, VOLUME)),
        "maturity_summary_years": _summary(_optional_numeric(annotated, MATURITY_YEARS)),
        "yield_distribution": {str(interval): int(count) for interval, count in bins.items()},
        "segments": segments,
        "data_quality": quality,
    }


def build_market_monitor(
    top_n: int = 5,
    data_path: str | None = None,
    data_frame: pd.DataFrame | None = None,
) -> dict:
    """Cross-section monitor board: high yield, low volume, thin maturity gaps, outliers."""
    df = annotate_frame(_resolve_frame(data_frame=data_frame, data_path=data_path))
    clean = df.dropna(subset=[YIELD]).copy() if YIELD in df.columns else df.iloc[0:0].copy()
    by_yield = clean.sort_values(YIELD, ascending=False) if not clean.empty else clean
    low_volume = (
        clean.dropna(subset=[VOLUME]).sort_values(VOLUME, ascending=True)
        if VOLUME in clean.columns
        else clean.iloc[0:0]
    )
    missing_maturity = (
        df[df[MATURITY_YEARS].isna()].head(top_n)
        if MATURITY_YEARS in df.columns
        else df.head(top_n)
    )

    # Statistical yield deviation within (券种, 期限分桶) groups — same grouped
    # method as detect_yield_outliers, so monitor and outlier views agree.
    outlier_rows, outlier_metadata = _grouped_outlier_frame(clean, method="zscore", threshold=3.0)

    high_yield_records = records_from_frame(by_yield, top_n)
    for record in high_yield_records:
        record["券种"] = classify_bond_type(record.get(BOND_NAME))
        record["期限分桶"] = maturity_bucket(record.get(MATURITY_YEARS))

    low_volume_records = records_from_frame(low_volume, top_n)
    for record in low_volume_records:
        record["券种"] = classify_bond_type(record.get(BOND_NAME))

    outlier_records = records_from_frame(outlier_rows, top_n)
    if "outlier_score" in outlier_rows.columns and outlier_records:
        for record, score in zip(
            outlier_records, outlier_rows["outlier_score"].head(top_n), strict=False
        ):
            record["outlier_score"] = round(float(score), 4)
            record["券种"] = classify_bond_type(record.get(BOND_NAME))
    else:
        for record in outlier_records:
            record["券种"] = classify_bond_type(record.get(BOND_NAME))

    maturity_gap_records = records_from_frame(missing_maturity, top_n)

    return {
        "tool": "build_market_monitor",
        "top_n": top_n,
        "high_yield": high_yield_records,
        "low_volume": low_volume_records,
        "yield_outliers": outlier_records,
        "outlier_metadata": outlier_metadata,
        "missing_maturity": maturity_gap_records,
        "summary_zh": (
            f"监控面板：高收益 {len(high_yield_records)} 条、低成交 {len(low_volume_records)} 条、"
            f"组内收益偏离 {len(outlier_records)} 条（口径：券种+期限分桶，共评估 "
            f"{outlier_metadata['groups_evaluated']} 组）、缺期限 "
            f"{int(df[MATURITY_YEARS].isna().sum()) if MATURITY_YEARS in df.columns else len(df)} 条。"
        ),
        "summary_en": (
            f"Monitor board: high-yield {len(high_yield_records)}, low-volume {len(low_volume_records)}, "
            f"grouped yield deviation {len(outlier_records)} "
            f"(type×maturity-bucket, {outlier_metadata['groups_evaluated']} groups), missing maturity "
            f"{int(df[MATURITY_YEARS].isna().sum()) if MATURITY_YEARS in df.columns else len(df)}."
        ),
    }


def rank_bonds(
    by: str = "yield",
    top_n: int = 10,
    ascending: bool = False,
    data_path: str | None = None,
    data_frame: pd.DataFrame | None = None,
) -> dict:
    df = _resolve_frame(data_frame=data_frame, data_path=data_path)
    column = RANK_COLUMNS.get(by, by)
    if column not in df.columns:
        allowed = ", ".join(sorted(RANK_COLUMNS))
        raise ValueError(f"Unsupported ranking column: {by}. Allowed keys: {allowed}")

    ranked = df.dropna(subset=[column]).sort_values(column, ascending=ascending)
    return {
        "tool": "rank_bonds",
        "rank_by": column,
        "ascending": ascending,
        "top_n": top_n,
        "records": records_from_frame(ranked, top_n),
    }


def _grouped_outlier_frame(
    df: pd.DataFrame,
    method: str = "zscore",
    threshold: float = 3.0,
    min_group_size: int = OUTLIER_MIN_GROUP_SIZE,
) -> tuple[pd.DataFrame, dict]:
    """Flag statistical yield deviation within (券种, 期限分桶) groups only.

    - Grouping keys must be reliable: 待分类/其他 and rows without a maturity
      bucket are skipped and reported, never mixed into other groups.
    - Small groups (< min_group_size) are skipped and reported honestly.
    - This is a statistical deviation label only — never a credit/default
      judgment or an investment-opportunity signal.
    """
    clean = df.dropna(subset=[YIELD]).copy()

    flagged_parts: list[pd.DataFrame] = []
    groups_evaluated = 0
    small_groups: list[dict] = []
    skipped_missing_bucket = (
        int(clean["期限分桶"].isna().sum()) if "期限分桶" in clean.columns else len(clean)
    )
    skipped_unreliable_type = (
        int(clean["券种"].isin(["待分类", "其他"]).sum()) if "券种" in clean.columns else 0
    )

    if {"券种", "期限分桶"}.issubset(clean.columns):
        for (bond_type, bucket), group in clean.groupby(["券种", "期限分桶"]):
            if bond_type in {"待分类", "其他"}:
                continue
            if len(group) < min_group_size:
                small_groups.append(
                    {"bond_type": bond_type, "bucket": bucket, "count": len(group)}
                )
                continue
            groups_evaluated += 1
            group = group.copy()
            if method == "iqr":
                q1 = group[YIELD].quantile(0.25)
                q3 = group[YIELD].quantile(0.75)
                iqr = q3 - q1
                lower = q1 - 1.5 * iqr
                upper = q3 + 1.5 * iqr
                median = group[YIELD].median()
                group["outlier_score"] = (group[YIELD] - median).abs()
                flagged = group[(group[YIELD] < lower) | (group[YIELD] > upper)].copy()
                group_stat = round(float(iqr), 4)
            else:
                mean = float(group[YIELD].mean())
                std = float(group[YIELD].std(ddof=0))
                group["outlier_score"] = 0.0 if std == 0 else ((group[YIELD] - mean) / std).abs()
                flagged = group[group["outlier_score"] >= threshold].copy()
                group_stat = round(std, 4)
            if flagged.empty:
                continue
            group_mean = float(group[YIELD].mean())
            flagged["组内均值"] = round(group_mean, 4)
            flagged["组内标准差"] = group_stat
            flagged["组内样本数"] = len(group)
            flagged["偏离(BP)"] = (flagged[YIELD] - group_mean).abs().mul(100).round(2)
            flagged_parts.append(flagged)

    outliers = pd.concat(flagged_parts, ignore_index=True) if flagged_parts else clean.iloc[0:0]
    metadata = {
        "method": "iqr" if method == "iqr" else "zscore",
        "threshold": threshold,
        "min_group_size": min_group_size,
        "groups_evaluated": groups_evaluated,
        "small_groups_skipped": small_groups,
        "skipped_missing_bucket_count": skipped_missing_bucket,
        "skipped_unreliable_type_count": skipped_unreliable_type,
        "grouping": "券种+期限分桶",
        "note_zh": (
            "统计口径：同一券种、同期限桶内的收益率统计偏离；"
            "仅描述统计偏离，不构成信用、违约或投资机会判断。"
        ),
        "note_en": (
            "Statistics are computed within same bond-type and maturity-bucket groups; "
            "labels describe statistical deviation only, never credit/default/investment claims."
        ),
    }
    return (outliers.sort_values("outlier_score", ascending=False), metadata) if not outliers.empty else (outliers, metadata)


def detect_yield_outliers(
    method: str = "zscore",
    threshold: float = 3.0,
    top_n: int = 20,
    data_path: str | None = None,
    data_frame: pd.DataFrame | None = None,
) -> dict:
    df = annotate_frame(_resolve_frame(data_frame=data_frame, data_path=data_path))
    outliers, metadata = _grouped_outlier_frame(df, method=method, threshold=threshold)

    records = records_from_frame(outliers, top_n)
    if "outlier_score" in outliers.columns:
        for record, score in zip(
            records, outliers["outlier_score"].head(top_n), strict=False
        ):
            record["outlier_score"] = round(float(score), 4)

    return {
        "tool": "detect_yield_outliers",
        "outlier_count": len(outliers),
        "metadata": metadata,
        "records": records,
    }


def compare_bond_to_market(
    bond_name: str | None = None,
    record: dict | None = None,
    data_path: str | None = None,
    data_frame: pd.DataFrame | None = None,
    outlier_threshold: float = 3.0,
) -> dict:
    df = _resolve_frame(data_frame=data_frame, data_path=data_path)
    target = None

    if record:
        target_name = record.get(BOND_NAME)
        if target_name:
            matches = df[df[BOND_NAME] == target_name]
            if not matches.empty:
                target = matches.iloc[0]

    if target is None and bond_name:
        matches = df[df[BOND_NAME].str.contains(bond_name, case=False, na=False, regex=False)]
        if not matches.empty:
            target = matches.iloc[0]

    if target is None:
        return {
            "tool": "compare_bond_to_market",
            "bond_name": bond_name,
            "found": False,
            "message": "No matching bond record found in the active bond data source.",
        }

    yield_series = pd.to_numeric(df[YIELD], errors="coerce").dropna() if YIELD in df.columns else pd.Series(dtype=float)
    volume_series = _optional_numeric(df, VOLUME).dropna()
    maturity_series = _optional_numeric(df, MATURITY_YEARS).dropna()

    target_yield = float(target[YIELD]) if YIELD in df.columns and pd.notna(target[YIELD]) else None
    target_volume = float(target[VOLUME]) if VOLUME in df.columns and pd.notna(target[VOLUME]) else None
    target_maturity = (
        float(target[MATURITY_YEARS])
        if MATURITY_YEARS in df.columns and pd.notna(target[MATURITY_YEARS])
        else None
    )
    bond_type = classify_bond_type(target[BOND_NAME])
    bucket = maturity_bucket(target_maturity)
    target_name = str(target[BOND_NAME]).strip()

    yield_mean = yield_series.mean()
    yield_std = yield_series.std(ddof=0)
    yield_zscore = None if target_yield is None or yield_std == 0 else (target_yield - yield_mean) / yield_std
    is_yield_outlier = bool(yield_zscore is not None and abs(yield_zscore) >= outlier_threshold)

    peer_frame = df
    if QUOTE_TIME in df.columns:
        dates = pd.to_datetime(df[QUOTE_TIME], errors="coerce", format="mixed").dt.date
        target_date = pd.to_datetime(target.get(QUOTE_TIME), errors="coerce")
        peer_frame = df.loc[dates == target_date.date()] if pd.notna(target_date) else df.iloc[0:0]
    peers = peer_bucket_frame(peer_frame, target_maturity, bond_type=bond_type, exclude_name=target_name)
    peer_yield = (
        pd.to_numeric(peers[YIELD], errors="coerce").dropna()
        if YIELD in peers.columns
        else pd.Series(dtype=float)
    )
    peer_volume = (
        pd.to_numeric(peers[VOLUME], errors="coerce").dropna()
        if VOLUME in peers.columns
        else pd.Series(dtype=float)
    )
    peer_count = len(peers)
    if peer_count >= PEER_MIN_SAMPLE:
        peer_status = "ok"
    elif peer_count > 0:
        peer_status = "small_sample"
    else:
        peer_status = "insufficient"
    peer_mean = peer_yield.mean() if not peer_yield.empty else None
    peer_std = peer_yield.std(ddof=0) if not peer_yield.empty else None
    peer_z = (
        None
        if target_yield is None or peer_std in (None, 0) or peer_mean is None
        else (target_yield - peer_mean) / peer_std
    )
    peer_spread_bp = None
    if target_yield is not None and peer_mean is not None:
        peer_spread_bp = round((target_yield - float(peer_mean)) * 100, 2)

    modified_duration = None
    macaulay_duration = None
    dv01 = None
    duration_method = None
    perpetual_mod = None
    is_perpetual = False
    maturity_note = None
    scenarios = []
    first_leg = None
    remote_leg = None
    if MODIFIED_DURATION in target.index and pd.notna(target.get(MODIFIED_DURATION)):
        modified_duration = round(float(target[MODIFIED_DURATION]), 4)
    if MACAULAY_DURATION in target.index and pd.notna(target.get(MACAULAY_DURATION)):
        macaulay_duration = round(float(target[MACAULAY_DURATION]), 4)
    if DV01 in target.index and pd.notna(target.get(DV01)):
        dv01 = round(float(target[DV01]), 6)
    if DURATION_METHOD in target.index and target.get(DURATION_METHOD):
        duration_method = str(target.get(DURATION_METHOD))
    if PERPETUAL_MOD_DURATION in target.index and pd.notna(target.get(PERPETUAL_MOD_DURATION)):
        perpetual_mod = round(float(target[PERPETUAL_MOD_DURATION]), 4)
    if IS_PERPETUAL in target.index:
        is_perpetual = bool(target.get(IS_PERPETUAL))
    if "首段期限(年)" in target.index and pd.notna(target.get("首段期限(年)")):
        first_leg = round(float(target.get("首段期限(年)")), 4)
    if "远端期限(年)" in target.index and pd.notna(target.get("远端期限(年)")):
        remote_leg = round(float(target.get("远端期限(年)")), 4)
    if "期限情景" in target.index and isinstance(target.get("期限情景"), list):
        scenarios = list(target.get("期限情景") or [])
    if MATURITY_PARSE_NOTE in target.index:
        raw_note = target.get(MATURITY_PARSE_NOTE)
        if raw_note is not None and not (isinstance(raw_note, float) and pd.isna(raw_note)) and str(raw_note).strip() not in {"", "nan", "None"}:
            maturity_note = str(raw_note).strip()
    # If frame lacks cashflow fields (older snapshots), compute on the fly.
    if modified_duration is None and target_yield is not None:
        computed = cashflow_modified_duration(
            target_maturity,
            target_yield,
            frequency=1,
            perpetual=is_perpetual,
        )
        modified_duration = computed.get("modified_duration")
        macaulay_duration = computed.get("macaulay_duration")
        dv01 = computed.get("dv01")
        duration_method = computed.get("method")
        if is_perpetual and perpetual_mod is None:
            consol = cashflow_modified_duration(None, target_yield, frequency=1, perpetual=True)
            perpetual_mod = consol.get("modified_duration")

    return {
        "tool": "compare_bond_to_market",
        "bond_name": target[BOND_NAME],
        "found": True,
        "bond_type": bond_type,
        "maturity_bucket": bucket,
        "record": records_from_frame(pd.DataFrame([target]), 1)[0],
        "yield_percentile": _percentile(yield_series, target_yield),
        "volume_percentile": _percentile(volume_series, target_volume),
        "maturity_percentile": _percentile(maturity_series, target_maturity),
        "yield_zscore": None if yield_zscore is None else round(float(yield_zscore), 4),
        "is_yield_outlier": is_yield_outlier,
        "nearest_market_context": _market_context(target_yield, target_volume, yield_series, volume_series),
        "rate_sensitivity": {
            "modified_duration_approx": modified_duration,  # kept key for UI compatibility
            "modified_duration": modified_duration,
            "macaulay_duration": macaulay_duration,
            "dv01_approx": dv01,
            "dv01": dv01,
            "perpetual_modified_duration": perpetual_mod,
            "is_perpetual_style": is_perpetual,
            "method": duration_method or "cashflow_level_coupon",
            "first_leg_years": first_leg,
            "remote_leg_years": remote_leg,
            "scenarios": scenarios,
            "assumptions_zh": (
                "年付息、票息缺省取 YTM（平价教学假设）、残期取整期数；非 OAS/行权树/交易所现金流。"
                + (" 永续：主列=首段行权窗口，另给理论 consol 久期。" if is_perpetual else "")
            ),
            "assumptions_en": (
                "Annual coupons; coupon defaults to YTM (par teaching case); residual rounded to periods; "
                "not OAS/call tree/exchange schedule."
                + (" Perpetual: primary=first-leg call window, plus theoretical consol duration." if is_perpetual else "")
            ),
            "note_zh": (
                "修正久期/DV01 为现金流水平票息假设下的教学值，非完整定价。"
                + (" 永续风格同时给出首段与理论永续情景。" if is_perpetual else "")
            ),
            "note_en": (
                "Modified duration/DV01 are cashflow level-coupon teaching values, not full pricing."
                + (" Perpetual-style exposes first-leg and theoretical perpetual scenarios." if is_perpetual else "")
            ),
            "maturity_parse_note": maturity_note,
        },
        "credit_context": {
            "issuer_rating": None,
            "available": False,
            "note_zh": "当前行情源不包含主体/债项评级，不作信用评级推断。",
            "note_en": "Active feed has no issuer/issue rating; no credit-rating inference is made.",
        },
        "peer_comparison": {
            "quote_date": str(target_date.date()) if QUOTE_TIME in df.columns and pd.notna(target_date) else None,
            "peer_count": peer_count,
            "peer_status": peer_status,
            "bond_type": bond_type,
            "maturity_bucket": bucket,
            "peer_definition_zh": (
                "同业口径：有行情日期时限定同日，券种及期限桶相同、排除目标券；"
                "无行情日期列时无法核验同日，使用当前样本；目标券日期缺失时不比较。"
            ),
            "peer_definition_en": (
                "Peers share bond type and maturity bucket, excluding the target. When quote dates exist, "
                "only the same date is used; without a date column same-day status cannot be verified. "
                "No comparison is made when the target date is missing."
            ),
            "peer_yield_median": None if peer_yield.empty else round(float(peer_yield.median()), 4),
            "peer_yield_mean": None if peer_mean is None else round(float(peer_mean), 4),
            "peer_yield_percentile": _percentile(peer_yield, target_yield),
            "peer_volume_percentile": _percentile(peer_volume, target_volume),
            "peer_yield_zscore": None if peer_z is None else round(float(peer_z), 4),
            "spread_vs_peer_mean_bp": peer_spread_bp,
            "note_zh": (
                f"同业可比样本 {peer_count} 条（券种={bond_type}，期限桶={bucket or '未知'}），样本偏小，仅供谨慎参考。"
                if peer_status == "small_sample"
                else (
                    f"同业可比样本 {peer_count} 条（券种={bond_type}，期限桶={bucket or '未知'}）。"
                    if peer_status == "ok"
                    else (
                        "无同券种、同期限桶的可比样本；不提供跨券种伪同业比较。"
                        "如需比较请放宽券种或期限条件。全市场分位数仍作为参考单独列出。"
                    )
                )
            ),
            "note_en": (
                f"Peer sample size {peer_count} (type={bond_type}, bucket={bucket or 'unknown'}) is small; interpret with care."
                if peer_status == "small_sample"
                else (
                    f"Peer sample size {peer_count} (type={bond_type}, bucket={bucket or 'unknown'})."
                    if peer_status == "ok"
                    else (
                        "No same-type, same-bucket peers available; no cross-type pseudo-peers are fabricated. "
                        "Widen bond-type or maturity criteria to compare. Whole-market percentiles are listed separately as a reference."
                    )
                )
            ),
        },
    }



def generate_bond_report(question: str, tool_outputs: Sequence[dict], plan: dict | None = None) -> dict:
    market = next((item for item in tool_outputs if item.get("tool") == "describe_market"), {})
    ranked = next((item for item in tool_outputs if item.get("tool") == "rank_bonds"), {})
    outliers = next((item for item in tool_outputs if item.get("tool") == "detect_yield_outliers"), {})
    search = next((item for item in tool_outputs if item.get("tool") == "search_bonds"), {})
    comparison = next((item for item in tool_outputs if item.get("tool") == "compare_bond_to_market"), {})
    monitor = next((item for item in tool_outputs if item.get("tool") == "build_market_monitor"), {})
    intent = (plan or {}).get("intent", "bond_report")

    analysis = _build_analysis(intent, market, ranked, outliers, search, comparison, monitor=monitor)

    return {
        "tool": "generate_bond_report",
        "question": question,
        "tools_used": [item.get("tool") for item in tool_outputs] + ["generate_bond_report"],
        "data_evidence": {
            "market": market,
            "search": search,
            "ranking": ranked,
            "outliers": outliers,
            "comparison": comparison,
            "monitor": monitor,
        },
        "analysis": analysis,
        "risk_notes": [
            "收益率较高可能对应信用、流动性、久期或估值波动风险，需要结合发行人和市场环境进一步判断。",
            "成交量低的债券可能存在流动性不足，样本内排序不等同于可交易机会。",
        ],
        "limitations": [
            "本报告仅基于当前 Agent 数据源的可用字段计算。",
            "公开实时接口可能受交易时段、第三方源稳定性和字段覆盖限制影响。",
            "未接入评级、主体财务、宏观利率曲线或新闻事件。",
            "券种与期限分桶由名称/字段规则推断，不做信用评级结论。",
            "修正久期/DV01 为年付息+票息=YTM 的现金流教学假设，非 OAS/行权树；永续同时给首段与理论 consol 情景。",
            "非投资建议，仅用于学习和研究。",
        ],
    }


def _percentile(series: pd.Series, value: float | None) -> float | None:
    if value is None or series.empty:
        return None
    return round(float((series <= value).mean() * 100), 2)


def _market_context(
    target_yield: float | None,
    target_volume: float | None,
    yield_series: pd.Series,
    volume_series: pd.Series,
) -> str:
    notes = []
    if target_yield is not None and not yield_series.empty:
        median_yield = yield_series.median()
        direction = "高于" if target_yield >= median_yield else "低于"
        notes.append(f"收益率{direction}样本中位数 {round(float(median_yield), 4)}%。")
    if target_volume is not None and not volume_series.empty:
        median_volume = volume_series.median()
        direction = "高于" if target_volume >= median_volume else "低于"
        notes.append(f"成交量{direction}样本中位数 {round(float(median_volume), 4)} 亿元。")
    return "".join(notes)


def _build_analysis(
    intent: str,
    market: dict,
    ranked: dict,
    outliers: dict,
    search: dict,
    comparison: dict,
    monitor: dict | None = None,
) -> list[str]:
    monitor = monitor or {}
    if search.get("records"):
        record = search["records"][0]
        if search.get("criteria", {}).get("name") is None:
            preview_names = "、".join(str(item.get(BOND_NAME)) for item in search["records"][:5])
            analysis = [
                f"检索条件命中 {search.get('match_count')} 条记录。",
                f"前 {min(5, len(search['records']))} 条样本包括：{preview_names}。",
                "该结果是筛选列表，不代表投资优先级；如需单券分析，请提供具体债券简称。",
            ]
            bond_type = (search.get("criteria") or {}).get("bond_type")
            if bond_type:
                analysis.insert(1, f"已按券种筛选：{bond_type}。")
            return analysis
        analysis = [
            f"检索命中 {search.get('match_count')} 条记录，优先分析 {record.get(BOND_NAME)}。",
            (
                f"{record.get(BOND_NAME)} 的待偿期为 {_display_maturity(record)}，"
                f"收盘净价 {record.get(PRICE)} 元，收益率 {record.get(YIELD)}%，"
                f"成交量 {record.get(VOLUME)} 亿元。"
            ),
        ]
        if comparison.get("found"):
            analysis.append(
                f"相对全市场样本，它的收益率分位数为 {comparison.get('yield_percentile')}%，"
                f"成交量分位数为 {comparison.get('volume_percentile')}%，"
                f"期限分位数为 {comparison.get('maturity_percentile')}%。"
            )
            peer = comparison.get("peer_comparison") or {}
            if peer:
                analysis.append(
                    f"同业可比（{peer.get('bond_type')}/{peer.get('maturity_bucket') or '未知期限'}，"
                    f"n={peer.get('peer_count')}）：相对同业均值利差 {peer.get('spread_vs_peer_mean_bp')} bp，"
                    f"同业收益率分位数 {peer.get('peer_yield_percentile')}%。"
                )
            outlier_text = "属于" if comparison.get("is_yield_outlier") else "不属于"
            analysis.append(
                f"按全市场 z-score 阈值判断，该债券{outlier_text}统计意义上收益率显著偏离的样本"
                f"（仅描述统计偏离，非信用或投资判断）。"
                f"{comparison.get('nearest_market_context', '')}"
            )
        if market:
            analysis.append(
                f"全市场背景：样本收益率中位数约 {market.get('yield_summary', {}).get('median', 'N/A')}%，"
                f"均值约 {market.get('yield_summary', {}).get('mean', 'N/A')}%。"
            )
            segments = (market.get("segments") or {}).get("by_bond_type") or []
            if segments:
                top_types = "；".join(
                    f"{item['bond_type']} {item['count']}只/中位收益{item.get('yield_median')}%"
                    for item in segments[:4]
                )
                analysis.append(f"券种结构：{top_types}。")
            quality = market.get("data_quality") or {}
            if quality:
                analysis.append(quality.get("summary_zh") or f"数据质量 {quality.get('score')}/100。")
                for issue in (quality.get("issues") or [])[:2]:
                    if issue.get("message_zh"):
                        analysis.append(f"数据诊断：{issue['message_zh']}")
        return analysis

    if search and search.get("match_count") == 0:
        return [
            "未在当前债券数据源中找到符合条件的债券记录。",
            "请检查债券简称、收益率范围、券种或待偿期条件；本项目不会凭空补充数据源之外的信息。",
        ]

    if intent == "ranking":
        records = ranked.get("records", [])
        if not records:
            return ["排序工具未返回可用记录。"]
        first = records[0]
        return [
            f"本次按 {ranked.get('rank_by')} 排序，返回前 {len(records)} 条样本。",
            (
                f"排名首位为 {first.get(BOND_NAME)}，收益率 {first.get(YIELD)}%，"
                f"成交量 {first.get(VOLUME)} 亿元，待偿期 {_display_maturity(first)}。"
            ),
            "排序结果只反映当前样本字段，不代表投资优先级。",
        ]

    if intent == "outlier_detection":
        records = outliers.get("records", [])
        metadata = outliers.get("metadata", {})
        if not records:
            return [
                "按当前阈值未发现显著的组内收益率统计偏离样本。",
                "统计口径：同券种、同期限桶内计分；样本不足的组不参与结论。",
            ]
        first = records[0]
        small_group_note = ""
        skipped_groups = metadata.get("small_groups_skipped") or []
        if skipped_groups:
            small_group_note = f"另有 {len(skipped_groups)} 个（券种×期限桶）组样本不足 {metadata.get('min_group_size')} 条，未参与计分。"
        return [
            (
                f"统计口径：同券种、同期限桶内 {metadata.get('method')} 方法，共评估 "
                f"{metadata.get('groups_evaluated')} 个组，发现 {outliers.get('outlier_count')} 条统计偏离样本。"
            ),
            (
                f"偏离分数最高的是 {first.get(BOND_NAME)}（{first.get('券种')}/{first.get('期限分桶')}），"
                f"收益率 {first.get(YIELD)}%，偏离 {first.get('偏离(BP)')} bp，"
                f"组内样本 {first.get('组内样本数')} 条，分数 {first.get('outlier_score')}。"
            ),
            small_group_note,
            "以上仅描述统计偏离，不构成信用、违约或投资机会判断。",
        ]

    yield_summary = market.get("yield_summary", {})
    volume_summary = market.get("volume_summary", {})
    analysis = [
        f"样本共 {market.get('sample_count', 0)} 条债券记录，收益率均值约 {yield_summary.get('mean', 'N/A')}%。",
        (
            f"收益率中位数约 {yield_summary.get('median', 'N/A')}%，"
            f"区间约为 {yield_summary.get('min', 'N/A')}% 到 {yield_summary.get('max', 'N/A')}%。"
        ),
        (
            f"成交量均值约 {volume_summary.get('mean', 'N/A')} 亿元，"
            f"中位数约 {volume_summary.get('median', 'N/A')} 亿元。"
        ),
    ]
    segments = (market.get("segments") or {}).get("by_bond_type") or []
    if segments:
        top_types = "；".join(
            f"{item['bond_type']} {item['count']}只/中位收益{item.get('yield_median')}%"
            for item in segments[:4]
        )
        analysis.append(f"券种结构：{top_types}。")
    buckets = (market.get("segments") or {}).get("by_maturity_bucket") or []
    if buckets:
        top_buckets = "；".join(
            f"{item['bucket']} {item['count']}只/中位收益{item.get('yield_median')}%"
            for item in buckets[:5]
        )
        analysis.append(f"期限分桶：{top_buckets}。")
    quality = market.get("data_quality") or {}
    if quality:
        analysis.append(quality.get("summary_zh") or f"数据质量 {quality.get('score')}/100。")
        for issue in (quality.get("issues") or [])[:2]:
            if issue.get("message_zh"):
                analysis.append(f"数据诊断：{issue['message_zh']}")
    if ranked.get("records"):
        first = ranked["records"][0]
        analysis.append(
            f"按 {ranked.get('rank_by')} 排序的首位样本是 {first.get(BOND_NAME)}，"
            f"收益率 {first.get(YIELD)}%，成交量 {first.get(VOLUME)} 亿元。"
        )
    if outliers.get("records"):
        analysis.append(
            f"统计口径下发现 {outliers.get('outlier_count')} 条组内收益率偏离样本"
            f"（同券种、同期限桶内计分，仅描述统计偏离）。"
        )
    if monitor.get("summary_zh"):
        analysis.append(monitor["summary_zh"])
    if intent in {"market_monitor", "composite_market"} and monitor.get("high_yield"):
        top = monitor["high_yield"][0]
        analysis.append(
            f"监控关注：截面高收益首位 {top.get(BOND_NAME)}（{top.get(YIELD)}%），"
            "仅为样本观察，不构成交易建议。"
        )
    return analysis



def _display_maturity(record: dict) -> str:
    maturity = record.get(MATURITY)
    if maturity is not None and str(maturity).strip():
        label = str(maturity)
        if record.get(IS_PERPETUAL) and "永续" not in label:
            return f"{label} (永续风格)"
        return label
    raw = record.get(MATURITY_RAW)
    if raw:
        return str(raw)
    return "当前数据源暂缺"
