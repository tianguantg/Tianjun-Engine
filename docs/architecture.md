# 架构

Tianjun Engine 围绕公共适配器、中央控制平面门面和可测试的应用服务组织。HTTP、Dashboard、ChatRuntime 和 MCP 都调用稳定的 `CentralControlPlane` facade；已经迁出的业务生命周期由独立服务维护。

## 运行时流程

1. 用户通过 Dashboard、CLI 聊天或 MCP 主机发起请求。
2. `ChatRuntime` 区分普通聊天、需求解析、策略选择和提交确认。
3. HTTP、聊天和 MCP 工具统一调用 `CentralControlPlane`。
4. 控制平面门面协调策略工作流、确定性调度、任务租约和执行反馈。
5. CloudSimPlus 示例或真实节点代理注册节点、发送心跳，并通过各自协议推进任务执行回报。
6. `/report`、`/health` 和 Dashboard 展示节点、任务、策略、执行和模型状态。

## 主要子系统

| 子系统 | 职责 |
| --- | --- |
| HTTP 接口 | 官方 REST/SSE API、Dashboard 静态服务、遗留兼容适配器 |
| Dashboard | 使用官方 API 的静态 HTML/CSS/JS 控制界面 |
| 聊天运行时 | Hermes 风格对话、策略选项选择、明确提交流程 |
| MCP 适配器 | 通过 HTTP 包装器向 MCP 主机暴露工具 |
| 控制平面门面 | 为 HTTP、聊天、MCP 和测试提供稳定 API |
| 调度引擎 | 确定性节点过滤和多目标评分 |
| 仿真/节点代理 | CloudSimPlus 兼容式直接调度、真实节点租约领取、心跳和结果回报 |

## 控制平面服务边界

| 服务 | 当前职责 |
| --- | --- |
| `NodeRegistry` | 节点注册、心跳、节点遥测变更、节点持久化 |
| `TaskLeaseService` | 任务提交、预览、pending 调度、agent 租约轮询、租约激活 |
| `RequirementDialogueService` | 需求解析、需求会话开始/继续/读取、地域可用性载荷 |
| `PolicyWorkflowService` | 策略起草、候选比较、模拟、提交、反馈解析、反馈记录、反馈优化 |
| `src/tianjun/cli/commands/` | 所有 CLI 命令处理器 |

`CentralControlPlane` 保留 facade 方法、共享状态、报表组装、恢复/持久化协调、拓扑注册、策略权重更新以及执行进度/结果回报等跨领域逻辑。已经迁移到服务中的业务流程不应复制回门面类。

## CloudSimPlus 仿真链路

`examples/cloudsimplus/` 中的 Java CloudSimPlus 工程是可选 DCI 参考实验和 HTTP API bridge smoke target，不是 Tianjun Engine 的正式仿真后端。它会：

- 调用 `/topology/register` 注册 DCI 物理拓扑。
- 调用 `/nodes/register` 注册 CloudSimPlus 仿真 VM 节点。
- 持续调用 `/nodes/heartbeat` 上报在线状态。
- 通过 `/schedule/commit` 请求 Tianjun 控制平面做兼容式直接调度。
- 在 CloudSimPlus 仿真完成后通过 `/task-runs/result` 回报执行结果。

真实节点代理使用 `/leases/next` 领取租约，并通过 `/task-runs/progress` 和 `/task-runs/result` 回报执行状态。CloudSimPlus 示例不代表正式 lease-based execution backend。

完整启动命令见 [README.md](../README.md)。

## 兼容层边界

遗留路由只应存在于 `src/tianjun/interfaces/http/legacy_routes.py`。新 Dashboard、CLI、MCP 和文档示例都应使用官方路由，尤其是 `/chat/sessions*` 聊天流程。
