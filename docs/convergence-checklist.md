# 架构收敛冻结清单

在声明当前研究工程收敛阶段完成前，使用本清单做最终检查。

## 必跑命令

```powershell
python -m py_compile `
  src\tianjun\cli\__init__.py `
  src\tianjun\application\control_plane.py `
  src\tianjun\application\node_registry.py `
  src\tianjun\application\task_lease_service.py `
  src\tianjun\application\requirement_dialogue.py `
  src\tianjun\application\policy_workflow.py `
  src\tianjun\interfaces\http\server.py `
  src\tianjun\interfaces\http\legacy_routes.py `
  scripts\smoke_test.py `
  scripts\convergence_check.py

python -m pytest
python scripts\smoke_test.py --port 8135
python scripts\convergence_check.py
```

## 完整本地启动检查

先配置并验证 LLM：

```powershell
python -B main.py secrets --config configs\tianjun.example.toml set deepseek --api-key "your_api_key_here"
python -B main.py llm-check --config configs\tianjun.example.toml
```

第一个终端启动控制平面：

```powershell
python -B main.py serve `
  --config configs\tianjun.example.toml `
  --default-execution-mode simulation `
  --host 127.0.0.1 `
  --port 8024
```

第二个终端可选启动 Java CloudSimPlus DCI 示例：

```powershell
cd examples\cloudsimplus
mvn clean compile
mvn exec:java "-Dexec.args=http://127.0.0.1:8024 normal"
```

第三个终端启动 MCP server：

```powershell
python -B main.py mcp-server `
  --config configs\tianjun.example.toml `
  --server http://127.0.0.1:8024
```

然后检查：

- `/health` 返回 `status=ok`。
- `/report` 返回控制平面状态。
- `/dashboard` 能打开静态 Dashboard。
- Dashboard 节点/拓扑页面能看到 CloudSimPlus 注册的仿真节点。
- MCP host 可通过 `mcp-server` 访问 Tianjun 工具。
- CloudSimPlus 示例注册拓扑和节点，发送心跳，并通过 `/schedule/commit` 和 `/task-runs/result` 完成兼容式直接调度 smoke flow。

## 可选 Java 示例检查

有 JDK 17 和 Maven 3.8+ 时，可单独验证 CloudSimPlus 示例能编译：

```powershell
cd examples\cloudsimplus
java -version
mvn -version
mvn -q -DskipTests compile
```

若 Maven 未加入 PATH，可使用完整路径或脚本：

```powershell
$MAVEN = "C:\tools\apache-maven-3.9.9\bin\mvn.cmd"
& $MAVEN -version
cd examples\cloudsimplus
& $MAVEN -q -DskipTests compile
```

或从仓库根目录运行：

```powershell
.\scripts\cloudsimplus_smoke.ps1 -MavenPath "C:\tools\apache-maven-3.9.9\bin\mvn.cmd"
```

该检查不属于 Python 主项目必跑链；没有 Java 环境时，不应影响 `python -m pytest`、`python scripts\smoke_test.py` 或 `python scripts\convergence_check.py` 的结论。

## 运行时检查

- 官方聊天路由 `/chat/sessions` 可以创建会话。
- 遗留 `/intent` 默认只预览。
- 遗留 `/intent` 在 `dry_run=false` 且无确认时返回 403。
- 节点注册和心跳通过 `NodeRegistry`。
- 任务提交、预览、调度和租约发放通过 `TaskLeaseService`。
- 需求解析和会话继续通过 `RequirementDialogueService`。
- 策略起草、比较、模拟、提交和反馈优化通过 `PolicyWorkflowService`。
- MCP contract 可导入并与注册工具一致。

## 静态检查

- `CentralControlPlane` 只保留 facade、共享状态、报表/恢复/拓扑/执行回报等跨领域协调职责。
- `node_registry.py` 拥有节点生命周期。
- `task_lease_service.py` 拥有任务和租约生命周期。
- `requirement_dialogue.py` 拥有需求对话生命周期。
- `policy_workflow.py` 拥有策略生命周期。
- `cli/__init__.py` 只包含 parser、配置解析和 dispatch；命令实现位于 `cli/commands/`。
- 已弃用路由只在 `interfaces/http/legacy_routes.py` 中实现。
- Dashboard 静态 JavaScript 不调用 `/intent`、旧 `/chat` 或 `/hermes/*`。
- `requirements.txt` 只作说明，不重复 `pyproject.toml` 的依赖事实。
- README 和 docs 对服务边界、确认规则、模拟节点启动方式的描述一致。

## 当前阶段外事项

这些事项不属于当前收敛冻结范围：

- 生产认证、RBAC、TLS、多租户授权。
- 服务完全脱离 `CentralControlPlane` 共享状态对象。
- Dashboard UI 重设计。
- 模型和数据资产迁移到发布制品或外链。
- 调度算法变更。
