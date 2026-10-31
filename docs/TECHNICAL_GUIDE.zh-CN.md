# BondLens 技术指南

[项目首页](../README.zh-CN.md) · [English guide](TECHNICAL_GUIDE.md)

本指南描述 V1.1 快照中的本地工作台。它是单实例研究演示，不是公网多用户服务或交易系统。产品概览和本地启动命令见 [README](../README.zh-CN.md)。

## 可复现作品集案例

仓库提供基于 **2024-03-22 静态样本**的 [21附息国债03 单券案例](portfolio_case/CASE_STUDY.zh-CN.md)、[HTML 证据包](portfolio_case/bond-study.html)和[工具计算数据](portfolio_case/case-data.json)。目标收益率 2.68%，与 19 条同券种、同期限桶可比记录的均值相差 +7.23 BP。历史样本无逐行行情日期，无法独立核验同日。

```bash
python scripts/generate_portfolio_case.py          # 静态确定性工具、隔离目录，生成案例文件
python scripts/generate_portfolio_case.py --check  # 重新计算并逐文件比对，不写入
node scripts/capture_portfolio_case.mjs            # 可选：在隔离服务中重截真实界面
python scripts/build_portfolio_walkthrough.py     # 可选：FFmpeg 生成 60 秒静态界面导览
```

脚本故意不依赖真实行情或模型服务，证据包生成时间固定以保证重复执行；**固定时间是构建元数据，不是行情时间或真实运行时间**。截图中的临时结果 ID、研究历史时间可能随重截变化。导览视频由实际程序截图构成，并非交互操作录屏；这些文件都是本地构建产物，不应描述为在线工作台。

## 本地运行与配置

部署基线为 Python 3.12，支持 3.11+。运行依赖使用 `requirements.lock`；测试还需安装 `requirements-dev.txt`。`python app.py` 默认监听 `127.0.0.1:5000`，可通过 `PORT` 和 `FLASK_RUN_HOST` 覆盖。离线使用前设置 `BOND_DATA_MODE=static`。直接运行 Python 时从进程环境读取配置，不自动加载 `.env`；可用选项见[配置示例](../.env.example)。

开发服务器仅供本地使用，系统没有内置认证或按用户隔离的历史。如需面向网络使用，必须有意配置带认证的反向代理；仅修改绑定地址并不构成安全防护。

### Docker

```bash
docker compose up --build
# http://127.0.0.1:8765/ 和 http://127.0.0.1:8765/healthz
```

`bondlens` 服务默认映射 `${BOND_BIND_HOST:-127.0.0.1}:${BOND_HOST_PORT:-8765}:5000`。容器使用一个 Gunicorn 进程和四个线程；临时结果链接依赖进程内缓存，增加 worker 会改变该行为。`docker compose down` 停止服务但保留卷。历史和证据包路径位于 `/app/.tmp` 下时，数据保存在 `bondlens-data` 卷；需要时应备份。`docker compose down -v` 会删除该卷。

可选模型配置包括 `OPENAI_BASE_URL`、`OPENAI_MODEL` 和 `OPENAI_API_STYLE` 等环境变量；不要提交凭据。在支持的环境中，Docker 可通过 `http://host.docker.internal:11434/v1` 连接宿主机 Ollama。配置模型不意味着自动调用。

### 检查

```bash
python -m pip install -r requirements.lock -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
node --check static/js/replay.js
node scripts/browser_smoke.mjs
```

浏览器 smoke 使用隔离本地服务和临时历史。Windows Edge 与 Python 默认路径可分别通过 `BOND_BROWSER_EDGE`、`BOND_BROWSER_PYTHON` 配置；Node 需支持 WebSocket。2026-10-31 的独立本地结果为 252 passed、1 skipped，提交 `595b87e` 的 CI 已成功。这是历史结果，不保证当前 CI 或真实供应商行为。

## 数据模式与指标口径

- **Live：**只使用实时数据；失败即报错，不回退至快照或静态数据。
- **Auto：**依次尝试实时、有效缓存快照及本地样本；实际来源、时间和回退原因以 `data_source` 为准。
- **Static：**使用本地 `data/testdata.xlsx` 样本，不访问实时行情。截图所用样本日期为 2024-03-22。
- **Import：**当前会话使用上传数据，数据在内存中，令牌过期后需重新上传。

实时输入可包含债券简称、净价、收益率、BP 变化、加权收益率、成交量，以及供应商提供时的待偿期。待偿期可能缺失，覆盖看板与未匹配导出用于提示缺口；不能假定每个行情源都有原生期限。同业比较使用相同券种和期限桶，有行情日期时限定同日；没有日期列时无法核验同日，目标券日期缺失时不跨日期比较。

久期和 DV01 基于教学级现金流假设；永续风格期限展示首段有限期限及理论永续情景，并非完整含权树或 OAS 估值。收益异常只描述统计偏离，不构成信用、违约或投资结论。行情不包含主体评级、财报、担保或信用事件；行情再分发权利须与软件许可分开核查。

## 七个确定性工具

规则规划器选择下列算子；不使用 LLM 也可完成研究。

| 工具 | 用途 |
| --- | --- |
| `search_bonds` | 按名称、券种、期限或收益率筛选。 |
| `describe_market` | 汇总收益率、成交量、市场结构和数据质量。 |
| `rank_bonds` | 按支持的数值字段排序。 |
| `detect_yield_outliers` | 输出分组统计偏离及方法。 |
| `compare_bond_to_market` | 比较单券与市场及符合条件的同业。 |
| `build_market_monitor` | 汇总高收益、低成交、异常和缺失期限。 |
| `generate_bond_report` | 从工具结果组装分析、风险提示和限制。 |

## 导入与安全

`POST /api/import` 接收 multipart `file`，支持 CSV、XLSX、XLS；请求上限为 1 MiB。导入报告说明字段映射、缺失字段、同名记录和日期覆盖；缺少输入时禁用依赖该字段的分析，不编造数据。多行情日期文件标记为混合时间点。返回的 `import_id` 用于后续选择内存数据集；缓存最多八个导入、TTL 一小时，重启或过期后需重新上传。

可在界面确认映射，或传入 `column_overrides` JSON 对象，将目标字段映射到源列。自动映射要求字段口径相容；全价不等于净价，成交金额不等于成交量，对这些不相容映射的显式覆盖也会被拒绝。上传文件解析时会临时写入 `BOND_UPLOAD_DIR`（默认 `.tmp/uploads`），通常在解析后移除；进程中断可能留下临时文件。原附件不会作为历史制品保留，可能保存的仅为经过隐私规约的派生结果。脱敏仅为尽力处理，问题和债券名称中不要输入密钥或个人信息。

## 历史与证据包

`BOND_REPLAY_DIR` 默认 `.tmp/replays`，`BOND_EVIDENCE_PACK_DIR` 默认 `.tmp/evidence_packs`。`BOND_REPLAY_ENABLED=false` 禁止历史读取与写入，但不删除旧文件。证据包访问是独立边界；关闭历史本身不会屏蔽已有 `/packs/...` 文件。证据包生成开关为 `BOND_EVIDENCE_PACK_ENABLED`。

版本化历史保存脱敏派生结果，单条上限 2 MiB，不保存原始上传文件或整表；部分导入自由文本被省略，无法恢复。恢复结果只读，重新运行产生独立记录。旧摘要不能恢复完整结果。历史和 HTML/JSON 证据包分别原子写入，并非跨文件事务；中断可能留下孤儿或缺失产物，缺失包不会显示为可用。

历史页使用服务端游标，每页 20 条，按文件名顺序分页。搜索与元数据筛选需要扫描记录；独立本地测试中，3,300 个约 16 KiB 文件里查找最旧匹配项的冷查询约 19.5 秒。演示可使用独立历史与证据包目录，并按需备份归档；不要通过删除历史绕过此限制。

Evidence Pack 包含选定的问题／意图、工具和来源信息，以及信任、风险、证据、结论和限制。[静态示例包](demo_runs/)可直接查看。有意生成演示包可运行 `python scripts/generate_demo_packs.py`；该命令会写入输出，测试时请使用隔离环境。

## HTTP 与流式接口

```http
POST /api/agent/query
Content-Type: application/json

{"question":"当前债券市场样本概览如何？","data_mode":"static"}
```

`POST /api/agent/stream` 接收同样的请求结构，发送 SSE `status`、`token`、`final` 或 `error` 事件。普通分析不请求模型。用户可通过 `/api/agent/explain` 显式请求解读；发送导入数据前需确认。成功解读可以缓存，失败后用户主动重试可能再次调用已配置的服务。浏览器停止接收不保证服务端或供应商停止处理。问题长度上限为 4,000 字符。

其他接口：

```text
GET  /healthz                     GET  /api/agent/schema
POST /api/import                 GET  /api/data/source
GET  /api/market/overview        GET  /api/bonds/search
GET  /api/bond/detail?key=...    GET  /replay
GET  /replay/<id>                GET  /packs/<id>.html|json
GET/POST /api/maturity/unmatched?format=csv|json
```

工作台读取接口按适用场景接收 `data_mode`、`import_id` 和 `lang`。证据包下载不使用客户端指定的目录。当前请求与响应契约可查询 `/api/agent/schema`。

## 语言、示例与来源

页头可切换中文和英文；`?lang=zh|en` 优先于语言 cookie，默认中文。界面标签和确定性报告框架提供双语，开发日志不保证完整翻译。

- [市场 HTML](demo_runs/demo-market-overview.html)、[单券 HTML](demo_runs/demo-bond-report.html)、[异常 HTML](demo_runs/demo-yield-outliers.html)：预生成的静态报告，不是交互服务；相应 JSON 位于同目录。
- [LLM 实测矩阵](demo_runs/llm_matrix_cpa_gpt54.md)：历史供应商路径记录，不是生产保证，也不代表当前真实供应商测试。
- [GitHub Pages](https://phoenix0531-sudo.github.io/BondLens/) 为静态项目介绍页，不是托管的交互工作台。
- 2024 年本科毕业设计原始版本保留在 `undergraduate-thesis-2024` 分支，当前工作继续在 `main`。

[MIT 许可证](../LICENSE) · 仅供研究和学习，不提供投资建议。
