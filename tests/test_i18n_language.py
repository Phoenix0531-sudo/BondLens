from app import _resolve_language, app


def test_default_language_is_zh():
    with app.test_request_context("/agent"):
        assert _resolve_language() == "zh"


def test_query_lang_overrides_cookie():
    with app.test_request_context("/agent?lang=en", headers={"Cookie": "bondlens_lang=zh"}):
        assert _resolve_language() == "en"


def test_cookie_lang_used_when_query_missing():
    with app.test_request_context("/agent", headers={"Cookie": "bondlens_lang=en"}):
        assert _resolve_language() == "en"


def test_agent_page_sets_language_cookie_and_default_zh():
    client = app.test_client()
    response = client.get("/agent")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-initial-lang="zh"' in body
    assert "bondlens_lang=zh" in response.headers.get("Set-Cookie", "")


def test_agent_page_lang_en_sets_cookie_and_html_lang():
    client = app.test_client()
    response = client.get("/agent?lang=en")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-initial-lang="en"' in body
    assert "bondlens_lang=en" in response.headers.get("Set-Cookie", "")


def test_advisory_refusal_english_skeleton():
    from bond_agent.agent import BondAnalystAgent

    result = BondAnalystAgent(data_mode="static").answer("Should I buy bonds today for guaranteed returns?")
    assert result["plan"]["intent"] == "advisory_refusal"
    assert result["llm_status"] == "disabled"
    assert result["used_llm_in_final"] is False
    text = result["final_answer"]
    assert "investment-advice blocked" in text or "advisory_refusal" in text
    assert "No specific buy" in text or "Hard boundary" in text
    assert "非投资建议，仅用于学习和研究。" in text
