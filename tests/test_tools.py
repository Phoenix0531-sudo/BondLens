from bond_agent.data_loader import BOND_NAME, VOLUME, YIELD
from bond_agent.tools import (
    compare_bond_to_market,
    describe_market,
    detect_yield_outliers,
    rank_bonds,
    search_bonds,
)


def test_describe_market_returns_core_statistics():
    result = describe_market()

    assert result["tool"] == "describe_market"
    assert result["sample_count"] > 3000
    assert result["yield_summary"]["count"] > 0
    assert result["volume_summary"]["count"] > 0


def test_rank_bonds_by_yield_descending():
    result = rank_bonds(by="yield", top_n=3)
    records = result["records"]

    assert len(records) == 3
    assert result["rank_by"] == YIELD
    assert records[0][YIELD] >= records[1][YIELD] >= records[2][YIELD]


def test_rank_bonds_by_volume_descending():
    result = rank_bonds(by="volume", top_n=3)
    records = result["records"]

    assert result["rank_by"] == VOLUME
    assert records[0][VOLUME] >= records[1][VOLUME] >= records[2][VOLUME]


def test_search_bonds_by_name():
    result = search_bonds(name="23附息国债26", limit=5)

    assert result["tool"] == "search_bonds"
    assert result["match_count"] >= 1
    assert result["records"][0][BOND_NAME] == "23附息国债26"


def test_search_bonds_treats_regex_special_chars_as_plain_text():
    result = search_bonds(name="[", limit=5)

    assert result["tool"] == "search_bonds"
    assert result["criteria"]["name"] == "["
    assert result["match_count"] == 0
    assert result["records"] == []


def test_detect_yield_outliers_returns_zscore_metadata():
    result = detect_yield_outliers(top_n=5)

    assert result["tool"] == "detect_yield_outliers"
    assert result["metadata"]["method"] == "zscore"
    assert "outlier_count" in result
    if result["records"]:
        assert "outlier_score" in result["records"][0]


def test_compare_bond_to_market_returns_percentiles():
    result = compare_bond_to_market(bond_name="23附息国债26")

    assert result["tool"] == "compare_bond_to_market"
    assert result["found"] is True
    assert result["bond_name"] == "23附息国债26"
    assert 0 <= result["yield_percentile"] <= 100
    assert 0 <= result["volume_percentile"] <= 100
    assert "is_yield_outlier" in result
