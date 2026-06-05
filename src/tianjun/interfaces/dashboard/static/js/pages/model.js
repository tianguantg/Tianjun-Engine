import { updatePolicyWeights } from "../api.js";
import { emit, state } from "../state.js";
import { METRIC_KEYS, activeDecision, displayKey, escapeHtml, fmt, metricLabel, modelStatusText, pct, topWeights } from "../utils.js";

const defaultWeights = Object.fromEntries(METRIC_KEYS.map((key) => [key, 0.1]));
const templates = {
  latency: ["低时延优先", { completion: 0.24, performance: 0.16, network: 0.16, reliability: 0.1, cost: 0.06, security: 0.05, balance: 0.08, fragmentation: 0.03, locality: 0.08 }],
  cost: ["成本优先", { cost: 0.24, balance: 0.15, completion: 0.13, performance: 0.09, reliability: 0.06, network: 0.08, security: 0.04, fragmentation: 0.08, locality: 0.08 }],
  stability: ["稳定性优先", { network: 0.22, reliability: 0.18, completion: 0.11, performance: 0.1, balance: 0.12, security: 0.08, cost: 0.05, fragmentation: 0.05, locality: 0.06 }],
  security: ["安全优先", { security: 0.26, reliability: 0.16, network: 0.14, completion: 0.11, performance: 0.09, cost: 0.06, balance: 0.07, fragmentation: 0.04, locality: 0.05 }],
  balanced: ["均衡策略", { completion: 0.14, performance: 0.12, network: 0.13, reliability: 0.11, cost: 0.11, security: 0.1, balance: 0.1, fragmentation: 0.06, locality: 0.08 }],
};

let draftWeights = {};
let localTimeline = [];
let selectedTemplate = "";

export function initModel() {
  document.getElementById("page-model").innerHTML = `
    <header class="page-head">
      <div>
        <h1 class="page-title">模型配置</h1>
        <p class="page-subtitle">策略调整时间线、权重差值与调度影响预估。</p>
      </div>
    </header>
    <section class="grid model-layout">
      <article class="card model-left">
        <h2 class="card-title title-success">模型状态</h2>
        <div id="modelRuntime" class="details-grid"></div>
        <h2 class="card-title title-success model-section-title">策略调整时间线</h2>
        <div id="weightHistory" class="history-timeline"></div>
      </article>
      <article class="card model-right">
        <h2 class="card-title title-success">9 轴权重滑块</h2>
        <p class="muted">显示当前策略相对上次提交的变化量。</p>
        <nav id="strategyTemplates" class="template-row" aria-label="策略模板"></nav>
        <div id="weightSliders" class="sliders"></div>
        <section class="impact-card">
          <h3>调整影响预估</h3>
          <div id="impactPreview" class="impact-grid"></div>
        </section>
        <div class="model-actions">
          <button id="previewWeights" class="btn-ghost">预览</button>
          <button id="commitWeights" class="btn-primary">提交</button>
        </div>
      </article>
    </section>`;

  document.getElementById("strategyTemplates").innerHTML = Object.entries(templates).map(([key, [label]]) => `<button class="template-btn" data-template="${key}" type="button">${escapeHtml(label)}</button>`).join("");
  document.getElementById("strategyTemplates").addEventListener("click", (event) => {
    const button = event.target.closest("[data-template]");
    if (!button) return;
    selectedTemplate = button.dataset.template;
    draftWeights = { ...templates[selectedTemplate][1] };
    renderModel(state.report, state.health);
  });
  document.getElementById("previewWeights").addEventListener("click", previewWeights);
  document.getElementById("commitWeights").addEventListener("click", () => void submitWeights());
}

export function renderModel(report, health) {
  if (!report) {
    renderModelLoading();
    return;
  }
  const runtime = report.model_runtime ?? health?.model_runtime ?? {};
  const decision = activeDecision(report, state.intentPayload);
  const snap = decision?.network_snapshot ?? {};
  const weights = currentWeights(report);
  document.getElementById("modelRuntime").innerHTML = [
    ["LSTM 时延预测", snap.model_prediction?.lstm_latency_ms !== undefined ? `${fmt(snap.model_prediction.lstm_latency_ms, 1)} ms` : modelStatusText(runtime)],
    ["GNN 拓扑稳定性", snap.fusion_features?.gnn_topology !== undefined ? pct(snap.fusion_features.gnn_topology, 1) : "--"],
    ["模型状态", modelStatusText(runtime)],
    ["当前策略", decision?.policy_id ?? state.hermesPolicyId ?? "--"],
  ].map(([k, v]) => `<div class="field"><label>${escapeHtml(k)}</label><b>${escapeHtml(v)}</b></div>`).join("");
  renderSliders(report, weights);
  renderImpact(report, weights);
  renderHistory(report, runtime);
}

function renderModelLoading() {
  document.getElementById("modelRuntime").innerHTML = [
    ["LSTM 时延预测", "--"],
    ["GNN 拓扑稳定性", "--"],
    ["模型状态", "加载中"],
    ["当前策略", "--"],
  ].map(([k, v]) => `<div class="field"><label>${escapeHtml(k)}</label><b>${escapeHtml(v)}</b></div>`).join("");
  document.getElementById("weightHistory").innerHTML = `<div class="empty">策略调整时间线加载中...</div>`;
  document.getElementById("weightSliders").innerHTML = `<div class="empty">权重滑块加载中...</div>`;
  document.getElementById("impactPreview").innerHTML = `<div class="empty">调整影响评估加载中...</div>`;
}

function renderSliders(report, weights) {
  const previous = previousWeights(report);
  document.getElementById("strategyTemplates").querySelectorAll("[data-template]").forEach((button) => button.classList.toggle("active", button.dataset.template === selectedTemplate));
  document.getElementById("weightSliders").innerHTML = METRIC_KEYS.map((key) => {
    const value = Number(weights[key] ?? 0);
    const delta = value - Number(previous[key] ?? 0);
    return `<label class="slider-row">
      <span>${escapeHtml(metricLabel(key))}<small>${escapeHtml(displayKey(key))}</small></span>
      <input type="range" min="0" max="1" step="0.01" data-weight="${key}" value="${value}">
      <b>${pct(value, 0)} <em class="${delta >= 0 ? "up" : "down"}">${deltaText(delta)}</em></b>
    </label>`;
  }).join("");
  document.querySelectorAll("[data-weight]").forEach((input) => input.addEventListener("input", () => {
    selectedTemplate = "";
    draftWeights[input.dataset.weight] = Number(input.value);
    renderModel(state.report, state.health);
  }));
}

function renderImpact(report, weights) {
  const active = activeTaskCount(report);
  const latency = Number(weights.completion ?? 0) + Number(weights.performance ?? 0) + Number(weights.network ?? 0);
  const cost = Number(weights.cost ?? 0);
  const impact = [
    ["预计平均时延变化", latency > 0.48 ? "下降 8% - 14%" : "下降 2% - 6%"],
    ["预计成本变化", cost > 0.18 ? "下降 5% - 10%" : "小幅波动"],
    ["预计 SLA 达标率变化", latency > 0.42 ? "提升 5% - 11%" : "提升 1% - 4%"],
    ["影响活跃任务数", `${active} 条正在执行 / 调度中任务`],
  ];
  document.getElementById("impactPreview").innerHTML = impact.map(([label, value]) => `<div class="impact-item"><label>${escapeHtml(label)}</label><b>${escapeHtml(value)}</b></div>`).join("");
}

function renderHistory(report, runtime) {
  const source = report.adjustment_history ?? runtime.adjustment_history ?? report.policy_history ?? [];
  const normalized = normalizeHistory([...source, ...localTimeline]);
  const recent = normalized.slice(-5).reverse();
  document.getElementById("weightHistory").innerHTML = recent.map((item, index) => {
    const top = formatTopWeights(item.weights ?? {});
    const affected = item.affected_records ?? item.affected_tasks ?? 0;
    const metrics = adjustmentMetrics(item.metrics);
    const order = normalized.length - index;
    return `<article class="timeline-item">
      <div class="timeline-head"><b>调整 #${escapeHtml(order)} · tick ${escapeHtml(item.tick ?? "--")}</b><span class="badge badge-success">${escapeHtml(item.scope ?? "自动调整")}</span></div>
      <p>${escapeHtml(item.reason)}</p>
      <div class="timeline-detail"><span>调整内容</span><b>${escapeHtml(item.change)}</b></div>
      <div class="timeline-detail"><span>影响指标</span><b>${escapeHtml(top || item.impact || "暂无权重变化")}</b></div>
      ${metrics ? `<div class="timeline-detail"><span>观测指标</span><b>${escapeHtml(metrics)}</b></div>` : ""}
      <div class="timeline-detail impact-count"><span>依据样本</span><b>${fmt(affected, 0)} 条执行记录</b></div>
    </article>`;
  }).join("") || "<div class=\"empty\">暂无权重调整历史。</div>";
}

function normalizeHistory(items) {
  const result = [];
  const seen = new Set();
  for (const item of items) {
    const reason = translateReason(item);
    const weights = item.weights ?? {};
    const top = topWeights(weights, 3);
    const change = item.change ?? (top.length ? `当前最高权重：${top.map(([key]) => metricLabel(key)).join("、")}` : "保持当前策略权重");
    const fingerprint = `${item.tick ?? ""}:${reason}:${change}`;
    if (seen.has(fingerprint)) continue;
    seen.add(fingerprint);
    result.push({ ...item, reason, change, weights, impact: top.map(([key]) => metricLabel(key)).join("、") });
  }
  return result;
}

function translateReason(item) {
  const raw = Array.isArray(item.reasons) ? item.reasons.join("；") : item.reason ?? item.status ?? "";
  const text = String(raw).toLowerCase();
  if (text.includes("sla")) return "检测到 SLA 达标率下降，系统提高完成时效、性能与可靠性权重。";
  if (text.includes("cost")) return "检测到成本超预算，系统提高成本权重并降低跨区域调度倾向。";
  if (text.includes("load")) return "检测到集群负载不均衡，系统提高负载均衡权重。";
  if (text.includes("latency") || text.includes("delay")) return "检测到稳健时延波动，系统提高完成时效与网络质量权重。";
  return raw || "控制面根据最近任务回放结果完成一次权重闭环调整。";
}

function formatTopWeights(weights) {
  return topWeights(weights, 3).map(([key, value]) => `${metricLabel(key)} ${pct(value, 0)}`).join(" / ");
}

function adjustmentMetrics(metrics = {}) {
  const parts = [];
  if (metrics.sla_rate !== undefined) parts.push(`SLA ${pct(metrics.sla_rate, 0)}`);
  if (metrics.failure_rate !== undefined) parts.push(`失败 ${pct(metrics.failure_rate, 0)}`);
  if (metrics.budget_violation_rate !== undefined) parts.push(`预算违规 ${pct(metrics.budget_violation_rate, 0)}`);
  if (metrics.load_imbalance !== undefined) parts.push(`负载不均衡 ${fmt(metrics.load_imbalance, 3)}`);
  return parts.join(" / ");
}

function currentWeights(report) {
  const decision = activeDecision(report, state.intentPayload);
  return { ...defaultWeights, ...(report?.policy_weights ?? {}), ...(decision?.weights ?? {}), ...draftWeights };
}

function previousWeights(report) {
  const history = report?.policy_history ?? report?.adjustment_history ?? [];
  return history.length ? history.at(-2)?.weights ?? history.at(-1)?.weights ?? {} : report?.policy_weights ?? {};
}

function activeTaskCount(report) {
  return Object.values(report?.task_statuses ?? {}).filter((status) => ["pending", "running", "scheduling", "assigned"].includes(status)).length;
}

function deltaText(delta) {
  if (Math.abs(delta) < 0.005) return "0%";
  return `${delta > 0 ? "+" : ""}${pct(delta, 0)}`;
}

function previewWeights() {
  const banner = document.getElementById("alertBanner");
  const weights = currentWeights(state.report);
    banner.textContent = `当前前三项：${formatTopWeights(weights) || "暂无修改"}`;
  banner.hidden = false;
}

async function submitWeights() {
  const banner = document.getElementById("alertBanner");
  try {
    const submittedWeights = currentWeights(state.report);
    await updatePolicyWeights({
      confirmed_by_user_button: true,
      weights: submittedWeights,
      reason: "用户手动提交多维策略权重。",
    });
    localTimeline.push({
      tick: "manual",
      scope: "人工提交",
      reason: "用户手动提交多维策略权重。",
      change: `提交 ${topWeights(submittedWeights, 3).map(([key]) => metricLabel(key)).join("、")} 优先策略`,
      affected_tasks: activeTaskCount(state.report),
      weights: submittedWeights,
    });
    banner.textContent = "策略提交成功，控制面将在下一次刷新后展示最新结果。";
    banner.hidden = false;
    draftWeights = {};
    selectedTemplate = "";
    emit("report:refresh");
  } catch (error) {
    banner.textContent = `策略提交失败：${error.message}`;
    banner.hidden = false;
  }
}
