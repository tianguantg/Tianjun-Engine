# CloudSim Plus DCI 示例

本目录提供一个可直接运行的独立 Maven 示例工程，用于把 `CloudSim Plus v8.5.7` 仿真节点接入 Tianjun Engine 控制平面。

该示例是可选的 DCI 参考实验和 HTTP API bridge smoke target，不是 Tianjun Engine 的正式仿真后端。它不会参与 Python 包安装，不作为生产执行器，也不承诺在线重调度语义。

目录布局如下：

```text
pom.xml
src/main/java/org/cloudsimplus/examples/HuaweiDciTianjunExperiment.java
src/main/java/org/cloudsimplus/examples/tianjun/TianjunHttpBridge.java
src/main/resources/huawei-dci-reference.brite
```

## 环境要求

- `JDK 17+`
- `Maven 3.8+`
- 已安装本仓库 Python 依赖

建议先确认本地 Java 工具链：

```powershell
java -version
mvn -version
```

已知可复现环境：

- Python 3.10+
- JDK 17
- Maven 3.8+
- CloudSim Plus 8.5.7
- Tianjun HTTP server at `http://127.0.0.1:8024`

## 启动顺序

### 1. 启动 Tianjun 控制平面

建议本地先用离线模式启动，避免因为未配置 LLM 而阻塞仿真验证：

```powershell
python -B main.py serve `
  --config configs\tianjun.example.toml `
  --default-execution-mode simulation `
  --host 127.0.0.1 `
  --port 8024 `
  --offline
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8024/health
```

### 2. 编译 CloudSim Plus v8.5.7 示例

在本目录执行：

```powershell
mvn clean compile
```

`pom.xml` 已固定使用：

```xml
<dependency>
    <groupId>org.cloudsimplus</groupId>
    <artifactId>cloudsimplus</artifactId>
    <version>8.5.7</version>
</dependency>
```

### 3. 启动仿真节点

在 `examples/cloudsimplus/` 目录执行：

```powershell
mvn exec:java "-Dexec.args=http://127.0.0.1:8024 normal"
```

可选参数顺序如下：

```text
<server> <scenario> <cloudletCount> <seed> <outputPath>
```

例如：

```powershell
mvn exec:java "-Dexec.args=http://127.0.0.1:8024 fault 36 20260527 output/huawei-dci-topology-snapshots.jsonl"
```

## 仿真内容

该实验创建了 24 个模拟计算 VM，并将三个 Hermes 部署区域映射到各一个模拟物理接入点：

- `east`：北京/杭州，连接到 `DC1`
- `west`：成都/重庆，连接到 `DC2`
- `south`：广州/深圳，连接到 `DC3`

实验会：

- 向 Tianjun 注册 DCI 物理拓扑和计算节点
- 周期性发送节点心跳与路径观测
- 通过控制平面提交调度任务
- 在仿真结束后回传任务执行结果
- 输出拓扑快照到 `output/`

`DC1/DC2` 遵循项目 README 中描述的公开案例抽象。`DC3` 是用于三区域实验的可复现模拟扩展，不是声称的生产网络站点。

## 故障定位

- `Unsupported class file major version`：检查 JDK 版本是否为 17 或更新。
- `Could not resolve org.cloudsimplus`：检查 Maven Central、代理配置和本地 `~/.m2` 缓存。
- `/health failed` 或提示控制平面不可达：先启动 Python 控制平面，并确认 `http://127.0.0.1:8024/health` 返回 `status=ok`。
- `No DCI tasks were mapped by Tianjun`：检查节点注册、网络路径、调度约束和 `/schedule/commit` 响应。
- 输出路径写入失败：检查 `output/` 目录权限，或传入可写的 `<outputPath>`。
