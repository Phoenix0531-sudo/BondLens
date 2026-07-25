from bond_agent.llm_guardrail import assess_llm_faithfulness


def _report():
    return {
        "data_evidence": {
            "market": {
                "yield_summary": {"mean": 2.77, "median": 2.45},
                "yield_distribution": {"(0.0814, 11.515]": 3089},
            }
        }
    }


def test_llm_guardrail_accepts_supported_numbers_and_safe_language():
    result = assess_llm_faithfulness("样本收益率中位数为 2.45%。非投资建议，仅用于学习和研究。", _report())

    assert result["status"] == "passed"
    assert result["numeric_status"] == "passed"
    assert result["language_status"] == "passed"
    assert result["unsupported_numbers"] == []
    assert result["unsafe_phrases"] == []


def test_llm_guardrail_rejects_unsupported_numbers():
    result = assess_llm_faithfulness("样本中 99% 的收益率都在合理范围。", _report())

    assert result["status"] == "failed"
    assert result["numeric_status"] == "failed"
    assert any(item["text"] == "99%" for item in result["unsupported_numbers"])


def test_llm_guardrail_rejects_investment_advice_language():
    result = assess_llm_faithfulness("建议买入这只债券，收益看起来非常安全。", _report())

    assert result["status"] == "failed"
    assert result["language_status"] == "failed"
    assert {item["rule_id"] for item in result["unsafe_phrases"]} == {"buy_recommendation", "risk_free_claim"}


def test_llm_guardrail_accepts_quantile_label_tokens():
    report = {
        "data_evidence": {
            "market": {
                "yield_summary": {
                    "mean": 2.7709,
                    "median": 2.45,
                    "p25": 2.255,
                    "p75": 2.7468,
                    "count": 3102,
                },
                "sample_count": 3365,
            }
        }
    }
    result = assess_llm_faithfulness(
        "均值 2.7709，中位数 2.45，25分位 2.255，75分位 2.7468。样本 3365。",
        report,
    )
    assert result["status"] == "passed"
    assert result["numeric_status"] == "passed"
    assert result["unsupported_numbers"] == []


def test_llm_guardrail_still_rejects_bare_25_percent_claim():
    report = {
        "data_evidence": {
            "market": {
                "yield_summary": {"mean": 2.77, "median": 2.45, "p25": 2.255, "p75": 2.7468},
            }
        }
    }
    result = assess_llm_faithfulness("样本中 25% 的债券收益异常。", report)
    assert result["status"] == "failed"
    assert result["numeric_status"] == "failed"
    assert any(item["text"] == "25%" for item in result["unsupported_numbers"])


def test_llm_guardrail_accepts_quality_note_percent_and_cn_yield_field():
    """Dual: audited quality % and CN yield columns must pass; invented % must fail."""
    report = {
        "data_evidence": {
            "market": {
                "sample_count": 3365,
                "yield_summary": {"mean": 2.7709, "median": 2.45, "p25": 2.255, "p75": 2.7468, "count": 3102},
                "data_quality": {
                    "score": 84,
                    "missing_yield_count": 263,
                    "issues": [
                        {
                            "id": "missing_yield",
                            "severity": "medium",
                            "message_zh": "收益率缺失 263/3365（7.8%）。",
                            "message_en": "Missing yield on 263/3365 (7.8%).",
                        },
                        {
                            "id": "missing_maturity",
                            "severity": "low",
                            "message_zh": "期限缺失 248/3365（7.4%），分桶与同业可比会变弱。",
                            "message_en": "Missing maturity on 248/3365 (7.4%); peer buckets weaken.",
                        },
                    ],
                },
            },
            "search": {
                "match_count": 1,
                "records": [
                    {
                        "债券简称": "06国开24",
                        "收盘到期收益率(%)": 2.5647,
                        "加权收益率(%)": 2.5644,
                        "待偿期": "12.61Y",
                        "交易量(亿元)": 3.8,
                    }
                ],
            },
        }
    }
    ok = assess_llm_faithfulness(
        "样本 3365 只，缺失收益率 7.8%。第一只债 06国开24 收盘到期收益率 2.5647%。非投资建议，仅用于学习和研究。",
        report,
    )
    assert ok["status"] == "passed"
    assert ok["numeric_status"] == "passed"
    assert ok["unsupported_numbers"] == []

    bad = assess_llm_faithfulness(
        "样本中 12.3% 的债券属于高收益异常区。",
        report,
    )
    assert bad["status"] == "failed"
    assert bad["numeric_status"] == "failed"
    assert any(item["text"] == "12.3%" for item in bad["unsupported_numbers"])


def test_llm_guardrail_accepts_unicode_minus_peer_spread():
    """Models often emit U+2212 minus; bare 5.95 must not be extracted as positive."""
    report = {
        "data_evidence": {
            "comparison": {
                "peer_comparison": {
                    "spread_vs_peer_mean_bp": -5.95,
                    "peer_yield_percentile": 28.57,
                }
            }
        }
    }
    unicode_minus = "\u2212"  # −
    text = f"spread vs peer mean **{unicode_minus}5.95 bp**; peer yield percentile **28.57%**."
    result = assess_llm_faithfulness(text, report)
    assert result["status"] == "passed"
    assert result["numeric_status"] == "passed"
    assert result["unsupported_numbers"] == []


def test_llm_guardrail_negated_risk_free_is_safe_but_positive_claim_fails():
    report = _report()
    ok = assess_llm_faithfulness(
        "This bond is not risk-free. There is no risk-free conclusion. Avoid risk-free claims.",
        report,
    )
    assert ok["language_status"] == "passed"
    assert ok["unsafe_phrases"] == []

    bad = assess_llm_faithfulness("The bond is risk-free with guaranteed return.", report)
    assert bad["language_status"] == "failed"
    rule_ids = {item["rule_id"] for item in bad["unsafe_phrases"]}
    assert "english_guarantee" in rule_ids
