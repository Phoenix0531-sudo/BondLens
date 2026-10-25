"""On-demand AI explain endpoint (/api/agent/explain) contract tests.

Covers: explicit-trigger semantics (zero model by default), success caching,
409 duplicate-submit guard, 410 expired result, imported-data confirmation
flow, guardrail failure, advisory block, and honest unavailable state.
All model calls are fakes — no network, no billed requests.
"""

import io

import pytest

import app as app_module
from app import app
from bond_agent import BondAnalystAgent

app.config["TESTING"] = True

CSV_TEXT = (
    "债券简称,债券代码,到期收益率(%),待偿期\n"
    "23附息国债26,230026,2.35,6.5\n"
    "24国开01,240205,2.20,3.2\n"
)


class _FakeChatMessage:
    content = "样本收益率整体处于较低水平，数据质量已通过基本检查。非投资建议，仅用于学习和研究。"


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


def _client():
    return app.test_client()


def _seed_result(intent="market_overview", runtime_mode="static"):
    """Store a deterministic result in the app cache and return its id."""
    result = {
        "plan": {"intent": intent, "requested_tools": ["describe_market"]},
        "data_evidence": {
            "market": {"sample_count": 3, "yield_summary": {"median": 2.2}, "data_quality": {}}
        },
        "data_source": {"runtime_mode": runtime_mode, "source_name": "static", "row_count": 3},
        "analysis": [],
        "risk_notes": [],
        "limitations": [],
        "tools_used": ["describe_market"],
    }
    return app_module._store_result(result, "当前样本收益率分布是什么样？", "static")


@pytest.fixture(autouse=True)
def _clean_inflight():
    app_module._EXPLAIN_INFLIGHT.clear()
    app_module._BOND_EXPLAIN_CACHE.clear()
    yield
    app_module._EXPLAIN_INFLIGHT.clear()
    app_module._BOND_EXPLAIN_CACHE.clear()


def test_explain_requires_identifier():
    resp = _client().post("/api/agent/explain", json={})
    assert resp.status_code == 400
    assert "result_id" in resp.get_json()["error"]


def test_explain_expired_result_returns_410():
    resp = _client().post("/api/agent/explain", json={"result_id": "deadbeef"})
    assert resp.status_code == 410
    assert "过期" in resp.get_json()["error"]


def test_explain_without_model_config_is_honestly_unavailable():
    result_id = _seed_result()
    resp = _client().post("/api/agent/explain", json={"result_id": result_id})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "unavailable"
    assert body["text"] is None
    assert "确定性结果不受影响" in body["message_zh"]
    # The cached deterministic result is untouched.
    entry = app_module._load_result(result_id)
    assert "ai_explanation" not in entry["result"]


def test_explain_success_caches_onto_result_entry(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:1.5b")
    monkeypatch.setattr(
        BondAnalystAgent, "_create_openai_client", lambda self, api_key, **kwargs: _FakeLocalClient()
    )
    result_id = _seed_result()
    resp = _client().post("/api/agent/explain", json={"result_id": result_id})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["text"].startswith("样本收益率整体处于较低水平")
    assert body["guardrail_status"] == "passed"
    assert body["model"]

    # Reload reuse: the interpretation is attached to the stored result so
    # page reloads don't issue a second billed call.
    entry = app_module._load_result(result_id)
    cached = entry["result"]["ai_explanation"]
    assert cached["text"] == body["text"]
    assert cached["model"] == body["model"]
    assert cached["guardrail_status"] == "passed"
    assert cached["generated_at"]


def test_explain_duplicate_submit_returns_409():
    result_id = _seed_result()
    lock_key = f"result:{result_id}"
    app_module._EXPLAIN_INFLIGHT.add(lock_key)
    try:
        resp = _client().post("/api/agent/explain", json={"result_id": result_id})
        assert resp.status_code == 409
        assert resp.get_json()["status"] == "in_progress"
    finally:
        app_module._EXPLAIN_INFLIGHT.discard(lock_key)


def test_explain_guardrail_failure_discards_text(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:1.5b")
    monkeypatch.setattr(
        BondAnalystAgent, "_create_openai_client", lambda self, api_key, **kwargs: _FakeBadLocalClient()
    )
    result_id = _seed_result()
    resp = _client().post("/api/agent/explain", json={"result_id": result_id})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "guardrail_failed"
    assert body["text"] is None
    assert body["guardrail"]["numeric_status"] == "failed"
    entry = app_module._load_result(result_id)
    assert "ai_explanation" not in entry["result"]


def test_explain_advisory_blocked_result_has_no_ai():
    result_id = _seed_result(intent="advisory_refusal")
    resp = _client().post("/api/agent/explain", json={"result_id": result_id})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "blocked"
    assert body["text"] is None
    assert "不提供 AI 解读" in body["message_zh"]


def _counting_good_client(calls):
    """A fake OpenAI-compatible client that counts real model calls."""

    class _CountingCompletions(_FakeChatCompletions):
        def create(self, **kwargs):
            calls["n"] += 1
            return _FakeChatCompletion()

    class _CountingChat(_FakeChat):
        completions = _CountingCompletions()

    class _CountingClient(_FakeLocalClient):
        chat = _CountingChat()

    return _CountingClient()


def _counting_bad_client(calls):
    class _CountingCompletions(_FakeBadChatCompletions):
        def create(self, **kwargs):
            calls["n"] += 1
            return _FakeBadChatCompletion()

    class _CountingChat(_FakeBadChat):
        completions = _CountingCompletions()

    class _CountingClient(_FakeBadLocalClient):
        chat = _CountingChat()

    return _CountingClient()


def _enable_local_model(monkeypatch, client):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:1.5b")
    monkeypatch.setattr(
        BondAnalystAgent, "_create_openai_client", lambda self, api_key, **kwargs: client
    )


def test_explain_result_id_second_request_reuses_cache_no_second_call(monkeypatch):
    """Server-side idempotency: two POSTs for one result_id bill the model once."""
    calls = {"n": 0}
    _enable_local_model(monkeypatch, _counting_good_client(calls))
    result_id = _seed_result()
    client = _client()
    first = client.post("/api/agent/explain", json={"result_id": result_id}).get_json()
    second = client.post("/api/agent/explain", json={"result_id": result_id}).get_json()
    assert first["status"] == "success"
    assert second["status"] == "success"
    assert second.get("cached") is True
    assert second["text"] == first["text"]
    assert second["guardrail_status"] == "passed"
    assert calls["n"] == 1


def test_explain_failure_not_cached_explicit_retry_calls_model(monkeypatch):
    calls = {"n": 0}
    _enable_local_model(monkeypatch, _counting_bad_client(calls))
    result_id = _seed_result()
    client = _client()
    first = client.post("/api/agent/explain", json={"result_id": result_id}).get_json()
    assert first["status"] == "guardrail_failed"
    entry = app_module._load_result(result_id)
    assert "ai_explanation" not in entry["result"]

    # Failure is never cached, so an explicit retry must invoke the model again.
    _enable_local_model(monkeypatch, _counting_good_client(calls))
    second = client.post("/api/agent/explain", json={"result_id": result_id}).get_json()
    assert second["status"] == "success"
    assert calls["n"] == 2


def test_explain_bond_path_repeat_click_reuses_cached_call(monkeypatch):
    calls = {"n": 0}
    _enable_local_model(monkeypatch, _counting_good_client(calls))
    client = _client()
    payload = {"name": "23附息国债26", "data_mode": "static"}
    first = client.post("/api/agent/explain", json=payload).get_json()
    second = client.post("/api/agent/explain", json=payload).get_json()
    assert first["status"] == "success"
    assert second["status"] == "success"
    assert second.get("cached") is True
    assert second["text"] == first["text"]
    assert calls["n"] == 1


def test_explain_imported_data_requires_confirmation_then_proceeds():
    client = _client()
    upload = client.post(
        "/api/import",
        data={"file": (io.BytesIO(CSV_TEXT.encode("utf-8-sig")), "quotes.csv")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    import_id = upload.get_json()["import_id"]

    resp = client.post(
        "/api/agent/explain",
        json={"name": "23附息国债26", "data_mode": "static", "import_id": import_id},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "needs_confirmation"
    assert "结构化摘要" in body["message_zh"]
    assert "确认" in body["message_zh"]

    # Client confirms: the request proceeds; without a model configured it
    # reports unavailable instead of sending anything.
    resp = client.post(
        "/api/agent/explain",
        json={
            "name": "23附息国债26",
            "data_mode": "static",
            "import_id": import_id,
            "confirm_send": True,
        },
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "unavailable"


def test_explain_confirm_send_requires_strict_json_boolean_true():
    client = _client()
    upload = client.post(
        "/api/import",
        data={"file": (io.BytesIO(CSV_TEXT.encode("utf-8-sig")), "quotes.csv")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    import_id = upload.get_json()["import_id"]
    base = {"name": "23附息国债26", "data_mode": "static", "import_id": import_id}

    # Strings, numbers, null and truthy-looking text must never count as consent.
    for bad in ("false", "true", "yes", "1", 1, None):
        resp = client.post("/api/agent/explain", json={**base, "confirm_send": bad})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "needs_confirmation"

    # Only the JSON boolean true proceeds (no model configured → unavailable).
    resp = client.post("/api/agent/explain", json={**base, "confirm_send": True})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "unavailable"


def test_explain_bond_not_found_returns_404():
    resp = _client().post(
        "/api/agent/explain",
        json={"name": "不存在的债券", "data_mode": "static"},
    )
    assert resp.status_code == 404
    assert "未找到" in resp.get_json()["error"]


def test_agent_result_page_renders_ai_panel_contract():
    client = _client()
    resp = client.post(
        "/agent",
        data={"question": "当前样本收益率分布是什么样？", "data_mode": "static", "lang": "zh"},
    )
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # Shared frontend contract: panel + status/body targets + button dataset.
    assert "data-ai-explain" in html
    assert "data-ai-button" in html
    assert "ai-explain.js" in html
