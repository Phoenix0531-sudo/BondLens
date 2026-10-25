"""Local CSV/Excel upload path with honest column mapping.

Design contracts:
- Uploaded files are user data: they are read from a temp location and never
  committed to the repository or shipped as examples.
- Missing fields disable only the analyses that need them; the import is never
  rejected outright as long as bond names exist.
- Same-name rows (multi-deal / duplicate snapshots) are preserved — no silent
  deduplication; distinct bond count is reported alongside record count.
- Mapping is reported verbatim (which source column became which field), and
  unmapped columns are listed instead of guessed.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .data_loader import (
    BOND_CODE,
    BOND_NAME,
    MATURITY,
    PRICE,
    QUOTE_TIME,
    VOLUME,
    WEIGHTED_YIELD,
    YIELD,
    _apply_maturity_details,
    _attach_rate_sensitivity,
)

# Canonical field -> acceptable source column names (exact, after strip).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    BOND_NAME: ("债券简称", "债券名称", "简称", "bond_name", "name"),
    BOND_CODE: ("债券代码", "证券代码", "代码", "bond_code", "code", "bondcode"),
    YIELD: (
        "收盘到期收益率(%)",
        "到期收益率(%)",
        "到期收益率",
        "收益率(%)",
        "收益率",
        "最新收益率",
        "yield",
    ),
    WEIGHTED_YIELD: ("加权收益率(%)", "加权收益率", "weighted_yield"),
    PRICE: ("收盘净价(元)", "收盘净价", "净价", "成交净价", "price"),
    VOLUME: ("交易量(亿元)", "交易量", "成交量(亿元)", "成交量", "volume"),
    MATURITY: ("待偿期", "待偿期(年)", "剩余期限", "剩余期限(年)", "期限", "maturity"),
    QUOTE_TIME: ("行情时间", "数据日期", "日期", "报价时间", "quote_time", "date"),
}

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}

# Same-name source columns with a DIFFERENT measurement口径 that we deliberately
# do not auto-map. They stay in the frame verbatim, are reported here, and
# explicit user overrides for them are REJECTED (see FORBIDDEN_CALIBER_OVERRIDES).
UNSUPPORTED_COLUMN_REASONS: dict[str, str] = {
    "全价": "「全价」含应计利息，与「收盘净价」口径不同",
    "成交金额": "「成交金额」与「交易量」口径/单位可能不同",
    "成交金额(亿元)": "「成交金额」与「交易量」口径/单位可能不同",
}

# Explicit column_overrides that are REJECTED outright. These are known
# caliber incompatibilities: silently converting them would fabricate data.
# A user may only use them after converting the column outside BondLens with
# a verifiable rule and re-uploading under an honest column name.
FORBIDDEN_CALIBER_OVERRIDES: dict[str, dict[str, dict[str, str]]] = {
    PRICE: {
        "全价": {
            "zh": (
                "拒绝映射「全价」→「收盘净价(元)」：全价 = 净价 + 应计利息，两者口径不同，"
                "直接改名会虚构价格数据。请在上传前按可核对的正式规则把全价转换为净价，"
                "或将该列改名为中性名称后仅作参考展示。"
            ),
            "en": (
                "Refusing to map a dirty-price column (全价) to 收盘净价(元): dirty price = "
                "clean price + accrued interest, so renaming it would fabricate prices. "
                "Convert dirty→clean price outside BondLens with a verifiable rule before upload, "
                "or keep the column under a neutral name for reference only."
            ),
        },
    },
    VOLUME: {
        "成交金额": {
            "zh": (
                "拒绝映射「成交金额」→「交易量(亿元)」：成交金额（面额口径、单位未知）与"
                "交易量（亿元）口径/单位不一致，直接改名会虚构成交量。请在上传前确认并换算单位，"
                "或保留原列仅作参考。"
            ),
            "en": (
                "Refusing to map 成交金额 (turnover amount, unknown unit/face-value basis) to "
                "交易量(亿元) (volume in 100M yuan): renaming it would fabricate volumes. "
                "Confirm the unit and convert outside BondLens before upload, or keep the raw column for reference."
            ),
        },
    },
}

# Substring markers: any source column whose (normalized) name contains one of
# these markers is treated as the incompatible caliber for that canonical field.
_CALIBER_MARKERS: dict[str, tuple[str, ...]] = {
    PRICE: ("全价",),
    VOLUME: ("成交金额",),
}

# Analyses that require each field to be present and non-empty.
FIELD_ANALYSIS_DEPS: dict[str, tuple[str, ...]] = {
    YIELD: ("rank_bonds", "detect_yield_outliers", "compare_bond_to_market", "build_market_monitor"),
    MATURITY: (),  # peer compare degrades honestly inside the tool, not disabled here
    VOLUME: (),
}

IMPORT_SOURCE_ID = "user_upload"


def read_table(path: str | Path) -> pd.DataFrame:
    """Read an uploaded CSV (utf-8-sig → gb18030 fallback) or Excel table."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {suffix or '(none)'}. Use CSV, XLSX or XLS.")
    if suffix == ".csv":
        try:
            return pd.read_csv(file_path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            return pd.read_csv(file_path, encoding="gb18030")
    return pd.read_excel(file_path)


def _normalize_header(value: str) -> str:
    return str(value).strip().replace("\u3000", "").replace(" ", "")


def _distinct_bond_count(frame: pd.DataFrame) -> int:
    """Count distinct bonds honestly.

    With a code column, identity is the (name, code) pair: the same name under
    different codes counts as different bonds, while multi-deal rows sharing
    name+code collapse to one bond. Without codes, distinct names are used.
    """
    if BOND_CODE in frame.columns:
        codes = frame[BOND_CODE].fillna("").astype(str).str.strip()
        pairs = pd.DataFrame({"n": frame[BOND_NAME].astype(str), "c": codes})
        return len(pairs.drop_duplicates())
    return int(frame[BOND_NAME].nunique())


def _derive_quote_date(frame: pd.DataFrame) -> dict:
    """Derive quote-date identity from the quote-time column, if readable.

    Returns a dict with:
    - sample_date: the single date when all rows share one date, else None.
    - quote_time: the most frequent quote timestamp (mode) for single-date samples.
    - quote_date_range: "min ~ max" when the sample spans multiple dates.
    - distinct_date_count / mixed_timepoints: honest mixed-snapshot flags.
    """
    info = {
        "sample_date": None,
        "quote_time": None,
        "quote_date_range": None,
        "distinct_date_count": 0,
        "mixed_timepoints": False,
    }
    if QUOTE_TIME not in frame.columns:
        return info
    parsed = pd.to_datetime(frame[QUOTE_TIME], errors="coerce").dropna()
    if parsed.empty:
        return info
    dates = parsed.dt.normalize().drop_duplicates().sort_values()
    info["distinct_date_count"] = len(dates)
    if len(dates) > 1:
        info["mixed_timepoints"] = True
        info["quote_date_range"] = f"{dates.iloc[0].date()} ~ {dates.iloc[-1].date()}"
        return info
    mode = parsed.mode().iloc[0]
    info["sample_date"] = str(mode.date())
    info["quote_time"] = mode.strftime("%Y-%m-%d %H:%M:%S") if mode.time() else info["sample_date"]
    return info


def _forbidden_caliber_reason(canonical: str, normalized_source: str) -> str | None:
    """Bilingual rejection reason when an override would cross a known caliber.

    Substring match on purpose: 「收盘全价」「成交金额(亿元)」 etc. must be
    rejected exactly like their plain forms.
    """
    markers = _CALIBER_MARKERS.get(canonical)
    if not markers:
        return None
    if not any(marker in normalized_source for marker in markers):
        return None
    reasons = FORBIDDEN_CALIBER_OVERRIDES[canonical]
    exact = reasons.get(normalized_source)
    if exact is None:
        first = next(iter(reasons.values()))
        exact = first
    return f"{exact['zh']} / {exact['en']}"


def map_columns(
    raw_df: pd.DataFrame, overrides: dict[str, str] | None = None
) -> tuple[pd.DataFrame, dict, list[str]]:
    """Rename recognized source columns to canonical fields.

    ``overrides`` maps canonical field -> source column name and lets the user
    confirm or correct the automatic aliases for UNKNOWN columns. Known
    caliber-incompatible mappings (全价→收盘净价, 成交金额→交易量) are REJECTED
    with a bilingual explanation instead of silently converting. Overrides win
    over aliases and are validated: unknown target fields, missing source
    columns, and forbidden calibers raise ValueError.

    Returns (renamed_df, mapping_report, unmapped_columns). mapping_report maps
    canonical field -> original column name; unmapped lists source columns that
    matched no canonical field (they stay in the frame verbatim).
    """
    df = raw_df.copy()
    df.columns = [_normalize_header(column) for column in df.columns]
    mapping: dict[str, str] = {}
    used_sources: set[str] = set()
    for canonical, source in (overrides or {}).items():
        if canonical not in COLUMN_ALIASES:
            raise ValueError(
                f"未知的目标字段：{canonical} / Unknown target field: {canonical}"
            )
        normalized_source = _normalize_header(str(source))
        if normalized_source not in df.columns:
            raise ValueError(
                f"字段映射指向不存在的列：{source} / "
                f"Override points to a column that does not exist: {source}"
            )
        forbidden_reason = _forbidden_caliber_reason(canonical, normalized_source)
        if forbidden_reason is not None:
            raise ValueError(forbidden_reason)
        mapping[canonical] = normalized_source
        used_sources.add(normalized_source)
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical in mapping:
            continue
        for alias in aliases:
            normalized_alias = _normalize_header(alias)
            if normalized_alias in df.columns and normalized_alias not in used_sources:
                mapping[canonical] = normalized_alias
                used_sources.add(normalized_alias)
                break
    renamed = df.rename(
        columns={source: canonical for canonical, source in mapping.items()}
    )
    unmapped = [column for column in renamed.columns if column not in COLUMN_ALIASES]
    return renamed, mapping, unmapped


def prepare_import_frame(
    raw_df: pd.DataFrame, file_name: str, column_overrides: dict[str, str] | None = None
) -> tuple[pd.DataFrame, dict]:
    """Build an analysis-ready frame plus an honest import report."""
    if raw_df is None or raw_df.empty:
        raise ValueError("上传文件没有可读的数据行。/ Uploaded file has no readable data rows.")

    renamed, mapping, unmapped = map_columns(raw_df, overrides=column_overrides)
    if BOND_NAME not in renamed.columns:
        raise ValueError(
            "未找到债券简称列（可用列名：债券简称/债券名称/简称）。/ "
            "No bond-name column found (accepted: 债券简称/债券名称/简称)."
        )

    frame = renamed.dropna(subset=[BOND_NAME]).copy()
    frame[BOND_NAME] = frame[BOND_NAME].astype(str).str.strip()
    frame = frame[frame[BOND_NAME] != ""]
    if frame.empty:
        raise ValueError("债券简称列全部为空。/ Bond-name column is entirely empty.")
    # Rows with only a name are kept: incomplete fields disable analyses, not rows.

    for numeric_field in (YIELD, WEIGHTED_YIELD, PRICE, VOLUME):
        if numeric_field in frame.columns:
            frame[numeric_field] = pd.to_numeric(frame[numeric_field], errors="coerce")
    if BOND_CODE in frame.columns:
        frame[BOND_CODE] = frame[BOND_CODE].astype("string").str.strip().replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})

    if MATURITY in frame.columns:
        # Normalize residual maturity to text so the shared parser and the
        # display-rewrite in _apply_maturity_details both work: numeric columns
        # (e.g. 6.5 meaning years) become "6.5Y"-style text first.
        if not pd.api.types.is_object_dtype(frame[MATURITY]) and not pd.api.types.is_string_dtype(frame[MATURITY]):
            frame[MATURITY] = frame[MATURITY].map(
                lambda value: None if pd.isna(value) else f"{float(value):g}Y"
            )
        else:
            frame[MATURITY] = frame[MATURITY].astype("string").str.strip()
        frame = _apply_maturity_details(frame, source_label="imported_field")
    if YIELD in frame.columns:
        # Rate sensitivity needs a yield column; skip honestly when absent.
        frame = _attach_rate_sensitivity(frame)

    missing_fields = [
        field
        for field in (YIELD, MATURITY, VOLUME, PRICE, BOND_CODE)
        if field not in frame.columns or frame[field].isna().all()
    ]

    yield_available = YIELD in frame.columns and bool(frame[YIELD].notna().any())
    volume_available = VOLUME in frame.columns and bool(frame[VOLUME].notna().any())
    maturity_available = (
        "待偿期(年)" in frame.columns and bool(frame["待偿期(年)"].notna().any())
    )
    available_analyses = {
        "yield_stats": yield_available,
        "ranking": yield_available,
        "outlier_detection": yield_available and maturity_available,
        "peer_compare": yield_available and maturity_available,
        "monitor": yield_available,
        "volume_stats": volume_available,
    }

    notes_zh: list[str] = []
    notes_en: list[str] = []
    if not yield_available:
        notes_zh.append("缺少收益率列：排序、组内偏离、同业比较与监控面板已禁用，其余分析照常。")
        notes_en.append("Yield column missing: ranking, deviation, peer comparison and monitor are disabled.")
    if not maturity_available:
        notes_zh.append("缺少待偿期列：期限分桶与同业期限比较不可用，概览与检索照常。")
        notes_en.append("Maturity column missing: maturity bucketing and peer maturity comparison are unavailable.")
    if not volume_available:
        notes_zh.append("缺少交易量列：成交量相关统计已禁用。")
        notes_en.append("Volume column missing: volume statistics are disabled.")
    if BOND_CODE not in frame.columns:
        notes_zh.append("未提供债券代码列：代码检索不可用，可按简称检索。")
        notes_en.append("No bond-code column: code search unavailable; search by name still works.")

    # Same-name columns with a different measurement口径 are never auto-mapped;
    # keep the raw column and tell the user exactly why (they can re-map via
    # column_overrides once they confirm the口径 themselves).
    unsupported = {
        column: reason
        for column, reason in UNSUPPORTED_COLUMN_REASONS.items()
        if column in unmapped
    }
    for column, reason in unsupported.items():
        notes_zh.append(
            f"{reason}，未自动映射；原列「{column}」已保留。BondLens 拒绝把该列映射为标准字段，"
            "如需使用请在上传前自行核对口径并完成换算。"
        )
        notes_en.append(
            f"Column '{column}' kept but NOT auto-mapped ({reason}); BondLens also rejects "
            "explicitly mapping it to a canonical field. Convert/verify the caliber yourself before upload."
        )

    date_info = _derive_quote_date(frame)
    sample_date = date_info["sample_date"]
    quote_time = date_info["quote_time"]
    if date_info["mixed_timepoints"]:
        notes_zh.append(
            f"样本跨多个日期（{date_info['quote_date_range']}，共 {date_info['distinct_date_count']} 天）："
            "截面统计混合了不同行情时点，请勿解读为单日截面。"
        )
        notes_en.append(
            f"Sample spans multiple dates ({date_info['quote_date_range']}, "
            f"{date_info['distinct_date_count']} days): cross-section statistics mix quote "
            "times and must not be read as a single-day snapshot."
        )
    elif sample_date is not None:
        notes_zh.append(f"数据日期：{sample_date}（取自行情时间列）。")
        notes_en.append(f"Sample date: {sample_date} (from the quote-time column).")
    else:
        notes_zh.append("未提供可读的数据日期：样本日期未知。")
        notes_en.append("No readable quote-date column: the sample date is unknown.")

    distinct_bonds = _distinct_bond_count(frame)
    duplicate_rows = int(len(frame) - distinct_bonds)
    if duplicate_rows > 0:
        notes_zh.append(
            f"检测到 {duplicate_rows} 条同一债券的多行记录（可能为多笔成交或重复快照），按原样保留、不去重。"
        )
        notes_en.append(
            f"{duplicate_rows} repeated rows for the same bond (possible multi-deals/duplicate snapshots) kept as-is; no dedup applied."
        )

    report = {
        "file_name": file_name,
        "mapped_columns": mapping,
        "column_overrides": dict(column_overrides or {}),
        "unmapped_columns": unmapped,
        "unsupported_columns": list(unsupported),
        "missing_fields": missing_fields,
        "available_analyses": available_analyses,
        "row_count": len(frame),
        "bond_count": distinct_bonds,
        "sample_date": sample_date,
        "quote_time": quote_time,
        "quote_date_range": date_info["quote_date_range"],
        "distinct_date_count": date_info["distinct_date_count"],
        "mixed_timepoints": date_info["mixed_timepoints"],
        "valid_yield_count": int(frame[YIELD].notna().sum()) if YIELD in frame.columns else 0,
        "record_granularity_zh": (
            "每行为一条行情记录；同一简称多行可能是多笔成交或重复快照，不做去重合并。"
        ),
        "record_granularity_en": (
            "Each row is one quote record; repeated names may be multiple deals or snapshots and are not deduplicated."
        ),
        "notes_zh": notes_zh,
        "notes_en": notes_en,
    }
    return frame, report


def describe_import_source(frame: pd.DataFrame, report: dict | None = None) -> dict:
    """Build a data_source profile for an uploaded frame (runtime_mode=import).

    When ``report`` is omitted (agent invoked with a bare frame), identity
    counts are derived directly from the frame.
    """
    report = report or {}
    bond_count = report.get("bond_count") or _distinct_bond_count(frame)
    valid_yield = int(frame[YIELD].notna().sum()) if YIELD in frame.columns else 0
    date_info = _derive_quote_date(frame)
    sample_date = report.get("sample_date") or date_info["sample_date"]
    quote_time = report.get("quote_time") or date_info["quote_time"]
    quote_date_range = report.get("quote_date_range") or date_info["quote_date_range"]
    mixed = bool(report.get("mixed_timepoints") or date_info["mixed_timepoints"])
    if mixed:
        sample_date = None
        sample_date_note_zh = (
            f"样本跨多个日期（{quote_date_range}）：混合时点样本，请勿解读为单日截面。"
        )
        sample_date_note_en = (
            f"Sample spans multiple dates ({quote_date_range}); a mixed-timepoint sample, "
            "not a single-day snapshot."
        )
    elif sample_date:
        sample_date_note_zh = f"数据日期：{sample_date}（取自行情时间列）。"
        sample_date_note_en = f"Sample date: {sample_date} (from the quote-time column)."
    else:
        sample_date_note_zh = "未提供可读的数据日期：样本日期未知。"
        sample_date_note_en = "No readable quote-date column: the sample date is unknown."
    available = report.get("available_analyses") or {
        "yield_stats": valid_yield > 0,
        "ranking": valid_yield > 0,
        "outlier_detection": valid_yield > 0 and "待偿期(年)" in frame.columns,
        "peer_compare": valid_yield > 0 and "待偿期(年)" in frame.columns,
        "monitor": valid_yield > 0,
        "volume_stats": VOLUME in frame.columns and bool(frame[VOLUME].notna().any()),
    }
    return {
        "source_id": IMPORT_SOURCE_ID,
        "source_name": report.get("file_name") or "uploaded file",
        "storage": "User upload kept in process memory / temp dir; never committed to the repository.",
        "runtime_mode": "import",
        "requested_mode": "import",
        "fetched_at": None,
        "sample_date": sample_date,
        "quote_time": quote_time,
        "quote_date_range": quote_date_range,
        "mixed_timepoints": mixed,
        "fallback_reason": None,
        "provider": "local_upload",
        "active_live_feed": False,
        "active_live_snapshot": False,
        "limitations": [],
        "row_count": report.get("row_count", len(frame)),
        "bond_count": bond_count,
        "sample_date_note_zh": sample_date_note_zh,
        "sample_date_note_en": sample_date_note_en,
        "record_granularity_zh": report.get("record_granularity_zh") or (
            "每行为一条行情记录；同一简称多行可能是多笔成交或重复快照，不做去重合并。"
        ),
        "record_granularity_en": report.get("record_granularity_en") or (
            "Each row is one quote record; repeated names may be multiple deals or snapshots and are not deduplicated."
        ),
        "valid_yield_count": report.get("valid_yield_count", valid_yield),
        "columns": [column for column in frame.columns if not column.startswith("Unnamed")],
        "has_code_column": BOND_CODE in frame.columns,
        "available_analyses": available,
        "import_notes_zh": report.get("notes_zh") or [],
        "import_notes_en": report.get("notes_en") or [],
    }
