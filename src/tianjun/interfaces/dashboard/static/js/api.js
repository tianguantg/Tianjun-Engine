const BASE = "";

export async function fetchReport() { return _get("/report"); }
export async function fetchHealth() { return _get("/health"); }
export async function startHermesSession(payload) { return _post("/chat/sessions", payload); }

export async function streamHermesChat(sessionId, message, signal) {
  const endpoint = sessionId
    ? `/chat/sessions/${encodeURIComponent(sessionId)}/messages/stream`
    : "/chat/sessions/stream";
  return fetch(BASE + endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });
}

export async function commitHermesPolicy(sessionId, payload) {
  return _post(`/chat/sessions/${encodeURIComponent(sessionId)}/commit`, payload);
}

export async function submitIntent(payload) { return _post("/intent", payload); }
export async function submitFeedback(payload) { return _post("/feedback", payload); }
export async function commitPolicy(payload) { return _post("/policies/commit", payload); }
export async function updatePolicyWeights(payload) { return _post("/policy-weights", payload); }
export async function cancelTaskRun(taskId, requeue = false) { return _post("/task-runs/cancel", { task_id: taskId, requeue }); }

async function _get(path) {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status}`);
  return r.json();
}

async function _post(path, body) {
  const r = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${path} -> ${r.status}`);
  return r.json();
}
