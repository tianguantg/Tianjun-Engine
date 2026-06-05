export const state = {
  report: null,
  health: null,
  activePage: "overview",
  hermesSessionId: null,
  hermesPolicyId: null,
  hermesHistory: [],
  intentPayload: null,
  interactionDecisions: [],
  hermesBusy: false,
  abortController: null,
  pollHandle: null,
};

const listeners = {};

export function on(event, fn) {
  (listeners[event] ??= []).push(fn);
}

export function off(event, fn) {
  listeners[event] = (listeners[event] ?? []).filter((item) => item !== fn);
}

export function emit(event, data) {
  (listeners[event] ?? []).forEach((fn) => fn(data));
}

export function rememberIntentPayload(payload, dryRun, mode = "Hermes") {
  state.intentPayload = payload;
  const decision = payload?.preview_decision ?? payload?.policy?.decision ?? null;
  const task = payload?.task ?? payload?.submitted_task ?? null;
  state.interactionDecisions.unshift({
    at: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
    dryRun: Boolean(dryRun),
    mode,
    status: payload?.status ?? (dryRun ? "preview" : "committed"),
    task,
    decision,
  });
  state.interactionDecisions = state.interactionDecisions.slice(0, 12);
}
