#!/usr/bin/env python3
"""Generate an offline, reproducible portfolio case from BondLens' static sample."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NAME = "21附息国债03"
QUESTION = f"分析{NAME}并与同类债券比较"
FILES = ("bond-study.html", "bond-study.json", "case-data.json", "CASE_STUDY.md", "CASE_STUDY.zh-CN.md")


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = cls(2024, 3, 22, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


def no_network(*args, **kwargs):
    raise RuntimeError("Portfolio generation forbids network access")


def generate(output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="bondlens-portfolio-isolated-") as isolated:
        os.environ.update({
            "BOND_DATA_MODE": "static", "BOND_REPLAY_ENABLED": "false",
            "BOND_EVIDENCE_PACK_ENABLED": "false", "OPENAI_API_KEY": "",
            "OPENAI_BASE_URL": "", "BOND_REPLAY_DIR": str(Path(isolated) / "history"),
            "BOND_EVIDENCE_PACK_DIR": str(Path(isolated) / "packs"),
        })
        # Imports must follow environment isolation: app caches configuration at import time.
        with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
            from app import app
            from bond_agent.agent import BondAnalystAgent
            from bond_agent.evidence_pack import export_evidence_pack

            client = app.test_client()
            params = {"data_mode": "static", "name": NAME}
            search = client.get("/api/bonds/search", query_string=params)
            detail = client.get("/api/bond/detail", query_string=params)
            if search.status_code != 200 or detail.status_code != 200:
                raise RuntimeError("Static workbench API failed")
            search, detail = search.get_json(), detail.get_json()
            result = BondAnalystAgent(data_mode="static").answer(QUESTION)
            if result.get("used_llm") or search["match_count"] != 1 or not detail["comparison"]["found"]:
                raise RuntimeError("Unexpected model use or ambiguous/missing target")
            if result["data_evidence"]["comparison"]["peer_comparison"] != detail["comparison"]["peer_comparison"]:
                raise RuntimeError("Agent and workbench peer comparisons differ")
            output.mkdir(parents=True, exist_ok=True)
            with patch("bond_agent.evidence_pack.datetime", FixedClock):
                export_evidence_pack(result, pack_id="bond-study", directory=output)
            # The renderer's HTML uses indented multiline blocks, including
            # whitespace-only blank lines. Strip only line-end whitespace in
            # the committed static artifact; its markup and data are unchanged.
            html_file = output / "bond-study.html"
            html_content = html_file.read_text(encoding="utf-8")
            html_file.write_text(
                "\n".join(line.rstrip() for line in html_content.splitlines()) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            source, record, comparison = detail["data_source"], detail["target"], detail["comparison"]
            peer = comparison["peer_comparison"]
            data = {
                "question": QUESTION, "name": NAME, "code": None,
                "source": {k: source[k] for k in ("source_id", "source_name", "sample_date", "row_count", "valid_yield_count", "has_code_column", "distinct_date_count")},
                "search_match_count": search["match_count"], "target": record,
                "comparison": comparison, "agent_tools_used": result["tools_used"],
                "agent_comparison": result["data_evidence"]["comparison"],
            }
            (output / "case-data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            zh = f"""# 单券研究案例：{NAME}

[英文](CASE_STUDY.md) · [静态证据包](bond-study.html) · [数据与比较 JSON](case-data.json) · [证据 JSON](bond-study.json)

## 问题与来源

> {QUESTION}

静态仓库样本 `{source['source_name']}`，样本日期 {source['sample_date']}，共 {source['row_count']} 条记录、{source['valid_yield_count']} 条有效收益率；不是实时行情。名称精确匹配 {search['match_count']} 条，样本没有证券代码列，代码缺失。记录按行情行计数，并非保证唯一债券数。

## 观察

目标为{record['券种']}，待偿期 {record['待偿期(年)']} 年（{record['期限分桶']}）；收盘净价 {record['收盘净价(元)']} 元，收盘到期收益率 {record['收盘到期收益率(%)']}%，加权收益率 {record['加权收益率(%)']}%，成交量 {record['交易量(亿元)']} 亿元。这些数值直接取自 `/api/bond/detail`；API 的派生数值已按其输出精度舍入。

`compare_bond_to_market` 以相同券种、相同期限桶且排除目标券的 {peer['peer_count']} 条记录为同业：平均收益率 {peer['peer_yield_mean']}%，中位数 {peer['peer_yield_median']}%，目标收益率在同业中的百分位为 {peer['peer_yield_percentile']}，高于同业平均 {peer['spread_vs_peer_mean_bp']} bp。此百分位是工具计算的相对位置，不是违约概率。样本没有逐行行情日期列，无法验证是否同日；展示的样本日期不能替代逐行日期。

教学假设下修正久期 {comparison['rate_sensitivity']['modified_duration']}，DV01 {comparison['rate_sensitivity']['dv01']}。计算采用年付息、票息默认等于到期收益率的平价假设和整数期数，并非完整估值或真实现金流。

## 路径与局限

固定问题经规则规划运行工具：`{' → '.join(result['tools_used'])}`。工作台和代理调用相同的比较工具；本次两者同业输出一致。静态证据包使用固定生成时间 2024-03-22T00:00:00+00:00 以便复现，该时间不是实际运行时间或行情时间。证据包 JSON 与 HTML 来自同一导出对象。

不含主体评级、财报、担保或信用事件；收益率差异不能解释为信用风险结论。该案例仅供研究和学习，不是投资建议。重现：在仓库根目录运行 `python scripts/generate_portfolio_case.py`；`--check` 只校验不改写。
"""
            en = f"""# Bond research case: {NAME}

[中文](CASE_STUDY.zh-CN.md) · [Static evidence pack](bond-study.html) · [Data and comparison JSON](case-data.json) · [Evidence JSON](bond-study.json)

## Question and source

> {QUESTION}

Repository static sample `{source['source_name']}`, sample date {source['sample_date']}: {source['row_count']} records, including {source['valid_yield_count']} valid yields. This is not a live quote. The name has {search['match_count']} exact match; the sample has no security-code column, so the code is missing. Counts represent quote rows, not necessarily unique securities.

## Observation

The target is a Chinese government bond with {record['待偿期(年)']} years remaining ({record['期限分桶']} bucket). Its closing clean price is {record['收盘净价(元)']} yuan, closing yield {record['收盘到期收益率(%)']}%, weighted yield {record['加权收益率(%)']}%, and volume {record['交易量(亿元)']} hundred million yuan. Values come directly from `/api/bond/detail`; derived figures retain the API's rounded output precision.

`compare_bond_to_market` compares {peer['peer_count']} other records of the same bond type and maturity bucket. Their mean yield is {peer['peer_yield_mean']}% and median {peer['peer_yield_median']}%; the target's peer yield percentile is {peer['peer_yield_percentile']}, and its spread over the mean is {peer['spread_vs_peer_mean_bp']} bp. This percentile is a tool-calculated relative position, not a default probability. The sample has no per-row quote date column: the sample date does not establish same-day alignment.

Under teaching assumptions, modified duration is {comparison['rate_sensitivity']['modified_duration']} and DV01 is {comparison['rate_sensitivity']['dv01']}. These use annual coupons, coupon defaulting to yield under a par assumption, and whole periods; they are not a full valuation or actual cash-flow schedule.

## Path and limitations

The fixed question follows the rule-based tool path: `{' → '.join(result['tools_used'])}`. The workbench and agent call the same comparison tool; their peer outputs match in this case. The static pack uses a fixed generation timestamp of 2024-03-22T00:00:00+00:00 for reproducibility, not the actual execution or quote time. Its JSON and HTML are exported from the same object.

Issuer ratings, financial statements, guarantees and credit events are absent; yield spreads alone cannot establish credit risk. This case is for research and education, not investment advice. Reproduce from the repository root with `python scripts/generate_portfolio_case.py`; `--check` verifies without writing outputs.
"""
            # Screenshots are captured separately from real browser pages. The
            # generated prose references those fixed assets without pretending
            # the screenshots or their transient history IDs are deterministic.
            zh += """
## 同一研究对象的界面证据

| 工作台单券比较 | 确定性分析结果 |
| :---: | :---: |
| [![21附息国债03的同业比较](screenshots/03-bond.png)](screenshots/03-bond.png) | [![21附息国债03的研究结论](screenshots/04-agent.png)](screenshots/04-agent.png) |
| [筛选入口](screenshots/02-screen.png) · [市场来源](screenshots/01-market.png) | [证据包画面](screenshots/06-evidence-pack.png) · [隔离历史画面](screenshots/05-history.png) |

这些图片从真实本地程序抓取，截图可能含不同的临时结果 ID；计算指标以本案例 JSON 与 HTML 证据包为准。可看 [60 秒无声截图导览](walkthrough.mp4)，它不是实际点击操作的录屏。
"""
            en += """
## Screenshots for this same bond

| Single-bond comparison | Deterministic research result |
| :---: | :---: |
| [![Same-bond peer comparison](screenshots/03-bond.png)](screenshots/03-bond.png) | [![Deterministic answer for the same bond](screenshots/04-agent.png)](screenshots/04-agent.png) |
| [Screening](screenshots/02-screen.png) · [Market source](screenshots/01-market.png) | [Evidence pack view](screenshots/06-evidence-pack.png) · [Isolated history](screenshots/05-history.png) |

These images were captured from the real local application; ephemeral result IDs can differ between captures. The case JSON and HTML evidence pack are the source of truth for numeric results. The [60-second silent screenshot tour](walkthrough.mp4) is not a recording of live clicks.
"""
            (output / "CASE_STUDY.zh-CN.md").write_text(zh, encoding="utf-8", newline="\n")
            (output / "CASE_STUDY.md").write_text(en, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare generated output without modifying destination")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs" / "portfolio_case")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="bondlens-portfolio-build-") as temp:
        generated = Path(temp)
        generate(generated)
        if args.check:
            different = [name for name in FILES if not (args.output_dir / name).is_file() or (generated / name).read_bytes() != (args.output_dir / name).read_bytes()]
            if different:
                print("Missing or different: " + ", ".join(different), file=sys.stderr)
                return 1
            print("Portfolio case matches generated output")
        else:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            for name in FILES:
                shutil.copyfile(generated / name, args.output_dir / name)
            print(f"Wrote portfolio case to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
