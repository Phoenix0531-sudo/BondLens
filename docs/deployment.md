# Deployment Guide

[English](#english) | [中文](#中文)

## English

BondLens is a Flask application packaged with Docker. The production entrypoint is gunicorn:

```bash
gunicorn -b 0.0.0.0:5000 app:app
```

The public portfolio demo normally runs on host port `8765`; Docker maps `8765` on the host to `5000` inside the container.

### Local Python demo

```bash
pip install -r requirements.txt
./scripts/run_demo.sh
# Open http://127.0.0.1:8765/agent
```

Windows:

```bat
scripts\run_demo.bat
```

By default the demo scripts unset `OPENAI_*` so the deterministic fallback path is exercised without secrets. Set `BOND_DEMO_WITH_LLM=1` only when you intentionally want to pass an already-exported LLM environment through.

### Local Docker

```bash
docker compose up --build
```

Compose naming is fixed for a tidy local portfolio environment:

| Item | Value |
| --- | --- |
| Service | `bondlens` |
| Container | `bondlens-demo` |
| Image | `bondlens:local` |
| Host URL | `http://localhost:8765/agent` |
| Health check | `http://localhost:8765/healthz` |
| Container port | `5000` |

Agent response schema:

```text
http://localhost:8765/api/agent/schema
```

### Environment Variables

```bash
FLASK_ENV=production
SECRET_KEY=change-me
BOND_DATA_MODE=auto
OPENAI_API_KEY=
OPENAI_MODEL=haochi/gpt-5.4
OPENAI_BASE_URL=
OPENAI_API_STYLE=chat
OPENAI_TIMEOUT_SECONDS=20
```

- `BOND_DATA_MODE=auto` tries AkShare live data first, then cached live snapshot, then the local Excel fallback.
- `OPENAI_API_KEY` is optional. Without it, deterministic fallback output is used.
- `OPENAI_BASE_URL` can point to an OpenAI-compatible gateway. The validated local CPA path is `http://[IP]:18317/v1` when a key is supplied via process environment.
- `OPENAI_TIMEOUT_SECONDS` defaults to `20` so slow model channels fail closed into deterministic fallback rather than timing out gunicorn.
- `BOND_REPLAY_ENABLED` controls sanitized run replay summaries for `/replay`. Defaults to `true`.
- `BOND_REPLAY_DIR` defaults to `/tmp/bondlens-replays` inside Docker and `.tmp/replays` in local Python.

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
2. Expose container port `5000`; map the public host port as needed.
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

公开作品集演示通常使用宿主机 `8765` 端口；Docker 将宿主机 `8765` 映射到容器内 `5000`。

### 本地 Python 演示

```bash
pip install -r requirements.txt
./scripts/run_demo.sh
# 打开 http://127.0.0.1:8765/agent
```

Windows：

```bat
scripts\run_demo.bat
```

默认脚本会清空 `OPENAI_*`，以无密钥、确定性 fallback 路径演示。只有明确要透传当前 shell 中已有的 LLM 环境变量时，才设置 `BOND_DEMO_WITH_LLM=1`。

### 本地 Docker

```bash
docker compose up --build
```

Compose 命名已固定，便于本机作品集环境保持整洁：

| 项 | 值 |
| --- | --- |
| 服务名 | `bondlens` |
| 容器名 | `bondlens-demo` |
| 镜像名 | `bondlens:local` |
| 访问地址 | `http://localhost:8765/agent` |
| 健康检查 | `http://localhost:8765/healthz` |
| 容器端口 | `5000` |

Agent 响应结构：

```text
http://localhost:8765/api/agent/schema
```

### 环境变量

```bash
FLASK_ENV=production
SECRET_KEY=change-me
BOND_DATA_MODE=auto
OPENAI_API_KEY=
OPENAI_MODEL=haochi/gpt-5.4
OPENAI_BASE_URL=
OPENAI_API_STYLE=chat
OPENAI_TIMEOUT_SECONDS=20
```

- `BOND_DATA_MODE=auto` 会先请求 AkShare 实时数据，然后使用实时快照，最后使用本地 Excel 兜底。
- `OPENAI_API_KEY` 是可选项。为空时使用确定性 fallback 输出。
- `OPENAI_BASE_URL` 可以指向 OpenAI-compatible 网关。已验证的本机 CPA 路径是 `http://[IP]:18317/v1`，密钥只通过进程环境变量传入。
- `OPENAI_TIMEOUT_SECONDS` 默认 `20` 秒，模型通道过慢时会安全回退，而不是拖到 gunicorn 超时。
- `BOND_REPLAY_ENABLED` 控制 `/replay` 的运行回放摘要，默认开启。
- `BOND_REPLAY_DIR` 在 Docker 内默认 `/tmp/bondlens-replays`，本地 Python 默认 `.tmp/replays`。

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
2. 暴露容器端口 `5000`，平台公网端口按需映射。
3. 健康检查路径配置为 `/healthz`。
4. 在平台环境变量中设置 `SECRET_KEY`。
5. 演示实时优先能力时使用 `BOND_DATA_MODE=auto`；需要稳定演示时使用 `static`。
6. 如果不想依赖外部 LLM，保持 `OPENAI_API_KEY` 为空。
7. 除非部署平台需要保留演示历史，否则 replay 存储保持临时即可。

### 运行时安全边界

BondLens 不是投资顾问系统。API 响应包含 `disclaimer`、`evidence_quality`、`llm_guardrail` 和 `data_source` 字段，调用方可以检查数据新鲜度、缺失上下文和 LLM 安全状态。

作品集页面默认展示证据账本、答案评审、风险画像和运行回放摘要，不展示原始 JSON/代码式调试面板。机器可读结构仍然通过 `/api/agent/query` 和 `/api/agent/schema` 提供。
