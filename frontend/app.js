/* ==========================================
   Agentic AI Workflow Assistant - Frontend Logic
   File: frontend/app.js

   Purpose:
   - UI only displays backend result
   - Left history is dynamic from user inputs
   - Tool cards are dynamic from backend response
   - Workflow nodes come from actual n8n workflow JSON
   - Normal answers do not show workflow completed
   ========================================== */


/* -----------------------------
   DOM references
----------------------------- */

const chatArea = document.getElementById("chatArea");
const userInput = document.getElementById("userInput");
const sendBtn = document.getElementById("sendBtn");
// ADDED:login and log out buttons, user chip and account summary for better auth state handling and display.
const loginBtn = document.getElementById("loginBtn");
const logoutBtn = document.getElementById("logoutBtn");
const userChip = document.getElementById("userChip");
const accountSummary = document.getElementById("accountSummary");
//const loginBtn = document.getElementById("loginBtn");`r`nconst logoutBtn = document.getElementById("logoutBtn");`r`nconst userChip = document.getElementById("userChip");`r`nconst accountSummary = document.getElementById("accountSummary");
const newChatBtn = document.getElementById("newChatBtn");
const searchChats = document.getElementById("searchChats");

const chatHistoryList = document.getElementById("chatHistoryList");

const toolStatusPanel = document.getElementById("toolStatusPanel");

const workflowSummary = document.getElementById("workflowSummary");
const workflowStatus = document.getElementById("workflowStatus");
const workflowNodes = document.getElementById("workflowNodes");
const workflowResult = document.getElementById("workflowResult");
const rawJsonBox = document.getElementById("rawJsonBox");
const settingsBtn = document.getElementById("settingsBtn");
const settingsModal = document.getElementById("settingsModal");
const closeSettingsBtn = document.getElementById("closeSettingsBtn");
const settingsLogoutBtn = document.getElementById("settingsLogoutBtn");
const settingsUserName = document.getElementById("settingsUserName");
const settingsUserEmail = document.getElementById("settingsUserEmail");
//const settingsUserId = document.getElementById("settingsUserId");

let currentUser = null;

/* -----------------------------
   Local storage
----------------------------- */

const CHATS_STORAGE_KEY = "agentic_ai_workflow_chats";
const CURRENT_CHAT_ID_KEY = "agentic_ai_workflow_current_chat_id";

let chats = loadFromStorage(CHATS_STORAGE_KEY, []);
let currentChatId = localStorage.getItem(CURRENT_CHAT_ID_KEY) || "";


function loadFromStorage(key, fallback) {
  try {
    const saved = localStorage.getItem(key);
    return saved ? JSON.parse(saved) : fallback;
  } catch (error) {
    return fallback;
  }
}


function saveToStorage(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn("Storage save failed:", error);
  }
}

function createChat(title = "New Chat") {
  const chat = {
    id: `chat_${Date.now()}_${Math.random().toString(16).slice(2)}`,
    title,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    messages: []
  };

  chats.unshift(chat);
  currentChatId = chat.id;

  saveChats();
  return chat;
}

function saveChats() {
  saveToStorage(CHATS_STORAGE_KEY, chats);
  localStorage.setItem(CURRENT_CHAT_ID_KEY, currentChatId || "");
}

function getCurrentChat() {
  let chat = chats.find((item) => item.id === currentChatId);

  if (!chat) {
    chat = createChat("New Chat");
  }

  return chat;
}

function updateCurrentChatTitleFromFirstMessage(message) {
  const chat = getCurrentChat();

  if (!chat.title || chat.title === "New Chat") {
    chat.title = truncateText(message, 34);
    chat.updatedAt = new Date().toISOString();
    saveChats();
  }
}

function renameChat(chatId) {
  const chat = chats.find((item) => item.id === chatId);

  if (!chat) {
    return;
  }

  const newTitle = prompt("Rename chat", chat.title);

  if (newTitle && newTitle.trim()) {
    chat.title = newTitle.trim();
    chat.updatedAt = new Date().toISOString();
    saveChats();
    renderSidebarHistory(searchChats?.value || "");
  }
}

function openChat(chatId) {
  currentChatId = chatId;
  saveChats();
  renderStoredChatMessages();
  renderSidebarHistory(searchChats?.value || "");
  resetWorkflowPanel();
}
/* -----------------------------
   Small helpers
----------------------------- */

function getTimeLabel(dateValue) {
  const date = dateValue ? new Date(dateValue) : new Date();

  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit"
  });
}


function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}
// ADDED:
// Safely set text only if the element exists.
// This prevents frontend crash:
// Cannot set properties of null (setting 'textContent')
function safeSetText(element, text) {
  if (element) {
    element.textContent = text;
  }
}

function scrollChatToBottom() {
  chatArea.scrollTop = chatArea.scrollHeight;
}


function truncateText(text, maxLength = 42) {
  const value = String(text || "").trim();

  if (value.length <= maxLength) {
    return value;
  }

  return value.slice(0, maxLength - 3) + "...";
}


function normalizeToolName(toolName) {
  const value = String(toolName || "").toLowerCase();

  if (value.includes("gmail")) return "gmail";
  if (value.includes("slack")) return "slack";
  if (value.includes("openai")) return "openai";
  if (value.includes("llm")) return "openai";
  if (value.includes("webhook")) return "webhook";
  if (value.includes("chat")) return "chat";

  return value || "tool";
}


function getToolLabel(toolName) {
  const tool = normalizeToolName(toolName);

  const labels = {
    gmail: "Gmail",
    slack: "Slack",
    openai: "AI",
    webhook: "Webhook",
    chat: "Chat",
    web: "Web",
    file: "File"
  };

  return labels[tool] || tool.charAt(0).toUpperCase() + tool.slice(1);
}


function getToolIconText(toolName) {
  const tool = normalizeToolName(toolName);

  const icons = {
    gmail: "G",
    slack: "S",
    openai: "AI",
    webhook: "WH",
    chat: "CH",
    web: "W",
    file: "F"
  };

  return icons[tool] ||  "-";
}


function getToolIconClass(toolName) {
  const tool = normalizeToolName(toolName);

  if (tool === "gmail") return "gmail";
  if (tool === "slack") return "slack";

  return "";
}


/* -----------------------------
   Chat message rendering
----------------------------- */

function addMessage(role, text, options = {}) {
  const save = options.save !== false;
  const time = options.time || new Date().toISOString();

  const row = document.createElement("div");
  row.className = `message-row ${role}`;

  const safeText = escapeHtml(text).replace(/\n/g, "<br>");

  if (role === "user") {
    row.innerHTML = `
      <div class="message-card user-card">
        <div class="message-meta">
          <strong>You</strong>
          <span>${getTimeLabel(time)}</span>
        </div>
        <p>${safeText}</p>
      </div>
    `;
  } else {
    row.innerHTML = `
      <div class="avatar">AI</div>
      <div class="message-card bot-card">
        <div class="message-meta">
          <strong>AI Assistant</strong>
          <span>${getTimeLabel(time)}</span>
        </div>
        <p>${safeText}</p>
      </div>
    `;
  }

  chatArea.appendChild(row);
  scrollChatToBottom();

  if (save) {
  const chat = getCurrentChat();

  chat.messages.push({
    role,
    text,
    time
  });

  chat.updatedAt = new Date().toISOString();

  saveChats();
  renderSidebarHistory(searchChats?.value || "");
}
}


function renderStoredChatMessages() {
  const chat = getCurrentChat();

  chatArea.innerHTML = "";

  if (!chat.messages.length) {
    chatArea.innerHTML = `
      <div class="message-row bot">
        <div class="avatar">AI</div>
        <div class="message-card bot-card">
          <div class="message-meta">
            <strong>AI Assistant</strong>
            <span>Ready</span>
          </div>
          <p>Tell me what workflow you want to create.</p>
        </div>
      </div>
    `;
    return;
  }

  chat.messages.forEach((message) => {
    addMessage(message.role, message.text, {
      save: false,
      time: message.time
    });
  });
}
/* -----------------------------
   Dynamic left sidebar history
----------------------------- */

function addSidebarHistoryItem(userText) {
  const cleanText = String(userText || "").trim();

  if (!cleanText) {
    return;
  }

  sidebarHistory.unshift({
    title: cleanText,
    time: new Date().toISOString()
  });

  // Keep latest 30 items only
  sidebarHistory = sidebarHistory.slice(0, 30);

  saveToStorage(HISTORY_STORAGE_KEY, sidebarHistory);
  renderSidebarHistory();
}


function renderSidebarHistory(filterText = "") {
  if (!chatHistoryList) {
    return;
  }

  const filter = String(filterText || "").toLowerCase();

  const filtered = chats.filter((chat) => {
    return chat.title.toLowerCase().includes(filter);
  });

  chatHistoryList.innerHTML = "";

  if (!filtered.length) {
    chatHistoryList.innerHTML = `
      <div class="chat-history-item">
        <span>No chats yet</span>
        <small></small>
      </div>
    `;
    return;
  }

  filtered.forEach((chat) => {
    const itemEl = document.createElement("div");
    itemEl.className = `chat-history-item ${chat.id === currentChatId ? "active" : ""}`;

    itemEl.innerHTML = `
      <span>${escapeHtml(truncateText(chat.title, 34))}</span>
      <div class="chat-actions">
        <small>${getTimeLabel(chat.updatedAt)}</small>
        <button class="rename-chat-btn" title="Rename">Edit</button>
      </div>
    `;

    itemEl.addEventListener("click", () => {
      openChat(chat.id);
    });

    itemEl.addEventListener("dblclick", () => {
      renameChat(chat.id);
    });

    const renameBtn = itemEl.querySelector(".rename-chat-btn");
    if (renameBtn) {
      renameBtn.addEventListener("click", (event) => {
        event.stopPropagation();
        renameChat(chat.id);
      });
    }

    chatHistoryList.appendChild(itemEl);
  });
}
/* -----------------------------
   Workflow state
----------------------------- */

function setWorkflowState(status, summaryText) {
  if (!workflowStatus || !workflowSummary) {
    return;
  }

  workflowStatus.className = "workflow-status";

  if (status === "completed") {
    workflowStatus.classList.add("completed");
    safeSetText(workflowStatus, "Completed");
  } else if (status === "failed") {
    workflowStatus.classList.add("failed");
    safeSetText(workflowStatus, "Failed");
  } else if (status === "running") {
    workflowStatus.classList.add("running");
    safeSetText(workflowStatus, "Running");
  } else {
    workflowStatus.classList.add("idle");
    safeSetText(workflowStatus, "Idle");
  }

  safeSetText(workflowSummary, summaryText || "No workflow executed yet.");
}

function resetWorkflowPanel() {
  setWorkflowState("idle", "No workflow executed yet.");

  workflowNodes.innerHTML = `
    <div class="empty-workflow">
      Actual n8n workflow will appear here after execution.
    </div>
  `;

  workflowResult.innerHTML = `<p>No result yet.</p>`;
  safeSetText(rawJsonBox, "{}");
}


/* -----------------------------
   Read backend response safely
----------------------------- */

function getPipelineResult(data) {
  return data?.debug?.pipeline_result || data?.pipeline_result || data || {};
}


function getPlannerOutput(data) {
  const pipeline = getPipelineResult(data);

  return (
    pipeline?.planner_output ||
    data?.planner_output ||
    {}
  );
}


function getResolverOutput(data) {
  const pipeline = getPipelineResult(data);

  return (
    pipeline?.resolver_output ||
    data?.resolver_output ||
    {}
  );
}


function getWorkflowJson(data) {
  const pipeline = getPipelineResult(data);

  return (
    pipeline?.builder_output?.workflow_json ||
    pipeline?.workflow_json ||
    data?.workflow_json ||
    null
  );
}


function getWorkflowSteps(data) {
  const pipeline = getPipelineResult(data);

  return (
    pipeline?.workflow_steps ||
    data?.workflow_steps ||
    []
  );
}


function getWebhookResponse(data) {
  return (
    data?.webhook_response ||
    data?.debug?.webhook_result?.webhook_response ||
    data?.webhook_result?.webhook_response ||
    null
  );
}

function renderConnectActions(data) {
  const actions =
    data?.connect_actions ||
    data?.resolver_output?.connect_actions ||
    data?.debug?.pipeline_result?.resolver_output?.connect_actions ||
    data?.pipeline_result?.resolver_output?.connect_actions ||
    [];

  if (!Array.isArray(actions) || !actions.length) {
    return "";
  }

  return `
    <div class="connect-actions" style="margin-top: 12px;">
      ${actions.map((action) => {
        const label = escapeHtml(action.label || "Connect");
        const url = escapeHtml(action.url || "#");

        return `
          <a
            class="tool-action-btn"
            href="${url}"
            target="_self"
            rel="noopener noreferrer"
            style="display:inline-block;margin-top:8px;padding:8px 14px;border-radius:8px;background:#6c5ce7;color:white;text-decoration:none;font-weight:600;"
          >
            ${label}
          </a>
        `;
      }).join("")}
    </div>
  `;
}
function extractFinalWorkflowText(data) {
  const webhookResponse = getWebhookResponse(data);

  // ADDED:
  // Format raw Gmail read response.
  // This handles workflows like:
  // "read top 2 email from vijji.m18@gmail.com and display here"
  // when n8n returns Gmail JSON directly instead of LLM text.
  if (webhookResponse && typeof webhookResponse === "object") {
    const from = webhookResponse.From || webhookResponse.from || "";
    const to = webhookResponse.To || webhookResponse.to || "";
    const subject = webhookResponse.Subject || webhookResponse.subject || "";
    const snippet = webhookResponse.snippet || "";

    if (from || to || subject || snippet) {
      const lines = ["Email result:"];

      if (from) {
        lines.push(`From: ${from}`);
      }

      if (to) {
        lines.push(`To: ${to}`);
      }

      if (subject) {
        lines.push(`Subject: ${subject}`);
      }

      if (snippet) {
        lines.push(`Snippet: ${snippet}`);
      }

      return lines.join("\n");
    }
  }

  const candidates = [
    webhookResponse?.output?.[0]?.content?.[0]?.text,
    webhookResponse?.output?.[0]?.content?.find?.((item) => item?.type === "output_text")?.text,
    webhookResponse?.message?.text,
    webhookResponse?.text,
    webhookResponse?.result,
    webhookResponse?.response,
    data?.message
  ];

  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }

  return "";
}
/* -----------------------------
   Dynamic tool cards
----------------------------- */

function extractToolsFromResponse(data) {
  const planner = getPlannerOutput(data);
  const resolver = getResolverOutput(data);
  const workflowJson = getWorkflowJson(data);

  const tools = new Set();

  // Backend-declared required connections
  const plannerConnections = planner?.requires_connection || [];
  const resolverConnections = resolver?.required_connections || [];

  plannerConnections.forEach((tool) => tools.add(normalizeToolName(tool)));
  resolverConnections.forEach((tool) => tools.add(normalizeToolName(tool)));

  // Actual n8n nodes
  if (workflowJson && Array.isArray(workflowJson.nodes)) {
    workflowJson.nodes.forEach((node) => {
      const tool = detectToolFromNode(node);

      if (tool && !["webhook", "chat", "unknown"].includes(tool)) {
        tools.add(tool);
      }
    });
  }

  // Remove empty / none
  tools.delete("");
  tools.delete("none");
  tools.delete("tool");

  return Array.from(tools);
}

// ADDED:
// Build useful action links for connected tools.
// Slack opens the exact posted channel when backend returns team/channel.
// Gmail opens inbox.
// If connection is missing, button opens backend OAuth route.
function getToolAction(tool, data, isMissing) {
  const normalizedTool = normalizeToolName(tool);
  const webhookResponse = getWebhookResponse(data);

  if (normalizedTool === "slack") {
    if (isMissing) {
      return {
        label: "Connect Slack",
        url: "/connect/slack"
      };
    }

    const teamId =
      webhookResponse?.message?.team ||
      webhookResponse?.team ||
      "";

    const channelId =
      webhookResponse?.channel ||
      webhookResponse?.message?.channel ||
      "";

    if (teamId && channelId) {
      return {
        label: "Open Channel",
        url: `https://app.slack.com/client/${teamId}/${channelId}`
      };
    }

    return {
      label: "Open Slack",
      url: "https://app.slack.com/client"
    };
  }

  if (normalizedTool === "gmail") {
    if (isMissing) {
      return {
        label: "Connect Gmail",
        url: "/connect/gmail"
      };
    }

    return {
      label: "Open Gmail",
      url: "https://mail.google.com/mail/u/0/#inbox"
    };
  }

  return null;
}
function updateToolStatus(data) {
  const tools = extractToolsFromResponse(data);
  const resolver = getResolverOutput(data);

  const missingConnections = resolver?.missing_connections || [];

  if (!toolStatusPanel) {
    return;
  }

  toolStatusPanel.innerHTML = "";

  if (!tools.length) {
    toolStatusPanel.classList.add("hidden");
    return;
  }

  toolStatusPanel.classList.remove("hidden");

  tools.forEach((tool) => {
    const normalizedTool = normalizeToolName(tool);
    const isMissing = missingConnections.includes(normalizedTool);
    const action = getToolAction(normalizedTool, data, isMissing);

    const card = document.createElement("div");
    card.className = "tool-card";

    card.innerHTML = `
      <div class="tool-left">
        <span class="tool-icon ${getToolIconClass(normalizedTool)}">${getToolIconText(normalizedTool)}</span>
        <div>
          <strong>${escapeHtml(getToolLabel(normalizedTool))}</strong>
          <p>${isMissing ? "Connection needed" : "Ready"}</p>
        </div>
      </div>

      <div class="tool-actions">
        <span class="status-pill ${isMissing ? "failed" : "connected"}">
          ${isMissing ? "Reconnect" : "Connected"}
        </span>

        ${
          action
            ? `<a class="tool-action-btn" href="${action.url}" target="_blank" rel="noopener noreferrer">${action.label}</a>`
            : ""
        }
      </div>
    `;

    toolStatusPanel.appendChild(card);
  });
}


/* -----------------------------
   Actual n8n workflow rendering
----------------------------- */

function detectToolFromNode(node) {
  const text = `${node?.name || ""} ${node?.type || ""}`.toLowerCase();

  if (text.includes("gmail")) return "gmail";
  if (text.includes("slack")) return "slack";
  if (text.includes("openai")) return "openai";
  if (text.includes("llm")) return "openai";
  if (text.includes("webhook")) return "webhook";
  if (text.includes("chat")) return "chat";

  return "unknown";
}


function detectSubtitleFromNode(node) {
  const name = String(node?.name || "").toLowerCase();
  const type = String(node?.type || "").toLowerCase();

  if (name.includes("webhook")) return "Trigger";
  if (name.includes("gmail") && name.includes("read")) return "Read emails";
  if (name.includes("gmail") && name.includes("send")) return "Send email";
  if (name.includes("slack") && name.includes("send")) return "Send message";
  if (name.includes("slack") && name.includes("read")) return "Read messages";
  if (name.includes("llm") || name.includes("transform") || type.includes("openai")) return "AI processing";

  return "n8n node";
}


function renderWorkflowNodesFromResponse(data) {
  workflowNodes.innerHTML = "";

  const workflowJson = getWorkflowJson(data);
  const workflowSteps = getWorkflowSteps(data);

  let nodes = [];

  // IMPORTANT:
  // Prefer actual n8n workflow JSON nodes.
  // This is the exact workflow created in n8n.
  if (workflowJson && Array.isArray(workflowJson.nodes)) {
    nodes = workflowJson.nodes.map((node, index) => ({
      step: index + 1,
      name: node.name || "n8n node",
      tool: detectToolFromNode(node),
      subtitle: detectSubtitleFromNode(node),
      status: "success"
    }));
  } else if (Array.isArray(workflowSteps) && workflowSteps.length) {
    // Fallback only if workflow JSON is not available
    nodes = workflowSteps.map((step, index) => ({
      step: index + 1,
      name: `${step.tool || "step"} ${step.action || ""}`.trim(),
      tool: step.tool || "unknown",
      subtitle: step.description || step.action || "",
      status: "success"
    }));
  }

  if (!nodes.length) {
    workflowNodes.innerHTML = `
      <div class="empty-workflow">
        No n8n workflow was created for this request.
      </div>
    `;
    return;
  }

  nodes.forEach((node) => {
    const nodeEl = document.createElement("div");
    nodeEl.className = "workflow-node";

    nodeEl.innerHTML = `
      <div class="node-number">${node.step}</div>
      <div class="node-title">${escapeHtml(node.name)}</div>
      <div class="node-subtitle">${escapeHtml(node.subtitle)}</div>
      <div class="node-status ${node.status}">
        ${node.status === "success" ? "Success" : "Pending"}
      </div>
    `;

    workflowNodes.appendChild(nodeEl);
  });
}


/* -----------------------------
   Workflow result display
----------------------------- */

function renderWorkflowResult(data) {
  const status = data?.status || "";
  const stage = data?.stage || "";
  const decision = data?.decision || getPlannerOutput(data)?.decision || "";

  // Connection required: show connect button, no n8n workflow
  if (data?.can_continue === false && renderConnectActions(data)) {
    setWorkflowState("idle", "No workflow executed.");

    workflowNodes.innerHTML = `
      <div class="empty-workflow">
        No n8n workflow was created for this request.
      </div>
    `;

    workflowResult.innerHTML = `
      <p>${escapeHtml(data?.message || "Please connect the required account.")}</p>
      ${renderConnectActions(data)}
    `;

    return;
  }

  // Direct answer: no workflow
  if (decision === "direct_answer" || data?.direct_answer) {
    setWorkflowState("idle", "No workflow executed for this request.");

    workflowNodes.innerHTML = `
      <div class="empty-workflow">
        No n8n workflow was created for this request.
      </div>
    `;

    workflowResult.innerHTML = `
      <p>${escapeHtml(data?.direct_answer || "Answered in chat.")}</p>
    `;

    return;
  }

  // Follow-up: no workflow yet
  if (decision === "follow_up" || data?.follow_up_question) {
    setWorkflowState("idle", "Waiting for more information.");

    workflowNodes.innerHTML = `
      <div class="empty-workflow">
        Workflow will be created after the missing detail is provided.
      </div>
    `;

    workflowResult.innerHTML = `
      <p>${escapeHtml(data?.follow_up_question || data?.message || "More information is needed.")}</p>
      ${renderConnectActions(data)}
    `;

    return;
  }

  // Workflow success
  if (status === "ok" && stage === "workflow_executed") {
    setWorkflowState("completed", "Workflow completed successfully.");
if (workflowResult) {
    workflowResult.innerHTML = "";
    workflowResult.style.display = "none";
  }

  return;
}

  // Failure cases
  if (
    status === "mapping_failed" ||
    status === "failed" ||
    data?.errors?.length ||
    data?.builder_preview_output?.errors?.length
  ) {
    setWorkflowState("failed", "Workflow failed.");

    const errors =
      data?.errors ||
      data?.builder_preview_output?.errors ||
      data?.webhook_result?.errors ||
      ["Something failed."];

    workflowResult.innerHTML = `
      <p><strong>Error</strong></p>
      <p>${escapeHtml(errors.join(" "))}</p>
    `;

    return;
  }

  // Safe fallback
  setWorkflowState("idle", "No workflow executed.");

  workflowResult.innerHTML = `
    <p>${escapeHtml(data?.message || "Request completed.")}</p>
    ${renderConnectActions(data)}
  `;
}

/* -----------------------------
   Backend call
----------------------------- */

async function sendMessageToBackend(message) {
  const currentChat = getCurrentChat();

  const history = currentChat.messages
    .slice(-10)
    .map((item) => ({
      role: item.role,
      text: item.text
    }));

  const response = await fetch("/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    credentials: "include",
    body: JSON.stringify({
      message,
      history
    })
  });

  if (!response.ok) {
    throw new Error(`Backend returned ${response.status}`);
  }

  return await response.json();
}

/* -----------------------------
   Bot response from backend
----------------------------- */

function buildBotResponse(data) {
  const planner = getPlannerOutput(data);

  if (data?.direct_answer) {
    return data.direct_answer;
  }

  if (data?.follow_up_question) {
    return data.follow_up_question;
  }

  if (planner?.decision === "direct_answer" && planner?.direct_answer) {
    return planner.direct_answer;
  }

  if (planner?.decision === "follow_up" && planner?.follow_up_question) {
    return planner.follow_up_question;
  }

  if (data?.stage === "workflow_executed" && data?.status === "ok") {
  const finalText = extractFinalWorkflowText(data);

  return finalText || "Done. Workflow executed successfully.";
}

  if (data?.status === "mapping_failed") {
  return "The workflow was understood, but one step could not be mapped.";
}

if (Array.isArray(data?.connect_actions) && data.connect_actions.length) {
  return data?.message || "Please connect the required account and try again.";
}

if (data?.message) {
  return data.message;
}

  return "Request completed.";
}


/* -----------------------------
   Main send handler
----------------------------- */

async function handleSend() {
  const message = userInput.value.trim();

  if (!message) {
    return;
  }

  userInput.value = "";

  updateCurrentChatTitleFromFirstMessage(message);
  addMessage("user", message);
// Clear old tool cards and old debug JSON before new request
if (toolStatusPanel) {
  toolStatusPanel.innerHTML = "";
  toolStatusPanel.classList.add("hidden");
}

safeSetText(rawJsonBox, "{}");
  setWorkflowState("running", "Processing request...");
  workflowNodes.innerHTML = `
    <div class="empty-workflow">
      Waiting for backend response...
    </div>
  `;
if (workflowResult) {
  workflowResult.style.display = "block";
  workflowResult.innerHTML = `<p>Waiting for result...</p>`;
}

  try {
    const data = await sendMessageToBackend(message);

   safeSetText(rawJsonBox, JSON.stringify(data, null, 2));
    const botText = buildBotResponse(data);
    addMessage("bot", botText);

    updateToolStatus(data);

    const decision = data?.decision || getPlannerOutput(data)?.decision || "";

    if (decision === "direct_answer" || data?.direct_answer) {
      renderWorkflowResult(data);
      return;
    }

    renderWorkflowNodesFromResponse(data);
    renderWorkflowResult(data);

  } catch (error) {
    setWorkflowState("failed", "Request failed.");

    workflowNodes.innerHTML = `
      <div class="empty-workflow">
        Workflow could not be completed.
      </div>
    `;

    workflowResult.innerHTML = `
      <p><strong>Error</strong></p>
      <p>${escapeHtml(error.message)}</p>
    `;

    addMessage("bot", `Something failed: ${error.message}`);
  }
}


/* -----------------------------
   Clear / new chat
----------------------------- */

function clearCurrentChat() {
  const chat = getCurrentChat();
  chat.messages = [];
  chat.updatedAt = new Date().toISOString();
  saveChats();

  renderStoredChatMessages();

  toolStatusPanel.innerHTML = "";
  toolStatusPanel.classList.add("hidden");

  resetWorkflowPanel();
}
async function logoutCurrentUser() {
  try {
    await fetch("/logout", {
      method: "POST",
      credentials: "include"
    });
  } catch (error) {
    console.warn("Logout failed:", error);
  }

  localStorage.removeItem(CHATS_STORAGE_KEY);
  localStorage.removeItem(CURRENT_CHAT_ID_KEY);

  window.location.href = "/";
}
function resetTransientUiState() {
  if (toolStatusPanel) {
    toolStatusPanel.innerHTML = "";
    toolStatusPanel.classList.add("hidden");
  }

  if (workflowNodes) {
    workflowNodes.innerHTML = `
      <div class="empty-workflow">
        Actual n8n workflow will appear here after execution.
      </div>
    `;
  }

  if (workflowResult) {
    workflowResult.style.display = "block";
    workflowResult.innerHTML = `<p>No result yet.</p>`;
  }

  safeSetText(rawJsonBox, "{}");

  setWorkflowState("idle", "No workflow executed yet.");
}
/* -----------------------------
   Events
----------------------------- */

sendBtn.addEventListener("click", handleSend);

userInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    handleSend();
  }
});
loginBtn?.addEventListener("click", () => {
  window.location.href = "/login/google";
});
// ADDED: logout handler to clear frontend state and redirect to backend logout route.

logoutBtn?.addEventListener("click", logoutCurrentUser);
//loginBtn?.addEventListener("click", () => {`r`n  window.location.href = "/login/google";`r`n});`r`n`r`nlogoutBtn?.addEventListener("click", logoutCurrentUser);
newChatBtn.addEventListener("click", async () => {
  createChat("New Chat");
  renderStoredChatMessages();
  renderSidebarHistory();

  resetTransientUiState();

  try {
    await fetch("/chat/reset", {
      method: "POST",
      credentials: "include"
    });
  } catch (error) {
    console.warn("Chat reset failed:", error);
  }
});
settingsBtn?.addEventListener("click", () => {
  settingsModal?.classList.remove("hidden");
});

closeSettingsBtn?.addEventListener("click", () => {
  settingsModal?.classList.add("hidden");
});

settingsModal?.addEventListener("click", (event) => {
  if (event.target === settingsModal) {
    settingsModal.classList.add("hidden");
  }
});

settingsLogoutBtn?.addEventListener("click", async () => {
  try {
    await fetch("/logout", {
      method: "POST",
      credentials: "include"
    });
  } catch (error) {
    console.warn("Logout failed:", error);
  }

  localStorage.removeItem(CHATS_STORAGE_KEY);
  localStorage.removeItem(CURRENT_CHAT_ID_KEY);

  window.location.href = "/";
});
if (searchChats) {
  searchChats.addEventListener("input", () => {
    renderSidebarHistory(searchChats.value);
  });
}


/* -----------------------------
   Startup
----------------------------- */

if (!chats.length) {
  createChat("New Chat");
}

renderStoredChatMessages();
renderSidebarHistory();

if (!toolStatusPanel.classList.contains("hidden")) {
  toolStatusPanel.classList.add("hidden");
}


  resetWorkflowPanel();

/* -----------------------------
   Startup auth gate
----------------------------- */
async function checkLoginAndOpenApp() {
  const loginScreen = document.getElementById("loginScreen");
  const appShell = document.getElementById("appShell");

  try {
    const response = await fetch("/me", {
      credentials: "include"
    });

    const data = await response.json();

    const isLoggedIn = Boolean(data.logged_in && data.authorized);

    if (isLoggedIn) {
      currentUser = data.user || null;

      safeSetText(settingsUserName, currentUser?.name || "-");
      safeSetText(settingsUserEmail, currentUser?.email || "-");
      //safeSetText(settingsUserId, currentUser?.user_id || "-");

      loginScreen?.classList.add("hidden");
      appShell?.classList.remove("hidden");
    } else {
      currentUser = null;

      safeSetText(settingsUserName, "-");
      safeSetText(settingsUserEmail, "-");
      //safeSetText(settingsUserId, "-");

      loginScreen?.classList.remove("hidden");
      appShell?.classList.add("hidden");
    }
  } catch (error) {
    currentUser = null;

    safeSetText(settingsUserName, "-");
    safeSetText(settingsUserEmail, "-");
    //safeSetText(settingsUserId, "-");

    loginScreen?.classList.remove("hidden");
    appShell?.classList.add("hidden");

    console.warn("Login check failed:", error);
  }
}
const testSettingsBtn = document.getElementById("settingsBtn");
const testSettingsModal = document.getElementById("settingsModal");

testSettingsBtn?.addEventListener("click", () => {
  testSettingsModal?.classList.remove("hidden");
});
document.addEventListener("DOMContentLoaded", () => {
  checkLoginAndOpenApp();
});
