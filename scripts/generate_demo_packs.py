"""Generate static Bond Evidence Pack demos under docs/demo_runs/."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bond_agent import BondAnalystAgent
from bond_agent.evidence_pack import DEMO_PACK_DIR, export_evidence_pack


CASES = [
    {
        "pack_id": "demo-market-overview",
        "question": "当前样本收益率分布是什么样？",
        "label": "Market overview with static sample",
    },
    {
        "pack_id": "demo-bond-report",
        "question": "搜索23附息国债26并给出收益率分析",
        "label": "Single-bond report",
    },
    {
        "pack_id": "demo-yield-outliers",
        "question": "有没有收益率异常的债券？",
        "label": "Outlier detection",
    },
]


def main() -> None:
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["BOND_EVIDENCE_PACK_ENABLED"] = "false"
    os.environ.setdefault("BOND_REPLAY_ENABLED", "false")

    DEMO_PACK_DIR.mkdir(parents=True, exist_ok=True)
    index_rows = []
    for case in CASES:
        result = BondAnalystAgent(data_mode="static").answer(case["question"])
        exported = export_evidence_pack(
            result,
            pack_id=case["pack_id"],
            directory=DEMO_PACK_DIR,
            write_html=True,
        )
        trust = result.get("trust_score") or {}
        index_rows.append(
            {
                "id": case["pack_id"],
                "question": case["question"],
                "label": case["label"],
                "trust": trust.get("score"),
                "html": Path(exported["html_path"]).name,
                "json": Path(exported["json_path"]).name,
            }
        )
        print(f"wrote {exported['html_path']} trust={trust.get('score')}")

    index_path = DEMO_PACK_DIR / "index.md"
    lines = [
        "# Bond Evidence Pack demos",
        "",
        "Static packs generated with `data_mode=static` and no LLM key.",
        "Open any `.html` file offline — no API key required.",
        "",
        "| Pack | Question | Trust | Files |",
        "| --- | --- | --- | --- |",
    ]
    for row in index_rows:
        lines.append(
            f"| `{row['id']}` | {row['question']} | {row['trust']}/100 | "
            f"[{row['html']}](./{row['html']}) · [{row['json']}](./{row['json']}) |"
        )
    lines.append("")
    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {index_path}")


if __name__ == "__main__":
    main()
