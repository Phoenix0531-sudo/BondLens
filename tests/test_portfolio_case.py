"""Offline portfolio generation contract; outputs never touch the repository."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_portfolio_case.py"


def run_case(destination, *args):
    env = os.environ.copy()
    env.update({"BOND_DATA_MODE": "live", "OPENAI_API_KEY": "must-not-be-used", "OPENAI_BASE_URL": "http://127.0.0.1:1"})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(destination), *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=90, check=True,
    )


def test_portfolio_case_is_offline_consistent_and_reproducible(tmp_path):
    from bond_agent.evidence_pack import render_evidence_pack_html

    first, second = tmp_path / "first", tmp_path / "second"
    run_case(first)
    run_case(second)
    for item in first.iterdir():
        assert item.read_bytes() == (second / item.name).read_bytes()
    run_case(first, "--check")
    data = json.loads((first / "case-data.json").read_text(encoding="utf-8"))
    pack = json.loads((first / "bond-study.json").read_text(encoding="utf-8"))
    assert data["name"] == "21附息国债03"
    assert data["code"] is None
    assert data["source"]["sample_date"] == "2024-03-22"
    assert data["source"]["row_count"] == 3365
    assert data["target"]["收盘到期收益率(%)"] == 2.68
    assert data["comparison"]["peer_comparison"]["peer_count"] == 19
    assert data["comparison"]["peer_comparison"] == data["agent_comparison"]["peer_comparison"]
    assert pack["question"] == data["question"]
    assert pack["data_lineage"]["runtime_mode"] == "static_sample"
    expected_html = "\n".join(
        line.rstrip() for line in render_evidence_pack_html(pack).splitlines()
    ) + "\n"
    assert (first / "bond-study.html").read_text(encoding="utf-8") == expected_html
