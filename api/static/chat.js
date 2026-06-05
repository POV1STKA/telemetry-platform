"use strict";

document.addEventListener("DOMContentLoaded", () => {
  populateChatDevices();
});

function populateChatDevices() {
  const sel = document.getElementById("chat-device-sel");
  if (!sel) return;

  while (sel.options.length > 1) sel.remove(1);
  const devices = window._cachedDevices || [];
  devices.forEach((d) => {
    const opt = document.createElement("option");
    opt.value = d.device_uid;
    opt.textContent = `${d.name || d.device_uid} (${d.device_type})`;
    sel.appendChild(opt);
  });
}

async function sendChat() {
  const inputEl   = document.getElementById("chat-input");
  const sendBtn   = document.getElementById("chat-send-btn");
  const sendIcon  = document.getElementById("chat-send-icon");
  const messagesEl = document.getElementById("chat-messages");
  const deviceSel = document.getElementById("chat-device-sel");

  const message = (inputEl?.value || "").trim();
  if (!message) return;

  const deviceUid = deviceSel?.value || null;

  inputEl.value = "";
  inputEl.style.height = "auto";

  _appendChatMsg("user", message);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  sendBtn.disabled = true;
  sendIcon.textContent = "⏳";
  const thinkingEl = _appendChatMsg("assistant", "Аналізую...", true);

  try {
    let token = null;
    if (window._auth0Client) {
      try {
        token = await window._auth0Client.getTokenSilently();
      } catch (e) {
      }
    }

    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const resp = await fetch("/api/chat", {
      method:  "POST",
      headers,
      body: JSON.stringify({
        message,
        device_uid: deviceUid || null,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    const data = await resp.json();

    thinkingEl.remove();
    _appendChatMsg("assistant", data.reply);

    const badge = document.getElementById("chat-model-badge");
    if (badge && data.model) badge.textContent = data.model.split(":")[0];

  } catch (err) {
    thinkingEl.remove();
    _appendChatMsg(
      "assistant",
      `Помилка: ${err.message || "Сервер недоступний."}`,
      false,
      true
    );
  } finally {
    sendBtn.disabled = false;
    sendIcon.textContent = "↑";
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function chatKeydown(e) {
  if (e.key === "Enter" && e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
}

function _appendChatMsg(role, text, isThinking = false, isError = false) {
  const messagesEl = document.getElementById("chat-messages");
  if (!messagesEl) return null;

  const msgEl = document.createElement("div");
  msgEl.className = `chat-msg chat-msg--${role}`;
  if (isThinking) msgEl.classList.add("chat-msg--thinking");
  if (isError)    msgEl.classList.add("chat-msg--error");

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";

  if (isThinking) {
    bubble.innerHTML = `
      <span class="thinking-dot"></span>
      <span class="thinking-dot"></span>
      <span class="thinking-dot"></span>
    `;
  } else {
    bubble.innerHTML = _escapeHtml(text).replace(/\n/g, "<br>");
  }

  msgEl.appendChild(bubble);

  if (role === "assistant" && !isThinking) {
    const ts = document.createElement("div");
    ts.className = "chat-ts";
    ts.textContent = new Date().toLocaleTimeString("uk-UA", {
      hour: "2-digit",
      minute: "2-digit",
    });
    msgEl.appendChild(ts);
  }

  messagesEl.appendChild(msgEl);
  return msgEl;
}

function _escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

window.addEventListener("devices:loaded", () => {
  populateChatDevices();
});
