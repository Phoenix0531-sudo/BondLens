import pandas as pd

from bond_agent import BondAnalystAgent


def test_agent_fallback_uses_local_tools_without_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    result = BondAnalystAgent(data_mode="static").answer("搜索23附息国债26并给出收益率分析")

    assert result["used_llm"] is False
    assert result["used_llm_in_final"] is False
    assert result["llm_status"] == "disabled"
    assert result["llm_error"] is None
    assert result["llm_guardrail"]["status"] == "not_run"
    assert result["answer_judge"]["status"] == "not_applicable"
    assert result["evidence_ledger"]
    assert result["risk_profile"]["overall_level"] in {"low", "medium", "high"}
    assert result["plan"]["intent"] == "bond_report"
    assert result["data_source"]["source_id"] == "local_static_excel"
    assert result["data_source"]["active_live_feed"] is False
    assert result["risk_explanations"]
    assert result["evidence_quality"]["level"] in {"medium", "high"}
    assert result["evidence_quality"]["decision_confidence"] == "low"
    assert "search_bonds" in result["tools_used"]
    assert "compare_bond_to_market" in result["tools_used"]
    assert "generate_bond_report" in result["tools_used"]
    assert "23附息国债26" in result["final_answer"]
    assert "风险解释层" in result["final_answer"]
    assert "证据质量" in result["final_answer"]
    assert "非投资建议，仅用于学习和研究" in result["final_answer"]
    assert result["tool_trace"][-1] == "-> final answer"
    assert result["replay_id"]


def test_agent_tool_selection_for_market_overview(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    result = BondAnalystAgent(data_mode="static").answer("当前样本收益率分布是什么样？")

    assert result["plan"]["intent"] == "market_overview"
    assert "describe_market" in result["tools_used"]
    assert "build_market_monitor" in result["tools_used"]
    assert "generate_bond_report" in result["tools_used"]
    assert "rank_bonds" not in result["tools_used"]
    assert "detect_yield_outliers" not in result["tools_used"]
    assert "-> generate_bond_report()" in result["tool_trace"]


def test_agent_search_only_answer_shows_search_evidence(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    result = BondAnalystAgent(data_mode="static").answer("筛选收益率大于 3 的债券")

    assert result["plan"]["intent"] == "bond_search"
    assert result["tools_used"] == ["search_bonds", "generate_bond_report"]
    assert "检索命中数量" in result["final_answer"]
    assert "检索条件" in result["final_answer"]


def test_agent_exposes_confidence_and_risk_retrieval(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    result = BondAnalystAgent(data_mode="static").answer("有没有收益率异常的债券？")

    assert result["evidence_quality"]["coverage"]["has_risk_explanations"] is True
    assert result["evidence_quality"]["data_freshness"] == "static_snapshot"
    assert any(item["id"] == "outlier_risk" for item in result["risk_explanations"])
    assert "Issuer ratings" in result["evidence_quality"]["penalties"][-1]


class _FakeResponse:
    output_text = "LLM enhanced answer。非投资建议，仅用于学习和研究。"


class _FakeResponses:
    def create(self, **kwargs):
        return _FakeResponse()


class _FakeClient:
    responses = _FakeResponses()


class _FailingResponses:
    def create(self, **kwargs):
        raise RuntimeError("boom")


class _FailingClient:
    responses = _FailingResponses()


class _FakeChatMessage:
    content = "Local LLM chat answer。"


class _FakeChatChoice:
    message = _FakeChatMessage()


class _FakeChatCompletion:
    def __init__(self):
        self.choices = [_FakeChatChoice()]


class _FakeChatCompletions:
    def create(self, **kwargs):
        return _FakeChatCompletion()


class _FakeChat:
    completions = _FakeChatCompletions()


class _FakeLocalClient:
    chat = _FakeChat()


class _FakeBadChatMessage:
    content = "样本中 99% 的收益率都非常安全。"


class _FakeBadChatChoice:
    message = _FakeBadChatMessage()


class _FakeBadChatCompletion:
    def __init__(self):
        self.choices = [_FakeBadChatChoice()]


class _FakeBadChatCompletions:
    def create(self, **kwargs):
        return _FakeBadChatCompletion()


class _FakeBadChat:
    completions = _FakeBadChatCompletions()


class _FakeBadLocalClient:
    chat = _FakeBadChat()


class _FakeAdviceChatMessage:
    content = "建议买入这只债券。非投资建议，仅用于学习和研究。"


class _FakeAdviceChatChoice:
    message = _FakeAdviceChatMessage()


class _FakeAdviceChatCompletion:
    def __init__(self):
        self.choices = [_FakeAdviceChatChoice()]


class _FakeAdviceChatCompletions:
    def create(self, **kwargs):
        return _FakeAdviceChatCompletion()


class _FakeAdviceChat:
    completions = _FakeAdviceChatCompletions()


class _FakeAdviceLocalClient:
    chat = _FakeAdviceChat()


def test_agent_llm_status_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_STYLE", raising=False)
    monkeypatch.setattr(BondAnalystAgent, "_create_openai_client", lambda self, api_key, **kwargs: _FakeClient())

    result = BondAnalystAgent(data_mode="static").answer("当前样本收益率分布是什么样？")

    assert result["used_llm"] is True
    assert result["used_llm_in_final"] is True
    assert result["llm_status"] == "success"
    assert result["llm_error"] is None
    assert result["llm_guardrail"]["status"] == "passed"
    assert result["answer_judge"]["status"] == "passed"
    assert result["final_answer"].startswith("LLM enhanced answer")


def test_agent_llm_status_failed_keeps_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_STYLE", "responses")
    monkeypatch.setattr(BondAnalystAgent, "_create_openai_client", lambda self, api_key, **kwargs: _FailingClient())

    result = BondAnalystAgent(data_mode="static").answer("当前样本收益率分布是什么样？")

    assert result["used_llm"] is False
    assert result["used_llm_in_final"] is False
    assert result["llm_status"] == "failed"
    assert result["llm_error"] == "OpenAI request failed: RuntimeError"
    assert "问题:" in result["final_answer"] or "Question:" in result["final_answer"]


def test_agent_local_openai_compatible_base_url_uses_chat_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:1.5b")

    seen = {}

    def fake_create_client(self, api_key, **kwargs):
        seen["api_key"] = api_key
        seen["base_url"] = kwargs.get("base_url")
        return _FakeLocalClient()

    monkeypatch.setattr(BondAnalystAgent, "_create_openai_client", fake_create_client)

    result = BondAnalystAgent(data_mode="static").answer("当前样本收益率分布是什么样？")

    assert seen["api_key"] == "local-not-needed"
    assert seen["base_url"] == "http://127.0.0.1:11434/v1"
    assert result["used_llm"] is True
    assert result["used_llm_in_final"] is True
    assert result["llm_status"] == "success"
    assert result["llm_guardrail"]["status"] == "passed"
    assert result["final_answer"].startswith("Local LLM chat answer")
    assert "非投资建议，仅用于学习和研究" in result["final_answer"]


def test_agent_openai_client_uses_configured_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "3.5")

    client = BondAnalystAgent()._create_openai_client("test-key", base_url="http://127.0.0.1:11434/v1")

    assert client.timeout == 3.5


def test_agent_rejects_llm_output_with_unsupported_numeric_claim(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")

    monkeypatch.setattr(BondAnalystAgent, "_create_openai_client", lambda self, api_key, **kwargs: _FakeBadLocalClient())

    result = BondAnalystAgent(data_mode="static").answer("当前样本收益率分布是什么样？")

    assert result["used_llm"] is True
    assert result["used_llm_in_final"] is False
    assert result["final_answer_source"] == "deterministic_fallback"
    assert result["llm_guardrail"]["status"] == "failed"
    assert result["answer_judge"]["status"] == "failed_guardrail"
    assert result["llm_guardrail"]["numeric_status"] == "failed"
    assert result["llm_guardrail"]["language_status"] == "failed"
    assert result["llm_enhanced_answer"].startswith("样本中 99%")
    assert result["final_answer"].startswith(("问题:", "Question:"))
    assert any(item["text"] == "99%" for item in result["llm_guardrail"]["unsupported_numbers"])
    assert any(item["rule_id"] == "risk_free_claim" for item in result["llm_guardrail"]["unsafe_phrases"])


def test_agent_rejects_llm_output_with_investment_advice_language(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")

    monkeypatch.setattr(BondAnalystAgent, "_create_openai_client", lambda self, api_key, **kwargs: _FakeAdviceLocalClient())

    result = BondAnalystAgent(data_mode="static").answer("搜索23附息国债26并给出收益率分析")

    assert result["used_llm"] is True
    assert result["used_llm_in_final"] is False
    assert result["final_answer_source"] == "deterministic_fallback"
    assert result["llm_guardrail"]["status"] == "failed"
    assert result["llm_guardrail"]["numeric_status"] == "passed"
    assert result["llm_guardrail"]["language_status"] == "failed"
    assert any(item["rule_id"] == "buy_recommendation" for item in result["llm_guardrail"]["unsafe_phrases"])
    assert result["final_answer"].startswith(("问题:", "Question:"))


def test_agent_can_use_live_bond_feed_without_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    def fake_fetcher():
        return pd.DataFrame(
            {
                "债券简称": ["25国开20", "26超长特别国债02", "25四川港投MTN002"],
                "成交净价": [101.23, 98.76, 100.01],
                "最新收益率": [2.12, 2.65, 3.2],
                "涨跌": [-0.3, 1.2, 0.0],
                "加权收益率": [2.11, 2.66, 3.18],
                "交易量": [4.5, 8.0, 2.1],
            }
        )

    result = BondAnalystAgent(data_mode="live", live_fetcher=fake_fetcher).answer("搜索25国开20并给出收益率分析")

    assert result["data_source"]["source_id"] == "chinamoney_bond_spot_deal"
    assert result["data_source"]["runtime_mode"] == "live"
    assert result["data_source"]["active_live_feed"] is True
    assert result["evidence_quality"]["data_freshness"] == "live_fetch"
    assert "25国开20" in result["final_answer"]


def test_agent_live_feed_enriches_known_bond_maturity(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

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

    result = BondAnalystAgent(data_mode="live", live_fetcher=fake_fetcher).answer("搜索23附息国债26并给出收益率分析")
    record = result["data_evidence"]["search"]["records"][0]

    assert record["待偿期"] is not None
    assert record["待偿期(年)"] is not None
    assert result["data_source"]["maturity_coverage"]["filled_count"] == 1
    assert "待偿期 None" not in result["final_answer"]


def test_agent_advisory_refusal_blocks_recommendations(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("BOND_EVIDENCE_PACK_ENABLED", "false")

    result = BondAnalystAgent(data_mode="static").answer("今天该不该买债？")

    assert result["plan"]["intent"] == "advisory_refusal"
    assert result["tools_used"] == ["describe_market", "generate_bond_report"]
    assert result["used_llm_in_final"] is False
    assert result["llm_status"] == "disabled"
    assert result["llm_error"] == "advisory_policy_block"
    assert result["final_answer_source"] == "deterministic_fallback"
    assert "投资建议拦截" in result["final_answer"]
    assert "不给出具体买入" in result["final_answer"]
    assert "非投资建议，仅用于学习和研究" in result["final_answer"]
    assert result["trust_score"]["score"] <= 72
    assert any("input_policy(status=advisory_refusal)" in item for item in result["tool_trace"])


def test_agent_ranking_includes_market_summary(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("BOND_EVIDENCE_PACK_ENABLED", "false")

    result = BondAnalystAgent(data_mode="static").answer("按收益率列出最高的前5只债券")

    assert result["plan"]["intent"] == "ranking"
    assert result["tools_used"] == ["describe_market", "rank_bonds", "generate_bond_report"]
    assert result["data_evidence"]["market"]
    assert result["data_evidence"]["market"]["yield_summary"].get("median") is not None
    assert result["data_evidence"]["ranking"]


def test_agent_retries_transient_rate_limit_then_succeeds(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:31876/v1")
    monkeypatch.setenv("OPENAI_API_STYLE", "chat")
    monkeypatch.setenv("OPENAI_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("OPENAI_RETRY_BACKOFF", "0")
    monkeypatch.setenv("OPENAI_MODEL", "grok-4.5")
    monkeypatch.delenv("OPENAI_MODEL_FALLBACKS", raising=False)
    monkeypatch.delenv("OPENAI_FALLBACK_MODEL", raising=False)

    class RateLimitError(Exception):
        pass

    calls = {"n": 0}

    class _FlakyChatCompletions:
        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RateLimitError("429 rate limit")
            return _FakeChatCompletion()

    class _FlakyChat:
        completions = _FlakyChatCompletions()

    class _FlakyClient:
        chat = _FlakyChat()

    monkeypatch.setattr(BondAnalystAgent, "_create_openai_client", lambda self, api_key, **kwargs: _FlakyClient())
    result = BondAnalystAgent(data_mode="static").answer("当前样本收益率分布是什么样？")
    assert calls["n"] == 3
    assert result["llm_status"] == "success"
    assert result["used_llm_in_final"] is True
    assert result["final_answer"].startswith("Local LLM chat answer")


def test_agent_llm_evidence_includes_peer_spread():
    agent = BondAnalystAgent(data_mode="static")
    report = {
        "data_evidence": {
            "market": {"sample_count": 10, "yield_summary": {"median": 2.5}, "data_quality": {}},
            "search": {"records": [{"债券简称": "06国开24"}], "match_count": 1},
            "comparison": {
                "bond_name": "06国开24",
                "peer_comparison": {
                    "spread_vs_peer_mean_bp": -5.95,
                    "peer_count": 12,
                    "bond_type": "国开",
                },
                "rate_sensitivity": {},
            },
            "ranking": {},
            "outliers": {},
        },
        "data_source": {"source_name": "static", "runtime_mode": "static", "row_count": 10},
        "analysis": [],
        "risk_notes": [],
        "limitations": [],
    }
    payload = agent._build_llm_evidence("report", {"intent": "bond_report"}, report)
    peer = ((payload.get("data_evidence") or {}).get("comparison") or {}).get("peer_comparison") or {}
    assert peer.get("spread_vs_peer_mean_bp") == -5.95
    assert peer.get("peer_count") == 12

def test_llm_model_candidates_default_includes_deepseek(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL_FALLBACKS", raising=False)
    monkeypatch.delenv("OPENAI_FALLBACK_MODEL", raising=False)
    models = BondAnalystAgent(data_mode="static")._llm_model_candidates()
    assert models[0] == "deepseek-v4-flash-search"
    assert "gpt-5.4-mini" in models


def test_filter_models_by_probe_prefers_listed_ids(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://[IP]:31876/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4")
    monkeypatch.delenv("OPENAI_MODEL_FALLBACKS", raising=False)
    monkeypatch.setenv("OPENAI_MODEL_PROBE", "1")
    BondAnalystAgent._probed_model_ids = None
    BondAnalystAgent._probed_model_base = None

    class _Model:
        def __init__(self, model_id):
            self.id = model_id

    class _Listed:
        def __init__(self):
            self.data = [_Model("deepseek-v4-flash-search"), _Model("other-local")]

    class _ModelsAPI:
        def list(self):
            return _Listed()

    class _Client:
        models = _ModelsAPI()

    agent = BondAnalystAgent(data_mode="static")
    ordered = agent._filter_models_by_probe(
        _Client(),
        ["gpt-5.4", "deepseek-v4-flash-search", "grok-4.5"],
    )
    assert ordered[0] == "deepseek-v4-flash-search"
    assert "gpt-5.4" in ordered  # still tried as last-resort


def test_build_llm_evidence_includes_market_focus_numbers():
    agent = BondAnalystAgent(data_mode="static")
    report = {
        "data_evidence": {
            "market": {
                "sample_count": 100,
                "yield_summary": {"count": 90, "mean": 2.5, "median": 2.4, "p25": 2.0, "p75": 3.0, "min": 1.0, "max": 5.0},
                "volume_summary": {"mean": 1.2, "median": 0.8},
                "data_quality": {
                    "score": 88,
                    "issues": [{"id": "missing_yield", "message_en": "Missing yield on 10/100 (10.0%)."}],
                },
            },
            "search": {"records": [], "match_count": 0},
            "comparison": {},
            "ranking": {},
            "outliers": {},
        },
        "data_source": {
            "source_name": "static",
            "runtime_mode": "static",
            "row_count": 100,
            "maturity_coverage": {"coverage_ratio": 0.999, "filled_count": 99, "missing_count": 1},
        },
        "analysis": [],
        "risk_notes": [],
        "limitations": [],
    }
    payload = agent._build_llm_evidence(
        "Give an overview of the current bond market sample.",
        {"intent": "market_overview"},
        report,
    )
    focus = payload.get("market_focus_numbers") or {}
    assert focus.get("sample_count") == 100
    assert focus.get("yield_median") == 2.4
    assert focus.get("coverage_percent") == 99.9
    assert any("10.0%" in str(x) for x in (focus.get("allowed_quality_percents") or []))
    instr = agent._llm_instructions("en")
    assert "Do NOT invent bare percentages" in instr
    assert "market_focus_numbers" in instr
