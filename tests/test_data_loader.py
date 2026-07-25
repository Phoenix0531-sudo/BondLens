import pandas as pd

from bond_agent.data_loader import (
    BOND_NAME,
    LIVE_CHANGE_BP,
    MATURITY,
    MATURITY_SOURCE,
    MATURITY_YEARS,
    PRICE,
    VOLUME,
    WEIGHTED_YIELD,
    YIELD,
    describe_data_source,
    load_bond_data,
    load_live_bond_data,
    load_live_snapshot,
    parse_maturity_to_years,
    resolve_bond_data,
)


def test_load_bond_data_reads_project_excel():
    df = load_bond_data()

    assert len(df) > 3000
    assert BOND_NAME in df.columns
    assert YIELD in df.columns
    assert MATURITY_YEARS in df.columns
    assert df[YIELD].notna().sum() > 0


def test_parse_maturity_to_years_handles_years_and_days():
    assert parse_maturity_to_years("9.68Y") == 9.68
    assert round(parse_maturity_to_years("364D"), 4) == round(364 / 365, 4)
    assert round(parse_maturity_to_years("177D+2Y"), 4) == round(177 / 365 + 2, 4)
    assert parse_maturity_to_years("bad-data") is None


def test_describe_data_source_marks_static_excel_and_legacy_crawler():
    profile = describe_data_source()

    assert profile["source_id"] == "local_static_excel"
    assert profile["source_name"] == "data/testdata.xlsx"
    assert profile["runtime_mode"] == "static_sample"
    assert profile["row_count"] > 3000
    assert profile["active_live_feed"] is False
    assert profile["legacy_crawler"]["status"] == "preserved_in_undergraduate_thesis_branch"


def test_load_live_bond_data_normalizes_akshare_columns(tmp_path):
    def fake_fetcher():
        return pd.DataFrame(
            {
                "债券简称": ["25国开20", "26超长特别国债02"],
                "成交净价": [101.23, 98.76],
                "最新收益率": [2.12, 2.65],
                "涨跌": [-0.3, 1.2],
                "加权收益率": [2.11, 2.66],
                "交易量": [4.5, 8.0],
            }
        )

    cache_path = tmp_path / "live_snapshot.csv"
    df = load_live_bond_data(fetcher=fake_fetcher, cache_path=cache_path)

    assert list(df[BOND_NAME]) == ["25国开20", "26超长特别国债02"]
    assert df[PRICE].tolist() == [101.23, 98.76]
    assert df[YIELD].tolist() == [2.12, 2.65]
    assert df[WEIGHTED_YIELD].tolist() == [2.11, 2.66]
    assert df[VOLUME].tolist() == [4.5, 8.0]
    assert df[LIVE_CHANGE_BP].tolist() == [-0.3, 1.2]
    assert MATURITY_YEARS in df.columns
    assert MATURITY_SOURCE in df.columns
    assert cache_path.exists()

    cached_df, snapshot = load_live_snapshot(cache_path=cache_path)

    assert list(cached_df[BOND_NAME]) == ["25国开20", "26超长特别国债02"]
    assert snapshot["snapshot_path"] == cache_path


def test_load_live_bond_data_enriches_matching_maturity_from_static_sample(tmp_path):
    def fake_fetcher():
        return pd.DataFrame(
            {
                "债券简称": ["23附息国债26"],
                "成交净价": [107.51],
                "最新收益率": [1.6025],
                "涨跌": [-0.5],
                "加权收益率": [1.608],
                "交易量": [23.1214],
            }
        )

    df = load_live_bond_data(fetcher=fake_fetcher, cache_path=tmp_path / "live_snapshot.csv")

    assert df.iloc[0][BOND_NAME] == "23附息国债26"
    assert pd.notna(df.iloc[0][MATURITY])
    assert pd.notna(df.iloc[0][MATURITY_YEARS])
    assert str(df.iloc[0][MATURITY_SOURCE]).startswith("local_static_excel_adjusted_from_")


def test_maturity_coverage_lists_unmatched_records(tmp_path):
    def fake_fetcher():
        return pd.DataFrame(
            {
                "债券简称": ["23附息国债26", "不存在的测试债XYZ"],
                "成交净价": [107.51, 100.0],
                "最新收益率": [1.6025, 4.2],
                "涨跌": [-0.5, 0.1],
                "加权收益率": [1.608, 4.1],
                "交易量": [23.1214, 0.5],
            }
        )

    _df, profile = resolve_bond_data(
        mode="live",
        live_fetcher=fake_fetcher,
        live_cache_path=tmp_path / "live_snapshot.csv",
    )
    coverage = profile["maturity_coverage"]

    assert coverage["filled_count"] >= 1
    assert coverage["missing_count"] >= 1
    assert coverage["unmatched_count"] == coverage["missing_count"]
    names = {item.get(BOND_NAME) for item in coverage.get("unmatched_records") or []}
    assert "不存在的测试债XYZ" in names


def test_resolve_bond_data_uses_live_profile_when_available(tmp_path):
    def fake_fetcher():
        return pd.DataFrame(
            {
                "债券简称": ["25国开20"],
                "成交净价": [101.23],
                "最新收益率": [2.12],
                "涨跌": [-0.3],
                "加权收益率": [2.11],
                "交易量": [4.5],
            }
        )

    cache_path = tmp_path / "live_snapshot.csv"
    df, profile = resolve_bond_data(mode="live", live_fetcher=fake_fetcher, live_cache_path=cache_path)

    assert df.iloc[0][BOND_NAME] == "25国开20"
    assert profile["source_id"] == "akshare_bond_spot_deal"
    assert profile["runtime_mode"] == "live"
    assert profile["requested_mode"] == "live"
    assert profile["active_live_feed"] is True
    assert profile["active_live_snapshot"] is False
    assert profile["fetched_at"]
    assert cache_path.exists()


def test_resolve_bond_data_uses_cached_live_snapshot_before_static_sample(tmp_path):
    cache_path = tmp_path / "live_snapshot.csv"

    def fake_fetcher():
        return pd.DataFrame(
            {
                "债券简称": ["25国开20", "26超长特别国债02"],
                "成交净价": [101.23, 98.76],
                "最新收益率": [2.12, 2.65],
                "涨跌": [-0.3, 1.2],
                "加权收益率": [2.11, 2.66],
                "交易量": [4.5, 8.0],
            }
        )

    resolve_bond_data(mode="live", live_fetcher=fake_fetcher, live_cache_path=cache_path)

    def failing_fetcher():
        raise RuntimeError("network down")

    df, profile = resolve_bond_data(mode="auto", live_fetcher=failing_fetcher, live_cache_path=cache_path)

    assert list(df[BOND_NAME]) == ["25国开20", "26超长特别国债02"]
    assert profile["source_id"] == "akshare_bond_spot_deal_snapshot"
    assert profile["runtime_mode"] == "live_snapshot"
    assert profile["requested_mode"] == "auto"
    assert profile["active_live_feed"] is False
    assert profile["active_live_snapshot"] is True
    assert profile["row_count"] == 2
    assert "network down" in profile["fallback_reason"]


def test_resolve_bond_data_falls_back_to_static_sample_when_live_and_snapshot_fail(tmp_path):
    def failing_fetcher():
        raise RuntimeError("network down")

    df, profile = resolve_bond_data(
        mode="auto",
        live_fetcher=failing_fetcher,
        live_cache_path=tmp_path / "missing_snapshot.csv",
    )

    assert len(df) > 3000
    assert profile["source_id"] == "local_static_excel"
    assert profile["runtime_mode"] == "static_fallback"
    assert profile["requested_mode"] == "auto"
    assert profile["active_live_feed"] is False
    assert profile["active_live_snapshot"] is False
    assert "network down" in profile["fallback_reason"]
    assert "snapshot fallback failed" in profile["fallback_reason"]


def test_resolve_bond_data_times_out_slow_live_fetch_and_uses_snapshot(tmp_path, monkeypatch):
    import time

    cache_path = tmp_path / "live_snapshot.csv"

    def fast_fetcher():
        return pd.DataFrame(
            {
                "债券简称": ["25国开20"],
                "成交净价": [101.23],
                "最新收益率": [2.12],
                "涨跌": [-0.3],
                "加权收益率": [2.11],
                "交易量": [4.5],
            }
        )

    resolve_bond_data(mode="live", live_fetcher=fast_fetcher, live_cache_path=cache_path)

    def slow_fetcher():
        time.sleep(2.0)
        return fast_fetcher()

    monkeypatch.setenv("BOND_LIVE_FETCH_TIMEOUT_SECONDS", "0.2")
    df, profile = resolve_bond_data(mode="auto", live_fetcher=slow_fetcher, live_cache_path=cache_path)

    assert list(df[BOND_NAME]) == ["25国开20"]
    assert profile["runtime_mode"] == "live_snapshot"
    assert "timed out" in (profile.get("fallback_reason") or "").lower()
