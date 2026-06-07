# DCI 实验与模型资产

DCI 参考资产是研究和复现材料。它们不是生产测量数据。

## 资产类别

| 路径 | 角色 |
| --- | --- |
| `data/dci_reference/` | DCI 参考实验的原始、验证和图样本数据 |
| `scripts/build_dci_graph_dataset.py` | 数据集构建辅助工具 |
| `scripts/train_dci_graphsage.py` | GraphSAGE 训练辅助工具 |
| `data/trained_models/` | 模型运行时加载的可选运行时制品 |
| `data/trained_models/MODEL_MANIFEST.json` | 模型哈希、来源、用途和加载策略 |
| `examples/cloudsimplus/` | 独立 Maven 工程形式的 CloudSimPlus 桥接器和参考实验 |

## 复现边界

运行时操作不需要研究数据集。最小控制平面可以通过以下方式启动：

```powershell
python -B main.py serve --config configs\tianjun.example.toml --offline
```

当训练好的模型制品存在时，运行时可能会加载 LSTM 延迟和 GraphSAGE 稳定性模型。如果 PyTorch 或制品不可用且不需要模型加载，调度器将回退到确定性评分。

`examples/cloudsimplus/` 是可选 DCI 参考实验和 HTTP API 兼容性 smoke target。它已经是本仓库内可直接运行的独立 Maven 工程，不需要复制到外部 `cloudsimplus-examples` 仓库。该示例通过 `/schedule/commit` 做兼容式直接调度，不代表真实节点代理的 `/leases/next` 租约执行链路。

推荐复现环境：

- Python 3.10+
- JDK 17
- Maven 3.8+
- CloudSim Plus 8.5.7
- Tianjun HTTP server at `http://127.0.0.1:8024`

## 大文件策略

当大型模型检查点、扩展数据集和报告副本超出正常仓库审查的范围时，应将其迁移到发布制品或外部存储。只保留清单、紧凑的参考数据和复现或检索资产所需的脚本。
