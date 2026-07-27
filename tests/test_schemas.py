from bond_agent import BondAnalystAgent
from bond_agent.schemas import AgentResponse, api_schema_bundle


def test_agent_response_validates_against_schema(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    result = BondAnalystAgent(data_mode="static").answer("当前样本收益率分布是什么样？")
    validated = AgentResponse.model_validate(result)

    assert validated.agent == "BondLens"
    assert validated.final_answer_source == "deterministic_fallback"
    assert validated.llm_guardrail.status == "not_run"
    assert validated.answer_judge.status == "not_applicable"
    assert validated.evidence_ledger
    assert validated.risk_profile.cards


def test_api_schema_bundle_exposes_response_contract():
    schema = api_schema_bundle()

    assert "agent_response" in schema
    assert "data_source" in schema["agent_response"]["properties"]
    assert "evidence_quality" in schema["agent_response"]["properties"]
    assert "llm_guardrail" in schema["agent_response"]["properties"]
    assert "evidence_ledger" in schema["agent_response"]["properties"]
    assert "answer_judge" in schema["agent_response"]["properties"]
    assert "risk_profile" in schema["agent_response"]["properties"]
