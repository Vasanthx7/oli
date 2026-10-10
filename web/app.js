"use strict";

const $ = (sel) => document.querySelector(sel);

// --- icon system ---------------------------------------------------------
// One source of truth for glyphs. Lucide-style 24px stroke icons, monochrome
// via currentColor so CSS controls color/size. Emoji render inconsistently
// across platforms; these stay crisp everywhere.
const ICONS = {
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  memory:
    '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.6 1 2.5h6c0-.9.3-1.8 1-2.5A6 6 0 0 0 12 3Z"/>',
  lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  monitor: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4"/>',
  send: '<path d="M12 20V5M5 12l7-7 7 7"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  trash:
    '<path d="M4 7h16M10 4h4M9 7v12a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2V7"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  sparkle: '<path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/>',
  alert: '<path d="M12 3 2 20h20L12 3Z"/><path d="M12 9.5v4M12 17h.01"/>',
  check: '<path d="M4 12l5 5L20 6"/>',
  hand: '<path d="M9 11V5.5a1.5 1.5 0 0 1 3 0V10m0-.5V4.5a1.5 1.5 0 0 1 3 0V10m0-2.5a1.5 1.5 0 0 1 3 0V14a6 6 0 0 1-6 6 6 6 0 0 1-5.2-3l-2.3-4a1.5 1.5 0 0 1 2.6-1.5L8 13"/>',
  bot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 8V4M9 13h.01M15 13h.01M9.5 17h5"/><path d="M2 13v3M22 13v3"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-4"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
};

// Build an <svg> string for a named icon. Extra classes let callers size/tint.
function icon(name, cls = "") {
  return `<svg class="icon${cls ? " " + cls : ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

// Inject icons into any static markup carrying data-icon="name".
function renderStaticIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = icon(el.dataset.icon);
  });
}

const state = {
  conversationId: null,
  streaming: false,
  controller: null, // AbortController for the in-flight chat stream (Stop button)
};

const els = {
  messages: $("#messages"),
  input: $("#input"),
  composer: $("#composer"),
  send: $("#send"),
  list: $("#conversation-list"),
  newChat: $("#new-chat"),
  sidebar: $("#sidebar"),
  sidebarToggle: $("#sidebar-toggle"),
  sidebarBackdrop: $("#sidebar-backdrop"),
  memoryBtn: $("#memory-btn"),
  memoryModal: $("#memory-modal"),
  memoryClose: $("#memory-close"),
  memoryList: $("#memory-list"),
  memoryAdd: $("#memory-add"),
  memoryInput: $("#memory-input"),
};

// --- tiny, safe markdown renderer ---------------------------------------

function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// Inline-level formatting for a single run of text. Escapes first, so every
// replacement below operates on safe HTML.
function renderInline(s) {
  s = escapeHtml(s);
  // inline code
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
  // bold / italic (asterisk + underscore)
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^\w])_([^_\n]+)_(?=[^\w]|$)/g, "$1<em>$2</em>");
  // links [text](url) — tolerate one level of parens inside the url (e.g. wiki links)
  s = s.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>'
  );
  // bare urls (skip ones already inside an href="…")
  s = s.replace(
    /(^|[^"(>])(https?:\/\/[^\s<)]+)/g,
    '$1<a href="$2" target="_blank" rel="noopener">$2</a>'
  );
  return s;
}

function renderMarkdown(md) {
  // Extract fenced code blocks first so their contents aren't further processed.
  // A NUL sentinel can't collide with anything the model emits.
  const blocks = [];
  md = md.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push(`<pre><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });

  // Line-based block parser: headings, ordered/unordered lists (a list may be
  // preceded by a lead-in line in the same block), and paragraphs. A blank line
  // ends the current paragraph/list.
  const out = [];
  let para = [];
  let list = null; // { type: "ul" | "ol", items: [] }

  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${para.join("<br>")}</p>`);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      out.push(
        `<${list.type}>` + list.items.map((i) => `<li>${i}</li>`).join("") + `</${list.type}>`
      );
      list = null;
    }
  };

  for (const line of md.split("\n")) {
    const ph = line.match(/^\u0000(\d+)\u0000$/); // a fenced block alone on its line
    if (ph) {
      flushPara();
      flushList();
      out.push(blocks[+ph[1]]);
      continue;
    }
    if (/^\s*$/.test(line)) {
      flushPara();
      flushList();
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushPara();
      flushList();
      out.push(`<h${h[1].length}>${renderInline(h[2])}</h${h[1].length}>`);
      continue;
    }
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) {
      flushPara();
      if (!list || list.type !== "ul") {
        flushList();
        list = { type: "ul", items: [] };
      }
      list.items.push(renderInline(ul[1]));
      continue;
    }
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) {
      flushPara();
      if (!list || list.type !== "ol") {
        flushList();
        list = { type: "ol", items: [] };
      }
      list.items.push(renderInline(ol[1]));
      continue;
    }
    // plain text line
    flushList();
    para.push(renderInline(line));
  }
  flushPara();
  flushList();

  let html = out.join("");
  // Restore any fenced block that ended up inline rather than alone on its line.
  html = html.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
  return html;
}

// --- DOM helpers ---------------------------------------------------------

function clearEmptyState() {
  const es = els.messages.querySelector(".empty-state");
  if (es) es.remove();
}

function addMessage(role, content) {
  clearEmptyState();
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const avatar = role === "user" ? icon("user") : icon("sparkle");
  wrap.innerHTML = `<div class="avatar">${avatar}</div><div class="body"></div>`;
  const body = wrap.querySelector(".body");
  if (role === "user") {
    body.textContent = content;
  } else {
    body.innerHTML = content ? renderMarkdown(content) : "";
  }
  els.messages.appendChild(wrap);
  scrollToBottom();
  return body;
}

function addToolChip(name, args) {
  const wrap = document.createElement("div");
  wrap.className = "tool-activity";
  const label = describeTool(name, args);
  wrap.innerHTML = `<span class="tool-chip"><span class="spin"></span>${label}</span>`;
  els.messages.appendChild(wrap);
  scrollToBottom();
  return wrap.querySelector(".tool-chip");
}

function describeTool(name, args) {
  if (name === "web_search") return `Searching the web: “${args.query ?? ""}”`;
  if (name === "web_fetch") return `Reading ${args.url ?? "a page"}`;
  if (name === "browse") {
    const as = args.profile ? ` (as ${args.profile})` : "";
    return `Browsing${as}: ${args.goal ?? ""}`;
  }
  return `Running ${name}`;
}

function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

// A placeholder shown the instant a message is sent, before the first token or tool
// event arrives — so the wait never looks frozen. Cleared as soon as real output lands.
let thinkingEl = null;
function addThinking() {
  clearThinking();
  clearEmptyState();
  const wrap = document.createElement("div");
  wrap.className = "msg assistant thinking";
  wrap.innerHTML =
    `<div class="avatar">${icon("sparkle")}</div>` +
    `<div class="body"><span class="thinking-dots"><i></i><i></i><i></i></span></div>`;
  els.messages.appendChild(wrap);
  scrollToBottom();
  thinkingEl = wrap;
}
function clearThinking() {
  if (thinkingEl) {
    thinkingEl.remove();
    thinkingEl = null;
  }
}

// Shimmer placeholder rows while a conversation's messages are being fetched.
function renderSkeleton() {
  els.messages.innerHTML =
    '<div class="skeleton-wrap">' +
    Array.from({ length: 3 })
      .map(
        (_, i) =>
          `<div class="msg ${i % 2 ? "user" : "assistant"} skel">` +
          `<div class="avatar skel-box"></div>` +
          `<div class="body"><div class="skel-line"></div><div class="skel-line short"></div></div>` +
          `</div>`
      )
      .join("") +
    "</div>";
}

// --- conversations -------------------------------------------------------

function highlightActive() {
  els.list.querySelectorAll("li").forEach((li) => {
    li.classList.toggle("active", li.dataset.id === state.conversationId);
  });
}

async function loadConversations() {
  let convos;
  try {
    const res = await fetch("/api/conversations");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    convos = await res.json();
  } catch {
    els.list.innerHTML = '<li class="list-error">Couldn’t load chats. Retry?</li>';
    els.list.querySelector(".list-error").onclick = loadConversations;
    return;
  }
  els.list.innerHTML = "";
  for (const c of convos) {
    const li = document.createElement("li");
    li.dataset.id = c.id;
    if (c.id === state.conversationId) li.classList.add("active");
    li.innerHTML = `<span class="title">${escapeHtml(c.title)}</span><button class="del" title="Delete">${icon("trash")}</button>`;
    li.querySelector(".title").onclick = () => openConversation(c.id);
    li.querySelector(".del").onclick = async (e) => {
      e.stopPropagation();
      await fetch(`/api/conversations/${c.id}`, { method: "DELETE" });
      if (state.conversationId === c.id) {
        state.conversationId = null;
        els.messages.innerHTML = "";
        showEmptyState();
      }
      loadConversations();
    };
    els.list.appendChild(li);
  }
}

async function openConversation(id) {
  state.conversationId = id;
  highlightActive(); // instant feedback — don't wait on the fetch
  closeSidebar(); // mobile: dismiss the drawer after choosing a chat
  renderSkeleton();
  let data;
  try {
    const res = await fetch(`/api/conversations/${id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch {
    els.messages.innerHTML = "";
    addErrorLine("Couldn’t load this conversation. Please try again.");
    return;
  }
  els.messages.innerHTML = "";
  for (const m of data.messages) {
    if (m.role === "user" || m.role === "assistant") {
      if (m.content) addMessage(m.role, m.content);
    } else if (m.role === "tool") {
      const chip = addToolChip(m.tool_name, {});
      chip.classList.add("done");
      chip.querySelector(".spin")?.remove();
    }
  }
  loadConversations();
}

function showEmptyState() {
  els.messages.innerHTML = `
    <div class="empty-state">
      <h1>Oli</h1>
      <p>Your personal assistant with live web access. Ask anything.</p>
    </div>`;
}

// --- sending / streaming -------------------------------------------------

function setStreamingUI(on) {
  state.streaming = on;
  els.send.classList.toggle("streaming", on);
  els.send.innerHTML = icon(on ? "stop" : "send");
  els.send.title = on ? "Stop generating" : "Send";
  els.send.disabled = false; // stays clickable while streaming so it can Stop
}

// Abort the in-flight chat stream. Pass { silent: true } when the abort is a side
// effect of navigation (e.g. starting a new chat) so it doesn't leave a "Stopped."
// notice behind.
function stopStream({ silent = false } = {}) {
  if (!state.controller) return; // nothing in flight — don't arm silentStop for the next stop
  if (silent) state.silentStop = true;
  state.controller.abort();
}

async function send(message) {
  if (state.streaming || !message.trim()) return;
  const controller = new AbortController();
  state.controller = controller;
  setStreamingUI(true);

  addMessage("user", message);
  els.input.value = "";
  autoGrow();
  addThinking(); // immediate feedback before the first token/tool event

  let assistantBody = null;
  let assistantText = "";
  let activeChip = null;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, conversation_id: state.conversationId }),
      signal: controller.signal,
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.replace(/^data: /, "").trim();
        if (!line) continue;
        const evt = JSON.parse(line);
        handleEvent(evt);
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      if (!state.silentStop) addNoticeLine("Stopped.");
    } else {
      addErrorLine(`Connection error: ${err.message}`);
    }
  } finally {
    clearThinking();
    state.controller = null;
    state.silentStop = false;
    setStreamingUI(false);
    if (assistantBody) assistantBody.classList.remove("cursor");
    loadConversations();
  }

  return assistantText;

  function handleEvent(evt) {
    // The "thinking" placeholder is cleared only when something visible replaces it
    // (a token, a tool chip, the final message, a notice or an error) — never on
    // invisible control events like `meta`/`intent`/`end`, which previously left a
    // blank, frozen-looking gap. Between steps (after a tool ends) it's restored so
    // the wait always shows activity.
    switch (evt.type) {
      case "meta":
        state.conversationId = evt.conversation_id;
        break;
      case "tool_start":
        clearThinking();
        activeChip = addToolChip(evt.name, evt.args || {});
        if (evt.name === "browse") enterAgentWatch();
        break;
      case "tool_end":
        if (activeChip) {
          activeChip.classList.add("done");
          activeChip.querySelector(".spin")?.remove();
          activeChip = null;
        }
        if (evt.name === "browse") exitAgentWatch();
        // More work is likely coming before the first token — keep showing activity.
        if (!assistantBody) addThinking();
        break;
      case "token":
        clearThinking();
        if (!assistantBody) {
          assistantBody = addMessage("assistant", "");
          assistantBody.classList.add("cursor");
        }
        assistantText += evt.text;
        assistantBody.innerHTML = renderMarkdown(assistantText);
        scrollToBottom();
        break;
      case "done":
        clearThinking();
        if (!assistantBody) assistantBody = addMessage("assistant", evt.content || "");
        assistantText = evt.content || assistantText;
        assistantBody.innerHTML = renderMarkdown(assistantText);
        assistantBody.classList.remove("cursor");
        break;
      case "notice":
        clearThinking();
        addNoticeLine(evt.message);
        if (!assistantBody) addThinking(); // a reply usually follows the notice
        break;
      case "error":
        clearThinking();
        addErrorLine(evt.message);
        break;
    }
  }
}

function addErrorLine(msg) {
  clearEmptyState();
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  wrap.innerHTML = `<div class="avatar avatar-error">${icon("alert")}</div><div class="body error-line">${escapeHtml(msg)}</div>`;
  els.messages.appendChild(wrap);
  scrollToBottom();
}

// A non-fatal notice (e.g. the chat provider failed over to a fallback). Rendered
// inline as a subtle line so a silent failover isn't actually silent (see ADR 0017).
function addNoticeLine(msg) {
  clearEmptyState();
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  wrap.innerHTML = `<div class="avatar avatar-notice">${icon("info")}</div><div class="body notice-line">${escapeHtml(msg)}</div>`;
  els.messages.appendChild(wrap);
  scrollToBottom();
}

// --- input UX ------------------------------------------------------------

function autoGrow() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 200) + "px";
}

els.input.addEventListener("input", autoGrow);
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send(els.input.value);
  }
});
els.composer.addEventListener("submit", (e) => {
  e.preventDefault();
  send(els.input.value);
});
// While streaming the send button becomes a Stop button: intercept its click (which
// fires before the form submit) to abort the stream instead of starting a new one.
els.send.addEventListener("click", (e) => {
  if (state.streaming) {
    e.preventDefault();
    stopStream();
  }
});
els.newChat.addEventListener("click", () => {
  stopStream({ silent: true }); // cancel any in-flight reply so the new chat is usable at once
  state.conversationId = null;
  showEmptyState();
  loadConversations();
  closeSidebar();
  els.input.focus();
});

document.addEventListener("click", (e) => {
  if (e.target.classList.contains("suggestion")) {
    send(e.target.textContent);
  }
});

// --- mobile sidebar drawer ----------------------------------------------
// Below 720px the sidebar is an off-canvas drawer; without this it (and the
// memory/profiles/live entry points) were unreachable on small screens.
function openSidebar() {
  els.sidebar.classList.add("open");
  els.sidebarBackdrop.classList.remove("hidden");
}
function closeSidebar() {
  els.sidebar.classList.remove("open");
  els.sidebarBackdrop.classList.add("hidden");
}
els.sidebarToggle.addEventListener("click", () => {
  els.sidebar.classList.contains("open") ? closeSidebar() : openSidebar();
});
els.sidebarBackdrop.addEventListener("click", closeSidebar);

// --- modal controller: Esc-to-close, focus trap + restore, nav active ----
let activeModal = null;
let activeModalClose = null;
let lastFocused = null;

function onModalOpen(modalEl, closeFn, navBtn) {
  lastFocused = document.activeElement;
  activeModal = modalEl;
  activeModalClose = closeFn;
  modalEl._navBtn = navBtn || null;
  if (navBtn) navBtn.classList.add("active");
  closeSidebar(); // if opened from the mobile drawer, tuck it away
  const first = modalEl.querySelector(
    'input, button, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  if (first) first.focus();
}
function onModalClose(modalEl) {
  if (modalEl._navBtn) modalEl._navBtn.classList.remove("active");
  // Only the tracked (user-opened) modal restores focus — the live panel can auto-open
  // mid-browse without going through onModalOpen, and we must not steal focus then.
  const wasActive = activeModal === modalEl;
  if (wasActive) {
    activeModal = null;
    activeModalClose = null;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
    lastFocused = null;
  }
}

function trapFocus(e, modalEl) {
  const f = [
    ...modalEl.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    ),
  ].filter((el) => el.offsetParent !== null);
  if (!f.length) return;
  const first = f[0];
  const last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

document.addEventListener("keydown", (e) => {
  if (!activeModal) return;
  if (e.key === "Escape") {
    e.preventDefault();
    if (activeModalClose) activeModalClose();
  } else if (e.key === "Tab") {
    trapFocus(e, activeModal);
  }
});

// --- memory panel --------------------------------------------------------

async function loadMemories() {
  els.memoryList.innerHTML = '<li class="list-loading">Loading…</li>';
  let memories;
  try {
    const res = await fetch("/api/memories");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    memories = await res.json();
  } catch {
    els.memoryList.innerHTML = '<li class="list-error">Couldn’t load memories. Retry?</li>';
    els.memoryList.querySelector(".list-error").onclick = loadMemories;
    return;
  }
  els.memoryList.innerHTML = "";
  if (!memories.length) {
    const li = document.createElement("li");
    li.className = "mem-empty";
    li.textContent = "Oli hasn't remembered anything yet. Chat, or add a fact above.";
    els.memoryList.appendChild(li);
    return;
  }
  for (const m of memories) {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="mem-content">${escapeHtml(m.content)}</span>
      <span style="display:flex;gap:8px;align-items:center;flex-shrink:0;">
        <span class="mem-source">${m.source}</span>
        <button class="mem-del" title="Forget">${icon("trash")}</button>
      </span>`;
    li.querySelector(".mem-del").onclick = async () => {
      await fetch(`/api/memories/${m.id}`, { method: "DELETE" });
      loadMemories();
    };
    els.memoryList.appendChild(li);
  }
}

function openMemory() {
  els.memoryModal.classList.remove("hidden");
  onModalOpen(els.memoryModal, closeMemory, els.memoryBtn);
  loadMemories();
  els.memoryInput.focus();
}
function closeMemory() {
  els.memoryModal.classList.add("hidden");
  onModalClose(els.memoryModal);
}

els.memoryBtn.addEventListener("click", openMemory);
els.memoryClose.addEventListener("click", closeMemory);
els.memoryModal.addEventListener("click", (e) => {
  if (e.target === els.memoryModal) closeMemory();
});
els.memoryAdd.addEventListener("submit", async (e) => {
  e.preventDefault();
  const content = els.memoryInput.value.trim();
  if (!content) return;
  await fetch("/api/memories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  els.memoryInput.value = "";
  loadMemories();
});

// --- browser profiles ----------------------------------------------------

const profilesModal = $("#profiles-modal");

async function loadProfiles() {
  const list = $("#profile-list");
  list.innerHTML = '<li class="list-loading">Loading…</li>';
  let profiles;
  try {
    const res = await fetch("/api/profiles");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    profiles = await res.json();
  } catch {
    list.innerHTML = '<li class="list-error">Couldn’t load profiles. Retry?</li>';
    list.querySelector(".list-error").onclick = loadProfiles;
    return;
  }
  list.innerHTML = "";
  if (!profiles.length) {
    const li = document.createElement("li");
    li.className = "prof-empty";
    li.textContent = "No profiles yet. Add a site above, then log in once.";
    list.appendChild(li);
    return;
  }
  for (const p of profiles) {
    const li = document.createElement("li");
    const dotClass = p.logging_in ? "pending" : p.logged_in ? "on" : "";
    const status = p.logging_in
      ? "signing in…"
      : p.logged_in
        ? "logged in"
        : "not logged in";
    const last = p.last_login
      ? ` · ${new Date(p.last_login * 1000).toLocaleDateString()}`
      : "";
    // While a login window is open, offer "Done"; otherwise "Log in"/"Re-login".
    const loginBtn = p.logging_in
      ? `<button class="primary finish">${icon("check")} I'm done</button>`
      : `<button class="login">${p.logged_in ? "Re-login" : "Log in"}</button>`;
    li.innerHTML = `
      <div class="prof-head">
        <span class="prof-name"><span class="prof-dot ${dotClass}"></span>${escapeHtml(p.label || p.name)}</span>
        <span class="prof-actions">
          ${loginBtn}
          <button class="danger del">Delete</button>
        </span>
      </div>
      <div class="prof-meta">${escapeHtml(p.start_url || "no login URL")} · ${status}${last}</div>`;

    const loginEl = li.querySelector(".login");
    if (loginEl)
      loginEl.onclick = async () => {
        loginEl.textContent = "Opening…";
        loginEl.disabled = true;
        const r = await fetch(`/api/profiles/${p.name}/login`, { method: "POST" });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          alert(err.detail || "Could not open the login window.");
        }
        loadProfiles();
      };

    const finishEl = li.querySelector(".finish");
    if (finishEl)
      finishEl.onclick = async () => {
        finishEl.textContent = "Saving…";
        finishEl.disabled = true;
        await fetch(`/api/profiles/${p.name}/login/finish`, { method: "POST" });
        loadProfiles();
      };

    li.querySelector(".del").onclick = async () => {
      if (!confirm(`Delete profile “${p.label || p.name}” and its saved login?`)) return;
      await fetch(`/api/profiles/${p.name}`, { method: "DELETE" });
      loadProfiles();
    };
    list.appendChild(li);
  }
}

function openProfiles() {
  profilesModal.classList.remove("hidden");
  onModalOpen(profilesModal, closeProfiles, $("#profiles-btn"));
  loadProfiles();
}
function closeProfiles() {
  profilesModal.classList.add("hidden");
  onModalClose(profilesModal);
}

$("#profiles-btn").addEventListener("click", openProfiles);
$("#profiles-close").addEventListener("click", closeProfiles);
profilesModal.addEventListener("click", (e) => {
  if (e.target === profilesModal) closeProfiles();
});

$("#profile-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const label = $("#profile-label").value.trim();
  const start_url = $("#profile-url").value.trim();
  if (!label) return;
  const res = await fetch("/api/profiles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, start_url }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || "Could not add profile.");
    return;
  }
  $("#profile-label").value = "";
  $("#profile-url").value = "";
  loadProfiles();
});

// --- live browser --------------------------------------------------------

const liveModal = $("#live-modal");
const liveCanvas = $("#live-canvas");
const liveCtx = liveCanvas.getContext("2d");
const live = {
  ws: null,
  running: false,
  img: new Image(),
  lastMove: 0,
  agentMode: false, // watching an autonomous browse run
  controlled: false, // user has taken control (agent paused)
  autoOpened: false, // panel popped open by a browse, not by the user
};

function setLiveStatus(text) {
  $("#live-status").textContent = text || "";
}

function drawFrame(b64) {
  live.img.onload = () => liveCtx.drawImage(live.img, 0, 0, liveCanvas.width, liveCanvas.height);
  live.img.src = "data:image/jpeg;base64," + b64;
}

function liveSend(evt) {
  // While watching an agent run, don't send input until the user takes control.
  if (live.agentMode && !live.controlled) return;
  if (live.ws && live.ws.readyState === WebSocket.OPEN) live.ws.send(JSON.stringify(evt));
}

function canvasCoords(e) {
  const r = liveCanvas.getBoundingClientRect();
  return { xr: (e.clientX - r.left) / r.width, yr: (e.clientY - r.top) / r.height };
}

function attachCanvasInput() {
  liveCanvas.onmousemove = (e) => {
    const now = Date.now();
    if (now - live.lastMove < 40) return; // throttle
    live.lastMove = now;
    liveSend({ kind: "mousemove", ...canvasCoords(e) });
  };
  liveCanvas.onmousedown = (e) => {
    liveCanvas.focus();
    const btn = e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
    liveSend({ kind: "mousedown", button: btn, ...canvasCoords(e) });
  };
  liveCanvas.onmouseup = (e) => {
    const btn = e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
    liveSend({ kind: "mouseup", button: btn, ...canvasCoords(e) });
  };
  liveCanvas.oncontextmenu = (e) => e.preventDefault();
  liveCanvas.onwheel = (e) => {
    e.preventDefault();
    liveSend({ kind: "wheel", dx: e.deltaX, dy: e.deltaY, ...canvasCoords(e) });
  };
  liveCanvas.onkeydown = (e) => {
    e.preventDefault();
    liveSend({ kind: "key", key: e.key });
  };
}

function connectLiveWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  live.ws = new WebSocket(`${proto}://${location.host}/api/live/ws`);
  live.ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "frame") drawFrame(msg.data);
  };
  live.ws.onclose = () => {
    if (live.running) setLiveStatus("Disconnected.");
  };
}

async function loadLiveProfiles() {
  const res = await fetch("/api/profiles");
  const profiles = await res.json();
  const sel = $("#live-profile");
  sel.innerHTML = '<option value="">No profile (temporary)</option>';
  for (const p of profiles) {
    const o = document.createElement("option");
    o.value = p.name;
    o.textContent = p.logged_in ? `${p.label} ✓` : p.label;
    sel.appendChild(o);
  }
}

async function startLive() {
  const profile = $("#live-profile").value || null;
  const url = $("#live-url").value.trim() || null;
  setLiveStatus("Starting browser…");
  const res = await fetch("/api/live/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile, url }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    setLiveStatus(`Failed: ${err.detail || res.status}`);
    return;
  }
  live.running = true;
  connectLiveWS();
  attachCanvasInput();
  $("#live-placeholder").classList.add("hidden");
  $("#live-start").classList.add("hidden");
  $("#live-stop").classList.remove("hidden");
  liveCanvas.focus();
  setLiveStatus(profile ? `Live — will save to “${profile}”. Log in, then Stop & save.` : "Live.");
}

async function stopLive() {
  if (!live.running) return;
  live.running = false;
  try { live.ws && live.ws.close(); } catch {}
  live.ws = null;
  const res = await fetch("/api/live/stop", { method: "POST" });
  const data = await res.json().catch(() => ({}));
  liveCtx.clearRect(0, 0, liveCanvas.width, liveCanvas.height);
  $("#live-placeholder").classList.remove("hidden");
  $("#live-stop").classList.add("hidden");
  $("#live-start").classList.remove("hidden");
  setLiveStatus(data.profile ? `Saved session to “${data.profile}”.` : "Stopped.");
  loadLiveProfiles();
}

async function liveNavigate() {
  const url = $("#live-url").value.trim();
  if (!url) return;
  if (!live.running) return startLive();
  await fetch("/api/live/navigate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
}

async function openLive() {
  liveModal.classList.remove("hidden");
  onModalOpen(liveModal, closeLive, $("#live-btn"));
  await loadLiveProfiles();
  const st = await (await fetch("/api/live/status")).json();
  if (st.agent_mode) {
    // Oli is browsing — join the watch view, reflecting who's in control.
    live.agentMode = true;
    live.autoOpened = false;
    setLiveMode(true);
    $("#live-placeholder").classList.add("hidden");
    if (!live.running) {
      live.running = true;
      connectLiveWS();
      attachCanvasInput();
    }
    live.controlled = !!st.controlled;
    $("#live-take").classList.toggle("hidden", live.controlled);
    $("#live-resume").classList.toggle("hidden", !live.controlled);
    $("#live-agent-note").innerHTML = live.controlled
      ? `${icon("hand")} You're driving — Oli is paused.`
      : `${icon("bot")} Oli is browsing…`;
    setLiveStatus(live.controlled ? "You have control." : "Watching Oli browse.");
  } else if (st.running) {
    // Reattach to an already-running owned session.
    setLiveMode(false);
    live.running = true;
    connectLiveWS();
    attachCanvasInput();
    $("#live-placeholder").classList.add("hidden");
    $("#live-start").classList.add("hidden");
    $("#live-stop").classList.remove("hidden");
    setLiveStatus("Reconnected to a running session.");
  } else {
    setLiveMode(false);
  }
}

function closeLive() {
  liveModal.classList.add("hidden");
  onModalClose(liveModal);
  if (live.agentMode) {
    // Just stop watching — leave Oli's run going (server detaches its own tap
    // when the browse finishes). Resume Oli if we'd paused it.
    if (live.controlled) releaseControl();
    live.agentMode = false;
    live.controlled = false;
    live.running = false;
    try { live.ws && live.ws.close(); } catch {}
    live.ws = null;
    setLiveMode(false);
    return;
  }
  stopLive(); // owned session: free the server browser when the panel is closed
}

$("#live-btn").addEventListener("click", openLive);
$("#live-close").addEventListener("click", closeLive);
liveModal.addEventListener("click", (e) => {
  if (e.target === liveModal) closeLive();
});
$("#live-start").addEventListener("click", startLive);
$("#live-stop").addEventListener("click", stopLive);
$("#live-go").addEventListener("click", liveNavigate);
$("#live-url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); liveNavigate(); }
});
$("#live-take").addEventListener("click", takeControl);
$("#live-resume").addEventListener("click", releaseControl);

// --- watching an autonomous browse run (agent mode) ----------------------

// Show/hide the manual controls vs. the agent watch bar.
function setLiveMode(agent) {
  $("#live-controls").classList.toggle("hidden", agent);
  $("#live-agent-bar").classList.toggle("hidden", !agent);
}

// Called when a `browse` tool starts. Taps into the agent's browser (server-side
// attach already happened) and streams it read-only until the user takes control.
function enterAgentWatch() {
  // Don't hijack a live session the user is already driving themselves.
  if (live.running && !live.agentMode) return;
  live.agentMode = true;
  live.controlled = false;
  live.autoOpened = liveModal.classList.contains("hidden");
  liveModal.classList.remove("hidden");
  setLiveMode(true);
  $("#live-resume").classList.add("hidden");
  $("#live-take").classList.remove("hidden");
  $("#live-agent-note").innerHTML = `${icon("bot")} Oli is browsing…`;
  $("#live-placeholder").classList.add("hidden");
  if (!live.running) {
    live.running = true;
    connectLiveWS();
    attachCanvasInput(); // handlers are gated by liveSend until control is taken
  }
  setLiveStatus("Watching Oli browse. Take control any time to step in.");
}

// Called when the `browse` tool ends. The server has already detached its tap.
function exitAgentWatch() {
  if (!live.agentMode) return;
  const wasHandsOn = live.controlled;
  live.agentMode = false;
  live.controlled = false;
  live.running = false;
  try { live.ws && live.ws.close(); } catch {}
  live.ws = null;
  setLiveMode(false);
  $("#live-agent-note").innerHTML = `${icon("bot")} Oli is browsing…`;
  setLiveStatus("Oli finished browsing.");
  // If the panel popped open on its own and the user never stepped in, tuck it away.
  if (live.autoOpened && !wasHandsOn) {
    setTimeout(() => {
      if (!live.running && !live.agentMode) {
        liveModal.classList.add("hidden");
        liveCtx.clearRect(0, 0, liveCanvas.width, liveCanvas.height);
        $("#live-placeholder").classList.remove("hidden");
      }
    }, 2500);
  }
  live.autoOpened = false;
}

async function takeControl() {
  const res = await fetch("/api/live/control", { method: "POST" });
  if (!res.ok) return;
  live.controlled = true;
  $("#live-take").classList.add("hidden");
  $("#live-resume").classList.remove("hidden");
  $("#live-agent-note").innerHTML = `${icon("hand")} You're driving — Oli is paused.`;
  liveCanvas.focus();
  setLiveStatus("You have control. Give it back to let Oli continue.");
}

async function releaseControl() {
  const res = await fetch("/api/live/release", { method: "POST" });
  if (!res.ok) return;
  live.controlled = false;
  $("#live-resume").classList.add("hidden");
  $("#live-take").classList.remove("hidden");
  $("#live-agent-note").innerHTML = `${icon("bot")} Oli is browsing…`;
  setLiveStatus("Handed back to Oli. Watching…");
}

// --- voice ---------------------------------------------------------------

const voice = {
  mode: false, // hands-free loop when true
  recording: false,
  mediaRecorder: null,
  chunks: [],
  stream: null,
  audioCtx: null,
  silenceTimer: null,
  chosen: null, // selected SpeechSynthesisVoice for the browser fallback
  server: false, // true when server-side neural TTS (/api/tts) is enabled
  serverVoice: null, // chosen server voice name
};

const micBtn = $("#mic");
const voiceStatus = $("#voice-status");
const voiceSelect = $("#voice-select");
const VOICE_KEY = "oli.voice";

// The browser's default voice is usually a robotic built-in (Windows David/Zira, eSpeak).
// Score voices so we auto-pick a natural/neural/online one, favouring en-IN (the user's
// locale) then any English. Higher = better.
function scoreVoice(v) {
  const n = `${v.name} ${v.voiceURI || ""}`.toLowerCase();
  let s = 0;
  if (/(natural|neural|premium|enhanced|online)/.test(n)) s += 5;
  if (/google/.test(n)) s += 4;
  if (!v.localService) s += 2; // network voices are usually higher quality
  if (v.lang === "en-IN") s += 3;
  else if (v.lang && v.lang.toLowerCase().startsWith("en")) s += 1;
  if (/(espeak|david|zira|mark|hazel)/.test(n)) s -= 3; // known robotic defaults
  return s;
}

// Populate voice.chosen + the picker. getVoices() is async on some browsers, so this is
// also wired to the voiceschanged event below.
function loadVoices() {
  if (!("speechSynthesis" in window)) return;
  const all = window.speechSynthesis
    .getVoices()
    .filter((v) => !v.lang || v.lang.toLowerCase().startsWith("en"));
  if (!all.length) return;
  all.sort((a, b) => scoreVoice(b) - scoreVoice(a));
  const saved = localStorage.getItem(VOICE_KEY);
  voice.chosen = all.find((v) => v.name === saved) || all[0];
  if (voiceSelect) {
    voiceSelect.innerHTML = "";
    for (const v of all) {
      const opt = document.createElement("option");
      opt.value = v.name;
      opt.textContent = `${v.name} (${v.lang})`;
      if (v.name === voice.chosen.name) opt.selected = true;
      voiceSelect.appendChild(opt);
    }
    // Populated but kept hidden until the user engages voice mode (see the mic handler).
  }
}

if ("speechSynthesis" in window) {
  loadVoices();
  window.speechSynthesis.onvoiceschanged = loadVoices;
}

const TTS_VOICE_KEY = "oli.tts.voice";

// If the server exposes neural TTS, prefer it and list ITS voices in the picker; otherwise
// keep the browser-voice quick win. Best-effort — any failure leaves the browser path.
async function initServerTTS() {
  if (!voiceSelect) return;
  let info;
  try {
    const res = await fetch("/api/tts/voices");
    if (!res.ok) return;
    info = await res.json();
  } catch {
    return;
  }
  if (!info || !info.enabled || !Array.isArray(info.voices) || !info.voices.length) return;
  voice.server = true;
  const saved = localStorage.getItem(TTS_VOICE_KEY);
  voice.serverVoice = info.voices.includes(saved) ? saved : info.default || info.voices[0];
  voiceSelect.innerHTML = "";
  for (const name of info.voices) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === voice.serverVoice) opt.selected = true;
    voiceSelect.appendChild(opt);
  }
  // Populated but kept hidden until the user engages voice mode (see the mic handler).
}

if (voiceSelect) {
  voiceSelect.addEventListener("change", () => {
    if (voice.server) {
      voice.serverVoice = voiceSelect.value;
      localStorage.setItem(TTS_VOICE_KEY, voiceSelect.value);
    } else {
      voice.chosen =
        window.speechSynthesis.getVoices().find((v) => v.name === voiceSelect.value) ||
        voice.chosen;
      localStorage.setItem(VOICE_KEY, voiceSelect.value);
    }
  });
}

function setVoiceStatus(text) {
  if (!text) {
    voiceStatus.classList.add("hidden");
    return;
  }
  voiceStatus.textContent = text;
  voiceStatus.classList.remove("hidden");
}

function stripMarkdown(md) {
  return md
    .replace(/```[\s\S]*?```/g, " code block. ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#+\s*/gm, "")
    .replace(/^\s*[-*]\s+/gm, "")
    .trim();
}

// Speak a reply: server neural TTS when enabled, else the browser voice; the server path
// falls back to the browser on any failure so audio is never lost.
async function speak(text) {
  if (!text) return;
  if (voice.server) {
    try {
      await speakServer(text);
      return;
    } catch {
      // fall through to the browser voice
    }
  }
  await speakBrowser(text);
}

let currentAudio = null;

function speakServer(text) {
  return new Promise((resolve, reject) => {
    fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: stripMarkdown(text).slice(0, 4000), voice: voice.serverVoice }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.blob();
      })
      .then((blob) => {
        stopAudio();
        const url = URL.createObjectURL(blob);
        currentAudio = new Audio(url);
        currentAudio.onended = () => {
          URL.revokeObjectURL(url);
          resolve();
        };
        currentAudio.onerror = () => {
          URL.revokeObjectURL(url);
          reject(new Error("audio playback failed"));
        };
        currentAudio.play().catch(reject);
      })
      .catch(reject);
  });
}

function stopAudio() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
}

function speakBrowser(text) {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window) || !text) return resolve();
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(stripMarkdown(text).slice(0, 4000));
    if (voice.chosen) {
      utter.voice = voice.chosen;
      utter.lang = voice.chosen.lang;
    }
    utter.rate = 1.0;
    utter.pitch = 1.0;
    utter.onend = resolve;
    utter.onerror = resolve;
    window.speechSynthesis.speak(utter);
  });
}

async function startRecording() {
  // Respect a voice-mode turn-off that happened while a resume was queued.
  if (!voice.mode || voice.recording || state.streaming) return;
  try {
    voice.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setVoiceStatus("Microphone access denied.");
    voice.mode = false;
    micBtn.classList.remove("active");
    return;
  }
  // Voice mode may have been turned off while the permission prompt was open — if so,
  // release the stream and don't start an invisible recording.
  if (!voice.mode) {
    voice.stream.getTracks().forEach((t) => t.stop());
    voice.stream = null;
    return;
  }
  voice.recording = true;
  voice.chunks = [];
  micBtn.classList.add("recording");
  setVoiceStatus("Listening… (click mic to stop)");

  voice.mediaRecorder = new MediaRecorder(voice.stream);
  voice.mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0) voice.chunks.push(e.data);
  };
  voice.mediaRecorder.onstop = onRecordingStop;
  voice.mediaRecorder.start();

  setupSilenceDetection();
}

function setupSilenceDetection() {
  try {
    voice.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source = voice.audioCtx.createMediaStreamSource(voice.stream);
    const analyser = voice.audioCtx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    let spoke = false;
    let lastLoud = Date.now();

    const check = () => {
      if (!voice.recording) return;
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += (v - 128) * (v - 128);
      const rms = Math.sqrt(sum / data.length);
      const now = Date.now();
      if (rms > 6) {
        lastLoud = now;
        spoke = true;
      }
      // Auto-stop 1.5s after speech ends (only once the user has spoken).
      if (spoke && now - lastLoud > 1500) {
        stopRecording();
        return;
      }
      requestAnimationFrame(check);
    };
    requestAnimationFrame(check);
  } catch {
    // Silence detection is best-effort; the manual stop still works.
  }
}

function stopRecording() {
  if (!voice.recording) return;
  voice.recording = false;
  micBtn.classList.remove("recording");
  try {
    voice.mediaRecorder && voice.mediaRecorder.stop();
  } catch {}
  voice.stream && voice.stream.getTracks().forEach((t) => t.stop());
  if (voice.audioCtx) {
    voice.audioCtx.close().catch(() => {});
    voice.audioCtx = null;
  }
}

async function onRecordingStop() {
  // The user turned voice mode off (e.g. clicked the mic to stop) — don't transcribe,
  // send, or resume. Only the silence-auto-stop path reaches here with voice mode on.
  if (!voice.mode) {
    setVoiceStatus("");
    return;
  }
  if (!voice.chunks.length) {
    setVoiceStatus(voice.mode ? "Didn't catch that." : "");
    if (voice.mode) setTimeout(startRecording, 400);
    return;
  }
  setVoiceStatus("Transcribing…");
  const blob = new Blob(voice.chunks, { type: "audio/webm" });
  const form = new FormData();
  form.append("audio", blob, "audio.webm");

  let text = "";
  try {
    const res = await fetch("/api/transcribe", { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    text = (await res.json()).text || "";
  } catch (err) {
    setVoiceStatus(`Transcription failed: ${err.message}`);
    if (voice.mode) setTimeout(startRecording, 1200);
    return;
  }

  // Cancelled while transcription was in flight — drop the result, don't send.
  if (!voice.mode) {
    setVoiceStatus("");
    return;
  }

  if (!text.trim()) {
    setVoiceStatus(voice.mode ? "Didn't catch that." : "");
    if (voice.mode) setTimeout(startRecording, 400);
    return;
  }

  setVoiceStatus("");
  const reply = await send(text);

  if (reply) {
    setVoiceStatus("Speaking…");
    await speak(reply);
  }
  setVoiceStatus("");
  // Hands-free: resume listening after Aria finishes.
  if (voice.mode) setTimeout(startRecording, 300);
}

micBtn.addEventListener("click", () => {
  // A mic click always toggles hands-free voice mode. Turning it OFF halts the whole
  // pipeline from any state — recording, a pending transcription, or in-progress
  // speech — so there's always a one-click way to stop/cancel.
  if (voice.mode) {
    voice.mode = false;
    micBtn.classList.remove("active", "recording");
    if (voiceSelect) voiceSelect.classList.add("hidden");
    stopRecording();
    setVoiceStatus("");
    window.speechSynthesis && window.speechSynthesis.cancel();
    stopAudio();
    return;
  }
  voice.mode = true;
  micBtn.classList.add("active");
  // Reveal the voice picker only while voice mode is on, so it doesn't sit under
  // the composer when the user never uses voice.
  if (voiceSelect && voiceSelect.options.length) voiceSelect.classList.remove("hidden");
  startRecording();
});

// --- init ----------------------------------------------------------------

renderStaticIcons();
loadConversations();
initServerTTS();
els.input.focus();
