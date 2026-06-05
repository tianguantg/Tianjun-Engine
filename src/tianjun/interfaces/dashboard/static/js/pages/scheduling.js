import { initChat, updateAgentRuntimeStatus } from "../chat.js";
import { state } from "../state.js";
import { activeDecision, compactText, decisionScore, escapeHtml, fmt, gnnDisplayState, nodeLoad, pct, stableLatencyOf } from "../utils.js";

let initialized = false;
let selectedTaskId = null;

export function initScheduling() {
  document.getElementById("page-scheduling").innerHTML = `
    <header class="page-head">
      <div>
        <h1 class="page-title">调度决策</h1>
        <p class="page-subtitle">当前决策队列、候选节点对比与资源占用预测。</p>
      </div>
    </header>
    <section class="grid scheduling-engine">
      <article class="card decision-queue-panel">
        <h2 class="card-title title-teal">当前决策队列</h2>
        <div id="decisionQueue" class="decision-queue"></div>
      </article>
      <aside class="card node-compare-panel">
        <h2 class="card-title title-teal">节点对比面板</h2>
        <div id="nodeComparePanel" class="node-compare"></div>
      </aside>
    </section>
    <button id="hermesToggle" class="hermes-fab" type="button">咨询 Hermes</button>
    <aside id="hermesDrawer" class="hermes-drawer" hidden>
      <article class="card agent-workbench">
        <div class="drawer-head">
          <h2 class="card-title title-teal">Hermes 咨询</h2>
          <button id="hermesClose" class="btn-ghost btn-sm" type="button">收起</button>
        </div>
        <div id="chatLog" class="chat-log"></div>
        <section class="info-panel parse-panel"><h3>需求解析</h3><div id="requirementParse" class="kv-grid"></div></section>
        <div class="composer">
          <textarea id="intentInput" rows="4" placeholder="输入业务需求、约束或优化反馈。"></textarea>
          <div class="composer-actions">
            <button id="askButton" class="btn-primary">发送</button>
            <button id="stopHermesButton" class="btn-danger" disabled>暂停回复</button>
            <button id="submitButton" class="btn-danger" disabled>正式下发</button>
            <button id="endTaskButton" class="btn-ghost">新会话</button>
          </div>
        </div>
        <div class="details-grid hermes-runtime">
          <div class="field"><label>当前模型</label><b id="agentLlmMode">检查中</b></div>
          <div class="field"><label>当前阶段</label><b id="agentRuntimeMode">等待输入</b></div>
          <div class="field"><label>工具链状态</label><b id="agentToolMode">检查中</b></div>
          <div class="field"><label>策略状态</label><b id="intentSummaryStatus">等待需求</b></div>
        </div>
        <div id="schedulingContext" class="kv-grid context-grid"></div>
        <div id="intentSummaryBody" class="details-grid hidden-summary"></div>
        <p id="workspaceRisk" class="muted">等待策略生成后显示风险、确认要求和下发保护状态。</p>
        <section class="tool-trace-panel"><div class="tool-trace-head"><b>工具调用 / 决策过程</b><span id="toolTraceStatus">等待输入</span></div><div id="toolTraceSteps" class="tool-steps decision-steps"></div></section>
      </article>
    </aside>`;

  document.getElementById("hermesToggle").addEventListener("click", () => {
    document.getElementById("hermesDrawer").hidden = false;
    document.getElementById("hermesToggle").hidden = true;
  });
  document.getElementById("hermesClose").addEventListener("click", () => {
    document.getElementById("hermesDrawer").hidden = true;
    document.getElementById("hermesToggle").hidden = false;
  });
  document.getElementById("decisionQueue").addEventListener("click", (event) => {
    const item = event.target.closest("[data-task-id]");
    if (!item) return;
    selectedTaskId = item.dataset.taskId;
    renderScheduling(state.report, state.health);
  });
  initChat();
  initialized = true;
}

export function renderScheduling(report, health) {
  if (!initialized || !report) return;
  updateAgentRuntimeStatus();
  renderRequirement(report);
  renderContext(report, health);
  renderQueue(report);
  renderCompare(report);
}

function decisions(report) {
  const list = [
    ...state.interactionDecisions.map((entry) => entry.decision).filter(Boolean),
    ...(report.recent_decisions ?? []),
  ];
  const unique = new Map();
  for (const decision of list) unique.set(decision.task_id ?? decision.policy_id ?? decision.node_id, decision);
  return Array.from(unique.values()).sort((a, b) => decisionScore(b) - decisionScore(a));
}

function renderQueue(report) {
  const list = decisions(report).slice(0, 12);
  if (!selectedTaskId && list[0]) selectedTaskId = list[0].task_id;
  document.getElementById("decisionQueue").innerHTML = list.map((decision) => {
    const snap = decision.network_snapshot ?? {};
    const selected = decision.task_id === selectedTaskId;
    const load = predictedLoad(decision, report);
    return `<article class="decision-queue-item ${selected ? "active" : ""}" data-task-id="${escapeHtml(decision.task_id ?? "")}">
      <div class="item-row">
        <b>${escapeHtml(compactText(decision.task_id ?? "-", 42))}</b>
        <span class="badge badge-primary">评分 ${fmt(decisionScore(decision), 3)}</span>
      </div>
      <p class="muted">推荐节点 ${escapeHtml(decision.node_id ?? "-")} · 稳健时延 ${fmt(snap.deterministic_latency_ms ?? snap.stable_latency_ms, 1)}ms · ${escapeHtml(gnnDisplayState(snap, decision, report).compact)}</p>
      <div class="queue-item-foot">
        <span class="resource-forecast" tabindex="0">◌ 资源占用预测<i>执行后节点负载预计 ${pct(load.before, 0)} → ${pct(load.after, 0)}，CPU 占用约 +${pct(load.delta, 0)}</i></span>
        <span class="badge ${load.after > 0.8 ? "badge-danger" : load.after > 0.62 ? "badge-warning" : "badge-success"}">${load.after > 0.8 ? "高负载" : load.after > 0.62 ? "需观察" : "容量充足"}</span>
      </div>
    </article>`;
  }).join("") || "<div class=\"empty\">暂无决策队列。</div>";
}

function renderCompare(report) {
  const decision = decisions(report).find((item) => item.task_id === selectedTaskId) ?? activeDecision(report, state.intentPayload);
  const candidates = topCandidateNodes(report, decision).slice(0, 3);
  document.getElementById("nodeComparePanel").innerHTML = `
    <div class="compare-current">
      <span>当前任务</span>
      <b>${escapeHtml(decision?.task_id ?? "等待选择")}</b>
      <em>推荐节点 ${escapeHtml(decision?.node_id ?? "--")}</em>
    </div>
    <div class="compare-grid">
      ${candidates.map((node) => renderNodeCandidate(node, decision)).join("") || "<div class=\"empty\">暂无候选节点。</div>"}
    </div>`;
}

function renderNodeCandidate(node, decision) {
  const selected = node.node_id === decision?.node_id;
  const load = nodeLoad(node);
  const latency = stableLatencyOf(node);
  const score = selected ? decisionScore(decision) : Math.max(0, Number(node.health_score ?? 0) - load - latency / 100);
  return `<article class="node-candidate ${selected ? "recommended" : ""}">
    <div class="item-row"><b>${escapeHtml(compactText(node.node_id, 30))}</b><span class="badge ${selected ? "badge-success" : "badge-neutral"}">${selected ? "推荐" : "候选"}</span></div>
    <div class="candidate-bars">
      ${candidateBar("时延", 1 - Math.min(1, latency / 8), `${fmt(latency, 1)}ms`)}
      ${candidateBar("稳定性", Number(node.health_score ?? 0), pct(node.health_score ?? 0, 0))}
      ${candidateBar("负载", 1 - load, pct(load, 0))}
      ${candidateBar("评分", Math.min(1, score), fmt(score, 3))}
    </div>
  </article>`;
}

function candidateBar(label, value, text) {
  return `<div class="candidate-bar"><span>${escapeHtml(label)}</span><div class="track"><div class="bar teal" style="width:${Math.max(4, Math.min(100, value * 100))}%"></div></div><b>${escapeHtml(text)}</b></div>`;
}

function topCandidateNodes(report, decision) {
  return (report.nodes ?? []).slice().sort((a, b) => {
    if (a.node_id === decision?.node_id) return -1;
    if (b.node_id === decision?.node_id) return 1;
    const scoreA = Number(a.health_score ?? 0) - nodeLoad(a) - stableLatencyOf(a) / 100;
    const scoreB = Number(b.health_score ?? 0) - nodeLoad(b) - stableLatencyOf(b) / 100;
    return scoreB - scoreA;
  });
}

function predictedLoad(decision, report) {
  const node = (report.nodes ?? []).find((item) => item.node_id === decision?.node_id);
  const before = node ? nodeLoad(node) : 0.42;
  const demand = decision?.task?.demand ?? {};
  const delta = Math.min(0.22, 0.04 + Number(demand.cpu ?? demand.cpu_cores ?? 1) * 0.018);
  return { before, delta, after: Math.min(1, before + delta) };
}

function renderRequirement(report) {
  const payload = state.intentPayload ?? {};
  const task = payload.task ?? payload.submitted_task ?? {};
  const decision = activeDecision(report, state.intentPayload);
  const fields = [
    ["任务类型", task.task_type ?? "等待输入"],
    ["目标区域", decision?.network_snapshot?.physical_topology?.source_location ?? "--"],
    ["时延上限", task.max_latency_ms ? `${task.max_latency_ms} ms` : "未指定"],
    ["候选节点数量", String((report.nodes ?? []).filter((node) => node.online !== false).length)],
  ];
  const target = document.getElementById("requirementParse");
  if (target) target.innerHTML = fields.map(([k, v]) => `<div class="kv"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join("");
}

function renderContext(report, health) {
  const decision = activeDecision(report, state.intentPayload);
  const snap = decision?.network_snapshot ?? {};
  const fields = [
    ["LSTM", snap.model_prediction?.lstm_latency_ms !== undefined ? `${fmt(snap.model_prediction.lstm_latency_ms, 1)} ms` : "待预测"],
    ["GNN", gnnDisplayState(snap, decision, report).value],
    ["库存校验", `${(report.nodes ?? []).filter((node) => node.online !== false).length} 个在线节点`],
    ["指标评分", decision?.metric_scores ? "已生成" : "等待生成"],
    ["当前推荐节点", decision?.node_id ?? "等待推荐"],
    ["控制面状态", health?.status === "ok" ? "系统在线" : "检查中"],
  ];
  const target = document.getElementById("schedulingContext");
  if (target) target.innerHTML = fields.map(([k, v]) => `<div class="kv"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join("");
}
