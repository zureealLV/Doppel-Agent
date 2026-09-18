const $ = (selector) => document.querySelector(selector);
const state = { runId: null, timer: null, busy: false };

const presets = {
  deepseek: { base: "https://api.deepseek.com", model: "deepseek-flash" },
  openai: { base: "https://api.openai.com/v1", model: "" },
  local: { base: "http://127.0.0.1:1234/v1", model: "" },
  mock: { base: "", model: "mock" },
};

function config() {
  return {
    provider: $("#preset").value === "mock" ? "mock" : "openai",
    base_url: $("#base-url").value.trim(),
    model: $("#model").value.trim(),
    api_key: $("#api-key").value,
  };
}

async function api(path, method = "GET", body = undefined) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.headers["X-Doppel-UI"] = "1";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function setProbe(message, type = "") {
  const target = $("#probe-result");
  target.textContent = message;
  target.className = `inline-result ${type}`;
}

function setRunStatus(status) {
  const target = $("#run-status");
  const labels = { queued: "等待执行", running: "正在执行", completed: "已完成", failed: "执行失败" };
  target.textContent = labels[status] || "等待任务";
  target.className = `result-state ${status || "idle"}`;
  $("#trace-indicator").textContent = status === "running" ? "● LIVE" : `● ${(status || "idle").toUpperCase()}`;
}

function eventDetail(event) {
  const payload = event.payload || {};
  if (event.kind === "tool_requested") return `${payload.name || "tool"}  ${JSON.stringify(payload.arguments || {}).slice(0, 120)}`;
  if (event.kind === "tool_failed") return String(payload.error || "").slice(0, 160);
  if (event.kind === "run_failed") return String(payload.reason || "").slice(0, 160);
  if (event.kind === "model_turn") return `第 ${payload.step} 步 · ${payload.tool_call_count || 0} 个工具调用`;
  if (event.kind === "tool_completed") return `${payload.name} · ${payload.output_bytes} bytes`;
  return "";
}

function renderEvents(events) {
  const list = $("#event-list");
  list.replaceChildren();
  $("#event-count").textContent = `${events.length} EVENTS`;
  if (!events.length) {
    const empty = document.createElement("li");
    empty.className = "trace-empty";
    empty.textContent = "等待事件流…";
    list.append(empty);
    return;
  }
  for (const event of events) {
    const item = document.createElement("li");
    if (event.kind.includes("failed")) item.classList.add("failed");
    const kind = document.createElement("span");
    kind.className = "event-kind";
    kind.textContent = event.kind.replaceAll("_", " ").toUpperCase();
    const meta = document.createElement("span");
    meta.className = "event-meta";
    meta.textContent = `#${String(event.sequence).padStart(2, "0")} / ${new Date(event.timestamp).toLocaleTimeString()}`;
    item.append(kind, meta);
    const detail = eventDetail(event);
    if (detail) {
      const line = document.createElement("span");
      line.className = "event-detail";
      line.textContent = detail;
      item.append(line);
    }
    list.append(item);
  }
}

function renderTasks(tasks) {
  const list = $("#task-list");
  list.replaceChildren();
  $("#task-count").textContent = `${tasks.length} TASKS`;
  if (!tasks.length) {
    const item = document.createElement("li");
    item.className = "trace-empty";
    item.textContent = "任务创建后显示在这里。";
    list.append(item);
    return;
  }
  for (const task of tasks) {
    const item = document.createElement("li");
    const title = document.createElement("strong");
    title.textContent = task.title;
    const status = document.createElement("span");
    status.textContent = task.status.toUpperCase();
    status.className = `task-state ${task.status}`;
    item.append(title, status);
    list.append(item);
  }
}

function renderApprovals(approvals) {
  const section = $("#approval-section");
  const list = $("#approval-list");
  section.hidden = approvals.length === 0;
  list.replaceChildren();
  for (const approval of approvals) {
    const card = document.createElement("div");
    card.className = "approval-card";
    const title = document.createElement("strong");
    title.textContent = `${approval.tool} · ${approval.capability}`;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "查看完整工具参数";
    const body = document.createElement("pre");
    body.textContent = JSON.stringify(approval.arguments, null, 2);
    details.append(summary, body);
    const buttons = document.createElement("div");
    buttons.className = "approval-actions";
    for (const [label, allow] of [["拒绝", false], ["本次允许", true]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.className = allow ? "approve-button" : "deny-button";
      button.addEventListener("click", async () => {
        buttons.querySelectorAll("button").forEach((item) => { item.disabled = true; });
        try {
          await api(`/api/runs/${state.runId}/approvals/${approval.id}/decision`, "POST", { allow });
          await pollRun();
        } catch (error) {
          setProbe(`审批失败 · ${error.message}`, "error");
        }
      });
      buttons.append(button);
    }
    card.append(title, details, buttons);
    list.append(card);
  }
}

async function refreshRuns() {
  const runs = await api("/api/runs");
  const list = $("#recent-runs");
  list.replaceChildren();
  if (!runs.length) {
    list.textContent = "暂无已保存的运行。";
    return;
  }
  for (const run of runs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "recent-run";
    button.textContent = `${run.status.toUpperCase()} · ${(run.prompt || run.run_id).slice(0, 90)}`;
    button.addEventListener("click", async () => {
      if (state.busy) return;
      state.runId = run.run_id;
      $("#empty-state").hidden = true;
      $("#run-id").textContent = `RUN ID / ${state.runId}`;
      $("#run-id").hidden = false;
      await pollRun();
      if (run.status === "queued" || run.status === "running") {
        state.busy = true;
        state.timer = setInterval(pollRun, 700);
      }
    });
    list.append(button);
  }
}

async function pollRun() {
  if (!state.runId) return;
  try {
    const [run, events, tasks, approvals] = await Promise.all([
      api(`/api/runs/${state.runId}`),
      api(`/api/runs/${state.runId}/events`),
      api(`/api/runs/${state.runId}/tasks`),
      api(`/api/runs/${state.runId}/approvals`),
    ]);
    setRunStatus(run.status);
    renderEvents(events);
    renderTasks(tasks);
    renderApprovals(approvals);
    if (run.status === "completed" || run.status === "failed") {
      clearInterval(state.timer);
      state.timer = null;
      state.busy = false;
      $("#run-button").disabled = false;
      $("#run-button").innerHTML = '<span class="run-icon" aria-hidden="true">▶</span> 启动任务';
      $("#answer").textContent = run.answer || "（模型没有返回文本）";
      $("#answer").hidden = false;
      refreshRuns().catch(() => {});
    }
  } catch (error) {
    clearInterval(state.timer);
    state.timer = null;
    state.busy = false;
    $("#run-button").disabled = false;
    setRunStatus("failed");
    $("#answer").textContent = `无法读取运行状态：${error.message}`;
    $("#answer").hidden = false;
  }
}

$("#preset").addEventListener("change", () => {
  const preset = presets[$("#preset").value];
  $("#base-url").value = preset.base;
  $("#model").value = preset.model;
  const disabled = $("#preset").value === "mock";
  $("#base-url").disabled = disabled;
  $("#model").disabled = disabled;
  $("#api-key").disabled = disabled;
  setProbe("尚未测试");
});

$("#toggle-key").addEventListener("click", () => {
  const input = $("#api-key");
  input.type = input.type === "password" ? "text" : "password";
  $("#toggle-key").textContent = input.type === "password" ? "显示" : "隐藏";
  $("#toggle-key").setAttribute("aria-label", input.type === "password" ? "显示 API Key" : "隐藏 API Key");
});

$("#probe-button").addEventListener("click", async () => {
  const button = $("#probe-button");
  button.disabled = true;
  setProbe("正在连接模型…");
  try {
    const result = await api("/api/probe", "POST", { config: config() });
    setProbe(`连接成功 · ${result.reply || "已响应"}`, "success");
  } catch (error) {
    setProbe(`连接失败 · ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
});

$("#run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;
  const prompt = $("#prompt").value.trim();
  if (!prompt) { $("#prompt").focus(); return; }
  state.busy = true;
  $("#run-button").disabled = true;
  $("#run-button").textContent = "正在提交…";
  $("#empty-state").hidden = true;
  $("#answer").hidden = true;
  renderEvents([]);
  renderTasks([]);
  renderApprovals([]);
  setRunStatus("queued");
  try {
    const result = await api("/api/runs", "POST", {
      prompt, config: config(),
      allow_write: $("#allow-write").checked,
      allow_command: $("#allow-command").checked,
      allow_mcp: $("#allow-mcp").checked,
      allow_delegate: $("#allow-delegate").checked,
    });
    state.runId = result.run_id;
    refreshRuns().catch(() => {});
    $("#run-id").textContent = `RUN ID / ${state.runId}`;
    $("#run-id").hidden = false;
    await pollRun();
    if (state.busy) state.timer = setInterval(pollRun, 700);
  } catch (error) {
    state.busy = false;
    $("#run-button").disabled = false;
    $("#run-button").innerHTML = '<span class="run-icon" aria-hidden="true">▶</span> 启动任务';
    setRunStatus("failed");
    $("#answer").textContent = `任务未能提交：${error.message}`;
    $("#answer").hidden = false;
  }
});

$("#prompt").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.ctrlKey) {
    event.preventDefault();
    $("#run-form").requestSubmit();
  }
});

api("/api/health").then((health) => {
  $("#server-dot").classList.add("online");
  $("#server-status").textContent = "本地核心在线";
  $("#workspace-path").textContent = health.workspace;
  refreshRuns().catch(() => {});
}).catch(() => {
  $("#server-status").textContent = "本地核心离线";
  $("#workspace-path").textContent = "无法连接";
});

$("#refresh-runs").addEventListener("click", () => refreshRuns().catch((error) => {
  $("#recent-runs").textContent = `读取失败：${error.message}`;
}));
