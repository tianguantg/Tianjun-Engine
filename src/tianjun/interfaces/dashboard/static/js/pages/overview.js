import { cancelTaskRun } from "../api.js";
import { emit, state } from "../state.js";
import { activeDecision, decisionScore, displayKey, displayRegion, escapeHtml, fmt, gnnDisplayState, pct } from "../utils.js";

const DC_ORDER = ["dc1", "dc2", "dc3"];

export function initOverview() {
  document.getElementById("page-overview").innerHTML = `
    <header class="page-head">
      <div>
        <h1 class="page-title">资源调度总览</h1>
        <p class="page-subtitle">资源容量、实时队列、SLA 风险与最近调度决策。</p>
      </div>
    </header>
    <section id="overviewMetrics" class="grid overview-metrics" aria-label="核心调度指标"></section>
    <section class="grid overview-engine">
      <article class="card capacity-card">
        <h2 class="card-title title-primary">资源池</h2>
        <div id="capacityMatrix" class="capacity-matrix"></div>
      </article>
      <article class="card queue-card">
        <h2 class="card-title title-primary">实时调度队列</h2>
        <div id="realtimeQueue" class="queue-columns"></div>
      </article>
    </section>
    <section class="grid overview-bottom">
      <article class="card decision-wide">
        <h2 class="card-title title-primary">最近决策列表</h2>
        <div id="overviewDecision" class="list"></div>
      </article>
      <aside id="slaAlertCard" class="card sla-alert-card" role="button" tabindex="0" aria-label="跳转任务执行页查看 SLA 未达标任务"></aside>
    </section>`;

  const jump = () => {
    location.hash = "tasks";
  };
  document.getElementById("slaAlertCard").addEventListener("click", jump);
  document.getElementById("slaAlertCard").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") jump();
  });
  document.getElementById("realtimeQueue").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-cancel-run]");
    if (!button || button.disabled) return;
    button.disabled = true;
    button.textContent = "释放中";
    try {
      await cancelTaskRun(button.dataset.cancelRun);
      emit("report:refresh");
    } catch (error) {
      button.disabled = false;
      button.textContent = "失败";
      button.title = error.message;
      setTimeout(() => {
        button.textContent = "释放";
      }, 1600);
    }
  });
}

export function renderOverview(report, health) {
  if (!report) {
    renderOverviewLoading();
    return;
  }
  renderMetrics(report);
  renderCapacity(report);
  renderQueue(report);
  renderRecent(report, health);
  renderSlaAlert(report);
}

function renderOverviewLoading() {
  document.getElementById("overviewMetrics").innerHTML = Array.from({ length: 4 }, () => `
    <article class="card metric-card kpi-core loading-card">
      <span class="metric-label">加载中</span>
      <strong class="metric-value">--</strong>
      <span class="metric-delta">正在获取调度数据</span>
    </article>`).join("");
  document.getElementById("capacityMatrix").innerHTML = `<div class="empty">资源池数据加载中...</div>`;
  document.getElementById("realtimeQueue").innerHTML = `<div class="empty">实时调度队列加载中...</div>`;
  document.getElementById("overviewDecision").innerHTML = `<div class="empty">最近决策加载中...</div>`;
  document.getElementById("slaAlertCard").innerHTML = `
    <span class="metric-label">SLA 未达标任务</span>
    <strong class="sla-alert-number">--</strong>
    <p>正在获取任务执行与 SLA 校验结果。</p>`;
}

function renderMetrics(report) {
  const metrics = report.metrics ?? {};
  const decision = activeDecision(report, state.intentPayload);
  const snap = decision?.network_snapshot ?? {};
  const gnnValue = metrics.gnn_stability_score ?? snap.fusion_features?.gnn_topology ?? report.model_runtime?.latest_prediction?.gnn_stability_score;
  const cards = [
    ["平均时延", `${fmt(metrics.average_stable_latency_ms ?? snap.deterministic_latency_ms, 1)} ms`, "primary", "LSTM 稳健时延预测"],
    ["GNN 稳定性", gnnValue === undefined ? "--" : pct(gnnValue, 1), "teal", "拓扑稳定性参与调度评分"],
    ["融合评分", fmt(metrics.average_fusion_score ?? snap.feature_fusion_score ?? decisionScore(decision), 3), "dark", decision ? `当前节点 ${decision.node_id}` : "等待调度决策"],
    ["在线节点", `${(report.nodes ?? []).filter((node) => node.online !== false).length}`, "good", "可参与资源分配的节点"],
  ];
  document.getElementById("overviewMetrics").innerHTML = cards.map(([label, value, tone, delta], index) => `
    <article class="card metric-card kpi-core ${tone === "teal" ? "teal" : tone === "good" ? "success" : tone === "dark" ? "ink" : "primary"}">
      <span class="metric-label">${escapeHtml(label)}</span>
      <strong class="metric-value ${escapeHtml(tone)}">${escapeHtml(String(value))}</strong>
      <span class="metric-delta">${escapeHtml(delta)}</span>
    </article>`).join("");
}

function renderCapacity(report) {
  const groups = capacityByDc(report);
  document.getElementById("capacityMatrix").innerHTML = groups.map((dc) => `
    <article class="capacity-row">
      <div class="capacity-title">
        <b>${escapeHtml(dc.label)}</b>
        <span>${dc.nodes.length} 个节点 · ${displayRegion(dc.region)}</span>
      </div>
      ${renderCapacityMetric("CPU", dc.cpu)}
      ${renderCapacityMetric("内存", dc.memory)}
      ${renderCapacityMetric("任务槽位", dc.slots)}
    </article>`).join("");
}

function renderCapacityMetric(label, value) {
  const tone = value > 0.82 ? "danger" : value > 0.62 ? "warning" : "success";
  return `<div class="capacity-metric ${tone}">
    <span>${escapeHtml(label)}</span>
    <div class="capacity-track"><i style="width:${Math.max(4, Math.min(100, value * 100))}%"></i></div>
    <b>${pct(value, 0)}</b>
  </div>`;
}

function capacityByDc(report) {
  const nodes = report.nodes ?? [];
  const buckets = new Map();
  for (const node of nodes) {
    const key = dcKey(node);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(node);
  }
  return DC_ORDER.map((key) => {
    const nodesInDc = buckets.get(key) ?? [];
    const avg = (fn) => nodesInDc.reduce((sum, node) => sum + Number(fn(node) ?? 0), 0) / Math.max(1, nodesInDc.length);
    const region = nodesInDc[0]?.service_region ?? nodesInDc[0]?.region ?? nodesInDc[0]?.location ?? key;
    return {
      key,
      label: key.toUpperCase(),
      region,
      nodes: nodesInDc,
      cpu: avg((node) => resourceValue(node, "cpu")),
      memory: avg((node) => resourceValue(node, "memory")),
      slots: avg((node) => node.slot_utilization ?? node.task_slot_utilization ?? nodeLoadFallback(node)),
    };
  });
}

function dcKey(node) {
  const text = String(node.node_id ?? node.dc ?? node.datacenter ?? "").toLowerCase();
  const match = text.match(/dc[-_]?(\d+)/);
  return match ? `dc${match[1]}` : "dc1";
}

function resourceValue(node, key) {
  const direct = node[`${key}_utilization`] ?? node[`${key}_used_ratio`] ?? node[`${key}_usage`];
  if (direct !== undefined && Number(direct) > 0) return Number(direct);
  const cap = Number(node.resources?.[key] ?? node.capacity?.[key] ?? 0);
  const used = Number(node.used_resources?.[key] ?? node.allocations?.[key] ?? 0);
  if (cap > 0 && used > 0) return used / cap;
  return key === "cpu" ? nodeLoadFallback(node) : nodeLoadFallback(node) * 0.82;
}

function nodeLoadFallback(node) {
  const perf = node.performance_factors ?? {};
  const direct = node.load ?? node.current_load ?? perf.cpu_utilization ?? perf.memory_utilization;
  if (direct !== undefined) return Number(direct);
  const health = Number(node.health_score ?? 0.9);
  const latency = Number(node.stable_latency_ms ?? node.avg_latency_ms ?? node.network_paths?.[0]?.latency_ms ?? 2);
  return Math.max(0.08, Math.min(0.86, (1 - health) * 0.8 + latency / 18));
}

function renderQueue(report) {
  const statuses = report.task_statuses ?? {};
  const indexed = taskIndex(report);
  const groups = [
    ["pending", "等待调度", "等待进入控制面"],
    ["scheduling", "正在调度", "Hermes / 控制面生成策略"],
    ["running", "执行中", "节点侧资源占用中"],
  ].map(([key, label, hint]) => {
    const tasks = key === "running"
      ? (report.active_runs ?? []).map((run) => run.task_id)
      : Object.entries(statuses).filter(([, status]) => {
          if (key === "scheduling") return ["scheduling", "preview", "assigned"].includes(status);
          return status === key;
        }).map(([id]) => id);
    return { key, label, hint, tasks: Array.from(new Set(tasks)).slice(0, 4), count: tasks.length };
  });
  document.getElementById("realtimeQueue").innerHTML = groups.map((group) => `
    <article class="queue-column ${group.key}">
      <span>${escapeHtml(group.hint)}</span>
      <strong>${fmt(group.count, 0)}</strong>
      <b>${escapeHtml(group.label)}</b>
      <div class="queue-list">
        ${group.tasks.map((taskId) => renderQueueTask(group.key, taskId, indexed.get(taskId))).join("") || "<em>暂无代表任务</em>"}
      </div>
    </article>`).join("");
}

function renderQueueTask(groupKey, taskId, record) {
  const label = `${displayKey(record?.task_type ?? "task")} · ${taskId}`;
  const action = groupKey === "running"
    ? `<button class="queue-release-btn" type="button" data-cancel-run="${escapeHtml(taskId)}" title="释放该任务占用的节点资源">释放</button>`
    : "";
  return `<em><span>${escapeHtml(label)}</span>${action}</em>`;
}

function taskIndex(report) {
  const map = new Map();
  for (const item of report.recent_records ?? []) map.set(item.task_id, item);
  for (const item of report.recent_progress_events ?? []) map.set(item.task_id, { ...(map.get(item.task_id) ?? {}), ...item });
  for (const item of report.active_runs ?? []) map.set(item.task_id, { ...(map.get(item.task_id) ?? {}), ...(item.task ?? {}), ...item });
  return map;
}

function renderSlaAlert(report) {
  const totals = report.totals ?? {};
  const records = report.recent_records ?? [];
  const completed = Number(totals.completed ?? totals.completed_attempts ?? records.length);
  const slaMet = Number(totals.sla_met ?? records.filter((record) => record.sla_met === true).length);
  const slaMiss = Number(totals.sla_missed ?? totals.sla_unmet ?? Math.max(0, completed - slaMet));
  document.getElementById("slaAlertCard").innerHTML = `
    <span class="metric-label">SLA 未达标任务</span>
    <strong class="sla-alert-number">${fmt(slaMiss, 0)}</strong>
    <p>执行完成但未满足时延、成本或稳定性目标。点击进入任务执行页定位异常记录。</p>
    <b>查看任务执行 →</b>`;
}

function renderRecent(report, health) {
  const issues = health?.issues ?? [];
  const decisions = (report.recent_decisions ?? []).slice().reverse();
  const rows = [
    ...issues.map((issue) => `<article class="list-item"><div class="item-row"><b>系统告警</b><span class="badge badge-danger">健康检查</span></div><p class="muted">${escapeHtml(issue)}</p></article>`),
    ...decisions.map((decision) => {
      const snap = decision.network_snapshot ?? {};
      const gnn = gnnDisplayState(snap, decision, report);
      return `<article class="list-item decision-rich">
        <div class="item-row"><b>${escapeHtml(decision.task_id ?? "-")} → ${escapeHtml(decision.node_id ?? "-")}</b><span class="badge badge-primary">后端决策</span></div>
        <div class="pill-row">
          <span class="badge badge-neutral">稳健时延 ${fmt(snap.deterministic_latency_ms ?? snap.stable_latency_ms, 1)} ms</span>
          <span class="badge badge-success">${escapeHtml(gnn.compact)}</span>
          <span class="badge badge-primary">融合评分 ${fmt(decisionScore(decision), 3)}</span>
        </div>
        <p class="muted"><b>推荐原因：</b>${escapeHtml(decision.explanation ?? "综合完成时效、网络质量、性能与可靠性后评分最高。")}</p>
      </article>`;
    }),
  ];
  document.getElementById("overviewDecision").innerHTML = rows.join("") || "<div class=\"empty\">暂无告警或调度决策。</div>";
}
