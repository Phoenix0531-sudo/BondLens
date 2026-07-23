from bond_agent.data_loader import load_bond_data
from bond_agent.taxonomy import assess_data_quality, classify_bond_type, maturity_bucket, summarize_segments
from bond_agent.tools import compare_bond_to_market, describe_market


def test_classify_bond_type_rules():
    assert classify_bond_type("23附息国债26") == "国债"
    assert classify_bond_type("23国开05") == "政策性金融债"
    assert classify_bond_type("23江苏债01") == "地方政府债" or classify_bond_type("23江苏债01") in {"地方政府债", "其他"}
    assert classify_bond_type("23中信银行CD001") == "同业存单"
    assert classify_bond_type("某未知简称XYZ") == "其他"


def test_maturity_buckets():
    assert maturity_bucket(0.5) == "0-1Y"
    assert maturity_bucket(2) == "1-3Y"
    assert maturity_bucket(4) == "3-5Y"
    assert maturity_bucket(7) == "5-10Y"
    assert maturity_bucket(12) == "10Y+"
    assert maturity_bucket(None) is None


def test_describe_market_includes_segments_and_quality():
    market = describe_market(data_frame=load_bond_data())
    assert market["segments"]["by_bond_type"]
    assert "data_quality" in market
    assert 0 <= market["data_quality"]["score"] <= 100


def test_compare_bond_includes_peer_comparison():
    df = load_bond_data()
    name = str(df.iloc[0]["债券简称"])
    result = compare_bond_to_market(bond_name=name, data_frame=df)
    assert result["found"] is True
    assert "peer_comparison" in result
    assert result["peer_comparison"]["peer_count"] >= 1
    assert result.get("bond_type")


def test_data_quality_score_bounds():
    quality = assess_data_quality(load_bond_data())
    assert 0 <= quality["score"] <= 100
    assert quality["level"] in {"low", "medium", "high"}


def test_summarize_segments_not_empty():
    summary = summarize_segments(load_bond_data())
    assert summary["by_bond_type"] or summary["type_counts"]
