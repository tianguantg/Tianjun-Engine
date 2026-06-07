# Tianjun 文档索引

当您需要快速找到合适的维护面时，请从这里开始。

| 主题 | 文档 |
| --- | --- |
| 本地安装、完整启动、模拟节点、核心入口 | [../README.md](../README.md) |
| 人类贡献流程和 PR 维护约定 | [../CONTRIBUTING.md](../CONTRIBUTING.md) |
| Coding agent 操作规范、验证声明、编码和仓库卫生 | [../AGENTS.md](../AGENTS.md) |
| 系统形态、运行链路和控制平面服务边界 | [architecture.md](architecture.md) |
| 官方 HTTP API、遗留路由归属和确认规则 | [api.md](api.md) |
| 已弃用端点和迁移路径 | [deprecation.md](deprecation.md) |
| 确认、执行器、密钥、LLM 和生产安全边界 | [security-boundary.md](security-boundary.md) |
| DCI 参考数据、模型资产和复现说明 | [experiments-dci.md](experiments-dci.md) |
| CloudSimPlus DCI 示例的本地 Maven 运行、环境要求和故障定位 | [../examples/cloudsimplus/README.md](../examples/cloudsimplus/README.md) |
| Dashboard 手动验收检查 | [dashboard-test-checklist.md](dashboard-test-checklist.md) |
| 架构收敛冻结检查 | [convergence-checklist.md](convergence-checklist.md) |

日常验证命令：

```powershell
python -m pytest
python scripts\smoke_test.py
python scripts\convergence_check.py
```
