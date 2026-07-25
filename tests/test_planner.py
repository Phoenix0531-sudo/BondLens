import pandas as pd

from bond_agent.data_loader import BOND_NAME, MATURITY, PRICE, VOLUME, WEIGHTED_YIELD, YIELD
from bond_agent.planner import classify_intent


def test_planner_market_overview_uses_market_tools():
    plan = classify_intent("当前样本收益率分布是什么样？")

    assert plan["intent"] == "market_overview"
    assert "describe_market" in plan["requested_tools"]
    assert "build_market_monitor" in plan["requested_tools"]
    assert plan["rank_by"] is None


def test_planner_bond_report_for_concrete_bond_analysis():
    plan = classify_intent("搜索23附息国债26并给出收益率分析")

    assert plan["intent"] == "bond_report"
    assert "search_bonds" in plan["requested_tools"]
    assert "compare_bond_to_market" in plan["requested_tools"]
    assert "generate_bond_report" in plan["requested_tools"]
    assert plan["search_params"]["name"] == "23附息国债26"


def test_planner_ranking_by_volume():
    plan = classify_intent("按成交量列出最活跃的前5只债券")

    assert plan["intent"] == "ranking"
    assert plan["requested_tools"] == ["describe_market", "rank_bonds"]
    assert plan["rank_by"] == "volume"


def test_planner_outlier_detection():
    plan = classify_intent("有没有收益率异常的债券？")

    assert plan["intent"] == "outlier_detection"
    assert plan["requested_tools"] == ["describe_market", "detect_yield_outliers"]


def test_planner_advisory_refusal():
    plan = classify_intent("今天该不该买债？")

    assert plan["intent"] == "advisory_refusal"
    assert plan["requested_tools"] == ["describe_market"]
    assert plan["flags"]["wants_advisory"] is True


def test_planner_composite_monitor():
    plan = classify_intent("打开今日市场监控面板：高收益、低成交与异常")

    assert plan["intent"] in {"market_monitor", "composite_market"}
    assert "build_market_monitor" in plan["requested_tools"]
    assert "describe_market" in plan["requested_tools"]
    assert plan["flags"]["wants_monitor"] is True


def test_planner_composite_ranking_and_outliers():
    plan = classify_intent("按收益率排名前5并指出异常样本")

    assert plan["intent"] in {"composite_market", "market_monitor", "bond_report"}
    assert "rank_bonds" in plan["requested_tools"]
    assert "detect_yield_outliers" in plan["requested_tools"]
    assert plan["flags"]["composite"] is True


def test_planner_bond_type_filter():
    plan = classify_intent("筛选国债收益率大于 2.5 的债券")

    assert plan["search_params"].get("bond_type") == "国债"
    assert plan["search_params"].get("min_yield") == 2.5


def test_planner_uses_custom_data_path_for_bond_name_lookup(tmp_path):
    data_path = tmp_path / "custom_bonds.xlsx"
    df = pd.DataFrame(
        [
            {
                BOND_NAME: "99测试债01",
                MATURITY: "1Y",
                PRICE: 100.0,
                YIELD: 3.1,
                WEIGHTED_YIELD: 3.0,
                VOLUME: 1.2,
            }
        ]
    )
    df.to_excel(data_path, index=False, startrow=1)

    plan = classify_intent("搜索99测试债01并给出收益率分析", data_path=str(data_path))

    assert plan["intent"] == "bond_report"
    assert plan["search_params"]["name"] == "99测试债01"
