import pytest

from app import app
from bond_agent.agent import BondAnalystAgent


@pytest.mark.parametrize("endpoint", ["/api/agent/query", "/api/agent/stream", "/api/maturity/unmatched"])
@pytest.mark.parametrize("body", ["[]", "null", '"text"', "{broken"])
def test_json_body_must_be_object(endpoint, body):
    response = app.test_client().post(endpoint, data=body, content_type="application/json")
    assert response.status_code == 400
    assert response.is_json


@pytest.mark.parametrize("endpoint", ["/api/agent/query", "/api/agent/stream"])
def test_question_length_is_bounded(endpoint):
    response = app.test_client().post(endpoint, json={"question": "x" * 4001})
    assert response.status_code == 400


def test_form_mode_is_preserved():
    response = app.test_client().post(
        "/api/agent/query", data={"question": "市场概览", "data_mode": "static"}
    )
    assert response.status_code == 200
    assert response.get_json()["data_source"]["requested_mode"] == "static"


def test_expired_result_gives_recovery_instruction():
    response = app.test_client().get("/agent?result_id=expired&lang=en")
    assert "Run the analysis again" in response.get_data(as_text=True)


def test_csv_text_cannot_execute_spreadsheet_formula():
    response = app.test_client().post(
        "/api/maturity/unmatched", json={"records": [{"债券简称": "=1+1"}]}
    )
    assert "'=1+1" in response.get_data(as_text=True)


@pytest.mark.parametrize("url", ["https://localhost.example.com/v1", "https://remote.example:11434/v1", "https://example.com/localhost"])
def test_keyless_llm_requires_local_hostname(url):
    assert not BondAnalystAgent._base_url_allows_keyless_llm(url)


def test_sync_and_stream_share_deterministic_analysis(monkeypatch):
    monkeypatch.setenv("BOND_REPLAY_ENABLED", "false")
    monkeypatch.setenv("BOND_EVIDENCE_PACK_ENABLED", "false")
    agent = BondAnalystAgent(data_mode="static")
    sync = agent.answer("按收益率列出最高的前5只债券")
    streamed = next(
        event["result"] for event in agent.iter_answer_events(sync["question"])
        if event["type"] == "final"
    )
    for key in ["plan", "data_evidence", "final_answer", "trust_score"]:
        assert sync[key] == streamed[key]
