import { commitHermesPolicy, streamHermesChat } from "./api.js";
import { emit, rememberIntentPayload, state } from "./state.js";
import { escapeHtml, renderInlineMarkdown } from "./utils.js";

const STEPS = [
  ["understand", "解析业务目标", "识别任务类型与约束"],
  ["inventory", "库存校验", "查询节点与资源余量"],
  ["lstm", "LSTM 时延预测", "预测稳定时延"],
  ["gnn", "GNN 稳定性评估", "评估拓扑风险"],
  ["candidate", "生成候选节点", "过滤可用节点"],
  ["fusion", "融合评分", "计算多维指标评分"],
  ["reply", "输出推荐节点", "生成解释与确认动作"],
];

export function setHermesBusy(isBusy) {
  state.hermesBusy = isBusy;
  document.getElementById("askButton")?.toggleAttribute("disabled", isBusy);
  document.getElementById("stopHermesButton")?.toggleAttribute("disabled", !isBusy);
}

export function stopHermesStream() {
  state.abortController?.abort();
  state.abortController = null;
}

export function resetToolTrace() {
  const host = document.getElementById("toolTraceSteps");
  if (!host) return;
  document.getElementById("toolTraceStatus").textContent = "等待输入";
  host.innerHTML = STEPS.map(([, label, desc]) => `<div class="tool-step"><b>${label}</b><span>${desc}</span></div>`).join("");
}

export function updateToolTrace(event) {
  const host = document.getElementById("toolTraceSteps");
  if (!host) return;
  const stage = toolStageForName(event.tool);
  let card = Array.from(host.querySelectorAll(".tool-step")).find((item) => item.querySelector("b")?.textContent === stage);
  if (!card) {
    card = document.createElement("div");
    card.className = "tool-step";
    card.innerHTML = `<b>${escapeHtml(stage)}</b><span>等待</span>`;
    host.appendChild(card);
  }
  const running = event.type === "tool_start";
  card.classList.toggle("running", running);
  card.classList.toggle("done", !running);
  card.querySelector("span").textContent = running ? "运行中" : (event.summary || event.label || "完成");
  document.getElementById("toolTraceStatus").textContent = running ? `${stage}中` : `${stage}完成`;
}

function toolStageForName(name) {
  const map = {
    analyze_user_intent: "需求理解",
    start_requirement_dialogue: "需求理解",
    continue_requirement_dialogue: "需求理解",
    get_cluster_state: "库存核验",
    draft_compute_network_policy: "融合评分",
    compare_policy_options: "多方案对比",
    explain_policy: "锁定方案",
    optimize_policy_from_feedback: "融合评分",
    simulate_policy: "生成候选节点",
    commit_policy: "输出推荐节点",
    schedule_pending_task: "输出推荐节点",
  };
  return map[name] || String(name || "工具调用");
}

export function renderStreamSteps() {
  return `<div class="stream-steps">${STEPS.map(([id, label, desc]) => `<div class="stream-step pending" data-step="${id}"><b>${label}</b><span>${desc}</span></div>`).join("")}</div>`;
}

export function stepIdFromEvent(event) {
  const tool = event.tool || "";
  if (event.type?.startsWith("llm")) return "understand";
  if (tool === "get_cluster_state") return "inventory";
  if (tool.includes("requirement")) return "understand";
  if (tool === "draft_compute_network_policy" || tool === "compare_policy_options" || tool === "optimize_policy_from_feedback") return "fusion";
  if (tool === "explain_policy") return "candidate";
  if (tool === "simulate_policy") return "candidate";
  if (event.type === "assistant_delta") return "reply";
  return null;
}

export function updateStreamStep(container, stepId, status, detail) {
  const step = container?.querySelector(`[data-step="${stepId}"]`);
  if (!step) return;
  step.className = `stream-step ${status}`;
  if (detail) step.querySelector("span").textContent = detail;
}

export function finishAllStreamSteps(container) {
  STEPS.forEach(([id]) => updateStreamStep(container, id, "done", null));
}

export function addMessage(role, html) {
  const log = document.getElementById("chatLog");
  if (!log) return null;
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.innerHTML = html;
  log.appendChild(node);
  log.scrollTop = log.scrollHeight;
  return node;
}

export function rememberTaskConversation(role, content) {
  const text = String(content ?? "").trim();
  if (text) state.hermesHistory.push({ role, content: text });
}

export function currentTaskHistory() {
  return state.hermesHistory.slice(-8);
}

export function resetIntentSummary() {
  const body = document.getElementById("intentSummaryBody");
  if (body) body.innerHTML = ["目标", "业务类型", "地域", "时延目标", "预算上限", "安全等级"].map((label) => `<div class="field"><label>${label}</label><b>--</b></div>`).join("");
  document.getElementById("intentSummaryStatus").textContent = "等待需求";
}

export function endTaskConversation() {
  stopHermesStream();
  state.hermesSessionId = null;
  state.hermesPolicyId = null;
  state.hermesHistory = [];
  document.getElementById("chatLog").innerHTML = welcomeMessage();
  resetIntentSummary();
  resetToolTrace();
  updateSubmitButton();
}

export function updateIntentSummary(payload, dryRun) {
  const task = payload?.task ?? payload?.submitted_task ?? {};
  const demand = task.demand ?? {};
  const decision = payload?.preview_decision ?? payload?.policy?.decision;
  document.getElementById("intentSummaryStatus").textContent = dryRun ? "已生成预览" : "已正式下发";
  const fields = [
    ["目标", task.task_id ?? payload?.policy?.policy_id ?? "--"],
    ["业务类型", task.task_type ?? payload?.policy?.requirement?.workload_type ?? "--"],
    ["地域", task.source_region ?? task.data_region ?? decision?.region ?? "--"],
    ["时延目标", task.max_latency_ms ? `${task.max_latency_ms} ms` : "--"],
    ["预算上限", demand.cost ?? "--"],
    ["安全等级", task.security_level ?? payload?.policy?.requirement?.security_level ?? "--"],
  ];
  document.getElementById("intentSummaryBody").innerHTML = fields.map(([k, v]) => `<div class="field"><label>${escapeHtml(k)}</label><b>${escapeHtml(v)}</b></div>`).join("");
}

export function updateWorkspaceFromPolicy(policy, simulation = null) {
  if (!policy) return;
  updateIntentSummary({ policy }, true);
  const risk = document.getElementById("workspaceRisk");
  if (risk) risk.textContent = (simulation?.risks || policy.explanation?.risks || []).join("；") || "未识别到阻断风险，正式下发仍需要按钮确认。";
  state.hermesPolicyId = policy.policy_id || state.hermesPolicyId;
  updateSubmitButton();
}

export async function askHermesStreamingActual() {
  const input = document.getElementById("intentInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  stopHermesStream();
  resetToolTrace();
  rememberTaskConversation("user", message);
  addMessage("user", `<b>你</b><p>${escapeHtml(message)}</p>`);
  const node = addMessage("assistant", `<b>天钧智能体</b><div class="stream-content">${renderStreamSteps()}</div>`);
  const content = node.querySelector(".stream-content");
  const controller = new AbortController();
  state.abortController = controller;
  setHermesBusy(true);
  let fullText = "";
  try {
    const res = await streamHermesChat(state.hermesSessionId, message, controller.signal);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";
      for (const eventText of events) handleEvent(eventText, content, (text) => { fullText += text; });
    }
    if (fullText) rememberTaskConversation("assistant", fullText);
  } catch (error) {
    content.innerHTML = error.name === "AbortError" ? renderText(`${fullText}\n\n[已暂停]`) : renderText(`Hermes 接口失败：${error.message}`);
  } finally {
    finishAllStreamSteps(content);
    state.abortController = null;
    setHermesBusy(false);
  }
}

function handleEvent(eventText, content, append) {
  const dataLine = eventText.split("\n").find((line) => line.startsWith("data:"));
  if (!dataLine) return;
  const payload = JSON.parse(dataLine.slice(5).trim());
  if (payload.type === "assistant_delta") {
    append(payload.delta || "");
    const current = content.dataset.text = `${content.dataset.text || ""}${payload.delta || ""}`;
    content.innerHTML = renderText(current);
    updateStreamStep(content, "reply", "running", null);
  } else if (payload.type === "session") {
    state.hermesSessionId = payload.session?.session_id || state.hermesSessionId;
  } else if (payload.type === "tool_start" || payload.type === "tool_done" || payload.type === "tool_result") {
    updateToolTrace(payload);
    const id = stepIdFromEvent(payload);
    if (id) updateStreamStep(content, id, payload.type === "tool_start" ? "running" : "done", payload.summary || payload.label);
  } else if (payload.type === "artifacts") {
    const artifacts = payload.artifacts || {};
    if (artifacts.policy) updateWorkspaceFromPolicy(artifacts.policy, artifacts.simulation);
    if (artifacts.optimization?.policy) updateWorkspaceFromPolicy(artifacts.optimization.policy, artifacts.simulation);
    if (artifacts.policy_options) {
      state.hermesPolicyId = null;
      updateSubmitButton();
    }
  } else if (payload.type === "done") {
    const result = payload.result || {};
    state.hermesSessionId = result.session?.session_id || state.hermesSessionId;
    state.hermesPolicyId = result.commit_policy_id || result.artifacts?.policy?.policy_id || state.hermesPolicyId;
    if (result.artifacts?.policy) updateWorkspaceFromPolicy(result.artifacts.policy, result.artifacts.simulation);
    updateSubmitButton();
    emit("report:refresh");
  } else if (payload.type === "error") {
    content.innerHTML = renderText(payload.message || payload.error || "流式响应异常");
  }
}

export async function commitHermesPolicyActual() {
  if (!state.hermesSessionId || !state.hermesPolicyId) {
    addMessage("assistant", "<b>尚无可下发策略</b><p>请先补充完整任务需求，生成推荐策略后再提交。</p>");
    return;
  }
  try {
    updateToolTrace({ type: "tool_start", tool: "commit_policy" });
    const result = await commitHermesPolicy(state.hermesSessionId, { policy_id: state.hermesPolicyId });
    if (result.dashboard_payload) {
      rememberIntentPayload(result.dashboard_payload, false, "正式下发");
      updateIntentSummary(result.dashboard_payload, false);
    }
    addMessage("assistant", `<b>正式下发结果</b>${renderText(result.message || "策略已提交。")}`);
    state.hermesPolicyId = null;
    updateToolTrace({ type: "tool_done", tool: "commit_policy", summary: "任务已提交" });
    updateSubmitButton();
    emit("report:refresh");
  } catch (error) {
    addMessage("assistant", `<b>策略下发失败</b><p>${escapeHtml(error.message)}</p>`);
  }
}

export function updateSubmitButton() {
  const button = document.getElementById("submitButton");
  if (!button) return;
  button.disabled = !state.hermesPolicyId;
  button.textContent = state.hermesPolicyId ? "正式下发" : "等待策略";
}

export function updateAgentRuntimeStatus() {
  const llm = state.health?.chat_runtime?.llm ?? {};
  document.getElementById("agentLlmMode").textContent = llm.enabled ? (llm.settings?.model || "LLM 已启用") : "本地规则";
  document.getElementById("agentRuntimeMode").textContent = state.hermesSessionId ? "会话进行中" : "等待会话";
  document.getElementById("agentToolMode").textContent = state.health?.status === "ok" ? "可用" : "检查中";
}

function renderText(text) {
  const html = escapeHtml(text).split(/\n{2,}/).map((part) => `<p>${renderInlineMarkdown(part).replace(/\n/g, "<br>")}</p>`).join("");
  return `<div class="md-body">${html}</div>`;
}

function welcomeMessage() {
  return "<div class=\"message assistant\"><b>天钧智能体</b><p>已连接实时控制面。请给出业务目标、地域、时延、安全或资源约束，我会生成策略与仿真结论。</p></div>";
}

export function initChat() {
  document.getElementById("chatLog").innerHTML = welcomeMessage();
  document.getElementById("askButton").addEventListener("click", () => void askHermesStreamingActual());
  document.getElementById("stopHermesButton").addEventListener("click", stopHermesStream);
  document.getElementById("submitButton").addEventListener("click", () => void commitHermesPolicyActual());
  document.getElementById("endTaskButton").addEventListener("click", endTaskConversation);
  resetIntentSummary();
  resetToolTrace();
  updateSubmitButton();
}
