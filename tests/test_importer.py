"""Import-path tests: honest mapping, degradation, duplicate-row honesty.

Fixtures are synthetic tables written to temp files — no real market data.
"""

import io
from pathlib import Path

import pandas as pd
import pytest

from app import app
from bond_agent import BondAnalystAgent
from bond_agent.data_loader import PRICE, VOLUME, YIELD
from bond_agent.importer import (
    SUPPORTED_EXTENSIONS,
    describe_import_source,
    map_columns,
    prepare_import_frame,
    read_table,
)

CSV_TEXT = (
    "债券简称,债券代码,到期收益率(%),待偿期\n"
    "23附息国债26,230026,2.35,6.5\n"
    "23附息国债26,230026,2.36,6.5\n"
    "24国开01,240205,2.20,3.2\n"
    "24进出01,240303,2.18,3.0\n"
    "24农发01,240403,2.15,1.0\n"
    "23湘电控MTN004,102382290,4.10,2.1\n"
    "24北京银行CD001,112424001,2.05,0.3\n"
)


def _write_csv(tmp_path: Path) -> Path:
    path = tmp_path / "quotes.csv"
    path.write_text(CSV_TEXT, encoding="utf-8-sig")
    return path


def _write_xlsx(tmp_path: Path) -> Path:
    path = tmp_path / "quotes.xlsx"
    pd.DataFrame(
        {
            "债券名称": ["23附息国债26", "24国开01", "24进出01", "24农发01", "23湘电控MTN004"],
            "收益率": [2.35, 2.20, 2.18, 2.15, 4.10],
            "剩余期限(年)": [6.5, 3.2, 3.0, 1.0, 2.1],
        }
    ).to_excel(path, index=False)
    return path


def test_map_columns_reports_mapping_and_unmapped(tmp_path):
    path = _write_csv(tmp_path)
    raw = read_table(path)
    renamed, mapping, unmapped = map_columns(raw)
    assert mapping["债券简称"] == "债券简称"
    assert mapping["债券代码"] == "债券代码"
    assert mapping[YIELD] == "到期收益率(%)"
    assert mapping["待偿期"] == "待偿期"
    assert set(mapping).issubset(set(renamed.columns))
    assert unmapped == []


def test_csv_import_preserves_duplicate_rows_and_reports_gap(tmp_path):
    path = _write_csv(tmp_path)
    _frame, report = prepare_import_frame(read_table(path), file_name=path.name)
    # Two rows for 23附息国债26 must survive (no dedup).
    assert report["row_count"] == 7
    assert report["bond_count"] == 6
    assert "交易量(亿元)" in report["missing_fields"]
    assert report["available_analyses"]["yield_stats"] is True
    assert report["available_analyses"]["volume_stats"] is False
    assert any("不去重" in note for note in report["notes_zh"])


def test_excel_import_with_alias_columns(tmp_path):
    path = _write_xlsx(tmp_path)
    frame, report = prepare_import_frame(read_table(path), file_name=path.name)
    mapping = report["mapped_columns"]
    assert mapping
    assert mapping["债券简称"] == "债券名称"
    assert mapping[YIELD] == "收益率"
    assert mapping["待偿期"] == "剩余期限(年)"
    assert report["row_count"] == 5
    assert report["valid_yield_count"] == 5
    # Numeric maturity is normalized to text and parsed into years.
    assert frame["待偿期(年)"].notna().all()
    assert (frame["待偿期(年)"] > 0).all()


def test_import_rejects_frame_without_bond_name(tmp_path):
    df = pd.DataFrame({"到期收益率(%)": [2.0, 2.1]})
    try:
        prepare_import_frame(df, file_name="bad.csv")
    except ValueError as exc:
        assert "债券简称" in str(exc)
    else:
        raise AssertionError("must reject frames without a bond-name column")


def test_source_profile_is_schema_compatible(tmp_path):
    path = _write_csv(tmp_path)
    frame, report = prepare_import_frame(read_table(path), file_name=path.name)
    source = describe_import_source(frame, report)
    assert source["runtime_mode"] == "import"
    assert source["provider"] == "local_upload"
    assert source["active_live_feed"] is False
    assert source["bond_count"] == 6
    assert "never committed" in source["storage"]


def test_agent_answers_over_imported_frame(tmp_path):
    """End-to-end: uploaded CSV analyzed without any LLM or live source."""
    path = _write_csv(tmp_path)
    frame, report = prepare_import_frame(read_table(path), file_name=path.name)
    source = describe_import_source(frame, report)
    agent = BondAnalystAgent(data_mode="static")
    result = agent.answer("请概览当前债券市场样本。", data_frame=frame, data_source=source)
    assert result["data_source"]["runtime_mode"] == "import"
    assert result["data_source"]["row_count"] == 7
    assert any("交易量" in str(note) for note in result["limitations"])


def test_agent_yield_analysis_disabled_without_yield_column(tmp_path):
    """Yield-less upload: yield analyses disabled honestly, search still works."""
    csv_text = "债券简称,待偿期\n23附息国债26,6.5\n24国开01,3.2\n"
    path = tmp_path / "no_yield.csv"
    path.write_text(csv_text, encoding="utf-8-sig")
    frame, report = prepare_import_frame(read_table(path), file_name=path.name)
    source = describe_import_source(frame, report)
    agent = BondAnalystAgent(data_mode="static")
    result = agent.answer("找出收益率异常偏离的债券", data_frame=frame, data_source=source)
    assert result["data_source"]["available_analyses"]["yield_stats"] is False
    assert any("已禁用" in note for note in result["limitations"])
    assert "detect_yield_outliers" not in result["plan"]["requested_tools"]


# --- HTTP API wiring (upload → query/stream by import_id) ---


def _upload(client, filename="quotes.csv"):
    resp = client.post(
        "/api/import",
        data={"file": (io.BytesIO(CSV_TEXT.encode("utf-8-sig")), filename)},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _client():
    app.config["TESTING"] = True
    return app.test_client()


def test_query_with_valid_import_id_runs_in_import_mode():
    client = _client()
    payload = _upload(client)
    resp = client.post(
        "/api/agent/query",
        json={
            "question": "请概览当前样本。",
            "import_id": payload["import_id"],
            "data_mode": "static",
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["data_source"]["runtime_mode"] == "import"
    assert body["data_source"]["row_count"] == 7


def test_stream_with_valid_import_id_emits_final():
    client = _client()
    payload = _upload(client)
    resp = client.post(
        "/api/agent/stream",
        json={
            "question": "请概览当前样本。",
            "import_id": payload["import_id"],
            "data_mode": "static",
        },
    )
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "event: error" not in text
    assert "event: final" in text
    assert '"runtime_mode": "import"' in text or '"runtime_mode":"import"' in text


def test_expired_import_id_returns_400():
    client = _client()
    for endpoint in ("/api/agent/query", "/api/agent/stream"):
        resp = client.post(
            endpoint,
            json={"question": "请概览当前样本。", "import_id": "deadbeef-cafe", "data_mode": "static"},
        )
        assert resp.status_code == 400
        assert "重新上传" in resp.get_json()["error"]


def test_query_without_import_id_keeps_builtin_data_mode():
    client = _client()
    resp = client.post(
        "/api/agent/query",
        json={"question": "请概览当前样本。", "data_mode": "static"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data_source"]["runtime_mode"] != "import"


# --- Caliber honesty: 全价/成交金额 are never auto-mapped ---

CALIBER_CSV = (
    "债券简称,债券代码,到期收益率(%),待偿期,行情时间,全价,成交金额\n"
    "23附息国债26,230026,2.35,6.5,2024-03-22,101.5000,125000\n"
    "24国开01,240205,2.20,3.2,2024-03-22,100.8000,88000\n"
    "24进出01,240303,2.18,3.0,2024-03-22,100.2000,76000\n"
)


def _read_csv_text(text: str) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(text.encode("utf-8-sig")))


def test_dirty_price_and_amount_are_not_auto_mapped():
    """全价≠净价、成交金额≠交易量：不自动映射，保留原列并明确报告。"""
    frame, report = prepare_import_frame(
        _read_csv_text(CALIBER_CSV), file_name="caliber.csv"
    )
    assert "全价" in report["unmapped_columns"]
    assert "成交金额" in report["unmapped_columns"]
    assert "全价" in frame.columns and "成交金额" in frame.columns  # kept verbatim
    assert "全价" in report["unsupported_columns"]
    assert "成交金额" in report["unsupported_columns"]
    assert any("口径不同" in note for note in report["notes_zh"])
    assert "收盘净价(元)" in report["missing_fields"]
    assert "交易量(亿元)" in report["missing_fields"]
    assert report["available_analyses"]["volume_stats"] is False


def test_forbidden_caliber_overrides_are_rejected():
    """已知口径不兼容的映射（全价→净价、成交金额→交易量）必须拒绝，不静默转换。"""
    raw = _read_csv_text(CALIBER_CSV)
    with pytest.raises(ValueError, match="全价"):
        prepare_import_frame(raw, file_name="x.csv", column_overrides={PRICE: "全价"})
    with pytest.raises(ValueError, match="成交金额"):
        prepare_import_frame(raw, file_name="x.csv", column_overrides={VOLUME: "成交金额"})
    with pytest.raises(ValueError, match="Refusing"):
        raw_dirty_named = _read_csv_text(CALIBER_CSV).rename(columns={"全价": "收盘全价"})
        prepare_import_frame(raw_dirty_named, file_name="x.csv", column_overrides={PRICE: "收盘全价"})

    # 拒绝信息必须中英双语，解释原因与替代方案。
    with pytest.raises(ValueError) as exc_info:
        prepare_import_frame(raw, file_name="x.csv", column_overrides={PRICE: "全价"})
    message = str(exc_info.value)
    assert "应计利息" in message and "accrued interest" in message


def test_unknown_column_override_still_maps_after_user_confirmation():
    """未知列（非已知不兼容口径）仍可由用户显式映射。"""
    raw = _read_csv_text(CALIBER_CSV)
    raw = raw.rename(columns={"全价": "最新价格"})
    frame, report = prepare_import_frame(
        raw,
        file_name="caliber.csv",
        column_overrides={PRICE: "最新价格"},
    )
    assert report["mapped_columns"][PRICE] == "最新价格"
    assert PRICE in frame.columns
    assert report["column_overrides"] == {PRICE: "最新价格"}


def test_column_overrides_are_validated():
    raw = _read_csv_text(CALIBER_CSV)
    with pytest.raises(ValueError, match="未知的目标字段"):
        prepare_import_frame(raw, file_name="x.csv", column_overrides={"不存在字段": "全价"})
    with pytest.raises(ValueError, match="不存在的列"):
        prepare_import_frame(raw, file_name="x.csv", column_overrides={PRICE: "无此列"})


def test_quote_date_is_derived_when_present_and_unknown_otherwise():
    frame, report = prepare_import_frame(
        _read_csv_text(CALIBER_CSV), file_name="dated.csv"
    )
    assert report["sample_date"] == "2024-03-22"
    source = describe_import_source(frame, report)
    assert source["sample_date"] == "2024-03-22"
    assert "日期未知" not in source["sample_date_note_zh"]

    frame, report = prepare_import_frame(
        _read_csv_text(CSV_TEXT), file_name="undated.csv"
    )
    assert report["sample_date"] is None
    assert any("样本日期未知" in note for note in report["notes_zh"])
    source = describe_import_source(frame, report)
    assert source["sample_date"] is None
    assert "样本日期未知" in source["sample_date_note_zh"]


def test_multi_day_sample_is_flagged_as_mixed_timepoints():
    """跨多天样本必须标注日期范围，且不得当作单日截面。"""
    csv_text = (
        "债券简称,到期收益率(%),数据日期\n"
        "A债,2.10,2024-03-20\n"
        "B债,2.20,2024-03-21\n"
        "C债,2.30,2024-03-22\n"
    )
    frame, report = prepare_import_frame(_read_csv_text(csv_text), file_name="multi.csv")
    assert report["mixed_timepoints"] is True
    assert report["quote_date_range"] == "2024-03-20 ~ 2024-03-22"
    assert report["sample_date"] is None
    assert any("勿解读为单日截面" in note for note in report["notes_zh"])
    source = describe_import_source(frame, report)
    assert source["mixed_timepoints"] is True
    assert source["quote_date_range"] == "2024-03-20 ~ 2024-03-22"
    assert "混合时点" in source["sample_date_note_zh"]
    assert "mixed-timepoint" in source["sample_date_note_en"]


def test_bond_count_distinguishes_same_name_different_code():
    """同名不同码是不同债券；同名同码的多行记录才计入重复行。"""
    csv_text = (
        "债券简称,债券代码,到期收益率(%)\n"
        "23附息国债26,230026,2.35\n"
        "23附息国债26,230026,2.36\n"      # same bond, repeated row
        "23附息国债26,230027,2.40\n"      # same name, DIFFERENT code -> another bond
    )
    _frame, report = prepare_import_frame(
        _read_csv_text(csv_text), file_name="pairs.csv"
    )
    assert report["row_count"] == 3
    assert report["bond_count"] == 2
    assert any("1 条同一债券" in note for note in report["notes_zh"])


def test_xls_support_is_backed_by_a_real_installed_reader():
    """宣称支持 .xls 必须有真实可用的读取器（xlrd），否则这里应失败。"""
    from importlib.util import find_spec

    assert ".xls" in SUPPORTED_EXTENSIONS
    assert find_spec("xlrd") is not None, "xlrd missing: .xls claim would be dishonest"
    assert pd.io.excel._base.get_default_engine("xls") == "xlrd"
