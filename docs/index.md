# BondLens：面向中国债券市场的专业研究工作台

<div align="center">

<img src="figs/voxel_icon.png" width="168" alt="BondLens 深色徽标：聚焦收益率曲线的六棱镜"/>

**数字由代码计算，叙述按需生成且必须通过护栏，每次输出都可追溯。**

![CI](https://github.com/Phoenix0531-sudo/BondLens/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Flask](https://img.shields.io/badge/Flask-3.x-green.svg)
![Tests](https://img.shields.io/badge/tests-212%20passed%20%2F%201%20skipped-informational)
![i18n](https://img.shields.io/badge/i18n-zh%2Fen-teal)
![License](https://img.shields.io/badge/License-MIT-blue.svg)

</div>

BondLens 是面向**中国债券市场**的专业研究工作台：
市场总览、条件筛选、单券研究、研究历史四类页面，外加可选的自然语言分析入口；
公开行情 → 快照 → 本地样例逐级回退（支持 CSV/Excel 上传），数据来源与行情日期全程可见；
AI 解读默认关闭，仅在结果页显式点击后发起单次请求。

**本页为 GitHub Pages 静态介绍页；交互应用请按 README 本地运行。**

## 工作台页面

| 页面 | 路径 | 内容 |
| --- | --- | --- |
| 市场总览 | `/market` | 收益率分布、成交量、来源/日期摘要 |
| 条件筛选 | `/screen` | 多条件过滤 + CSV/Excel 样本上传 |
| 单券研究 | `/bond` | 关键指标、同业对比、按需 AI 解读面板 |
| 研究历史 | `/replay` | 既往运行摘要、证据包入口 |
| 自然语言分析（可选） | `/agent` | 一句话问题 → 可审计分析运行 |

## 示例报告（无需 API key，浏览器直接打开）

| Run | Open |
| --- | --- |
| Market overview | [demo-market-overview.html](demo_runs/demo-market-overview.html) |
| Single-bond report | [demo-bond-report.html](demo_runs/demo-bond-report.html) |
| Yield outliers | [demo-yield-outliers.html](demo_runs/demo-yield-outliers.html) |
| LLM final-answer matrix | [llm_matrix_cpa_gpt54.md](https://github.com/Phoenix0531-sudo/BondLens/blob/main/docs/demo_runs/llm_matrix_cpa_gpt54.md) |

## Codebase Snapshot

| Layer | What it includes |
| --- | --- |
| Workbench | Market / screener / bond / history pages · zh/en · CSV/Excel import |
| Deterministic tools | 7 public operators · numbers never invented by the model |
| Trust layer | Guardrail · judge · Trust score · Evidence Pack · replay |
| Evals | 212 pytest passed / 1 skipped · agent + red-team evals · Docker healthz |
| Data | live → snapshot → static with explicit lineage |

## Quick links

- Repository: <https://github.com/Phoenix0531-sudo/BondLens>
- Runtime & deployment (README): <https://github.com/Phoenix0531-sudo/BondLens/blob/main/README.md#runtime-and-deployment>
- English README: <https://github.com/Phoenix0531-sudo/BondLens/blob/main/README.md>
- 中文 README: <https://github.com/Phoenix0531-sudo/BondLens/blob/main/README.zh-CN.md>
- Architecture diagram: <https://github.com/Phoenix0531-sudo/BondLens/blob/main/docs/figs/architecture.png>
- CI: <https://github.com/Phoenix0531-sudo/BondLens/actions/workflows/ci.yml>

## Local quick start

```bash
cp .env.example .env
docker compose up --build
# or: python -m pip install -r requirements.lock && python app.py
```

## License

MIT. Non-investment advice — learning, research, portfolio demonstration, and interview discussion only.
