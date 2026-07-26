from __future__ import annotations

import contextlib
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "testdata.xlsx"
DEFAULT_LIVE_CACHE_PATH = PROJECT_ROOT / ".tmp" / "bond_spot_deal_snapshot.csv"

BOND_NAME = "债券简称"
MATURITY = "待偿期"
PRICE = "收盘净价(元)"
YIELD = "收盘到期收益率(%)"
WEIGHTED_YIELD = "加权收益率(%)"
VOLUME = "交易量(亿元)"
MATURITY_YEARS = "待偿期(年)"

NUMERIC_COLUMNS = [PRICE, YIELD, WEIGHTED_YIELD, VOLUME]
REQUIRED_COLUMNS = [BOND_NAME, MATURITY, PRICE, YIELD, WEIGHTED_YIELD, VOLUME]
LIVE_CHANGE_BP = "涨跌(BP)"
MATURITY_SOURCE = "待偿期来源"
MATURITY_RAW = "待偿期原文"
IS_PERPETUAL = "是否永续风格"
MATURITY_PARSE_NOTE = "待偿期解析说明"
MODIFIED_DURATION = "修正久期(现金流假设)"
DV01 = "DV01(现金流假设)"
MACAULAY_DURATION = "麦考利久期(现金流假设)"
DURATION_METHOD = "久期方法"
PERPETUAL_MOD_DURATION = "理论永续修正久期"
DATA_MODES = {"auto", "live", "static"}
STATIC_SAMPLE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

_STATIC_DF_CACHE: dict[str, pd.DataFrame] = {}
_STATIC_DF_LOCK = __import__("threading").Lock()
_CHINAMONEY_SESSION = None
_CHINAMONEY_SESSION_LOCK = __import__("threading").Lock()


def parse_maturity_details(value: object) -> dict:
    """Parse residual maturity with honest perpetual dual-scenario handling.

    ChinaMoney perpetual-style residuals look like ``5Y+65.44Y+N``.
    We expose:
    - first finite leg (often nearer to next call/window) as primary analytics tenor
    - remote finite legs if present
    - dual scenarios: call/first-leg vs theoretical perpetual (+N)
    without inventing a multi-century single residual.
    """
    empty = {
        "years": None,
        "is_perpetual": False,
        "raw": None,
        "display": None,
        "note_zh": None,
        "note_en": None,
        "legs": [],
        "first_leg_years": None,
        "remote_leg_years": None,
        "scenarios": [],
    }
    if pd.isna(value):
        return empty
    raw = str(value).strip()
    original = raw.upper().replace(" ", "")
    if not original or original in {"N", "NA", "NULL", "-"}:
        return {**empty, "raw": raw or None}
    has_perpetual_marker = bool(re.search(r"(?:\+N\b|\bN\b)$", original)) or "+N" in original
    cleaned = re.sub(r"\+N\b", "", original).strip("+").strip()
    if not cleaned or cleaned == "N":
        scenarios = (
            [
                {
                    "id": "perpetual",
                    "label_zh": "理论永续",
                    "label_en": "Theoretical perpetual",
                    "years": None,
                }
            ]
            if has_perpetual_marker
            else []
        )
        return {
            **empty,
            "raw": raw,
            "is_perpetual": has_perpetual_marker,
            "note_zh": "永续风格残期无法解析为有限年限；仅保留理论永续情景。" if has_perpetual_marker else None,
            "note_en": (
                "Perpetual-style residual could not be parsed to a finite tenor; "
                "only a theoretical perpetual scenario remains."
                if has_perpetual_marker
                else None
            ),
            "scenarios": scenarios,
        }
    parts = [part for part in cleaned.split("+") if part and part != "N"]
    if not parts:
        return {**empty, "raw": raw, "is_perpetual": has_perpetual_marker}
    parsed_parts = [_parse_maturity_part(part) for part in parts]
    if has_perpetual_marker:
        first = parsed_parts[0] if parsed_parts else None
        remote = None
        if len(parsed_parts) > 1 and all(p is not None for p in parsed_parts[1:]):
            remote = float(sum(float(p) for p in parsed_parts[1:]))
        if first is None:
            return {**empty, "raw": raw, "is_perpetual": True, "legs": parts}
        display = f"{_format_maturity(first)} (call/first-leg; perpetual +N)"
        scenarios = [
            {
                "id": "call_or_first_leg",
                "label_zh": "行权/首段有限期限",
                "label_en": "Call / first finite leg",
                "years": float(first),
            },
            {
                "id": "perpetual",
                "label_zh": "理论永续(+N)",
                "label_en": "Theoretical perpetual (+N)",
                "years": None,
            },
        ]
        if remote is not None:
            scenarios.insert(
                1,
                {
                    "id": "remote_leg",
                    "label_zh": "远端有限段(行情拼写)",
                    "label_en": "Remote finite leg (feed spelling)",
                    "years": remote,
                },
            )
        return {
            "years": float(first),
            "is_perpetual": True,
            "raw": raw,
            "display": display,
            "note_zh": (
                f"永续风格残期原文 {raw}；主分析取首段 {_format_maturity(first)} 作为行权/观察窗口，"
                f"并并行给出理论永续情景"
                + (f"，远端拼写段 {_format_maturity(remote)}" if remote is not None else "")
                + "。非完整含权永续定价。"
            ),
            "note_en": (
                f"Perpetual-style residual raw={raw}; primary analytics uses first finite leg "
                f"{_format_maturity(first)} as call/observation window, with a parallel theoretical "
                f"perpetual scenario"
                + (f" and remote feed leg {_format_maturity(remote)}" if remote is not None else "")
                + ". Not a full callable-perpetual pricer."
            ),
            "legs": parts,
            "first_leg_years": float(first),
            "remote_leg_years": remote,
            "scenarios": scenarios,
        }
    if any(part is None for part in parsed_parts):
        return {**empty, "raw": raw}
    years = sum(float(part) for part in parsed_parts)
    return {
        "years": years,
        "is_perpetual": False,
        "raw": raw,
        "display": _format_maturity(years),
        "note_zh": None,
        "note_en": None,
        "legs": parts,
        "first_leg_years": float(parsed_parts[0]) if parsed_parts else None,
        "remote_leg_years": None,
        "scenarios": [
            {
                "id": "to_maturity",
                "label_zh": "至到期",
                "label_en": "To maturity",
                "years": years,
            }
        ],
    }


def parse_maturity_to_years(value: object) -> float | None:
    return parse_maturity_details(value)["years"]


def infer_static_sample_date(path: str | Path = DEFAULT_DATA_PATH):
    data_path = Path(path)
    try:
        header = pd.read_excel(data_path, header=None, nrows=1).astype(str).to_string()
    except Exception:  # noqa: BLE001 - Excel/path failures should degrade to None
        return None

    match = STATIC_SAMPLE_DATE_RE.search(header)
    if not match:
        return None
    # Sample date is a calendar date from the workbook header (no timezone).
    year, month, day = (int(part) for part in match.group(1).split("-"))
    return date(year, month, day)


def clear_static_bond_cache() -> None:
    """Drop cached static DataFrames (tests / explicit reload)."""
    with _STATIC_DF_LOCK:
        _STATIC_DF_CACHE.clear()


def load_bond_data(path: str | Path = DEFAULT_DATA_PATH, *, use_cache: bool = True) -> pd.DataFrame:
    data_path = Path(path)
    if not data_path.exists():
        raise FileNotFoundError(f"Bond data file not found: {data_path}")

    cache_key = str(data_path.resolve())
    if use_cache:
        with _STATIC_DF_LOCK:
            cached = _STATIC_DF_CACHE.get(cache_key)
            if cached is not None:
                return cached.copy(deep=False)

    df = pd.read_excel(data_path, header=1)
    df.columns = [str(column).strip() for column in df.columns]

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    df = df.dropna(subset=[BOND_NAME]).copy()
    df[BOND_NAME] = df[BOND_NAME].astype(str).str.strip()
    df = _apply_maturity_details(df, source_label="source_field")
    for column in NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = _attach_rate_sensitivity(df)
    if use_cache:
        with _STATIC_DF_LOCK:
            _STATIC_DF_CACHE[cache_key] = df
        return df.copy(deep=False)
    return df



def _chinamoney_session():
    """Reuse one requests.Session for ChinaMoney POSTs (connection keep-alive)."""
    global _CHINAMONEY_SESSION
    import requests

    with _CHINAMONEY_SESSION_LOCK:
        if _CHINAMONEY_SESSION is None:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; BondLens/1.0; "
                        "+https://github.com/Phoenix0531-sudo/BondLens)"
                    )
                }
            )
            _CHINAMONEY_SESSION = session
        return _CHINAMONEY_SESSION


def fetch_chinamoney_bond_spot_deal() -> pd.DataFrame:
    """Fetch ChinaMoney spot deals and keep native residual maturity.

    AkShare ``bond_spot_deal`` hits the same endpoint but drops ``termToMaturity``.
    BondLens keeps that field so live peer/maturity buckets are usable.
    """
    url = "https://www.chinamoney.com.cn/ags/ms/cm-u-md-bond/CbtPri"
    payload = {"flag": "1", "lang": "cn", "bondName": ""}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; BondLens/1.0; +https://github.com/Phoenix0531-sudo/BondLens)"
        ),
        "Referer": "https://www.chinamoney.com.cn/chinese/mkdatabond/",
    }
    # Align HTTP timeout with outer live wall-clock (BOND_LIVE_FETCH_TIMEOUT_SECONDS, default 12).
    outer = _live_fetch_timeout_seconds()
    http_timeout = max(3.0, min(float(outer), 20.0))
    session = _chinamoney_session()
    response = session.post(url, data=payload, headers=headers, timeout=http_timeout)
    response.raise_for_status()
    payload_json = response.json()
    records = payload_json.get("records") or []
    if not records:
        raise ValueError("ChinaMoney bond spot deal returned no records")

    rows: list[dict] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name = (
            record.get("abdAssetEncdShrtDesc")
            or record.get("abdAssetEncdShrtDescByRmb")
            or record.get("abdAssetEncdFullDescByRmb")
            or ""
        )
        rows.append(
            {
                "债券简称": str(name).strip(),
                "成交净价": record.get("dmiLatestRate"),
                "最新收益率": record.get("dmiLatestContraRate"),
                "涨跌": record.get("bp"),
                "加权收益率": record.get("dmiWghtdContraRate"),
                "交易量": record.get("dmiTtlTradedAmnt"),
                "待偿期": record.get("termToMaturity"),
                "债券代码": record.get("bondcode"),
                "行情时间": record.get("showDate"),
            }
        )

    raw_df = pd.DataFrame(rows)
    if raw_df.empty or "债券简称" not in raw_df.columns:
        raise ValueError("ChinaMoney bond spot deal payload could not be normalized")
    return raw_df


def load_live_bond_data(
    fetcher=None,
    cache_path: str | Path | None = None,
    write_cache: bool = True,
    security_master: pd.DataFrame | None = None,
    timeout_seconds: float | None = None,
) -> pd.DataFrame:
    if fetcher is None:
        fetcher = fetch_chinamoney_bond_spot_deal

    timeout = _live_fetch_timeout_seconds(timeout_seconds)
    raw_df = _call_with_timeout(fetcher, timeout_seconds=timeout, label="live bond fetch")
    df = normalize_live_bond_data(raw_df, security_master=security_master)
    if write_cache:
        save_live_snapshot(df, cache_path=cache_path)
    return df


def normalize_live_bond_data(raw_df: pd.DataFrame, security_master: pd.DataFrame | None = None) -> pd.DataFrame:
    required = ["债券简称", "成交净价", "最新收益率", "加权收益率", "交易量"]
    missing = [column for column in required if column not in raw_df.columns]
    if missing:
        raise ValueError(f"Missing live bond columns: {', '.join(missing)}")

    df = pd.DataFrame()
    df[BOND_NAME] = raw_df["债券简称"].where(raw_df["债券简称"].notna(), "").astype(str).str.strip()
    native_maturity = _extract_native_maturity_series(raw_df)
    df[MATURITY] = native_maturity
    df[PRICE] = pd.to_numeric(raw_df["成交净价"], errors="coerce")
    df[YIELD] = pd.to_numeric(raw_df["最新收益率"], errors="coerce")
    df[WEIGHTED_YIELD] = pd.to_numeric(raw_df["加权收益率"], errors="coerce")
    df[VOLUME] = pd.to_numeric(raw_df["交易量"], errors="coerce")
    if "涨跌" in raw_df.columns:
        df[LIVE_CHANGE_BP] = pd.to_numeric(raw_df["涨跌"], errors="coerce")
    df = _apply_maturity_details(df, source_label="chinamoney_term_to_maturity")
    df = df[df[BOND_NAME] != ""].copy()
    df = enrich_live_maturity_from_static_master(df, security_master=security_master)
    return _attach_rate_sensitivity(df)


def enrich_live_maturity_from_static_master(
    df: pd.DataFrame,
    security_master: pd.DataFrame | None = None,
    reference_date=None,
    current_date=None,
) -> pd.DataFrame:
    """Fill residual maturity gaps from the local static master.

    Native ChinaMoney ``termToMaturity`` always wins. Static matching is only a
    secondary backfill for rows the live feed left blank or unparsable.
    """
    if security_master is None:
        try:
            security_master = load_bond_data()
        except Exception:  # noqa: BLE001 - missing master must not break live path
            return df

    required = {BOND_NAME, MATURITY, MATURITY_YEARS}
    if not required.issubset(security_master.columns):
        return df

    reference_date = reference_date or infer_static_sample_date()
    current_date = current_date or datetime.now(timezone.utc).date()
    elapsed_years = _elapsed_years(reference_date, current_date)
    maturity_by_name = (
        security_master.dropna(subset=[BOND_NAME, MATURITY_YEARS])
        .drop_duplicates(subset=[BOND_NAME])
        .set_index(BOND_NAME)[[MATURITY, MATURITY_YEARS]]
    )

    enriched = df.copy()
    if MATURITY_YEARS not in enriched.columns:
        enriched[MATURITY_YEARS] = None
    if MATURITY not in enriched.columns:
        enriched[MATURITY] = None
    if MATURITY_SOURCE not in enriched.columns:
        enriched[MATURITY_SOURCE] = None

    for index, row in enriched.iterrows():
        if pd.notna(row.get(MATURITY_YEARS)):
            continue
        name = row.get(BOND_NAME)
        if not name or name not in maturity_by_name.index:
            continue
        master_row = maturity_by_name.loc[name]
        years = master_row[MATURITY_YEARS]
        if pd.isna(years):
            continue

        adjusted_years = max(float(years) - elapsed_years, 0.0) if elapsed_years is not None else float(years)
        enriched.at[index, MATURITY_YEARS] = round(adjusted_years, 4)
        enriched.at[index, MATURITY] = _format_maturity(adjusted_years)
        if reference_date:
            enriched.at[index, MATURITY_SOURCE] = f"local_static_excel_adjusted_from_{reference_date.isoformat()}"
        else:
            enriched.at[index, MATURITY_SOURCE] = "local_static_excel_unadjusted"
    return enriched


def save_live_snapshot(df: pd.DataFrame, cache_path: str | Path | None = None) -> Path:
    snapshot_path = _live_cache_path(cache_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(snapshot_path, index=False, encoding="utf-8-sig")
    return snapshot_path


def load_live_snapshot(cache_path: str | Path | None = None, max_age_hours: float | None = None) -> tuple[pd.DataFrame, dict]:
    snapshot_path = _live_cache_path(cache_path)
    if not snapshot_path.exists():
        raise FileNotFoundError(f"Live snapshot cache not found: {snapshot_path}")

    cached_at = datetime.fromtimestamp(snapshot_path.stat().st_mtime, timezone.utc)
    if max_age_hours is not None:
        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            raise ValueError(f"Live snapshot cache is stale: {age_hours:.2f} hours old")

    df = pd.read_csv(snapshot_path, encoding="utf-8-sig")
    for column in [PRICE, YIELD, WEIGHTED_YIELD, VOLUME, LIVE_CHANGE_BP, MATURITY_YEARS]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    if BOND_NAME in df.columns:
        df[BOND_NAME] = df[BOND_NAME].where(df[BOND_NAME].notna(), "").astype(str).str.strip()
    return df[df[BOND_NAME] != ""].copy(), {"snapshot_path": snapshot_path, "cached_at": cached_at}


def resolve_bond_data(
    mode: str = "static",
    path: str | Path | None = DEFAULT_DATA_PATH,
    live_fetcher=None,
    live_cache_path: str | Path | None = None,
    cache_max_age_hours: float | None = None,
) -> tuple[pd.DataFrame, dict]:
    data_path = path or DEFAULT_DATA_PATH
    cache_max_age = _cache_max_age_hours(cache_max_age_hours)
    normalized_mode = (mode or "static").lower()
    if normalized_mode not in DATA_MODES:
        raise ValueError(f"Unsupported bond data mode: {mode}. Choose from: {', '.join(sorted(DATA_MODES))}")

    if normalized_mode in {"auto", "live"}:
        try:
            df = load_live_bond_data(fetcher=live_fetcher, cache_path=live_cache_path)
            return df, _build_live_profile(df, requested_mode=normalized_mode)
        except Exception as exc:  # noqa: BLE001 - live network/provider failures must fall back
            live_error = f"{type(exc).__name__}: {exc}"
            try:
                df, snapshot = load_live_snapshot(cache_path=live_cache_path, max_age_hours=cache_max_age)
                return df, _build_snapshot_profile(
                    df,
                    requested_mode=normalized_mode,
                    snapshot_path=snapshot["snapshot_path"],
                    cached_at=snapshot["cached_at"],
                    fallback_reason=live_error,
                )
            except Exception as snapshot_exc:  # noqa: BLE001 - snapshot path also degrades honestly
                fallback_reason = f"{live_error}; snapshot fallback failed: {type(snapshot_exc).__name__}: {snapshot_exc}"
            df = load_bond_data(data_path)
            return df, _build_static_profile(
                df,
                path=data_path,
                runtime_mode="static_fallback",
                requested_mode=normalized_mode,
                fallback_reason=fallback_reason,
            )

    df = load_bond_data(data_path)
    return df, _build_static_profile(df, path=data_path, runtime_mode="static_sample", requested_mode=normalized_mode)


def describe_data_source(path: str | Path = DEFAULT_DATA_PATH) -> dict:
    df = load_bond_data(path)
    return _build_static_profile(df, path=path, runtime_mode="static_sample", requested_mode="static")


def _build_static_profile(
    df: pd.DataFrame,
    path: str | Path = DEFAULT_DATA_PATH,
    runtime_mode: str = "static_sample",
    requested_mode: str = "static",
    fallback_reason: str | None = None,
) -> dict:
    data_path = Path(path)
    relative_path = data_path
    with contextlib.suppress(ValueError):
        relative_path = data_path.resolve().relative_to(PROJECT_ROOT)

    return {
        "source_id": "local_static_excel",
        "source_name": str(relative_path).replace("\\", "/"),
        "storage": "Excel workbook committed with the repository",
        "runtime_mode": runtime_mode,
        "requested_mode": requested_mode,
        "fetched_at": None,
        "fallback_reason": fallback_reason,
        "row_count": len(df),
        "valid_yield_count": int(df[YIELD].notna().sum()),
        "columns": [BOND_NAME, MATURITY, PRICE, YIELD, WEIGHTED_YIELD, VOLUME, MATURITY_SOURCE],
        "maturity_coverage": _maturity_coverage(df),
        "active_live_feed": False,
        "active_live_snapshot": False,
        "provider": "repository",
        "legacy_crawler": {
            "path": "undergraduate-thesis-2024:data/Crawler.py",
            "status": "preserved_in_undergraduate_thesis_branch",
            "targets": [
                "http://company.cnstock.com/company/scp_gsxw/",
                "http://ggjd.cnstock.com/gglist/search/qmtbbdj/",
                "http://ggjd.cnstock.com/gglist/search/ggkx/",
            ],
            "notes": [
                "The current main branch does not include, import, or call this crawler.",
                "The legacy crawler depends on MongoDB and thesis-era text analysis modules.",
                "Legacy CNSTOCK endpoints are not treated as a reliable live data source.",
            ],
        },
        "limitations": [
            "Static repository sample, not real-time market data.",
            "No issuer rating, credit event, macro curve, or news feed is attached.",
            "Use results as an engineering demo and evidence-grounded sample analysis only.",
        ],
    }


def _build_live_profile(df: pd.DataFrame, requested_mode: str) -> dict:
    return {
        "source_id": "chinamoney_bond_spot_deal",
        "source_name": "ChinaMoney bond spot deal (CbtPri)",
        "provider": "ChinaMoney public bond market data",
        "target_url": "https://www.chinamoney.com.cn/chinese/mkdatabond/",
        "storage": "Fetched at request time; not persisted",
        "runtime_mode": "live",
        "requested_mode": requested_mode,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fallback_reason": None,
        "row_count": len(df),
        "valid_yield_count": int(df[YIELD].notna().sum()),
        "columns": [BOND_NAME, MATURITY, PRICE, YIELD, WEIGHTED_YIELD, VOLUME, LIVE_CHANGE_BP, MATURITY_SOURCE],
        "maturity_coverage": _maturity_coverage(df),
        "active_live_feed": True,
        "active_live_snapshot": False,
        "legacy_crawler": {
            "path": "undergraduate-thesis-2024:data/Crawler.py",
            "status": "preserved_in_undergraduate_thesis_branch",
        },
        "limitations": [
            "Public live endpoint availability depends on third-party source stability and trading session.",
            "Native residual maturity comes from ChinaMoney termToMaturity when present; perpetual-style +N legs are only partially parsed.",
            "Issuer rating, credit events, and macro curve fields are still not attached.",
            "Rows without native maturity may fall back to the local static sample by exact bond name.",
            "Use live results as market monitoring evidence, not investment advice.",
        ],
    }


def _build_snapshot_profile(
    df: pd.DataFrame,
    requested_mode: str,
    snapshot_path: Path,
    cached_at: datetime,
    fallback_reason: str,
) -> dict:
    relative_path = snapshot_path
    with contextlib.suppress(ValueError):
        relative_path = snapshot_path.resolve().relative_to(PROJECT_ROOT)

    return {
        "source_id": "chinamoney_bond_spot_deal_snapshot",
        "source_name": "Cached ChinaMoney bond spot deal snapshot",
        "provider": "ChinaMoney public bond market data",
        "target_url": "https://www.chinamoney.com.cn/chinese/mkdatabond/",
        "storage": str(relative_path).replace("\\", "/"),
        "runtime_mode": "live_snapshot",
        "requested_mode": requested_mode,
        "fetched_at": cached_at.isoformat(),
        "fallback_reason": fallback_reason,
        "row_count": len(df),
        "valid_yield_count": int(df[YIELD].notna().sum()),
        "columns": [BOND_NAME, MATURITY, PRICE, YIELD, WEIGHTED_YIELD, VOLUME, LIVE_CHANGE_BP, MATURITY_SOURCE],
        "maturity_coverage": _maturity_coverage(df),
        "active_live_feed": False,
        "active_live_snapshot": True,
        "legacy_crawler": {
            "path": "undergraduate-thesis-2024:data/Crawler.py",
            "status": "preserved_in_undergraduate_thesis_branch",
        },
        "limitations": [
            "Live fetch failed, so this answer uses the most recent local live-data snapshot.",
            "Snapshot freshness depends on the last successful ChinaMoney/AkShare-compatible request.",
            "Issuer rating, credit events, macro curve, and full security master fields are still not attached.",
            "Maturity coverage in the snapshot reflects whatever was saved on the last successful live fetch.",
        ],
    }


def _live_cache_path(cache_path: str | Path | None = None) -> Path:
    configured = cache_path or os.environ.get("BOND_LIVE_CACHE_PATH")
    return Path(configured) if configured else DEFAULT_LIVE_CACHE_PATH


def _cache_max_age_hours(value: float | None) -> float | None:
    if value is not None:
        return value
    configured = os.environ.get("BOND_LIVE_CACHE_MAX_AGE_HOURS")
    return float(configured) if configured else 24.0


def _live_fetch_timeout_seconds(value: float | None = None) -> float:
    if value is not None:
        return float(value)
    configured = os.environ.get("BOND_LIVE_FETCH_TIMEOUT_SECONDS")
    if configured:
        return float(configured)
    return 12.0


def _call_with_timeout(func, *, timeout_seconds: float, label: str = "operation"):
    """Run a blocking call with a hard wall-clock timeout.

    Live market fetchers (AkShare/network) can hang far longer than a demo page
    should wait. Timing out here lets auto/live modes fall back to snapshot/static
    instead of freezing the Flask request forever.
    """
    if timeout_seconds <= 0:
        return func()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"{label} timed out after {timeout_seconds:.1f}s") from exc



def _apply_maturity_details(df: pd.DataFrame, source_label: str | None = None) -> pd.DataFrame:
    """Attach parsed residual maturity fields from the MATURITY column."""
    out = df.copy()
    details = out[MATURITY].map(parse_maturity_details) if MATURITY in out.columns else pd.Series([{}] * len(out), index=out.index)
    out[MATURITY_RAW] = details.map(lambda item: (item or {}).get("raw"))
    out[IS_PERPETUAL] = details.map(lambda item: bool((item or {}).get("is_perpetual")))
    out[MATURITY_PARSE_NOTE] = details.map(lambda item: (item or {}).get("note_zh"))
    out[MATURITY_YEARS] = details.map(lambda item: (item or {}).get("years"))
    out["首段期限(年)"] = details.map(lambda item: (item or {}).get("first_leg_years"))
    out["远端期限(年)"] = details.map(lambda item: (item or {}).get("remote_leg_years"))
    out["期限情景"] = details.map(lambda item: (item or {}).get("scenarios") or [])
    if source_label:
        out[MATURITY_SOURCE] = None
        native_mask = out[MATURITY_YEARS].notna()
        out.loc[native_mask, MATURITY_SOURCE] = source_label
    display = details.map(lambda item: (item or {}).get("display"))
    mask = display.notna()
    out.loc[mask, MATURITY] = display[mask]
    return out


def cashflow_modified_duration(
    years: float | None,
    yield_pct: float | None,
    *,
    frequency: int = 1,
    coupon_pct: float | None = None,
    perpetual: bool = False,
) -> dict:
    """Cashflow Macaulay/modified duration under explicit teaching assumptions.

    Assumptions (documented, not market-complete):
    - annual (frequency=1) or semi-annual coupons
    - coupon defaults to yield (par-bond teaching case) when unknown
    - residual maturity rounded to whole periods
    - perpetual-style theoretical consol uses MacD=(1+y/f)/(y/f)/f years
    - not OAS / not callable tree / not exchange cashflow schedule
    """
    empty = {
        "macaulay_duration": None,
        "modified_duration": None,
        "dv01": None,
        "method": None,
        "frequency": frequency,
        "coupon_pct": None,
        "n_periods": None,
        "assumptions": [],
    }
    if yield_pct is None or (isinstance(yield_pct, float) and pd.isna(yield_pct)):
        return empty
    y = float(yield_pct) / 100.0
    if y <= -0.999999:
        return empty
    freq = max(int(frequency or 1), 1)
    cpn = float(coupon_pct) / 100.0 if coupon_pct is not None and not pd.isna(coupon_pct) else y
    assumptions = [
        f"frequency={freq}/year",
        "coupon defaults to YTM (par teaching case) when schedule unknown",
        "not OAS / not issuer call tree",
    ]
    # Theoretical consol / pure perpetual when years missing and perpetual flag set.
    if perpetual and (years is None or (isinstance(years, float) and pd.isna(years)) or float(years) <= 0):
        if y <= 0:
            return {**empty, "assumptions": [*assumptions, "perpetual requires positive yield"]}
        y_period = y / freq
        mac = (1.0 + y_period) / y_period / freq
        mod = mac / (1.0 + y_period)
        return {
            "macaulay_duration": round(mac, 4),
            "modified_duration": round(mod, 4),
            "dv01": round(mod * 100.0 / 10000.0, 6),
            "method": "cashflow_consol_perpetual",
            "frequency": freq,
            "coupon_pct": round(cpn * 100.0, 4),
            "n_periods": None,
            "assumptions": [*assumptions, "theoretical perpetual consol"],
        }
    if years is None or (isinstance(years, float) and pd.isna(years)) or float(years) <= 0:
        return empty
    n = max(round(float(years) * freq), 1)
    y_period = y / freq
    c_period = cpn / freq
    times = [i / freq for i in range(1, n + 1)]
    cfs = [100.0 * c_period] * (n - 1) + [100.0 * c_period + 100.0]
    if abs(y_period) < 1e-12:
        price = sum(cfs)
        mac = sum(t * cf for t, cf in zip(times, cfs, strict=False)) / price if price else None
    else:
        pv_cfs = [cf / ((1.0 + y_period) ** i) for i, cf in enumerate(cfs, start=1)]
        price = sum(pv_cfs)
        mac = sum(t * pv for t, pv in zip(times, pv_cfs, strict=False)) / price if price else None
    if mac is None:
        return empty
    mod = mac / (1.0 + y_period)
    dv01 = mod * 100.0 / 10000.0
    method = "cashflow_to_first_leg_call_window" if perpetual else "cashflow_level_coupon"
    if perpetual:
        assumptions = [*assumptions, "perpetual-style: duration to first finite leg only in primary columns"]
    return {
        "macaulay_duration": round(float(mac), 4),
        "modified_duration": round(float(mod), 4),
        "dv01": round(float(dv01), 6),
        "method": method,
        "frequency": freq,
        "coupon_pct": round(cpn * 100.0, 4),
        "n_periods": n,
        "assumptions": assumptions,
    }


def _attach_rate_sensitivity(df: pd.DataFrame) -> pd.DataFrame:
    """Attach cashflow-based modified duration / DV01 under documented assumptions.

    Primary columns use cashflow level-coupon formulas. Perpetual-style rows get
    first-leg (call-window) cashflow duration in primary columns, plus a separate
    theoretical consol modified-duration column when yield is positive.
    """
    out = df.copy()
    years = pd.to_numeric(out.get(MATURITY_YEARS), errors="coerce")
    yld = pd.to_numeric(out.get(YIELD), errors="coerce")
    perpetual = (
        out[IS_PERPETUAL].fillna(False)
        if IS_PERPETUAL in out.columns
        else pd.Series(False, index=out.index)
    )

    mod_vals: list[float | None] = []
    mac_vals: list[float | None] = []
    dv01_vals: list[float | None] = []
    method_vals: list[str | None] = []
    perp_mod_vals: list[float | None] = []
    for idx in out.index:
        y_raw = yld.loc[idx] if idx in yld.index else None
        t_raw = years.loc[idx] if idx in years.index else None
        y = None if y_raw is None or pd.isna(y_raw) else float(y_raw)
        t = None if t_raw is None or pd.isna(t_raw) else float(t_raw)
        is_perp = bool(perpetual.loc[idx]) if idx in perpetual.index else False
        if is_perp:
            primary = cashflow_modified_duration(t, y, frequency=1, perpetual=True)
            consol = cashflow_modified_duration(None, y, frequency=1, perpetual=True)
            perp_mod_vals.append(consol.get("modified_duration"))
        else:
            primary = cashflow_modified_duration(t, y, frequency=1, perpetual=False)
            perp_mod_vals.append(None)
        mod_vals.append(primary.get("modified_duration"))
        mac_vals.append(primary.get("macaulay_duration"))
        dv01_vals.append(primary.get("dv01"))
        method_vals.append(primary.get("method"))

    out[MODIFIED_DURATION] = pd.Series(mod_vals, index=out.index)
    out[DV01] = pd.Series(dv01_vals, index=out.index)
    out[MACAULAY_DURATION] = pd.Series(mac_vals, index=out.index)
    out[DURATION_METHOD] = pd.Series(method_vals, index=out.index)
    out[PERPETUAL_MOD_DURATION] = pd.Series(perp_mod_vals, index=out.index)
    return out


def _extract_native_maturity_series(raw_df: pd.DataFrame) -> pd.Series:
    """Pick residual maturity from common live-feed column names."""
    for column in ("待偿期", "termToMaturity", "term_to_maturity", "剩余期限"):
        if column in raw_df.columns:
            return raw_df[column]
    return pd.Series([None] * len(raw_df), index=raw_df.index)


def _parse_maturity_part(text: str) -> float | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([YMD]?)", text.strip())
    if not match:
        return None

    amount = float(match.group(1))
    unit = match.group(2) or "Y"
    if unit == "Y":
        return amount
    if unit == "M":
        return amount / 12
    if unit == "D":
        return amount / 365
    return None


def _elapsed_years(reference_date, current_date) -> float | None:
    if not reference_date or not current_date:
        return None
    elapsed_days = max((current_date - reference_date).days, 0)
    return elapsed_days / 365


def _format_maturity(years: float) -> str:
    if years >= 1:
        return f"{years:.2f}Y"
    return f"{max(0, round(years * 365))}D"


def _maturity_coverage(df: pd.DataFrame, unmatched_limit: int = 50) -> dict:
    filled = int(df[MATURITY_YEARS].notna().sum()) if MATURITY_YEARS in df.columns else 0
    missing = int(len(df) - filled)
    source_counts = {}
    if MATURITY_SOURCE in df.columns:
        source_counts = {
            str(source): int(count)
            for source, count in df[MATURITY_SOURCE].dropna().value_counts().items()
        }

    unmatched_records: list[dict] = []
    if MATURITY_YEARS in df.columns and BOND_NAME in df.columns:
        missing_df = df[df[MATURITY_YEARS].isna()].copy()
        if not missing_df.empty:
            display_columns = [
                column
                for column in [BOND_NAME, YIELD, VOLUME, PRICE, WEIGHTED_YIELD, LIVE_CHANGE_BP, MATURITY_SOURCE]
                if column in missing_df.columns
            ]
            unmatched_records = (
                missing_df[display_columns]
                .head(unmatched_limit)
                .where(pd.notnull(missing_df[display_columns]), None)
                .to_dict(orient="records")
            )

    return {
        "filled_count": filled,
        "missing_count": missing,
        "coverage_ratio": round(filled / len(df), 4) if len(df) else 0,
        "source_counts": source_counts,
        "unmatched_count": missing,
        "unmatched_limit": unmatched_limit,
        "unmatched_records": unmatched_records,
        "enrichment_note": (
            "Native ChinaMoney residual maturity is preferred; unmatched rows still cannot join "
            "peer/maturity buckets reliably."
            if any(str(source).startswith("chinamoney") for source in source_counts)
            or any(str(source).startswith("local_static_excel") for source in source_counts)
            or (missing > 0 and filled > 0)
            else (
                "Live/snapshot residual maturity is incomplete; peer and maturity-bucket analysis stays limited."
                if missing > 0
                else None
            )
        ),
    }


def records_from_frame(df: pd.DataFrame, limit: int = 10) -> list[dict]:
    display_columns = [
        BOND_NAME,
        MATURITY,
        MATURITY_YEARS,
        MATURITY_SOURCE,
        MATURITY_RAW,
        IS_PERPETUAL,
        MATURITY_PARSE_NOTE,
        "首段期限(年)",
        "远端期限(年)",
        PRICE,
        YIELD,
        WEIGHTED_YIELD,
        VOLUME,
        LIVE_CHANGE_BP,
        MODIFIED_DURATION,
        MACAULAY_DURATION,
        DV01,
        PERPETUAL_MOD_DURATION,
        DURATION_METHOD,
    ]
    available_columns = [column for column in display_columns if column in df.columns]
    records = df[available_columns].head(limit).where(pd.notnull(df[available_columns]), None)
    return records.to_dict(orient="records")
