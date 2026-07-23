from pathlib import Path

from bond_agent import BondAnalystAgent
from bond_agent.evidence_pack import DEMO_PACK_DIR, export_evidence_pack
from bond_agent.stress_view import build_stress_view
from bond_agent.trust_score import compute_trust_score


def test_trust_score_penalizes_static_sample(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BOND_EVIDENCE_PACK_ENABLED", "false")

    result = BondAnalystAgent(data_mode="static").answer("当前样本收益率分布是什么样？")
    trust = result["trust_score"]
    stress = result["stress_view"]

    assert 0 <= trust["score"] <= 100
    assert trust["level"] in {"low", "medium", "high"}
    assert trust["summary_zh"]
    assert any(item["id"] == "data_freshness" for item in trust["adjustments"])
    assert any(item["delta"] < 0 and item["id"] == "data_freshness" for item in trust["adjustments"])
    assert stress["severity"] in {"low", "medium", "high"}
    assert stress["summary_zh"]
    assert any(item["id"].startswith("data_") for item in stress["signals"])


def test_agent_exports_evidence_pack(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BOND_EVIDENCE_PACK_ENABLED", "true")
    monkeypatch.setenv("BOND_EVIDENCE_PACK_DIR", str(tmp_path))

    result = BondAnalystAgent(data_mode="static").answer("按收益率列出最高的前5只债券")

    assert result["trust_score"]["score"] is not None
    assert result["stress_view"]["severity"]
    assert result["evidence_pack_id"]
    assert result["evidence_pack_paths"]["json_path"]
    assert result["evidence_pack_paths"]["html_path"]
    assert Path(result["evidence_pack_paths"]["json_path"]).exists()
    assert Path(result["evidence_pack_paths"]["html_path"]).exists()
    assert "非投资建议，仅用于学习和研究。" in result["limitations"]
    assert "收益率高低是风险信号，不是买卖依据。" in result["limitations"]


def test_evidence_pack_html_contains_core_sections(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BOND_EVIDENCE_PACK_ENABLED", "false")

    result = BondAnalystAgent(data_mode="static").answer("有没有收益率异常的债券？")
    exported = export_evidence_pack(result, directory=tmp_path, pack_id="demo-outlier")
    html = Path(exported["html_path"]).read_text(encoding="utf-8")

    assert "Bond Evidence Pack" in html
    assert "信任分" in html or "Trust" in html
    assert "证据账本" in html
    assert "局限性" in html
    assert "运行压力" in html or "Stress" in html
    assert result["question"] in html


def test_compute_trust_score_guardrail_failure_hurts():
    base = {
        "data_source": {"runtime_mode": "live", "fallback_reason": None},
        "evidence_quality": {"score": 80, "level": "high"},
        "llm_guardrail": {"status": "failed"},
        "answer_judge": {"status": "failed_guardrail"},
        "final_answer_source": "deterministic_fallback",
        "evidence_ledger": [{"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}],
    }
    failed = compute_trust_score(**base)
    base["llm_guardrail"] = {"status": "passed"}
    base["answer_judge"] = {"status": "passed"}
    base["final_answer_source"] = "llm"
    passed = compute_trust_score(**base)
    assert failed["score"] < passed["score"]


def test_stress_view_marks_static_as_review():
    stress = build_stress_view(
        data_source={"runtime_mode": "static_sample", "fallback_reason": None},
        trust_score={"score": 45, "level": "low"},
        llm_guardrail={"status": "not_run"},
        answer_judge={"status": "not_applicable"},
        final_answer_source="deterministic_fallback",
        evidence_quality={"score": 60, "level": "medium"},
    )
    assert stress["severity"] in {"medium", "high"}
    assert stress["requires_review"] is True
    assert any(item["id"] == "data_static" for item in stress["signals"])
    assert any(item["id"] == "trust_low" for item in stress["signals"])


def test_pack_route_serves_exported_html(monkeypatch, tmp_path):
    from app import app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BOND_EVIDENCE_PACK_ENABLED", "true")
    monkeypatch.setenv("BOND_EVIDENCE_PACK_DIR", str(tmp_path))

    result = BondAnalystAgent(data_mode="static").answer("当前样本收益率分布是什么样？")
    pack_id = result["evidence_pack_id"]
    client = app.test_client()
    resp = client.get(f"/packs/{pack_id}.html")
    assert resp.status_code == 200
    assert b"Bond Evidence Pack" in resp.data


def test_demo_pack_dir_constant():
    assert DEMO_PACK_DIR.name == "demo_runs"
    assert "docs" in DEMO_PACK_DIR.parts
