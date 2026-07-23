const state = {
  agents: [],
  sessions: [],
  activeSessionId: null,
  messages: [],
  isStreaming: false,
  settings: null,
  builderMode: false,
  flow: null,
  builderPython: "",
  traceMode: false,
  traceRuns: [],
  activeTraceId: null,
  activeTrace: null,
};

const elements = {
  agentSelect: document.querySelector("#agent-select"),
  builderCode: document.querySelector("#builder-code"),
  builderForm: document.querySelector("#builder-form"),
  builderLog: document.querySelector("#builder-log"),
  builderNodeCount: document.querySelector("#builder-node-count"),
  builderPrompt: document.querySelector("#builder-prompt"),
  builderShell: document.querySelector("#builder-shell"),
  builderToggle: document.querySelector("#builder-toggle"),
  composer: document.querySelector("#composer"),
  copyPython: document.querySelector("#copy-python"),
  copyPythonStatus: document.querySelector("#copy-python-status"),
  flowCanvas: document.querySelector("#flow-canvas"),
  flowNameInput: document.querySelector("#flow-name-input"),
  flowPromptInput: document.querySelector("#flow-prompt-input"),
  modelSelect: document.querySelector("#model-select"),
  messages: document.querySelector("#messages"),
  newChat: document.querySelector("#new-chat"),
  prompt: document.querySelector("#prompt"),
  providerSelect: document.querySelector("#provider-select"),
  root: document.querySelector("#chat-root"),
  saveSettings: document.querySelector("#save-settings"),
  send: document.querySelector("#send"),
  sessions: document.querySelector("#sessions"),
  settingsClose: document.querySelector("#settings-close"),
  settingsPanel: document.querySelector("#settings-panel"),
  settingsToggle: document.querySelector("#settings-toggle"),
  sidebarOverlay: document.querySelector("#sidebar-overlay"),
  sidebarToggle: document.querySelector("#sidebar-toggle"),
  status: document.querySelector("#status"),
  temperatureInput: document.querySelector("#temperature-input"),
  temperatureValue: document.querySelector("#temperature-value"),
  thinkingSelect: document.querySelector("#thinking-select"),
  traceDetail: document.querySelector("#trace-detail"),
  traceRefresh: document.querySelector("#trace-refresh"),
  traceRuns: document.querySelector("#trace-runs"),
  traceShell: document.querySelector("#trace-shell"),
  tracesToggle: document.querySelector("#traces-toggle"),
};

function setStatus(text) {
  elements.status.textContent = text;
}

function isMobileLayout() {
  return window.matchMedia("(max-width: 760px)").matches;
}

function syncSidebarState() {
  if (isMobileLayout()) {
    elements.root.classList.remove("sidebar-collapsed");
    const open = elements.root.classList.contains("sidebar-open");
    elements.sidebarOverlay.hidden = !open;
    elements.sidebarToggle.setAttribute("aria-expanded", String(open));
    return;
  }
  elements.root.classList.remove("sidebar-open");
  elements.sidebarOverlay.hidden = true;
  elements.sidebarToggle.setAttribute(
    "aria-expanded",
    String(!elements.root.classList.contains("sidebar-collapsed")),
  );
}

function toggleSidebar(open) {
  if (isMobileLayout()) {
    const nextOpen = typeof open === "boolean" ? open : !elements.root.classList.contains("sidebar-open");
    elements.root.classList.toggle("sidebar-open", nextOpen);
  } else {
    const nextOpen = typeof open === "boolean" ? open : elements.root.classList.contains("sidebar-collapsed");
    elements.root.classList.toggle("sidebar-collapsed", !nextOpen);
  }
  syncSidebarState();
}

function toggleSettings(open) {
  const nextOpen = typeof open === "boolean" ? open : elements.settingsPanel.hidden;
  elements.settingsPanel.hidden = !nextOpen;
  elements.settingsToggle.setAttribute("aria-expanded", String(nextOpen));
}

function toggleBuilderMode(open) {
  const nextOpen = typeof open === "boolean" ? open : !state.builderMode;
  state.builderMode = nextOpen;
  state.traceMode = false;
  elements.builderShell.hidden = !nextOpen;
  elements.traceShell.hidden = true;
  elements.messages.hidden = nextOpen;
  elements.composer.hidden = nextOpen;
  elements.root.classList.toggle("builder-active", nextOpen);
  elements.root.classList.toggle("trace-active", false);
  elements.builderToggle.setAttribute("aria-pressed", String(nextOpen));
  elements.builderToggle.classList.toggle("active", nextOpen);
  elements.tracesToggle.setAttribute("aria-pressed", "false");
  elements.tracesToggle.classList.remove("active");
  if (nextOpen && !state.flow) {
    loadBuilderFlow().catch((error) => {
      setStatus("Error");
      console.error(error);
    });
  }
}

function toggleTraceMode(open) {
  const nextOpen = typeof open === "boolean" ? open : !state.traceMode;
  state.traceMode = nextOpen;
  state.builderMode = false;
  elements.traceShell.hidden = !nextOpen;
  elements.builderShell.hidden = true;
  elements.messages.hidden = nextOpen;
  elements.composer.hidden = nextOpen;
  elements.root.classList.toggle("trace-active", nextOpen);
  elements.root.classList.toggle("builder-active", false);
  elements.tracesToggle.setAttribute("aria-pressed", String(nextOpen));
  elements.tracesToggle.classList.toggle("active", nextOpen);
  elements.builderToggle.setAttribute("aria-pressed", "false");
  elements.builderToggle.classList.remove("active");
  if (nextOpen) {
    loadTraceRuns().catch((error) => {
      setStatus("Error");
      console.error(error);
    });
  }
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

function renderMarkdown(value) {
  const lines = value.split(/\r?\n/);
  const html = [];
  let inList = false;
  let inCode = false;
  const codeLines = [];

  for (const line of lines) {
    if (line.startsWith("```")) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines.length = 0;
        inCode = false;
      } else {
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    const listItem = line.match(/^\s*[-*]\s+(.+)$/);
    if (!listItem && inList) {
      html.push("</ul>");
      inList = false;
    }
    if (heading) {
      const level = heading[1].length + 1;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
    } else if (listItem) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${renderInlineMarkdown(listItem[1])}</li>`);
    } else if (line.trim()) {
      html.push(`<p>${renderInlineMarkdown(line)}</p>`);
    }
  }

  if (inCode) {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  if (inList) {
    html.push("</ul>");
  }
  return html.join("");
}

function messageText(message) {
  return message.content || "";
}

function renderSessions() {
  elements.sessions.innerHTML = "";
  for (const session of state.sessions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = session.id === state.activeSessionId ? "session active" : "session";
    button.textContent = session.title;
    button.addEventListener("click", () => {
      openSession(session.id);
      if (isMobileLayout()) {
        toggleSidebar(false);
      }
    });
    elements.sessions.append(button);
  }
}

function renderMessages() {
  elements.messages.innerHTML = "";
  if (!state.messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = "<h2>Start a conversation</h2><p>Your agent responses will stream here and stay in this session.</p>";
    elements.messages.append(empty);
    return;
  }
  for (const message of state.messages) {
    elements.messages.append(createMessageElement(message));
  }
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function createMessageElement(message) {
  const article = document.createElement("article");
  article.className = `message ${message.role}`;
  const body = document.createElement("div");
  body.className = "message-body";
  body.innerHTML = renderMarkdown(messageText(message));
  article.append(body);
  if (message.traceRunId) {
    const traceButton = document.createElement("button");
    traceButton.type = "button";
    traceButton.className = "trace-link";
    traceButton.textContent = "Open trace";
    traceButton.addEventListener("click", () => {
      toggleTraceMode(true);
      openTraceRun(message.traceRunId).catch((error) => {
        setStatus("Error");
        console.error(error);
      });
    });
    article.append(traceButton);
  }
  return article;
}

function formatDateTime(value) {
  if (!value) {
    return "Not recorded";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString([], { dateStyle: "short", timeStyle: "medium" });
}

function formatMetric(value, suffix = "") {
  if (value === null || value === undefined || value === "") {
    return "n/a";
  }
  return `${value}${suffix}`;
}

function prettyJson(value) {
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function latestUserMessage(messages) {
  if (!Array.isArray(messages)) {
    return "";
  }
  const userMessages = messages.filter((message) => message && message.role === "user");
  return userMessages.at(-1)?.content || "";
}

function renderTraceRuns() {
  elements.traceRuns.innerHTML = "";
  if (!state.traceRuns.length) {
    const empty = document.createElement("div");
    empty.className = "trace-list-empty";
    empty.textContent = "No traced runs yet.";
    elements.traceRuns.append(empty);
    return;
  }
  for (const run of state.traceRuns) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = run.id === state.activeTraceId ? "trace-run active" : "trace-run";
    button.innerHTML = `
      <span class="trace-run-topline">
        <strong>${escapeHtml(run.graph_name || run.agent_name)}</strong>
        <em class="trace-status ${escapeHtml(run.status)}">${escapeHtml(run.status)}</em>
      </span>
      <span class="trace-run-preview">${escapeHtml(run.input_preview || "No input recorded")}</span>
      <span class="trace-run-meta">
        <span>${escapeHtml(formatDateTime(run.started_at))}</span>
        <span>${run.turn_count} turns</span>
        <span>${run.tool_call_count} tools</span>
      </span>
    `;
    button.addEventListener("click", () => {
      openTraceRun(run.id).catch((error) => {
        setStatus("Error");
        console.error(error);
      });
    });
    elements.traceRuns.append(button);
  }
}

function renderTraceDetail() {
  const trace = state.activeTrace;
  if (!trace) {
    elements.traceDetail.innerHTML = `
      <div class="trace-empty">
        <h2>Select a trace run</h2>
        <p>Open a run to inspect turns, graph nodes, model requests, tool calls, and failures.</p>
      </div>
    `;
    return;
  }
  const run = trace.run;
  const title = run.graph_name || run.agent_name;
  const failures = trace.failures || [];
  const inputPreview = latestUserMessage(trace.steps?.[0]?.input_messages) || run.root_input || "";
  elements.traceDetail.innerHTML = `
    <div class="trace-detail-header">
      <div>
        <span class="trace-kicker">${escapeHtml(run.graph_name ? "Graph trace" : "Agent trace")}</span>
        <h2>${escapeHtml(title)}</h2>
        <p>${escapeHtml(run.id)}</p>
      </div>
      <div class="trace-detail-actions">
        <button type="button" data-copy="${escapeHtml(run.id)}">Copy run ID</button>
        <button type="button" data-copy="${escapeHtml(trace.report || "")}">Copy report</button>
      </div>
    </div>
    <div class="trace-metrics">
      <div><span>Status</span><strong class="trace-status ${escapeHtml(run.status)}">${escapeHtml(run.status)}</strong></div>
      <div><span>Agent</span><strong>${escapeHtml(run.agent_name)}</strong></div>
      <div><span>Started</span><strong>${escapeHtml(formatDateTime(run.started_at))}</strong></div>
      <div><span>Latency</span><strong>${escapeHtml(formatMetric(run.total_latency_ms, "ms"))}</strong></div>
      <div><span>Prompt tokens</span><strong>${escapeHtml(formatMetric(run.total_prompt_tokens))}</strong></div>
      <div><span>Completion</span><strong>${escapeHtml(formatMetric(run.total_completion_tokens))}</strong></div>
    </div>
    ${failures.length ? renderTraceFailures(failures) : ""}
    <section class="trace-io">
      <div>
        <span>Input</span>
        <p>${escapeHtml(inputPreview)}</p>
      </div>
      <div>
        <span>Final output</span>
        <p>${escapeHtml(run.final_output || "")}</p>
      </div>
    </section>
    <section class="trace-timeline">
      ${(trace.steps || []).map((step) => renderTraceStep(step)).join("")}
    </section>
  `;
}

function renderTraceFailures(failures) {
  return `
    <section class="trace-failures" aria-label="Detected trace failures">
      <h3>Failures</h3>
      ${failures
        .map((failure) => `<p><strong>${escapeHtml(failure.kind || "failure")}</strong> ${escapeHtml(failure.message || "")}</p>`)
        .join("")}
    </section>
  `;
}

function renderTraceStep(step) {
  return `
    <article class="trace-step ${escapeHtml(step.status)}">
      <div class="trace-step-rail">
        <span>${String(step.turn_index).padStart(2, "0")}</span>
      </div>
      <div class="trace-step-body">
        <header class="trace-step-header">
          <div>
            <span>${escapeHtml(step.node_name || "agent")}</span>
            <h3>Turn ${step.turn_index}</h3>
          </div>
          <div class="trace-step-meta">
            <strong class="trace-status ${escapeHtml(step.status)}">${escapeHtml(step.status)}</strong>
            <span>${escapeHtml(formatMetric(step.latency_ms, "ms"))}</span>
          </div>
        </header>
        <div class="trace-step-summary">
          <div><span>User input</span><p>${escapeHtml(latestUserMessage(step.input_messages) || "No user message")}</p></div>
          <div><span>Final output</span><p>${escapeHtml(step.final_output || "Intermediate turn")}</p></div>
        </div>
        ${step.model_calls.map((call) => renderModelCall(call)).join("")}
        ${step.tool_calls.map((call) => renderToolCall(call)).join("")}
      </div>
    </article>
  `;
}

function renderModelCall(call) {
  return `
    <details class="trace-call model-call" open>
      <summary>
        <span>Model call</span>
        <strong>${escapeHtml(call.provider)} / ${escapeHtml(call.model)}</strong>
        <em>${escapeHtml(call.status)}</em>
      </summary>
      <div class="trace-call-grid">
        <div><span>API shape</span><strong>${escapeHtml(call.api_shape)}</strong></div>
        <div><span>Endpoint</span><strong>${escapeHtml(call.endpoint || "n/a")}</strong></div>
        <div><span>Latency</span><strong>${escapeHtml(formatMetric(call.latency_ms, "ms"))}</strong></div>
        <div><span>Finish</span><strong>${escapeHtml(call.response?.finish_reason || "n/a")}</strong></div>
      </div>
      ${renderJsonDetails("Request JSON", call.request)}
      ${renderJsonDetails("Response JSON", call.response)}
      ${renderJsonDetails("Usage JSON", call.usage)}
    </details>
  `;
}

function renderToolCall(call) {
  return `
    <details class="trace-call tool-call" open>
      <summary>
        <span>Tool call</span>
        <strong>${escapeHtml(call.tool_name)}</strong>
        <em>${escapeHtml(call.status)}</em>
      </summary>
      <div class="trace-call-grid">
        <div><span>Latency</span><strong>${escapeHtml(formatMetric(call.latency_ms, "ms"))}</strong></div>
        <div><span>Started</span><strong>${escapeHtml(formatDateTime(call.started_at))}</strong></div>
      </div>
      ${renderJsonDetails("Arguments", call.arguments)}
      ${renderJsonDetails("Result", call.result)}
      ${call.error ? renderJsonDetails("Error", call.error) : ""}
    </details>
  `;
}

function renderJsonDetails(label, value) {
  const text = prettyJson(value);
  return `
    <details class="json-pane">
      <summary>
        <span>${escapeHtml(label)}</span>
        <button type="button" data-copy="${escapeHtml(text)}">Copy</button>
      </summary>
      <pre><code>${escapeHtml(text)}</code></pre>
    </details>
  `;
}

function nodeTone(kind) {
  return {
    input: "Input",
    agent: "Agent",
    tool: "Tool",
    prompt: "Prompt",
    eval: "Eval",
    output: "Output",
  }[kind] || "Node";
}

function renderFlow() {
  if (!state.flow) {
    return;
  }
  elements.flowCanvas.innerHTML = "";
  const heading = document.createElement("div");
  heading.className = "flow-summary-heading";
  heading.innerHTML = `
    <span>Generated flow</span>
    <strong>${escapeHtml(state.flow.name)}</strong>
    <small>${escapeHtml(state.flow.description || "Ready to turn into a ClearAgent module.")}</small>
  `;
  elements.flowCanvas.append(heading);

  state.flow.nodes.forEach((node, index) => {
    const step = document.createElement("article");
    step.className = `flow-step ${node.kind}`;
    const details = Object.entries(node.config || {})
      .slice(0, 3)
      .map(([key, value]) => {
        const readable = Array.isArray(value) ? value.join(", ") : String(value);
        return `<li><span>${escapeHtml(key.replaceAll("_", " "))}</span><strong>${escapeHtml(readable)}</strong></li>`;
      })
      .join("");
    step.innerHTML = `
      <div class="flow-step-index">${String(index + 1).padStart(2, "0")}</div>
      <div class="flow-step-body">
        <span class="flow-step-kind">${nodeTone(node.kind)}</span>
        <h3>${escapeHtml(node.label)}</h3>
        <ul>${details || `<li><span>role</span><strong>${escapeHtml(state.flow.name)}</strong></li>`}</ul>
      </div>
    `;
    elements.flowCanvas.append(step);
  });
  elements.builderNodeCount.textContent = `${state.flow.nodes.length} nodes`;
}

function syncEditableFlowFields() {
  if (!state.flow) {
    return;
  }
  const agentNode = state.flow.nodes.find((node) => node.kind === "agent");
  elements.flowNameInput.value = state.flow.name || "";
  elements.flowPromptInput.value = agentNode?.config?.system_prompt || "";
}

function applyFlowEdits() {
  if (!state.flow) {
    return;
  }
  state.flow.name = elements.flowNameInput.value.trim() || state.flow.name;
  const agentNode = state.flow.nodes.find((node) => node.kind === "agent");
  if (agentNode) {
    agentNode.config = {
      ...(agentNode.config || {}),
      system_prompt: elements.flowPromptInput.value.trim(),
    };
  }
  elements.builderCode.textContent = flowToPythonSketch();
  renderFlow();
}

function slug(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function flowToPythonSketch() {
  if (!state.flow) {
    return state.builderPython || "";
  }
  const agentNode = state.flow.nodes.find((node) => node.kind === "agent");
  const toolNodes = state.flow.nodes.filter((node) => node.kind === "tool");
  const model = agentNode?.config?.model || "openrouter:openai/gpt-4.1-mini";
  const prompt = agentNode?.config?.system_prompt || "You are a helpful ClearAgent agent.";
  const lines = ["from clearagent import create_agent, tool", ""];
  const toolNames = [];
  for (const node of toolNodes) {
    const functionName = node.config?.function || slug(node.label) || "lookup";
    toolNames.push(functionName);
    lines.push("@tool");
    lines.push(`def ${functionName}(query: str) -> dict:`);
    lines.push(`    """${node.config?.description || node.label}"""`);
    lines.push('    return {"query": query, "status": "replace with your implementation"}');
    lines.push("");
  }
  lines.push("agent = create_agent(");
  lines.push(`    name="${slug(state.flow.name) || "builder_agent"}",`);
  lines.push(`    model="${model}",`);
  lines.push(`    system_prompt=${JSON.stringify(prompt)},`);
  lines.push(`    tools=[${toolNames.join(", ")}],`);
  lines.push(")");
  return lines.join("\n");
}

async function copyPythonFlow() {
  const code = elements.builderCode.textContent.trim();
  if (!code) {
    elements.copyPythonStatus.textContent = "Build a flow first.";
    return;
  }
  try {
    await navigator.clipboard.writeText(code);
    elements.copyPythonStatus.textContent = "Copied Python flow.";
  } catch {
    elements.copyPythonStatus.textContent = "Select the Python flow and copy it.";
  }
}

function appendBuilderLog(message) {
  const item = document.createElement("div");
  item.className = "builder-log-item";
  item.textContent = message;
  elements.builderLog.prepend(item);
}

async function loadBuilderFlow() {
  const response = await api("/api/builder/flow");
  state.flow = await response.json();
  state.builderPython = "";
  elements.builderCode.textContent = "";
  appendBuilderLog("Loaded the current agent as an editable flow.");
  syncEditableFlowFields();
  renderFlow();
}

async function askBuilderAgent() {
  const instruction = elements.builderPrompt.value.trim();
  if (!instruction) {
    return;
  }
  elements.builderPrompt.value = "";
  const response = await api("/api/builder/plan", {
    method: "POST",
    body: JSON.stringify({ instruction, flow: state.flow }),
  });
  const payload = await response.json();
  state.flow = payload.flow;
  state.builderPython = payload.python;
  elements.builderCode.textContent = payload.python;
  syncEditableFlowFields();
  appendBuilderLog(payload.message);
  renderFlow();
}

async function loadTraceRuns() {
  const response = await api("/api/traces");
  state.traceRuns = await response.json();
  renderTraceRuns();
  if (!state.activeTraceId && state.traceRuns.length) {
    await openTraceRun(state.traceRuns[0].id);
  } else if (state.activeTraceId) {
    renderTraceRuns();
  }
}

async function openTraceRun(runId) {
  state.activeTraceId = runId;
  renderTraceRuns();
  const response = await api(`/api/triage/runs/${encodeURIComponent(runId)}`);
  state.activeTrace = await response.json();
  renderTraceDetail();
}

async function copyText(value) {
  if (!value) {
    return;
  }
  await navigator.clipboard.writeText(value);
  setStatus("Copied");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response;
}

function parseSseFrames(buffer) {
  const frames = [];
  let remaining = buffer;
  let boundary = remaining.indexOf("\n\n");
  while (boundary !== -1) {
    const rawFrame = remaining.slice(0, boundary);
    remaining = remaining.slice(boundary + 2);
    const lines = rawFrame.split("\n");
    const eventLine = lines.find((line) => line.startsWith("event:"));
    const dataLines = lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart());
    if (dataLines.length) {
      frames.push({
        event: eventLine ? eventLine.slice(6).trimStart() : "message",
        data: dataLines.join("\n"),
      });
    }
    boundary = remaining.indexOf("\n\n");
  }
  return { frames, remaining };
}

async function loadHealth() {
  const response = await api("/api/health");
  const health = await response.json();
  return health;
}

async function loadAgents() {
  const response = await api("/api/agents");
  state.agents = await response.json();
  elements.agentSelect.innerHTML = "";
  for (const agent of state.agents) {
    const option = document.createElement("option");
    option.value = agent.name;
    option.textContent = agent.name;
    elements.agentSelect.append(option);
  }
}

function updateTemperatureLabel() {
  elements.temperatureValue.value = Number(elements.temperatureInput.value).toFixed(1);
}

async function loadModels(provider, selectedModel) {
  elements.modelSelect.disabled = true;
  const response = await api(`/api/models?provider=${encodeURIComponent(provider)}`);
  const models = await response.json();
  elements.modelSelect.innerHTML = "";
  const hasSelectedModel = models.some((model) => model.id === selectedModel);
  if (selectedModel && !hasSelectedModel) {
    const option = document.createElement("option");
    option.value = selectedModel;
    option.textContent = selectedModel;
    elements.modelSelect.append(option);
  }
  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.name || model.id;
    elements.modelSelect.append(option);
  }
  if (selectedModel) {
    elements.modelSelect.value = selectedModel;
  }
  elements.modelSelect.disabled = false;
}

async function loadSettings() {
  const response = await api("/api/settings");
  state.settings = await response.json();
  elements.providerSelect.value = state.settings.provider;
  elements.temperatureInput.value = state.settings.temperature;
  elements.thinkingSelect.value = state.settings.thinking;
  updateTemperatureLabel();
  await loadModels(state.settings.provider, state.settings.model);
}

async function saveSettings() {
  const settings = {
    provider: elements.providerSelect.value,
    model: elements.modelSelect.value,
    temperature: Number(elements.temperatureInput.value),
    thinking: elements.thinkingSelect.value,
  };
  const response = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
  state.settings = await response.json();
  toggleSettings(false);
  setStatus("Settings saved");
}

async function loadSessions() {
  const response = await api("/api/sessions");
  state.sessions = await response.json();
  renderSessions();
  return state.sessions;
}

async function createSession() {
  const response = await api("/api/sessions", { method: "POST", body: "{}" });
  const session = await response.json();
  await loadSessions();
  await openSession(session.id);
}

async function openSession(sessionId) {
  state.activeSessionId = sessionId;
  renderSessions();
  const response = await api(`/api/sessions/${sessionId}/messages`);
  state.messages = await response.json();
  renderMessages();
}

async function sendMessage(content) {
  if (!state.activeSessionId) {
    await createSession();
  }
  const userMessage = {
    id: `local-${Date.now()}`,
    role: "user",
    content,
  };
  const assistantMessage = {
    id: `stream-${Date.now()}`,
    role: "assistant",
    content: "",
  };
  state.messages.push(userMessage, assistantMessage);
  renderMessages();

  state.isStreaming = true;
  elements.send.disabled = true;
  setStatus("Streaming");

  const response = await api(`/api/sessions/${state.activeSessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let sseBuffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    sseBuffer += decoder.decode(value, { stream: true });
    const parsed = parseSseFrames(sseBuffer);
    sseBuffer = parsed.remaining;
    for (const frame of parsed.frames) {
      if (frame.data === "[DONE]") {
        continue;
      }
      if (frame.event === "error") {
        const payload = JSON.parse(frame.data);
        throw new Error(payload.message || "Request failed");
      }
      if (frame.event === "trace") {
        const payload = JSON.parse(frame.data);
        assistantMessage.traceRunId = payload.run_id;
        if (state.traceMode) {
          loadTraceRuns().catch((error) => console.error(error));
        }
        renderMessages();
        continue;
      }
      assistantMessage.content += JSON.parse(frame.data);
      renderMessages();
    }
  }

  state.isStreaming = false;
  elements.send.disabled = false;
  setStatus("Ready");
  await loadSessions();
}

async function submitComposer() {
  const content = elements.prompt.value.trim();
  if (!content || state.isStreaming) {
    return;
  }
  elements.prompt.value = "";
  elements.prompt.style.height = "auto";
  try {
    await sendMessage(content);
  } catch (error) {
    setStatus("Error");
    state.messages.push({
      id: `error-${Date.now()}`,
      role: "assistant",
      content: `Request failed: ${error.message}`,
    });
    state.isStreaming = false;
    elements.send.disabled = false;
    renderMessages();
  }
}

elements.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitComposer();
});

elements.prompt.addEventListener("keydown", async (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    await submitComposer();
  }
});

elements.prompt.addEventListener("input", () => {
  elements.prompt.style.height = "auto";
  elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 180)}px`;
});

elements.newChat.addEventListener("click", () => {
  createSession().catch((error) => {
    setStatus("Error");
    console.error(error);
  });
});

elements.sidebarToggle.addEventListener("click", () => toggleSidebar());

elements.sidebarOverlay.addEventListener("click", () => toggleSidebar(false));

elements.settingsToggle.addEventListener("click", () => toggleSettings());

elements.settingsClose.addEventListener("click", () => toggleSettings(false));

elements.builderToggle.addEventListener("click", () => toggleBuilderMode());

elements.tracesToggle.addEventListener("click", () => toggleTraceMode());

elements.traceRefresh.addEventListener("click", () => {
  loadTraceRuns().catch((error) => {
    setStatus("Error");
    console.error(error);
  });
});

elements.traceDetail.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement) || !target.dataset.copy) {
    return;
  }
  event.preventDefault();
  copyText(target.dataset.copy).catch((error) => {
    setStatus("Copy failed");
    console.error(error);
  });
});

elements.copyPython.addEventListener("click", () => {
  copyPythonFlow().catch((error) => {
    elements.copyPythonStatus.textContent = "Copy failed.";
    console.error(error);
  });
});

elements.builderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Building flow");
  try {
    await askBuilderAgent();
    setStatus("Ready");
  } catch (error) {
    setStatus("Error");
    appendBuilderLog(`Builder failed: ${error.message}`);
  }
});

elements.flowNameInput.addEventListener("input", applyFlowEdits);
elements.flowPromptInput.addEventListener("input", applyFlowEdits);

elements.providerSelect.addEventListener("change", () => {
  loadModels(elements.providerSelect.value).catch((error) => {
    setStatus("Error");
    console.error(error);
  });
});

elements.temperatureInput.addEventListener("input", updateTemperatureLabel);

elements.settingsPanel.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await saveSettings();
  } catch (error) {
    setStatus("Error");
    console.error(error);
  }
});

window.addEventListener("resize", syncSidebarState);

async function boot() {
  setStatus("Loading");
  await loadHealth();
  await loadAgents();
  await loadSettings();
  const sessions = await loadSessions();
  if (sessions.length) {
    await openSession(sessions[0].id);
  } else {
    await createSession();
  }
  setStatus("Ready");
  syncSidebarState();
}

boot().catch((error) => {
  setStatus("Error");
  console.error(error);
});
