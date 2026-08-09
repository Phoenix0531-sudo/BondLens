# BondLens: An Evidence-First Bond Analysis Agent for Chinese Market Data

[English](README.md) | [中文](README.zh-CN.md)

<div align="center">

<img src="docs/figs/voxel_icon.png" width="192" alt="BondLens badge — hex prism focusing a yield curve, amber peak marks the inspected claim"/>

**A claim-level evidence agent for Chinese bonds**  
Chinese bonds · deterministic tools · provenance-tracked · optional LLM narration under guardrail.

![CI](https://github.com/Phoenix0531-sudo/BondLens/actions/workflows/ci.yml/badge.svg?style=flat-square)
![Agent Evals (manual)](https://img.shields.io/badge/agent%20evals%20(manual)-10%2F10-brightgreen?style=flat-square)
![Red Team (manual)](https://img.shields.io/badge/red--team%20(manual)-3%2F3-brightgreen?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg?style=flat-square)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)
![Flask](https://img.shields.io/badge/Flask-3.x-green.svg?style=flat-square)
![Tests](https://img.shields.io/badge/tests-pytest%2Bevals-informational?style=flat-square)
![Docker](https://img.shields.io/badge/docker-healthz-blue?style=flat-square)
![i18n](https://img.shields.io/badge/i18n-zh%2Fen-teal?style=flat-square)
![Data](https://img.shields.io/badge/data-AkShare%20live%2Fsnapshot%2Fstatic-orange?style=flat-square)
![Pages](https://img.shields.io/badge/project%20page-GitHub%20Pages-222?style=flat-square)

**BondLens** turns a natural-language bond question into an **auditable analysis run**:  
live / snapshot / static data → deterministic tools → optional LLM narration → Trust Layer.

[Project page](https://phoenix0531-sudo.github.io/BondLens/) · [Social preview](docs/figs/voxel_social.png) · light badge: [logo](docs/figs/logo_white_background.png)

> Non-investment advice. For learning, research, portfolio demonstration, and interview discussion only.

</div>

### Example Runs (no API key — open in browser)

| Run | What you see | Open |
| --- | --- | --- |
| Market overview | Sample yield / volume board + trust-facing pack | [demo-market-overview.html](docs/demo_runs/demo-market-overview.html) |
| Single-bond report | First-bond style report with evidence body | [demo-bond-report.html](docs/demo_runs/demo-bond-report.html) |
| Yield outliers | Cross-section outlier monitor pack | [demo-yield-outliers.html](docs/demo_runs/demo-yield-outliers.html) |
| LLM final-answer matrix | Recorded CPA path (zh/en × overview/bond) | [llm_matrix_cpa_gpt54.md](docs/demo_runs/llm_matrix_cpa_gpt54.md) |

Raw JSON siblings live under [docs/demo_runs/](docs/demo_runs/).

---

## Table of Contents

- [Scope](#scope)
- [Design Principle](#design-principle-deterministic-compute-llm-narration)
- [Architecture](#architecture)
- [Screenshots](#screenshots)
- [Quick Start](#quick-start)
- [Language (i18n)](#language-i18n)
- [Tool Catalog](#tool-catalog-deterministic-operators)
- [Trust Score & Evidence Pack](#trust-score--evidence-pack)
- [Example Questions](#example-questions)
- [API](#api)
- [Data Source Boundary](#data-source-boundary)
- [LLM matrix (recorded)](docs/demo_runs/llm_matrix_cpa_gpt54.md)
- [Background](#background)
- [License](#license)
- [Disclaimer](#disclaimer)

---

## Scope

| In scope | Out of scope |
| --- | --- |
| Chinese bond market questions in natural language | Multi-agent equity research desktop |
| Live / snapshot / static data with explicit lineage | Issuer ratings, financials, guarantees, credit events |
| Deterministic yield / volume / ranking / outlier tools | Trade recommendations or buy/sell signals |
| Optional LLM narration under numeric + language guardrails | Model inventing numbers outside tool evidence |
| Trust score, Evidence Pack, replay, red-team evals | Full OAS / call-tree valuation desk |
| Bilingual UI (zh default) + portable demo packs | Complete market coverage claims |

**Honest scale:** ~10k lines of project Python (`bond_agent/` + app/tests/evals) — a focused vertical, not a 100k+ multi-agent platform.

---

## Design Principle: Deterministic Compute, LLM Narration

BondLens strictly separates **deterministic financial computation** (tools never invent numbers) from **LLM-based narration** (text only, numbers must match evidence). See the [Tool Catalog](#tool-catalog-deterministic-operators) for what tools produce deterministically, and the [Trust Score](#trust-score--evidence-pack) layer for the guardrail + judge that gates narrative text.

**Tools compute, models narrate, trust decides.** Without `OPENAI_API_KEY`, the project still runs with deterministic fallback output.

### Codebase Snapshot

| Layer | What it includes |
| --- | --- |
| **Agent core** | Single path: Planner → Tools → Evidence → Report |
| **Deterministic tools** | 7 public operators: `search_bonds`, `describe_market`, `rank_bonds`, `detect_yield_outliers`, `compare_bond_to_market`, `build_market_monitor`, `generate_bond_report` |
| **Trust layer** | Numeric + language guardrail · answer judge · Trust score · Evidence Pack · replay store · risk profile |
| **Evals** | ~110 pytest cases · Docker `/healthz` in CI; see badges above for the live run. |
| **Data** | AkShare live → cached snapshot → static Excel, with explicit lineage and maturity coverage board |
| **Product surface** | Flask + Jinja · zh-default / en switch (query+cookie) · SSE soft-render · CI + GitHub Pages |

---

### Product surfaces (answer-first)

- **Answer Snapshot** + SSE soft final render: tool-step progress, token preview, final summary card without forced full-page reload; share/full board still via `result_url`
- **Bilingual UI (zh default)** + maturity/residual boards: high yield / low volume / yield outliers / missing maturity; same-type + same-bucket peer comparison
- **Trust score + stress view + audit folds**: guardrail / judge / risk / ledger behind `<details>`; replay dashboard for past runs

See the [Screenshots](#screenshots) below for each of these in action.

---

## Architecture

<div align="center">
<img src="docs/figs/architecture.png" width="92%" alt="BondLens architecture: Question → Resolver → Planner → Tools → Evidence → Guardrail → Trust">
</div>

```mermaid
flowchart TD
    A[User Question] --> B[Data Source Resolver]
    B --> C[Planner multi-intent]
    C --> D[Deterministic Tools]
    D --> E[Structured Evidence]
    E --> F[Report + Limitations]
    F --> G{Optional LLM}
    G -->|guardrail pass| H[Narrated answer]
    G -->|fail or disabled| I[Deterministic answer]
    H --> J[Judge + Trust + Pack + Replay]
    I --> J
```

See the [Tool Catalog](#tool-catalog-deterministic-operators) for the 7 deterministic operators used by `Planner → Tools`.

---

## Screenshots

Captured on the current live agent page (`BOND_DATA_MODE=auto`, no API key → deterministic final answers).
Narrative: **can answer · can go deep · will refuse · can rank · can switch languages · can audit**.

Current product shots live in `docs/screenshots/current/`; the root screenshots folder now only keeps the GitHub social preview asset.

<table>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/current/01-agent-zh-home.png" alt="Chinese BondLens agent console with a single header language switch">
      <br><strong>Default Chinese — clean agent console</strong>
      <br>Single header language switch; no duplicate console selector.
    </td>
    <td width="50%">
      <img src="docs/screenshots/current/02-overview-zh-live.png" alt="Chinese market overview with live trust score, evidence, monitor board and data lineage">
      <br><strong>Can answer — market overview</strong>
      <br><code>当前债券市场样本概览如何？</code>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/current/03-bond-report-zh-clean.png" alt="Chinese single-bond report request and trust panel">
      <br><strong>Can go deep — single-bond report</strong>
      <br><code>请对样本中第一只债券生成分析报告</code>
    </td>
    <td width="50%">
      <img src="docs/screenshots/current/04-advisory-refusal-zh.png" alt="Advisory policy block without LLM">
      <br><strong>Will refuse — advisory policy block</strong>
      <br><code>今天该不该买债？</code> → Trust ≤72, no LLM
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/current/05-ranking-zh-live.png" alt="Chinese highest-yield ranking result with evidence metrics">
      <br><strong>Can rank — high-yield evidence list</strong>
      <br><code>收益率最高的债券是哪只？</code>
    </td>
    <td width="50%">
      <img src="docs/screenshots/current/06-agent-en-home.png" alt="English BondLens agent console with EN selected">
      <br><strong>English UI — same console, switched language</strong>
      <br>Header `EN` drives the product surface.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/current/07-overview-en-live.png" alt="English market overview result with trust score and deterministic final source">
      <br><strong>English answer path — overview</strong>
      <br><code>Give an overview of the current bond market sample.</code>
    </td>
    <td width="50%">
      <img src="docs/screenshots/current/08-replay-dashboard.png" alt="Replay dashboard for auditable run history">
      <br><strong>Can audit — replay / evidence path</strong>
      <br>Replay dashboard for past runs (traceable output).
    </td>
  </tr>
</table>

---

## Quick Start

### 0 minutes (no install)

Open a pre-generated Example Run in the browser — no server, no API key:

```text
docs/demo_runs/demo-market-overview.html
```

Or jump from the [Example Runs table](#example-runs-no-api-key--open-in-browser) above.

### 5 minutes (offline demo)

```bash
pip install -r requirements.txt
./scripts/run_demo.sh
# open http://127.0.0.1:8765/agent
# try: 当前样本收益率分布是什么样？
```

Windows users can run the same deterministic demo with:

```bat
scripts\run_demo.bat
```

Other packs:

- [demo-market-overview.html](docs/demo_runs/demo-market-overview.html)
- [demo-bond-report.html](docs/demo_runs/demo-bond-report.html)
- [demo-yield-outliers.html](docs/demo_runs/demo-yield-outliers.html)

### 30 minutes (live path + fallback)

```bash
export FLASK_RUN_HOST=127.0.0.1
export PORT=8765
export BOND_DATA_MODE=auto   # live first, then snapshot, then static
python app.py
# force live: BOND_DATA_MODE=live
# watch data_source.runtime_mode, maturity board, and trust score when live degrades
```

Optional LLM polish (never required):

```bash
export OPENAI_API_KEY=... OPENAI_BASE_URL=http://127.0.0.1:18317/v1   # example: local CPA/OpenAI-compatible gateway
export OPENAI_MODEL=haochi/gpt-5.4
export OPENAI_API_STYLE=chat
export OPENAI_MODEL_FALLBACKS=haochi/gpt-5.4-mini,gongyi/deepseek-v4-flash-search   # optional
# Keys stay in process env only. Do not commit secrets.
```

### Docker demo

BondLens is Docker-packaged, but the portfolio demo does not require Docker.
The Compose service is intentionally named `bondlens`, with container name `bondlens-demo`, image `bondlens:local`, and host port `8765` mapped to container port `5000`.

```bash
docker compose up --build
# open http://localhost:8765/agent
# health: http://localhost:8765/healthz
```

---

## Language (i18n)

- Default UI language: **Chinese**
- Explicit switch in one place: the page header (`中 / EN`)
- Persistence: `?lang=zh|en` query wins, then `bondlens_lang` cookie, else `zh` (localStorage mirrors client-side)
- Covers templates, intent/tool labels, deterministic report skeleton, advisory refusal, and flash/error copy
- LLM system prompts follow the active language; logs stay developer-facing and are not fully bilingual

## Tool Catalog (deterministic operators)

| Tool | Inputs | Deterministic outputs |
| --- | --- | --- |
| `search_bonds` | name / type / maturity / yield filters | match_count, records |
| `describe_market` | active frame | yield/volume summaries, segments, data quality |
| `rank_bonds` | by, top_n, order | ranked records |
| `detect_yield_outliers` | method, threshold | outlier_count, scores |
| `compare_bond_to_market` | bond / record | percentiles, peer comparison |
| `build_market_monitor` | top_n | high-yield / low-volume / outliers / missing maturity |
| `generate_bond_report` | tool outputs + plan | analysis, risk notes, limitations |

Numbers in the final answer must come from these tools (or be rejected by the guardrail).

---

## Trust Score & Evidence Pack

Each answer includes `trust_score` (0–100) built from evidence quality, data freshness/degradation,
ledger coverage, guardrail outcome, judge outcome, and a forced non-advisory penalty.

Every run can export a portable **Bond Evidence Pack** (JSON + static HTML):

- question / intent / tools
- data lineage + maturity coverage
- trust score + adjustments
- guardrail + judge + risk profile
- evidence ledger + final answer
- mandatory limitations

```bash
python scripts/generate_demo_packs.py
```

Runtime packs: `.tmp/evidence_packs/`  
Committed demos: [docs/demo_runs/](docs/demo_runs/)

### Maturity unmatched export

Live/snapshot feeds have **no native maturity field**. BondLens enriches matched names from the local security master and exposes:

```text
GET/POST /api/maturity/unmatched?format=csv&data_mode=static
GET/POST /api/maturity/unmatched?format=json&data_mode=static
```

The UI maturity board links to the same export for unmatched bond names.

---

## Example Questions

```text
当前样本收益率分布是什么样？
搜索23附息国债26并给出收益率分析
打开今日市场监控面板：高收益、低成交与异常
按收益率列出最高的前5只债券
有没有收益率异常的债券？
筛选国债收益率大于 2.5 的债券
今天该不该买债？   # advisory policy block; no LLM
```

---

## API

```http
POST /api/agent/query
Content-Type: application/json

{
  "question": "搜索23附息国债26并给出收益率分析",
  "data_mode": "auto"
}
```

Streaming (SSE):

```http
POST /api/agent/stream
Content-Type: application/json

{
  "question": "当前债券市场样本概览如何？",
  "data_mode": "static"
}
```

Events include `status` (tool steps), `token` (partial text), and `final` (soft-render view + `result_url`).

Operational endpoints:

```text
GET  /healthz
GET  /api/agent/schema
GET  /replay
GET  /packs/<pack_id>.html
GET  /packs/<pack_id>.json
GET  /api/maturity/unmatched
```

Deployment notes: [docs/deployment.md](docs/deployment.md)

---

## Data Source Boundary

```text
Primary:        ChinaMoney / AkShare-style spot deal fetch (direct preferred)
Snapshot:       .tmp/bond_spot_deal_snapshot.csv
Final fallback: data/testdata.xlsx
```

- Live fields used include bond name, clean price, yield, BP change, weighted yield, volume, and native residual maturity when present (`termToMaturity`)
- Residual maturity may still be incomplete → coverage board + trust penalty on weak coverage
- Cashflow duration / DV01 are **teaching-level** level-coupon estimates, not OAS / full call-tree valuation
- Perpetual-style residuals expose dual scenarios (first finite leg + theoretical perpetual), not a multi-century fake tenor
- No issuer ratings, financial statements, guarantees, or credit events
- Yield is a **risk signal**, not a trade instruction

Modes:

```text
auto   -> live first, cached snapshot second, local fallback third
live   -> live source requested; fallback reason shown if it degrades
static -> local Excel only
```

---

## Recorded LLM working path

A recorded CPA/OpenAI-compatible-gateway LLM path (zh/en x overview/bond-report) and its honest residuals live in [docs/demo_runs/llm_matrix_cpa_gpt54.md](docs/demo_runs/llm_matrix_cpa_gpt54.md). This is evidence of a working path, **not** a zero-bug claim; provider channel churn can still force deterministic fallback when models vanish, and the product still works without an API key.

---

## Background

This project started as a 2024 undergraduate thesis: a Flask-based bond data analysis system.
The original thesis version is preserved and should not be rewritten:

- Original thesis branch: `undergraduate-thesis-2024`
- Current branch: `main`

## License

MIT

## Disclaimer

BondLens is an engineering and research demonstration. It does not provide investment advice,
does not claim complete market coverage, and does not replace professional fixed-income research tools.
