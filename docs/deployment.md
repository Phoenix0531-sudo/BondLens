# Deployment Guide

[English](#english) | [中文](#中文)

## English

BondLens is a Flask application packaged with Docker. The production entrypoint is gunicorn:

```bash
gunicorn -b 0.0.0.0:5000 app:app
```

### Local Docker

```bash
docker compose up --build
```

Open:

```text
http://localhost:5000/agent
```

Health check:

```text
http://localhost:5000/healthz
```

Agent response schema:

```text
http://localhost:5000/api/agent/schema
```

### Environment Variables

```bash
FLASK_ENV=production
SECRET_KEY=change-me-in-production
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini
OPENAI_BASE_URL=
OPENAI_API_STYLE=auto
OPENAI_TIMEOUT_SECONDS=20
BOND_DATA_MODE=auto
BOND_LIVE_CACHE_PATH=
BOND_LIVE_CACHE_MAX_AGE_HOURS=24
BOND_REPLAY_ENABLED=true
BOND_REPLAY_DIR=
```

- `BOND_DATA_MODE=auto` tries AkShare live data first, then cached live snapshot, then the local Excel fallback.
- `OPENAI_API_KEY` is optional. Without it, deterministic fallback output is used.
- `OPENAI_BASE_URL` can point to an OpenAI-compatible local server such as Ollama.
- `OPENAI_TIMEOUT_SECONDS` defaults to `20` so slow local models fail closed into deterministic fallback rather than timing out gunicorn.
- `BOND_REPLAY_ENABLED` controls sanitized run replay summaries for `/replay`. Defaults to `true`.
- `BOND_REPLAY_DIR` defaults to `.tmp/replays`, which is ignored by Git.

### Ollama From Docker

When Docker runs on Windows or macOS, point the container to the host machine:

```bash
set OPENAI_BASE_URL=http://host.docker.internal:11434/v1
set OPENAI_MODEL=qwen2.5:1.5b
set OPENAI_API_STYLE=chat
docker compose up --build
```

The local model is only used after Python tools produce evidence. The LLM answer must pass numeric and risk-language guardrails before it can become the final answer.

### Platform Deployment Notes

For Render, Railway, Fly.io, or similar platforms:

1. Use Dockerfile deployment.
2. Expose port `5000`.
3. Configure the health check path as `/healthz`.
4. Set `SECRET_KEY` in the platform environment.
5. Keep `BOND_DATA_MODE=auto` for live-first behavior or `static` for deterministic demos.
6. Leave `OPENAI_API_KEY` empty if the demo should run without external LLM calls.
7. Keep replay storage ephemeral unless the deployment platform needs persistent demo history.

### Runtime Safety Boundary

BondLens is not an investment advisory system. The API response includes `disclaimer`, `evidence_quality`, `llm_guardrail`, and `data_source` fields so callers can inspect data freshness, missing context, and LLM safety status.

The portfolio UI intentionally renders evidence ledger, answer judge, risk profile, and replay summaries instead of raw JSON/code-like diagnostics. Raw contracts remain available through `/api/agent/query` and `/api/agent/schema`.

## 中文

BondLens 是一个 Flask 应用，通过 Docker 打包。生产入口是 gunicorn：

```bash
gunicorn -b 0.0.0.0:5000 app:app
```

### 本地 Docker

```bash
docker compose up --build
```

打开：

```text
http://localhost:5000/agent
```

健康检查：

```text
http://localhost:5000/healthz
```

Agent 响应结构：

```text
http://localhost:5000/api/agent/schema
```

### 环境变量

```bash
FLASK_ENV=production
SECRET_KEY=change-me-in-production
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini
OPENAI_BASE_URL=
OPENAI_API_STYLE=auto
OPENAI_TIMEOUT_SECONDS=20
BOND_DATA_MODE=auto
BOND_LIVE_CACHE_PATH=
BOND_LIVE_CACHE_MAX_AGE_HOURS=24
BOND_REPLAY_ENABLED=true
BOND_REPLAY_DIR=
```

- `BOND_DATA_MODE=auto` 会先请求 AkShare 实时数据，然后使用实时快照，最后使用本地 Excel 兜底。
- `OPENAI_API_KEY` 是可选项。为空时使用确定性 fallback 输出。
- `OPENAI_BASE_URL` 可以指向 Ollama 等 OpenAI-compatible 本地服务。
- `OPENAI_TIMEOUT_SECONDS` 默认 `20` 秒，本地模型过慢时会安全回退，而不是拖到 gunicorn 超时。
- `BOND_REPLAY_ENABLED` 控制 `/replay` 的运行回放摘要，默认开启。
- `BOND_REPLAY_DIR` 默认是 `.tmp/replays`，该目录不会提交到 Git。

### Docker 连接 Ollama

如果 Docker 运行在 Windows 或 macOS 上，容器需要通过宿主机地址访问本地 Ollama：

```bash
set OPENAI_BASE_URL=http://host.docker.internal:11434/v1
set OPENAI_MODEL=qwen2.5:1.5b
set OPENAI_API_STYLE=chat
docker compose up --build
```

本地模型只在 Python 工具生成证据之后使用。LLM 输出必须通过数字一致性和风险语言 guardrail，才会成为最终答案。

### 平台部署说明

如果部署到 Render、Railway、Fly.io 或类似平台：

1. 使用 Dockerfile 部署。
2. 暴露端口 `5000`。
3. 健康检查路径配置为 `/healthz`。
4. 在平台环境变量中设置 `SECRET_KEY`。
5. 演示实时优先能力时使用 `BOND_DATA_MODE=auto`；需要稳定演示时使用 `static`。
6. 如果不想依赖外部 LLM，保持 `OPENAI_API_KEY` 为空。
7. 除非部署平台需要保留演示历史，否则 replay 存储保持临时即可。

### 运行时安全边界

BondLens 不是投资顾问系统。API 响应包含 `disclaimer`、`evidence_quality`、`llm_guardrail` 和 `data_source` 字段，调用方可以检查数据新鲜度、缺失上下文和 LLM 安全状态。

作品集页面默认展示证据账本、答案评审、风险画像和运行回放摘要，不展示原始 JSON/代码式调试面板。机器可读结构仍然通过 `/api/agent/query` 和 `/api/agent/schema` 提供。
