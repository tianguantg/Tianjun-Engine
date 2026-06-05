export const textMap = {
  performance: "性能",
  completion: "完成时效",
  cost: "成本",
  reliability: "可靠性",
  balance: "负载均衡",
  fragmentation: "碎片控制",
  locality: "地域匹配",
  network: "网络质量",
  security: "安全策略",
  pending: "待调度",
  running: "运行中",
  succeeded: "已成功",
  failed: "已失败",
  process: "执行进程",
  docker: "Docker",
  kubernetes: "Kubernetes",
  simulation: "仿真执行",
  noop: "空操作",
  backend: "后端决策",
  hermes: "Hermes 推荐",
  replay: "策略回放",
  loaded: "模型已加载",
  ok: "正常",
  waiting: "等待中",
  cpu: "CPU",
  memory: "内存",
  gpu: "GPU",
  storage: "存储",
  latency_history: "LSTM 时延",
  jitter: "时延抖动",
  node_load: "节点负载",
  bandwidth_utilization: "带宽可用性",
  gnn_topology: "GNN 稳定性",
  robust_latency: "稳健时延",
  uncertainty_index: "不确定性指数",
  packet_loss: "丢包率",
  metric_scores: "指标评分",
  inference: "推理任务",
  training: "训练任务",
  streaming: "流式任务",
  analytics: "分析任务",
  batch: "批处理任务",
  batch_cpu: "CPU 批处理",
  task: "普通任务",
};

export const regionMap = {
  east: "东部区域",
  west: "西部区域",
  south: "华南区域",
  shanghai: "上海",
  beijing: "北京",
  hangzhou: "杭州",
  chengdu: "成都",
  shenzhen: "深圳",
  guangzhou: "广州",
  chongqing: "重庆",
  dc1: "DC1",
  dc2: "DC2",
  dc3: "DC3",
  unknown: "未知区域",
};

export const METRIC_KEYS = ["performance", "completion", "cost", "reliability", "balance", "fragmentation", "locality", "network", "security"];

export function escapeHtml(value) {
  return String(value ?? "-")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

export function fmt(value, digits = 2) {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n.toFixed(digits) : "-";
}

export function pct(value, digits = 1) {
  return `${fmt(Number(value ?? 0) * 100, digits)}%`;
}

export function displayKey(key) {
  const raw = String(key ?? "");
  return textMap[raw.toLowerCase()] || raw.replaceAll("_", " ");
}

export function displayRegion(region) {
  const raw = String(region ?? "-");
  return regionMap[raw.toLowerCase()] || raw;
}

export function metricLabel(key) {
  return textMap[String(key ?? "").toLowerCase()] || displayKey(key);
}

export function statusText(value) {
  return textMap[String(value ?? "").toLowerCase()] || String(value ?? "未知");
}

export function modelStatusText(runtime = {}) {
  if (runtime.status === "loaded") return "模型已加载";
  if (runtime.status === "disabled") return "模型未启用";
  if (runtime.status === "error") return "模型异常";
  return statusText(runtime.status ?? "检查中");
}

export function demandText(demand) {
  const d = demand ?? {};
  return `${fmt(d.cpu ?? d.cpu_cores ?? 0, 0)}C / ${fmt(d.memory ?? d.memory_gb ?? 0, 0)}G / GPU ${fmt(d.gpu ?? d.gpu_count ?? 0, 0)} / 存储 ${fmt(d.storage ?? d.storage_gb ?? 0, 0)}G`;
}

export function latestDecision(report) {
  const decisions = report?.recent_decisions ?? [];
  return decisions.length ? decisions[decisions.length - 1] : null;
}

export function activeDecision(report, intentPayload = null) {
  return intentPayload?.preview_decision ?? intentPayload?.policy?.decision ?? latestDecision(report);
}

export function decisionScore(decision) {
  const snap = decision?.network_snapshot ?? {};
  return Number(decision?.total_score ?? decision?.match_score ?? snap.feature_fusion_score ?? 0);
}

export function decisionSummary(decision) {
  if (!decision) return "暂无候选节点";
  const snap = decision.network_snapshot ?? {};
  return `节点 ${decision.node_id}，稳定时延 ${fmt(snap.deterministic_latency_ms ?? snap.stable_latency_ms, 1)} ms，融合评分 ${fmt(decisionScore(decision), 3)}`;
}

export function decisionSourceLabel(entry = {}) {
  if (entry.mode?.includes("Hermes")) return "Hermes 推荐";
  if (entry.mode?.includes("回放")) return "策略回放";
  return "后端决策";
}

export function looksLikeSchedulingRequest(message) {
  const text = String(message ?? "").trim();
  const action = /(提交|下发|创建|运行|部署|安排|调度|启动|分配|派发)/i.test(text);
  const shape = /(推理|训练|计算|CPU|GPU|内存|带宽|时延|延迟|SLA|ms|Mbps|\d+\s*C|\d+\s*G)/i.test(text);
  const question = /(为什么|解释|有哪些|状态|是否|当前|最近|什么是|多少|怎么)/.test(text);
  return (action && shape) || (shape && !question);
}

export function isCommitConfirmation(message) {
  return /^(确认|可以|没问题|正式下发|下发|执行|提交|就这个)/.test(String(message ?? "").trim());
}

export function renderInlineMarkdown(text) {
  return escapeHtml(text).replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
}

export function compactText(value, max = 18) {
  const text = String(value ?? "-");
  return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

export function gnnDisplayState(snapshot = {}, decision = null, report = {}) {
  const pred = snapshot.model_prediction ?? report?.model_runtime?.latest_prediction ?? {};
  const value = pred.gnn_stability_score ?? snapshot.fusion_features?.gnn_topology;
  if (value !== undefined && value !== null) {
    return { value: pct(value, 1), detail: "GNN 基于物理拓扑邻居、路径时延和节点负载评估稳定性", compact: `GNN ${pct(value, 0)}` };
  }
  return { value: "--", detail: decision ? "等待 GNN 稳定性回传" : "等待调度决策", compact: "GNN --" };
}

export function nodePrimaryPath(node) {
  const paths = node?.network_paths ?? {};
  return paths.shanghai ?? paths[node?.region] ?? Object.values(paths)[0] ?? {};
}

export function stableLatencyOf(node) {
  const path = nodePrimaryPath(node);
  return Number(path.deterministic_latency_ms ?? path.stable_latency_ms ?? path.latency_ms ?? 0);
}

export function resourceUtil(node, key) {
  const total = Number(node?.capacity?.[key] ?? 0);
  const available = Number(node?.available?.[key] ?? 0);
  if (total <= 0) return 0;
  return Math.max(0, Math.min(1, (total - available) / total));
}

export function nodeLoad(node) {
  const values = ["cpu", "memory", "gpu", "storage"].map((key) => resourceUtil(node, key)).filter((value) => Number.isFinite(value));
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

export function topWeights(weights = {}, count = 3) {
  return Object.entries(weights)
    .filter(([, value]) => Number.isFinite(Number(value)))
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, count);
}

export function policyBias(weights = {}) {
  const top = topWeights(weights, 2).map(([key]) => key);
  if (top.includes("completion") || top.includes("performance")) return "低时延与完成时效优先";
  if (top.includes("cost")) return "成本控制优先";
  if (top.includes("reliability") || top.includes("network")) return "稳定性与可靠性优先";
  if (top.includes("security")) return "安全策略优先";
  return "均衡调度策略";
}
