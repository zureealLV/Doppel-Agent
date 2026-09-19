const $ = (selector) => document.querySelector(selector);
const state = { conversationId: null, conversations: [], runId: null, timer: null, busy: false, settings: null };

const presets = {
  deepseek: { base: "https://api.deepseek.com", model: "deepseek-flash" },
  openai: { base: "https://api.openai.com/v1", model: "" },
  local: { base: "http://127.0.0.1:1234/v1", model: "" },
  mock: { base: "", model: "mock" },
};

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

function currentConfig() {
  const preset = $("#preset").value;
  return {
    provider: preset === "mock" ? "mock" : "openai",
    preset,
    base_url: $("#base-url").value.trim(),
    model: $("#model").value.trim(),
    api_key: $("#api-key").value,
  };
}

function setProbe(message, type = "") {
  $("#probe-result").textContent = message;
  $("#probe-result").className = `inline-result ${type}`;
}

function setRunStatus(status) {
  const labels = { queued: "排队中", running: "运行中", completed: "已完成", failed: "失败", idle: "空闲" };
  const value = labels[status] || labels.idle;
  $("#run-status").textContent = value;
  $("#run-status").className = `run-state ${status || "idle"}`;
  $("#trace-indicator").textContent = `● ${value}`;
  $("#trace-indicator").className = `live-indicator ${status || "idle"}`;
}

function formatTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function appendMessage(message, pending = false) {
  const article = document.createElement("article");
  article.className = `message ${message.role}${pending ? " pending" : ""}`;
  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  avatar.textContent = message.role === "assistant" ? "D" : "YOU";
  const content = document.createElement("div");
  content.className = "message-content";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const author = document.createElement("strong");
  author.textContent = message.role === "assistant" ? "Doppel" : "你";
  const time = document.createElement("time");
  time.textContent = pending ? "正在处理" : formatTime(message.created_at);
  meta.append(author, time);
  const copy = document.createElement("div");
  copy.className = "message-copy";
  if (pending) {
    const dots = document.createElement("span");
    dots.className = "thinking-dots";
    dots.innerHTML = "<i></i><i></i><i></i>";
    copy.append(dots);
  } else {
    copy.textContent = message.content;
  }
  content.append(meta, copy);
  article.append(avatar, content);
  $("#message-list").append(article);
}

function renderConversation(conversation) {
  const messages = conversation?.messages || [];
  $("#conversation-title").textContent = conversation?.title || "新对话";
  $("#message-list").replaceChildren();
  $("#welcome").hidden = messages.length > 0;
  for (const message of messages) appendMessage(message);
  if (state.busy) appendMessage({ role: "assistant" }, true);
  requestAnimationFrame(() => { $("#message-scroll").scrollTop = $("#message-scroll").scrollHeight; });
}

async function loadConversation(conversationId) {
  if (!conversationId) {
    state.conversationId = null;
    renderConversation(null);
    renderConversationList();
    return;
  }
  const conversation = await api(`/api/conversations/${conversationId}`);
  state.conversationId = conversationId;
  renderConversation(conversation);
  renderConversationList();
}

function renderConversationList() {
  const list = $("#conversation-list");
  const query = $("#conversation-search").value.trim().toLowerCase();
  list.replaceChildren();
  const conversations = state.conversations.filter((item) => `${item.title} ${item.preview}`.toLowerCase().includes(query));
  if (!conversations.length) {
    const empty = document.createElement("p");
    empty.className = "list-empty";
    empty.textContent = query ? "没有匹配的对话" : "还没有保存的对话";
    list.append(empty);
    return;
  }
  for (const item of conversations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `conversation-item${item.id === state.conversationId ? " active" : ""}`;
    const text = document.createElement("span");
    const title = document.createElement("b");
    title.textContent = item.title;
    const preview = document.createElement("small");
    preview.textContent = item.preview || "空对话";
    text.append(title, preview);
    const time = document.createElement("time");
    time.textContent = formatTime(item.updated_at);
    button.append(text, time);
    button.addEventListener("click", () => { if (!state.busy) loadConversation(item.id).catch(showChatError); });
    list.append(button);
  }
}

async function refreshConversations(selectId = null) {
  state.conversations = await api("/api/conversations");
  renderConversationList();
  if (selectId) await loadConversation(selectId);
}

async function createConversation() {
  const conversation = await api("/api/conversations", "POST", { title: "新对话" });
  await refreshConversations(conversation.id);
  $("#prompt").focus();
  return conversation.id;
}

function eventDetail(event) {
  const payload = event.payload || {};
  if (event.kind === "tool_requested") return `${payload.name || "tool"}  ${JSON.stringify(payload.arguments || {}).slice(0, 130)}`;
  if (event.kind === "tool_failed") return String(payload.error || "").slice(0, 180);
  if (event.kind === "run_failed") return String(payload.reason || "").slice(0, 180);
  if (event.kind === "model_turn") return `第 ${payload.step} 步 · ${payload.tool_call_count || 0} 个工具调用`;
  if (event.kind === "model_usage") return `输入 ${payload.prompt_tokens ?? "?"} · 输出 ${payload.completion_tokens ?? "?"} tokens`;
  if (event.kind === "tool_completed") return `${payload.name} · ${payload.output_bytes} bytes`;
  return "";
}

function renderEvents(events) {
  const labels = { run_started: "任务开始", model_turn: "模型响应", model_usage: "Token 用量", tool_requested: "调用工具", tool_completed: "工具完成", tool_failed: "工具失败", run_completed: "任务完成", run_failed: "任务失败", context_watermark: "上下文水位", context_compacted: "上下文压缩", approval_requested: "请求授权", approval_decided: "授权结果" };
  const list = $("#event-list");
  list.replaceChildren();
  $("#event-count").textContent = `${events.length} 条`;
  if (!events.length) {
    const empty = document.createElement("li");
    empty.className = "trace-empty";
    empty.textContent = "等待任务事件…";
    list.append(empty);
    return;
  }
  for (const event of events.slice().reverse()) {
    const item = document.createElement("li");
    if (event.kind.includes("failed")) item.classList.add("failed");
    const kind = document.createElement("span");
    kind.className = "event-kind";
    kind.textContent = labels[event.kind] || event.kind;
    const meta = document.createElement("span");
    meta.className = "event-meta";
    meta.textContent = `#${String(event.sequence).padStart(2, "0")} · ${new Date(event.timestamp).toLocaleTimeString()}`;
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
  $("#task-count").textContent = `${tasks.length} 项`;
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
    status.textContent = task.status;
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
    summary.textContent = "查看完整参数";
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
        buttons.querySelectorAll("button").forEach((node) => { node.disabled = true; });
        try { await api(`/api/runs/${state.runId}/approvals/${approval.id}/decision`, "POST", { allow }); await pollRun(); }
        catch (error) { showChatError(error); }
      });
      buttons.append(button);
    }
    card.append(title, details, buttons);
    list.append(card);
  }
}

async function pollRun() {
  if (!state.runId) return;
  try {
    const [run, events, tasks, approvals] = await Promise.all([
      api(`/api/runs/${state.runId}`), api(`/api/runs/${state.runId}/events`),
      api(`/api/runs/${state.runId}/tasks`), api(`/api/runs/${state.runId}/approvals`),
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
      await refreshConversations();
      if (state.conversationId) await loadConversation(state.conversationId);
    }
  } catch (error) {
    clearInterval(state.timer);
    state.timer = null;
    state.busy = false;
    $("#run-button").disabled = false;
    setRunStatus("failed");
    showChatError(error);
  }
}

function showChatError(error) {
  $("#welcome").hidden = true;
  appendMessage({ role: "assistant", content: `发生错误：${error.message}`, created_at: new Date().toISOString() });
}

function applySettings(settings) {
  state.settings = settings;
  $("#preset").value = settings.preset || "deepseek";
  $("#base-url").value = settings.base_url || "";
  $("#model").value = settings.model || "";
  $("#api-key").value = "";
  $("#key-state").textContent = settings.api_key_saved ? `已加密保存 · ${settings.key_protection}` : "尚未保存";
  $("#forget-key").disabled = !settings.api_key_saved;
  $("#provider-summary").textContent = `${settings.model || "未选择模型"}${settings.api_key_saved ? " · Key 已保存" : ""}`;
  updatePresetDisabled();
}

function updatePresetDisabled() {
  const disabled = $("#preset").value === "mock";
  $("#base-url").disabled = disabled;
  $("#model").disabled = disabled;
  $("#api-key").disabled = disabled;
}

$("#preset").addEventListener("change", () => {
  const preset = presets[$("#preset").value];
  $("#base-url").value = preset.base;
  $("#model").value = preset.model;
  updatePresetDisabled();
  setProbe("尚未测试");
});

$("#toggle-key").addEventListener("click", () => {
  const input = $("#api-key");
  input.type = input.type === "password" ? "text" : "password";
  $("#toggle-key").textContent = input.type === "password" ? "显示" : "隐藏";
});

$("#open-settings").addEventListener("click", () => $("#settings-dialog").showModal());

$("#probe-button").addEventListener("click", async () => {
  $("#probe-button").disabled = true;
  setProbe("正在连接模型…");
  try {
    const result = await api("/api/probe", "POST", { config: currentConfig() });
    setProbe(`连接成功 · ${result.reply || "已响应"}`, "success");
  } catch (error) { setProbe(`连接失败 · ${error.message}`, "error"); }
  finally { $("#probe-button").disabled = false; }
});

$("#save-settings").addEventListener("click", async () => {
  $("#save-settings").disabled = true;
  try {
    const saved = await api("/api/settings", "POST", { config: currentConfig() });
    applySettings(saved);
    setProbe("设置已保存；API Key 已由 Windows DPAPI 加密。", "success");
  } catch (error) { setProbe(`保存失败 · ${error.message}`, "error"); }
  finally { $("#save-settings").disabled = false; }
});

$("#forget-key").addEventListener("click", async () => {
  try {
    const saved = await api("/api/settings", "POST", { config: currentConfig(), forget_key: true });
    applySettings(saved);
    setProbe("已删除保存的 API Key。", "success");
  } catch (error) { setProbe(`删除失败 · ${error.message}`, "error"); }
});

$("#new-conversation").addEventListener("click", () => { if (!state.busy) createConversation().catch(showChatError); });
$("#conversation-search").addEventListener("input", renderConversationList);

$("#rename-conversation").addEventListener("click", async () => {
  if (!state.conversationId || state.busy) return;
  const current = state.conversations.find((item) => item.id === state.conversationId);
  const title = window.prompt("新的对话名称", current?.title || "");
  if (!title?.trim()) return;
  await api(`/api/conversations/${state.conversationId}/rename`, "POST", { title });
  await refreshConversations(state.conversationId);
});

$("#delete-conversation").addEventListener("click", async () => {
  if (!state.conversationId || state.busy || !window.confirm("删除这条对话及其消息？运行审计记录仍会保留。")) return;
  await api(`/api/conversations/${state.conversationId}/delete`, "POST", {});
  state.conversationId = null;
  await refreshConversations();
  if (state.conversations.length) await loadConversation(state.conversations[0].id);
  else renderConversation(null);
});

for (const button of document.querySelectorAll("[data-prompt]")) {
  button.addEventListener("click", () => { $("#prompt").value = button.dataset.prompt; $("#prompt").focus(); });
}

for (const checkbox of document.querySelectorAll(".permission-popover input")) {
  checkbox.addEventListener("change", () => {
    const count = document.querySelectorAll(".permission-popover input:checked").length;
    $("#permission-count").textContent = count ? `${count} 项开启` : "只读";
  });
}

$("#run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;
  const prompt = $("#prompt").value.trim();
  if (!prompt) return;
  try {
    if (!state.conversationId) await createConversation();
    state.busy = true;
    $("#run-button").disabled = true;
    setRunStatus("queued");
    const result = await api("/api/runs", "POST", {
      prompt, conversation_id: state.conversationId, config: currentConfig(),
      allow_write: $("#allow-write").checked, allow_command: $("#allow-command").checked,
      allow_mcp: $("#allow-mcp").checked, allow_delegate: $("#allow-delegate").checked,
    });
    state.runId = result.run_id;
    $("#active-run-id").textContent = state.runId;
    $("#prompt").value = "";
    await refreshConversations();
    await loadConversation(state.conversationId);
    await pollRun();
    if (state.busy) state.timer = setInterval(pollRun, 700);
  } catch (error) {
    state.busy = false;
    $("#run-button").disabled = false;
    setRunStatus("failed");
    showChatError(error);
  }
});

$("#prompt").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("#run-form").requestSubmit(); }
});

async function initialize() {
  try {
    const [health, settings] = await Promise.all([api("/api/health"), api("/api/settings")]);
    $("#server-dot").classList.add("online");
    $("#server-status").textContent = "本地核心在线";
    $("#workspace-path").textContent = health.workspace;
    applySettings(settings);
    await refreshConversations();
    if (state.conversations.length) await loadConversation(state.conversations[0].id);
  } catch (error) {
    $("#server-status").textContent = "本地核心离线";
    showChatError(error);
  }
}

initialize();
