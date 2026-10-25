"""Phase 1 regression tests: peer contract, code search, grouped outliers.

All fixtures are minimal synthetic tables — no third-party market data.
"""

import pandas as pd

from bond_agent.data_loader import YIELD
from bond_agent.taxonomy import classify_bond_type, maturity_bucket
from bond_agent.tools import (
    OUTLIER_MIN_GROUP_SIZE,
    PEER_MIN_SAMPLE,
    compare_bond_to_market,
    detect_yield_outliers,
    search_bonds,
)


def _base_frame(extra: list[dict] | None = None) -> pd.DataFrame:
    rows = [
        {YIELD: 2.30, "债券简称": "23附息国债26", "待偿期(年)": 6.5},
        {YIELD: 2.32, "债券简称": "23附息国债09", "待偿期(年)": 5.8},
        {YIELD: 2.28, "债券简称": "22附息国债10", "待偿期(年)": 7.1},
        {YIELD: 2.31, "债券简称": "23附息国债20", "待偿期(年)": 6.9},
        {YIELD: 2.29, "债券简称": "24附息国债02", "待偿期(年)": 8.0},
        {YIELD: 2.25, "债券简称": "23国开10", "待偿期(年)": 6.4},
        {YIELD: 2.24, "债券简称": "23进出05", "待偿期(年)": 6.2},
        {YIELD: 2.26, "债券简称": "23农发30", "待偿期(年)": 6.6},
    ]
    rows.extend(extra or [])
    return pd.DataFrame(rows)


def test_peers_exclude_target_and_stay_same_type():
    df = _base_frame()
    result = compare_bond_to_market(bond_name="23附息国债26", data_frame=df)
    assert result["found"] is True
    peer = result["peer_comparison"]
    assert peer["peer_count"] >= 1
    assert peer["peer_status"] in {"ok", "small_sample"}
    # Same type, same maturity bucket as the target, target excluded.
    assert peer["bond_type"] == "国债"
    assert peer["maturity_bucket"] == maturity_bucket(6.5)
    assert "23附息国债26" not in {row["债券简称"] for row in peer.get("records", [])}


def test_no_cross_type_fallback_when_peers_insufficient():
    """Only one other 国债 in the same bucket -> insufficient, never borrow 金融债."""
    df = _base_frame(
        extra=[
            {YIELD: 2.20, "债券简称": "24国开01", "待偿期(年)": 6.0},
            {YIELD: 2.21, "债券简称": "24进出01", "待偿期(年)": 6.1},
        ]
    )
    df = df[df["债券简称"] != "23附息国债09"]
    df = df[df["债券简称"] != "22附息国债10"]
    df = df[df["债券简称"] != "23附息国债20"]
    df = df[df["债券简称"] != "24附息国债02"]
    result = compare_bond_to_market(bond_name="23附息国债26", data_frame=df)
    peer = result["peer_comparison"]
    # Remaining same-bucket peers are 政策性金融债, which must NOT be borrowed.
    assert peer["peer_count"] == 0
    assert peer["peer_status"] == "insufficient"
    assert "跨券种伪同业比较" in peer["note_zh"]


def test_small_peer_sample_gets_honest_status():
    """2 same-type peers (< PEER_MIN_SAMPLE) -> small_sample caveat, not ok."""
    assert PEER_MIN_SAMPLE == 5
    keep = {"23附息国债26", "23附息国债09", "22附息国债10"}
    df = _base_frame()
    df = df[df["债券简称"].isin(keep)].reset_index(drop=True)
    result = compare_bond_to_market(bond_name="23附息国债26", data_frame=df)
    peer = result["peer_comparison"]
    assert peer["peer_count"] == 2
    assert peer["peer_status"] == "small_sample"
    assert "样本偏小" in peer["note_zh"]


def test_search_by_code_exact_match():
    df = _base_frame()
    df["债券代码"] = ["230026", "230009", "220010", "230020", "240002", "230210", "230305", "230410"]
    result = search_bonds(code="230026", data_frame=df)
    assert result["records"], "exact code must hit"
    assert result["matched_by"] and "code_exact" in result["matched_by"]
    assert result["records"][0]["债券简称"] == "23附息国债26"


def test_search_by_code_missing_column_degrades_honestly():
    """Static-style frame without a code column reports the gap, no fake hits."""
    df = _base_frame()
    result = search_bonds(code="230026", data_frame=df)
    assert result["matched_by"] == ["code_column_missing"]
    assert result["records"] == []


def test_search_by_name_reports_match_mode():
    df = _base_frame()
    exact = search_bonds(name="23附息国债26", data_frame=df)
    assert exact["records"]
    assert "name_exact" in exact["matched_by"]
    vague = search_bonds(name="国债", data_frame=df)
    assert vague["distinct_bond_count"] >= 2
    assert "name_contains" in vague["matched_by"]
    assert vague.get("ambiguity_note")


def test_grouped_outliers_metadata_and_small_groups():
    df = _base_frame()
    result = detect_yield_outliers(data_frame=df)
    meta = result["metadata"]
    assert meta["grouping"] == "券种+期限分桶"
    assert meta["min_group_size"] == OUTLIER_MIN_GROUP_SIZE
    assert meta["groups_evaluated"] >= 0
    assert isinstance(meta["small_groups_skipped"], list)
    # Synthetic groups are tiny (< 5), so nothing may be flagged.
    assert result["records"] == []
    assert result["outlier_count"] == 0


def test_grouped_outlier_detects_within_group_only():
    """An extreme yield must be flagged only against its own type/bucket group.

    Uses the IQR method: within tiny groups a z-score can never reach 3.0
    (max |z| = (n-1)/sqrt(n)), so zscore would be unflaggable by construction.
    """
    rows = []
    for index in range(OUTLIER_MIN_GROUP_SIZE):
        rows.append({YIELD: 2.30 + 0.01 * index, "债券简称": f"23附息国债{index:02d}", "待偿期(年)": 6.5})
    rows.append({YIELD: 9.99, "债券简称": "23附息国债99", "待偿期(年)": 6.5})
    # Same bucket, different type: must not pollute the 国债 group statistics.
    for index in range(OUTLIER_MIN_GROUP_SIZE):
        rows.append({YIELD: 2.25, "债券简称": f"23国开{index:02d}", "待偿期(年)": 6.5})
    df = pd.DataFrame(rows)
    result = detect_yield_outliers(method="iqr", data_frame=df)
    flagged = {row["债券简称"] for row in result["records"]}
    assert "23附息国债99" in flagged
    for row in result["records"]:
        assert classify_bond_type(row["债券简称"]) != "待分类"
    top = result["records"][0]
    assert top["outlier_score"] > 3
    assert "偏离(BP)" in top and "组内样本数" in top


def test_grouped_outliers_skip_unreliable_types():
    """待分类 rows must never enter any group or be flagged."""
    rows = []
    for index in range(OUTLIER_MIN_GROUP_SIZE + 2):
        rows.append({YIELD: 8.0 if index == 0 else 2.0, "债券简称": f"17松滋金投债{index:02d}", "待偿期(年)": 3.0})
    df = pd.DataFrame(rows)
    assert classify_bond_type("17松滋金投债00") == "待分类"
    result = detect_yield_outliers(data_frame=df)
    assert result["records"] == []
    assert result["metadata"]["skipped_unreliable_type_count"] >= 1
