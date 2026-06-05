import { fetchHealth, fetchReport } from "./api.js";
import { emit, on, state } from "./state.js";
import { modelStatusText } from "./utils.js";
import { initOverview, renderOverview } from "./pages/overview.js";
import { initScheduling, renderScheduling } from "./pages/scheduling.js";
import { initTopology, renderTopology } from "./pages/topology.js";
import { initTasks, renderTasks } from "./pages/tasks.js";
import { initModel, renderModel } from "./pages/model.js";

const PAGES = ["overview", "scheduling", "topology", "tasks", "model"];
const renderers = {
  overview: renderOverview,
  scheduling: renderScheduling,
  topology: renderTopology,
  tasks: renderTasks,
  model: renderModel,
};

function navigate(to, updateHash = true) {
  if (!PAGES.includes(to)) to = "overview";
  const current = document.querySelector(".page:not([hidden])");
  const target = document.getElementById(`page-${to}`);
  state.activePage = to;
  document.querySelectorAll(".tab-btn").forEach((button) => button.classList.toggle("active", button.dataset.page === to));
  if (updateHash && location.hash.slice(1) !== to) history.replaceState(null, "", `#${to}`);
  if (!target) {
    renderActive();
    return;
  }

  if (current === target) {
    renderActive();
    return;
  }

  if (current) {
    current.hidden = true;
    current.classList.remove("page-exit");
  }
  target.hidden = false;
  target.classList.add("page-enter");
  renderActive();
  requestAnimationFrame(() => target.classList.remove("page-enter"));
}

function renderActive() {
  renderers[state.activePage]?.(state.report, state.health);
}

async function refreshDashboard() {
  try {
    const [report, health] = await Promise.all([fetchReport(), fetchHealth()]);
    state.report = report;
    state.health = health;
    emit("report", { report, health });
    emit("health", health);
    renderActive();
    updateTopnav(report, health);
    updateAlertBanner(health);
  } catch (error) {
    const dot = document.getElementById("statusDot");
    dot.className = "status-dot error";
    document.getElementById("systemStatus").textContent = "系统离线";
    document.getElementById("modelStatus").textContent = "连接失败";
    document.getElementById("hermesLlmStatus").textContent = error.message;
  }
}

function updateTopnav(report, health) {
  const dot = document.getElementById("statusDot");
  dot.className = `status-dot ${health?.status === "ok" ? "ok" : "error"}`;
  document.getElementById("systemStatus").className = `badge ${health?.status === "ok" ? "badge-success" : "badge-danger"}`;
  document.getElementById("systemStatus").textContent = health?.status === "ok" ? "系统在线" : "系统异常";
  document.getElementById("modelStatus").className = "badge badge-primary";
  document.getElementById("modelStatus").textContent = modelStatusText(report?.model_runtime ?? health?.model_runtime ?? {});
  const llm = health?.chat_runtime?.llm ?? {};
  document.getElementById("hermesLlmStatus").className = `badge ${llm.enabled ? "badge-success" : "badge-neutral"}`;
  document.getElementById("hermesLlmStatus").textContent = llm.enabled ? `当前模型 ${llm.settings?.model || "LLM 已启用"}` : "本地规则";
  document.getElementById("autoRefreshStatus").textContent = "自动刷新中";
  document.getElementById("lastSync").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function updateAlertBanner(health) {
  const banner = document.getElementById("alertBanner");
  const issues = health?.issues ?? [];
  if (issues.length) {
    banner.textContent = `警告 ${issues.join(" / ")}`;
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

export function schedulePolling(intervalMs = 5000) {
  if (state.pollHandle) clearInterval(state.pollHandle);
  state.pollHandle = setInterval(() => void refreshDashboard(), intervalMs);
  return state.pollHandle;
}

function init() {
  initOverview();
  initScheduling();
  initTopology();
  initTasks();
  initModel();
  document.querySelectorAll(".tab-btn").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.page)));
  window.addEventListener("hashchange", () => navigate(location.hash.slice(1), false));
  document.getElementById("refreshButton").addEventListener("click", () => void refreshDashboard());
  on("report:refresh", () => void refreshDashboard());
  const initial = PAGES.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview";
  document.querySelectorAll(".page").forEach((page) => {
    page.hidden = page.id !== `page-${initial}`;
  });
  navigate(initial, false);
  void refreshDashboard();
  schedulePolling();
}

init();
