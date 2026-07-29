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
    # Console language select mirrors active language.
    assert 'id="ui_lang"' in body
    assert 'option value="en"' in body
    assert 'selected' in body
    assert "window.applyLanguage = applyLanguage" in body


def test_rendered_result_carries_bilingual_dynamic_panels():
    client = app.test_client()
    response = client.post(
        "/agent",
        data={"question": "当前样本收益率分布是什么样？", "data_mode": "static", "lang": "zh"},
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "data-lineage-translations" in body
    assert "Repository static sample; not a live market clock." in body
    assert "data-maturity-board-title" in body
    assert "Maturity Enrichment Board" in body
    assert "data-maturity-board-note" in body
    assert "LLM 模型" in body
    assert "LLM model" in body


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
