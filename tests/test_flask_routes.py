from app import app


def test_agent_pages_smoke():
    client = app.test_client()

    assert client.get("/").status_code == 302
    response = client.get("/agent?data_mode=static")

    assert response.status_code == 200
    assert b"Agent Console" in response.data
    assert b"Evidence Console" not in response.data
    assert b"Planner JSON" not in response.data


def test_agent_page_exposes_language_switch():
    client = app.test_client()

    response = client.get("/agent?data_mode=static&lang=zh")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-language-option="zh"' in html
    assert 'data-language-option="en"' in html
    assert "智能体控制台" in html
    assert "Agent Console" in html


def test_agent_page_shows_submit_busy_state_hooks():
    client = app.test_client()

    response = client.get("/agent?data_mode=static&lang=zh")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="agent-query-form"' in html
    assert 'id="run-agent-button"' in html
    assert "分析中，请稍候" in html
    assert "请勿重复点击" in html
    assert 'data-async-endpoint="/api/agent/query"' in html
    assert 'data-stream-endpoint="/api/agent/stream"' in html
    assert 'id="agent-async-progress"' in html
    assert 'id="agent-stream-preview"' in html
    assert "/api/agent/query" in html
    assert "/api/agent/stream" in html


def test_agent_page_localizes_result_for_chinese():
    client = app.test_client()

    response = client.post(
        "/agent",
        data={"question": "当前样本收益率分布是什么样？", "data_mode": "static", "lang": "zh"},
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "首屏答案摘要" in html or "最终回答" in html or "完整回答与轨迹" in html
    assert "问题：" in html
    assert "风险解释层" in html
    assert "风险画像" in html
    assert "证据账本" in html
    assert "答案评审" in html
    assert "工具轨迹" in html
    assert "跳过：LLM 未启用" in html
    assert "规划器 JSON" not in html
    assert "数据证据 JSON" not in html


def test_replay_page_smoke(monkeypatch, tmp_path):
    monkeypatch.setenv("BOND_REPLAY_DIR", str(tmp_path))
    client = app.test_client()
    client.post("/agent", data={"question": "当前样本收益率分布是什么样？", "data_mode": "static", "lang": "zh"})

    response = client.get("/replay?lang=zh")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Agent 回放仪表盘" in html
    assert "当前样本收益率分布是什么样？" in html
    assert "Evidence Console" not in html


def test_healthz():
    client = app.test_client()

    response = client.get("/healthz")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {"checks": {"app": "ok"}, "service": "BondLens", "status": "ok"}


def test_agent_schema_endpoint():
    client = app.test_client()

    response = client.get("/api/agent/schema")

    assert response.status_code == 200
    payload = response.get_json()
    assert "agent_query_request" in payload
    assert "agent_response" in payload
    assert "api_error" in payload
    assert "final_answer" in payload["agent_response"]["properties"]
    assert "llm_guardrail" in payload["agent_response"]["properties"]
    assert "answer_judge" in payload["agent_response"]["properties"]
    assert "evidence_ledger" in payload["agent_response"]["properties"]


def test_agent_api_smoke():
    client = app.test_client()

    response = client.post("/api/agent/query", json={"question": "找出收益率最高的债券", "data_mode": "static"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["agent"] == "BondLens"
    assert payload["plan"]["intent"] == "ranking"
    assert "rank_bonds" in payload["tools_used"]
    assert "llm_status" in payload
    assert payload["data_source"]["runtime_mode"] == "static_sample"
    assert payload["risk_explanations"]
    assert payload["evidence_ledger"]
    assert payload["answer_judge"]["status"] == "not_applicable"
    assert payload["risk_profile"]["cards"]
    assert payload["evidence_quality"]["decision_confidence"] == "low"
    assert payload["used_llm"] is False


def test_agent_api_rejects_invalid_data_mode():
    client = app.test_client()

    response = client.post("/api/agent/query", json={"question": "test", "data_mode": "bad"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["allowed_data_modes"] == ["auto", "live", "static"]
    assert "Unsupported data_mode" in payload["error"]


def test_agent_api_handles_regex_special_character_search():
    client = app.test_client()

    response = client.post("/api/agent/query", json={"question": "搜索\"[\"并给出收益率分析", "data_mode": "static"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["plan"]["intent"] == "bond_report"
    assert payload["data_evidence"]["search"]["match_count"] == 0
    assert "未在当前债券数据源中找到符合条件的债券记录" in payload["final_answer"]


def test_agent_page_shows_answer_summary_and_maturity_board():
    client = app.test_client()

    response = client.post(
        "/agent",
        data={"question": "当前样本收益率分布是什么样？", "data_mode": "static", "lang": "zh"},
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "首屏答案摘要" in html
    assert "展开完整最终回答" in html
    assert "期限补全看板" in html
    assert "导出未匹配 CSV" in html
    assert "/api/maturity/unmatched?format=csv" in html
    assert "数据新鲜度" in html
    assert "答案来源说明" in html
    assert "获取时间" in html
    assert "无（本地样本）" in html or "获取" in html


def test_agent_page_explains_deterministic_fallback_provenance():
    client = app.test_client()

    response = client.post(
        "/agent",
        data={"question": "今天该不该买债？", "data_mode": "static", "lang": "zh"},
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "答案来源说明" in html
    assert "投资建议类问题被政策拦截" in html or "advisory" in html.lower() or "政策拦截" in html
    assert "数据新鲜度" in html


def test_maturity_unmatched_export_csv_and_json():
    client = app.test_client()

    csv_response = client.post(
        "/api/maturity/unmatched?format=csv&data_mode=static",
        json={
            "data_mode": "static",
            "maturity_coverage": {
                "filled_count": 1,
                "missing_count": 1,
                "coverage_ratio": 0.5,
                "unmatched_count": 1,
            },
            "records": [
                {
                    "债券简称": "测试债A",
                    "收盘到期收益率(%)": 3.2,
                    "交易量(亿元)": 1.1,
                }
            ],
        },
    )
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers.get("Content-Type", "")
    body = csv_response.get_data(as_text=True)
    assert "债券简称" in body
    assert "测试债A" in body

    json_response = client.post(
        "/api/maturity/unmatched?format=json&data_mode=static",
        json={
            "data_mode": "static",
            "maturity_coverage": {
                "filled_count": 1,
                "missing_count": 1,
                "coverage_ratio": 0.5,
                "unmatched_count": 1,
            },
            "records": [{"债券简称": "测试债B"}],
        },
    )
    assert json_response.status_code == 200
    payload = json_response.get_json()
    assert payload["unmatched_count"] == 1
    assert payload["records"][0]["债券简称"] == "测试债B"

def test_agent_api_returns_result_id_for_async_render():
    client = app.test_client()

    response = client.post(
        "/api/agent/query",
        json={"question": "当前样本收益率分布是什么样？", "data_mode": "static", "lang": "zh"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["result_id"]
    assert payload["result_url"]
    assert "result_id=" in payload["result_url"]

    page = client.get(payload["result_url"])
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "首屏答案摘要" in html or "最终回答" in html
    assert "数据新鲜度" in html


def test_agent_stream_endpoint_emits_final_event():
    client = app.test_client()
    response = client.post(
        "/api/agent/stream",
        json={"question": "当前样本收益率分布是什么样？", "data_mode": "static", "lang": "zh"},
        buffered=False,
    )
    assert response.status_code == 200
    assert "text/event-stream" in (response.headers.get("Content-Type") or "")
    body = response.get_data(as_text=True)
    assert "event: status" in body
    assert "event: final" in body
    assert "result_id" in body


def test_agent_page_rate_sensitivity_cashflow_labels():
    client = app.test_client()
    response = client.post(
        "/agent",
        data={"question": "搜索23附息国债26并给出收益率分析", "data_mode": "static", "lang": "zh"},
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "利率敏感度与信用边界" in html
    assert "修正久期(现金流)" in html or "Mod. duration" in html
