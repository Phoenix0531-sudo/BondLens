# BondLens — 本地债券研究工作台

本页是与 [GitHub Pages 静态介绍页](https://phoenix0531-sudo.github.io/BondLens/)配套的简明入口，不提供在线交互服务。

## 可复现单券研究案例

**21附息国债03**，使用仓库内 **2024-03-22 历史静态样本**。目标收益率为 2.68%，与 19 条同券种、同期限桶的可比记录相比，相对同业平均收益率高 7.23 BP。没有逐行行情日期，无法核验同日；本案例不构成投资建议。

- [直接阅读 HTML Evidence Pack](portfolio_case/bond-study.html)
- [完整中文案例与计算边界](portfolio_case/CASE_STUDY.zh-CN.md)
- [60 秒无声截图导览](portfolio_case/walkthrough.mp4)
- [核验计算结果 JSON](portfolio_case/case-data.json)

## 工作台与源码

市场观察、债券筛选、单券比较、确定性研究与研究历史均在本地运行。AI 解释是可选的、用户显式发起的功能，不要求为了查看静态案例配置付费模型。

- [中文 README 与本地启动](https://github.com/Phoenix0531-sudo/BondLens/blob/main/README.zh-CN.md)
- [English README](https://github.com/Phoenix0531-sudo/BondLens/blob/main/README.md)
- [技术指南](TECHNICAL_GUIDE.zh-CN.md)
- [测试与 CI](https://github.com/Phoenix0531-sudo/BondLens/actions/workflows/ci.yml)

**2026-10-31 验证快照**：253 passed、1 skipped；GitHub CI 与 Docker 健康检查通过。当前运行结果以 CI 状态为准。软件采用 MIT 许可，仅供学习与研究。
