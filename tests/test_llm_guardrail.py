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


def test_llm_guardrail_accepts_bare_peer_spread_magnitude_but_rejects_invented_spread():
    """Dual: absolute peer-spread magnitude OK when evidence has ±value; invented magnitude fails."""
    report = {
        "data_evidence": {
            "comparison": {
                "peer_comparison": {
                    "spread_vs_peer_mean_bp": -5.95,
                    "peer_yield_zscore": -0.42,
                    "peer_yield_percentile": 28.57,
                }
            }
        }
    }
    ok = assess_llm_faithfulness(
        "Absolute peer-mean spread is 5.95 bp (below peers). Percentile 28.57%. Not investment advice.",
        report,
    )
    assert ok["status"] == "passed"
    assert ok["numeric_status"] == "passed"
    assert ok["unsupported_numbers"] == []

    signed_ok = assess_llm_faithfulness(
        "spread vs peer mean -5.95 bp; z-score -0.42.",
        report,
    )
    assert signed_ok["status"] == "passed"

    bad = assess_llm_faithfulness(
        "Absolute peer-mean spread is 9.99 bp.",
        report,
    )
    assert bad["status"] == "failed"
    assert bad["numeric_status"] == "failed"
    assert any(item["text"] == "9.99" for item in bad["unsupported_numbers"])


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

def test_llm_guardrail_accepts_coverage_ratio_percent_and_iso_date():
    """Dual: 0–1 coverage_ratio may be cited as 99.94%; inventing 88.8% fails. ISO dates ignored."""
    report = {
        "data_source": {
            "maturity_coverage": {
                "filled_count": 3363,
                "missing_count": 2,
                "coverage_ratio": 0.9994,
                "coverage_percent": 99.94,
            }
        },
        "data_evidence": {
            "market": {
                "sample_count": 3365,
                "yield_summary": {"mean": 2.7709, "median": 2.45, "count": 3102},
                "volume_summary": {"mean": 4.934, "median": 1.0},
            }
        },
    }
    ok = assess_llm_faithfulness(
        "覆盖率 99.94%（3363/3365）。成交量均值 4.934 亿元。样本 3365。Report date 2026-07-26 is context only.",
        report,
    )
    assert ok["status"] == "passed"
    assert ok["numeric_status"] == "passed"
    assert ok["unsupported_numbers"] == []

    # Ratio form also OK
    ok_ratio = assess_llm_faithfulness("maturity coverage_ratio=0.9994. sample 3365. mean 2.7709%.", report)
    assert ok_ratio["status"] == "passed"

    bad = assess_llm_faithfulness("覆盖率 88.8%。", report)
    assert bad["status"] == "failed"
    assert any(item["text"] == "88.8%" for item in bad["unsupported_numbers"])


def test_llm_guardrail_rejects_unit_converted_volume_and_duration_as_percent():
    """Dual: volume stays in 亿元; duration years must not be re-labeled as percent."""
    report = {
        "data_evidence": {
            "market": {"volume_summary": {"mean": 4.934, "median": 1.0, "count": 3363}},
            "search": {
                "records": [
                    {
                        "收盘到期收益率(%)": 2.5647,
                        "修正久期(现金流假设)": 10.9372,
                        "交易量(亿元)": 3.8,
                    }
                ]
            },
            "comparison": {
                "rate_sensitivity": {"modified_duration": 10.9372, "dv01": 0.109372},
            },
        }
    }
    ok = assess_llm_faithfulness(
        "volume mean 4.934 亿元; modified duration 10.9372 years; yield 2.5647%.",
        report,
    )
    assert ok["status"] == "passed"
    assert ok["unsupported_numbers"] == []

    bad_vol = assess_llm_faithfulness("average turnover is 493.4 million CNY.", report)
    assert bad_vol["status"] == "failed"
    assert any(item["text"] == "493.4" for item in bad_vol["unsupported_numbers"])

    bad_dur = assess_llm_faithfulness("modified duration is 10.94%.", report)
    assert bad_dur["status"] == "failed"
    assert any(item["text"] == "10.94%" for item in bad_dur["unsupported_numbers"])

def test_llm_guardrail_accepts_truncated_dv01_and_ratio_bare_number():
    """Dual: truncated DV01 / bare coverage percent OK; invented values fail."""
    report = {
        "data_source": {"maturity_coverage": {"coverage_ratio": 0.9994, "coverage_percent": 99.94}},
        "data_evidence": {
            "comparison": {
                "rate_sensitivity": {"dv01": 0.109372, "modified_duration": 10.9372},
                "peer_comparison": {"spread_vs_peer_mean_bp": -5.95, "peer_yield_percentile": 28.57},
            },
            "search": {"records": [{"收盘到期收益率(%)": 2.5647, "交易量(亿元)": 3.8}]},
        },
    }
    ok = assess_llm_faithfulness(
        "coverage 99.94; dv01 0.1093; duration 10.9372 years; yield 2.5647%; volume 3.8.",
        report,
    )
    assert ok["status"] == "passed", ok["unsupported_numbers"]
    ok_bp_pct = assess_llm_faithfulness("peer spread about -0.0595%. yield 2.5647%.", report)
    assert ok_bp_pct["status"] == "passed", ok_bp_pct["unsupported_numbers"]
    bad = assess_llm_faithfulness("coverage 88.8; dv01 0.2222; yield 2.5647%.", report)
    assert bad["status"] == "failed"
    texts = {item["text"] for item in bad["unsupported_numbers"]}
    assert "88.8" in texts or "88.8%" in texts
    assert "0.2222" in texts


def test_llm_guardrail_en_overview_rejects_invented_bare_share_but_accepts_quality_percent():
    """Dual: EN overview may copy quality 7.8%; invented bare 5%/10% shares fail."""
    report = {
        "data_evidence": {
            "market": {
                "sample_count": 3365,
                "yield_summary": {
                    "mean": 2.7709,
                    "median": 2.45,
                    "p25": 2.255,
                    "p75": 2.7468,
                    "count": 3102,
                },
                "volume_summary": {"mean": 4.934, "median": 1.0},
                "data_quality": {
                    "score": 84,
                    "issues": [
                        {
                            "id": "missing_yield",
                            "message_en": "Missing yield on 263/3365 (7.8%).",
                            "message_zh": "收益率缺失 263/3365（7.8%）。",
                        }
                    ],
                },
            }
        },
        "data_source": {
            "maturity_coverage": {"coverage_ratio": 0.9994, "coverage_percent": 99.94}
        },
    }
    ok = assess_llm_faithfulness(
        "Sample has 3365 bonds. Yield mean 2.7709%, median 2.45%. "
        "Missing yield on 263/3365 (7.8%). Coverage 99.94%. Not investment advice.",
        report,
    )
    assert ok["status"] == "passed", ok["unsupported_numbers"]
    assert ok["numeric_status"] == "passed"

    bad_five = assess_llm_faithfulness(
        "About 5% of the sample shows elevated stress in this overview.",
        report,
    )
    assert bad_five["status"] == "failed"
    assert bad_five["numeric_status"] == "failed"
    assert any(item["text"] == "5%" for item in bad_five["unsupported_numbers"])

    bad_ten = assess_llm_faithfulness(
        "Roughly 10% of bonds sit in an abnormal yield pocket.",
        report,
    )
    assert bad_ten["status"] == "failed"
    assert any(item["text"] == "10%" for item in bad_ten["unsupported_numbers"])


def test_llm_guardrail_en_overview_still_rejects_25_percent_share_even_with_p25():
    """p25 label evidence must not license invented '25% of the market' claims."""
    report = {
        "data_evidence": {
            "market": {
                "sample_count": 100,
                "yield_summary": {"mean": 2.5, "median": 2.4, "p25": 2.0, "p75": 3.0},
            }
        }
    }
    result = assess_llm_faithfulness(
        "About 25% of the market trades above the median in this sample.",
        report,
    )
    assert result["status"] == "failed"
    assert result["numeric_status"] == "failed"
    assert any(item["text"] == "25%" for item in result["unsupported_numbers"])


def test_host_openai_env_is_isolated_from_unit_tests():
    """Regression lock: autouse conftest must strip host OPENAI_* before each test."""
    import os

    leaked = sorted(k for k in os.environ if k.startswith("OPENAI_"))
    assert leaked == [], f"host OPENAI_* leaked into tests: {leaked}"


# === P1-A: CJK numeral bypass attacks must be blocked ===

def test_llm_guardrail_rejects_cjk_numeral_bypass_attack():
    """A CJK-spelled percentile like '百分之七点八' must hit the same numeric
    guardrail as '7.8%'. Without _normalize_cjk_numerals, the regex scanner
    would only see ASCII digits and let the invented 7.8 pass as innocent
    prose while the backed evidence has yield 2.45% and duration 7.8 years."""
    from bond_agent.llm_guardrail import _has_cjk_numerals, _normalize_cjk_numerals

    assert _has_cjk_numerals("收益率约百分之七点八") is True
    assert _has_cjk_numerals("久期 7.8 年") is False

    report = {
        "market_overview": {
            "weighted_yield_summary": {"median": 2.45, "sample_count": 12}
        },
        "risk_profile": {"modified_duration": 7.8, "macaulay_duration": 7.8},
    }
    r = assess_llm_faithfulness(
        "该券收益率约百分之七点八，远高于市场平均水平。非投资建议，仅用于学习和研究。",
        report,
    )
    assert r["numeric_status"] == "failed"
    # The fail-closed canary emits a synthetic <cjk-numeral> entry when CJK
    # numerals are present but ASCII extraction yielded nothing. Either that
    # canary or a normalized 7.8% must show up in the unsupported list.
    texts = {item["text"] for item in r["unsupported_numbers"]}
    assert "<cjk-numeral>" in texts or "7.8%" in texts or "7.8" in texts or "百分之七点八" in texts


def test_llm_guardrail_rejects_cjk_plus_ascii_mix_bypass_attack():
    """Mixed CJK + ASCII ('百分之8') must also be caught — the CJK normalizer
    handles '百分之' + ASCII digit and produces a percent-form 8% token."""
    report = {
        "market_overview": {
            "weighted_yield_summary": {"median": 2.45, "sample_count": 12}
        },
        "risk_profile": {"modified_duration": 8.0, "macaulay_duration": 8.0},
    }
    r = assess_llm_faithfulness(
        "该券收益率约百分之8，远高于市场平均水平。非投资建议，仅用于学习和研究。",
        report,
    )
    assert r["numeric_status"] == "failed"


# === P1-B: cross-domain magnitude laundering attacks ===

def test_llm_guardrail_rejects_yield_claim_using_duration_evidence_value():
    """Attacker writes '收益率约 7.8' while the only evidence holding 7.8 is
    modified_duration — same magnitude, wrong domain. Must FAIL.
    Before P1-B, magnitude-only matching laundered the duration 7.8 into a
    'yield 7.8%' claim that had no real yield evidence support."""
    report = {
        "market_overview": {
            "weighted_yield_summary": {"median": 2.45, "sample_count": 12}
        },
        "risk_profile": {"modified_duration": 7.8, "macaulay_duration": 7.8},
    }
    r = assess_llm_faithfulness(
        "该券收益率约 7.8，远高于市场平均水平。非投资建议，仅用于学习和研究。",
        report,
    )
    assert r["numeric_status"] == "failed"
    assert any(item["text"] == "7.8" for item in r["unsupported_numbers"])


def test_llm_guardrail_rejects_duration_claim_using_yield_evidence_value():
    """Symmetric cross-domain attack: '久期 7.8 年' written against a report
    that has no duration field but a coincidentally-equal yield 7.8%. Without
    P1-B, magnitude-only matching laundered the yield 7.8% into the duration
    claim. Must FAIL."""
    report = {
        "market_overview": {
            "weighted_yield_summary": {"median": 7.8, "sample_count": 12}
        }
    }
    r = assess_llm_faithfulness(
        "该券久期 7.8 年。非投资建议，仅用于学习和研究。",
        report,
    )
    assert r["numeric_status"] == "failed"
    assert any(item["text"] == "7.8" for item in r["unsupported_numbers"])


def test_llm_guardrail_still_accepts_legit_duration_with_real_yield():
    """Regression lock for P1-B's per-category gating: when both domains are
    covered by real evidence — duration 7.8 years AND yield 2.45% — the same
    dual-claim sentence must still PASS. Don't over-tighten into false rejects."""
    report = {
        "market_overview": {
            "weighted_yield_summary": {"median": 2.45, "sample_count": 12}
        },
        "risk_profile": {"modified_duration": 7.8, "macaulay_duration": 7.8},
    }
    r = assess_llm_faithfulness(
        "该券久期 7.8 年，中位收益率 2.45%。非投资建议，仅用于学习和研究。",
        report,
    )
    assert r["status"] == "passed"
    assert r["unsupported_numbers"] == []


def test_llm_guardrail_accepts_bare_yield_numeric_against_percent_evidence():
    """Regression lock for the legacy loose behavior: a bare numeric yield
    claim ('收益率 7.8' without %) must still match percent-unit yield
    evidence at 7.8%. The P1-B yield cross-unit bridge keeps this working."""
    report = {
        "market_overview": {
            "weighted_yield_summary": {"median": 7.8, "sample_count": 12}
        }
    }
    r = assess_llm_faithfulness(
        "该券中位收益率 7.8。非投资建议，仅用于学习和研究。",
        report,
    )
    assert r["status"] == "passed"
    assert r["unsupported_numbers"] == []


def test_llm_guardrail_anchors_claim_to_nearest_keyword_not_distant_one():
    """Regression lock for _CLAIM_KW_MAX_DIST: in a multi-claim sentence like
    '覆盖率 99.94%（3363/3365）', the 3363 numerator must NOT be classified
    as a 'ratio' claim by the distant 覆盖率 keyword 16+ chars earlier —
    instead it falls back to 'unknown' and matches the count evidence. This
    avoids the over-tight false-reject introduced by an over-eager anchor."""
    report = {
        "data_source": {
            "maturity_coverage": {
                "filled_count": 3363,
                "missing_count": 2,
                "coverage_ratio": 0.9994,
                "coverage_percent": 99.94,
            }
        },
        "data_evidence": {"market": {"sample_count": 3365}},
    }
    r = assess_llm_faithfulness(
        "覆盖率 99.94%（3363/3365）。非投资建议，仅用于学习和研究。",
        report,
    )
    assert r["status"] == "passed"
    assert r["unsupported_numbers"] == []


def test_llm_guardrail_bare_quantile_labels_unchanged_legacy_loose():
    """Regression lock: '套入样本均值 2.7709 ...' with NO strong yield keyword
    near the bare numeric must still match yield percent evidence via the
    legacy loose (unknown-cat) branch — P1-B must not over-tighten this."""
    report = {
        "data_evidence": {
            "market": {
                "sample_count": 3365,
                "yield_summary": {
                    "mean": 2.7709, "median": 2.45, "p25": 2.255, "p75": 2.7468, "count": 3102,
                },
            }
        }
    }
    r = assess_llm_faithfulness(
        "均值 2.7709，中位数 2.45，25分位 2.255，75分位 2.7468。非投资建议。",
        report,
    )
    assert r["status"] == "passed"


def test_planner_blocks_chinese_advisory_blacklist_and_analytical_ok():
    """Regression lock for the bilingual advisory regex symmetry update.
    Chinese advisory phrases (该不该买/配/卖, 值不值得买) must route to
    advisory_refusal, but analytical 'compare buy-side volume' must not
    be over-blocked by a bare 'buy' keyword."""
    from bond_agent.planner import classify_intent

    assert classify_intent("今天该不该买 23 国债 10?")["intent"] == "advisory_refusal"
    assert classify_intent("该不该配 22 国债 15?")["intent"] == "advisory_refusal"
    assert classify_intent("该不该卖这只债?")["intent"] == "advisory_refusal"
    assert classify_intent("这只值不值得买?")["intent"] == "advisory_refusal"
    # Analytical uses must NOT be classified as advisory.
    assert classify_intent("compare buy-side volume for 期限>5Y 国债")["intent"] != "advisory_refusal"
    assert classify_intent("portfolio weights and duration drift 分析")["intent"] != "advisory_refusal"


def test_planner_blocks_english_advisory_phrases():
    """Regression lock for the English advisory regex extension."""
    from bond_agent.planner import classify_intent

    assert classify_intent("Should I buy this bond?")["intent"] == "advisory_refusal"
    assert classify_intent("Should I sell this bond?")["intent"] == "advisory_refusal"
    assert classify_intent("Is bond 22 国债 15 worth buying?")["intent"] == "advisory_refusal"


def test_planner_blocks_chinese_retail_slang_advisory():
    """Regression lock for retail slang advisory attacks caught in P1-D pass 2.

    These short / casual phrasings (加吗 / 减仓么 / 清仓喔 / all in 这只) are
    typical retail advice solicitations BondLens must refuse — they would
    flow through to the report path otherwise (a false negative attack).
    """
    from bond_agent.planner import classify_intent

    blocked = [
        "加吗",
        "减吗",
        "加仓吗",
        "减仓么",
        "清仓吧",
        "该不该持有这只?",
        "可不可以长期持有?",
        "可不可以长期持有这只",
        "all in 这只?",
        "all-in 23国债10 行不行",
        "持有还是赎?",
    ]
    for q in blocked:
        assert classify_intent(q)["intent"] == "advisory_refusal", f"regression: {q!r} not refused"


def test_planner_keeps_analytical_buyside_volume_off_advisory():
    """Regression lock: 'buy-side' as an analytical adjective must NOT be
    classified as advisory — matched by neither planner nor guardrail. "Down
    weighting' / 调研 wording are analytical terms BondLens serves as data."""
    from bond_agent.planner import classify_intent

    not_advisory = [
        "compare buy-side volume for 期限>5Y 国债",
        "buy-side flow analysis",
        "portfolio weights and duration drift 分析",
        "25分位收益率分布分析",
        "债券按期限分类占比",
    ]
    for q in not_advisory:
        assert classify_intent(q)["intent"] != "advisory_refusal", f"regression: {q!r} wrongly refused"


def test_llm_guardrail_english_buy_side_strategy_not_false_flagged():
    """Regression lock: 'a buy-side desk' / 'a buy-side strategy' are analytical
    prose, not advisory recommendations. The negative-lookahead `(?!\s*-)` on
    `a buy|a sell|…` patterns must keep them clear while still catching colloquial
    'a buy' / 'Strong buy' inside declarative recommendations."""
    from bond_agent.llm_guardrail import _find_unsafe_phrases

    # Analytical / sentence-context uses must NOT be flagged.
    assert _find_unsafe_phrases("A buy-side strategy is common in bond desks") == []
    assert _find_unsafe_phrases("buy-side flow analysis") == []
    assert _find_unsafe_phrases("a sell-side desk") == []
    assert _find_unsafe_phrases("The report is not a buy recommendation") == []

    # Genuine advisory forms must still be flagged after the fix.
    assert any(item["rule_id"] == "english_buy_recommendation" for item in _find_unsafe_phrases("Strong buy signal"))
    assert any(item["rule_id"] == "english_buy_recommendation" for item in _find_unsafe_phrases("you should buy this bond"))
    assert any(item["rule_id"] == "english_buy_recommendation" for item in _find_unsafe_phrases("consider buying this bond"))

